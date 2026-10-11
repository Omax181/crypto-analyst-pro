"""Constructeur du prompt pour le rapport du matin.

Assemble persona + données collectées + état mémoire (soir précédent) +
contrat de sortie JSON détaillé (schéma du refactor).
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.vue_modele import vue_modele
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
  "risk_score_readout": {"driver (1 phrase : CE QUI pèse le plus dans le score, ex. 'Score tiré par la concentration L1 47% et l'absence de cash')","caveat (1 phrase de NUANCE CRITIQUE : un score de risque est subjectif et déterministe, il ne capte pas tout — ex. 'Note indicative : elle ne mesure pas le risque idiosyncratique projet ni les corrélations cachées')","reco (1 phrase : le levier qui ferait baisser le score — information, sans geste ni taille ; ex. 'Le score baisserait avec une concentration L1 moindre')"},
  "onchain_indicators": {
    "verdict": "positif|négatif|neutre (CONCLUSION GLOBALE on-chain, annoncée en tête de la lecture)",
    "combined_reading": "string (APRÈS le verdict : ce que ça implique pour l'investisseur — orienté DÉCISION, pas seulement description. v29 (MB2) : MAX 2 phrases de LECTURE CROISÉE (ce que les métriques disent ENSEMBLE : convergences/divergences, ex. « MVRV bas mais adresses en baisse → accumulation sans conviction »). NE RE-CITE PAS les valeurs déjà dans les tuiles (MVRV, adresses, max pain, funding, DVOL sont DÉJÀ affichés juste au-dessus) — le lecteur les a sous les yeux ; ta valeur ajoutée est la SYNTHÈSE, pas la répétition. v26 : les TUILES de la grille sont construites PAR LE SYSTÈME — NE fournis PAS de champ metrics. Fonde ta lecture sur data.onchain_advanced / data.options_deribit / data.whale_inflows / data.stablecoin_supply / data.etf_flows.)"
  },
  "onchain_empty_reason": "string (REQUIS si onchain_indicators absent)",
  "sector_rotation": [{"sector","change_24h","leaders (string ex. 'DOGE PEPE')","your_holdings": ["ticker1","ticker2"]}],
  "sector_rotation_ptf_note": "string (1-2 phrases : ce que la rotation veut dire sur TON ptf. v19/M-B13 — LIS LES 3 FENÊTRES de data.sector_rotation[] : change_24h ET change_7d ET change_30d. Commente toute DIVERGENCE entre elles, ex. 'L2 rebondit à court terme (+5% 24h, +10% 7j) mais reste baissier sur 30j (−17%) : rebond technique, pas de retournement confirmé'. Ne te limite JAMAIS à la 24h seule.)",
  "news_24h": [{"category (Macro/Géopo/Catalyseur/Risque/Filtré)","tag_bg (hex)","tag_color (hex)","title","source","timestamp","confidence (ENTIER 0-100, jamais /5 ; v16 : ≤ 80 PAR DÉFAUT — 90+ réservé à un fait CERTAIN et vérifié, pas à une interprétation)","impact_on_ptf (v16 : lien d'impact sur le PTF, 3-4 LIGNES MAX, dense, pas de remplissage)","is_update (BOOL optionnel — v18/M-A10 : mets true UNIQUEMENT si c'est un VRAI complément/évolution d'une news déjà sortie un jour précédent, ex. un chiffre nouveau ou un retournement. Une news déjà couverte SANS élément nouveau ne doit PAS être re-soumise : le système dédoublonne sur 48h. Par défaut false/absent.)"}],
  "news_24h_empty_reason": "string (REQUIS si news_24h vide — RARE, voir RÈGLE 8)",
  "today_watch": "string (PROSE : 2-3 catalyseurs/risques précis à surveiller dans la journée)",
  "thesis_of_the_day": [{
     "asset","name (nom complet ex. 'The Graph')","tier_label (recopie data.eligible_theses[].tier_label, ex. 'Tier 2 · mid cap')","price_line (ex. '$0.026 · position $11.49 · +8% / 24h')",
     "action","action_type (bullish|bearish|neutral)","thesis_type (v18/Chantier F : 'tactical' OU 'conviction'. RECOPIE data.eligible_theses[].thesis_scoring.thesis_type comme base. TACTIQUE = court terme 7-30j, porté par technique + catalyseur immédiat. CONVICTION = long terme 3-12 mois, porté par fondamentaux + position sous PRU + structure W1/M1. Ces deux types s'affichent distinctement.)","confidence","size_note (OMETTRE — v33 : la taille vient du moteur)",
     "reliability (complète|partielle)",
     "signals_summary (v21 : RECOPIE le score pondéré ET la convergence depuis data.eligible_theses[].thesis_scoring — ex. 'score 9 · seuil 2 · 4 familles convergentes'. N'écris JAMAIS 'seuil non atteint' pour un actif listé dans data.eligible_theses : il EST éligible par construction.)",
     "observation (PROSE plusieurs phrases — décris CE QUI CONVERGE réellement, familles + niveaux chiffrés. INTERDIT d'affirmer qu'un seuil n'est pas atteint pour une thèse listée : c'est contradictoire avec son éligibilité.)","sources_timestamps (LAISSE LA CHAÎNE VIDE '' — la provenance et l'heure de collecte sont RÉÉCRITES en Python à partir des horodatages réels. Toute valeur que tu écris ici sera écrasée.)",
     "reasoning_signals": ["signal 1 phrasé complet","signal 2",".."],
     "historical_pattern": {"verified","narrative (PROSE détaillée si verified)","occurrences_count","avg_move_pct","max_drawdown_pct","win_rate","data_source"},
     "self_critique (PROSE plusieurs arguments)","macro_coherence (PROSE)",
     "counter_thesis (v27/TH3 — OBLIGATOIRE pour toute thèse FERME : le MEILLEUR argument CONTRE ta propre thèse, honnête et chiffré, + ce qui te ferait changer d'avis. 1-2 phrases. Ex. 'Si le funding TAO repasse durablement positif, le squeeze est déjà pricé et la cassure de 205 devient un piège haussier ; je réviserais sous 195 en clôture D1.' JAMAIS un homme de paille : le vrai risque. Sers-toi de data.cross_signals.signals.confirmation_bias si actif.)",
     "sector_rank (v27/TH6 — OPTIONNEL : si l'actif appartient à un secteur où tu détiens plusieurs positions, situe-le vs ses pairs en 1 phrase chiffrée FACTUELLE. Ex. 'TAO surperforme RENDER de +8 pts sur 30j avec un funding moins tendu'. v28 (1.B, M-A16) : AUCUNE métaphore — pas de 'meilleur cheval', 'gérant du portefeuille', 'mène la danse' ; des faits chiffrés, point. Croise data.sector_rotation + les perfs des positions du même secteur.)",
     "targets": "OMETTRE (v33 : les fourchettes publiées sont calculées par le système)",
     "watch_trigger (UNIQUEMENT si action SURVEILLER/MAINTENIR : 1 phrase, le déclencheur chiffré qui ferait passer à l'action)",
     "action_plan": "OMETTRE (v33 : aucun plan de ton cru — ni entrée, ni stop, ni take-profit, ni R:R, ni taille)"
  }],
  "thesis_empty_reason": "string (REQUIS si thesis_of_the_day vide — v26 : COURT, 1-2 phrases d'INTRO seulement, SANS puces markdown '*', SANS détail par actif — le détail va dans no_thesis_assets ci-dessous. Ex. 'Aucune convergence assez forte ce matin pour une reco ferme : on surveille, on n'agit pas dans le bruit.')",
  "no_thesis_assets": [{"asset (ticker RÉEL étudié)","real_confidence_pct (ENTIER — ta confiance RÉELLE honnête pour cet actif, FORCÉMENT < 75 puisqu'il n'est pas émis. INTERDIT d'écrire une confiance ≥ 75 ici : ce serait contradictoire avec son absence des thèses)","cap_pct (ENTIER — le plafond de complétude data.eligible_theses[].thesis_scoring.confidence_bounds.cap, si connu)","why (1 ligne : CE QUI MANQUE concrètement — catalyseur, volume, signal on-chain)","watch_level (le niveau/déclencheur CHIFFRÉ qui changerait la donne, ex. 'repli vers $58,454 ou cassure >$63,254')"}] ,
  "macro_impact": {
    "intro": "string (PROSE courte : l'impact macro du jour sur le PTF)",
    "exposed_positions": [{"asset (un actif RÉEL du PTF)","driver (le facteur macro, ex. 'DXY > 100')","effect (effet attendu CHIFFRÉ ou directionnel sur cet actif, ex. 'pression baissière, −3 à −5%')"}],
    "implication": "string (LE 'Donc' — CONCLUSION ACTIONNABLE : nomme 1-3 actifs du PTF et leur exposition concrète, ex. 'TAO et FET, les plus sensibles au risk-off si le DXY casse 100 ; BTC plus résilient'. INTERDIT : répéter l'auto-critique globale, citer une limite méthodologique, ou citer une corrélation < 0,25 (bruit). Si aucun bêta significatif, dis simplement quels actifs sont structurellement les plus exposés au régime et quoi surveiller.)"
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
    # Audit 02/10 — le modèle ne voit plus le plan V30 (asset_plan, cibles et
    # stop des projections) ni la confiance des recos héritées (vue_modele).
    data_json = json.dumps(vue_modele(data), ensure_ascii=False, indent=2, default=str)
    evening_json = json.dumps(vue_modele(evening_state), ensure_ascii=False,
                              default=str)[:4000]
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
00. v33 — LE MOTEUR D'ALLOCATION DÉCIDE (règle prioritaire, NON NÉGOCIABLE).
   Les seules recommandations du système sont dans data.opportunity :
   « recommandations » (RENFORCER décidés par le moteur déterministe) et
   « allegements_regles_profit » (règles de prise de profit d'Omar). Pour ces
   actifs, rédige l'observation, le raisonnement, la contre-thèse et les jalons.
   N'écris RENFORCER/ALLÉGER pour AUCUN autre actif ; ne donne aucune taille,
   aucune cible chiffrée et aucune probabilité de ton cru : le système les
   ignore et retire toute phrase qui prescrirait un geste non décidé. Sans
   recommandation, « aucune recommandation » est un résultat normal : explique
   le motif du moteur (data.opportunity.refus), n'en fabrique pas.
0av. v27 — RÈGLES DE FOND (analyse plus profonde, demandées par Omar) :
   • RÉGIME (ME1) : data.market_regime donne le régime BTC (bull/bear/range/
     transition) DÉTERMINISTE. Aligne l'agressivité de tes thèses dessus : en
     BEAR, privilégie la préservation et les invalidations serrées ; en BULL,
     laisse respirer les convictions ; en RANGE, joue les bornes. Ne CONTREDIS
     pas le régime sans une raison chiffrée.
   • CONTRE-THÈSE (TH3) : chaque thèse FERME DOIT porter un champ
     `counter_thesis` = le meilleur argument CONTRE (pas un homme de paille) +
     le niveau qui te ferait changer d'avis. C'est non négociable : une thèse
     sans son propre point faible est un biais de confirmation.
   • RELATIF SECTEUR (TH6) : quand tu détiens plusieurs actifs d'un même
     secteur, situe l'actif de la thèse vs ses pairs (`sector_rank`), en
     comparatif chiffré sobre (v28 : sans métaphore hippique ni figure de style).
   • VALORISATION (TH7) : exploite data.eligible_theses[].valuation.metrics
     (FDV/MC = dilution, dilution_remaining_pct, pf_ratio/ps_ratio = cher/pas
     cher vs revenus réels, mc_tvl_ratio) DANS la thèse et pour ancrer la
     cible LT — pas seulement le graphique. Cite le ratio qui compte.
   • CASH (RE1 — IMPÉRATIF) : NE traite JAMAIS le niveau de cash (même 0%)
     comme une contrainte ou un risque opérationnel. Omar peut TOUJOURS
     injecter des fonds externes. Le sizing s'exprime en % du PTF / en $
     (calculé côté Python), sans conditionner à une vente préalable. N'écris
     PAS « cash 0% = pas de poudre sèche » ni « céder X pour financer Y ».
   • PLAN V30 RETIRÉ (v33) : aucun plan par actif (invalidation, cible 30 j,
     fourchette, R:R, scénarios pondérés, EV, DCA, taille) n'est publié ni
     fourni : n'en écris aucun chiffre. Les fourchettes publiées viennent
     du système (volatilité mesurée, dérive nulle).
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
   OB1 — ALLÈGEMENTS : si data.exit_signals.available, AJOUTE une puce '✓' dédiée
   listant les positions À CONSIDÉRER POUR ALLÈGEMENT (data.exit_signals.signals :
   symbol + reason + action). C'est le CŒUR de la stratégie d'Omar (prise de profit
   par paliers +80/×2/×3, vendre la force sur les satellites qui pumpent) — ne
   l'omets JAMAIS quand available. Le cœur (BTC/ETH/TAO/LINK) n'y figure JAMAIS :
   aucun allègement n'est proposé sur lui. C'est « à considérer », pas un ordre.
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
   v26 — ON-CHAIN : les TUILES de la grille sont désormais CONSTRUITES PAR LE
   SYSTÈME (déterministes : valeur, Δ, date si donnée différée) — tu ne fournis
   PLUS de champ metrics. Ton travail = le `verdict` (positif / négatif /
   neutre) + la `combined_reading` : elle COMMENCE par la conclusion, PUIS
   explique l'implication pour la DÉCISION d'investissement (« on-chain neutre
   → pas de signal d'entrée fort, attendre confirmation prix »). Ne te contente
   JAMAIS de décrire : conclus et oriente. Fonde-toi sur les données réelles
   (data.onchain_advanced, options_deribit, whale_inflows, stablecoin_supply,
   etf_flows) et reste COHÉRENT avec elles.
   v18 (M-A22 — FRAÎCHEUR) : la note de fraîcheur des données différées est
   affichée UNE fois par le système sous la grille — ne répète PAS « données du
   23/05 » dans la lecture combinée NI dans plusieurs paragraphes ; si un
   signal repose sur une donnée différée, nuance-le simplement (« MVRV daté »).
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
   v26 — RÈGLES NEWS SUPPLÉMENTAIRES (audit v25, NON NÉGOCIABLES) :
   (f) LANGUE (A6) : le `title` de CHAQUE news est en FRANÇAIS. Traduis le titre
   original (garde les noms propres, tickers et symboles : Metaplanet, OpenAI,
   $BTC). Un mail en français avec 6 titres en anglais est un défaut d'audit.
   Ex. « Metaplanet buys 2,823 Bitcoin » → « Metaplanet achète 2 823 Bitcoin ».
   (g) DIVERSITÉ DES TAGS (A7) : la catégorie reflète la NATURE de la news, pas
   un tag par défaut. « Macro » est RÉSERVÉ aux news macro-économiques (Fed,
   taux, dollar, inflation, emploi). Un achat institutionnel crypto = Catalyseur.
   Des sorties d'ETF massives = Risque. Une restructuration de protocole = Info
   ou Catalyseur selon l'impact. 5 news sur 6 taguées « Macro » comme dans le
   mail v25 = défaut : varie selon le contenu réel.
   (h) SOURCES TELEGRAM (A8) : si data.telegram porte des FAITS CHIFFRÉS frais
   (flux ETF, achats whales, annonces), inclue au moins 1-2 cartes news
   sourcées « Telegram · <canal> · HH:MM » quand elles sont pertinentes — ces
   messages temps réel sont souvent les VRAIS catalyseurs du jour, ne les
   écarte pas au profit du seul RSS.
   v26 — CHIFFRES MACRO DATÉS (A11/B3, ZÉRO TOLÉRANCE) : tout chiffre macro
   (CPI, NFP, chômage, PCE) que tu cites est LIBELLÉ dans le temps : un chiffre
   PASSÉ se présente comme « dernier NFP +172k » ou « NFP de mai : +172k »,
   JAMAIS comme un fait du jour — surtout si la publication du jour est encore
   à venir. Quand data.upcoming_calendar fournit forecast/previous pour
   l'événement du jour, cite « consensus 114k · précédent +172k » (chiffres
   VERBATIM). Le mail v25 écrivait « NFP +172k » comme un fait actuel le matin
   même où le nouveau NFP sortait : erreur grave à ne jamais reproduire.
   v26 — ÉVÉNEMENT DU JOUR UNIQUE (A13) : le bloc « Agenda macro · 72h »
   (système, déterministe) porte SEUL l'heure + consensus + précédent des
   événements. Le MÊME événement (ex. NFP) n'est DÉVELOPPÉ qu'à UN endroit de
   ta prose (today_watch). Ailleurs (EN BREF, contexte global, thèses) :
   demi-phrase de référence maximum, SANS répéter l'horaire ni les chiffres.
   4 mentions détaillées du NFP dans le mail v25 = défaut de redite.
   v26 — VOLATILITÉ QUANTIFIÉE (A16) : quand tu annonces qu'un événement « peut
   générer de la volatilité », CHIFFRE l'attente si la donnée existe (DVOL de
   data.options_deribit, expected_move de projection.volatility) : « move
   implicite ±3,1% sur BTC en 48h (DVOL 41) ». Sans donnée : reste qualitatif
   sans inventer.
   v26 — BÊTAS LISIBLES ET PRUDENTS (A18/A19) : écris « bêta S&P 500 +2.9 »
   (JAMAIS « β-S&P500 +2.94 » qui se lit « bêta moins »). Un bêta > 2 est une
   ESTIMATION 30j instable : qualifie-le (« très sensible, estimation 30j ») au
   moins une fois, et ne cite pas deux bêtas d'indices différents pour le même
   actif dans la même section sans nommer chaque référence.
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
     v28 (W-A11) — UNITÉ OBLIGATOIRE : cite le funding UNIQUEMENT via
     funding_annualized_pct, suffixé « /an » (ex. « funding +2,4%/an »). Jamais
     le taux 8h brut : le 07/07, « +0.0022% » (matin) face à « −3,89% » (hebdo)
     rendait les deux mails incomparables pour la même donnée.
   - data.whale_inflows : gros dépôts ETH vers exchanges (pression vendeuse).
   - v26 (A3) — data.etf_flows : flux ETF STRUCTURÉS (btc/eth : total_flow_musd
     en M$, date, avg_7d_musd, source). C'est la SEULE base autorisée pour citer
     un chiffre de flux ETF ; mentionne sa date (« au 01/07 ») car c'est du J-1.
     Si data.etf_flows.available est false, AUCUN chiffre de flux ETF nulle part
     (pas même repris d'une news sans l'attribuer explicitement à cette news).
     v28 (M-A6) — RÈGLE DURCIE (violée le 07/07 : « +254,7 M$ » cité dans
     l'essentiel et le bilan on-chain alors que la source était indisponible) :
     quand data.etf_flows.available est false, un chiffre de flux vu dans une
     news ou un message Telegram ne peut apparaître QUE dans la carte news
     correspondante, TOUJOURS préfixé « selon [média] », et JAMAIS dans
     l'essentiel, le bilan on-chain, les thèses ou la macro. Partout ailleurs,
     reste qualitatif (« entrées nettes rapportées par CoinDesk »). Le footer
     liste la source indisponible : un chiffre non attribué le contredirait.
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
   - v28 (M-A18) — COHÉRENCE « RISK-OFF » : ne qualifie un événement de
     « risk-off » / « aversion au risque » QUE si les données le confirment
     (or en hausse OU VIX en hausse OU indices actions en baisse). Le 07/07,
     une news titrait « risk-off » sur Ormuz alors que l'or RECULAIT (−0,7%)
     et que le VIX restait calme — incohérence visible. Si les faits divergent
     du narratif, DIS-LE (« tensions à Ormuz mais l'or recule : le marché n'y
     croit pas ») au lieu de plaquer l'étiquette.
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
6ter. RÈGLES DE RENDU SUPPLÉMENTAIRES (v12) :
   - v23.x (SEUIL D'AFFICHAGE UNIQUE 75% — DEMANDE D'OMAR, NON NÉGOCIABLE) :
     toute thèse affichée dans thesis_of_the_day EXIGE une confiance ≥ 75%
     (v33 : SAUF les actifs décidés par le système — data.opportunity —,
     TOUJOURS rédigés, sans condition de confiance). Sous
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
     alors (v26/B2) : thesis_empty_reason = 1-2 phrases d'INTRO (sans puces) et
     le détail PAR actif va dans no_thesis_assets (STRUCTURÉ : confiance RÉELLE
     — forcément < 75, pas un « plafond » ; plafond de complétude à part ; ce
     qui manque ; niveau à surveiller chiffré). N'écris JAMAIS « confiance
     plafonnée à 80% » comme motif de non-émission : si l'actif n'est pas émis,
     c'est que ta confiance réelle est SOUS 75 — donne CE chiffre-là. Un PTF
     sans thèse à ≥75% un matin donné est NORMAL et honnête.
   - v19 (Partie 5 §3 — THÈSE MULTIDIMENSIONNELLE) : toute thèse doit intégrer
     EXPLICITEMENT les 9 DIMENSIONS suivantes (pas seulement celles qui ont
     déclenché les signaux ; cite les chiffres réels de chacune et explique sa
     contribution à la reco) : (1) MACRO (régime risk-on/off, DXY, 10Y, calendrier
     banques centrales ≤7j, corrélation actuelle au DXY/SPX), (2) NEWS & CATALYSEURS
     (événements <72h, calendrier ≤7j, narratifs émergents = data.hot_narratives
     🔥/🧊 catégories qui chauffent/refroidissent 24h — croise avec tes satellites),
     (3) TECHNIQUE (niveau
     vs supports/résistances D1 ET W1, RSI multi-TF, MA50/200, Bollinger, volume),
     (4) ON-CHAIN (MVRV, NVT, adresses actives, flux exchanges, concentration
     whales), (5) DÉRIVÉS (funding, Open Interest, put/call, max pain, skew),
     v28 (M-A8) — MAX PAIN : reprends EXCLUSIVEMENT la lecture déterministe
     fournie par le système (« aimant haussier/baissier/neutre » selon le
     signe de l'écart au spot), JAMAIS l'inverse. Le 07/07, la grille disait
     « aimant baissier » (max pain SOUS le spot) et la thèse BTC « support
     psychologique » (lecture haussière) pour le MÊME chiffre : interdit.
     Un max pain sous le spot tire le prix VERS LE BAS à court terme, point.
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
     sensible au risk-off, exposé si le S&P casse 7400 »), jamais en notation
     cryptique du type « ≥ S&P500 +2.54 ». Un humain doit comprendre sans légende.
   - (v20/A6 — BLOCS D'INVALIDATION NON REDONDANTS) : « À surveiller aujourd'hui »,
     « Ce que je surveille pour invalider mon scénario » et « Auto-critique » ne
     répètent PAS les mêmes 3-4 facteurs (DXY 101.5, S&P 7400, Fed 35%). Chacun a un
     angle DISTINCT : agenda chiffré du jour / seuils d'invalidation précis / limites
     et angles morts de l'analyse. Si un bloc n'a rien de neuf, fais-le très court.
   - (M-A17 / M-A19 / v23.x PROJECTION — ABROGÉS en v33, audit 02/10) : tu
     ne produis ni cible 30 j, ni fourchette 6-12 mois, ni plan d'action
     (entrée, stop, take-profit, R:R, taille). Les fourchettes publiées sont
     calculées par le système et les décisions par le moteur. Si l'historique
     d'un setup est à espérance négative, DIS-LE dans l'analyse : c'est une
     information, pas une décision. data.eligible_theses[].projection garde
     la volatilité et les niveaux techniques ordonnés (levels_above/below) :
     ce sont des repères d'analyse, pas des cibles.
   - (M-A20) INFLATION : si CPI/Core PCE pilotent ton régime macro, ils
     APPARAISSENT dans le contexte macro (macro_impact ou une donnée mise en
     avant), pas seulement dans l'auto-critique. Une donnée qui fonde l'analyse
     se montre.
   - (M-A22) SECTEURS : nomenclature CONSOLIDÉE et STABLE. Pas de multiples
     « Infra » (« Oracle/Infra », « Infra », « Indexing/Infra ») — un secteur =
     un nom. Un actif garde le MÊME secteur entre le matin et le weekly (GRT n'est
     pas « Indexing/Infra » le matin et « Infra » le weekly).
   - v28 (M-A9) ROTATION SECTORIELLE : ton commentaire « SUR TON PORTEFEUILLE »
     (sector_rotation_ptf_note) commente EXCLUSIVEMENT les secteurs de
     data.sector_rotation_display — ce sont les TUILES que le lecteur a sous
     les yeux. Le 07/07, le texte parlait de L1/L2/DeFi/AI pendant que les
     tuiles montraient Data/Interop/Infra : impossible de relier le texte aux
     chiffres. Si un secteur hors tuiles mérite UNE mention (mouvement
     majeur), dis explicitement « hors tuiles : … », une seule fois maximum.
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
