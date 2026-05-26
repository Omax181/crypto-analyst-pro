"""Constructeur du prompt pour le panic mode (alerte flash)."""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import ANALYST_PERSONA, DISCLAIMER


def build_panic_prompt(*, timestamp: str, triggers: list[dict[str, Any]]) -> str:
    """Construit le prompt d'une alerte panic (courte, actionnable, fondée).

    Args:
        timestamp: horodatage Casablanca.
        triggers: déclencheurs détectés (``{type, detail}``).

    Returns:
        Prompt pour ``generate_json``.
    """
    triggers_json = json.dumps({"triggers": triggers}, ensure_ascii=False, default=str)
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. PANIC MODE · alerte flash.

DÉCLENCHEURS :
{triggers_json}

INSTRUCTIONS :
- Alerte COURTE (max ~120 mots), factuelle, actionnable.
- Explique ce qui se passe, l'impact sur CE portefeuille, l'action raisonnable.
- Aucune prédiction de prix garantie. Pas d'urgence injustifiée.

Réponds UNIQUEMENT en JSON :
{{"title","body","severity": "info|warning|danger"}}

Disclaimer (à intégrer brièvement) : "{DISCLAIMER}"
"""
