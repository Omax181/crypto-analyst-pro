"""Source Alternative.me : Fear & Greed Index (sentiment marché global 0-100)."""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["fear_greed"]


def get_fear_greed() -> dict[str, Any]:
    """Récupère l'indice Fear & Greed actuel, la veille et l'historique 8 j.

    v26 (W-B3) : l'API renvoie désormais 8 points (gratuit, même appel) pour
    donner au hebdo l'ÉVOLUTION du sentiment sur la semaine (« F&G 24 → 19 »)
    et une sparkline — pas seulement la valeur ponctuelle.

    Returns:
        Dict ``{available, value, classification, value_yesterday, delta,
        value_7d_ago, delta_7d, history}``. ``history`` = liste chronologique
        (ancien → récent) des valeurs. ``available=False`` si l'API ne répond
        pas.
    """

    def _fetch() -> Any:
        return get_json(_BASE, params={"limit": 8})

    raw = CACHE.get_or_compute("fng8", 3600, _fetch)
    if not raw or "data" not in raw or not raw["data"]:
        return {"available": False}

    data = raw["data"]  # ordre API : le plus récent d'abord
    today = data[0]
    value = int(today.get("value", 0))
    yesterday = int(data[1].get("value", value)) if len(data) > 1 else value
    history = [int(d.get("value", 0)) for d in reversed(data)
               if str(d.get("value", "")).lstrip("-").isdigit()]
    # value_7d_ago SEULEMENT si on a bien 8 points : un historique partiel
    # donnerait un « il y a 7 j » qui n'en est pas un (mal étiqueté).
    value_7d = history[0] if len(history) >= 8 else None
    out: dict[str, Any] = {
        "available": True,
        "value": value,
        "classification": today.get("value_classification"),
        "value_yesterday": yesterday,
        "delta": value - yesterday,
        "history": history,
    }
    if value_7d is not None:
        out["value_7d_ago"] = value_7d
        out["delta_7d"] = value - value_7d
    return out
