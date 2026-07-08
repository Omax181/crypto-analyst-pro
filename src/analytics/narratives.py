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
    # v28 (M-A9) — « Indexing/Infra » fusionné dans « Infra » : le 07/07, les
    # tuiles de rotation affichaient DEUX secteurs quasi homonymes
    # (« Indexing/Infra » pour GRT, « Infra » pour ANKR) — taxonomie confuse.
    "GRT": "Infra",
    "AXL": "Interop",
    # v26 (W-A17) — W = Wormhole, un BRIDGE cross-chain : c'est de l'interop,
    # pas de l'IA (l'audit a vu « AI : FET, RENDER, TAO, W » dans l'exposition
    # sectorielle du hebdo).
    "W": "Interop",
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


# ── OB6 — DÉTECTION DE NARRATIFS ÉMERGENTS (gratuit, remplace Kaito mort) ─────
# Quels narratifs (catégories CoinGecko) chauffent/refroidissent sur 24h. On
# FILTRE le bruit : (1) market cap + volume minimum (sinon des micro-caps à +80 %
# polluent le signal), (2) les groupements « … Ecosystem » = chaînes entières,
# pas des narratifs thématiques. Signal clé pour la rotation des satellites d'Omar.
_NARR_MIN_MCAP = 500_000_000.0
_NARR_MIN_VOL = 5_000_000.0
_NARR_HOT_PCT = 3.0
_NARR_TOP_N = 4
# Plafond de variation 24h : au-delà, c'est presque toujours un ARTEFACT de
# composition CoinGecko (un gros coin ajouté/retiré de la catégorie), pas un vrai
# mouvement uniforme → on l'écarte pour ne pas polluer le signal.
_NARR_MAX_ABS_PCT = 60.0
_NARR_EXCLUDE = {
    "smart contract platform", "layer 1 (l1)", "layer 0 (l0)",
    "centralized exchange (cex) token", "stablecoins", "wrapped-tokens",
}


def _is_thematic_narrative(name: str) -> bool:
    """Exclut les catégories non-thématiques (écosystèmes de chaînes, méta).

    « ecosystem » N'IMPORTE OÙ dans le nom = groupement de chaîne (ex.
    « Solana Ecosystem », « Four.meme Ecosystem (BNB Memes) ») → écarté.
    """
    low = name.lower().strip()
    if "ecosystem" in low:
        return False
    return low not in _NARR_EXCLUDE


def detect_hot_narratives(
    cats_result: dict[str, Any],
    *,
    min_mcap: float = _NARR_MIN_MCAP,
    min_vol: float = _NARR_MIN_VOL,
    top_n: int = _NARR_TOP_N,
) -> dict[str, Any]:
    """Narratifs qui chauffent / refroidissent (24h) depuis les catégories CoinGecko.

    Args:
        cats_result: sortie de ``coingecko.get_categories()``.

    Returns:
        ``{available, hot: [...], cold: [...], reading}``. ``available=False`` si
        la source est indisponible ou rien de significatif après filtrage.
    """
    if not cats_result.get("available"):
        return {"available": False}
    rows = []
    for c in cats_result.get("categories") or []:
        name = str(c.get("name") or "")
        mcap = c.get("market_cap") or 0.0
        vol = c.get("volume_24h") or 0.0
        chg = c.get("change_24h")
        if (chg is None or mcap < min_mcap or vol < min_vol
                or abs(chg) > _NARR_MAX_ABS_PCT
                or not _is_thematic_narrative(name)):
            continue
        rows.append(c)
    if len(rows) < 2:
        return {"available": False}
    rows.sort(key=lambda c: c["change_24h"], reverse=True)
    hot = [c for c in rows[:top_n] if c["change_24h"] >= _NARR_HOT_PCT]
    cold = [c for c in rows[::-1][:2] if c["change_24h"] < 0]

    def _fmt(lst: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "name": c["name"],
            "change_24h": c["change_24h"],
            "market_cap": c["market_cap"],
            "top_coins": c.get("top_coins", []),
        } for c in lst]

    parts: list[str] = []
    if hot:
        parts.append("🔥 " + ", ".join(
            f"{c['name']} {c['change_24h']:+.1f}%" for c in hot))
    if cold:
        parts.append("🧊 " + ", ".join(
            f"{c['name']} {c['change_24h']:+.1f}%" for c in cold))
    return {
        "available": bool(hot or cold),
        "hot": _fmt(hot),
        "cold": _fmt(cold),
        "reading": " | ".join(parts),
    }
