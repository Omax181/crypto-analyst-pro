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
