"""Source GDELT : événements géopolitiques mondiaux des dernières 24h."""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_KEYWORDS = (
    "central bank OR fed OR rate cut OR inflation OR cpi OR sanctions OR "
    "war OR election OR regulation crypto OR stablecoin OR bitcoin OR "
    "geopolitical OR tariff OR currency"
)

_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def get_gdelt_events(max_results: int = 15) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            params = {
                "query": _KEYWORDS,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": max_results,
                "timespan": "24h",
                "sort": "hybridrel",
            }
            data = get_json(_BASE_URL, params=params)
            if not data or "articles" not in data:
                return {"available": False, "events": [], "count": 0}

            events = []
            for art in data.get("articles", [])[:max_results]:
                events.append({
                    "title": art.get("title", ""),
                    "url": art.get("url", ""),
                    "source": art.get("domain", ""),
                    "date": art.get("seendate", ""),
                    "tone": art.get("tone", 0),
                    "language": art.get("language", "English"),
                })

            return {"available": True, "events": events, "count": len(events)}
        except Exception as exc:
            logger.warning("GDELT indisponible : %s", exc)
            return {"available": False, "events": [], "count": 0}

    return CACHE.get_or_compute("gdelt_events", 1800, _fetch)
