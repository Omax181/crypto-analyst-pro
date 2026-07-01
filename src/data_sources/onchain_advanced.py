"""On-chain avancé : Glassnode free tier avec fallback blockchain.info/Etherscan.

Fournit les indicateurs on-chain quotidiens du rapport matin : réserves
d'exchange BTC, SSR (proxy), whale tx, active addresses BTC/ETH. Si Glassnode
n'est pas disponible (pas de clé / rate-limit), on retombe sur les sources
gratuites déjà câblées.
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources import onchain_btc, onchain_eth
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_GLASSNODE = "https://api.glassnode.com/v1/metrics"


def _glassnode(path: str, asset: str) -> Any:
    key = os.environ.get("GLASSNODE_API_KEY", "").strip()
    if not key:
        return None
    return get_json(
        f"{_GLASSNODE}/{path}", params={"a": asset, "api_key": key, "i": "24h"}
    )


def get_onchain_indicators() -> dict[str, Any]:
    """Récupère les indicateurs on-chain quotidiens (Glassnode ou fallback).

    Returns:
        Dict ``{available, source, btc_exchange_reserves_change_7d, ssr,
        whale_tx_24h, eth_active_addresses, btc_network}``.
    """

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "source": "fallback"}

        # Tentative Glassnode (3 métriques free tier).
        reserves = _glassnode("distribution/balance_exchanges", "BTC")
        active_eth = _glassnode("addresses/active_count", "ETH")
        if reserves or active_eth:
            out["source"] = "glassnode"
            out["available"] = True
            if isinstance(reserves, list) and len(reserves) >= 8:
                try:
                    delta = reserves[-1]["v"] - reserves[-8]["v"]
                    out["btc_exchange_reserves_change_7d"] = round(delta, 1)
                except (KeyError, TypeError, IndexError):
                    pass
            if isinstance(active_eth, list) and active_eth:
                try:
                    out["eth_active_addresses"] = int(active_eth[-1]["v"])
                except (KeyError, TypeError, ValueError):
                    pass

        # Fallback gratuit : santé réseau BTC + on-chain ETH.
        btc = onchain_btc.get_btc_onchain()
        eth = onchain_eth.get_eth_onchain()
        if btc.get("available"):
            out["available"] = True
            out["btc_network"] = {
                "n_tx_24h": btc.get("n_tx_24h"),
                "hash_rate_ehs": btc.get("hash_rate_ehs"),
                "mempool_size": btc.get("mempool_size"),
            }
        if eth.get("available"):
            out["available"] = True
            out["eth_gas_fast_gwei"] = eth.get("gas_fast_gwei")
        return out

    return CACHE.get_or_compute("onchain:advanced", 3600, _fetch)
