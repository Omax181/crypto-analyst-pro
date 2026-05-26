"""Source Telegram : scraping de canaux publics via Telethon.

Récupère les derniers messages (<24h) de canaux crypto (Watcher Guru) et
géopo (BRICS News, filtré par mots-clés). Nécessite une session string générée
une fois en local (cf. MIGRATION_GUIDE.md). Dégradation gracieuse totale si les
secrets manquent ou si Telethon n'est pas installé.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_CONF = load_config("telegram_channels")
_CHANNELS = _CONF["telegram_channels"]
_KEYWORDS = [k.lower() for k in _CONF["keywords_filter"]]
_SETTINGS = _CONF["settings"]


def _credentials() -> tuple[str, str, str] | None:
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if api_id and api_hash and session:
        return api_id, api_hash, session
    return None


def get_telegram_messages() -> dict[str, Any]:
    """Récupère les messages récents des canaux configurés.

    Returns:
        Dict ``{available, messages: [{channel, text, timestamp}]}``.
    """
    creds = _credentials()
    if creds is None:
        logger.info("Telegram : secrets absents, source ignorée.")
        return {"available": False, "messages": []}

    def _fetch() -> dict[str, Any]:
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
        except ImportError:
            logger.warning("Telethon non installé.")
            return {"available": False, "messages": []}

        api_id, api_hash, session = creds
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=int(_SETTINGS["max_age_hours"])
        )
        limit = int(_SETTINGS["max_messages_per_channel"])
        messages: list[dict[str, Any]] = []
        try:
            with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
                for name, cfg in _CHANNELS.items():
                    do_filter = bool(cfg.get("filter"))
                    for msg in client.iter_messages(cfg["handle"], limit=limit):
                        if not msg.text or (msg.date and msg.date < cutoff):
                            continue
                        text = msg.text
                        if do_filter and not any(
                            k in text.lower() for k in _KEYWORDS
                        ):
                            continue
                        messages.append(
                            {
                                "channel": name,
                                "text": text[:500],
                                "timestamp": msg.date.isoformat() if msg.date else None,
                            }
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram indisponible : %s", exc)
            return {"available": False, "messages": []}
        return {"available": bool(messages), "messages": messages}

    return CACHE.get_or_compute("telegram:messages", 1800, _fetch)
