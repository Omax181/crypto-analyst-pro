"""Risque de portefeuille & force relative (v22 P1 #20/#42/#44/#45/#46).

Transforme une bonne reco ISOLÉE en bonne décision de PORTEFEUILLE. Tout est
calculé en Python pur, à partir de séries déjà collectées (clôtures datées
CoinGecko + valeurs des positions). Réutilise les primitives de ``correlation``.

  • Force relative vs BTC (#20)   : un alt qui sous-performe BTC n'est pas un bon hold.
  • Bêta-to-BTC par position (#42) : sensibilité au marché crypto, pour le sizing.
  • Concentration HHI (#44)        : nombre EFFECTIF de paris (diversification réelle).
  • Stress-test (#45)             : « si BTC −20%, le PTF fait ≈ X% ».
  • VaR historique 95% (#46)       : perte journalière de queue plausible.
"""

from __future__ import annotations

from typing import Any, Optional

from src.analytics.correlation import _align_returns, _beta, _pearson
from src.utils.logger import get_logger

logger = get_logger(__name__)

_MIN_ABS_CORR = 0.25   # bêta non significatif en deçà → ignoré
_BETA_CAP = 4.0        # au-delà = bruit sur 30j


def relative_strength_vs_btc(
    asset_dated: dict[str, float],
    btc_dated: dict[str, float],
    windows: tuple[int, ...] = (7, 30, 90),
) -> dict[str, Any]:
    """Surperformance (%) de l'actif vs BTC sur plusieurs fenêtres.

    RS = perf_actif − perf_BTC sur la fenêtre. Positif = surperforme BTC.

    Returns:
        Dict ``{available, rs: {window: pts}, reading}``.
    """
    common = sorted(set(asset_dated) & set(btc_dated))
    if len(common) < 8:
        return {"available": False}
    rs: dict[str, float] = {}
    for w in windows:
        if len(common) <= w:
            continue
        a0, a1 = asset_dated[common[-w - 1]], asset_dated[common[-1]]
        b0, b1 = btc_dated[common[-w - 1]], btc_dated[common[-1]]
        if a0 and b0:
            perf_a = (a1 - a0) / a0 * 100
            perf_b = (b1 - b0) / b0 * 100
            rs[f"{w}d"] = round(perf_a - perf_b, 1)
    if not rs:
        return {"available": False}
    # Fenêtre de référence pour la lecture : 30j si dispo, sinon la plus longue
    # disponible (90j) puis 7j — ne pas conclure « neutre » faute de 30j.
    ref = None
    ref_w = None
    for w in ("30d", "90d", "7d"):
        if w in rs:
            ref, ref_w = rs[w], w
            break
    if ref is not None and ref >= 5:
        reading = f"surperforme BTC (+{ref:.0f} pts sur {ref_w}) : leadership relatif"
    elif ref is not None and ref <= -5:
        reading = f"sous-performe BTC ({ref:.0f} pts sur {ref_w}) : faiblesse relative"
    else:
        reading = "en ligne avec BTC (pas d'avantage relatif net)"
    return {"available": True, "rs": rs, "reading": reading}


def _ptf_daily_returns(
    asset_dated: dict[str, dict[str, float]],
    position_values: dict[str, float],
) -> list[float]:
    """Série de rendements quotidiens du PTF pondéré (dates communes)."""
    series = {s: d for s, d in asset_dated.items()
              if d and (position_values.get(s) or 0) > 0}
    if len(series) < 1:
        return []
    common = sorted(set.intersection(*[set(d) for d in series.values()]))
    if len(common) < 11:
        return []
    total = sum(position_values.get(s, 0) for s in series) or 1.0
    ptf_returns: list[float] = []
    for i in range(1, len(common)):
        r = 0.0
        for s, dated in series.items():
            prev, cur = dated[common[i - 1]], dated[common[i]]
            if prev:
                r += (position_values.get(s, 0) / total) * (cur - prev) / prev
        ptf_returns.append(r)
    return ptf_returns


def compute_portfolio_risk(
    asset_dated: dict[str, dict[str, float]],
    btc_dated: dict[str, float],
    position_values: dict[str, float],
    *,
    btc_shock_pct: float = -20.0,
    window: int = 30,
) -> dict[str, Any]:
    """Bloc de risque portefeuille consolidé.

    Args:
        asset_dated: ``{symbol: {date: close}}`` par actif.
        btc_dated: ``{date: close}`` de BTC (référence marché).
        position_values: ``{symbol: valeur_usd}``.
        btc_shock_pct: choc BTC pour le stress-test (défaut −20%).
        window: fenêtre de rendements (jours).

    Returns:
        Dict ``{available, beta_to_btc, concentration, stress_test, var_95_pct,
        readings}``.
    """
    total = sum(v for v in position_values.values() if isinstance(v, (int, float)) and v > 0)
    if total <= 0:
        return {"available": False, "reason": "valeurs de positions absentes"}

    # --- Bêta-to-BTC par actif ---
    beta_to_btc: dict[str, float] = {}
    if btc_dated:
        for sym, dated in asset_dated.items():
            if sym == "BTC" or not dated:
                continue
            ra, rb = _align_returns(dated, btc_dated)
            if not ra or not rb:
                continue
            n = min(len(ra), len(rb), window)
            corr = _pearson(ra[-n:], rb[-n:])
            beta = _beta(ra[-n:], rb[-n:])
            if corr is None or abs(corr) < _MIN_ABS_CORR:
                continue
            if beta is None or abs(beta) > _BETA_CAP:
                continue
            beta_to_btc[sym] = round(beta, 2)

    # --- Concentration (HHI / nombre effectif de paris) ---
    weights = {s: (v / total) for s, v in position_values.items()
               if isinstance(v, (int, float)) and v > 0}
    hhi = sum(w * w for w in weights.values())
    effective_bets = round(1.0 / hhi, 1) if hhi > 0 else None
    top_asset = max(weights, key=weights.get) if weights else None
    top_weight = round(weights[top_asset] * 100, 1) if top_asset else None
    concentration = {
        "hhi": round(hhi, 4),
        "effective_bets": effective_bets,
        "positions": len(weights),
        "top_asset": top_asset,
        "top_weight_pct": top_weight,
    }

    # --- Stress-test : si BTC fait btc_shock, le PTF fait ≈ ptf_beta × choc ---
    # Bêta par défaut = 1.0 (un crypto sans bêta fiable suit ~BTC) ; BTC = 1.0.
    ptf_beta = 0.0
    for s, w in weights.items():
        b = 1.0 if s == "BTC" else beta_to_btc.get(s, 1.0)
        ptf_beta += w * b
    stress_move = round(ptf_beta * btc_shock_pct, 1)
    stress_test = {
        "btc_shock_pct": btc_shock_pct,
        "ptf_beta_to_btc": round(ptf_beta, 2),
        "ptf_estimated_move_pct": stress_move,
        "estimated_loss_usd": round(total * stress_move / 100, 0),
    }

    # --- VaR historique 95% (5e percentile des rendements quotidiens du PTF) ---
    ptf_returns = _ptf_daily_returns(asset_dated, position_values)
    var_95_pct: Optional[float] = None
    if len(ptf_returns) >= 10:
        ordered = sorted(ptf_returns)
        # 5e percentile (queue gauche) = perte journalière plausible à 95%.
        idx = min(len(ordered) - 1, max(0, int(0.05 * len(ordered))))
        var_95_pct = round(ordered[idx] * 100, 2)

    readings: list[str] = []
    if effective_bets is not None:
        readings.append(
            f"Concentration : {len(weights)} positions mais seulement "
            f"~{effective_bets} paris EFFECTIFs (HHI) — "
            + (f"{top_asset} pèse {top_weight}%."
               if top_asset else "réparti.")
        )
    readings.append(
        f"Stress-test : un choc BTC de {btc_shock_pct:.0f}% entraînerait ≈ "
        f"{stress_move:+.0f}% sur le portefeuille (bêta PTF {ptf_beta:.2f})."
    )
    if var_95_pct is not None:
        readings.append(
            f"VaR 95% (historique 30j) : journée de queue ≈ {var_95_pct:.1f}%."
        )

    return {
        "available": True,
        "beta_to_btc": beta_to_btc,
        "concentration": concentration,
        "stress_test": stress_test,
        "var_95_pct": var_95_pct,
        "readings": readings,
    }
