"""Constructeurs de prompts pour les rapports matin, soir et alertes intra-day.

Chaque builder assemble persona + données sérialisées + instructions + schéma
de sortie, et renvoie une chaîne prête à passer à ``GeminiClient.generate_json``.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_SCHEMA,
)


def _serialize(data: dict[str, Any]) -> str:
    """Sérialise les données collectées en JSON lisible (tronqué si besoin)."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def build_morning_prompt(
    *, timestamp: str, data: dict[str, Any], portfolio_yaml: str
) -> str:
    """Construit le prompt du rapport du matin."""
    return f"""{ANALYST_PERSONA}

CONTEXTE TEMPOREL : {timestamp} (Casablanca, UTC+1). Rapport du MATIN.

DONNÉES BRUTES COLLECTÉES (14 sources, certaines peuvent être indisponibles) :
{_serialize(data)}

PORTFOLIO :
{portfolio_yaml}

INSTRUCTIONS :
1. Évalue d'abord le NIVEAU DE VOLATILITÉ du jour (champ data.report_style
   déjà pré-calculé : respecte-le).
2. JOUR CALME -> rapport court (3-5 sections), utile, sans remplissage.
   JOUR AGITÉ -> développe tout ce qui le mérite.
3. Pour CHAQUE position listée dans data.positions_to_mention : produis une
   entrée. N'invente PAS de positions non listées (le filtre a déjà tranché).
4. Donne des AVIS FONDÉS seulement si les 3 conditions sont réunies ; sinon
   "avis": null.
5. Croise macro / micro / géopolitique en permanence.
6. Utilise data.historical_patterns pour des liens chiffrés quand pertinent.
7. Cite tes sources sous chaque insight crypto-spécifique (champ "sources").
8. Mentionne le cash réserve UNIQUEMENT si data.opportunity_flag est vrai.

DISCLAIMER à inclure dans footer : "{DISCLAIMER}"

OUTPUT : réponds UNIQUEMENT avec un objet JSON valide respectant EXACTEMENT
ce schéma (pas de texte hors JSON, pas de backticks) :
{OUTPUT_SCHEMA}
"""


def build_evening_prompt(
    *,
    timestamp: str,
    data: dict[str, Any],
    portfolio_yaml: str,
    morning_digest: str = "",
) -> str:
    """Construit le prompt du rapport du soir (inclut le delta depuis le matin)."""
    delta_block = (
        f"\nRÉSUMÉ DU RAPPORT DU MATIN (pour calculer le delta de la journée) :\n{morning_digest}\n"
        if morning_digest
        else "\n(Pas de digest matin disponible : produis un récap autonome.)\n"
    )
    return f"""{ANALYST_PERSONA}

CONTEXTE TEMPOREL : {timestamp} (Casablanca, UTC+1). Rapport du SOIR.
{delta_block}
DONNÉES BRUTES COLLECTÉES :
{_serialize(data)}

PORTFOLIO :
{portfolio_yaml}

INSTRUCTIONS (rapport du soir) :
1. Respecte data.report_style pour la longueur.
2. Inclus SYSTÉMATIQUEMENT une lecture "ce qui s'est passé depuis ce matin"
   dans le champ marche_global.commentaire (delta de la journée).
3. Journée calme -> récap rapide + setup pour demain.
   Journée agitée -> développement complet + analyse des mouvements.
4. Mêmes règles d'avis fondés, de sources et de silence sur l'inactif.
5. Mentionne le cash réserve UNIQUEMENT si data.opportunity_flag est vrai.

DISCLAIMER à inclure dans footer : "{DISCLAIMER}"

OUTPUT : UNIQUEMENT un objet JSON valide respectant EXACTEMENT ce schéma
(pas de texte hors JSON, pas de backticks) :
{OUTPUT_SCHEMA}
"""


def build_intraday_prompt(*, timestamp: str, triggers: list[dict[str, Any]]) -> str:
    """Construit le prompt d'une alerte intra-day (concise, urgente, fondée).

    Args:
        timestamp: horodatage Casablanca.
        triggers: liste de déclencheurs ``{symbol, type, detail}``.
    """
    return f"""{ANALYST_PERSONA}

CONTEXTE : {timestamp} (Casablanca). ALERTE INTRA-DAY.

DÉCLENCHEURS DÉTECTÉS :
{_serialize({"triggers": triggers})}

INSTRUCTIONS :
- Rédige une alerte COURTE et ACTIONNABLE (max ~120 mots).
- Explique ce qui se passe, pourquoi c'est important pour CE portefeuille,
  et l'action raisonnable (sans urgence injustifiée).
- Si l'info est un hack/exploit/delisting confirmé sur une position détenue,
  sois explicite sur le risque.
- Pas de prédiction de prix garantie.

OUTPUT : réponds UNIQUEMENT avec un objet JSON :
{{
  "title": "string (titre court de l'alerte)",
  "body": "string (corps de l'alerte, ~120 mots max)",
  "severity": "info | warning | danger"
}}
"""
