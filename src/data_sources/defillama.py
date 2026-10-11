"""DeFiLlama : TVL global et par protocole (API publique, sans clé)."""
from __future__ import annotations
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

_SYMBOL_TO_SLUG = {"LINK": "chainlink", "UNI": "uniswap", "INJ": "injective",
    "YFI": "yearn-finance", "GRT": "the-graph", "AXL": "axelar", "RSR": "reserve-protocol",
    "AAVE": "aave", "CRV": "curve-dex", "LDO": "lido", "MKR": "makerdao",
    "PENDLE": "pendle", "GMX": "gmx", "SNX": "synthetix", "COMP": "compound",
    "DYDX": "dydx", "RUNE": "thorchain", "CAKE": "pancakeswap", "SUSHI": "sushiswap"}


def _protocols_raw() -> list[dict[str, Any]]:
    """Liste brute ``/protocols`` (≈ 8 400 protocoles), téléchargée UNE fois par run.

    Elle était déjà téléchargée par ``get_defi_tvl`` pour n'en garder que le
    top 8. Le moteur d'opportunité en a besoin en entier : c'est la seule
    population de PAIRS de même catégorie disponible sans clé.
    """
    def _fetch() -> list[dict[str, Any]]:
        try:
            data = get_json("https://api.llama.fi/protocols")
        except Exception as exc:  # noqa: BLE001
            logger.warning("DeFiLlama /protocols : %s", exc)
            return []
        return data if isinstance(data, list) else []
    return CACHE.get_or_compute("defillama:protocols", 3600, _fetch)


def get_category_peers(category: Any, *, exclude_name: Any = None) -> dict[str, Any]:
    """Ratios MC/TVL MESURÉS des protocoles de la même catégorie DeFiLlama.

    Audit zero-trust (01/10) : le moteur comparait le MC/TVL d'un actif à celui
    des autres positions DÉTENUES — un oracle, un index et un bridge le 30/09.
    Ce ne sont pas des pairs. La seule référence défendable est la catégorie,
    mesurée sur toute la base DeFiLlama, candidat exclu. Seuls les protocoles
    portant à la fois une capitalisation et une TVL strictement positives sont
    retenus : un ratio sans l'un des deux n'est pas une mesure.

    Returns:
        ``{available, category, ratios, n, source}``.
    """
    cat = str(category or "").strip()
    if not cat:
        return {"available": False, "category": None, "ratios": [], "n": 0}
    excl = str(exclude_name or "").strip().lower()
    ratios: list[float] = []
    for p in _protocols_raw():
        if not isinstance(p, dict) or str(p.get("category") or "") != cat:
            continue
        if excl and str(p.get("name") or "").strip().lower() == excl:
            continue
        mc, tv = p.get("mcap"), p.get("tvl")
        if (isinstance(mc, (int, float)) and isinstance(tv, (int, float))
                and mc > 0 and tv > 0):
            ratios.append(float(mc) / float(tv))
    return {"available": bool(ratios), "category": cat, "ratios": ratios,
            "n": len(ratios), "source": "DeFiLlama /protocols"}


def get_defi_tvl() -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            chains = get_json("https://api.llama.fi/v2/chains")
            protocols = _protocols_raw()
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


def get_protocol_fees(symbol: str) -> dict[str, Any]:
    """Frais et revenus réels d'un protocole DeFi (v22 #2 — DeFiLlama, sans clé).

    Les frais (ce que paient les utilisateurs) et les revenus (ce qui revient au
    protocole/holders) sont la base d'une valorisation FONDAMENTALE : « le
    protocole gagne-t-il vraiment de l'argent ». Annualisés depuis la fenêtre 30j
    (plus stable que 24h). ``{available: False}`` pour les non-DeFi (la plupart
    des alts d'Omar) — honnête, pas de donnée inventée.

    Returns:
        Dict ``{available, fees_24h, fees_annualized, revenue_24h,
        revenue_annualized}``.
    """
    slug = _SYMBOL_TO_SLUG.get(symbol)
    if not slug:
        return {"available": False}

    def _summary(data_type: str) -> tuple[float | None, float | None]:
        data = get_json(
            f"https://api.llama.fi/summary/fees/{slug}",
            params={"dataType": data_type},
        )
        if not isinstance(data, dict):
            return None, None
        t24 = data.get("total24h")
        t30 = data.get("total30d")
        annual = (t30 / 30.0 * 365.0) if isinstance(t30, (int, float)) and t30 else None
        return (t24 if isinstance(t24, (int, float)) else None), annual

    def _fetch() -> dict[str, Any]:
        try:
            fees_24h, fees_annual = _summary("dailyFees")
            rev_24h, rev_annual = _summary("dailyRevenue")
            if fees_24h is None and fees_annual is None and rev_annual is None:
                return {"available": False}
            return {
                "available": True,
                "fees_24h": fees_24h,
                "fees_annualized": round(fees_annual, 0) if fees_annual else None,
                "revenue_24h": rev_24h,
                "revenue_annualized": round(rev_annual, 0) if rev_annual else None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("DeFiLlama fees %s : %s", symbol, exc)
            return {"available": False}

    return CACHE.get_or_compute(f"defillama:fees:{symbol}", 3600, _fetch)
