"""Source stablecoins : capitalisation USDT + USDC et leur variation.

Les flux de stablecoins sont un leading indicator du pouvoir d'achat de
l'écosystème crypto ("dry powder") :
- supply en HAUSSE = capital qui entre en crypto (potentiel achat) -> bullish ;
- supply en BAISSE = capital qui sort / redemptions -> bearish.

Utilise CoinGecko (/coins/markets) avec la variation 7j déjà disponible.
Dégradation gracieuse : ``{available: False}`` si CoinGecko échoue.
"""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.data_sources.coingecko import _base_and_headers

logger = get_logger(__name__)

_STABLES = {"tether": "USDT", "usd-coin": "USDC", "dai": "DAI"}


def get_stablecoin_supply() -> dict[str, Any]:
    """Récupère la capitalisation des principaux stablecoins et leur variation 7j.

    Returns:
        Dict ``{available, total_mcap_usd, total_change_7d_pct, components,
        interpretation}``. ``components`` liste chaque stable avec mcap +
        variation 7j.
    """

    def _fetch() -> dict[str, Any]:
        base, headers = _base_and_headers()
        data = get_json(
            f"{base}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(_STABLES.keys()),
                "price_change_percentage": "7d",
            },
            headers=headers,
        )
        if not isinstance(data, list) or not data:
            return {"available": False}

        components = []
        total_mcap = 0.0
        weighted_change = 0.0
        for coin in data:
            mcap = coin.get("market_cap") or 0
            sym = _STABLES.get(coin.get("id", ""), coin.get("symbol", "").upper())
            ch7d = coin.get("price_change_percentage_7d_in_currency")
            # Pour un stablecoin, la variation pertinente est celle du market cap,
            # approximée ici par la variation de supply (le prix ~ $1).
            components.append(
                {
                    "symbol": sym,
                    "market_cap_usd": mcap,
                    "change_7d_pct": round(ch7d, 2) if ch7d is not None else None,
                }
            )
            total_mcap += mcap
            if ch7d is not None and mcap:
                weighted_change += mcap * ch7d

        total_change_7d = (weighted_change / total_mcap) if total_mcap else None
        return {
            "available": True,
            "total_mcap_usd": round(total_mcap, 0),
            "total_change_7d_pct": round(total_change_7d, 3)
            if total_change_7d is not None
            else None,
            "components": components,
        }

    try:
        result = CACHE.get_or_compute("stablecoins:supply", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stablecoin supply échoué : %s", exc)
        return {"available": False}

    if result.get("available") and result.get("total_change_7d_pct") is not None:
        ch = result["total_change_7d_pct"]
        if ch >= 0.5:
            result["interpretation"] = (
                "supply stablecoins en hausse · capital entrant, dry powder en "
                "augmentation (bullish structurel)"
            )
        elif ch <= -0.5:
            result["interpretation"] = (
                "supply stablecoins en baisse · redemptions / capital sortant "
                "(bearish structurel)"
            )
        else:
            result["interpretation"] = "supply stablecoins stable · pas de signal directionnel"
    return result
