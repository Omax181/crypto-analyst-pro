"""Source on-chain BTC : blockchain.info (hashrate, mempool, stats réseau).

Aucune authentification. Sert d'indicateur de santé du réseau Bitcoin.
"""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_STATS = _SOURCES["endpoints"]["blockchain_stats"]
_BASE = _SOURCES["endpoints"]["blockchain_info"]


def get_btc_onchain() -> dict[str, Any]:
    """Récupère les stats réseau BTC.

    Returns:
        Dict ``{available, hash_rate_ehs, n_tx_24h, mempool_size,
        miners_revenue_usd, difficulty}``.
    """

    def _fetch_stats() -> Any:
        return get_json(_STATS, params={"format": "json"})

    stats = CACHE.get_or_compute("btc:stats", 3600, _fetch_stats)
    if not stats:
        return {"available": False}

    # hash_rate est en GH/s -> conversion en EH/s.
    hash_rate_ghs = stats.get("hash_rate", 0)
    result: dict[str, Any] = {
        "available": True,
        "hash_rate_ehs": round(hash_rate_ghs / 1e9, 2) if hash_rate_ghs else None,
        "n_tx_24h": stats.get("n_tx"),
        "miners_revenue_usd": stats.get("miners_revenue_usd"),
        "difficulty": stats.get("difficulty"),
        "market_price_usd": stats.get("market_price_usd"),
    }

    def _fetch_mempool() -> Any:
        return get_json(f"{_BASE}/q/unconfirmedcount")

    mempool = CACHE.get_or_compute("btc:mempool", 600, _fetch_mempool)
    result["mempool_size"] = mempool if isinstance(mempool, int) else None
    return result
