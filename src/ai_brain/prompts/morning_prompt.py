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
  "executive_summary": {"bullets": [{"icon ('✓'|'⚠'|'✗')","text (1 ligne dense : action/risque/contexte)"}]},
  "macro_regime_readout": {"regime (risk-on/risk-off/neutre — repris de la PASSE 1 data.macro_regime)","confidence_pct","drivers (string : 2-3 moteurs clés)","crypto_bias (ce que ça implique pour le crypto)"},
  "story_of_the_day": {"narrative (PROSE 5-7 LIGNES MAX, dense : 3 fils nommés Macro/On-chain/Setup — le DÉTAIL vit dans les blocs dédiés, pas ici)","threads": ["macro","onchain","individual"]},
  "self_critique_global": {"bullets": ["angle mort 1 (1 ligne)","angle mort 2","angle mort 3 (2-4 puces max)"]},
  "invalidation_watch": [{"condition (ex. 'DXY > 101,0')","implication (1 ligne : ce que ça invaliderait)"}],
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
  "news_24h": [{"category (Macro/Géopo/Catalyseur/Risque/Filtré)","tag_bg (hex)","tag_color (hex)","title","source","timestamp","confidence (ENTIER 0-100, jamais /5)","impact_on_ptf (lien direct/indirect)"}],
  "news_24h_empty_reason": "string (REQUIS si news_24h vide — RARE, voir RÈGLE 8)",
  "today_watch": "string (PROSE : 2-3 catalyseurs/risques précis à surveiller dans la journée)",
  "thesis_of_the_day": [{
     "asset","name (nom complet ex. 'The Graph')","tier_label (recopie data.eligible_theses[].tier_label, ex. 'Tier 2 · mid cap')","price_line (ex. '$0.026 · position $11.49 · +8% / 24h')",
     "action","action_type (bullish|bearish|neutral)","confidence","size_note (ex. 'taille standard')",
     "reliability (complète|partielle)",
     "signals_summary (ex. '4 signaux convergents (seuil Tier 1 atteint)')",
     "observation (PROSE plusieurs phrases)","sources_timestamps (ex. 'CoinGecko 08h12 · TradingView 08h15')",
     "reasoning_signals": ["signal 1 phrasé complet","signal 2",".."],
     "historical_pattern": {"verified","narrative (PROSE détaillée si verified)","occurrences_count","avg_move_pct","max_drawdown_pct","win_rate","data_source"},
     "self_critique (PROSE plusieurs arguments)","macro_coherence (PROSE)",
     "targets": {"short_term_label (ex. 'Tactique court terme · 30j')","short_term_30d","short_term_note (ex. '+46% · cible technique')","long_term_6_12m_low","long_term_6_12m_high","long_term_note (ex. 'si bull alts confirmé')"},
     "watch_trigger (UNIQUEMENT si action SURVEILLER/MAINTENIR : 1 phrase, le déclencheur chiffré qui ferait passer à l'action)",
     "action_plan": "OBJET UNIQUEMENT si action = RENFORCER ou ALLÉGER. Pour SURVEILLER/MAINTENIR : OMETTRE complètement action_plan (ne pas mettre de champs 'None'). Forme: {entry, limit_orders, take_profit:{30pct,30pct_b,40pct}, stop_loss, stop_loss_basis, rr (ex '3.2:1' si fondé sinon omettre), invalidation_conditions}"
  }],
  "thesis_empty_reason": "string (REQUIS si thesis_of_the_day vide)",
  "macro_impact": {
    "intro": "string (PROSE introductive sur l'impact macro du jour)",
    "exposed_positions": [{"asset","beta_dxy","expected_impact_pct"}],
    "implication": "string (PROSE : que faire sur ton PTF · termine par 1 phrase de limite méthodo — il n'y a PLUS de bloc auto-critique macro séparé, cf. RÈGLE 10bis)"
  },
  "all_positions_summary": [{"asset","tier","change_24h","comment","action_active (RENFORCER/ALLÉGER/SORTIR/SURVEILLER/MAINTENIR ou null)"}],
  "blind_spots": "string",
  "footer": {"active_sources": [..],"next_report_at"}
}
"""


def build_morning_prompt(
    *, timestamp: str, data: dict[str, Any], portfolio_yaml: str,
    evening_state: dict[str, Any], macro_regime: dict[str, Any] | None = None,
) -> str:
    """Construit le prompt du rapport du matin.

    Args:
        timestamp: horodatage Casablanca formaté.
        data: dict de données collectées (toutes sources + pré-calculs).
        portfolio_yaml: portfolio sérialisé.
        evening_state: contenu du dernier rapport du soir (cohérence).
        macro_regime: verdict de la PASSE 1 (régime macro). Optionnel —
            rétro-compatible : si absent, le prompt fonctionne comme avant.

    Returns:
        Prompt complet prêt pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    evening_json = json.dumps(evening_state, ensure_ascii=False, default=str)[:4000]
    regime_block = ""
    if macro_regime:
        regime_json = json.dumps(macro_regime, ensure_ascii=False, default=str)
        regime_block = f"""
RÉGIME MACRO (PASSE 1 — déjà établi par la passe macro ; sert de CADRE à tes
thèses, cf. RÈGLE 13) :
{regime_json}
"""
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT DU MATIN · point d'entrée complet de la journée.
{regime_block}
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
0bis. RÈGLE DES CHIFFRES (CRITIQUE, ZÉRO TOLÉRANCE). Tout nombre que tu écris
   (prix, %, niveau, capitalisation, score, ratio, taux) DOIT être copié
   VERBATIM depuis le JSON de données fourni. Tu n'as pas le droit de :
   - calculer, arrondir différemment, extrapoler ou "corriger" un chiffre ;
   - réutiliser un chiffre mémorisé d'un autre contexte ou d'une session passée ;
   - inventer un prix "plausible" quand la donnée est absente.
   Si une valeur n'est pas dans le JSON : écris "n/d" ou décris qualitativement
   SANS chiffre. Un prix faux affiché en confiance est l'ERREUR LA PLUS GRAVE
   possible dans ce rapport — il vaut TOUJOURS mieux ne pas donner de chiffre que
   d'en donner un non sourcé. Les prix des actifs viennent EXCLUSIVEMENT de
   data.all_positions_summary et data.macro_context ; ne les recalcule jamais.
1. Construis "l'histoire du jour" en PROSE DENSE de 5 à 7 LIGNES MAXIMUM
   (v15 — audit : 15 lignes qui répétaient les blocs = doublon ; le DÉTAIL vit
   dans les blocs dédiés, l'histoire SYNTHÉTISE) : 3 fils narratifs croisés et
   NOMMÉS (Macro / On-chain / Setup individuel), chacun avec 1-2 chiffres
   marquants. Du contexte causal, zéro répétition des blocs.
   "self_critique_global" : 2-4 PUCES (1 ligne chacune) — quelles sources
   manquent ce matin, quelles incertitudes pèsent, ce qui invaliderait le
   scénario. Des angles NOUVEAUX (RÈGLE 10bis), pas les redites des thèses.
   "executive_summary.bullets" (v15) : EXACTEMENT 2 à 4 puces typées —
   icon '✓' = action/élément positif du jour, '⚠' = vigilance, '✗' = risque
   avéré. Chaque puce = 1 ligne scannable avec chiffre. PAS de paragraphe.
   "invalidation_watch" (v15) : LISTE de 2-4 objets {{condition, implication}},
   chaque condition CHIFFRÉE (« S&P 500 < 7 200 en clôture »).
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
   (crypto), data.macro_news (actualité macro/finance : Yahoo Finance, CNBC et
   autres sources accessibles — Fed, inflation, devises, matières premières,
   actions), les transcripts YouTube et messages Telegram.
   data.boursorama_calendar fournit le calendrier macroéconomique (événements à
   venir). data.youtube_corpus contient les transcripts des chaînes crypto
   (Crypto Pour Tous, etc.) et data.geopolitics la synthèse géopolitique (tensions,
   banques centrales, régulations). Exploite ces sources pour enrichir
   l'analyse macro et le sentiment. Il y a TOUJOURS de l'actualité mondiale à
   fort impact : produis plusieurs entrées news_24h avec pour chacune le lien
   d'impact (direct/indirect) sur le portefeuille, une category (Catalyseur,
   Risque, Macro, Géopolitique, Info) et une importance (1-5, 5 = majeur). Cite
   la source réelle (ex. "Yahoo Finance", "Crypto Pour Tous", "Telegram").
   Ne laisse cette section vide QUE si réellement aucune source news n'est active.
5. all_positions_summary est déjà calculé côté Python (ne pas le régénérer).
6. DONNÉES V6 À EXPLOITER librement (best-effort, pas de grille imposée) :
   - data.macro_context contient maintenant Gold, S&P 500, Nasdaq, Brent, WTI,
     EUR/USD, USD/JPY, VIX, US 10Y/2Y, courbe des taux. Croise-les avec le crypto
     quand c'est pertinent (RÈGLE 12). Cite les chiffres exacts reçus.
   - INTERNATIONAL (v14.1) : data.macro_context contient aussi nikkei, stoxx50,
     dax, ecb_deposit_rate (taux de dépôt BCE), boj_rate (taux BoJ). Intègre la
     dimension MONDIALE de ton analyse : liquidité BCE, carry trade yen (BoJ),
     appétit risque Asie/Europe (Nikkei/Stoxx avant l'ouverture US). RÈGLE 12.
   - ACTIONS ↔ CRYPTO (v14.1) : data.equity_quotes (NVDA/AMD/TSM/COIN/MSTR/MARA,
     prix + % séance) et data.equity_crypto_links.links (corr/β 30j Python entre
     ces actions et tes positions, avec mécanisme). La ligne condensée est dans
     analytics_digest.equity_crypto. Raisonne en transmission (« si NVDA monte,
     RENDER monte car demande GPU/IA — corr +0,62, β 1,4 ») en citant UNIQUEMENT
     les chiffres reçus. Pertinent en priorité pour RENDER, TAO, FET (bloc IA
     du PTF) et pour BTC via COIN/MSTR/MARA.
   - data.market_movers (Crypto Bubbles) : top gainers/losers du marché sur 24h
     + data.market_movers.portfolio_movers (tes positions vs le marché). Sert à
     repérer si un token du PTF surperforme/sous-performe le marché global, et la
     rotation au-delà de tes positions.
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
6bis. CHAMPS À RENSEIGNER À PARTIR DE DONNÉES PRÉ-CALCULÉES (ne pas inventer) :
   - macro_regime_readout : recopie le verdict de la PASSE 1 (data.macro_regime :
     regime, confidence_pct, drivers, crypto_bias). Si la passe 1 est absente,
     déduis-le brièvement du contexte macro mais dis-le.
   - macro_impact.exposed_positions[].beta_dxy : utilise data.per_asset_beta
     (by_asset[ACTIF].dxy.beta). Si un bêta n'est pas disponible pour un actif,
     n'invente pas — écris « n/d » et explique en auto-critique. expected_impact_pct
     se déduit du bêta (β × variation DXY plausible), à présenter comme ordre de grandeur.
   - thesis_of_the_day[].historical_pattern : remplis depuis
     data.eligible_theses[].historical_stats (verified = historical_stats.available ;
     occurrences_count, avg_move_pct = avg_forward_pct, win_rate, data_source = "OHLC 90j").
     Si available=false : verified=false et narrative explique l'historique insuffisant.
   - CALENDRIER À VENIR : data.upcoming_calendar.events liste les prochains
     événements macro CONSOLIDÉS (FRED + Boursorama + décisions FOMC/BoJ
     officielles ; les entrées « (estimé) » sont des récurrences statistiques).
     today_watch ne cite QUE des événements de cette liste (avec leur date) ou
     des catalyseurs issus des news fournies. N'invente AUCUN événement ni
     horaire absent des données (audit : « Balance commerciale 14h30 »
     halluciné = défaut majeur).
   - POLYMARKET ÉTENDU (v15) : data.polymarket.fed_bars donne baisse/maintien/
     hausse + le scénario DOMINANT — cite TOUJOURS le dominant en premier
     (« maintien à 99,2% », jamais « baisse 0,2% » seul). data.polymarket.
     extra_markets liste d'autres probabilités de marché à fort volume
     (récession, géopolitique, crypto) : exploite-les comme un EDGE dans le
     panorama macro et les scénarios quand elles éclairent une thèse.
   - MOUVEMENTS PTF > ±10% (v15, audit P1-6) : data.ptf_big_movers_24h liste
     les positions ayant bougé de plus de 10% sur 24h. CHAQUE entrée DOIT être
     commentée quelque part (thèse dédiée si éligible, sinon 1 ligne dans
     sector_rotation_ptf_note ou l'histoire du jour) : un +10,8% du PTF passé
     sous silence = défaut d'audit avéré, quel que soit le tier.
   - R:R : pour chaque plan d'action, calcule action_plan.rr depuis tes entry/TP1/
     stop_loss et ne l'affiche que s'il est fondé (cf. RÈGLE 6).
6ter. RÈGLES DE RENDU SUPPLÉMENTAIRES (v12) :
   - SEUIL DE CONFIANCE ≥ 60% (v14) : n'émets une thèse dans thesis_of_the_day
     QUE si sa confidence est >= 60. En dessous de 60, l'incertitude est trop
     forte pour mériter une thèse dédiée — ne l'inclus PAS (ni en SURVEILLER, ni
     autrement). Mieux vaut 2 thèses solides (≥60) que 5 thèses tièdes. Si aucun
     actif n'atteint 60, renvoie thesis_of_the_day vide + thesis_empty_reason.
   - SURVEILLER / MAINTENIR : N'ÉMETS AUCUN action_plan (pas de "Take profit:
     None / None / None", pas d'entrée). Une position surveillée n'a pas de plan
     d'entrée — explique juste en 1 phrase ce que tu attends pour agir.
   - Le score de risque PTF est déjà calculé (data.risk_score : score/10, level,
     factors). Ne le recalcule pas ; le rendu l'affiche. Tu peux y faire référence
     en 1 phrase dans "en bref" si pertinent.
   - VS HIER : si data.reco_evolution_30d ou l'état du soir révèlent un vrai
     changement (nouveau régime, reco retournée, nouvelle thèse), dis-le en 1
     phrase. Sinon, n'invente pas de comparaison.
   - POUSSIÈRES (<10 $) : pas de thèse ni d'analyse (RÈGLE 2bis).
   - RÉFÉRENCE VALORISATION : utilise market_cap autant que la distance à l'ATH
     quand c'est pertinent (RÈGLE 9bis).
   - POLYMARKET (v14) : data.polymarket fournit des probabilités de marché. Au-delà
     de la décision Fed, exploite TOUTES les probabilités importantes disponibles
     (récession, plafond de la dette, élections/votes macro, prix BTC cible, etc.)
     quand elles éclairent le contexte. Présente-les clairement : indique TOUJOURS
     la probabilité de l'ÉVÉNEMENT formulé positivement (ex. « maintien des taux
     99,8% » et non « cut 0,2% » qui prête à confusion). Polymarket = un edge sur
     le probable ; mets-le en valeur sans le déformer.
7. Termine par les angles morts (data.blind_spots) — recopie-les fidèlement.
   Si MVRV/on-chain CoinMetrics est indisponible, NE le répète PAS dans plusieurs
   sections (1 mention max en angle mort) et NE bloque pas l'analyse pour autant.

{OUTPUT_CONTRACT}
Disclaimer à placer dans footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_MORNING_SCHEMA}
"""
