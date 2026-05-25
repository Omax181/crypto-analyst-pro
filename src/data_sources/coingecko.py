"""Source de données CoinGecko (source primaire prix & marché global).

Free tier : ~30 calls/min avec clé. La clé est optionnelle ; sans clé,
l'endpoint public fonctionne mais avec une limite plus basse.
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
_CACHE_TTL = load_config("thresholds").get("cache_ttl", {})
_CG_IDS: dict[str, str] = _SOURCES["coingecko_ids"]


def _base_and_headers() -> tuple[str, dict[str, str]]:
    """Retourne (base_url, headers) selon présence d'une clé pro/demo."""
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    endpoints = _SOURCES["endpoints"]
    if key:
        # Les clés "demo" gratuites passent par l'API publique avec header dédié.
        return endpoints["coingecko"], {"x-cg-demo-api-key": key}
    return endpoints["coingecko"], {}


def get_market_data(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Récupère prix, MC, volume, variations 24h pour une liste de symboles.

    Args:
        symbols: tickers du portfolio (ex. ``["BTC", "ETH"]``).

    Returns:
        Dict ``{symbol: {price, market_cap, volume_24h, change_24h, ath,
        atl, change_from_ath_pct}}``. Symboles non résolus omis.
    """
    ids = [_CG_IDS[s] for s in symbols if s in _CG_IDS]
    if not ids:
        return {}
    id_to_symbol = {v: k for k, v in _CG_IDS.items()}

    base, headers = _base_and_headers()
    cache_key = "cg:markets:" + ",".join(sorted(ids))

    def _fetch() -> Optional[list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        # /coins/markets paginé par lots de 250 (large marge ici).
        for i in range(0, len(ids), 250):
            chunk = ids[i : i + 250]
            data = get_json(
                f"{base}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(chunk),
                    "price_change_percentage": "24h",
                },
                headers=headers,
            )
            if isinstance(data, list):
                out.extend(data)
        return out

    raw = CACHE.get_or_compute(cache_key, _CACHE_TTL.get("prices", 300), _fetch)
    result: dict[str, dict[str, Any]] = {}
    for coin in raw or []:
        sym = id_to_symbol.get(coin.get("id", ""))
        if not sym:
            continue
        ath = coin.get("ath") or 0
        price = coin.get("current_price") or 0
        change_from_ath = ((price - ath) / ath * 100) if ath else None
        result[sym] = {
            "price": price,
            "market_cap": coin.get("market_cap"),
            "volume_24h": coin.get("total_volume"),
            "change_24h": coin.get("price_change_percentage_24h"),
            "ath": ath,
            "atl": coin.get("atl"),
            "change_from_ath_pct": change_from_ath,
        }
    logger.info("CoinGecko : %d/%d symboles résolus.", len(result), len(symbols))
    return result


def get_global() -> dict[str, Any]:
    """Récupère les métriques globales : BTC dominance, total MC, total vol.

    Returns:
        Dict ``{available, total_market_cap_usd, total_volume_usd,
        btc_dominance_pct, market_cap_change_24h_pct}``.
    """
    base, headers = _base_and_headers()

    def _fetch() -> Optional[dict[str, Any]]:
        return get_json(f"{base}/global", headers=headers)

    raw = CACHE.get_or_compute("cg:global", 300, _fetch)
    if not raw or "data" not in raw:
        return {"available": False}
    d = raw["data"]
    return {
        "available": True,
        "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
        "total_volume_usd": d.get("total_volume", {}).get("usd"),
        "btc_dominance_pct": d.get("market_cap_percentage", {}).get("btc"),
        "eth_dominance_pct": d.get("market_cap_percentage", {}).get("eth"),
        "market_cap_change_24h_pct": d.get("market_cap_change_percentage_24h_usd"),
    }
