"""Analytique technique : lecture multi-timeframe -> score 0-100.

Convertit les recommandations TradingView de chaque timeframe en un score
pondéré, avec bonus/malus selon divergences RSI et volume.
"""

from __future__ import annotations

from typing import Any

from src.data_sources.tradingview import detect_rsi_divergence
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_TF_WEIGHTS: dict[str, float] = _TH["technical_timeframe_weights"]
_SIGNAL_SCORES: dict[str, int] = _TH["tv_signal_scores"]


def evaluate_technical(technical: dict[str, Any]) -> dict[str, Any]:
    """Calcule un score technique pondéré multi-TF.

    Args:
        technical: sortie de ``tradingview.get_technical``.

    Returns:
        Dict ``{score, dominant_signal, divergence, per_tf}``. ``score`` ∈
        [0,100] ou ``None`` si données indisponibles.
    """
    if not technical.get("available"):
        return {"score": None, "dominant_signal": None, "divergence": None, "per_tf": {}}

    signals = technical.get("signals", {})
    weighted_sum = 0.0
    weight_total = 0.0
    per_tf: dict[str, Any] = {}

    for tf, weight in _TF_WEIGHTS.items():
        tf_data = signals.get(tf)
        if not tf_data:
            continue
        reco = tf_data.get("recommendation")
        score = _SIGNAL_SCORES.get(reco, 50)
        weighted_sum += score * weight
        weight_total += weight
        per_tf[tf] = {"recommendation": reco, "score": score, "rsi": tf_data.get("rsi")}

    if weight_total == 0:
        return {"score": None, "dominant_signal": None, "divergence": None, "per_tf": {}}

    base_score = weighted_sum / weight_total

    # Bonus/malus divergence RSI sur le daily.
    divergence = detect_rsi_divergence(technical)
    if divergence == "oversold":
        base_score = min(base_score + 5, 100)
    elif divergence == "overbought":
        base_score = max(base_score - 5, 0)

    dominant = _score_to_label(base_score)
    return {
        "score": round(base_score, 1),
        "dominant_signal": dominant,
        "divergence": divergence,
        "per_tf": per_tf,
    }


def _score_to_label(score: float) -> str:
    """Convertit un score 0-100 en label sémantique."""
    if score >= 80:
        return "STRONG_BUY"
    if score >= 60:
        return "BUY"
    if score > 40:
        return "NEUTRAL"
    if score > 20:
        return "SELL"
    return "STRONG_SELL"
