"""Source calendrier économique : Trading Economics (auth ``guest:guest``).

Free tier très limité mais suffisant pour les événements high-impact (FOMC,
NFP, CPI). En cas d'échec, renvoie une liste vide (dégradation gracieuse) ;
la géopolitique/macro via Gemini compensera.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["trading_economics"]

# Mots-clés d'événements à fort impact pour le crypto.
_HIGH_IMPACT = (
    "fed interest rate",
    "fomc",
    "non farm",
    "nonfarm",
    "cpi",
    "inflation rate",
    "pce",
    "gdp",
    "unemployment rate",
    "ppi",
)


def get_economic_calendar(days_ahead: int = 7) -> dict[str, Any]:
    """Récupère les événements économiques US à venir.

    Args:
        days_ahead: horizon en jours.

    Returns:
        Dict ``{available, events: [{date, country, event, importance,
        actual, forecast, previous, high_impact}], high_impact_count}``.
    """
    now = datetime.now(timezone.utc)
    d1 = now.strftime("%Y-%m-%d")
    d2 = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def _fetch() -> Any:
        return get_json(
            f"{_BASE}/country/united states/{d1}/{d2}",
            params={"c": "guest:guest", "f": "json"},
        )

    raw = CACHE.get_or_compute(f"econcal:{d1}:{d2}", 3600, _fetch)
    if not isinstance(raw, list):
        return {"available": False, "events": [], "high_impact_count": 0}

    events: list[dict[str, Any]] = []
    high_impact_count = 0
    for ev in raw:
        name = str(ev.get("Event", "")).lower()
        is_high = any(k in name for k in _HIGH_IMPACT) or ev.get("Importance") == 3
        if is_high:
            high_impact_count += 1
        events.append(
            {
                "date": ev.get("Date"),
                "country": ev.get("Country"),
                "event": ev.get("Event"),
                "importance": ev.get("Importance"),
                "actual": ev.get("Actual"),
                "forecast": ev.get("Forecast"),
                "previous": ev.get("Previous"),
                "high_impact": is_high,
            }
        )
    # Garder en priorité les high-impact, limiter la verbosité.
    events.sort(key=lambda e: (not e["high_impact"], str(e["date"])))
    return {
        "available": True,
        "events": events[:25],
        "high_impact_count": high_impact_count,
    }
