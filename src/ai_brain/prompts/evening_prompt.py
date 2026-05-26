"""Constructeur du prompt pour le rapport du soir (différentiel).

Le soir COMPLÈTE le matin : il ne répète pas macro/on-chain/rotation déjà
couverts. Il lit le rapport du matin (mémoire) et produit les deltas.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_CONTRACT,
)

_EVENING_SCHEMA = """
{
  "header": {"date","time_casablanca","ptf_value_delta_since_morning","ptf_value_pct_since_morning"},
  "delta_of_the_day": ["string (max 3)"],
  "morning_reco_evolution": [{"asset","morning_action","status","current_evidence","advice"}],
  "market_evolution": [{"change_type": "improvement|mixed|new","fact"}],
  "tonight_and_night": [{"time","event"}],
  "tomorrow_morning_setup": ["string"],
  "actions_tonight_optional": ["string"],
  "blind_spots_evening": "string",
  "footer": {"next_report_at"}
}
"""


def build_evening_prompt(
    *, timestamp: str, data: dict[str, Any], morning_state: dict[str, Any]
) -> str:
    """Construit le prompt du rapport du soir.

    Args:
        timestamp: horodatage Casablanca.
        data: données collectées (légères : deltas, prix, news <24h).
        morning_state: contenu du rapport du matin (référence obligatoire).

    Returns:
        Prompt complet pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    morning_json = json.dumps(morning_state, ensure_ascii=False, default=str)[:6000]
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT DU SOIR · complément différentiel du matin.

RAPPORT DU MATIN (référence obligatoire, RÈGLE 7 — NE PAS le répéter) :
{morning_json}

DONNÉES DU SOIR (deltas depuis le matin) :
{data_json}

INSTRUCTIONS :
1. "Le delta du jour" : 3 changements max depuis le matin.
2. Pour chaque reco du matin : confirmation / invalidation / évolution avec
   preuve chiffrée (current_evidence).
3. Ce qui a évolué côté marché (DXY, ETF, news <24h) — uniquement le nouveau.
4. Soirée/nuit : événements et niveaux clés à surveiller.
5. Setup pour demain matin : les checks que tu feras.
6. Actions optionnelles ce soir si pertinent. Angles morts.
NE répète PAS le contexte macro/on-chain/rotation déjà donné le matin.

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_EVENING_SCHEMA}
"""
