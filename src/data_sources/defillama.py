"""DeFiLlama : TVL global et par protocole (API publique, sans clé)."""
from __future__ import annotations
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

def get_defi_tvl() -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            global_data = get_json("https://api.llama.fi/v2/chains")
            protocols = get_json("https://api.llama.fi/protocols")
            total_tvl = sum(c.get("tvl", 0) for c in (global_data or [])) if isinstance(global_data, list) else None
            top_protocols = []
            if isinstance(protocols, list):
                sorted_p = sorted(protocols, key=lambda x: x.get("tvl", 0), reverse=True)[:10]
                for p in sorted_p:
                    top_protocols.append({"name": p.get("name"), "tvl": p.get("tvl"), "change_1d": p.get("change_1d")})
            return {"available": True, "total_tvl_usd": total_tvl, "top_protocols": top_protocols}
        except Exception as exc:
            logger.warning("DeFiLlama indisponible : %s", exc)
            return {"available": False}
    return CACHE.get_or_compute("defillama:tvl", 3600, _fetch)

def get_protocol_tvl(symbol: str) -> dict[str, Any]:
    SYMBOL_TO_SLUG = {"LINK": "chainlink", "AAVE": "aave", "UNI": "uniswap", "INJ": "injective", "YFI": "yearn-finance", "GRT": "the-graph", "AXL": "axelar"}
    slug = SYMBOL_TO_SLUG.get(symbol)
    if not slug:
        return {"available": False, "reason": "pas de mapping DeFiLlama"}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"https://api.llama.fi/protocol/{slug}")
            if not data:
                return {"available": False}
            tvl_now = data.get("tvl", [{}])[-1].get("totalLiquidityUSD") if data.get("tvl") else None
            return {"available": True, "tvl_usd": tvl_now, "name": data.get("name"), "category": data.get("category")}
        except Exception as exc:
            logger.warning("DeFiLlama %s : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"defillama:{symbol}", 3600, _fetch)
