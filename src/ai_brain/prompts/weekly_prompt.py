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
  "weekly_summary": ["v16 — LISTE de 4 à 6 BULLETPOINTS explicatifs (PAS un bloc de prose). Chaque puce = une chaîne causale chiffrée de la semaine, avec les TERMES CLÉS et CHIFFRES en gras Markdown (**...**) pour accrocher l'œil. Fenêtre 7 JOURS (jamais de /24h). Ex. '**S&P 500 +2,1%** et **DXY -0,7%** → léger soutien au risque, mais **Peur Extrême (F&G 12)** persistante'"],
  "predictions_scoring": {
    "lesson": "string (PROSE : leçon de la semaine + action correctrice). v15 — les compteurs (issued/validated/invalidated/win_rate) et le tableau detail sont CALCULÉS CÔTÉ PYTHON depuis data.scoring_detail : NE LES GÉNÈRE PAS. Ta seule contribution ici est la leçon, fondée sur data.scoring_detail."
  },
  "predictions_empty_reason": "string (REQUIS si data.scoring_detail vide : ex. 'Première semaine, pas encore d historique')",
  "sector_exposure": [{"sector","ptf_pct","market_pct","color (hex)"}],
  "concentration_reading": "string (PROSE : lecture concentration + recommandation structurelle)",
  "upcoming_calendar": [{"day (ex. 'Mer 18h')","day_bg (hex)","day_color (hex)","title","impact_label (Impact élevé/moyen/Catalyseur crypto)","detail (PROSE)"}],
  "scenarios": [{"type (bearish|neutral|bullish)","label (ex. 'baissier')","probability_pct (ANCRÉ sur data.scenario_scaffold.prior — cf. RÈGLE 5 ; somme des 3 = 100)","description (PROSE DENSE & PROFONDE : croise macro+Polymarket+technique/niveaux BTC+DVOL+dérivés+sentiment+géo+calendrier daté, chiffres à l'appui, et la dérivation de la proba ; PAS de remplissage)","action (PROSE : que faire CONCRÈTEMENT sur CE PTF, positions nommées)"}],
  "strategy_focus": "string (v15 — LA stratégie de la semaine en 3 phrases MAX : le biais directionnel, la priorité n°1, la condition qui ferait tout changer. Pas un résumé : une consigne.)",
  "my_errors": "string (v15 — 1-2 phrases : LA pire erreur d'analyse de la semaine écoulée, nommée honnêtement, avec le correctif. Si vraiment aucune : ce qui a failli mal tourner.)",
  "weekly_action_plan": [{"priority (1-3)","action (concret ex. 'Si BTC < 60k → alléger TAO de 30%')","rationale (1 phrase)"}],
  "losses_vs_recos": "string — 1-3 phrases : relie les plus fortes baisses de la semaine aux recos qu'on avait émises (ex. 'ZK était en SURVEILLER lundi, -21% depuis : sortie au-dessus de 0.005 aurait évité -X%'). Honnête sur les erreurs.",
  "watchlist": [{"asset","direction (entrée/sortie)","trigger (niveau/condition précis)","rationale (1 phrase fondée)"}],
  "macro_panorama": "string — 2-3 phrases : panorama macro de la semaine à venir (Fed/CPI/NFP du calendrier réel + Polymarket + ETF flows, ET la dimension internationale si fournie : BCE, BoJ/carry trade yen, Nikkei/Stoxx — le crypto ne vit pas qu'aux USA) et son implication pour le PTF. Le fil rouge macro.",
  "exit_plan": {"subtitle","diagnosis (PROSE chiffrée)","monitoring (PROSE : comment l'agent surveille)"},
  "long_term_positioning": [{"asset","analysis (v23.x — ≤ ~90 caractères, UNE ligne terminée par un POINT, JAMAIS tronquée. C'est une ANALYSE CHIFFRÉE : le POURQUOI de la phase de cycle + le signal clé du moment. PAS une description du projet (Omar sait à quoi sert chaque crypto — décrire = remplissage inutile). Ex. '−52% sous ATH, halving digéré, dominance en hausse : zone d'accumulation du cœur.')","target_price (NOMBRE | null — objectif de prix réaliste 6-12m, ancré sur l'ATH réel/MVRV/cycle. null si aucune base chiffrable — PAS de texte type 'cible à préciser')","status (v19/V18-W1 — vocabulaire de CYCLE selon la position vs ATH : 'capitulation' si drawdown vs ATH > 75% · 'accumulation' si 50-75% · 'expansion' si <50% et en hausse · 'distribution' si proche ATH et essoufflement. N'emploie PAS 'consolide' seul pour un actif à −85% de son ATH : c'est de la capitulation.)","action (renforcer | garder | alléger | sortir — verdict cohérent avec la phase ET la conviction de l'actif)"}],
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
   v17 (W-A7) : le win rate et son SEUIL sont gérés CÔTÉ PYTHON avec une règle
   unique : il faut AU MOINS 5 recos clôturées pour afficher un taux ; en dessous
   c'est « — » + « Recos clôturées : X/5 minimum ». N'ÉCRIS JAMAIS toi-même un
   taux de réussite (« 100% (3/3) », « 67% sur la semaine »…) dans la leçon ou
   ailleurs : ce serait en contradiction avec le gate Python (l'audit a vu
   « 100% (3/3) » à côté d'un « — »). Réfère-toi au taux seulement s'il est
   affiché (≥ 5 clôturées) ; sinon parle de discipline/process, pas de
   pourcentage.
3. Vue d'ensemble portfolio : perf, drawdown, exposition sectorielle vs marché.
4. Calendrier semaine à venir (FOMC, CPI, NFP, upgrades) avec impact chiffré.
   Si calendrier vide : "données calendrier indisponibles".
5. 3 SCÉNARIOS (baissier / neutre / haussier) — ANALYSE PROFONDE & PROBABILITÉS
   ANCRÉES (v23.x · DEEPTHINK · NON NÉGOCIABLE). C'est LE cœur du hebdo : l'analyse
   sous-jacente doit être IRRÉPROCHABLE et les probabilités DÉRIVÉES de signaux
   OBJECTIFS, jamais des réflexes (INTERDIT 60/25/15, 50/40/10 par habitude ; deux
   semaines différentes ne donnent JAMAIS les mêmes %). data.scenario_scaffold te
   fournit un ÉCHAFAUDAGE DÉTERMINISTE (quand .available=true) :
     • .prior {{bearish, neutral, bullish}} = POINT DE DÉPART de tes probabilités
       (somme déjà = 100). PARS de ce prior ; ne t'en écarte qu'avec une RAISON
       explicite (catalyseur déjà pricé par les options, divergence forte, etc.) et
       reste dans le même ordre de grandeur. Justifie tout écart en une demi-phrase.
     • .factor_tilts = le biais PAR DIMENSION avec les CHIFFRES réels (Macro VIX/DXY,
       Technique MM/RSI, Sentiment F&G, Dérivés funding, Momentum) → CITE-les.
     • .implied_move_7d_pct = amplitude attendue 7j (DVOL) → borne tes mouvements ;
       .key_levels (support/résistance BTC) → définissent le RANGE neutre et les
       seuils de bascule bear/bull. .net_tilt = biais directionnel net ; .dispersion
       = largeur des queues (catalyseurs/vol ↑ → neutre ↓).
     • .event_risk.events = catalyseurs macro DATÉS ≤7j ; .polymarket = dominant Fed
       (+ %) + marchés extra ; .drivers = pistes par scénario (enrichis-les, source).
   CHAQUE scénario intègre EXPLICITEMENT, chiffres à l'appui : (1) MACRO (régime
   risk-on/off, DXY, courbe 2s10s, liquidité Fed/RRP, calendrier banques centrales),
   (2) POLYMARKET (proba Fed + événements), (3) TECHNIQUE/GRAPHIQUE (niveaux BTC
   support/résistance, MM50/200, RSI, volume), (4) VOLATILITÉ IMPLICITE (DVOL → ±X%
   sur 7j), (5) DÉRIVÉS (funding, OI), (6) SENTIMENT (F&G, contrarian aux extrêmes),
   (7) ON-CHAIN si pertinent, (8) GÉOPO / NEWS datées, (9) IMPLICATION CONCRÈTE pour
   CE PTF (positions NOMMÉES + action). Le NEUTRE = range support↔résistance + move
   implicite ; BEAR/BULL = franchissement de niveau + DÉCLENCHEUR daté (« FOMC mer. :
   si surprise hawkish → cassure 58k »). Le plus probable est COHÉRENT avec
   .net_tilt ET le dominant Polymarket. Si .available=false (données insuffisantes),
   construis les % à la main MAIS cite quand même Polymarket + calendrier + technique.
   v18 (W-A17/W-B8) : tout actif cité dans un scénario doit (a) être réellement
   dans le portefeuille et (b) être rattaché à son VRAI secteur. N'écris jamais
   « NOT, un L1/AI » : NOT (Notcoin) est Meme/Gaming. Vérifie le secteur réel
   avant de citer un actif comme représentant d'un secteur.
6. Exit plan poussières (< 10 $) : attendre spike +30%, statut par actif.
   v15 — les poussières (data.dust_positions) n'apparaissent QUE dans ce bloc :
   jamais dans la watchlist, les scénarios ou le plan d'action.
   v16 — CONFLIT THÈSE LT vs POUSSIÈRE : chaque dust_position porte un flag
   `conviction` (tier 1-2). Un actif `conviction:true` qui passe sous 10 $ est
   SOUS-PONDÉRÉ, pas une poussière à liquider : NE LE METS PAS dans l'exit plan
   des poussières, et NE le liste JAMAIS comme « à liquider sur spike +30% ».
   Si tu lui consacres une thèse long terme (long_term_positioning), il est par
   définition exclu de l'exit plan — un même actif ne peut pas être à la fois
   « conviction long terme » et « poussière condamnée ». Cohérence absolue.
   v16.1 — RÈGLE GÉNÉRALE ANTI-CONTRADICTION (vaut pour TOUS les actifs, pas un
   cas précis) : si un actif a une reco VALIDÉE ou EN COURS cette semaine dans
   data.predictions_scoring (statut validated/in_progress, ou tu viens de saluer
   sa performance), il NE PEUT PAS figurer dans l'exit plan des poussières du
   MÊME mail. C'est incohérent de féliciter une reco « RENFORCER » sur un actif
   (ex. +24% validé) puis de dire de le liquider. Choisis UNE lecture : soit
   c'est une reco active qu'on suit (alors PAS dans l'exit plan), soit c'est une
   poussière condamnée (alors PAS de reco/félicitation dessus). En cas de doute,
   exclus-le de l'exit plan : une position qui performe n'est pas une poussière.
   SIGNAL PYTHON : chaque dust_position porte un flag `active_reco` (true si
   l'actif a une reco validée/en cours cette semaine) ; si active_reco:true, ne
   le mets PAS dans l'exit plan, point.
7. Positionnement long terme (long_term_positioning) par actif Tier 0/1.
   v23.x — pour CHAQUE actif : une ANALYSE chiffrée (le POURQUOI de la phase de
   cycle + le signal clé du moment), JAMAIS une description du projet (« réserve
   de valeur », « plateforme DeFi »… = du remplissage : Omar sait déjà à quoi
   sert chaque crypto, focalise sur l'ANALYSE). data.ath_by_asset fournit l'ATH
   RÉEL et la distance : ancre tout objectif dessus (écrire « retest ATH 73k »
   quand l'ATH réel est 108k = défaut d'audit avéré). target_price = un NOMBRE
   réaliste (ATH réel, multiple, MVRV, cycle) ou null si aucune base — JAMAIS de
   texte « cible à préciser ». status = vocabulaire de CYCLE selon le drawdown vs
   ATH (capitulation >75% · accumulation 50-75% · expansion <50% en hausse ·
   distribution proche ATH). action = renforcer/garder/alléger/sortir, cohérent.
   v18 (W-B11 — COUVERTURE) : data.conviction_assets liste les actifs de
   CONVICTION (tier 0/1) réellement détenus. long_term_positioning DOIT couvrir
   CHACUN d'eux (l'audit a vu des tier-1 comme TAO, RENDER, JASMY absents). Tu
   peux ajouter d'autres positions notables. Le prix actuel, le % vs PRU, la
   conviction et la performance de la reco à 30j sont ajoutés DÉTERMINISTIQUEMENT
   au tableau (ne les fournis pas).
8. SOURCES (v16) — NE CITE AUCUN NOMBRE DE SOURCES dans sources_review.summary.
   Le compteur exact est DÉJÀ affiché dans le titre du bloc (« Sources actives
   cette semaine · X/25 »), le répéter dans la prose crée des contradictions
   (l'audit a vu « 20/25 » dans le titre et « 5 sources » dans le texte). Décris
   QUALITATIVEMENT les familles de sources exploitées (macro, on-chain,
   calendriers, Polymarket, prix) SANS chiffre. data.active_sources_count reste
   la vérité unique, gérée par le template. gaps : décris les lacunes réelles
   (ETF indisponibles, on-chain daté) sans inventer.
   v17 (W-A9) : ne qualifie PAS d'« absente » une métrique qui a en fait été
   affichée mais périmée. Si le matin a montré MVRV/NVT depuis un miroir daté
   (ex. 23/05), dis « MVRV/NVT en différé (miroir du JJ/MM), pas temps réel » —
   PAS « MVRV/NVT absents ». « Absent » est réservé à une donnée jamais obtenue.
   v18 (W-A8 — NOM UNIQUE ETF) : la source des flux ETF s'appelle « ETF flows
   (Farside) ». N'utilise PAS deux noms distincts pour la même source dans le
   même mail (l'audit a vu « Farside Investors indispo » ET « ETF flows est la
   lacune la plus récurrente » comme si c'étaient deux sources). Un seul libellé.
   v18 (Chantier E — ANALYSE TRANSVERSE) : data.cross_signals.readings fournit des
   signaux de CONTEXTE structurel (liquidité M2, cycle DXY 3-6 mois, spreads high
   yield, saisonnalité du mois, régime de volatilité réalisée du PTF, structure
   de marché D1 par actif, MVRV en perspective de cycle). Le weekly étant le
   bilan le plus profond, INTÈGRE ces signaux dans macro_panorama et le
   positionnement long terme. Cohérence obligatoire : ne décris pas un contexte
   « structurellement porteur » si M2 se contracte ET que les spreads HY
   s'écartent. Si data.cross_signals.signals.confirmation_bias est actif,
   nuance tes thèses sur les actifs signalés.
9. EXPOSITION SECTORIELLE — déjà calculée côté Python (data.sector_exposure_computed,
   poids PTF réels par secteur). Recopie-la, ne mets JAMAIS « n/d% » : si elle est
   absente, omets la section.
10. SOURCES CLÉS À EXPLOITER (P3-A5) — données factuelles fournies, à UTILISER
   dans l'analyse, pas seulement à afficher :
   - data.upcoming_calendar.events : calendrier macro CONSOLIDÉ v15 (FRED +
     Boursorama + décisions FOMC/BoJ officielles ; « (estimé) » = récurrence).
     Alimente macro_panorama + upcoming_calendar + watchlist + scénarios. Ne
     cite JAMAIS un événement absent de cette liste.
     v18 (W-A1/W-B1/W-A13 — RÈGLE ABSOLUE SUR LES JOURS) : chaque événement porte
     un champ `weekday_label` (ex. « mardi ») et `date_label` (ex. « mardi 16
     juin ») DÉJÀ CALCULÉS en Python. Tu DOIS réutiliser ces libellés tels quels.
     Tu ne calcules JAMAIS toi-même le jour de la semaine d'une date (l'audit a vu
     « BoJ (lundi) » alors que le 16 juin tombe un MARDI). N'écris jamais une date
     au format ISO « 2026-06-16 » dans une phrase : utilise `date_label`.
   - data.polymarket.fed_bars : baisse/maintien/hausse + DOMINANT → cite le
     dominant en premier. data.polymarket.extra_markets : autres probabilités
     de marché majeures (récession, géopo, crypto) — un edge à CROISER avec le
     calendrier (« FOMC mercredi, Polymarket maintien 99% → pas de catalyseur
     taux : scénario range »).
   - data.etf_flows : flux ETF BTC/ETH → sentiment institutionnel. Intègre-les
     dans le panorama et les scénarios.
   - data.scoring_detail : le tableau RÉEL des recos de la semaine (dédupliqué,
     dates, delta, statut). Ta lesson + losses_vs_recos se fondent dessus.
1bis. v16 — weekly_summary = LISTE de 4-6 BULLETPOINTS (plus de gros bloc de
   prose : illisible). Chaque puce reste CAUSALE (une chaîne, pas un constat :
   « l'inflation à 4,3% a repoussé les baisses de taux → DXY +0,7 → pression
   sur les actifs longue duration → ton bloc AI -5,4% sur la semaine »), avec
   les TERMES CLÉS et CHIFFRES en **gras** Markdown pour accrocher l'œil. La
   dernière puce donne la conséquence nette pour CE portefeuille. Fenêtre 7
   jours partout.
   v16.1 — EXPLIQUER LES FORTES VARIATIONS : pour tout actif du PTF ayant
   bougé fortement sur la semaine (≥ ±20%, cf. data.weekly_movers et
   data.ath_by_asset), tente d'en donner la RAISON en quelques mots, en
   croisant les news/catalyseurs/rotation sectorielle des données fournies
   (ex. « **NOT +36%** sur le narratif gaming/Telegram »). À défaut de
   catalyseur identifiable dans les données, dis-le honnêtement (« pas de
   catalyseur clair, rebond technique de survente »). N'invente JAMAIS une
   news : sans source, formule une hypothèse de marché, pas un fait.
11. LIEN PERTES ↔ RECOS (losses_vs_recos) : relie HONNÊTEMENT les plus fortes
   baisses de la semaine aux recos émises. Si une position en SURVEILLER/RENFORCER
   a chuté, dis-le et tire la leçon chiffrée. v15 — fais le même lien pour les
   plus fortes HAUSSES (data : top movers) : une hausse captée par une reco =
   à créditer ; une hausse ratée (aucune reco) = à nommer.
   v18 (W-A16 — PRÉCISION) : quand tu relies une perte à une reco, cite la reco
   PRÉCISE — l'actif, la date d'émission et le niveau/déclencheur d'origine
   (data.predictions_scoring porte ces champs : asset, issued_at, entry_price,
   stop_loss). Pas de « certaines de nos recos ont souffert » : nomme « RENFORCER
   TAO émis lundi à 280 → −18%, stop à 250 non franchi mais momentum cassé ».
   Vague = inutile ; précis = exploitable pour la prochaine décision.
   v19/V18-W5/W-A16 — SI AUCUNE reco n'a été émise ni clôturée cette semaine
   (data.predictions_scoring vide), DIS-LE explicitement (« Pas de reco émise
   cette semaine — pas de post-mortem de reco ») dans my_errors ET
   losses_vs_recos, au lieu de fabriquer un faux post-mortem sur la seule lecture
   marché. N'intitule PAS « pertes » une section qui ne parle que de gains
   manqués : nomme-les « coût d'opportunité », précise quelle poche du PTF a
   sous-performé et la leçon pour la semaine prochaine.
12. SCÉNARIOS COHÉRENTS AVEC LE PTF (scenarios) : chaque scénario doit dire ce
   qu'il implique CONCRÈTEMENT pour CE portefeuille (positions exposées nommées),
   pas des généralités. Et l'action proposée doit être cohérente avec la
   composition réelle (concentration L1/AI, absence de cash).
   v19/W-A17 + v23.x — Les probability_pct NE SONT PAS arbitraires : ancre-les sur
   data.scenario_scaffold.prior (cf. RÈGLE 5) et EXPLIQUE la dérivation dans la
   description (Polymarket dominant, DVOL/move implicite, net_tilt, niveaux BTC,
   calendrier). Décompose une issue conditionnelle plutôt que de la sous-estimer
   (ex. un scénario « réaction hawkish » ne peut peser 15% si Polymarket donne 99%
   de maintien sans poser P(maintien)×P(commentaire hawkish|maintien)). Somme = 100.
   Montre l'ancrage CHIFFRÉ, jamais un simple « estimées par l'IA ».
13. ALLÉGEMENTS SPÉCIFIQUES (A9) : ne dis jamais « alléger les positions exposées »
   en vague. NOMME les positions (ex. « alléger TAO : 25% du PTF, secteur AI -9%/j,
   β-DXY défavorable »), avec un argument ET un contre-argument.
14. PLAN D'ACTION SEMAINE (weekly_action_plan) : 2-4 actions concrètes,
   conditionnelles et chiffrées pour la semaine (« si X → fais Y »).
15. WATCHLIST (watchlist) : actifs à entrer/sortir avec trigger précis et raison
   FONDÉE (analysée), pas une liste au hasard. v15 — ÉQUILIBRE : vise au moins
   1 ENTRÉE fondée (niveau d'accumulation sur un actif de conviction) en plus
   des sorties ; une watchlist 100% sorties = pas une watchlist, un exit plan.
   JAMAIS de poussière (<10 $) ici. v16 — UNE SORTIE DOIT ÊTRE JUSTIFIÉE par une
   raison RÉELLE : invalidation de thèse, cassure technique majeure,
   sur-pondération à réduire. NE mets PAS un actif en « sortie » juste parce
   qu'il a peu bougé ou « pour réallouer » sans déclencheur concret (l'audit a
   vu RSR, un actif > 5 $ avec une thèse, listé en sortie sans raison fondée).
   Si tu ne sais pas POURQUOI sortir, ne le liste pas.
   v17 (T-TAO / W-A12 — COHÉRENCE) : data.firm_postures donne la posture FERME du
   dernier matin par actif. Si le matin a dit RENFORCER un actif (achat), NE le
   mets PAS en « SORTIE » dans la watchlist hebdo sans réconciliation explicite.
   Une thèse d'achat du jour et une SORTIE hebdo sur le même actif, sans
   explication, est une contradiction que l'audit a relevée (matin RENFORCER TAO
   vs weekly SORTIE TAO 280). Soit tu alignes la watchlist sur la posture
   d'achat (entrée/accumulation), soit tu expliques le changement par un fait de
   la semaine. Cohérence entre les 3 mails impérative.
   v19 (NUANCES ÉDITORIALES — à respecter) :
   • (V18-W6/V18-W7 — WATCHLIST & PLAN COHÉRENTS LT) : les triggers de watchlist
     et du plan d'action respectent le profil LONG TERME. Un « RSI < 30 D1 » ou
     « alléger si prix > X » sur un actif de CONVICTION (tier 1-2) est un réflexe
     tactique CT contradictoire : badge-le TACTIQUE, ou utilise un trigger LT
     (accumulation sous PRU, invalidation de thèse W1). N'allège PAS une conviction
     (ex. TAO) dans un scénario HAUSSIER.
   • (V18-W8 — CASH 0% = RISQUE OPÉRATIONNEL) : si la réserve cash = 0%, tire-en la
     conséquence : pas de poudre sèche → impossible de saisir une opportunité sans
     céder une position. Nomme 1 piste (ex. « céder une poussière pour libérer de
     l'optionnalité »).
   • (V18-W4 — POUSSIÈRES actionnables) : pour une poussière < 1 $ (ex. SXT 0,26 $),
     « attendre un spike +30% » n'a pas de sens (frais ≈ valeur) : recommande une
     liquidation immédiate plutôt qu'une attente passive.
   • (W-A18 — ÉVOLUTION F&G) : commente l'évolution du Fear & Greed SUR LA SEMAINE
     (« F&G 22, stable vs 24 il y a 7j » ou « 40 → 22 : sentiment qui se dégrade »),
     pas seulement sa valeur ponctuelle.
   • (W-A19 — SANTÉ PTF) : la note « Santé du portefeuille » (plus haut = mieux)
     suit EXACTEMENT la même logique que la note Santé des mails matin/soir
     (mêmes axes : diversification, momentum vs BTC, solidité). Inutile de la
     re-expliquer longuement ; le bloc dédié + sa footnote s'en chargent.
   • (W-B14 — BOUCLE D'APPRENTISSAGE) : quand l'historique le permet, tire une
     LEÇON concrète des erreurs passées (« les stops < 5% sur actifs LT ont stoppé
     out 70% du temps → relâche les stops »), pas un simple constat.
   • (V18-W10/X11 — SPÉCIALISATION vs matin) : le hebdo apporte une vue PROSPECTIVE
     (semaine à venir) ; il ne re-détaille pas le snapshot macro du matin (BCE/BoJ,
     ETF indispo) mais le met en perspective sur l'horizon hebdo.
   • (v20/W4 — CALENDRIER MACRO RÉEL) : si data.upcoming_calendar.events contient des
     événements (PCE, PMI, FOMC…), tu DOIS les nommer dans macro_panorama et le fil
     rouge. NE dis JAMAIS « absence de calendrier économique précis » quand des
     événements sont fournis — c'est faux et contredit le matin/le bot.
   • (v20/M14 — ANTI-RÉPÉTITION) : un même fait (divergence macro, proba Fed,
     concentration « 9 positions 84% ») n'est développé qu'UNE fois ; ailleurs, une
     référence courte SANS re-citer les mêmes chiffres.
   • (v20/M4 — CHIFFRE DE SOURCE INDISPONIBLE) : aucun chiffre ETF/funding/on-chain
     précis si la source est indisponible ce jour ; attribue-le à la news si c'est de
     là qu'il vient, sans le présenter comme un flux mesuré.
   • (v20/M20 — PROPRETÉ) : phrases complètes, parenthèses fermées, pas de mot répété
     collé, « se rapprocher DE » (pas « à »).
16. strategy_focus (v15) : 3 phrases MAX — biais directionnel de la semaine,
   priorité n°1, condition de bascule. C'est une CONSIGNE, pas un résumé.
17. my_errors (v16) : nomme LA pire erreur RÉELLE de la semaine (reco ratée,
   lecture macro démentie) + le correctif. INTERDIT le conditionnel d'esquive
   (« l'erreur AURAIT été… », « on aurait pu… ») : c'est une dérobade. Tu
   ÉCRIS l'erreur au passé composé / présent (« j'ai sous-estimé X », « ma
   lecture de Y était fausse »). Si la semaine est réellement propre, nomme la
   décision la plus FRAGILE prise et ce qui aurait pu la démentir — mais sans
   conditionnel mou. Jamais d'auto-félicitation. v23 — GENÈSE : si c'est la 1re
   semaine de suivi (aucune reco clôturée, data.scoring_detail ne contient que des
   positions ouvertes à 0j / historique vide), n'INVENTE PAS d'« erreur de la
   semaine passée » : dis franchement que le tracking DÉMARRE cette semaine et
   nomme plutôt le pari le plus fragile engagé cette 1re semaine.
18. v16 — COHÉRENCE DES FENÊTRES dans weekly_summary et tout le mail hebdo :
   le bilan est HEBDOMADAIRE → utilise des chiffres 7 JOURS. N'INJECTE JAMAIS
   un « / 24h » dans le bilan hebdo (l'audit a vu « -1,27% / 24h » dans le
   bilan de la semaine : incohérent). Les perfs sectorielles et de positions
   citées dans le résumé sont sur 7 jours. Si tu n'as qu'une donnée 24h pour un
   point, soit tu l'omets, soit tu le dis explicitement (« sur la séance »),
   mais le fil conducteur du bilan reste la semaine.
   v17 (T-7J / W-A2 — CHIFFRE 7j UNIQUE, IMPÉRATIF) : la performance 7j du PTF et
   le vs BTC 7j sont des FAITS Python (data.portfolio_snapshot.change_7d_pct et
   .vs_btc_7d_pct, déjà affichés en KPI). Quand tu cites la perf hebdo du PTF
   dans weekly_summary ou ailleurs, tu REPRENDS EXACTEMENT ces chiffres — JAMAIS
   un autre couple recalculé par toi. L'audit a vu 3 valeurs différentes pour la
   même semaine (+13,9% / +12,7% / +11,21%) : c'est interdit. Un seul chiffre 7j
   PTF dans tout le mail, celui de data.portfolio_snapshot.
   v18 (W-A18 — F&G UNIQUE) : l'indice Fear & Greed est une donnée Python unique
   (data.fear_greed). Cite EXACTEMENT cette valeur partout dans le mail (bilan,
   scénarios, divergence sentiment). L'audit a vu « F&G 13 » à un endroit et
   « F&G 18 » à un autre dans le MÊME rapport : interdit. Une seule valeur F&G.

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_WEEKLY_SCHEMA}
"""
