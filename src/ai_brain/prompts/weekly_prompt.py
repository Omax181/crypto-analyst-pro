"""Constructeur du prompt pour le rapport hebdomadaire (dimanche).

Bilan de la semaine + scoring des prédictions + anticipation de la semaine à
venir (calendrier, 3 scénarios) + révision stratégique long terme.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_CONTRACT,
)

_WEEKLY_SCHEMA = """
{
  "header": {"date","time_casablanca","week_number (int)","upcoming_week (ex. '2-8 juin')"},
  "portfolio_snapshot": "CALCULÉ CÔTÉ PYTHON — ne pas générer (value_usd, change_7d_pct, change_7d_usd, vs_btc_7d_pct, drawdown_ath_pct, drawdown_change_pts, usdc_pct, usdc_usd sont injectés automatiquement)",
  "weekly_summary": "string (PROSE 5-8 phrases : bilan complet de la semaine avec chiffres)",
  "predictions_scoring": {
    "issued (int)","validated (int)","invalidated (int)","win_rate_pct",
    "detail": [{"asset","reco (RENFORCER/ALLÉGER/SURVEILLER/...)","result (1 phrase chiffrée)","score (+1, -1, 0)"}],
    "lesson": "string (PROSE : leçon de la semaine + action correctrice)"
  },
  "predictions_empty_reason": "string (REQUIS si pas d'historique : ex. 'Première semaine, pas encore d historique')",
  "sector_exposure": [{"sector","ptf_pct","market_pct","color (hex)"}],
  "concentration_reading": "string (PROSE : lecture concentration + recommandation structurelle)",
  "upcoming_calendar": [{"day (ex. 'Mer 18h')","day_bg (hex)","day_color (hex)","title","impact_label (Impact élevé/moyen/Catalyseur crypto)","detail (PROSE)"}],
  "scenarios": [{"type (bearish|neutral|bullish)","label (ex. 'baissier')","probability_pct","description (PROSE)","action (PROSE : que faire)"}],
  "exit_plan": {"subtitle","diagnosis (PROSE chiffrée)","monitoring (PROSE : comment l'agent surveille)"},
  "long_term_positioning": [{"asset","thesis","target","status (en route/consolide/accumulation/à surveiller/stable)","status_color (hex)"}],
  "sources_review": {"summary (PROSE bilan sources)","gaps (PROSE lacunes structurelles)"},
  "footer": {"next_morning","next_weekly"}
}
"""


def build_weekly_prompt(
    *, timestamp: str, data: dict[str, Any], week_state: dict[str, Any]
) -> str:
    """Construit le prompt du rapport hebdomadaire.

    Args:
        timestamp: horodatage Casablanca.
        data: données collectées + win rate + historique semaine.
        week_state: agrégat des rapports de la semaine (mémoire).

    Returns:
        Prompt complet pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    week_json = json.dumps(week_state, ensure_ascii=False, default=str)[:6000]
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT HEBDOMADAIRE · bilan + anticipation.

MÉMOIRE DE LA SEMAINE (rapports agrégés) :
{week_json}

DONNÉES + SCORING :
{data_json}

INSTRUCTIONS :
0. RÈGLE DES CHIFFRES (CRITIQUE). Tout nombre (prix, %, niveau, drawdown, win
   rate) doit être copié VERBATIM depuis le JSON fourni — jamais calculé,
   extrapolé, mémorisé d'ailleurs, ni inventé. Donnée absente = "n/d" ou
   description sans chiffre. Un chiffre faux affiché en confiance est l'erreur la
   plus grave de ce rapport.
1. Bilan narratif court de la semaine (ce qui a dominé).
2. Scoring des prédictions : win rate réel (data.win_rate) + leçon apprise.
3. Vue d'ensemble portfolio : perf, drawdown, exposition sectorielle vs marché.
4. Calendrier semaine à venir (FOMC, CPI, NFP, upgrades) avec impact chiffré.
   Si calendrier vide : "données calendrier indisponibles".
5. 3 scénarios (baissier/neutre/haussier) avec probabilités et actions.
6. Exit plan poussières (<$5) : attendre spike +30%, statut par actif.
7. Cibles long terme révisées par actif Tier 0/1.

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_WEEKLY_SCHEMA}
"""
