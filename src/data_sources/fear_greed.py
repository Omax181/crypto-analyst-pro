"""Source Alternative.me : Fear & Greed Index (sentiment marché global 0-100)."""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["fear_greed"]


def get_fear_greed() -> dict[str, Any]:
    """Récupère l'indice Fear & Greed actuel et celui de la veille.

    Returns:
        Dict ``{available, value, classification, value_yesterday,
        delta}``. ``available=False`` si l'API ne répond pas.
    """

    def _fetch() -> Any:
        return get_json(_BASE, params={"limit": 2})

    raw = CACHE.get_or_compute("fng", 3600, _fetch)
    if not raw or "data" not in raw or not raw["data"]:
        return {"available": False}

    data = raw["data"]
    today = data[0]
    value = int(today.get("value", 0))
    yesterday = int(data[1].get("value", value)) if len(data) > 1 else value
    return {
        "available": True,
        "value": value,
        "classification": today.get("value_classification"),
        "value_yesterday": yesterday,
        "delta": value - yesterday,
    }
