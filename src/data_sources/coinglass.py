"""Source dérivés : Coinglass (funding rate, open interest).

⚠️ FREE TIER INSUFFISANT (vérifié par diagnostic 05/2026) : la clé free tier
renvoie "Upgrade plan" sur les endpoints funding/openInterest. Le module est
donc désactivé proprement par défaut. Si l'utilisateur souscrit un plan payant,
mettre COINGLASS_PAID=1 dans les secrets pour réactiver les appels.

API V3 : header CG-API-KEY (l'ancien header coinglassSecret de la V2 est mort).
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://open-api-v3.coinglass.com/api"


def _headers() -> dict[str, str]:
    key = os.environ.get("COINGLASS_API_KEY", "").strip()
    return {"CG-API-KEY": key, "accept": "application/json"} if key else {}


def get_derivatives(symbol: str) -> dict[str, Any]:
    """Récupère funding rate et open interest d'un actif (si plan payant).

    Par défaut désactivé : le free tier ne couvre pas ces endpoints (HTTP
    "Upgrade plan"). Réactivable via COINGLASS_PAID=1.

    Args:
        symbol: ticker (ex. ``"BTC"``).

    Returns:
        Dict ``{available, funding_rate, oi_raw}`` ou ``{available: False, reason}``.
    """
    key = os.environ.get("COINGLASS_API_KEY", "").strip()
    if not key:
        return {"available": False, "reason": "pas de clé Coinglass"}

    # Le free tier renvoie "Upgrade plan" : on n'appelle pas pour ne pas gaspiller
    # le quota ni polluer les logs. Réactivation explicite si plan payant.
    if os.environ.get("COINGLASS_PAID", "").strip() not in ("1", "true", "yes"):
        return {"available": False, "reason": "free_tier_insufficient (endpoints payants)"}

    def _fetch() -> dict[str, Any]:
        result: dict[str, Any] = {"available": False}
        funding = get_json(
            f"{_BASE}/futures/fundingRate/exchange-list",
            params={"symbol": symbol},
            headers=_headers(),
        )
        if funding and funding.get("code") == "0" and funding.get("data"):
            data = funding["data"]
            rates = [
                float(d.get("uMarginList", [{}])[0].get("rate", 0))
                for d in data
                if isinstance(d, dict) and d.get("uMarginList")
            ]
            rates = [r for r in rates if r]
            if rates:
                result["available"] = True
                result["funding_rate"] = round(sum(rates) / len(rates), 5)
        return result

    return CACHE.get_or_compute(f"coinglass:{symbol}", 1800, _fetch)
