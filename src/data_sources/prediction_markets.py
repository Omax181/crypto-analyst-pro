"""Source prediction markets : Polymarket (API publique, sans clé).

Récupère les probabilités implicites sur des événements macro (baisses de
taux Fed, etc.). Dégradation gracieuse si l'API ne répond pas.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_GAMMA = "https://gamma-api.polymarket.com/markets"
_KEYWORDS = ("fed", "rate cut", "interest rate", "fomc")


def get_fed_cut_probabilities() -> dict[str, Any]:
    """Récupère les probabilités de baisse de taux Fed depuis Polymarket.

    Returns:
        Dict ``{available, markets: [{question, probability_pct, end_date}]}``.
    """

    def _fetch() -> Any:
        # v12 — on trie par volume décroissant et on élargit la fenêtre : les
        # marchés Fed/taux sont à fort volume mais noyés parmi des milliers de
        # marchés ; sans tri par volume, ils n'apparaissaient pas (cause du
        # « Polymarket indisponible »). On agrège deux tris pour fiabiliser.
        out: list[Any] = []
        for order in ("volume24hr", "volumeNum"):
            data = get_json(
                _GAMMA,
                params={
                    "active": "true", "closed": "false", "limit": 250,
                    "order": order, "ascending": "false",
                },
            )
            if isinstance(data, list):
                out.extend(data)
        # Dédup par id en préservant l'ordre.
        seen: set = set()
        uniq: list[Any] = []
        for m in out:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid in seen:
                continue
            seen.add(mid)
            uniq.append(m)
        return uniq or None

    raw = CACHE.get_or_compute("polymarket:fed", 3600, _fetch)
    if not isinstance(raw, list):
        return {"available": False, "markets": []}

    markets: list[dict[str, Any]] = []
    for m in raw:
        question = str(m.get("question", "")).lower()
        if not any(k in question for k in _KEYWORDS):
            continue
        prob = _extract_yes_probability(m)
        if prob is None:
            continue
        markets.append(
            {
                "question": m.get("question"),
                "probability_pct": round(prob * 100, 1),
                "end_date": m.get("endDate"),
            }
        )
    return {"available": bool(markets), "markets": markets[:10]}


# ── v15 — Polymarket ÉTENDU (demande explicite Omar : « Polymarket nous donne
# un edge — accéder aussi aux autres probabilités importantes, pas que la
# Fed »). Deux livrables :
#   1. fed_bars : probas baisse / maintien / hausse agrégées pour la PROCHAINE
#      réunion (alimente les 3 barres du template, scénario DOMINANT en tête).
#   2. extra_markets : top marchés à fort volume sur des thèmes qui comptent
#      pour le PTF (crypto, récession, élections, géopolitique, régulation).
# Tout est factuel (probabilités implicites du marché), zéro Gemini.

# v23.x (Omar) — thèmes PERTINENTS pour un investisseur crypto, par TIER de
# priorité décroissante. tier 1 = crypto direct, le plus actionnable (« Bitcoin
# above/below $X », ETF, régulation crypto…) ; tier 2 = macro qui meut la crypto ;
# tier 3 = géopolitique à fort impact « risk-off » (backup). Les ÉLECTIONS /
# NOMINATIONS (« Jon Stewart 2028 », « Newsom ») sont VOLONTAIREMENT exclues :
# aucune valeur de trading, c'est précisément le bruit à supprimer.
_CRYPTO_THEMES = (
    "bitcoin", "ethereum", "crypto", "solana", "dogecoin", "ripple", "cardano",
    "stablecoin", "coinbase", "binance", "microstrategy", "saylor", "etf",
    "blackrock", "halving", "altcoin", "memecoin", "blockchain",
)
_CRYPTO_TICKERS = ("btc", "eth", "sol", "xrp", "ada", "bnb", "doge")  # mot entier
_MACRO_THEMES = (
    "recession", "inflation", "cpi", "pce", "gdp", "unemployment", "jobs report",
    "tariff", "shutdown", "debt ceiling", "treasury", "regulation", "default",
)
_GEO_THEMES = (
    "iran", "israel", "china", "taiwan", "strait", "hormuz", "russia",
    "ukraine", "nuclear", "north korea",
)  # « war » traité à part (mot entier) — voir _WAR_RE.
# v18 (M-B2) — BLOCKLIST : thèmes SANS lien crypto/macro, à exclure même si un
# mot de la whitelist apparaît par accident. L'audit a vu « Netherlands FIFA
# World Cup » s'afficher : zéro lien avec le PTF. Sport, divertissement, people.
_EXTRA_BLOCKLIST = (
    "fifa", "world cup", "super bowl", "nba", "nfl", "ufc", "olympic", "champions league",
    "premier league", "ballon", "oscar", "grammy", "album", "movie", "box office",
    "rihanna", "taylor swift", "kanye", "celebrity", "tournament", "playoff", "world series",
)
_FED_NEXT_PATTERNS = {
    # famille -> mots-clés question (insensible casse)
    "cut": ("decrease", "cut", "lower"),
    "hold": ("no change", "unchanged", "hold", "maintain"),
    "hike": ("increase", "hike", "raise"),
}


_TICKER_RE = re.compile(r"\b(" + "|".join(_CRYPTO_TICKERS) + r")\b")
_WAR_RE = re.compile(r"\bwar\b")  # mot entier : pas « stewart », « warren », « toward »


def _market_tier(ql: str) -> Optional[int]:
    """Tier de pertinence (crypto-first) d'une question Polymarket en minuscules.

    1 = crypto direct · 2 = macro · 3 = géopolitique · ``None`` = hors-sujet
    (élections/nominations, météo, people…). Les tokens courts/ambigus (tickers
    btc/eth/…, « war ») sont matchés en MOT ENTIER pour éviter les faux positifs
    (« eth » dans « whether », « war » dans « stewart »).
    """
    if any(t in ql for t in _CRYPTO_THEMES) or _TICKER_RE.search(ql):
        return 1
    if any(t in ql for t in _MACRO_THEMES):
        return 2
    if any(t in ql for t in _GEO_THEMES) or _WAR_RE.search(ql):
        return 3
    return None


def get_key_markets() -> dict[str, Any]:
    """Vue Polymarket enrichie : barres Fed + autres marchés majeurs.

    Returns:
        ``{available, fed_bars: {cut_pct, hold_pct, hike_pct, dominant,
        dominant_pct, meeting_hint}, fed_markets: [...], extra_markets:
        [{question, probability_pct, volume_usd, end_date}]}``.
        ``dominant`` ∈ {"maintien", "baisse", "hausse"} — c'est LUI qu'on
        affiche en premier (règle métier : jamais le scénario minoritaire).
    """
    base = get_fed_cut_probabilities()  # réutilise le fetch caché (1 appel)

    def _fetch_all() -> Any:
        out: list[Any] = []
        for order in ("volume24hr", "volumeNum"):
            data = get_json(
                _GAMMA,
                params={
                    "active": "true", "closed": "false", "limit": 250,
                    "order": order, "ascending": "false",
                },
            )
            if isinstance(data, list):
                out.extend(data)
        seen: set = set()
        uniq: list[Any] = []
        for m in out:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid in seen:
                continue
            seen.add(mid)
            uniq.append(m)
        return uniq or None

    try:
        raw = CACHE.get_or_compute("polymarket:fed", 3600, _fetch_all)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Polymarket étendu indisponible : %s", exc)
        raw = None

    fed_bars: dict[str, Any] = {}
    fed_markets = base.get("markets") or []
    if fed_markets:
        # On agrège la PROCHAINE échéance : end_date la plus proche parmi les
        # marchés Fed (les questions « after the June 2026 meeting » partagent
        # la même end_date). Probas hors famille -> ignorées.
        dated = sorted(
            (m for m in fed_markets if m.get("end_date")),
            key=lambda m: str(m["end_date"]),
        )
        next_end = dated[0]["end_date"] if dated else None
        probs = {"cut": 0.0, "hold": 0.0, "hike": 0.0}
        meeting_hint = None
        for m in fed_markets:
            if next_end and m.get("end_date") != next_end:
                continue
            q = str(m.get("question", "")).lower()
            for fam, keys in _FED_NEXT_PATTERNS.items():
                if any(k in q for k in keys):
                    probs[fam] = max(probs[fam], float(m.get("probability_pct") or 0))
                    if meeting_hint is None:
                        import re as _re
                        mm = _re.search(
                            r"(january|february|march|april|may|june|july|august|"
                            r"september|october|november|december)\s+20\d\d", q)
                        if mm:
                            _mois = {"january": "janvier", "february": "février",
                                     "march": "mars", "april": "avril", "may": "mai",
                                     "june": "juin", "july": "juillet",
                                     "august": "août", "september": "septembre",
                                     "october": "octobre", "november": "novembre",
                                     "december": "décembre"}
                            en = mm.group(0).split()
                            meeting_hint = f"réunion {_mois.get(en[0], en[0])} {en[1]}"
                    break
        if any(v > 0 for v in probs.values()):
            label_fr = {"cut": "baisse", "hold": "maintien", "hike": "hausse"}
            dom = max(probs, key=lambda k: probs[k])
            fed_bars = {
                "cut_pct": round(probs["cut"], 1),
                "hold_pct": round(probs["hold"], 1),
                "hike_pct": round(probs["hike"], 1),
                "dominant": label_fr[dom],
                "dominant_pct": round(probs[dom], 1),
                "meeting_hint": meeting_hint,
            }

    extra: list[dict[str, Any]] = []
    if isinstance(raw, list):
        scored: list[tuple[int, dict[str, Any]]] = []
        for m in raw:
            q = str(m.get("question", ""))
            ql = q.lower()
            if any(k in ql for k in _KEYWORDS):
                continue  # déjà couvert par les barres Fed
            # v18 (M-B2) : exclusion DURE des thèmes sport/divertissement.
            if any(b in ql for b in _EXTRA_BLOCKLIST):
                continue
            # v23.x : ne garder QUE les marchés pertinents (crypto > macro > géo) ;
            # tout le reste (élections/nominations, météo…) est écarté.
            tier = _market_tier(ql)
            if tier is None:
                continue
            prob = _extract_yes_probability(m)
            if prob is None:
                continue
            try:
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
            except (ValueError, TypeError):
                vol = 0.0
            scored.append((tier, {
                "question": q if len(q) <= 90 else q[:87] + "…",
                # v18 (M-A13) : proba ENTIÈRE pour les marchés extra. La tuile
                # affichait « 21% » et le texte « 21.3% » pour le MÊME marché.
                # Une proba géopolitique au dixième près est faussement précise :
                # on arrondit à l'entier partout (source unique → cohérence).
                "probability_pct": round(prob * 100),
                "volume_usd": round(vol),
                "end_date": m.get("endDate"),
            }))
        # v23.x : tri CRYPTO-FIRST (tier croissant) puis volume décroissant.
        scored.sort(key=lambda e: (e[0], -e[1]["volume_usd"]))
        # Probas extrêmes (>97% ou <3%) = quasi acquis, peu informatif → dépriorisé.
        informative = [e for e in scored if 3 <= e[1]["probability_pct"] <= 97]
        ranked = informative or scored
        # v23.x : crypto + macro priment ; le géopolitique (tier 3) n'est qu'un
        # COMPLÉMENT plafonné à 1 marché (sinon des paris « guerre » lointains
        # à 5-14% noyaient les marchés crypto vraiment actionnables).
        crypto_macro = [e[1] for e in ranked if e[0] <= 2]
        geo = [e[1] for e in ranked if e[0] == 3]
        extra = (crypto_macro + geo[:1])[:5]

    return {
        "available": bool(fed_bars or extra or fed_markets),
        "fed_bars": fed_bars,
        "fed_markets": fed_markets,
        "markets": fed_markets,  # alias rétro-compat (prompts/consommateurs v14)
        "extra_markets": extra,
    }


def _extract_yes_probability(market: dict[str, Any]) -> float | None:
    """Extrait la probabilité du résultat 'Yes' (best effort selon le schéma)."""
    prices = market.get("outcomePrices")
    try:
        if isinstance(prices, str):
            import json

            prices = json.loads(prices)
        if isinstance(prices, list) and prices:
            return float(prices[0])
    except (ValueError, TypeError):
        return None
    return None
