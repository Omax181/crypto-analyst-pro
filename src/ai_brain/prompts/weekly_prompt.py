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
  "header": {"week","ptf_perf_week_pct"},
  "weekly_narrative": "string (bilan court)",
  "weekly_predictions_scoring": {"win_rate_pct","validated","invalidated","neutral","lesson"},
  "portfolio_overview": {"perf_week_pct","drawdown_pct","sector_exposure_vs_market": [{"sector","ptf_pct","market_signal"}]},
  "next_week_calendar": [{"date","event","impact"}],
  "next_week_scenarios": [{"scenario": "bearish|neutral|bullish","probability_pct","description","actions": [..]}],
  "exit_plan_dust": [{"asset","value_usd","spike_target_pct","status"}],
  "long_term_targets_review": [{"asset","target_6_12m_low","target_6_12m_high","rationale"}],
  "active_sources_week": ["string"],
  "footer": {"next_report_at"}
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
