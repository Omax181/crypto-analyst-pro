"""Filtre de contenu : décide quelles positions méritent d'apparaître.

Implémente ``should_mention_in_report`` selon les seuils par tier et les
signaux (mouvement, news, technique, santé).
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_THRESHOLDS: dict[int, float] = {
    int(k): float(v) for k, v in _TH["mention_thresholds_pct"].items()
}
_NEWS_MIN: float = _TH["news_score_to_mention"]


def should_mention(symbol: str, tier: int, data: dict[str, Any]) -> bool:
    """Détermine si une crypto mérite d'apparaître dans le rapport.

    Args:
        symbol: ticker (pour logs).
        tier: tier 1-4.
        data: dict agrégé pour ce symbole, peut contenir ``change_24h``,
            ``news_score``, ``technical_signal``, ``health_verdict``.

    Returns:
        ``True`` si la crypto doit être mentionnée.
    """
    threshold = _THRESHOLDS.get(tier, 10.0)

    change = data.get("change_24h")
    if change is not None and abs(change) > threshold:
        logger.debug("%s mentionné : mouvement %.1f%% > %.1f%%", symbol, change, threshold)
        return True

    if data.get("news_score", 0.0) > _NEWS_MIN:
        return True

    if tier == 1 and data.get("technical_signal") in ("STRONG_BUY", "STRONG_SELL"):
        return True

    if data.get("health_verdict") in ("exit", "warning"):
        return True

    return False


def filter_positions(
    portfolio: dict[str, Any], enriched: dict[str, dict[str, Any]]
) -> list[str]:
    """Retourne la liste des symboles à mentionner, triés par importance.

    Args:
        portfolio: dict ``{symbol: {tier, ...}}``.
        enriched: dict ``{symbol: data}`` (mouvement, news, technique, santé).

    Returns:
        Liste de symboles ordonnée (tier croissant, puis |mouvement| décroissant).
    """
    keep: list[str] = []
    for sym, info in portfolio.items():
        if info.get("role") == "cash_reserve":
            continue
        tier = int(info["tier"])
        data = enriched.get(sym, {})
        if should_mention(sym, tier, data):
            keep.append(sym)

    def _sort_key(s: str) -> tuple[int, float]:
        tier = int(portfolio[s]["tier"])
        change = abs(enriched.get(s, {}).get("change_24h") or 0)
        return (tier, -change)

    return sorted(keep, key=_sort_key)
