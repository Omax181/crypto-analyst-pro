"""Source on-chain ETH : Etherscan (gas, prix ETH, supply).

Free tier : 5 calls/sec, 100k/jour. La clé est requise pour des limites
correctes ; sans clé on tente quand même mais Etherscan peut rejeter.
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
_BASE = _SOURCES["endpoints"]["etherscan"]


def _key() -> str:
    return os.environ.get("ETHERSCAN_API_KEY", "").strip()


def get_eth_onchain() -> dict[str, Any]:
    """Récupère gas price et prix ETH via Etherscan.

    Returns:
        Dict ``{available, gas_safe_gwei, gas_propose_gwei, gas_fast_gwei,
        eth_price_usd}``.
    """
    key = _key()
    if not key:
        logger.info("Etherscan : pas de clé, on-chain ETH ignoré.")
        return {"available": False}

    def _fetch_gas() -> Any:
        return get_json(
            _BASE,
            params={"module": "gastracker", "action": "gasoracle", "apikey": key},
        )

    gas = CACHE.get_or_compute("eth:gas", 600, _fetch_gas)
    result: dict[str, Any] = {"available": False}
    if gas and gas.get("status") == "1":
        r = gas.get("result", {})
        result.update(
            {
                "available": True,
                "gas_safe_gwei": _to_float(r.get("SafeGasPrice")),
                "gas_propose_gwei": _to_float(r.get("ProposeGasPrice")),
                "gas_fast_gwei": _to_float(r.get("FastGasPrice")),
            }
        )

    def _fetch_price() -> Any:
        return get_json(
            _BASE,
            params={"module": "stats", "action": "ethprice", "apikey": key},
        )

    price = CACHE.get_or_compute("eth:price", 300, _fetch_price)
    if price and price.get("status") == "1":
        result["available"] = True
        result["eth_price_usd"] = _to_float(price.get("result", {}).get("ethusd"))

    return result


def _to_float(value: Any) -> Any:
    """Convertit en float si possible, sinon ``None``."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
