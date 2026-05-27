"""LunarCrush : métriques de sentiment social (Galaxy Score, AltRank, social volume).
Nécessite LUNARCRUSH_API_KEY (free tier : 10 req/min).
"""
from __future__ import annotations
import os
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

_BASE = "https://lunarcrush.com/api4/public"

def _key() -> str:
    return os.environ.get("LUNARCRUSH_API_KEY", "").strip()

def get_social_metrics(symbol: str) -> dict[str, Any]:
    """Galaxy Score, AltRank, social volume pour un symbole."""
    key = _key()
    if not key:
        return {"available": False, "reason": "pas de clé LunarCrush"}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/coins/{symbol.lower()}/v1",
                           headers={"Authorization": f"Bearer {key}"})
            if not data or not data.get("data"):
                return {"available": False}
            d = data["data"]
            return {
                "available": True,
                "galaxy_score": d.get("galaxy_score"),
                "alt_rank": d.get("alt_rank"),
                "social_volume_24h": d.get("social_volume_24h"),
                "social_dominance": d.get("social_dominance"),
                "sentiment": d.get("sentiment"),
                "market_cap_rank": d.get("market_cap_rank"),
            }
        except Exception as exc:
            logger.warning("LunarCrush %s : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"lunarcrush:{symbol}", 1800, _fetch)

def get_trending_coins() -> dict[str, Any]:
    """Top coins par Galaxy Score (signal de rotation narrative)."""
    key = _key()
    if not key:
        return {"available": False, "trending": []}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/coins/list/v2",
                           params={"sort": "galaxy_score", "limit": 10},
                           headers={"Authorization": f"Bearer {key}"})
            items = (data or {}).get("data", [])
            return {"available": True,
                    "trending": [{"symbol": i.get("symbol"), "galaxy_score": i.get("galaxy_score"),
                                  "alt_rank": i.get("alt_rank")} for i in items]}
        except Exception as exc:
            logger.warning("LunarCrush trending : %s", exc)
            return {"available": False, "trending": []}
    return CACHE.get_or_compute("lunarcrush:trending", 3600, _fetch)
