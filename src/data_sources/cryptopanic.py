"""Source CryptoPanic : agrégation news crypto par tag + sentiment.

Free tier : ~1000 calls/jour. Renvoie les news récentes globales et, si
demandé, filtrées par devises spécifiques du portfolio.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["cryptopanic"]


def _vote_sentiment(votes: dict[str, Any]) -> float:
    """Calcule un score de sentiment [-1, 1] à partir des votes CryptoPanic."""
    positive = votes.get("positive", 0) + votes.get("liked", 0)
    negative = votes.get("negative", 0) + votes.get("disliked", 0)
    total = positive + negative
    if total == 0:
        return 0.0
    return (positive - negative) / total


def get_news(currencies: Optional[list[str]] = None, limit: int = 30) -> dict[str, Any]:
    """Récupère les news récentes, optionnellement filtrées par devises.

    Args:
        currencies: tickers à filtrer (ex. ``["BTC", "ETH"]``). ``None`` = global.
        limit: nombre max de news à conserver.

    Returns:
        Dict ``{available, items: [{title, url, published_at, sentiment,
        currencies, importance}], count}``.
    """
    key = os.environ.get("CRYPTOPANIC_API_KEY", "").strip()
    if not key:
        logger.info("CryptoPanic : pas de clé, news ignorées.")
        return {"available": False, "items": [], "count": 0}

    params: dict[str, Any] = {"auth_token": key, "public": "true"}
    if currencies:
        params["currencies"] = ",".join(currencies)
    cache_key = "cryptopanic:" + (",".join(sorted(currencies)) if currencies else "global")

    def _fetch() -> Any:
        return get_json(_BASE, params=params)

    raw = CACHE.get_or_compute(cache_key, 900, _fetch)
    if not raw or "results" not in raw:
        return {"available": False, "items": [], "count": 0}

    items: list[dict[str, Any]] = []
    for post in raw["results"][:limit]:
        votes = post.get("votes", {}) or {}
        items.append(
            {
                "title": post.get("title"),
                "url": post.get("url"),
                "published_at": post.get("published_at"),
                "sentiment": round(_vote_sentiment(votes), 2),
                "currencies": [c.get("code") for c in (post.get("currencies") or [])],
                "importance": post.get("kind"),
            }
        )
    return {"available": True, "items": items, "count": len(items)}


def news_score_by_symbol(news: dict[str, Any], symbols: list[str]) -> dict[str, float]:
    """Agrège un score de news [0,1] par symbole.

    Score = densité de couverture pondérée par |sentiment|. Sert au filtre
    ``should_mention_in_report``.

    Returns:
        Dict ``{symbol: score}`` pour les symboles ayant au moins une news.
    """
    scores: dict[str, float] = {s: 0.0 for s in symbols}
    counts: dict[str, int] = {s: 0 for s in symbols}
    for item in news.get("items", []):
        for code in item.get("currencies", []):
            if code in scores:
                counts[code] += 1
                scores[code] += abs(item.get("sentiment", 0))
    out: dict[str, float] = {}
    for sym in symbols:
        if counts[sym] == 0:
            continue
        # Normalisation douce : plus il y a de news engageantes, plus le score monte.
        raw = scores[sym] + 0.2 * counts[sym]
        out[sym] = round(min(raw, 1.0), 2)
    return out
