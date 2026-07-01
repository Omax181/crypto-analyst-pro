"""LunarCrush : sentiment social (Galaxy Score, AltRank). Nécessite LUNARCRUSH_API_KEY.

⚠️ FREE TIER FERMÉ (v21, vérifié 06/2026) : les endpoints ``api4/public`` renvoient
désormais 402 « Payment Required » même avec une clé free tier. Le module est donc
DÉSACTIVÉ proprement par défaut (comme Coinglass) pour ne pas polluer les logs ni
gaspiller le throttle. Le sentiment social est couvert gratuitement par Reddit
(``reddit.py``) + Fear & Greed. Réactivable via ``LUNARCRUSH_PAID=1`` si abonnement.
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


def _enabled() -> bool:
    """True seulement si une clé ET le flag payant sont présents (free tier = 402)."""
    if not _key():
        return False
    return os.environ.get("LUNARCRUSH_PAID", "").strip().lower() in ("1", "true", "yes")


def get_social_metrics(symbol: str) -> dict[str, Any]:
    if not _enabled():
        return {"available": False, "reason": "LunarCrush free tier fermé (402) — repli Reddit/F&G"}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/coins/{symbol.lower()}/v1",
                            headers={"Authorization": f"Bearer {_key()}"})
            d = (data or {}).get("data")
            if not d:
                return {"available": False}
            return {"available": True, "galaxy_score": d.get("galaxy_score"),
                    "alt_rank": d.get("alt_rank"), "social_volume_24h": d.get("social_volume_24h"),
                    "social_dominance": d.get("social_dominance"), "sentiment": d.get("sentiment")}
        except Exception as exc:
            logger.warning("LunarCrush %s : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"lunarcrush:{symbol}", 1800, _fetch)


def get_trending_coins() -> dict[str, Any]:
    if not _enabled():
        return {"available": False, "trending": []}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/coins/list/v2",
                            params={"sort": "galaxy_score", "limit": 10},
                            headers={"Authorization": f"Bearer {_key()}"})
            items = (data or {}).get("data", [])
            return {"available": True, "trending": [{"symbol": i.get("symbol"),
                    "galaxy_score": i.get("galaxy_score"), "alt_rank": i.get("alt_rank")} for i in items]}
        except Exception as exc:
            logger.warning("LunarCrush trending : %s", exc)
            return {"available": False, "trending": []}
    return CACHE.get_or_compute("lunarcrush:trending", 3600, _fetch)
