"""Source World Bank : indicateurs macro mondiaux (gratuit, sans cle)."""

from __future__ import annotations
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.worldbank.org/v2"

_INDICATORS = {
    "global_inflation": "FP.CPI.TOTL.ZG",
    "global_gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "us_m2": "FM.LBL.BMNY.GD.ZS",
}


def _fetch_indicator(indicator_code, country="WLD"):
    url = f"{_BASE_URL}/country/{country}/indicator/{indicator_code}"
    params = {"format": "json", "date": "2020:2026", "per_page": 10}
    try:
        data = get_json(url, params=params)
        if not data or not isinstance(data, list) or len(data) < 2:
            return {"available": False}
        records = data[1] or []
        valid = [r for r in records if r.get("value") is not None]
        if not valid:
            return {"available": False}
        latest = valid[0]
        return {
            "available": True,
            "value": latest["value"],
            "date": latest["date"],
            "indicator": latest.get("indicator", {}).get("value", indicator_code),
        }
    except Exception as exc:
        logger.warning("World Bank %s indisponible : %s", indicator_code, exc)
        return {"available": False}


def get_world_bank_macro():
    def _fetch():
        indicators = {}
        any_available = False
        for key, code in _INDICATORS.items():
            country = "USA" if key == "us_m2" else "WLD"
            result = _fetch_indicator(code, country)
            indicators[key] = result
            if result.get("available"):
                any_available = True
        return {"available": any_available, "indicators": indicators}
    return CACHE.get_or_compute("worldbank_macro", 86400, _fetch)
