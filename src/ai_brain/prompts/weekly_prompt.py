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
  "weekly_action_plan": [{"priority (1-3)","action (concret ex. 'Si BTC < 60k → alléger TAO de 30%')","rationale (1 phrase)"}],
  "losses_vs_recos": "string — 1-3 phrases : relie les plus fortes baisses de la semaine aux recos qu'on avait émises (ex. 'ZK était en SURVEILLER lundi, -21% depuis : sortie au-dessus de 0.005 aurait évité -X%'). Honnête sur les erreurs.",
  "watchlist": [{"asset","direction (entrée/sortie)","trigger (niveau/condition précis)","rationale (1 phrase fondée)"}],
  "macro_panorama": "string — 2-3 phrases : panorama macro de la semaine à venir (Fed/CPI/NFP du calendrier réel + Polymarket + ETF flows) et son implication pour le PTF. Le fil rouge macro.",
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
7. Cibles long terme révisées par actif Tier 0/1. Donne des cibles CONCRÈTES
   (niveau de prix, fourchette, ou multiple ancré sur un repère : ATH, ratio
   MVRV, cycle) — si tu n'as pas de base réelle pour chiffrer une cible, écris
   « cible à préciser » plutôt qu'une formule vide du type « à définir via
   analyse dédiée ». Pas de remplissage creux.
8. SOURCES — n'invente PAS un nombre de sources. Le compte réel est
   data.active_sources_count (sur total_sources_count) : utilise-le tel quel dans
   sources_review. Ne dis jamais « 15 sources » si le compte fourni est différent.
9. EXPOSITION SECTORIELLE — déjà calculée côté Python (data.sector_exposure_computed,
   poids PTF réels par secteur). Recopie-la, ne mets JAMAIS « n/d% » : si elle est
   absente, omets la section.
10. SOURCES CLÉS À EXPLOITER (P3-A5) — données factuelles fournies, à UTILISER
   dans l'analyse, pas seulement à afficher :
   - data.upcoming_calendar.events : prochaines publications macro (dates réelles).
     Alimente macro_panorama + upcoming_calendar + watchlist (ex. « CPI jeudi → ne
     pas se positionner avant »).
   - data.polymarket.markets : probas Fed implicites du marché → biais taux.
   - data.etf_flows : flux ETF BTC/ETH → sentiment institutionnel. Intègre-les
     dans le panorama et les scénarios.
11. LIEN PERTES ↔ RECOS (losses_vs_recos) : relie HONNÊTEMENT les plus fortes
   baisses de la semaine aux recos émises. Si une position en SURVEILLER/RENFORCER
   a chuté, dis-le et tire la leçon chiffrée.
12. SCÉNARIOS COHÉRENTS AVEC LE PTF (scenarios) : chaque scénario doit dire ce
   qu'il implique CONCRÈTEMENT pour CE portefeuille (positions exposées nommées),
   pas des généralités. Et l'action proposée doit être cohérente avec la
   composition réelle (concentration L1/AI, absence de cash).
13. ALLÉGEMENTS SPÉCIFIQUES (A9) : ne dis jamais « alléger les positions exposées »
   en vague. NOMME les positions (ex. « alléger TAO : 25% du PTF, secteur AI -9%/j,
   β-DXY défavorable »), avec un argument ET un contre-argument.
14. PLAN D'ACTION SEMAINE (weekly_action_plan) : 2-4 actions concrètes,
   conditionnelles et chiffrées pour la semaine (« si X → fais Y »).
15. WATCHLIST (watchlist) : actifs à entrer/sortir avec trigger précis et raison
   FONDÉE (analysée), pas une liste au hasard.

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_WEEKLY_SCHEMA}
"""
