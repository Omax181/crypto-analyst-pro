"""Agrégation de flux RSS d'actualité (crypto + macro-finance), sans clé API.

Complète CryptoPanic/NewsAPI avec les grands médias qui exposent un flux RSS
public, sur deux axes :
  • CRYPTO : CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine,
    CryptoSlate, CoinJournal.
  • MACRO/FINANCE : Reuters, MarketWatch, Investing.com, Financial Times,
    Seeking Alpha, Barron's, Stocktwits, Yahoo Finance.

Aucune clé requise, simple GET sur le flux XML. Le parsing utilise la lib
standard (``xml.etree``) — pas de dépendance externe. Dégradation gracieuse :
si un flux est indisponible (réseau, format, 4xx/5xx, paywall), il est ignoré
et les autres sont quand même agrégés. Si tous échouent, renvoie
``available=False`` sans casser le pipeline.

Les news sont filtrées par fenêtre temporelle (récence) et, optionnellement,
par mots-clés à fort impact, puis dédoublonnées par titre normalisé.

Note : certains flux (FT, Barron's, Seeking Alpha) peuvent être protégés par
paywall ou limiter leur RSS — d'où la dégradation gracieuse. Les sources non
disponibles en RSS pur (Arkham, Polymarket, CryptoBubbles, TradingView) sont
gérées par leurs modules dédiés (prediction_markets, cryptobubbles, tradingview).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

from src.data_sources.http import get_text
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Flux RSS crypto publics. label -> url.
CRYPTO_FEEDS: dict[str, str] = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "CoinJournal": "https://coinjournal.net/news/feed/",
}

# Flux RSS macro/finance publics. label -> url.
MACRO_FEEDS: dict[str, str] = {
    # Reuters a coupé son RSS officiel (404 depuis mars 2026). On le reconstitue
    # via Google News filtré sur reuters.com — fiable, gratuit, sans clé.
    "Reuters": "https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+business&ceid=US:en&hl=en-US&gl=US",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "MarketWatch Markets": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    "Investing.com": "https://www.investing.com/rss/news.rss",
    "Investing.com Crypto": "https://www.investing.com/rss/news_301.rss",
    "Financial Times": "https://www.ft.com/rss/home",
    "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
    "Barron's": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    # L'endpoint api.stocktwits.com renvoie 403 (auth requise). Le flux de
    # sentiment Stocktwits sur BTC est reconstitué via Google News (mentions
    # marché). Garde une couverture "sentiment retail" sans clé.
    "Stocktwits": "https://news.google.com/rss/search?q=when:24h+stocktwits+OR+(retail+traders+crypto)&ceid=US:en&hl=en-US&gl=US",
}

# Vue agrégée pour compatibilité ascendante + accès "toutes sources".
RSS_FEEDS: dict[str, str] = {**CRYPTO_FEEDS, **MACRO_FEEDS}

# Mots-clés à fort impact (cohérents avec cryptopanic.HIGH_IMPACT_KEYWORDS).
HIGH_IMPACT_KEYWORDS = (
    "hack", "exploit", "breach", "lawsuit", "sec ", "listing", "delisting",
    "upgrade", "partnership", "etf", "halt", "depeg", "bankrupt", "ban",
    "approval", "rate cut", "fed", "regulation", "liquidation", "whale",
    "inflation", "cpi", "fomc", "recession", "tariff", "default",
)

_CACHE_KEY = "crypto_rss_news"
_CACHE_TTL = 600  # 10 min : les flux ne bougent pas à la seconde.


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Parse une date RSS (RFC 822 ou ISO 8601) en datetime aware UTC."""
    if not value:
        return None
    value = value.strip()
    # RFC 822 (format RSS classique : "Mon, 25 May 2026 14:30:00 +0000").
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    # ISO 8601 (Atom : "2026-05-25T14:30:00Z").
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    """Retire les balises HTML d'un résumé RSS et compacte les espaces."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_feed(label: str, xml_text: str) -> list[dict[str, Any]]:
    """Parse un flux RSS/Atom et renvoie une liste d'items normalisés.

    Tolère RSS 2.0 (``<item>``) et Atom (``<entry>``). Ne lève jamais.
    """
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS %s : XML illisible (%s).", label, exc)
        return items

    # RSS 2.0 : channel/item ; Atom : feed/entry (avec namespace).
    nodes = root.findall(".//item")
    is_atom = False
    if not nodes:
        nodes = [e for e in root.iter() if e.tag.endswith("}entry") or e.tag == "entry"]
        is_atom = True

    for node in nodes:
        def _find(tag_names: tuple[str, ...]) -> Optional[str]:
            for child in node:
                t = child.tag.split("}")[-1]
                if t in tag_names:
                    if t == "link" and is_atom and not (child.text or "").strip():
                        return child.attrib.get("href")
                    return (child.text or "").strip()
            return None

        title = _find(("title",))
        if not title:
            continue
        summary = _find(("description", "summary", "content"))
        pub = _find(("pubDate", "published", "updated", "date"))
        link = _find(("link",))
        items.append({
            "title": _strip_html(title),
            "summary": _strip_html(summary or "")[:300],
            "published": _parse_date(pub),
            "url": link,
            "source": label,
        })
    return items


def get_news(
    hours: int = 24,
    *,
    high_impact_only: bool = False,
    limit: int = 30,
    feeds: Optional[dict[str, str]] = None,
    category: str = "crypto",
) -> dict[str, Any]:
    """Agrège les news récentes de plusieurs flux RSS (crypto et/ou macro).

    Args:
        hours: fenêtre de récence (news plus anciennes ignorées). Les items
            sans date sont conservés (best-effort).
        high_impact_only: ne garder que les titres contenant un mot-clé à
            fort impact.
        limit: nombre max de news renvoyées (triées par date décroissante).
        feeds: override explicite de la liste de flux (prioritaire sur category).
        category: ``"crypto"`` (défaut), ``"macro"`` ou ``"all"`` — choisit le
            jeu de flux à interroger si ``feeds`` n'est pas fourni.

    Returns:
        ``{"available": bool, "news": [...], "sources_ok": [...],
           "sources_down": [...], "count": int}``.
    """
    cache_sig = f"{_CACHE_KEY}:{hours}:{high_impact_only}:{limit}:{category}"
    cached = CACHE.get(cache_sig)
    if cached is not None:
        return cached

    if feeds is None:
        if category == "macro":
            feeds = MACRO_FEEDS
        elif category == "all":
            feeds = RSS_FEEDS
        else:
            feeds = CRYPTO_FEEDS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    all_items: list[dict[str, Any]] = []
    sources_ok: list[str] = []
    sources_down: list[str] = []

    for label, url in feeds.items():
        xml_text = get_text(url, headers={"User-Agent": "Mozilla/5.0 (crypto-analyst-pro)"})
        if not xml_text:
            sources_down.append(label)
            continue
        parsed = _parse_feed(label, xml_text)
        if parsed:
            sources_ok.append(label)
            all_items.extend(parsed)
        else:
            sources_down.append(label)

    # Filtre récence (garde les items sans date).
    recent = [
        it for it in all_items
        if it["published"] is None or it["published"] >= cutoff
    ]

    # Filtre fort impact si demandé.
    if high_impact_only:
        recent = [
            it for it in recent
            if any(kw in it["title"].lower() for kw in HIGH_IMPACT_KEYWORDS)
        ]

    # Dédoublonnage par titre normalisé.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for it in recent:
        key = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:80]
        if key and key not in seen:
            seen.add(key)
            deduped.append(it)

    # Tri par date décroissante (les sans-date en fin).
    deduped.sort(
        key=lambda it: it["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    deduped = deduped[:limit]

    # Sérialise les dates en ISO pour le payload.
    for it in deduped:
        if it["published"] is not None:
            it["published_iso"] = it["published"].isoformat()
        it["published"] = None  # évite les objets datetime dans le JSON

    result = {
        "available": bool(sources_ok),
        "news": deduped,
        "sources_ok": sources_ok,
        "sources_down": sources_down,
        "count": len(deduped),
    }
    if sources_ok:
        logger.info(
            "RSS news : %d news de %d/%d flux (%s).",
            len(deduped), len(sources_ok), len(feeds), ", ".join(sources_ok),
        )
    else:
        logger.warning("RSS news : aucun flux disponible (%s).", ", ".join(sources_down))
    CACHE.set(cache_sig, result, ttl=_CACHE_TTL)
    return result
