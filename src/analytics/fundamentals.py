"""Indicateurs fondamentaux et helpers de calcul sûrs.

Contient notamment ``compute_ath_distance`` qui corrige le bug "-100% ATH"
(mathématiquement impossible tant que le prix courant est > 0).
"""

from __future__ import annotations

from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Borne basse : un actif coté > 0 ne peut être exactement à -100% de son ATH.
_ATH_FLOOR_PCT = -99.99


def compute_ath_distance(current_price: float, ath: float) -> Optional[float]:
    """Calcule la distance (%) au plus-haut historique, bornée à -99.99%.

    Args:
        current_price: prix courant (USD).
        ath: all-time high (USD).

    Returns:
        Distance en pourcentage (négative si sous l'ATH), clampée à
        ``-99.99``. ``None`` si les entrées sont invalides (prix/ath <= 0).

    Examples:
        >>> compute_ath_distance(0.005, 5.0)
        -99.9
        >>> round(compute_ath_distance(50.0, 100.0), 1)
        -50.0
    """
    if current_price is None or ath is None:
        return None
    if current_price <= 0 or ath <= 0:
        return None
    pct = ((current_price - ath) / ath) * 100.0
    return max(pct, _ATH_FLOOR_PCT)


def fundamental_score_from_signals(
    *,
    dev_activity: dict,
    tvl_trend: Optional[str] = None,
    revenue_trend: Optional[str] = None,
) -> float:
    """Score fondamental [0,100] combinant dev, TVL et revenus.

    Les commits GitHub ne représentent au maximum que la moitié de ce
    sous-score (donc ~5% du score composite total, cf. SIGNAL_WEIGHTS), pour
    ne jamais surpondérer une absence de commit.

    Args:
        dev_activity: sortie de ``github_dev.get_dev_activity``.
        tvl_trend: ``"up"``/``"flat"``/``"down"`` ou ``None`` si indisponible.
        revenue_trend: idem pour les revenus protocolaires.

    Returns:
        Score [0,100]. 50 = neutre.
    """
    neutral = 50.0
    # Composante dev : amplitude limitée à +/-25 autour du neutre.
    dev_component = 0.0
    if dev_activity.get("available"):
        commits = dev_activity.get("commits_30d", 0) or 0
        days_ago = dev_activity.get("last_commit_days_ago")
        if commits >= 50:
            dev_component += 15
        elif commits >= 10:
            dev_component += 9
        elif commits >= 1:
            dev_component += 3
        else:
            dev_component -= 10
        if days_ago is not None:
            if days_ago <= 3:
                dev_component += 5
            elif days_ago > 30:
                dev_component -= 8
    dev_component = max(-25.0, min(25.0, dev_component))

    trend_component = 0.0
    for trend in (tvl_trend, revenue_trend):
        if trend == "up":
            trend_component += 10
        elif trend == "down":
            trend_component -= 10
    trend_component = max(-25.0, min(25.0, trend_component))

    return max(0.0, min(100.0, neutral + dev_component + trend_component))
