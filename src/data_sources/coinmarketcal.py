"""CoinMarketCal : calendrier des CATALYSEURS crypto datés (v22 P2 #38).

API gratuite (clé requise, free tier généreux). Événements à venir par projet :
mainnet, upgrades, listings, tokenomics, votes de gouvernance, conférences. Ce
sont les CATALYSEURS que l'analyse calendrier macro (FOMC/CPI) ne couvre pas.

Dégradation gracieuse TOTALE : sans clé ``COINMARKETCAL_API_KEY`` (ou en cas
d'erreur), renvoie ``{available: False}`` — jamais d'exception, jamais de blocage.
Clé optionnelle : Omar l'ajoute en secret GitHub s'il la veut.
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://developers.coinmarketcal.com/v1/events"


def get_events(max_events: int = 15) -> dict[str, Any]:
    """Prochains catalyseurs crypto datés (CoinMarketCal).

    Returns:
        Dict ``{available, events: [{title, date, coins, category}]}`` trié par
        date croissante. ``available=False`` sans clé ou si l'API échoue.
    """
    key = os.environ.get("COINMARKETCAL_API_KEY", "").strip()
    if not key:
        return {"available": False, "reason": "pas de clé CoinMarketCal (gratuite, optionnelle)"}

    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(
                _BASE,
                params={"max": max_events, "sortBy": "hot_events"},
                headers={
                    "x-api-key": key,
                    "Accept": "application/json",
                    "Accept-Encoding": "deflate, gzip",
                },
            )
            body = (data or {}).get("body") if isinstance(data, dict) else None
            if not isinstance(body, list) or not body:
                return {"available": False}
            events: list[dict[str, Any]] = []
            for ev in body:
                if not isinstance(ev, dict):
                    continue
                title = ev.get("title")
                if isinstance(title, dict):
                    title = title.get("en") or next(iter(title.values()), None)
                coins = [
                    c.get("symbol") for c in (ev.get("coins") or [])
                    if isinstance(c, dict) and c.get("symbol")
                ]
                cats = [
                    c.get("name") for c in (ev.get("categories") or [])
                    if isinstance(c, dict) and c.get("name")
                ]
                events.append({
                    "title": title,
                    "date": ev.get("date_event"),
                    "coins": coins,
                    "category": cats[0] if cats else None,
                })
            events.sort(key=lambda e: e.get("date") or "")
            return {"available": bool(events), "events": events}
        except Exception as exc:  # noqa: BLE001
            logger.warning("CoinMarketCal indisponible : %s", exc)
            return {"available": False}

    return CACHE.get_or_compute("coinmarketcal:events", 3600, _fetch)
