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
    "lesson": "string (PROSE : leçon de la semaine + action correctrice). v15 — les compteurs (issued/validated/invalidated/win_rate) et le tableau detail sont CALCULÉS CÔTÉ PYTHON depuis data.scoring_detail : NE LES GÉNÈRE PAS. Ta seule contribution ici est la leçon, fondée sur data.scoring_detail."
  },
  "predictions_empty_reason": "string (REQUIS si data.scoring_detail vide : ex. 'Première semaine, pas encore d historique')",
  "sector_exposure": [{"sector","ptf_pct","market_pct","color (hex)"}],
  "concentration_reading": "string (PROSE : lecture concentration + recommandation structurelle)",
  "upcoming_calendar": [{"day (ex. 'Mer 18h')","day_bg (hex)","day_color (hex)","title","impact_label (Impact élevé/moyen/Catalyseur crypto)","detail (PROSE)"}],
  "scenarios": [{"type (bearish|neutral|bullish)","label (ex. 'baissier')","probability_pct","description (PROSE)","action (PROSE : que faire)"}],
  "strategy_focus": "string (v15 — LA stratégie de la semaine en 3 phrases MAX : le biais directionnel, la priorité n°1, la condition qui ferait tout changer. Pas un résumé : une consigne.)",
  "my_errors": "string (v15 — 1-2 phrases : LA pire erreur d'analyse de la semaine écoulée, nommée honnêtement, avec le correctif. Si vraiment aucune : ce qui a failli mal tourner.)",
  "weekly_action_plan": [{"priority (1-3)","action (concret ex. 'Si BTC < 60k → alléger TAO de 30%')","rationale (1 phrase)"}],
  "losses_vs_recos": "string — 1-3 phrases : relie les plus fortes baisses de la semaine aux recos qu'on avait émises (ex. 'ZK était en SURVEILLER lundi, -21% depuis : sortie au-dessus de 0.005 aurait évité -X%'). Honnête sur les erreurs.",
  "watchlist": [{"asset","direction (entrée/sortie)","trigger (niveau/condition précis)","rationale (1 phrase fondée)"}],
  "macro_panorama": "string — 2-3 phrases : panorama macro de la semaine à venir (Fed/CPI/NFP du calendrier réel + Polymarket + ETF flows, ET la dimension internationale si fournie : BCE, BoJ/carry trade yen, Nikkei/Stoxx — le crypto ne vit pas qu'aux USA) et son implication pour le PTF. Le fil rouge macro.",
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
5. 3 scénarios (baissier/neutre/haussier). v15 — GARDE-FOUS PROBABILITÉS :
   chaque probabilité doit être JUSTIFIÉE par 1-2 moteurs cités (calendrier,
   Polymarket, flux ETF, régime) — pas de 50/40/10 par défaut. La somme fait
   100. Le scénario le PLUS probable doit être COHÉRENT avec le régime macro
   constaté et le dominant Polymarket (data.polymarket.fed_bars.dominant) ; si
   tu t'en écartes, dis pourquoi. Chaque scénario inclut son ÉLÉMENT
   DÉCLENCHEUR daté quand le calendrier le permet (« FOMC mercredi : si
   maintien confirmé → ... »).
6. Exit plan poussières (< 10 $) : attendre spike +30%, statut par actif.
   v15 — les poussières (data.dust_positions) n'apparaissent QUE dans ce bloc :
   jamais dans la watchlist, les scénarios ou le plan d'action.
7. Cibles long terme révisées par actif Tier 0/1. v15 — data.ath_by_asset
   fournit l'ATH RÉEL et la distance actuelle par actif (CoinGecko) : toute
   référence à l'ATH utilise CE chiffre (écrire « retest ATH 73k » quand l'ATH
   réel est 108k = défaut d'audit avéré). Donne des cibles CONCRÈTES (niveau,
   fourchette, ou multiple ancré sur l'ATH réel, un ratio MVRV, le cycle) — si
   tu n'as pas de base réelle pour chiffrer, écris « cible à préciser » plutôt
   qu'une formule vide. Pas de remplissage creux.
8. SOURCES — n'invente PAS un nombre de sources. Le compte réel est
   data.active_sources_count (sur total_sources_count) : utilise-le tel quel dans
   sources_review. Ne dis jamais « 15 sources » si le compte fourni est différent.
9. EXPOSITION SECTORIELLE — déjà calculée côté Python (data.sector_exposure_computed,
   poids PTF réels par secteur). Recopie-la, ne mets JAMAIS « n/d% » : si elle est
   absente, omets la section.
10. SOURCES CLÉS À EXPLOITER (P3-A5) — données factuelles fournies, à UTILISER
   dans l'analyse, pas seulement à afficher :
   - data.upcoming_calendar.events : calendrier macro CONSOLIDÉ v15 (FRED +
     Boursorama + décisions FOMC/BoJ officielles ; « (estimé) » = récurrence).
     Alimente macro_panorama + upcoming_calendar + watchlist + scénarios. Ne
     cite JAMAIS un événement absent de cette liste.
   - data.polymarket.fed_bars : baisse/maintien/hausse + DOMINANT → cite le
     dominant en premier. data.polymarket.extra_markets : autres probabilités
     de marché majeures (récession, géopo, crypto) — un edge à CROISER avec le
     calendrier (« FOMC mercredi, Polymarket maintien 99% → pas de catalyseur
     taux : scénario range »).
   - data.etf_flows : flux ETF BTC/ETH → sentiment institutionnel. Intègre-les
     dans le panorama et les scénarios.
   - data.scoring_detail : le tableau RÉEL des recos de la semaine (dédupliqué,
     dates, delta, statut). Ta lesson + losses_vs_recos se fondent dessus.
1bis. v15 — weekly_summary CAUSAL : le bilan n'est pas une liste de constats
   (« le DXY a monté, les actions ont baissé ») mais une CHAÎNE causale
   (« l'inflation à 4,3% a repoussé les baisses de taux → DXY +0,7 → pression
   sur les actifs longue duration → ton bloc AI -5,4% »). Termine par la
   conséquence nette pour CE portefeuille.
11. LIEN PERTES ↔ RECOS (losses_vs_recos) : relie HONNÊTEMENT les plus fortes
   baisses de la semaine aux recos émises. Si une position en SURVEILLER/RENFORCER
   a chuté, dis-le et tire la leçon chiffrée. v15 — fais le même lien pour les
   plus fortes HAUSSES (data : top movers) : une hausse captée par une reco =
   à créditer ; une hausse ratée (aucune reco) = à nommer.
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
   FONDÉE (analysée), pas une liste au hasard. v15 — ÉQUILIBRE : vise au moins
   1 ENTRÉE fondée (niveau d'accumulation sur un actif de conviction) en plus
   des sorties ; une watchlist 100% sorties = pas une watchlist, un exit plan.
   JAMAIS de poussière (<10 $) ici.
16. strategy_focus (v15) : 3 phrases MAX — biais directionnel de la semaine,
   priorité n°1, condition de bascule. C'est une CONSIGNE, pas un résumé.
17. my_errors (v15) : nomme LA pire erreur d'analyse de la semaine (reco ratée,
   lecture macro démentie) + le correctif. Honnêteté totale — si la semaine est
   propre, nomme ce qui a failli mal tourner. Jamais d'auto-félicitation.

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_WEEKLY_SCHEMA}
"""
