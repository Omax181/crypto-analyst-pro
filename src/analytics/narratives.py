"""Rotation narrative sectorielle et mapping vers les positions du portfolio.

Classe les actifs du portfolio par secteur narratif (AI, L1, L2, DeFi,
infra/oracle, etc.) et calcule la performance moyenne 24h par secteur pour
détecter les rotations en cours.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Mapping symbole -> secteur narratif.
NARRATIVES: dict[str, str] = {
    # AI / DePIN compute
    "TAO": "AI",
    "RENDER": "AI",
    "FET": "AI",
    "WLD": "AI",
    "W": "AI",  # marqué AI dans le PTF (Wormhole)
    "NMR": "AI",
    # Layer 1
    "BTC": "L1",
    "ETH": "L1",
    "ADA": "L1",
    "ATOM": "L1",
    "HBAR": "L1",
    "STX": "L1",
    "CKB": "L1",
    "CELO": "L1",
    "AR": "L1",
    "FIL": "Storage/DePIN",
    # Layer 2 / scaling
    "ARB": "L2",
    "IMX": "L2",
    "ZK": "L2",
    "CFX": "L2",
    # DeFi
    "INJ": "DeFi",
    "RSR": "DeFi",
    "YFI": "DeFi",
    "ACH": "Payments",
    # Infra / oracle / interop
    "LINK": "Oracle/Infra",
    "QNT": "Oracle/Infra",
    "GRT": "Indexing/Infra",
    "AXL": "Interop",
    "ANKR": "Infra",
    # Payments / autres
    "XRP": "Payments",
    "JASMY": "IoT/Data",
    # Memes / divers
    "NOT": "Meme/Gaming",
    "HMSTR": "Meme/Gaming",
    "SATS": "Ordinals",
    "SXT": "Data",
    "TRB": "Oracle/Infra",
    "ZEN": "Privacy/L1",
    "USDC": "Stablecoin",
}


def sector_rotation(market: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Calcule la performance moyenne par secteur narratif (24h, 7j, 30j).

    v18 (M-B13) — en plus du 24h, on agrège le 7j et le 30j quand CoinGecko les
    fournit (``change_7d`` / ``change_30d``). Permet de distinguer un vrai mouvement
    de fond d'un soubresaut intraday.

    Args:
        market: dict ``{symbol: {change_24h, change_7d, change_30d, ...}}``.

    Returns:
        Dict ``{sectors: {sector: {avg_change_24h, avg_change_7d, avg_change_30d,
        members}}, leaders, laggards}``.
    """
    buckets: dict[str, list[float]] = {}
    buckets_7d: dict[str, list[float]] = {}
    buckets_30d: dict[str, list[float]] = {}
    members: dict[str, list[str]] = {}
    for sym, data in market.items():
        sector = NARRATIVES.get(sym, "Autre")
        if sector == "Stablecoin":
            continue
        change = data.get("change_24h")
        if change is None:
            continue
        buckets.setdefault(sector, []).append(change)
        members.setdefault(sector, []).append(sym)
        _c7 = data.get("change_7d")
        if isinstance(_c7, (int, float)):
            buckets_7d.setdefault(sector, []).append(_c7)
        _c30 = data.get("change_30d")
        if isinstance(_c30, (int, float)):
            buckets_30d.setdefault(sector, []).append(_c30)

    sectors: dict[str, Any] = {}
    for sector, changes in buckets.items():
        _e = {
            "avg_change_24h": round(sum(changes) / len(changes), 2),
            "members": members[sector],
        }
        if buckets_7d.get(sector):
            _e["avg_change_7d"] = round(sum(buckets_7d[sector]) / len(buckets_7d[sector]), 2)
        if buckets_30d.get(sector):
            _e["avg_change_30d"] = round(sum(buckets_30d[sector]) / len(buckets_30d[sector]), 2)
        sectors[sector] = _e

    ranked = sorted(
        sectors.items(), key=lambda kv: kv[1]["avg_change_24h"], reverse=True
    )
    leaders = [s for s, _ in ranked[:2]] if ranked else []
    laggards = [s for s, _ in ranked[-2:]] if len(ranked) > 1 else []

    return {"sectors": sectors, "leaders": leaders, "laggards": laggards}
