"""Kaito AI : mindshare et narratives crypto. Nécessite KAITO_API_KEY."""
from __future__ import annotations
import os
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)
_BASE = "https://api.kaito.ai/api/v1"


def _key() -> str:
    return os.environ.get("KAITO_API_KEY", "").strip()


def get_mindshare(symbol: str) -> dict[str, Any]:
    if not _key():
        return {"available": False, "reason": "pas de clé Kaito"}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/yaps/tokens", params={"ticker": symbol},
                            headers={"Authorization": f"Bearer {_key()}"})
            if not data:
                return {"available": False}
            items = data.get("data") if isinstance(data, dict) else data
            d = items[0] if isinstance(items, list) and items else (data if isinstance(data, dict) else None)
            if not d:
                return {"available": False}
            return {"available": True, "mindshare_pct": d.get("mindshare"),
                    "mindshare_7d_change": d.get("mindshare_change_7d"), "yap_score": d.get("yap_score")}
        except Exception as exc:
            logger.warning("Kaito %s : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"kaito:{symbol}", 3600, _fetch)


def get_trending_narratives() -> dict[str, Any]:
    if not _key():
        return {"available": False, "narratives": []}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"{_BASE}/narratives/trending",
                            headers={"Authorization": f"Bearer {_key()}"})
            items = (data or {}).get("data", []) if isinstance(data, dict) else (data or [])
            return {"available": True, "narratives": [{"name": i.get("name"),
                    "score": i.get("score"), "change_7d": i.get("change_7d")} for i in items[:10]]}
        except Exception as exc:
            logger.warning("Kaito narratives : %s", exc)
            return {"available": False, "narratives": []}
    return CACHE.get_or_compute("kaito:narratives", 3600, _fetch)
