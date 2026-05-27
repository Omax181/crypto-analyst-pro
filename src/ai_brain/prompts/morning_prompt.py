"""Constructeur du prompt pour le rapport du matin.

Assemble persona + données collectées + état mémoire (soir précédent) +
contrat de sortie JSON détaillé (schéma du refactor).
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_CONTRACT,
)

_MORNING_SCHEMA = """
{
  "header": {"date","time_casablanca","active_sources_count","win_rate_30d","win_rate_total"},
  "portfolio_snapshot": {"value_usd","change_24h_pct","change_7d_pct","vs_btc_7d_pct","drawdown_ath_pct"},
  "story_of_the_day": {"narrative","threads": ["macro","onchain","individual"]},
  "self_critique_global": "string",
  "active_recommendations_tracking": [{"asset","action","issued_at","ct_target","current_price","progress_pct","status"}],
  "macro_context": {"btc_price","fear_greed","dxy","polymarket_fed_cut_july"},
  "onchain_indicators": {"btc_exchange_reserves_change_7d","ssr","whale_tx_24h","whale_tx_anomaly_pct","eth_active_addresses"},
  "sector_rotation": [{"sector","change_24h","leaders": [..],"your_holdings": [..]}],
  "news_24h": [{"category","title","source","timestamp","confidence","impact_on_ptf"}],
  "today_alerts": ["string"],
  "thesis_of_the_day": [{
     "asset","action","confidence","action_size","observation","sources_timestamps",
     "reasoning_signals": [..],
     "historical_pattern": {"verified","occurrences_count","avg_move_pct","max_drawdown_pct","win_rate","data_source"},
     "self_critique","macro_coherence",
     "targets": {"short_term_30d","long_term_6_12m_low","long_term_6_12m_high"},
     "action_plan": {"entry","limit_orders","take_profit": {"30pct","30pct_b","40pct"},"stop_loss","invalidation_conditions"}
  }],
  "macro_impact_on_ptf": {"trigger","historical_stat","exposed_positions": [{"asset","beta_dxy","expected_impact_pct"}],"implication","self_critique"},
  "all_positions_summary": [{"asset","status","change_24h","reco"}],
  "blind_spots": "string",
  "footer": {"active_sources": [..],"next_report_at"}
}
"""


def build_morning_prompt(
    *, timestamp: str, data: dict[str, Any], portfolio_yaml: str, evening_state: dict[str, Any]
) -> str:
    """Construit le prompt du rapport du matin.

    Args:
        timestamp: horodatage Casablanca formaté.
        data: dict de données collectées (toutes sources + pré-calculs).
        portfolio_yaml: portfolio sérialisé.
        evening_state: contenu du dernier rapport du soir (cohérence).

    Returns:
        Prompt complet prêt pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    evening_json = json.dumps(evening_state, ensure_ascii=False, default=str)[:4000]
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT DU MATIN · point d'entrée complet de la journée.

ÉTAT MÉMOIRE · dernier rapport du soir (pour la cohérence, RÈGLE 7) :
{evening_json}

DONNÉES COLLECTÉES (14 sources ; certaines peuvent être indisponibles) :
{data_json}

PORTFOLIO :
{portfolio_yaml}

INSTRUCTIONS :
0. SOURCES ACTIVES ce matin = data.active_sources. INTERDICTION ABSOLUE de citer
   une news, une donnée macro/on-chain ou une statistique provenant d'une source
   ABSENTE de cette liste. Si "News" n'est pas dans active_sources : écrire
   "pas de news majeure vérifiée · marché en silence", ne JAMAIS inventer de
   titre, de source (ex. "geopolitics") ni d'heure. Toute donnée non présente
   dans le JSON fourni est INVENTÉE et donc interdite.
1. Construis "l'histoire du jour" : 3 fils narratifs croisés (macro/on-chain/
   individuel) UNIQUEMENT à partir des données fournies.
2. Pour chaque actif de data.eligible_theses UNIQUEMENT, produis une thèse
   complète respectant la RÈGLE 10. N'invente aucune thèse hors de cette liste
   (le seuil de signaux adaptatif a déjà filtré). Chaque thèse DOIT exploiter,
   si présents, les champs fibonacci, bollinger, support_resistance, tvl,
   social, signals_detail de l'actif : cite les niveaux chiffrés (support,
   résistance, bande de Bollinger, niveaux de Fibonacci) dans l'observation et
   le plan d'action. C'est ce qui rend l'analyse technique crédible.
3. Reprends le tracking des recos actives (data.active_recommendations).
4. Indicateurs on-chain, rotation sectorielle (secteurs réels de data), news
   <24h taggées (si la source News est active uniquement).
5. Récapitule les positions (all_positions_summary) avec statut fondé, sans
   copier-coller le même paragraphe.
6. Termine par les angles morts (data.blind_spots) — recopie-les fidèlement.

{OUTPUT_CONTRACT}
Disclaimer à placer dans footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_MORNING_SCHEMA}
"""
