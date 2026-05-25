"""Source FRED : indicateurs macro USA (Fed Funds, DXY, 10Y, VIX, CPI...).

Clé gratuite, requêtes illimitées. Pour chaque série on récupère la dernière
observation et la précédente (pour le delta).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["fred"]
_SERIES: dict[str, str] = _SOURCES["fred_series"]


def _latest_observation(series_id: str, key: str) -> Optional[dict[str, Any]]:
    """Récupère les 2 dernières observations valides d'une série FRED."""
    data = get_json(
        f"{_BASE}/series/observations",
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        },
    )
    if not data or "observations" not in data:
        return None
    valid = [
        o for o in data["observations"] if o.get("value") not in (".", "", None)
    ]
    if not valid:
        return None
    latest = valid[0]
    prev = valid[1] if len(valid) > 1 else None
    try:
        value = float(latest["value"])
        prev_value = float(prev["value"]) if prev else None
    except (ValueError, TypeError):
        return None
    return {
        "value": value,
        "date": latest.get("date"),
        "previous": prev_value,
        "delta": (value - prev_value) if prev_value is not None else None,
    }


def get_macro() -> dict[str, Any]:
    """Récupère toutes les séries macro configurées.

    Returns:
        Dict ``{available, series: {name: {value, date, previous, delta}}}``.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        logger.info("FRED : pas de clé, macro ignorée.")
        return {"available": False, "series": {}}

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, series_id in _SERIES.items():
            obs = _latest_observation(series_id, key)
            if obs:
                out[name] = obs
        return out

    series = CACHE.get_or_compute("fred:all", 3600, _fetch)
    return {"available": bool(series), "series": series}
