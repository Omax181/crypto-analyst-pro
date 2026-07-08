"""Constructeur du prompt pour le rapport du soir (différentiel).

Le soir COMPLÈTE le matin : il ne répète pas macro/on-chain/rotation déjà
couverts. Il lit le rapport du matin (mémoire) et produit les deltas.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_CONTRACT,
)

_EVENING_SCHEMA = """
{
  "header": {"date","time_casablanca","hours_since_morning (int — déjà fourni dans data, recopie-le)"},
  "portfolio_snapshot": {"value_usd","change_since_morning_pct"},
  "delta_summary": [{"icon ('✓'|'⚠'|'✗')","text (1-2 phrases, dense, chiffré : une chose à retenir + sa CONSÉQUENCE concrète, pas un constat vague)"}],
  "market_changes": [{"status (invalidated|confirmed|unchanged|new)","tag (MÊME logique que le matin : 'Catalyseur'|'Risque'|'Macro'|'Géopo'|'Info' — sert à colorer)","importance (1-5 ; un mouvement anecdotique comme +2,7 points de base sur un taux = 1-2, PAS 'NOUVEAU' majeur)","description (1-2 phrases, le DELTA vs ce matin uniquement)","source (nom réel de la source ; ajoute une heure UNIQUEMENT si c'est l'horodatage PROPRE de la donnée, ex. 'Financial Times 12h48'. v21 (E11) : n'ajoute JAMAIS l'heure d'envoi du rapport (~20h Casablanca), identique sur toutes les lignes — elle se répétait 6× pour rien)"}],
  "news_today": [{"title (titre court)","source (nom réel)","time (ex. '12h48')","impact (1 phrase : effet sur le PTF/marché)","status (intégré|actionnable)"}],
  "levels_tonight": [{"asset (BTC/ETH/DXY/… )","level (niveau PRÉCIS ex. '63 000 $')","type (support|resistance|critical|threshold)","trigger (ce qui se passe si cassé/atteint, ACTIONNABLE ex. 'sous 62k → alléger, capitulation probable')"}],
  "actions_tonight": [{"action (le geste précis et CHIFFRÉ, ex. 'Alléger 10% de TAO à 270$')","rationale (POURQUOI, chiffré et technique : RSI/résistance/divergence/surchauffe, ex. 'RSI 4h à 78 (surchauffe), butée sur résistance 272$ jamais cassée en 3 tentatives')","rebuy (SI c'est un allègement sur un actif de CONVICTION LT : le niveau de RACHAT visé + sa logique, ex. 'racheter vers 235-240$ après retour RSI < 55 / test support')","horizon (precise si l'action est tactique sur une position LT — ex. 'geste tactique, la thèse LT TAO reste intacte')"}],
  "tomorrow_checklist": {
    "calendar": "string — événements macro réels des 48h (RECOPIE data.tomorrow_macro_events). Si vide : 'Pas d'événement macro majeur dans les 48h.'",
    "checks": "string — 2-3 vérifs CONCRÈTES liées aux mouvements/recos du jour (ex. 'IMX tient son +12% overnight ? · DXY reste sous 100 ?'). Pas de généralité.",
    "scenario": "string — 1 phrase TRANCHÉE : le scénario le plus probable + sa condition (ex. 'consolidation 63k-65k si DXY < 100'). Jamais 'ça dépend'.",
    "invalidation": "string — 1 condition CHIFFRÉE qui ferait basculer l'analyse (ex. 'BTC sous 62k + VIX > 25 = risk-off confirmé'). Cohérente avec levels_tonight."
  },
  "blind_spots": "string — 1 phrase MAX si un angle mort est critique (ex. flux ETF indisponibles), sinon chaîne vide.",
  "footer": {"next_morning_time (ex. '08h30')"}
}
"""


def build_evening_prompt(
    *, timestamp: str, data: dict[str, Any], morning_state: dict[str, Any]
) -> str:
    """Construit le prompt du rapport du soir.

    Args:
        timestamp: horodatage Casablanca.
        data: données collectées (légères : deltas, prix, news <24h).
        morning_state: contenu du rapport du matin (référence obligatoire).

    Returns:
        Prompt complet pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    morning_json = json.dumps(morning_state, ensure_ascii=False, default=str)[:6000]
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT DU SOIR · complément différentiel du matin.

RAPPORT DU MATIN (référence obligatoire, RÈGLE 7 — NE PAS le répéter) :
{morning_json}

DONNÉES DU SOIR (deltas depuis le matin) :
{data_json}

INSTRUCTIONS :
0. RÈGLE DES CHIFFRES (CRITIQUE). Tout nombre (prix, %, niveau, delta) doit être
   copié VERBATIM depuis le JSON fourni — jamais calculé, extrapolé, mémorisé
   d'ailleurs, ni inventé. Donnée absente = "n/d" ou description sans chiffre. Un
   prix faux affiché en confiance est l'erreur la plus grave de ce rapport.
1. delta_summary (« À retenir aujourd'hui ») : 3 à 4 PUCES TYPÉES, objet
   {{icon, text}}. icon '✓' = positif/confirmé, '⚠' = vigilance, '✗' = risque
   avéré. RÈGLE STRICTE v16.1 : CHAQUE puce DOIT avoir un icon '✓', '⚠' ou '✗' —
   AUCUNE puce sans icon, AUCUN autre symbole (pas de '→', pas de '•'). Si une
   puce est neutre, choisis '⚠' (vigilance) par défaut. Chaque puce = une chose à
   retenir AVEC sa conséquence concrète et chiffrée — pas un constat en l'air
   (« sentiment stable » seul ne sert à rien ; « F&G 12 inchangé → prudence
   maintenue malgré les actions en hausse » est utile). PAS de paragraphe.
   v28 (E-A6) — ne RÉPÈTE PAS la valeur F&G dans une puce si elle est
   inchangée depuis le matin (elle est déjà dans la grille marchés) : une puce
   F&G n'est justifiée que si l'indice a bougé de plus de 3 points.
2. market_changes (« Ce qui a évolué côté marché ») : 4 à 6 items MAX. Chaque
   item = un statut (invalidated ✗ / confirmed ✓ / unchanged → / new ↑) + un
   `tag` (Catalyseur/Risque/Macro/Géopo/Info, comme le matin) + une `importance`
   1-5 + 1-2 phrases décrivant UNIQUEMENT le DELTA vs ce matin + la source réelle
   avec son heure. v16 — ANTI SUR-EMPHASE : un micro-mouvement (ex. un taux
   directeur qui passe de 0,700 % à 0,727 %, soit +2,7 points de base) est
   ANECDOTIQUE : importance 1-2, statut « unchanged » ou une simple mention,
   JAMAIS « ↑ NOUVEAU » majeur. Réserve « new » à un vrai changement de régime.
   Inclus les évolutions marché autonomes (DXY, ETF, divergence indices) qui
   sont de la valeur ajoutée — v14.1 : dont l'INTERNATIONAL (clôtures
   Nikkei/Stoxx, BCE/BoJ via data.evening_macro) et les actions liées crypto en
   séance (data.equity_quotes : NVDA pour le bloc IA RENDER/TAO/FET,
   COIN/MSTR/MARA comme proxys BTC) quand le mouvement est significatif et CHANGE
   la lecture du matin. Le régime macro du matin (macro_regime_readout du RAPPORT
   DU MATIN) : s'il a bougé, mets-le ICI en 1 ligne. Pas de bloc régime séparé.
   v17 (E-A9) : ces évolutions sont de VRAIS DELTAS DEPUIS LE MATIN, pas les
   chiffres 24h/hebdo déjà au matin. Si VIX −1,76, Nikkei +1802, Stoxx +130
   étaient déjà dans le rapport du matin, NE les reprends PAS comme nouveaux.
   Séance calme = section COURTE, c'est honnête ; ne meuble pas.
2bis. v19/WS3 — FENÊTRE DÉGÉNÉRÉE : si data.run_window.degenerate est true, le
   Morning a tourné il y a moins de 4h (run de rattrapage / hors-cycle). La
   fenêtre matin→soir est alors INSIGNIFIANTE : il n'y a normalement RIEN à
   reporter en market_changes ni news_today, et le P&L / F&G « depuis ce matin »
   sont triviaux (~0). DIS-LE explicitement (ex. « run de rattrapage à
   data.run_window.minutes_since_morning min du matin : pas d'évolution
   significative depuis le dernier rapport ») au lieu d'inventer des deltas ou de
   commenter un mouvement nul comme un signal. N'écris JAMAIS « inchangé depuis
   ce matin » sur 3 minutes comme si c'était une information.
   v23 (E1) — INTERDICTION DE CONTRADICTION : en fenêtre dégénérée, tu ne peux PAS
   à la fois dire « INCHANGÉ / rien depuis X min » ET marquer des items « ↑ NOUVEAU ».
   Les données 24h (Nikkei, BCE/BoJ, COIN/MARA…) ne sont PAS « NOUVEAU » : elles
   étaient déjà dans le rapport du matin. Si rien n'a bougé depuis le matin,
   market_changes ne contient AUCUN item « NOUVEAU » — au plus 1 item « INCHANGÉ »
   qui le constate. « NOUVEAU » est réservé à un événement RÉELLEMENT survenu après
   le matin.
3. news_today (« Ce qui est tombé depuis ce matin ») : 3 à 5 news MAX, ultra
   compactes. Chaque news = titre court + source réelle + heure + 1 phrase
   d'impact + statut (intégré / actionnable). UNIQUEMENT les news qui CHANGENT
   quelque chose vs le matin. Ne répète pas une news déjà couverte dans
   market_changes. Pas de % de confiance (inutile, le matin a déjà trié).
   v24 — ZÉRO news SANS IMPACT crypto/PTF : si l'impact se résume à « aucun impact
   direct » (ex. dividende d'un ETF obligataire, résultat sportif, actu people),
   NE L'AFFICHE PAS DU TOUT — ne la liste pas juste pour dire qu'elle est neutre.
   Mieux vaut 2 news pertinentes que 5 dont une creuse. L'audit a vu « Global X
   Zero Coupon Bond ETF declares dividend — pas d'impact direct » : à bannir.
   v18 (E-B3) : « depuis ce matin » est LITTÉRAL — n'inclus QUE des news dont
   l'heure est POSTÉRIEURE au rapport du matin (~08h30 Casablanca). Une news de
   la nuit ou d'avant le matin a déjà été vue : elle n'a rien à faire ici. Si une
   news n'a pas d'heure claire postérieure au matin, ne la mets pas.
   v17 (E-B2 — anti-doublon) : « Ce qui a évolué » (market_changes) et « Ce qui
   est tombé » (news_today) ne doivent PAS se recouvrir. Règle de partage :
   market_changes = MOUVEMENTS DE MARCHÉ/PRIX/indices/taux ; news_today =
   ÉVÉNEMENTS/ANNONCES (réglementaire, géopo, projet, listing). Une même
   information n'apparaît que dans UNE des deux sections, en 1 ligne. Si tu n'as
   rien de neuf pour l'une, laisse-la courte ou vide plutôt que de recopier
   l'autre.
   v19 (NUANCES ÉDITORIALES — à respecter) :
   • (E-A15/V18-E4 — TIMING FOMC) : situe les événements par rapport à L'HEURE
     RÉELLE du mail. La décision FOMC tombe ~19:00 Casablanca : si le mail part à
     17:36, écris « FOMC dans ~1h30 », pas « ce soir » vague ni « aujourd'hui »
     ambigu. Si l'événement est PASSÉ, parle au passé (« la Fed a maintenu… »).
   • (E-B6 — CFX niveaux vs action) : un actif qui a déjà une ACTION ce soir ne
     doit PAS aussi figurer dans « niveaux à surveiller » comme s'il n'avait pas
     de plan — choisis l'un, OU explicite le lien (« rachat après allègement »).
   • (V18-E1 — ACTION SANS THÈSE MATIN) : si tu proposes une action que le matin
     n'avait pas anticipée, badge-la explicitement « TACTIQUE court terme » et
     précise que la conviction LT reste inchangée. Pas de trading déguisé en
     conviction pour un investisseur LT.
   • (V18-E12/X4 — DIAGNOSTIC COHÉRENT avec le matin) : ton diagnostic d'un actif
     doit être COHÉRENT avec celui du matin du jour. Si le matin a dit « rotation
     L2/Interop » sur CFX, ne dis pas le soir « spike sans catalyseur » — confirme,
     ou dis « le narratif L2 du matin ne s'est pas confirmé ».
   • (V18-E6 — FLAG « actionnable ») : ne tague « actionnable » qu'une news qui
     débouche sur une action précise NOMMÉE. Une news sans lien avec tes positions
     n'est PAS actionnable.
   • (X9 — ANTI-REDONDANCE macro) : une info macro déjà détaillée le matin (ex.
     FOMC 99,8%) se RAPPELLE en 1 ligne le soir, sans la re-détailler.
   • (v20/M4 — CHIFFRE DE SOURCE INDISPONIBLE) : aucun chiffre ETF/funding/on-chain
     précis si la source est indisponible ce jour ; attribue-le à la news si c'est de
     là qu'il vient, sans le présenter comme un flux mesuré.
   • (v20/M21 — NEWS > 12h = CONTEXTE) : une news de plus de 12h ne « tombe » pas
     ce soir ; date-la et traite-la comme contexte, pas comme nouveauté de séance.
   • (v20/M20 — PROPRETÉ) : phrases complètes, parenthèses fermées, pas de mot répété
     collé, « se rapprocher DE » (pas « à »).
   v17 — COHÉRENCE PRIX (E-A3, IMPÉRATIF, ZÉRO HALLUCINATION) : tout prix cité
   dans un titre/une analyse de news DOIT être cohérent avec le spot réel
   (data.evening_macro.btc_price). Si un titre dit « BTC stagne à 77K » alors que
   le spot est 64,6K (écart ~19%), NE reprends PAS ce prix — soit tu l'ignores,
   soit tu signales explicitement que le chiffre du titre est incohérent avec le
   spot. JAMAIS afficher un prix de news qui contredit le marché sans le dire.
   Fais le CHECK avant d'écrire.
   v17 (E-A7) : si tu mentionnes un prix d'ENTRÉE pour un actif sous reco ferme,
   il vient de morning_state.firm_postures[ACTIF].entry (la posture du matin) —
   ne réinvente pas une entrée différente. Le soir et le weekly doivent citer la
   MÊME entrée que le matin pour le même actif (l'audit a vu BTC entrée 64.109 le
   soir vs 63.739 au weekly : interdit).
4. levels_tonight (« Niveaux à surveiller cette nuit ») — bloc le PLUS
   actionnable : 4 à 8 niveaux PRÉCIS. Inclus OBLIGATOIREMENT BTC (≥1 support +
   ≥1 résistance), ETH (idem) et DXY. AJOUTE chaque position de
   data.big_movers_day (mouvement > ±8% aujourd'hui) avec un niveau de
   TP/résistance ou de protection — un +12% du jour SANS niveau le soir =
   défaut d'audit avéré. Pour chaque niveau : type (support/resistance/
   critical/threshold) + trigger ACTIONNABLE (« sous 62k → alléger »), jamais
   « à surveiller ». Niveaux ancrés techniquement (supports testés, Fibonacci,
   max pain), pas de ronds arbitraires.
   v18 (E-B6) : une position qui PÈSE significativement dans le PTF (≳ 1% du
   portefeuille) et qui a bougé mérite un niveau, MÊME sans reco active dessus —
   c'est du capital exposé qu'Omar doit pouvoir surveiller. Ne réserve pas les
   niveaux aux seuls actifs sous reco.
   v26 (E-B5 — NIVEAUX CALCULÉS = SOURCE DE VÉRITÉ, IMPÉRATIF) :
   data.computed_levels[SYM] fournit pour BTC/ETH (et les gros movers) les
   supports/résistances CALCULÉS depuis la série de prix réelle (pivots de
   swing, MM50/100/200, retracements Fibonacci, bandes de Bollinger, seuils
   ronds), chacun avec sa base et sa distance au prix, PLUS un readout
   technique (RSI, MACD, Bollinger, ATR, tendance, volume) et le range attendu
   ±ATR (expected_range). RÈGLES : (a) tes levels_tonight pour ces actifs sont
   CHOISIS PARMI ces niveaux — recopie les valeurs telles quelles ; (b)
   INTERDIT d'inventer un niveau qui n'y figure pas (fini les ronds arbitraires
   « 59 000 / 61 000 » sans ancrage) ; (c) enrichis le trigger avec le readout
   quand il éclaire la décision (ex. « RSI 72 en surchauffe sous la résistance
   62 126 $ → prise de profit partielle défendable ») ; (d) le scénario/
   l'invalidation de la checklist s'appuient sur expected_range et le premier
   support. Exception : DXY et actifs absents de computed_levels — analyse
   classique depuis le contexte macro, prudence sur les chiffres.
4bis. actions_tonight (v16.1) : 0 à 3 actions à POSER ce soir, objet structuré
   {{action, rationale, rebuy, horizon}}. C'est LA section qui découle de toute
   l'analyse — Omar s'en sert pour décider, donc elle doit être DENSE en chiffres
   et IRRÉPROCHABLE (zéro hallucination, chaque chiffre dérivé d'une analyse
   réelle : RSI, résistance/support testés, divergence, max pain, ATR). Chaque
   action :
   - action : le geste précis et chiffré (« Alléger 10% de TAO à 270$ »,
     « Placer un ordre d'achat ETH à 1 600$ »).
   - rationale : POURQUOI, justifié techniquement et chiffré (« RSI 4h à 78 =
     surchauffe + butée sur résistance 272$ non franchie en 3 tentatives sur 24h »).
     JAMAIS « pour sécuriser » seul : explique le signal technique.
   - rebuy : CRITIQUE — Omar est INVESTISSEUR LONG TERME. Si l'action est un
     allègement sur une position de CONVICTION (Tier 1-2, ou un actif avec une
     thèse LT comme BTC/ETH/TAO/RENDER), tu DOIS donner le niveau de RACHAT visé
     et sa logique (« racheter 235-240$ après retour RSI < 55 / test du support
     hebdo »). Un allègement sans plan de rachat sur un actif de conviction = NON
     conforme à sa stratégie. Si c'est une vraie sortie définitive (faible
     conviction), dis-le et rebuy peut rester vide.
   - horizon : précise que le geste est TACTIQUE et que la thèse LT reste intacte
     (« geste tactique court terme, conviction LT TAO inchangée »).
   Ne propose un allègement QUE s'il est techniquement justifié : pas de vente
   gratuite. Si rien ne justifie une action chiffrée, renvoie une liste VIDE
   (ne meuble pas). Ne répète pas un simple niveau de levels_tonight — une action
   = un geste à exécuter avec sa justification complète.
   v18 (E-A3 — DÉFINITION « POUSSIÈRE », IMPÉRATIF) : une position est une
   « poussière » si sa VALEUR TOTALE dans le portefeuille (quantité × prix) est
   < 10 $ — JAMAIS si son prix UNITAIRE est faible. JASMY à 0,005 $ l'unité mais
   ~72 $ de valeur N'EST PAS une poussière. Chaque actif du contexte porte sa
   valeur de position (value_usd) : fie-toi à ELLE pour qualifier une poussière,
   pas au prix unitaire. Ne justifie JAMAIS un allègement par « optimiser la
   liquidité d'une poussière » si la valeur de position est ≥ 10 $.
   v17 (T-TAO / E-A1 — COHÉRENCE AVEC LE MATIN, IMPÉRATIF) : morning_state.firm_postures
   donne la posture FERME du matin par actif (RENFORCER / ALLÉGER + entrée + SL).
   Tes actions du soir NE DOIVENT PAS contredire frontalement cette posture sans
   l'expliciter. Si le matin a dit RENFORCER TAO (achat, entrée ~260) et que tu
   proposes d'alléger TAO le soir, c'est soit (a) un SCALP tactique court terme
   sur un rebond — alors DIS-LE explicitement dans horizon (« prise de profit
   tactique sur le rebond vers 270, la thèse d'achat LT du matin à 260 reste
   valide, rachat visé 245-250 ») et donne un rebuy cohérent avec l'entrée du
   matin ; soit (b) un vrai changement d'avis justifié par un fait nouveau du
   soir — alors explique CE fait. INTERDIT : recommander sèchement « ALLÉGER TAO »
   le soir comme si le matin n'avait pas dit « RENFORCER », sans réconciliation.
   Omar lit les deux mails : ils doivent raconter une histoire cohérente.
5. tomorrow_checklist (« Demain matin ») — objet à 4 champs :
   - calendar : RECOPIE EXCLUSIVEMENT data.tomorrow_macro_events (v16 : déjà
     FILTRÉ aux 2 prochains jours calendaires — le weekly couvre la semaine).
     N'invente AUCUN événement. v16 — TEMPORALITÉ CRITIQUE : ces événements sont
     les plus proches dans les 48h, chacun avec son `when` réel (« demain »,
     « dans 2j »). Ne présente JAMAIS un événement à J+4 comme « demain ». Si la
     liste est vide → « Pas d'événement macro majeur dans les 48h. » (un FOMC à
     J+5 n'a RIEN à faire dans la checklist du soir : il est dans le weekly).
     v18 (E-A15) : la date de chaque événement est affichée UNE SEULE FOIS par le
     rendu (badge « when » + libellé propre). Dans `checks` et `scenario`, NE
     RÉPÈTE PAS la date du même événement (« BoJ dans 2j » puis « décision dans 2j »
     puis « 2026-06-16 ») : réfère-toi à l'événement par son nom, sans re-dater.
   - checks : 2-3 vérifs CONCRÈTES dérivées du jour (position >8% → persistance
     overnight ; seuil macro → tient-il ?). Pas de généralité. v15 — INTERDIT
     de répéter un niveau déjà listé dans levels_tonight : si « S&P < 7 200 »
     est un level, le check porte sur AUTRE chose (volume, flux ETF, suivi
     d'une reco) ; sinon supprime le check (2 suffisent).
     v18 (E-B8) : la checklist du soir doit rester COURTE — 4 lignes MAX au total
     (1 calendar + 2 checks + 1 scénario, l'invalidation tenant sur la même ligne
     que le scénario si possible). Au-delà, c'est du bruit : coupe les checks les
     moins actionnables. Une checklist dense de 4 lignes vaut mieux qu'une liste
     de 8 lignes diluée.
   - scenario : 1 phrase TRANCHÉE (scénario probable + condition). Jamais « ça
     dépend » ni « consolidation dans un contexte incertain ».
   - invalidation : 1 condition CHIFFRÉE, cohérente avec levels_tonight.
5bis. POLYMARKET (v16) : data.polymarket.fed_bars (baisse/maintien/hausse +
   dominant). Si tu cites Polymarket pour la Fed, cite le DOMINANT en premier.
   GÉNÉRALISE les extra_markets : data.polymarket.extra_markets contient des
   probabilités de marché sur des événements majeurs (récession, géopolitique,
   crypto). Quand l'un d'eux éclaire une news ou un mouvement, INTÈGRE sa
   probabilité directement, idéalement entre parenthèses à droite du titre
   concerné (ex. « Tensions US-Iran (accord de paix : 17% sur Polymarket) »).
   N'utilise QUE les marchés réellement fournis — n'invente aucune probabilité.
6. blind_spots : 1 phrase MAX si un angle mort est critique (ex. flux ETF
   indisponibles), sinon chaîne vide. Si MVRV/on-chain CoinMetrics manque, NE le
   répète pas en boucle (1 mention max).
7. NE PRODUIS PAS de bilan des recos : il est calculé par Python (data fourni en
   aval) et rendu automatiquement, 1 ligne par actif. N'émets donc AUCUN champ
   reco_evolution / reco bilan dans ton JSON.
8. RÈGLE CASH : le portefeuille est 100% crypto, ZÉRO USDC. N'écris jamais
   « rester liquide en USDC », « renforcer USDC » ni « déployer du cash ».
   v27 (RE1) : le cash n'est JAMAIS une contrainte — Omar peut injecter des
   fonds externes à tout moment. NE conditionne PAS une entrée à l'allègement
   d'une autre position (« financer en vendant X » = interdit) ; un allègement
   ne se propose que s'il est justifié par la thèse de l'actif allégé.
9. NOMS DE SOURCES — libellé public TOUJOURS : « CoinGecko » (pas prices_now),
   « Fear & Greed Index » (pas fear_greed), « Yahoo Finance » (pas evening_macro),
   « Farside Investors » (pas etf_flows), « Rapport matin » (pas morning_report).
   Aucun identifiant Python dans le texte rendu.
10. PAS DE SOURCE PLACEHOLDER. Une mention « Source · X » exige un nom RÉEL et un
   horodatage DISTINCT du moment du rapport (pas tout à 20h00). Sinon, omets la
   source entièrement plutôt que d'en inventer une.
NE répète PAS le contexte macro/on-chain/rotation déjà donné le matin.
Le mail tombe à 20h Casablanca = 14h US = MI-SÉANCE américaine (pas la clôture).

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_EVENING_SCHEMA}
"""
