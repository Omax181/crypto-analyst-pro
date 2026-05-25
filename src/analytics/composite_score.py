"""Score composite par crypto : agrégation pondérée de 5 dimensions.

Dimensions (pondérations dans ``thresholds.yaml``) :
- technical (0.30) : score TradingView multi-TF,
- on_chain (0.20) : signaux on-chain disponibles,
- fundamental (0.20) : santé dev (GitHub),
- sentiment (0.15) : news + Reddit,
- macro_alignment (0.15) : alignement avec le contexte macro global.

Chaque sous-score est dans [0,100]. Les dimensions indisponibles sont
neutralisées (50) plutôt que de fausser le total.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_WEIGHTS: dict[str, float] = _TH["composite_weights"]
_NEUTRAL = 50.0


def composite_score(
    *,
    technical: dict[str, Any],
    dev_activity: dict[str, Any],
    news_score: float,
    reddit_sentiment: float,
    macro_fit: float,
    onchain_available: bool,
) -> dict[str, Any]:
    """Calcule le score composite d'une crypto.

    Args:
        technical: sortie de ``analytics.technical.evaluate_technical``.
        dev_activity: sortie de ``data_sources.github_dev.get_dev_activity``.
        news_score: score news [0,1] pour ce symbole.
        reddit_sentiment: sentiment Reddit global [-1,1].
        macro_fit: alignement macro [0,100] (calculé en amont).
        onchain_available: si des données on-chain existent pour ce coin.

    Returns:
        Dict ``{total, components}`` avec ``total`` ∈ [0,100].
    """
    tech = technical.get("score")
    tech_score = tech if tech is not None else _NEUTRAL

    onchain_score = 55.0 if onchain_available else _NEUTRAL

    fundamental_score = _fundamental_from_dev(dev_activity)

    # Sentiment : combine news (intensité) et Reddit (direction).
    sentiment_score = _NEUTRAL + (reddit_sentiment * 25) + (news_score * 10)
    sentiment_score = max(0.0, min(100.0, sentiment_score))

    components = {
        "technical": round(tech_score, 1),
        "on_chain": round(onchain_score, 1),
        "fundamental": round(fundamental_score, 1),
        "sentiment": round(sentiment_score, 1),
        "macro_alignment": round(macro_fit, 1),
    }
    total = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)
    return {"total": round(total, 1), "components": components}


def _fundamental_from_dev(dev: dict[str, Any]) -> float:
    """Dérive un score fondamental [0,100] de l'activité dev."""
    if not dev.get("available"):
        return _NEUTRAL
    commits = dev.get("commits_30d", 0) or 0
    days_ago = dev.get("last_commit_days_ago")
    score = _NEUTRAL
    if commits >= 50:
        score += 25
    elif commits >= 10:
        score += 15
    elif commits >= 1:
        score += 5
    else:
        score -= 20
    if days_ago is not None:
        if days_ago <= 3:
            score += 10
        elif days_ago > 30:
            score -= 15
    return max(0.0, min(100.0, score))
