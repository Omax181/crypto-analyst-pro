"""Régime de marché explicite (v27 · ME1) — bull / bear / range / transition.

Classification DÉTERMINISTE depuis la structure BTC (le « temps qu'il fait »
qui cadre tout le reste : agressivité des recos, lecture des scénarios) :
    • bull       : prix > MM200, MM50 > MM200, pente MM50 positive ;
    • bear       : prix < MM200, MM50 < MM200, pente MM50 négative ;
    • transition : croisement MM50/MM200 récent (≤ 14 jours) ;
    • range      : tout le reste (signaux mixtes).

Le régime est persisté (state) : chaque mail affiche « Régime : RANGE
(depuis N j) » et signale explicitement un CHANGEMENT de régime — c'est le
signal le plus important qu'un lecteur pressé doit voir.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

REGIME_LABELS_FR = {
    "bull": "HAUSSIER",
    "bear": "BAISSIER",
    "range": "RANGE",
    "transition": "TRANSITION",
}


def _sma_series(closes: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def classify_regime(closes: list[float]) -> dict[str, Any]:
    """Classifie le régime courant depuis une série daily BTC (≥ 210 closes).

    Returns:
        ``{available, regime, label_fr, reasons: [str], price_vs_ma200_pct,
        ma50_vs_ma200_pct, ma50_slope_14d_pct}`` — ``available=False`` si la
        série est trop courte pour une MM200 fiable.
    """
    series = [float(c) for c in (closes or []) if isinstance(c, (int, float))]
    if len(series) < 210:
        return {"available": False,
                "reason": f"série trop courte ({len(series)} < 210 closes)"}
    px = series[-1]
    ma50_s = _sma_series(series, 50)
    ma200_s = _sma_series(series, 200)
    ma50, ma200 = ma50_s[-1], ma200_s[-1]
    if not ma50 or not ma200 or px <= 0:
        return {"available": False, "reason": "MM incalculables"}

    slope = (ma50 - ma50_s[-15]) / ma50_s[-15] * 100 if ma50_s[-15] else 0.0
    px_vs_200 = (px - ma200) / ma200 * 100
    m50_vs_200 = (ma50 - ma200) / ma200 * 100

    # Croisement MM50/MM200 dans les 14 derniers jours → TRANSITION.
    crossed_recently = False
    for i in range(len(series) - 14, len(series)):
        a, b = ma50_s[i - 1], ma50_s[i]
        c, d = ma200_s[i - 1], ma200_s[i]
        if a and b and c and d and ((a - c) * (b - d) < 0):
            crossed_recently = True
            break

    reasons: list[str] = [
        f"prix {'+' if px_vs_200 >= 0 else '−'}{abs(px_vs_200):.1f}% vs MM200",
        f"MM50 {'+' if m50_vs_200 >= 0 else '−'}{abs(m50_vs_200):.1f}% vs MM200",
        f"pente MM50 14j {'+' if slope >= 0 else '−'}{abs(slope):.1f}%",
    ]
    if crossed_recently:
        regime = "transition"
        reasons.append("croisement MM50/MM200 ≤ 14 j")
    elif px > ma200 and ma50 > ma200 and slope > 0:
        regime = "bull"
    elif px < ma200 and ma50 < ma200 and slope < 0:
        regime = "bear"
    else:
        regime = "range"

    return {
        "available": True,
        "regime": regime,
        "label_fr": REGIME_LABELS_FR[regime],
        "reasons": reasons,
        "price_vs_ma200_pct": round(px_vs_200, 1),
        "ma50_vs_ma200_pct": round(m50_vs_200, 1),
        "ma50_slope_14d_pct": round(slope, 1),
    }


def with_persistence(current: dict[str, Any], today_iso: str) -> dict[str, Any]:
    """Enrichit le régime courant de la continuité (depuis quand, changement).

    Lit/écrit ``state/market_regime.json`` via report_memory. Le flag
    ``changed`` + ``previous`` permettent au rendu d'afficher l'alerte
    « ⚠ changement de régime bear → range ».
    """
    if not current.get("available"):
        return current
    from src.state import report_memory as mem
    prev = mem.load_market_regime()
    out = dict(current)
    if prev.get("regime") == current["regime"]:
        out["since"] = prev.get("since") or today_iso
        out["changed"] = False
    else:
        out["since"] = today_iso
        out["changed"] = bool(prev.get("regime"))
        if prev.get("regime"):
            out["previous"] = prev["regime"]
            out["previous_label_fr"] = REGIME_LABELS_FR.get(
                prev["regime"], prev["regime"])
    try:
        _d1 = datetime_from_iso(out["since"])
        _d2 = datetime_from_iso(today_iso)
        if _d1 and _d2:
            out["days_in_regime"] = max((_d2 - _d1).days, 0)
    except Exception:  # noqa: BLE001
        pass
    mem.save_market_regime({"regime": out["regime"], "since": out["since"]})
    return out


def datetime_from_iso(s: Any):
    """Parse tolérant d'une date ISO (None si invalide)."""
    from datetime import date
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None
