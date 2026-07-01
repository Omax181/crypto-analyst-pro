"""Source news macro : flux RSS Yahoo Finance (gratuit, sans clé).

Remplace l'accès aux sources tier-1 (Reuters/Bloomberg) que NewsAPI réserve à
son plan payant. Yahoo Finance expose des flux RSS publics et fiables couvrant
la macro (marchés actions, Fed, devises, matières premières, économie).

Parsing via xml.etree (stdlib) — aucune dépendance supplémentaire. Dégradation
gracieuse totale : renvoie une liste vide si les flux sont injoignables.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crypto-analyst-pro/2.0)"}
_TTL = 1800  # 30 min

# Flux RSS Yahoo Finance : actualité marchés + grandes valeurs macro.
# Le flux "headline" couvre l'actualité financière générale ; les flux par
# symbole (^GSPC = S&P 500, ^DJI = Dow, DX-Y.NYB = dollar index) ajoutent du
# contexte macro ciblé.
_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DJI&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DX-Y.NYB&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US",
]


def get_macro_news(limit: int = 12) -> list[dict[str, Any]]:
    """Récupère les news macro/finance depuis les flux RSS Yahoo Finance.

    Args:
        limit: nombre max d'articles renvoyés (après dédup).

    Returns:
        Liste de dicts ``{title, source, url, published_at, description}``,
        triés du plus récent au plus ancien. Liste vide si indisponible.
    """

    def _fetch() -> list[dict[str, Any]]:
        import requests

        items: list[dict[str, Any]] = []
        for feed_url in _FEEDS:
            try:
                resp = requests.get(feed_url, headers=_HEADERS, timeout=12)
                resp.raise_for_status()
                items.extend(_parse_rss(resp.content))
            except Exception as exc:  # noqa: BLE001
                logger.info("Yahoo RSS échec (%s) : %s", feed_url[:60], exc)
                continue
        return items

    try:
        raw = CACHE.get_or_compute("yahoo_macro_news", _TTL, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo Finance indisponible : %s", exc)
        return []

    if not raw:
        return []

    # Déduplication par titre (les flux se recoupent).
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for it in raw:
        title = (it.get("title") or "").strip()
        key = title.lower()[:80]
        if not title or key in seen:
            continue
        seen.add(key)
        unique.append(it)

    # Tri du plus récent au plus ancien.
    unique.sort(key=lambda x: x.get("_ts") or 0, reverse=True)
    # On nettoie le champ technique de tri avant de renvoyer.
    for it in unique:
        it.pop("_ts", None)
    return unique[:limit]


def _parse_rss(content: bytes) -> list[dict[str, Any]]:
    """Parse un flux RSS Yahoo Finance en liste d'articles."""
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.info("Yahoo RSS parse error : %s", exc)
        return out

    for item in root.iter("item"):
        title = _text(item.find("title"))
        if not title:
            continue
        link = _text(item.find("link"))
        desc = _text(item.find("description"))
        pub = _text(item.find("pubDate"))
        ts, pub_iso = _parse_date(pub)
        # Nettoie le HTML résiduel des descriptions.
        desc = re.sub(r"<[^>]+>", "", desc or "").strip()
        out.append(
            {
                "title": title.strip(),
                "source": "Yahoo Finance",
                "url": link,
                "published_at": pub_iso,
                "description": desc[:300],
                "_ts": ts,
            }
        )
    return out


def _text(el: Any) -> str:
    """Extrait le texte d'un élément XML, tolérant au None."""
    return el.text if el is not None and el.text else ""


def _parse_date(value: str) -> tuple[float, str]:
    """Parse une date RFC822 (RSS) → (timestamp, ISO). (0, '') si échec."""
    if not value:
        return (0.0, "")
    # Format RSS typique : "Wed, 28 May 2026 14:30:00 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt.timestamp(), dt.isoformat())
        except ValueError:
            continue
    return (0.0, value)
