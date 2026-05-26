"""Score composite V2 : agrégation pondérée de 9 signaux + comptage de signaux.

Nouveautés du refactor :
- 9 signaux pondérés (cf. ``thresholds.yaml > signal_weights``).
- Les commits GitHub sont DANS ``fundamental`` (jamais > 50% de ce poids),
  soit ~5% du total — fini les recos justifiées par les seuls commits.
- Comptage du nombre de signaux *convergents* (significativement au-dessus ou
  en-dessous du neutre), utilisé par le seuil adaptatif par tier.
- Conversion confiance -> taille d'action.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_WEIGHTS: dict[str, float] = _TH["signal_weights"]
_CONF_MAP = _TH["confidence_to_action"]
_NEUTRAL = 50.0
# Un signal "compte" comme convergent s'il s'écarte d'au moins ce delta du neutre.
_CONVERGENCE_DELTA = 12.0


def composite_score(signals: dict[str, Optional[float]]) -> dict[str, Any]:
    """Calcule le score composite à partir des 9 sous-scores.

    Args:
        signals: dict ``{nom_signal: score [0,100] | None}``. Les clés absentes
            ou ``None`` sont neutralisées (50) et NE comptent pas comme un
            signal convergent.

    Returns:
        Dict ``{total, components, signals_count, bullish_count,
        bearish_count}``.
    """
    components: dict[str, float] = {}
    bullish = 0
    bearish = 0
    for name, weight in _WEIGHTS.items():
        raw = signals.get(name)
        if raw is None:
            components[name] = _NEUTRAL
            continue
        val = max(0.0, min(100.0, float(raw)))
        components[name] = round(val, 1)
        if val >= _NEUTRAL + _CONVERGENCE_DELTA:
            bullish += 1
        elif val <= _NEUTRAL - _CONVERGENCE_DELTA:
            bearish += 1

    total = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS)
    signals_count = bullish + bearish
    return {
        "total": round(total, 1),
        "components": components,
        "signals_count": signals_count,
        "bullish_count": bullish,
        "bearish_count": bearish,
    }


def confidence_to_action(confidence: float) -> dict[str, Any]:
    """Convertit un niveau de confiance (0-100) en taille d'action.

    Args:
        confidence: niveau de confiance en pourcentage.

    Returns:
        Dict ``{label, firm}`` où ``firm`` indique si une reco ferme est permise.
    """
    c = max(0.0, min(100.0, confidence))
    for band in _CONF_MAP:
        if band["min"] <= c <= band["max"]:
            return {"label": band["label"], "firm": bool(band["firm"])}
    return {"label": "silence · pas de mention", "firm": False}
