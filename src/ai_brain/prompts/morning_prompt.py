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
  "macro_regime_readout": {"regime (risk-on/risk-off/neutre — repris de la PASSE 1 data.macro_regime)","confidence_pct","reading (v16 — UNE phrase d'INTERPRÉTATION concrète du régime, PAS une liste d'indicateurs bruts : ex. 'Les actions montent mais l'or et la Peur Extrême signalent une prudence sous-jacente : appétit pour le risque fragile.')","crypto_bias (ce que ça implique pour le crypto, court)"},
  "self_critique_global": {"bullets": ["angle mort 1 (1 ligne)","angle mort 2","angle mort 3 (2-4 puces max)"]},
  "invalidation_watch": [{"condition (ex. 'DXY > 101,0')","implication (1 ligne : ce que ça invaliderait)"}],
  "active_recommendations_tracking": [{"asset","action","issued_at","ct_target","current_price","progress_pct","progress_label","status","status_color"}],
  "tracking_footnote": "string (1 phrase : leçon récente, ce qu'a appris l'agent)",
  "macro_context": {"btc_price","btc_note (ex. 'range macro')","fear_greed","fear_greed_label (ex. 'peur extrême')","dxy","dxy_note (ex. 'cassure ↑')","polymarket_fed_cut_pct","fed_cut_note (ex. '−10pts en 2 sem.')","regime_synthesis (v23.x — LE paragraphe macro COMPLET du « Contexte global » : 2-3 phrases DENSES, zéro blabla. Croise DXY/Gold/VIX/courbe des taux 2s10s + actions US ET internationales (Nikkei/Stoxx/DAX), la Fed/Polymarket (proba + implication LIQUIDITÉ pour les actifs risqués) et le DÉCOUPLAGE du crypto (F&G). Chaque phrase = un fait chiffré + son implication. Le VERDICT de régime (transition/risk-on/off · confiance %) et le BIAIS crypto (garde-fou) sont affichés SÉPARÉMENT en tête du bloc par le système — NE les répète pas, complète-les. Ex. 'Transition : DXY stable 101,2, Gold −0,3%, VIX 18,5 modéré ; actions US en repli léger mais Asie/Europe résilientes (Nikkei +107, DAX +48). Courbe 2s10s +0,31 (cycle en bascule). Fed attendue en maintien (81,5% Polymarket) sur inflation+emploi robustes → liquidité bridée pour le risque. Crypto en Peur extrême (F&G 12), décorrélé des actions.')"},
  "risk_score_readout": {"driver (1 phrase : CE QUI pèse le plus dans le score, ex. 'Score tiré par la concentration L1 47% et l'absence de cash')","caveat (1 phrase de NUANCE CRITIQUE : un score de risque est subjectif et déterministe, il ne capte pas tout — ex. 'Note indicative : elle ne mesure pas le risque idiosyncratique projet ni les corrélations cachées')","reco (1 phrase ACTIONNABLE pour réduire le risque, ex. 'Pour baisser d'un cran : reconstituer 5-10% de cash et diversifier hors L1')"},
  "onchain_indicators": {
    "metrics": [{"label (COURT, ex. 'MVRV BTC')","value (LISIBLE et formaté humainement — JAMAIS un nombre brut : '$265 Mds' pas '265492887109.0' ; '63 000 $' pas '63000.0' ; '1.41' pour un ratio ; '0' pas '0.0'. Si la donnée est nulle/absente, mets 'n/d')","color (hex)","short (mini-commentaire ≤6 mots affiché sous la valeur, ex. 'profit latent modéré')","interpretation (1 phrase, repli si short absent)"}],
    "verdict": "positif|négatif|neutre (CONCLUSION GLOBALE on-chain, annoncée en tête de la lecture)",
    "combined_reading": "string (APRÈS le verdict : ce que ça implique pour l'investisseur — orienté DÉCISION, pas seulement description)"
  },
  "onchain_empty_reason": "string (REQUIS si onchain_indicators absent)",
  "sector_rotation": [{"sector","change_24h","leaders (string ex. 'DOGE PEPE')","your_holdings": ["ticker1","ticker2"]}],
  "sector_rotation_ptf_note": "string (1-2 phrases : ce que la rotation veut dire sur TON ptf. v19/M-B13 — LIS LES 3 FENÊTRES de data.sector_rotation[] : change_24h ET change_7d ET change_30d. Commente toute DIVERGENCE entre elles, ex. 'L2 rebondit à court terme (+5% 24h, +10% 7j) mais reste baissier sur 30j (−17%) : rebond technique, pas de retournement confirmé'. Ne te limite JAMAIS à la 24h seule.)",
  "news_24h": [{"category (Macro/Géopo/Catalyseur/Risque/Filtré)","tag_bg (hex)","tag_color (hex)","title","source","timestamp","confidence (ENTIER 0-100, jamais /5 ; v16 : ≤ 80 PAR DÉFAUT — 90+ réservé à un fait CERTAIN et vérifié, pas à une interprétation)","impact_on_ptf (v16 : lien d'impact sur le PTF, 3-4 LIGNES MAX, dense, pas de remplissage)","is_update (BOOL optionnel — v18/M-A10 : mets true UNIQUEMENT si c'est un VRAI complément/évolution d'une news déjà sortie un jour précédent, ex. un chiffre nouveau ou un retournement. Une news déjà couverte SANS élément nouveau ne doit PAS être re-soumise : le système dédoublonne sur 48h. Par défaut false/absent.)"}],
  "news_24h_empty_reason": "string (REQUIS si news_24h vide — RARE, voir RÈGLE 8)",
  "today_watch": "string (PROSE : 2-3 catalyseurs/risques précis à surveiller dans la journée)",
  "thesis_of_the_day": [{
     "asset","name (nom complet ex. 'The Graph')","tier_label (recopie data.eligible_theses[].tier_label, ex. 'Tier 2 · mid cap')","price_line (ex. '$0.026 · position $11.49 · +8% / 24h')",
     "action","action_type (bullish|bearish|neutral)","thesis_type (v18/Chantier F : 'tactical' OU 'conviction'. RECOPIE data.eligible_theses[].thesis_scoring.thesis_type comme base. TACTIQUE = court terme 7-30j, porté par technique + catalyseur immédiat, confiance 55-70%, R/R ≥ 2:1, stop serré. CONVICTION = long terme 3-12 mois, porté par fondamentaux + position sous PRU + structure W1/M1, confiance 65-85%, stop LARGE = invalidation de thèse, paliers d'accumulation au lieu de TP court terme. Ces deux types s'affichent distinctement.)","confidence","size_note (ex. 'taille standard')",
     "reliability (complète|partielle)",
     "signals_summary (v21 : RECOPIE le score pondéré ET la convergence depuis data.eligible_theses[].thesis_scoring — ex. 'score 9 · seuil 2 · 4 familles convergentes'. N'écris JAMAIS 'seuil non atteint' pour un actif listé dans data.eligible_theses : il EST éligible par construction.)",
     "observation (PROSE plusieurs phrases — décris CE QUI CONVERGE réellement, familles + niveaux chiffrés. INTERDIT d'affirmer qu'un seuil n'est pas atteint pour une thèse listée : c'est contradictoire avec son éligibilité.)","sources_timestamps (ex. 'CoinGecko 08h12 · TradingView 08h15')",
     "reasoning_signals": ["signal 1 phrasé complet","signal 2",".."],
     "historical_pattern": {"verified","narrative (PROSE détaillée si verified)","occurrences_count","avg_move_pct","max_drawdown_pct","win_rate","data_source"},
     "self_critique (PROSE plusieurs arguments)","macro_coherence (PROSE)",
     "targets": {"short_term_label (ex. 'Tactique court terme · 30j')","short_term_30d (NOMBRE BRUT, ex. 285 — JAMAIS '285,00 $' ni '285$' — ANCRE-le sur data.eligible_theses[].projection.short_term_30d.target, confluence prioritaire, DANS la bande réaliste ; cf. RÈGLE PROJECTION)","short_term_note (ex. '+19,8% · confluence résistance + Fibonacci 0,618' — CITE la base du chiffre)","long_term_6_12m_low (NOMBRE BRUT — ancré sur projection.long_term_6_12m.low)","long_term_6_12m_high (NOMBRE BRUT — ancré sur projection.long_term_6_12m.high)","long_term_note (CONDITION NOMMÉE, ex. 'retour ATH si narratif AI confirmé')"},
     "watch_trigger (UNIQUEMENT si action SURVEILLER/MAINTENIR : 1 phrase, le déclencheur chiffré qui ferait passer à l'action)",
     "action_plan": "OBJET UNIQUEMENT si action = RENFORCER ou ALLÉGER. Pour SURVEILLER/MAINTENIR : OMETTRE complètement action_plan (ne pas mettre de champs 'None'). Forme: {entry, limit_orders, position_size_pct, take_profit:{30pct,30pct_b,40pct}, stop_loss, stop_loss_basis, rr (ex '3.2:1' si fondé sinon omettre), invalidation_conditions}. v17 RÈGLE STRICTE FORMAT : entry, take_profit.*, stop_loss et TOUS les prix sont des NOMBRES BRUTS (ex. 264.3, 285, 302.17) — JAMAIS de chaîne pré-formatée ('264,30 $', '302.17 $', '285,00 $'). Le rendu applique le format ; si tu écris le symbole $ ou des séparateurs, tu provoques des incohérences (302.17 $ ET 302,00 $). stop_loss_basis est du TEXTE court (ex. 'bande basse Bollinger') et le niveau qu'il cite DOIT être cohérent avec stop_loss (même valeur). v18/M-B15 — position_size_pct (NOMBRE) : taille du geste en % DU PORTEFEUILLE (ex. 2 pour '+2% du PTF'), pas en % de la position. Dimensionne selon la conviction × la tradabilité (data.eligible_theses[].tradability) × le garde-fou macro (réduis si prudence). NE fournis PAS position_size_usd : la taille en $ est calculée AUTOMATIQUEMENT (% × valeur PTF). v23.x — stop_loss S'ANCRE sur data.eligible_theses[].projection.stop_suggestion (swing low réel) quand il existe. v18/M-B16 — stop_loss pour une CONVICTION LONG TERME = niveau d'INVALIDATION DE LA THÈSE (cassure d'un support majeur W1/M1, perte d'un palier structurel), PAS un stop technique serré (RSI/Bollinger intraday) qui serait touché par le bruit. Pour Omar (investisseur long terme), un stop à -3% sur une conviction n'a aucun sens : le stop doit laisser respirer la thèse. stop_loss_basis explicite la nature (ex. 'invalidation : cassure support W1 58k' vs 'bande basse Bollinger 1h')."
  }],
  "thesis_empty_reason": "string (REQUIS si thesis_of_the_day vide — v18/M-B11 : explique POURQUOI on attend, de façon CONCRÈTE et actionnable. Ne dis PAS juste 'pas de signal' : nomme ce qui manque et ce qui débloquerait une thèse. Ex. 'BTC consolide sous 64k sans volume : il faudrait une cassure confirmée >64.5k ou un repli vers 62k (support) pour une entrée. ETH attend que le MVRV repasse <0.9 ou un catalyseur ETF. Rien d'assez convergent ce matin — patience.' Cite 1-2 actifs précis, leurs niveaux/déclencheurs, et ce qu'on surveille.)",
  "macro_impact": {
    "intro": "string (PROSE courte : l'impact macro du jour sur le PTF)",
    "exposed_positions": [{"asset (un actif RÉEL du PTF)","driver (le facteur macro, ex. 'DXY > 100')","effect (effet attendu CHIFFRÉ ou directionnel sur cet actif, ex. 'pression baissière, −3 à −5%')"}],
    "implication": "string (LE 'Donc' — CONCLUSION ACTIONNABLE : nomme 1-3 actifs du PTF et leur exposition concrète, ex. 'TAO et FET, les plus sensibles au risk-off, à alléger si le DXY casse 100 ; BTC plus résilient'. INTERDIT : répéter l'auto-critique globale, citer une limite méthodologique, ou citer une corrélation < 0,25 (bruit). Si aucun bêta significatif, dis simplement quels actifs sont structurellement les plus exposés au régime et quoi surveiller.)"
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
1. v16 — PLUS D'« histoire du jour » (le pavé narratif est supprimé). Le SEUL
   résumé de tête est "executive_summary.bullets" : 4 à 5 PUCES typées,
   l'ESSENTIEL pour comprendre la journée en 5 secondes. Chaque puce = 1 ligne
   scannable avec un chiffre. icon '✓' = élément positif/action, '⚠' =
   vigilance, '✗' = risque avéré. PAS de paragraphe, PAS de redite entre puces.
   Couvre : le régime macro, le signal on-chain ou sectoriel dominant, le
   risque principal, et l'action/biais du jour s'il y en a un.
   "self_critique_global" : 2-4 PUCES (1 ligne chacune) — quelles sources
   manquent ce matin, quelles incertitudes pèsent, ce qui invaliderait le
   scénario. Des angles NOUVEAUX (RÈGLE 10bis), pas les redites des thèses.
   "invalidation_watch" (v15) : LISTE de 2-4 objets {{condition, implication}},
   chaque condition CHIFFRÉE (« S&P 500 < 7 200 en clôture »). v21 (#75) — sois
   PROACTIF avec Polymarket : croise ces seuils avec les probabilités de marché
   réelles (data.polymarket.extra_markets / fed_bars / macro_context.polymarket_*).
   Si un marché pertinent affiche une probabilité ÉLEVÉE (≥ 70%), n'énonce pas
   seulement l'invalidation — DÉRIVE l'implication dans ce sens et oriente le
   scénario central (ex. « Polymarket donne 82% de maintien des taux → pas de
   détente monétaire avant [date], ce qui plafonne le rebond des alts ; la thèse
   ne bascule que si cette proba chute sous 60% »). Le marché de prédiction ORIENTE
   l'analyse, il n'est pas une simple note de bas de page.
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
   v16 — ON-CHAIN : fournis 7 à 8 indicateurs MAX (les plus parlants : MVRV BTC,
   MVRV ETH, adresses actives, put/call, max pain, supply stablecoins, whale
   inflows, et un de plus si pertinent). Chaque métrique porte un `short` :
   mini-commentaire d'implication de ≤6 mots (« profit latent modéré », « zone
   d'accumulation », « pression vendeuse faible »). La `combined_reading`
   COMMENCE par un `verdict` (positif / négatif / neutre) qui tranche le bilan
   on-chain global, PUIS explique l'implication pour la DÉCISION d'investissement
   (« on-chain neutre → pas de signal d'entrée fort, attendre confirmation prix »).
   Ne te contente JAMAIS de décrire : conclus et oriente.
   v18 (M-A21 — COHÉRENCE) : le `short` d'une métrique doit être COHÉRENT avec le
   `verdict` global. N'écris PAS « MVRV ETH 0.97 → zone d'accumulation » (signal
   haussier fort) si ton verdict global est « neutre » : soit le `short` devient
   « sous-évalué mais activité molle » (nuancé, cohérent avec neutre), soit le
   verdict passe à « légèrement positif ». Un tile « accumulation » + un bilan
   « neutre » sur le MÊME actif est une contradiction à proscrire.
   v18 (M-A22 — FRAÎCHEUR) : si des métriques on-chain sont en différé (miroir
   daté, ex. 23/05), mentionne-le UNE SEULE FOIS (dans combined_reading OU en note,
   pas les deux). Ne répète pas « données du 23/05 » dans plusieurs paragraphes.
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
   v19/M-A10 — FRAÎCHEUR DES NEWS : une news dont l'heure remonte à PLUS DE 12h
   n'est PAS un « catalyseur du jour ». Classe-la en category "Info" (contexte),
   JAMAIS "Catalyseur", et ne la présente pas comme animant la séance du jour ;
   réserve "Catalyseur" aux événements frais (< 12h) ou à venir. Le système
   dédoublonne déjà sur 48h : ne re-soumets pas une news déjà couverte sans
   élément RÉELLEMENT nouveau (is_update=true uniquement dans ce cas).
   v16 — DEUX RÈGLES STRICTES sur les news : (a) CONFIANCE ≤ 80 PAR DÉFAUT.
   Un score de 90+ est réservé à un FAIT certain, vérifié, public (« la Fed a
   maintenu ses taux »), JAMAIS à une interprétation ou à une prévision
   d'impact (« cette news est haussière »). Sois sobre : la plupart des news
   méritent 60-80. (b) Le commentaire impact_on_ptf fait 3-4 LIGNES MAX, dense
   et actionnable — pas de paragraphe, pas de remplissage. Va à l'essentiel :
   qui est touché dans le PTF et dans quel sens.
   v17 (M-A13/M-A14/M-A16) RÈGLES SUPPLÉMENTAIRES sur les news : (c) GRANDS
   NOMBRES dans les titres/analyses → format humain abrégé : « $350 Mds » jamais
   « $350,000,000,000 » ; « 2,4 M$ » jamais « 2400000 ». Les longues séries de
   zéros mangent l'espace. (d) PAS DE DOUBLON avec les tuiles : ne crée pas une
   news qui répète une donnée déjà affichée en tuile (ex. « Fear & Greed Index:
   18 » alors que F&G 18 est déjà en tuile) — c'est du bruit. (e) COHÉRENCE PRIX :
   tout prix cité dans un titre/analyse de news DOIT être cohérent avec le spot
   réel (data.macro_context.btc_price) ; si un titre mentionne un prix très
   différent du spot (ex. « BTC à 77K » alors que le spot est 64,6K), NE le
   reprends PAS tel quel — soit tu l'ignores, soit tu signales explicitement
   l'écart. Aucune hallucination de prix.
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
   - v22 — NOUVELLES DIMENSIONS À EXPLOITER (combler l'analyse des alts) :
     • data.eligible_theses[].valuation (FONDAMENTAL) : ratios RÉELS — FDV/MC
       (dilution future), % en circulation + dilution_remaining_pct (pression
       d'émission), P/F et P/S (cher/pas cher vs frais/revenus réels), MC/TVL.
       Utilise-les dans la thèse au lieu de raisonner uniquement sur le prix.
       valuation.signals donne déjà les lectures prêtes. Pour un alt sans
       valuation (non-DeFi), dis-le (donnée fondamentale absente → confiance bridée).
     • data.eligible_theses[].relative_strength.rs (FORCE RELATIVE vs BTC, 7/30/90j) :
       un alt qui SOUS-performe BTC n'est pas un bon hold même s'il monte en absolu.
       Intègre ce verdict (relative_strength.reading) dans toute thèse d'alt.
     • data.portfolio_risk (DÉCISION DE PORTEFEUILLE, pas seulement par actif) :
       concentration (nombre EFFECTIF de paris via HHI), stress_test
       (« si BTC −20% → PTF ≈ X% »), var_95_pct, beta_to_btc par position. Sers-t'en
       dans macro_impact / risk_score_readout pour chiffrer le risque réel et
       dimensionner. data.portfolio_risk.readings donne les phrases prêtes.
     • data.eligible_theses[].derivatives.long_short_ratio (OKX) : positionnement de
       la foule (> 1 = longs majoritaires ; extrême = contrarian).
     • data.cross_signals readings v22 : yield_curve (courbe 2s10s, récession),
       real_rates (taux réel 10Y = coût d'opportunité), fed_liquidity (QE/QT + RRP),
       altseason (dominance BTC → conditions alts). Intègre-les au régime macro.
     • data.crypto_events (CoinMarketCal) : catalyseurs crypto DATÉS (mainnet,
       listings, upgrades, votes). Croise-les avec tes positions pour today_watch
       et les catalyseurs de thèse (n'invente jamais d'événement absent de la liste).
     • data.eligible_theses[].tradability : garde-fou de TAILLE. Si liquidity
       « faible » (microcap), dimensionne PETIT même si la thèse est forte (slippage).
   - data.cross_signals (v18 — Partie 4, ANALYSE TRANSVERSE) : signaux de CONTEXTE
     déterministes (Python) que ton analyse DOIT intégrer pour être complète.
     data.cross_signals.readings est une liste de lectures prêtes à l'emploi :
     liquidité M2 (driver structurel), cycle du dollar (DXY 3-6 mois), spreads
     high yield (risk-off avancé), saisonnalité du mois, régime de volatilité
     réalisée du PTF (compression = calme avant tempête), structure de marché
     D1 par actif (HH/HL haussier vs LH/LL baissier), MVRV en perspective de
     cycle. Si data.cross_signals.signals.confirmation_bias est actif, tu DOIS
     argumenter explicitement le scénario CONTRAIRE sur les actifs signalés
     (anti-momentum-bias). Ces signaux nourrissent ta lecture macro et tes
     thèses ; tu n'es pas obligé de tous les citer, mais ton analyse doit en
     tenir compte (ne conclus pas « contexte porteur » si M2 se contracte et que
     les spreads HY s'écartent).
   - data.macro_guardrail (v18/M-B12/M-B14) : si présent et `active`, des signaux
     macro de PRUDENCE sont détectés en Python (VIX≥25, peur extrême, dollar fort).
     C'est NON NÉGOCIABLE : ton récit, tes thèses et ton sizing doivent refléter
     cette prudence (pas de ton trop haussier, pas de renforcement agressif). Une
     bannière distincte l'affiche déjà — n'entre pas en contradiction avec elle.
   - data.reco_changes : tes changements d'avis récents — si tu changes une reco
     par rapport à avant, explique POURQUOI (quels signaux ont changé, RÈGLE 13... 
     en pratique : sois transparent sur le revirement).
6bis. CHAMPS À RENSEIGNER À PARTIR DE DONNÉES PRÉ-CALCULÉES (ne pas inventer) :
   - portfolio_snapshot (value_usd, change_24h_pct, change_7d_pct, vs_btc_7d_pct,
     drawdown_ath_pct) est CALCULÉ CÔTÉ PYTHON. v17 (T-7J / M-A7) : si tu cites la
     perf 7j du PTF ou le vs BTC 7j dans EN BREF ou ailleurs, REPRENDS EXACTEMENT
     data.portfolio_snapshot.change_7d_pct et .vs_btc_7d_pct — ne recalcule
     JAMAIS un autre chiffre. Un seul couple 7j dans tout le mail.
   - macro_regime_readout : recopie le verdict de la PASSE 1 (data.macro_regime :
     regime, confidence_pct, drivers, crypto_bias). Si la passe 1 est absente,
     déduis-le brièvement du contexte macro mais dis-le.
   - macro_impact (v16) : « liens chiffrés sur ton PTF » doit NOMMER des actifs
     RÉELS du portefeuille. exposed_positions = liste {{asset, driver, effect}} :
     l'actif, le facteur macro déclencheur (ex. « DXY > 100 »), et l'effet
     attendu chiffré ou directionnel sur cet actif. Utilise data.per_asset_beta
     (by_asset[ACTIF].dxy.beta) quand le bêta existe pour estimer l'effet ; sinon
     raisonne sur l'exposition structurelle (un Tier-2 AI à fort bêta tech est
     plus risk-off-sensible que BTC). Le champ implication (« Donc ») est une
     CONCLUSION ACTIONNABLE qui nomme 1-3 actifs et leur exposition concrète.
     INTERDIT dans « Donc » : (a) répéter l'auto-critique globale, (b) citer une
     limite méthodologique (« absence de bêtas significatifs… »), (c) citer une
     corrélation < 0,25 (c'est du bruit : BTC↔S&P +0,03 n'a aucune valeur). Le
     « Donc » doit aider à décider, pas se dédouaner.
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
     v18 (M-A13) : reprends la probabilité EXACTEMENT comme fournie dans data
     (déjà arrondie à l'entier pour les extra_markets). N'invente JAMAIS un
     dixième (« 21,3% » alors que data dit 21%) : la tuile et ton texte doivent
     afficher le MÊME chiffre.
   - MOUVEMENTS PTF > ±10% (v15, audit P1-6) : data.ptf_big_movers_24h liste
     les positions ayant bougé de plus de 10% sur 24h. CHAQUE entrée DOIT être
     commentée quelque part (thèse dédiée si éligible, sinon 1 ligne dans
     sector_rotation_ptf_note ou une puce d'EN BREF) : un +10,8% du PTF passé
     sous silence = défaut d'audit avéré, quel que soit le tier.
     v16.1 — EXPLIQUER LA CAUSE : pour tout mouvement marqué (≥ ±15%), tente
     d'en donner la RAISON en 1 phrase, en CROISANT data.news_24h /
     data.geopolitics / data.sector_rotation (catalyseur projet, rotation
     sectorielle, news macro) ou, à défaut de catalyseur identifiable, dis-le
     honnêtement (« pas de catalyseur identifié, probablement un mouvement
     technique/flux »). Ne JAMAIS inventer une news : si tu n'as pas de source
     dans les données, formule-le comme une hypothèse de marché (effet
     momentum/short squeeze/rotation), pas comme un fait. Objectif : Omar doit
     comprendre POURQUOI sa position a bougé. Exemple : « TAO +18% : rotation
     vers l'IA décentralisée après la news d'interdiction d'un modèle IA
     centralisé (The Block), effet narratif sur le secteur. »
   - R:R : pour chaque plan d'action, calcule action_plan.rr depuis tes entry/TP1/
     stop_loss et ne l'affiche que s'il est fondé (cf. RÈGLE 6).
6ter. RÈGLES DE RENDU SUPPLÉMENTAIRES (v12) :
   - v23.x (SEUIL D'AFFICHAGE UNIQUE 75% — DEMANDE D'OMAR, NON NÉGOCIABLE) :
     toute thèse affichée dans thesis_of_the_day EXIGE une confiance ≥ 75%. Sous
     75% : NE l'émets PAS (filtre anti-bruit — on ne montre que les convictions
     FORTES et bien analysées). À 75% ou plus : la thèse est recommandée. Le
     système RE-FILTRE déterministiquement à 75% : une thèse à 74% sera supprimée,
     donc n'en produis pas. Ce seuil s'applique aux DEUX types (tactique ET
     conviction) et aux SURVEILLER affichées.
   - v18 (Chantier F — SEUILS DE CONFIANCE PAR TYPE) : la confiance dépend du TYPE,
     mais le PLANCHER D'AFFICHAGE 75% prime sur tout.
       • Thèse TACTIQUE : plancher d'affichage 75%, plafond 80%.
       • Thèse de CONVICTION : plancher d'affichage 75%, plafond 85%.
       • Confiance > 80% INTERDITE sauf si ≥ 5 dimensions convergent ET aucune
         ne contredit (data.eligible_theses[].thesis_scoring.dimensions_count et
         .confidence_bounds te donnent le plafond exact applicable).
       • P0 #53 — PLAFOND DE COMPLÉTUDE (NON NÉGOCIABLE) : ta confiance ne peut
         JAMAIS dépasser data.eligible_theses[].thesis_scoring.confidence_bounds.cap,
         qui intègre la COMPLÉTUDE de l'analyse (thesis_scoring.completeness.pct +
         .missing). Quand des dimensions manquent (ex. un alt sans on-chain ni
         dérivés : completeness 50%), tu DOIS (a) plafonner la confiance, et (b) le
         DIRE explicitement dans l'auto-critique (« analyse partielle : pas d'on-chain
         ni de dérivés sur cet actif → confiance plafonnée à 65% »). Une reco ferme à
         haute confiance sur une analyse à trous est INTERDITE. COROLLAIRE du seuil
         75% : si ce plafond de complétude tombe SOUS 75%, la thèse ne peut PAS être
         affichée — ne l'émets pas (au mieux une ligne de surveillance dans
         all_positions_summary). C'est voulu : une analyse à trous = du bruit, filtré.
       • P0 #59 — FRAÎCHEUR : si data.eligible_theses[].data_freshness.onchain_as_of
         est ancien (miroir daté), ne présente pas une métrique on-chain comme un
         « signal du jour » ; cite l'as_of une seule fois. Prix/technique/dérivés
         sont « live ».
       • v24 — RENFORCÉ : une métrique on-chain de PLUS DE 2 SEMAINES (ex. MVRV au
         23/05 alors qu'on est en juillet) est un CONTEXTE STRUCTUREL, jamais un
         déclencheur d'accumulation présenté comme actuel — vaut AUSSI dans l'EN
         BREF, les thèses et la watchlist, pas seulement la grille on-chain.
     La confiance doit refléter la CONVERGENCE MULTIDIMENSIONNELLE, pas
     l'enthousiasme. Sous le seuil du type, n'émets PAS la thèse. Si aucun actif
     n'atteint son seuil, renvoie thesis_of_the_day vide + thesis_empty_reason.
   - v18 (Chantier F — ÉLIGIBILITÉ PAR SCORE PONDÉRÉ) : data.eligible_theses ne
     contient QUE des actifs déjà jugés éligibles par un score PONDÉRÉ
     multi-dimensions (un seul signal fondamental LT fort — MVRV < 1 + position
     sous PRU — peut suffire, MÊME dans le calme sans mouvement de prix). NE
     REJETTE PAS un actif éligible au prétexte qu'il « ne bouge pas » : les
     meilleures entrées d'accumulation arrivent dans le calme. data.eligible_-
     theses[].thesis_scoring porte le score, le type suggéré et les signaux par
     catégorie — appuie-toi dessus.
   - v19 (ANTI-THÈSE-VIDE — corrige le « zéro reco » systématique) : pour un
     investisseur LONG TERME, l'ABSENCE de catalyseur immédiat n'est JAMAIS un
     motif de rejet. Un actif éligible dont
     data.eligible_theses[].thesis_scoring.fundamental_weight ≥ 3 (signal
     fondamental fort : MVRV < 1, position sous PRU, drawdown profond sur
     conviction) porte un setup d'ACCUMULATION valable : analyse-le PLEINEMENT et
     NE le rejette PAS au seul prétexte qu'« aucun catalyseur immédiat » n'existe.
     La confiance d'une CONVICTION repose sur la convergence FONDAMENTALE (+
     structure W1/M1), PAS sur un catalyseur. MAIS le seuil d'affichage 75% prime :
     émets la thèse SI elle atteint HONNÊTEMENT 75% (convergence fondamentale forte
     ET complétude suffisante) ; si elle ne les vaut pas, NE gonfle PAS le chiffre —
     laisse-la hors thèses et explique-le dans thesis_empty_reason. Tu gardes le
     choix de l'action quand elle est affichée :
       • RENFORCER si le niveau actuel est déjà une entrée d'accumulation
         correcte (fournis entry + paliers + invalidation de thèse) ;
       • SURVEILLER si tu vises un meilleur prix, MAIS alors watch_trigger DOIT
         donner le NIVEAU DE PRIX précis ET le déclencheur chiffré (ex.
         « accumuler ETH sous 1 650 $, ou si MVRV repasse < 0,90 »). Jamais de
         surveillance vague sans niveau.
     thesis_of_the_day est vide dès qu'AUCUN actif éligible n'atteint 75% de
     confiance (cas désormais plus fréquent, c'est le but du filtre anti-bruit) ;
     alors thesis_empty_reason détaille le manque PAR actif (niveau à surveiller +
     trigger + à combien de % de confiance on plafonne), jamais un « pas de signal »
     générique. Un PTF sans thèse à ≥75% un matin donné est NORMAL et honnête.
   - v19 (Partie 5 §3 — THÈSE MULTIDIMENSIONNELLE) : toute thèse doit intégrer
     EXPLICITEMENT les 9 DIMENSIONS suivantes (pas seulement celles qui ont
     déclenché les signaux ; cite les chiffres réels de chacune et explique sa
     contribution à la reco) : (1) MACRO (régime risk-on/off, DXY, 10Y, calendrier
     banques centrales ≤7j, corrélation actuelle au DXY/SPX), (2) NEWS & CATALYSEURS
     (événements <72h, calendrier ≤7j, narratifs émergents), (3) TECHNIQUE (niveau
     vs supports/résistances D1 ET W1, RSI multi-TF, MA50/200, Bollinger, volume),
     (4) ON-CHAIN (MVRV, NVT, adresses actives, flux exchanges, concentration
     whales), (5) DÉRIVÉS (funding, Open Interest, put/call, max pain, skew),
     (6) SENTIMENT (Fear & Greed, Polymarket pertinent, social), (7) POSITION DANS
     LE PTF (PRU, drawdown depuis entrée, poids actuel, sur/sous-pondération vs
     conviction LT), (8) ROTATION SECTORIELLE (perf 7j du secteur, narratif,
     comparables intra-secteur), (9) FONDAMENTAUX PROJET (TVL si DeFi, croissance
     utilisateurs, partenariats, activité dev GitHub). Une thèse RENFORCER doit
     tenir même si on retire UN argument (test de robustesse). Une thèse qui
     n'évoque qu'une ou deux dimensions est INCOMPLÈTE et doit être enrichie.
   - v18 (Chantier F — GARDE-FOUS) : R/R minimum 1.5:1 pour les fermes, 2:1 pour
     les tactiques. ALLÉGER sur une CONVICTION LT exige une justification
     FONDAMENTALE (pas juste un RSI élevé). Pas de thèse sur une poussière
     (< 10 $) sauf catalyseur exceptionnel. Cohérence avec firm_postures.
   - SURVEILLER / MAINTENIR : N'ÉMETS AUCUN action_plan (pas de "Take profit:
     None / None / None", pas d'entrée). Une position surveillée n'a pas de plan
     d'entrée — explique juste en 1 phrase ce que tu attends pour agir.
   - v23 — La note de SANTÉ du portefeuille (data.health_score : score/10 où
     PLUS HAUT = PLUS SAIN, axes Diversification/Momentum vs BTC/Solidité,
     driver, improve) est calculée ET affichée AUTOMATIQUEMENT par le système,
     avec un bref « ce qui tire la note » et un « pour l'améliorer ». NE la
     recalcule pas, NE la duplique pas en prose, n'écris PAS de « note de risque
     PTF » (ce concept a été remplacé par la santé). Tu peux t'y référer en 1
     demi-phrase si une thèse le justifie, mais le bloc dédié s'en charge.
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
     le probable ; mets-le en valeur sans le déformer. v21 (#75) — PROACTIVITÉ :
     quand une probabilité est forte (≥ 70%) ou a bougé nettement, TIRES-EN une
     conclusion actionnable pour le PTF (quel actif/secteur en profite ou souffre,
     quel niveau surveiller), au lieu de te contenter de l'afficher.
7. Termine par les angles morts (data.blind_spots) — recopie-les fidèlement.
   Si MVRV/on-chain CoinMetrics est indisponible, NE le répète PAS dans plusieurs
   sections (1 mention max en angle mort) et NE bloque pas l'analyse pour autant.

8. v17 — RÈGLES DE COHÉRENCE (l'audit a relevé ces incohérences, à éliminer) :
   - (M-A6) UNE SEULE valeur de variation 24h par actif dans tout le mail. Si TAO
     est à +24,0% dans la heatmap, il est à +24,0% dans la thèse et dans
     « positions vs marché » — pas +23,8% ici et +23,96% là. Prends la valeur de
     data (source unique) et reste cohérent partout.
   - (M-A10) MVRV : une SEULE interprétation par valeur. Seuils fixes : MVRV < 1
     = sous la valeur réalisée (accumulation) ; 1–2 = neutre / profit latent
     modéré ; 2–3 = profit latent élevé ; > 3 = euphorie/risque. N'écris pas
     « profit latent modéré » à un endroit et « neutre » à un autre pour le même
     chiffre — choisis et garde la même formule.
   - (v19/M-A22) On-chain PÉRIMÉ : la date de fraîcheur on-chain (miroir, ex.
     23/05) apparaît EXACTEMENT UNE FOIS, en footnote sous la grille on-chain
     (« on-chain MVRV/adresses au JJ/MM — miroir, pas temps réel »). INTERDIT de
     la répéter dans la VALEUR des tuiles, dans le bilan on-chain ET dans
     l'auto-critique (l'audit a relevé 3 mentions : une seule suffit). Ne mélange
     pas un chiffre vieux de 3 semaines avec un prix live sans le dire.
   - (v19/M-A21) MVRV — TUILE vs BILAN (vocabulaire unique) : la TUILE affiche le
     verdict MVRV SEUL avec UN seul libellé (< 1 « sous la valeur réalisée /
     accumulation » ; 1–2 « neutre » ; 2–3 « profit latent élevé » ; > 3
     « euphorie »). Le BILAN on-chain est le verdict COMPOSITE (MVRV + activité +
     flux) et peut conclure « neutre » même si le MVRV seul est « accumulation »,
     MAIS tu dois alors l'expliciter (« MVRV bas mais activité molle → bilan
     neutre »). N'emploie JAMAIS trois mots différents (capitulation / sous-évalué
     / neutre) pour la même donnée sans distinguer clairement tuile et bilan.
   - (v19/M-B10 — STYLE D'ANALYSE UNIFIÉ) : toute section d'analyse en PROSE
     (synthèse, bilan on-chain, note rotation PTF, observation de thèse,
     auto-critique) commence par la CONCLUSION puis structure les idées clés en
     puces courtes, pas un pavé continu. Même logique éditoriale partout.
   - (v19/M-A6 + V18-M9 — ANTI-RÉPÉTITION) : ne répète pas le ticker d'un actif
     plusieurs fois dans la même ligne (« Dépôts Whales ETH … 520 ETH … ETH ») —
     une mention + l'unité suffit. Un même mouvement (ex. CFX +10%) n'est cité
     qu'UNE fois, pas à la fois en top mouvements, en « tes positions » ET en
     heatmap.
   - (v19/V18-M11 — REGROUPER les drivers identiques) : si plusieurs actifs
     partagent EXACTEMENT le même driver macro (ex. TAO/FET/RENDER « FOMC
     hawkish »), regroupe-les en UNE ligne (« Bloc IA : TAO β+2.65 · FET β+2.93 ·
     RENDER β+2.14 — exposition commune au FOMC ») au lieu de 3 lignes identiques.
   - (v19/X10 — ACTIFS SURVEILLÉS JUSTIFIÉS) : tout actif cité en « surveillance
     active » DOIT avoir ≥ 1 ligne d'état (niveau, trigger) ailleurs dans le mail.
     N'ajoute PAS un actif (ex. QNT, XRP) à la watchlist s'il n'apparaît nulle
     part ailleurs et que tu n'as rien à en dire.
   - (v19/V18-M8 — Fed via Polymarket) : dans l'inline des taux directeurs, cite
     la Fed via sa proba Polymarket (« Fed : maintien 99,8% implicite Polymarket »)
     à côté de BCE/BoJ, pas seulement plus haut dans la tuile macro.
   - (v19/M-A5 — CORRÉLATION documentée) : quand tu cites une corrélation (ex.
     BTC↔DXY), précise la FENÊTRE (30j) et ne sur-interprète pas une valeur proche
     de 0 (|corr| < 0,2 = lien ténu, dis-le tel quel ; pas un signal).
   - (v20/M14 — ANTI-RÉPÉTITION INTER-SECTIONS) : un même FAIT macro (la divergence
     « actions US en hausse vs crypto en Peur extrême », la proba Fed, la corrélation
     du PTF) n'est DÉVELOPPÉ qu'UNE fois (bloc Régime macro). Ailleurs (synthèse,
     lecture passe 1, thèses) tu peux y faire référence en une demi-phrase mais SANS
     re-citer les mêmes chiffres (S&P +80, Nasdaq +496, F&G 14, « 9 positions 84% »)
     à chaque section. La redite gonfle le mail et lasse — c'est un défaut.
   - (v20/M4 — CHIFFRE DE SOURCE INDISPONIBLE) : ne cite JAMAIS un chiffre précis
     (flux ETF, funding, on-chain) si sa source figure dans les angles morts /
     indisponibles ce jour. Si la donnée vient d'une news (pas d'un flux structuré),
     attribue-la (« selon X, ~−100 M$ ») sans la présenter comme un flux mesuré.
     Cohérence absolue avec la liste des sources actives.
   - (v20/M11 — FRAÎCHEUR ON-CHAIN AU POINT D'USAGE) : si une thèse s'appuie sur un
     MVRV/une métrique on-chain DIFFÉRÉ (ex. « données au 23/05 »), rappelle la date
     À CET ENDROIT (dans le signal/observation), pas seulement dans la section
     on-chain. Une métrique de 3 semaines ne fonde pas un « signal du jour » sans ce
     caveat explicite.
   - (v20/M10 — SIGNAUX CONVERGENTS HONNÊTES) : un « signal convergent » qui SOUTIENT
     une thèse RENFORCER doit être réellement favorable. Ne classe PAS une observation
     baissière (« mouvement −5% = faiblesse tactique ») parmi les signaux qui
     justifient l'achat : si c'est un risque, il va dans l'auto-critique, pas dans le
     faisceau haussier.
   - (v20/M21 — NEWS > 12h = CONTEXTE) : une news de plus de 12h n'« anime » pas
     AUJOURD'HUI. Date-la et présente-la comme contexte de fond, pas comme catalyseur
     du jour.
   - (v20/M20 — PROPRETÉ RÉDACTIONNELLE) : phrases complètes, parenthèses fermées,
     aucune répétition de mot collée (« développement sur le développement »), « se
     rapprocher DE » (pas « à »). Relis-toi avant de rendre.
   - (v20/M12 — DRAWDOWN COHÉRENT) : le drawdown vs ATH d'un actif est UN seul
     chiffre dans toute la thèse (celui du score pondéré). N'écris pas « −66,9% »
     dans le raisonnement et « −63% » dans le score : reprends la valeur fournie,
     ne la recalcule pas avec un ATH différent.
   - (v20/A3 — BÉTA LISIBLE) : dans « Macro · liens chiffrés sur ton PTF », le
     driver et l'effet d'une position s'écrivent EN CLAIR (« β S&P +2,5 → très
     sensible au risk-off, allège si le S&P casse 7400 »), jamais en notation
     cryptique du type « ≥ S&P500 +2.54 ». Un humain doit comprendre sans légende.
   - (v20/A6 — BLOCS D'INVALIDATION NON REDONDANTS) : « À surveiller aujourd'hui »,
     « Ce que je surveille pour invalider mon scénario » et « Auto-critique » ne
     répètent PAS les mêmes 3-4 facteurs (DXY 101.5, S&P 7400, Fed 35%). Chacun a un
     angle DISTINCT : agenda chiffré du jour / seuils d'invalidation précis / limites
     et angles morts de l'analyse. Si un bloc n'a rien de neuf, fais-le très court.
   - (M-A17) RECO vs HISTORIQUE : si l'analyse historique d'un setup est à
     espérance NÉGATIVE (rendement moyen 7j < 0, win rate < 50%), tu ne peux pas
     recommander RENFORCER avec une confiance élevée sans le justifier. Soit tu
     baisses la confiance, soit tu expliques pourquoi CE cas diffère de
     l'historique. Ne masque pas la tension : nomme-la.
   - (M-A19) STOP LOSS : le niveau de stop_loss et le niveau cité dans
     stop_loss_basis DOIVENT être le même (si la justification est « bande basse
     de Bollinger 203,84 », le stop_loss est 203,84, pas 245). Le R/R affiché est
     calculé depuis entry/TP1/stop réels et cohérent (pas « 2:1 » si entry→TP1
     donne 1,67:1).
   - (v23.x — MÉTHODE DE PROJECTION DES CIBLES · DEEPTHINK · NON NÉGOCIABLE) : les
     3 cases d'une thèse (TACTIQUE 30j · POSITIONNEMENT 6-12 mois · PLAN D'ACTION)
     sont le CŒUR de la reco — leurs chiffres doivent être ANCRÉS, réalistes et
     défendables, JAMAIS inventés. Pour CHAQUE thèse tu disposes de
     data.eligible_theses[].projection, un échafaudage DÉTERMINISTE calculé sur les
     VRAIS niveaux + la volatilité RÉELLE de l'actif :
       • projection.volatility : expected_move_30d_pct = mouvement 30j attendu (ATR×√30) ;
         realistic_30d_high_pct = PLAFOND réaliste. Ta cible 30j NE DOIT PAS dépasser ce
         plafond SANS catalyseur DATÉ explicite (sinon elle est fantaisiste et sera
         ramenée par le système).
       • projection.short_term_30d = cible 30j HAUSSIÈRE déjà ancrée {{target, basis,
         move_pct, confluence}}. projection.short_term_30d_bear = cible BAISSIÈRE
         (support) pour une thèse ALLÉGER. projection.long_term_6_12m = fourchette
         {{low, high, basis}}. projection.levels_above/below = tous les niveaux ordonnés.
         projection.stop_suggestion = stop ancré sous un swing low réel.
       • RÈGLE D'OR COURT TERME : targets.short_term_30d S'ANCRE sur
         projection.short_term_30d.target. Une CONFLUENCE (plusieurs indicateurs au même
         prix, confluence=true) est la cible la plus FIABLE — privilégie-la. Tu peux
         ajuster À L'INTÉRIEUR de la bande réaliste selon le momentum (RSI/MACD), un
         catalyseur ≤30j (data.crypto_events/calendrier), le funding & long-short
         (data.eligible_theses[].derivatives : excès = move amplifié ou risque de squeeze),
         le régime macro (DXY/VIX/liquidité) et Polymarket si pertinent — mais CITE la
         base dans short_term_note (ex. « +19,8% · confluence résistance + Fibonacci
         0,618 »). Si tu t'écartes de la cible ancrée, justifie en 1 membre de phrase.
       • RÈGLE D'OR LONG TERME : targets.long_term_6_12m_low/high S'ANCRENT sur
         projection.long_term_6_12m, puis AFFINÉS par la valorisation
         (data.eligible_theses[].valuation : FDV/MC, dilution restante, P/F, P/S, MC/TVL),
         la phase de cycle, le narratif sectoriel et l'ATH. Le HAUT est CONDITIONNEL à un
         catalyseur NOMMÉ (long_term_note : « retour ATH si narratif X confirmé »). Une
         projection LT sans condition explicite est interdite.
       • PROFONDEUR : le raisonnement DERRIÈRE ces chiffres mobilise les 9 DIMENSIONS
         (technique + on-chain + dérivés + valorisation + macro + calendrier + Polymarket
         + sentiment + rotation). Les CASES restent COURTES (1 ligne de note chacune) ;
         c'est observation/self_critique qui portent la profondeur. Chaque nombre est
         traçable à un niveau réel ou une formule — zéro approximation, zéro hallucination.
       • PLAN D'ACTION (RENFORCER/ALLÉGER) — explicite et EXÉCUTABLE, sans verbiage :
         entry ANCRÉE sur un vrai niveau (repli sur support/MM/Fibo, pas un prix au
         hasard) ; stop_loss = projection.stop_suggestion (ou un swing low / niveau
         d'invalidation réel, cohérent avec stop_loss_basis) ; take_profit aligné sur
         targets ; rr calculé depuis entry/TP1/stop ; position_size_pct dimensionnée par
         la CONVICTION × la tradabilité (data.eligible_theses[].tradability : réduis si
         liquidité faible) × le garde-fou macro (réduis en risk-off). La TAILLE EN $
         (position_size_usd) est calculée AUTOMATIQUEMENT par le système (% × valeur PTF)
         — n'écris QUE le %. invalidation_conditions = le niveau/événement chiffré qui
         CASSE la thèse.
   - (M-A20) INFLATION : si CPI/Core PCE pilotent ton régime macro, ils
     APPARAISSENT dans le contexte macro (macro_impact ou une donnée mise en
     avant), pas seulement dans l'auto-critique. Une donnée qui fonde l'analyse
     se montre.
   - (M-A22) SECTEURS : nomenclature CONSOLIDÉE et STABLE. Pas de multiples
     « Infra » (« Oracle/Infra », « Infra », « Indexing/Infra ») — un secteur =
     un nom. Un actif garde le MÊME secteur entre le matin et le weekly (GRT n'est
     pas « Indexing/Infra » le matin et « Infra » le weekly).
   - (M-A16) MARCHÉ vs NARRATIF : si une probabilité Polymarket CONTREDIT une news
     (ex. marché « accord US-Iran d'ici juin 25% » alors qu'une news annonce
     « accord signé demain » à confiance 75%), SIGNALE le désaccord plutôt que de
     présenter les deux comme vrais. Le marché price une probabilité, la news une
     affirmation : si les deux divergent, dis-le (« le marché reste sceptique à
     25% malgré l'annonce »).

{OUTPUT_CONTRACT}
Disclaimer à placer dans footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_MORNING_SCHEMA}
"""
