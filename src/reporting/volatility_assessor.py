"""Évaluateur de volatilité : détermine la longueur/style du rapport.

Calcule un score de volatilité à partir des événements macro, du nombre de
positions en mouvement, des shifts de narrative et du volume de news.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_RS = _TH["report_style"]
_W = _RS["weights"]


def determine_report_style(signals: dict[str, Any]) -> dict[str, Any]:
    """Détermine le style du rapport.

    Args:
        signals: dict pouvant contenir ``macro_high_impact_today`` (int),
            ``positions_moving`` (int), ``narrative_shift`` (bool),
            ``major_news_count`` (int).

    Returns:
        Dict ``{style, score}`` où ``style`` ∈ {``"calm"``, ``"normal"``,
        ``"active"``}.
    """
    score = 0

    if signals.get("macro_high_impact_today", 0) > 0:
        score += _W["macro_high_impact_event"]
    if signals.get("positions_moving", 0) >= _RS["positions_moving_min"]:
        score += _W["positions_moving"]
    if signals.get("narrative_shift"):
        score += _W["narrative_shift"]
    if signals.get("major_news_count", 0) >= _RS["major_news_min"]:
        score += _W["major_news"]

    if score < _RS["calm_max"]:
        style = "calm"
    elif score < _RS["normal_max"]:
        style = "normal"
    else:
        style = "active"

    logger.info("Style de rapport : %s (score %d)", style, score)
    return {"style": style, "score": score}
