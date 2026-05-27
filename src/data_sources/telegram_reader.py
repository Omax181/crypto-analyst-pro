"""Telegram : lit les chaînes publiques via Telethon (session string)."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

_CHANNELS = {"WatcherGuru": {"filter": False}, "CryptoMichNL": {"filter": False},
             "AltcoinDailyio": {"filter": False}, "BRICSinfo": {"filter": True}}
_BRICS_KW = ["bitcoin","crypto","digital","currency","payment","stablecoin","yuan","dollar","sanction","trade","cbdc"]


def get_telegram_news(hours: int = 24) -> dict[str, Any]:
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
    if not all([api_id, api_hash, session]):
        return {"available": False, "messages": []}
    def _fetch() -> dict[str, Any]:
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            messages: list[dict[str, Any]] = []
            with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
                for ch, cfg in _CHANNELS.items():
                    try:
                        for msg in client.iter_messages(ch, limit=20):
                            if not msg.text or not msg.date:
                                continue
                            dt = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                            if dt < cutoff:
                                break
                            if cfg["filter"] and not any(k in msg.text.lower() for k in _BRICS_KW):
                                continue
                            messages.append({"channel": ch, "text": msg.text[:400], "timestamp": dt.isoformat()})
                    except Exception as e:
                        logger.debug("Telegram %s : %s", ch, e)
            return {"available": bool(messages), "messages": messages}
        except Exception as exc:
            logger.warning("Telegram : %s", exc)
            return {"available": False, "messages": []}
    return CACHE.get_or_compute("telegram:news", 1800, _fetch)
