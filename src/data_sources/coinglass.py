"""Source dérivés : Coinglass (funding, open interest, long/short, liquidations).

Nécessite COINGLASS_API_KEY (free tier). Sert à qualifier les pumps suspects
(ex. un +10% sur funding extrême = risque de squeeze). Dégradation gracieuse
sans clé.
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://open-api.coinglass.com/public/v2"


def _headers() -> dict[str, str]:
    key = os.environ.get("COINGLASS_API_KEY", "").strip()
    return {"coinglassSecret": key} if key else {}


def get_derivatives(symbol: str) -> dict[str, Any]:
    """Récupère funding rate, open interest et long/short ratio d'un actif.

    Args:
        symbol: ticker (ex. ``"BTC"``).

    Returns:
        Dict ``{available, funding_rate, oi_change_24h_pct, long_short_ratio}``.
    """
    if not os.environ.get("COINGLASS_API_KEY", "").strip():
        return {"available": False, "reason": "pas de clé Coinglass"}

    def _fetch() -> dict[str, Any]:
        result: dict[str, Any] = {"available": False}
        funding = get_json(
            f"{_BASE}/funding", params={"symbol": symbol}, headers=_headers()
        )
        if funding and funding.get("data"):
            data = funding["data"]
            rates = [
                d.get("rate")
                for d in data
                if isinstance(d, dict) and d.get("rate") is not None
            ]
            if rates:
                result["available"] = True
                result["funding_rate"] = round(sum(rates) / len(rates), 5)
        oi = get_json(
            f"{_BASE}/open_interest", params={"symbol": symbol}, headers=_headers()
        )
        if oi and oi.get("data"):
            result["available"] = True
            result["oi_raw"] = oi["data"]
        return result

    return CACHE.get_or_compute(f"coinglass:{symbol}", 1800, _fetch)
