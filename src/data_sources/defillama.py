"""DeFiLlama : TVL global et par protocole (API publique, sans clé)."""
from __future__ import annotations
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

_SYMBOL_TO_SLUG = {"LINK": "chainlink", "UNI": "uniswap", "INJ": "injective",
    "YFI": "yearn-finance", "GRT": "the-graph", "AXL": "axelar", "RSR": "reserve-protocol"}


def get_defi_tvl() -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            chains = get_json("https://api.llama.fi/v2/chains")
            protocols = get_json("https://api.llama.fi/protocols")
            total = sum(c.get("tvl", 0) for c in chains) if isinstance(chains, list) else None
            top = []
            if isinstance(protocols, list):
                for p in sorted(protocols, key=lambda x: x.get("tvl", 0) or 0, reverse=True)[:8]:
                    top.append({"name": p.get("name"), "tvl": p.get("tvl"), "change_1d": p.get("change_1d")})
            return {"available": True, "total_tvl_usd": total, "top_protocols": top}
        except Exception as exc:
            logger.warning("DeFiLlama : %s", exc)
            return {"available": False}
    return CACHE.get_or_compute("defillama:tvl", 3600, _fetch)


def get_protocol_tvl(symbol: str) -> dict[str, Any]:
    slug = _SYMBOL_TO_SLUG.get(symbol)
    if not slug:
        return {"available": False}
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(f"https://api.llama.fi/protocol/{slug}")
            if not data:
                return {"available": False}
            tvl_series = data.get("tvl") or []
            tvl_now = tvl_series[-1].get("totalLiquidityUSD") if tvl_series else None
            trend = None
            if len(tvl_series) >= 8:
                prev = tvl_series[-8].get("totalLiquidityUSD")
                if prev and tvl_now:
                    trend = "up" if tvl_now > prev * 1.03 else "down" if tvl_now < prev * 0.97 else "flat"
            return {"available": True, "tvl_usd": tvl_now, "tvl_trend_7d": trend,
                    "name": data.get("name"), "category": data.get("category")}
        except Exception as exc:
            logger.warning("DeFiLlama %s : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"defillama:{symbol}", 3600, _fetch)
