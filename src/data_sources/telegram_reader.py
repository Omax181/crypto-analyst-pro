"""Telegram : lit les chaînes publiques via Telethon (session string).

Valide la session base64 AVANT toute tentative de connexion (une session
corrompue, comme une chaîne dont la longueur n'est pas multiple de 4, produit
sinon une erreur cryptique). Les chaînes sont lues depuis
config/telegram_channels.yaml.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_CONF = load_config("telegram_channels")
_KEYWORDS = [k.lower() for k in (_CONF.get("keywords_filter") or [])]
_SETTINGS = _CONF.get("settings") or {}


def _channels() -> dict[str, dict[str, Any]]:
    """Construit le mapping {handle: {filter: bool}} depuis la config."""
    out: dict[str, dict[str, Any]] = {}
    for _, cfg in (_CONF.get("telegram_channels") or {}).items():
        handle = (cfg or {}).get("handle", "").strip()
        if handle:
            out[handle] = {"filter": bool((cfg or {}).get("filter", False))}
    return out


def _session_is_valid(session: str) -> bool:
    """Vérifie que la session string est un base64 Telethon décodable.

    Telethon préfixe ses StringSession par '1' puis du base64 urlsafe. On
    valide la partie base64 (après le préfixe de version) pour détecter une
    troncature au copier-coller.
    """
    if not session:
        return False
    payload = session[1:] if session and session[0].isdigit() else session
    try:
        # Telethon utilise urlsafe_b64 ; on ajoute le padding manquant.
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return True
    except Exception:
        return False


def get_telegram_news(hours: int = 24) -> dict[str, Any]:
    """Récupère les messages récents des chaînes Telegram configurées.

    Returns:
        ``{available: bool, messages: list, reason?: str}``.
    """
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()

    if not all([api_id, api_hash, session]):
        return {"available": False, "messages": [],
                "reason": "secrets Telegram absents (API_ID/HASH/SESSION)"}

    if not _session_is_valid(session):
        logger.error(
            "TELEGRAM_SESSION_STRING invalide (longueur %d) — régénérer via "
            "generate_telegram_session.py et recopier la chaîne COMPLÈTE.",
            len(session),
        )
        return {"available": False, "messages": [],
                "reason": "session string corrompue (base64 invalide)"}

    channels = _channels()
    if not channels:
        return {"available": False, "messages": [], "reason": "aucune chaîne configurée"}

    limit = int(_SETTINGS.get("max_messages_per_channel", 20))

    def _fetch() -> dict[str, Any]:
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            messages: list[dict[str, Any]] = []
            with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
                for handle, cfg in channels.items():
                    try:
                        for msg in client.iter_messages(handle, limit=limit):
                            if not msg.text or not msg.date:
                                continue
                            dt = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                            if dt < cutoff:
                                break
                            if cfg["filter"] and not any(k in msg.text.lower() for k in _KEYWORDS):
                                continue
                            messages.append({
                                "channel": handle,
                                "text": msg.text[:400],
                                "timestamp": dt.isoformat(),
                            })
                    except Exception as e:
                        logger.debug("Telegram %s : %s", handle, e)
            return {"available": bool(messages), "messages": messages}
        except Exception as exc:
            logger.warning("Telegram : %s", exc)
            return {"available": False, "messages": [], "reason": str(exc)[:120]}

    return CACHE.get_or_compute("telegram:news", 1800, _fetch)
