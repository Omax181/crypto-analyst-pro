"""Source CoinMarketCap (cross-validation des prix CoinGecko).

Free tier : 10 000 calls/mois. Utilisé en complément pour détecter des
écarts de prix anormaux entre sources.
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")


def get_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Récupère les prix CMC pour cross-check.

    Args:
        symbols: tickers (ex. ``["BTC", "ETH"]``). CMC utilise les tickers
            directement (pas d'id à mapper).

    Returns:
        Dict ``{symbol: {price, change_24h}}`` ou ``{}`` si clé absente/échec.
    """
    key = os.environ.get("COINMARKETCAP_API_KEY", "").strip()
    if not key:
        logger.info("CMC : pas de clé, cross-check ignoré.")
        return {}

    base = _SOURCES["endpoints"]["coinmarketcap"]
    cache_key = "cmc:" + ",".join(sorted(symbols))

    def _fetch() -> Any:
        return get_json(
            f"{base}/cryptocurrency/quotes/latest",
            params={"symbol": ",".join(symbols), "convert": "USD"},
            headers={"X-CMC_PRO_API_KEY": key},
        )

    raw = CACHE.get_or_compute(cache_key, 300, _fetch)
    if not raw or raw.get("status", {}).get("error_code", 1) != 0:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for sym, payload in (raw.get("data") or {}).items():
        # CMC peut renvoyer une liste si plusieurs tokens partagent un ticker.
        entry = payload[0] if isinstance(payload, list) else payload
        quote = entry.get("quote", {}).get("USD", {})
        out[sym] = {
            "price": quote.get("price"),
            "change_24h": quote.get("percent_change_24h"),
        }
    return out


def cross_check(
    cg_data: dict[str, dict[str, Any]], cmc_data: dict[str, dict[str, Any]]
) -> dict[str, float]:
    """Compare les prix CG vs CMC, renvoie les écarts >2%.

    Returns:
        Dict ``{symbol: ecart_pct}`` pour les divergences notables.
    """
    discrepancies: dict[str, float] = {}
    for sym, cg in cg_data.items():
        cmc = cmc_data.get(sym)
        if not cmc or not cg.get("price") or not cmc.get("price"):
            continue
        diff = abs(cg["price"] - cmc["price"]) / cg["price"] * 100
        if diff > 2.0:
            discrepancies[sym] = round(diff, 2)
    if discrepancies:
        logger.warning("Écarts de prix CG/CMC >2%% : %s", discrepancies)
    return discrepancies
