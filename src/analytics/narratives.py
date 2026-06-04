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
    """Calcule la performance 24h moyenne par secteur narratif.

    Args:
        market: dict ``{symbol: {change_24h, ...}}`` (CoinGecko).

    Returns:
        Dict ``{sectors: {sector: {avg_change_24h, members}},
        leaders, laggards}``.
    """
    buckets: dict[str, list[float]] = {}
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

    sectors: dict[str, Any] = {}
    for sector, changes in buckets.items():
        sectors[sector] = {
            "avg_change_24h": round(sum(changes) / len(changes), 2),
            "members": members[sector],
        }

    ranked = sorted(
        sectors.items(), key=lambda kv: kv[1]["avg_change_24h"], reverse=True
    )
    leaders = [s for s, _ in ranked[:2]] if ranked else []
    laggards = [s for s, _ in ranked[-2:]] if len(ranked) > 1 else []

    return {"sectors": sectors, "leaders": leaders, "laggards": laggards}
