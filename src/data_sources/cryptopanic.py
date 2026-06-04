"""Source CryptoPanic : agrégation news crypto par tag + sentiment.

Free tier : ~1000 calls/jour. Renvoie les news récentes globales et, si
demandé, filtrées par devises spécifiques du portfolio.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["cryptopanic"]

# Mots-clés à fort impact pour le filtrage news (RÈGLE 8).
HIGH_IMPACT_KEYWORDS = (
    "hack", "exploit", "breach", "lawsuit", "sec ", "listing", "delisting",
    "upgrade", "partnership", "etf", "halt", "depeg", "bankrupt", "ban",
)


def parse_timestamp(value: str) -> Optional[datetime]:
    """Parse un timestamp ISO CryptoPanic en datetime aware (UTC).

    Args:
        value: chaîne ISO 8601 (ex. ``"2026-05-25T22:00:00Z"``).

    Returns:
        ``datetime`` timezone-aware en UTC, ou ``None`` si non parsable.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def get_recent_news(symbol: Optional[str] = None, hours: int = 24) -> list[dict[str, Any]]:
    """Retourne UNIQUEMENT les news publiées dans les ``hours`` dernières heures.

    Corrige le bug "news inventées/périmées" (RÈGLE 8) : tout item dont le
    timestamp est plus ancien que la fenêtre est rejeté.

    Args:
        symbol: ticker à filtrer (``None`` = global).
        hours: fenêtre temporelle en heures.

    Returns:
        Liste d'items news récents (``[]`` si aucun, ou si source indisponible).
    """
    currencies = [symbol] if symbol else None
    news = get_news(currencies=currencies, limit=50)
    if not news.get("available"):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent: list[dict[str, Any]] = []
    for item in news.get("items", []):
        ts = parse_timestamp(item.get("published_at"))
        if ts is not None and ts > cutoff:
            recent.append(item)
    logger.info(
        "CryptoPanic : %d news <%dh pour %s.", len(recent), hours, symbol or "global"
    )
    return recent


def check_keywords_recent(
    keywords: list[str], hours: int = 1, symbols: Optional[list[str]] = None
) -> Optional[dict[str, Any]]:
    """Détecte une news récente contenant un des mots-clés (helper générique).

    Args:
        keywords: mots-clés à chercher dans les titres (insensible à la casse).
        hours: fenêtre temporelle.
        symbols: restreindre aux devises données (``None`` = global).

    Returns:
        Le premier item news matchant, ou ``None``.
    """
    news = get_news(currencies=symbols, limit=50)
    if not news.get("available"):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kws = [k.lower() for k in keywords]
    for item in news.get("items", []):
        ts = parse_timestamp(item.get("published_at"))
        if ts is None or ts <= cutoff:
            continue
        title = (item.get("title") or "").lower()
        if any(k in title for k in kws):
            return item
    return None
