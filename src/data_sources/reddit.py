"""Source Reddit : sentiment social via les endpoints publics ``.json``.

Pas d'authentification pour les endpoints publics (mais Reddit exige un
User-Agent descriptif). On récupère les top posts "hot" et on calcule un
sentiment basique (ratio upvotes, mots-clés).
"""

from __future__ import annotations

from typing import Any

import requests

from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_SUBREDDITS: list[str] = _SOURCES["reddit_subreddits"]
_HEADERS = {"User-Agent": "crypto-analyst-pro:v2.0 (by /u/Omax181)"}

_BULLISH = ("moon", "bull", "pump", "buy", "rally", "ath", "breakout", "surge")
_BEARISH = ("crash", "dump", "bear", "sell", "rug", "scam", "fud", "collapse")


def _keyword_sentiment(text: str) -> int:
    """Retourne +1/0/-1 selon les mots-clés dominants d'un titre."""
    t = text.lower()
    pos = sum(t.count(w) for w in _BULLISH)
    neg = sum(t.count(w) for w in _BEARISH)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def get_reddit_sentiment(limit_per_sub: int = 15) -> dict[str, Any]:
    """Agrège un sentiment global à partir des top posts des subreddits crypto.

    Returns:
        Dict ``{available, sentiment_score, post_count, top_titles}``.
        ``sentiment_score`` ∈ [-1, 1].
    """

    def _fetch() -> dict[str, Any]:
        scores: list[int] = []
        titles: list[str] = []
        for sub in _SUBREDDITS:
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{sub}/hot.json",
                    params={"limit": limit_per_sub},
                    headers=_HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
            except Exception as exc:  # noqa: BLE001
                logger.debug("Reddit r/%s indisponible : %s", sub, exc)
                continue
            for child in children:
                post = child.get("data", {})
                if post.get("stickied"):
                    continue
                title = post.get("title", "")
                scores.append(_keyword_sentiment(title))
                titles.append(title)

        if not scores:
            return {"available": False, "sentiment_score": 0.0, "post_count": 0}
        avg = sum(scores) / len(scores)
        return {
            "available": True,
            "sentiment_score": round(avg, 2),
            "post_count": len(scores),
            "top_titles": titles[:10],
        }

    return CACHE.get_or_compute("reddit:sentiment", 1800, _fetch)
