"""Source NewsAPI.org : news financières/crypto récentes (free tier 100 req/jour).

Remplace ``cryptopanic`` devenu payant. Même interface (``get_recent_news`` et
``check_keywords_recent``) pour minimiser l'impact dans ``main.py``.

Dégradation gracieuse : sans clé, renvoie toujours ``[]`` / ``None`` sans crash.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://newsapi.org/v2/everything"

# Mapping symbole -> termes de recherche enrichis. Sans mapping, on cherche
# juste le symbole (ex. "BTC") ; avec mapping on cible le nom complet.
_QUERY_HINTS = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "LINK": "Chainlink", "INJ": "Injective",
    "ARB": "Arbitrum", "WLD": "Worldcoin", "TAO": "Bittensor", "FIL": "Filecoin",
    "ATOM": "Cosmos", "ADA": "Cardano", "GRT": "Graph", "XRP": "Ripple",
    "STX": "Stacks", "FET": "Fetch.ai", "RENDER": "Render Network",
}


def _key() -> str:
    return os.environ.get("NEWSAPI_KEY", "").strip()


def _query_for(symbol: Optional[str]) -> str:
    """Construit la requête NewsAPI pour un symbole (ou global crypto)."""
    if not symbol:
        return "crypto OR bitcoin OR ethereum"
    hint = _QUERY_HINTS.get(symbol)
    return f'"{hint}" OR {symbol}' if hint else f"{symbol} crypto"


def get_recent_news(
    symbol: Optional[str] = None, hours: int = 24
) -> list[dict[str, Any]]:
    """Retourne les news <``hours`` heures, format compatible avec l'ancien cryptopanic.

    Args:
        symbol: ticker à filtrer (``None`` = global crypto).
        hours: fenêtre temporelle.

    Returns:
        Liste de dicts ``{title, source, url, published_at}`` (vide si pas
        de clé ou aucun résultat).
    """
    key = _key()
    if not key:
        return []

    def _fetch() -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        try:
            data = get_json(
                _BASE,
                params={
                    "q": _query_for(symbol),
                    "from": since,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 20,
                },
                headers={"X-Api-Key": key},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("NewsAPI échec : %s", exc)
            return []
        if not isinstance(data, dict) or data.get("status") != "ok":
            return []
        articles = data.get("articles") or []
        out: list[dict[str, Any]] = []
        for art in articles:
            out.append(
                {
                    "title": art.get("title"),
                    "source": (art.get("source") or {}).get("name"),
                    "url": art.get("url"),
                    "published_at": art.get("publishedAt"),
                    "description": (art.get("description") or "")[:280],
                }
            )
        return out

    cache_key = f"newsapi:{symbol or 'global'}:{hours}"
    result = CACHE.get_or_compute(cache_key, 1800, _fetch)
    return result or []


def get_macro_news(hours: int = 24) -> list[dict[str, Any]]:
    """Récupère les news macro/finance des sources tier-1 (Reuters, Bloomberg...).

    Complète ``get_recent_news`` (crypto) avec l'actualité macro qui impacte
    indirectement le marché : Fed, inflation, géopolitique, marchés actions,
    matières premières. Cible explicitement les sources de référence indexées
    par NewsAPI (Reuters, Bloomberg, Financial Times, CNBC, WSJ, etc.).

    Returns:
        Liste de dicts ``{title, source, url, published_at, description}``.
    """
    key = _key()
    if not key:
        return []

    def _fetch() -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        # Domaines des sources tier-1 (NewsAPI filtre par domaine).
        domains = (
            "reuters.com,bloomberg.com,ft.com,cnbc.com,wsj.com,"
            "marketwatch.com,apnews.com,economist.com"
        )
        query = (
            "Federal Reserve OR inflation OR interest rates OR recession OR "
            "DXY OR dollar OR gold OR oil OR China economy OR Trump tariffs OR "
            "stock market OR Treasury yields OR ECB OR Bank of Japan"
        )
        try:
            data = get_json(
                _BASE,
                params={
                    "q": query,
                    "domains": domains,
                    "from": since,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 25,
                },
                headers={"X-Api-Key": key},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("NewsAPI macro échec : %s", exc)
            return []
        if not isinstance(data, dict) or data.get("status") != "ok":
            return []
        out: list[dict[str, Any]] = []
        for art in data.get("articles") or []:
            out.append(
                {
                    "title": art.get("title"),
                    "source": (art.get("source") or {}).get("name"),
                    "url": art.get("url"),
                    "published_at": art.get("publishedAt"),
                    "description": (art.get("description") or "")[:280],
                    "category": "macro",
                }
            )
        return out

    result = CACHE.get_or_compute(f"newsapi:macro:{hours}", 1800, _fetch)
    return result or []


def check_keywords_recent(
    keywords: list[str], hours: int = 1, symbols: Optional[list[str]] = None
) -> Optional[dict[str, Any]]:
    """Détecte une news récente contenant un des mots-clés (pour panic mode).

    Args:
        keywords: mots-clés à chercher dans les titres (insensible à la casse).
        hours: fenêtre temporelle.
        symbols: si fourni, restreint la recherche à ces symboles.

    Returns:
        Le premier item news matchant, ou ``None``.
    """
    news = get_recent_news(None, hours=hours)
    if not news:
        return None
    kws = [k.lower() for k in keywords]
    for item in news:
        title = (item.get("title") or "").lower()
        if any(k in title for k in kws):
            return item
    return None
