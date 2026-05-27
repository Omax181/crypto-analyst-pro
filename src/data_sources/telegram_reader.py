"""Telegram : lit les chaînes publiques crypto via Telethon (session string).
Tu es déjà membre des chaînes — Telethon lit les messages comme un vrai client.
Chaînes : WatcherGuru, CryptoMichNL, AltcoinDaily, BRICSinfo (filtré).
"""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

CHANNELS = {
    "WatcherGuru": {"filter": False},
    "CryptoMichNL": {"filter": False},
    "AltcoinDailyio": {"filter": False},
    "BRICSinfo": {"filter": True},
}
BRICS_KEYWORDS = ["bitcoin","crypto","digital","currency","payment","stablecoin","yuan","dollar","sanction","trade","cbdc"]

def get_telegram_news(hours: int = 24) -> dict[str, Any]:
    api_id = os.environ.get("TELEGRAM_API_ID","").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH","").strip()
    session = os.environ.get("TELEGRAM_SESSION_STRING","").strip()
    if not all([api_id, api_hash, session]):
        logger.info("Telegram : secrets absents.")
        return {"available": False, "messages": []}
    def _fetch() -> dict[str, Any]:
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            messages = []
            with TelegramClient(StringSession(session), int(api_id), api_hash) as client:
                for channel, cfg in CHANNELS.items():
                    try:
                        for msg in client.iter_messages(channel, limit=20):
                            if not msg.text or not msg.date:
                                continue
                            dt = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                            if dt < cutoff:
                                break
                            text = msg.text
                            if cfg["filter"] and not any(k in text.lower() for k in BRICS_KEYWORDS):
                                continue
                            messages.append({"channel": channel, "text": text[:400],
                                           "timestamp": dt.isoformat()})
                    except Exception as e:
                        logger.debug("Telegram %s : %s", channel, e)
            return {"available": bool(messages), "messages": messages}
        except Exception as exc:
            logger.warning("Telegram indisponible : %s", exc)
            return {"available": False, "messages": []}
    return CACHE.get_or_compute("telegram:news", 1800, _fetch)
