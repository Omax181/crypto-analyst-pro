"""Source prediction markets : Polymarket (API publique, sans clé).

Récupère les probabilités implicites sur des événements macro (baisses de
taux Fed, etc.). Dégradation gracieuse si l'API ne répond pas.
"""

from __future__ import annotations

from typing import Any

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

_EXTRA_THEMES = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "etf",
    "recession", "inflation", "cpi", "tariff", "election", "president",
    "war", "iran", "china", "strait", "hormuz", "regulation", "sec",
    "treasury", "boj", "ecb", "rate hike", "shutdown",
)
_FED_NEXT_PATTERNS = {
    # famille -> mots-clés question (insensible casse)
    "cut": ("decrease", "cut", "lower"),
    "hold": ("no change", "unchanged", "hold", "maintain"),
    "hike": ("increase", "hike", "raise"),
}


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
        for m in raw:
            q = str(m.get("question", ""))
            ql = q.lower()
            if any(k in ql for k in _KEYWORDS):
                continue  # déjà couvert par les barres Fed
            if not any(t in ql for t in _EXTRA_THEMES):
                continue
            prob = _extract_yes_probability(m)
            if prob is None:
                continue
            try:
                vol = float(m.get("volumeNum") or m.get("volume") or 0)
            except (ValueError, TypeError):
                vol = 0.0
            extra.append({
                "question": q if len(q) <= 90 else q[:87] + "…",
                "probability_pct": round(prob * 100, 1),
                "volume_usd": round(vol),
                "end_date": m.get("endDate"),
            })
        extra.sort(key=lambda e: e["volume_usd"], reverse=True)
        # Probas extrêmes (>97% ou <3%) = quasi acquis, peu informatif → dépriorisé.
        informative = [e for e in extra if 3 <= e["probability_pct"] <= 97]
        extra = (informative or extra)[:5]

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
