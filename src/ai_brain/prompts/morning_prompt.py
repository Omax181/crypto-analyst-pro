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
  "executive_summary": "string (TL;DR en 1-2 phrases pour scan 5 secondes : actions clés du jour + régime macro. Ex. 'Aujourd'hui : 1 renforcement GRT, 1 allègement INJ. Régime macro prudent (DXY haut, courbe inversée). 2 actions à poser.')",
  "story_of_the_day": {"narrative (PROSE 4-6 phrases avec 3 fils nommés Macro/On-chain/Setup)","threads": ["macro","onchain","individual"]},
  "self_critique_global": "string (PLUSIEURS phrases : sources manquantes, incertitudes, ce qui invaliderait)",
  "invalidation_watch": "string (2-3 triggers chiffrés que tu surveilles aujourd'hui pour invalider ton scénario)",
  "active_recommendations_tracking": [{"asset","action","issued_at","ct_target","current_price","progress_pct","progress_label","status","status_color"}],
  "tracking_footnote": "string (1 phrase : leçon récente, ce qu'a appris l'agent)",
  "macro_context": {"btc_price","btc_note (ex. 'range macro')","fear_greed","fear_greed_label (ex. 'peur extrême')","dxy","dxy_note (ex. 'cassure ↑')","polymarket_fed_cut_pct","fed_cut_note (ex. '−10pts en 2 sem.')","regime_synthesis (1 phrase qui lit le régime macro en croisant DXY/Gold/VIX/courbe/actions US — ex. 'Risk-off léger : DXY en hausse, Gold qui monte, courbe inversée, mais VIX calme')"},
  "onchain_indicators": {
    "metrics": [{"label","value","color (hex)","interpretation (1 phrase)"}],
    "combined_reading": "string (lecture globale qui croise les métriques)"
  },
  "onchain_empty_reason": "string (REQUIS si onchain_indicators absent)",
  "sector_rotation": [{"sector","change_24h","leaders (string ex. 'DOGE PEPE')","your_holdings": ["ticker1","ticker2"]}],
  "sector_rotation_ptf_note": "string (1-2 phrases : ce que la rotation veut dire sur TON ptf)",
  "news_24h": [{"category (Macro/Géopo/Catalyseur/Risque/Filtré)","tag_bg (hex)","tag_color (hex)","title","source","timestamp","confidence","impact_on_ptf (lien direct/indirect)"}],
  "news_24h_empty_reason": "string (REQUIS si news_24h vide — RARE, voir RÈGLE 8)",
  "today_watch": "string (PROSE : 2-3 catalyseurs/risques précis à surveiller dans la journée)",
  "thesis_of_the_day": [{
     "asset","name (nom complet ex. 'The Graph')","price_line (ex. '$0.026 · position $11.49 · +8% / 24h')",
     "action","action_type (bullish|bearish|neutral)","confidence","size_note (ex. 'taille standard')",
     "reliability (complète|partielle)",
     "signals_summary (ex. '4 signaux convergents (seuil Tier 1 atteint)')",
     "observation (PROSE plusieurs phrases)","sources_timestamps (ex. 'CoinGecko 08h12 · TradingView 08h15')",
     "reasoning_signals": ["signal 1 phrasé complet","signal 2",".."],
     "historical_pattern": {"verified","narrative (PROSE détaillée si verified)","occurrences_count","avg_move_pct","max_drawdown_pct","win_rate","data_source"},
     "self_critique (PROSE plusieurs arguments)","macro_coherence (PROSE)",
     "targets": {"short_term_label (ex. 'Tactique court terme · 30j')","short_term_30d","short_term_note (ex. '+46% · cible technique')","long_term_6_12m_low","long_term_6_12m_high","long_term_note (ex. 'si bull alts confirmé')"},
     "action_plan": {"entry","limit_orders","take_profit": {"30pct","30pct_b","40pct"},"stop_loss","invalidation_conditions"}
  }],
  "thesis_empty_reason": "string (REQUIS si thesis_of_the_day vide)",
  "macro_impact": {
    "intro": "string (PROSE introductive sur l'impact macro du jour)",
    "exposed_positions": [{"asset","beta_dxy","expected_impact_pct"}],
    "implication": "string (PROSE : que faire sur ton PTF)",
    "self_critique": "string (limites de la règle empirique)"
  },
  "all_positions_summary": [{"asset","tier","change_24h","comment","action_active (RENFORCER/ALLÉGER/SORTIR/SURVEILLER/MAINTENIR ou null)"}],
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

DONNÉES COLLECTÉES (sources multiples ; voir data.active_sources pour les actives) :
{data_json}

PORTFOLIO :
{portfolio_yaml}

INSTRUCTIONS :
0. SOURCES ACTIVES ce matin = data.active_sources. INTERDICTION ABSOLUE de citer
   une news, une donnée macro/on-chain ou une statistique provenant d'une source
   ABSENTE de cette liste. Si "News" n'est pas dans active_sources : remplir
   news_24h_empty_reason au lieu d'inventer. Toute donnée non présente dans le
   JSON fourni est INVENTÉE et donc interdite.
1. Construis "l'histoire du jour" en PROSE DÉVELOPPÉE (4 à 6 phrases minimum si
   la matière le permet) : 3 fils narratifs croisés et NOMMÉS (Macro / On-chain /
   Setup individuel), chacun avec ses chiffres et sources, comme un éditorial de
   marché. Donne du contexte, pas seulement des constats secs.
   "self_critique_global" : PLUSIEURS arguments (3-4 phrases) — quelles sources
   manquent ce matin, quelles incertitudes pèsent sur la lecture, ce qui pourrait
   invalider le scénario du jour. Développe, ne te contente pas d'une phrase.
2. Pour CHAQUE actif de data.eligible_theses, produis une thèse complète suivant
   la RÈGLE 10 (7 sous-blocs, prose développée, longueur adaptative). IL N'Y A
   PAS DE NOMBRE MAXIMUM de thèses : si 8 actifs sont éligibles, produis 8 thèses
   complètes ; si la liste est vide, ne produis AUCUNE thèse et remplis
   thesis_empty_reason. PRIORITÉ grandes cryptos (Tier 0-1) en horizon long terme,
   renforcement bienvenu si signaux convergents ; petites (Tier 2+) en court terme.
   Chaque "reasoning_signals" CROISE plusieurs domaines (technique, volume,
   on-chain, dérivés, macro, sentiment, fondamental) en citant les chiffres des
   données (fibonacci, bollinger, support_resistance, tvl, social, signals_detail).
   "self_critique" de chaque thèse = plusieurs arguments concrets, pas une phrase.
3. Reprends le tracking des recos actives (data.active_recommendations).
4. Indicateurs on-chain (sinon onchain_empty_reason), rotation sectorielle réelle.
   SECTION NEWS — au sens LARGE (RÈGLE 8) : crypto, macro, géopolitique, or,
   Trump/US, Chine, exchanges, ETF, stablecoins. Utilise data.news_24h_global
   (crypto), data.macro_news (sources tier-1 : Reuters, Bloomberg, FT, CNBC,
   WSJ — actualité macro/finance), les transcripts YouTube et messages Telegram.
   data.boursorama_calendar fournit le calendrier macroéconomique (événements à
   venir). data.youtube_corpus contient les transcripts des chaînes crypto
   (Crypto Pour Tous, etc.) et data.geopolitics la synthèse géopolitique (tensions,
   banques centrales, régulations). Exploite ces deux sources pour enrichir
   l'analyse macro et le sentiment. Il y a TOUJOURS de l'actualité mondiale à
   fort impact : produis plusieurs entrées news_24h avec pour chacune le lien
   d'impact (direct/indirect) sur le portefeuille. Cite la source réelle (ex.
   "Reuters", "Bloomberg", "Crypto Pour Tous"). Ne laisse cette section vide QUE
   si réellement aucune source news n'est active.
5. all_positions_summary est déjà calculé côté Python (ne pas le régénérer).
6. DONNÉES V6 À EXPLOITER librement (best-effort, pas de grille imposée) :
   - data.macro_context contient maintenant Gold, S&P 500, Nasdaq, Brent, WTI,
     EUR/USD, USD/JPY, VIX, US 10Y/2Y, courbe des taux. Croise-les avec le crypto
     quand c'est pertinent (RÈGLE 12). Cite les chiffres exacts reçus.
   - data.eligible_theses[].derivatives (ou les signaux) contient le funding rate
     RÉEL (Binance Futures) : un funding élevé/positif = surchauffe longs (signal
     d'allègement), négatif = excès shorts. Utilise-le dans le raisonnement.
   - data.whale_inflows : gros dépôts ETH vers exchanges (pression vendeuse).
   - data.stablecoin_supply : variation supply stablecoins (dry powder entrant/sortant).
   - data.btc_network : hashrate/difficulté (santé réseau BTC).
   - data.position_correlation : clusters de positions corrélées (risque concentré).
   - data.reco_changes : tes changements d'avis récents — si tu changes une reco
     par rapport à avant, explique POURQUOI (quels signaux ont changé, RÈGLE 13... 
     en pratique : sois transparent sur le revirement).
7. Termine par les angles morts (data.blind_spots) — recopie-les fidèlement.

{OUTPUT_CONTRACT}
Disclaimer à placer dans footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_MORNING_SCHEMA}
"""
