"""OB24 — CALIBRATEUR DE CONFIANCE « HUMBLE » (apprentissage borné et SÛR).

Le point le plus sensible du système : une « mémoire qui apprend » mal conçue
peut surapprendre le bruit, dériver ou se dégrader en silence. D'où 7 garde-fous
NON négociables (validés avec Omar) :

  1. Apprend UN seul paramètre borné (un multiplicateur de confiance), jamais du
     comportement libre, jamais une réécriture de prompt ou de règle.
  2. HUMBLE-ONLY : multiplicateur ∈ [0.70, 1.00] → il ne peut QUE réduire la
     confiance, JAMAIS l'augmenter. Si l'agent a été sur-confiant, il devient
     plus prudent ; il ne devient jamais plus agressif.
  3. Exige ≥ MIN_SAMPLE prédictions clôturées avant d'agir (anti-surapprentissage) ;
     inerte (×1.00) tant que l'historique est insuffisant.
  4. Adaptation LENTE (moyenne mobile EMA) : une semaine atypique ne fait pas
     basculer le curseur.
  5. KILL SWITCH : LEARNING_ENABLED=0 désactive tout (retour à ×1.00).
  6. TRANSPARENT : le multiplicateur ET sa raison sont exposés (affichés), jamais
     une boîte noire.
  7. Ne touche JAMAIS aux recos ni aux faits déterministes — seulement la
     CONFIANCE affichée (appliqué en dernier, après le plafond de complétude).
"""

from __future__ import annotations

import os
from typing import Any, Optional

_MULT_MIN = 0.70          # borne basse (jamais plus prudent que ça)
_MULT_MAX = 1.00          # borne haute = HUMBLE-ONLY (jamais > 1)
_MIN_SAMPLE = 10          # prédictions clôturées minimum avant d'agir
_EMA_ALPHA = 0.30         # adaptation lente : 30 % neuf, 70 % ancien
_STATE_KEY = "learning_calibration.json"

# Milieu du palier de confiance annoncé (aligné sur prediction_scoring.compute_calibration).
_ANNOUNCED_MID = {"50-69%": 60.0, "70-79%": 75.0, "80%+": 90.0}


def _enabled() -> bool:
    """Kill switch : LEARNING_ENABLED absent/vide = activé ; 0/false/off = coupé."""
    raw = os.environ.get("LEARNING_ENABLED")
    return raw is None or raw.strip().lower() not in ("0", "false", "off", "no")


def _clamp(x: float) -> float:
    return max(_MULT_MIN, min(_MULT_MAX, x))


def compute_confidence_multiplier(tracker: Any) -> dict[str, Any]:
    """Multiplicateur de confiance humble ∈ [0.70, 1.00], lissé (EMA) et persisté.

    Args:
        tracker: instance exposant ``compute_calibration(period_days) ->
            {available, buckets: [{range, realized_pct, n}], ...}``.

    Returns:
        ``{available, multiplier, reason, sample, raw_multiplier, enabled}``.
        ``multiplier`` vaut toujours 1.0 quand inactif/insuffisant (aucun effet).
    """
    from src.state import report_memory as mem
    prev = mem._read(_STATE_KEY, {})
    prev_mult = prev.get("multiplier") if isinstance(prev, dict) else None

    if not _enabled():
        return {"available": False, "multiplier": 1.0, "enabled": False,
                "reason": "apprentissage désactivé (LEARNING_ENABLED=0)"}

    try:
        cal = tracker.compute_calibration(90)
    except Exception:  # noqa: BLE001
        cal = {"available": False}
    if not isinstance(cal, dict) or not cal.get("available"):
        return {"available": False, "multiplier": 1.0, "enabled": True,
                "reason": "historique insuffisant pour calibrer"}

    buckets = cal.get("buckets") or []
    sample = sum(int(b.get("n") or 0) for b in buckets)
    if sample < _MIN_SAMPLE:
        return {"available": False, "multiplier": 1.0, "enabled": True,
                "sample": sample,
                "reason": f"échantillon insuffisant ({sample} < {_MIN_SAMPLE})"}

    # Ratio réalisé / annoncé, moyenné pondéré par n, borné HUMBLE-ONLY.
    num = den = 0.0
    for b in buckets:
        n = int(b.get("n") or 0)
        realized = b.get("realized_pct")
        mid = _ANNOUNCED_MID.get(str(b.get("range") or ""))
        if not n or realized is None or not mid:
            continue
        num += (float(realized) / mid) * n
        den += n
    if den == 0:
        return {"available": False, "multiplier": 1.0, "enabled": True,
                "sample": sample, "reason": "aucun palier exploitable"}

    raw = _clamp(num / den)   # borné [0.70, 1.00] → jamais un boost de confiance
    # Lissage EMA contre la valeur précédente (adaptation lente).
    if isinstance(prev_mult, (int, float)):
        mult = _clamp(_EMA_ALPHA * raw + (1.0 - _EMA_ALPHA) * float(prev_mult))
    else:
        mult = raw
    mult = round(mult, 3)

    try:
        mem._write(_STATE_KEY, {
            "multiplier": mult, "raw_multiplier": round(raw, 3),
            "sample": sample, "updated_at": mem.now_iso(),
        })
    except Exception:  # noqa: BLE001
        pass

    if mult < 0.98:
        reason = (f"sur-confiance historique (échantillon {sample}) → confiance "
                  f"affichée réduite ×{mult} par prudence (bornée à {_MULT_MIN})")
    else:
        reason = (f"calibration correcte (échantillon {sample}) → confiance quasi "
                  f"inchangée (×{mult})")
    return {"available": True, "multiplier": mult, "raw_multiplier": round(raw, 3),
            "sample": sample, "reason": reason, "enabled": True}


def apply_multiplier(confidence: Optional[float], multiplier: float) -> Optional[float]:
    """Applique le multiplicateur à une confiance (HUMBLE-ONLY : ne peut que la
    réduire). Renvoie un entier arrondi, ou l'entrée telle quelle si invalide."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return confidence
    m = _clamp(float(multiplier)) if isinstance(multiplier, (int, float)) else 1.0
    return int(round(min(c, c * m)))
