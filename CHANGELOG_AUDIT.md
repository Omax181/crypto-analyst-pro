# Refonte v15 — audit complet des 3 mails du 11/06 (2026-06-12)

Base : v14.1. ~80 points d'audit traités (morning / evening / weekly / transverse).
153/153 tests verts en local (130 hérités adaptés + 23 nouveaux `tests/test_v15.py`).
Rappel important : les 3 mails audités provenaient du code **main (footer v13)** —
les runs ont checkout `origin/main`, pas la branche v14.1 ; plusieurs points
étaient donc déjà corrigés en v14.1 et arrivent en prod avec ce déploiement.

## Fiabilité des données (anti-hallucination)

1. **Calendrier macro CONSOLIDÉ** — nouveau `src/data_sources/macro_calendar.py` :
   FRED (dates réelles) + Boursorama (4 URL candidates, l'unique 404ait) +
   calendriers OFFICIELS FOMC/BoJ 2026 + récurrences CPI/NFP flaguées « (estimé) ».
   Dédup par famille (une date réelle écrase une estimation). Le « Aucun événement
   macro majeur » avec un FOMC à J+6 ne peut plus se produire ; `today_watch` et la
   checklist du soir sont VERROUILLÉS sur cette liste (fin du « Balance commerciale
   14h30 » inventé).
2. **Polymarket ÉTENDU** — `get_key_markets()` : barres baisse/maintien/hausse
   agrégées sur la prochaine échéance, scénario **dominant affiché en premier**
   (« maintien 99,2 % », plus jamais « 0,1 % baisse » seul) + top 5 autres marchés
   à fort volume (récession, géopo, crypto) comme edge, croisés avec le calendrier
   dans `week_ahead` (« FOMC mercredi · Polymarket : maintien 99,2 % »).
3. **ATH réels injectés** (`ath_by_asset`, CoinGecko) — les cibles LT s'ancrent
   dessus ; colonne « vs ATH réel » dans le tableau LT hebdo. Fin du « retest ATH
   >73k » quand l'ATH BTC réel est ~108k. Interdiction prompt de « n/d » sec en LT.
4. **Cohérence chiffres** — règle « une seule valeur par métrique » (CPI 4,2 vs
   4,3 dans le même mail), cohérence FX (EUR/USD qui monte ≠ « fuite vers le
   dollar » sans explication), labels FR partout (« Peur Extrême »), un même
   chiffre d'analyse cité ≤ 2 fois (bêta STX répété 4×).

## Tracker & scoring (le chantier prioritaire)

5. **« La dernière reco prime »** — `add_recommendation` : une ré-émission MET À
   JOUR la reco ouverte (confiance, rationale, prix signal) au lieu de l'ignorer.
   Choix technique assumé : `entry_price` et `created_at` d'ORIGINE sont préservés
   (sinon la cible se ré-ancre chaque matin → win rate inatteignable, et la fenêtre
   30 j glisse à l'infini). Compteur `reissues` + `last_issued_at` pour la traçabilité.
6. **Détail de scoring 100 % Python** — `build_scoring_detail()` : UNE ligne par
   (actif, action), la plus récente prime, avec date d'entrée réelle, prix
   entrée→actuel, Δ %, jours de détention, statut (✓ ✗ ● =). Gemini ne génère PLUS
   ce tableau (cause des « 11 recos clôturées en 1 jour » à résultats contradictoires) ;
   il n'écrit que la leçon. Header et détail partagent LA MÊME source : compteurs
   recomposés depuis le détail, win rate affiché seulement à ≥ 5 clôturées (seuil
   écrit dans le mail), légende du score sous le tableau.
7. **BTC HOLD : même fenêtre que le P&L** — base = valeur PTF il y a 7 j ×
   perf BTC 7 j (libellé « [fenêtre : 7 jours] »). Fin du −4,5 % vs −1,7 %
   contradictoires. Fenêtre du P&L explicitée : « 1 773 $ (il y a 7 j) → 1 686 $ ».
8. **Sources de la semaine réelles** — `compute_weekly_source_stats()` : moyenne
   quotidienne depuis `source_health` (le « 4/23 » reflétait le seul run dominical
   à 6 sources max). Header hebdo : « 21/25 sources actives en moyenne (pic 23) ».
9. **Regret honnête** — fini « Aucune erreur coûteuse · discipline maintenue »
   sous un tableau de pertes : sans clôture, on écrit que la mesure n'existe pas
   encore. Nouveau bloc « Mon erreur de la semaine » (`my_errors`, nominatif).

## Garde-fous Python sur les recos (défense en profondeur)

10. **R:R > 8:1 → SURVEILLER automatique** (un 13:1 vient toujours d'un SL collé) ;
    SL absent / du mauvais côté / à < 1,5 % de l'entrée → même bascule, avec la
    raison AFFICHÉE dans la carte (« ⛉ Garde-fou : ... »). Badge R:R synchronisé
    (favorable seulement entre 1,5 et 8 ; > 8 = « irréaliste (SL trop serré) »).
11. **Compteur header honnête** — « X recos fermes · Y sous surveillance »
    (le « 4 thèses fondées » avec 100 % de SURVEILLER était mensonger).
12. **Mouvements PTF > ±10 % obligatoirement commentés** (`ptf_big_movers_24h`
    injecté + règle prompt) — le NOT +10,8 % ne passe plus sous silence ; le soir,
    tout mover > ±8 % reçoit un niveau dans `levels_tonight`.

## Lisibilité (sans perdre la profondeur)

13. Morning — EN BREF en 2-4 puces typées ✓/⚠/✗ ; score de risque décomposé en
    4 mini-barres (Drawdown/Concentration/Cash/Sentiment) ; Polymarket en 3
    mini-barres ; on-chain en tableau 3 colonnes (Indicateur | Valeur | Lecture) ;
    histoire du jour bornée à 5-7 lignes ; news cap STRICT à 6, triées, datées en
    FR (« hier 15:41 ») via `_fr_when`, confiance normalisée 0-100 (fin du
    « Confiance 4 % ») ; corrélations < 0,4 masquées du bloc quantitatif ;
    heatmap auto-suffisante avec 17ᵉ cellule « +N autres (moy. pondérée) » ;
    DXY sous-titré « indice large Fed (échelle ≠) » ; ordre de fin : À surveiller
    → Invalidation (liste ▸ chiffrée) → Auto-critique (puces, EN DERNIER) ;
    auto-critique macro fusionnée (plus de doublon).
14. Evening — header factuel « matin 10h14 · soir 19h32 · Δ9h » ; P&L à 2
    décimales + « journée neutre » + $ adaptatif (< 10 $ → centimes) ; mini-heatmap
    soir (3 ↑ / 3 ↓) ; ligne « International · Europe & Asie » (Stoxx/Nikkei/
    EUR-USD/USD-JPY) + ligne Polymarket dominant ; nouveau bloc « Actions à poser
    ce soir » (checklist ☐, fond sombre) ; checks « demain matin » interdits de
    répéter les niveaux de la nuit.
15. Weekly — période couverte explicite (« du 5 au 12 juin ») ; vue PTF remontée
    juste après le bilan ; calibration FUSIONNÉE dans la carte scoring ; sparkline
    SVG → mini-barres (Gmail bloque les SVG inline) ; top movers élargis à 5+5 ;
    secteurs < 1 % regroupés en « Divers » + fusion Indexing/Infra→Infra ;
    poussières unifiées < 10 $ et cantonnées à l'exit plan (jamais en watchlist) ;
    watchlist avec ≥ 1 entrée fondée ; scénarios à probabilités JUSTIFIÉES
    (somme 100, cohérents avec le dominant Polymarket, déclencheur daté) ;
    bilan CAUSAL (chaîne, pas constats) ; hausses ratées nommées ; nouveau bloc
    « Stratégie de la semaine » (consigne 3 phrases, fond sombre) ; score
    QUALITÉ PTF 4 axes 0-10 (formules transparentes) avec delta WoW (stocké
    dans les snapshots hebdo).

## Transverse

16. `APP_VERSION = "v15"` unique (email_html), injectée dans les 3 footers (le
    « v13 » en dur ne peut plus mentir). Règle cash : exception explicite
    « injection de cash externe » réservée aux opportunités exceptionnelles,
    toujours annoncée comme telle. Diagnostic : 2 checks ajoutés (« Polymarket
    étendu (v15) », « Calendrier macro consolidé (v15) » — ne doit JAMAIS être
    vide). Normalisation rétro-compatible des formats Gemini (string ↔ puces).

17. **Workflows : double-envoi corrigé À LA SOURCE** — les blocs `schedule:`
    natifs de morning/evening sont retirés du repo (cron-job.org via
    `repository_dispatch` reste l'unique déclencheur planifié ; weekly garde son
    schedule natif, il n'a pas de cron externe). Le fix n'existait que dans le
    Codespace : sans ça, écraser le code aurait réintroduit les mails en double.

## Ce qui n'a PAS pu être vérifié ici (réseau coupé chez Claude)

Aucun appel API réel n'a été exécuté pendant le chantier : les URL Boursorama
candidates, l'agrégation Polymarket étendue et le calendrier FRED réel se
valident au `python diagnostic_apis.py` du Codespace puis au test à blanc.
Les filets Python garantissent une dégradation propre dans tous les cas.

---

# Audit pré-déploiement v14 — correctifs appliqués (2026-06-10)

9 bugs confirmés corrigés + 4 fixes secondaires. 105/105 tests verts (88 d'origine + 17 régressions ajoutées).

## Bugs corrigés

1. **YouTube jamais cité (critique)** — `youtube-transcript-api>=0.6.2` installe la 1.2.4 qui a supprimé `get_transcript()` → AttributeError avalé → corpus toujours vide → source indisponible → la Règle 0 interdisait à Gemini de la citer. Fix `src/data_sources/youtube.py` : compat 0.6.x ET ≥1.0 (`fetch()` + snippets `.text`), **repli titres + descriptions** quand YouTube bloque les transcripts depuis les IP GitHub Actions (champ `mode: transcripts|titles|mixte`), `publishedAfter` RFC 3339 strict (sans microsecondes, suffixe Z), résolution de handle robuste aux accents (`HugoDécrypte`→`@hugodecrypte`, `Heu?reka`→`@heureka`).
2. **Corrélation des positions jamais calculée** — `coingecko.get_price_volume_series` renvoyait `closes`, `main.py` lisait `prices` → série toujours vide. Fix : alias `prices` == `closes` dans le retour.
3. **Crash tri des thèses** — `confidence: "72%"` (string Gemini) → TypeError sur `-conf`. Fix : `_coerce_confidence()` dans `_thesis_rank`.
4. **F&G soir : label jamais affiché + flèche manquante** — le template lisait `fear_greed_label` que Python ne produisait pas. Fix : helper `_fng_label_fr()` (Peur extrême ≤25 / Peur ≤45 / Neutre ≤55 / Avidité ≤75 / Avidité extrême) + `fear_greed_label` et `fear_greed_delta` produits dans `_macro_context` (matin + soir) + flèche `arrow24` ajoutée au BLOC 3 soir.
5. **Footer hebdo : mauvaise heure** — cron réel = dimanche 11h UTC = **12:00** Casablanca ; le footer écrivait 15:00. Corrigé.
6. **Header hebdo cassé** — Python remplissait `header["week"]` (jamais lu) ; le template lit `week_number` + année `/2026` en dur. Fix : injection `week_number`, `year`, `date`, `time_casablanca` + année dynamique dans le template.
7. **Alignement Outlook** — gainers/losers Crypto Bubbles en `float:right` (ignoré par Outlook, boîte akdital) → convertis en tables 2 colonnes `text-align:right`.
8. **Astérisques markdown bruts** — 8 champs prose du matin sans filtre `|md` (executive_summary, story narrative, macro_impact ×3, today_watch, self_critique_global, invalidation_watch). Filtre ajouté.
9. **Secrets jamais transmis** — `COINMETRICS_API_KEY` (débloque le MVRV, 403 keyless sur IP datacenter), `COINGLASS_PAID`, `KAITO_API_KEY` absents des `env:` des 3 workflows : ajouter le secret côté repo n'avait AUCUN effet. Ajoutés aux 3 workflows (secret absent = chaîne vide, sans effet).

## Fixes secondaires

- Soir : footer utilise `footer.next_report_at` calculé Python (fallback ancien libellé).
- Soir BLOC 6 : masque `< 0.01` retiré → les micro-prix (CKB ~0,0013 $) s'affichent via `fmt_money` au lieu de « — ».
- VIX formaté à 1 décimale (matin + soir) — Yahoo renvoie p.ex. 16.5400009.
- Bandeau « ⚠️ raison » dans les 3 footers quand le rapport est dégradé (`footer.note` n'était affiché nulle part : mail dégradé sans explication).

## État des sources (testées en ligne le 10/06)

- ✅ Polymarket Gamma `/markets`, Crypto Bubbles `bubbles1000.usd.json`, Farside ETF flows, The Block RSS (actif, maj < 30 min).
- ☠️ `api.unlocks.app` : mort confirmé (plus indexé nulle part) — dégradation gracieuse déjà en place, aucune alternative gratuite.
- Documentés inchangés : LunarCrush 402 (payant), CoinMetrics 403 keyless (→ ajouter `COINMETRICS_API_KEY`, désormais transmis), Binance 451 (→ repli OKX opérationnel).

## Validation

- `pytest` : **105/105**.
- Rendu (payloads riches + dégradés ×3) : matin **25,2 Ko** (<60 Ko), zéro grid/flex/float:right, zéro None/NaN/inf visibles, balises équilibrées, zéro Jinja résiduel.
- `compileall` + parse des 3 templates : OK.

---

# Audit v14.1 — 2e passe complète + international (10/06/2026)

Re-vérification intégrale post-v14 : les 9 fixes du 1er audit sont en place.
Cette passe a trouvé **7 nouveaux problèmes** (corrigés) et livré la demande
**« pas que les USA » + liens actions ↔ crypto**. 140 tests (105 → 140).

## Bugs & incohérences corrigés

1. **`_parse_num` / `fmt_money` cassaient le format français** — « 69.637,63 $ »
   (la propre sortie de `fmt_money` !) était parsé 69.637 au lieu de 69 637,63.
   Bilan recos du soir potentiellement faussé si l'IA recopiait un prix formaté.
   → Parsing locale-aware (séparateur le plus à droite = décimale) dans
   `src/main.py::_parse_num` ET `email_html.py::_fmt_money` (re-formatage
   idempotent). 11 cas de tests paramétrés.
2. **Sous-label or du mail matin : delta absolu affiché comme un %**
   (`gold_delta|fmt_pct` → « +24.0% » pour un mouvement de 24 $). Dormant tant
   que le delta venait de FRED (série or gelée), explosait avec les deltas live.
   → % réel calculé (delta/valeur×100) dans le template.
3. **Flèches 24h : valeur Yahoo live + delta FRED périmé** = tendance pouvant
   contredire la valeur affichée. → `get_macro_quotes_detailed()` fournit le
   delta vs clôture précédente (même unité), prioritaire partout (matin + soir),
   FRED en fallback. `us_10y` converti (÷10) sur valeur ET delta.
4. **`econ_calendar` (Trading Economics, HTTP 410) : module mort encore importé**
   dans main.py. → import retiré, module supprimé.
5. **`run_evening` scrapait Boursorama puis jetait le résultat** (jamais lu,
   B8 = FRED-only depuis v14). → appel réseau retiré du soir (gardé le matin,
   où il est réellement affiché).
6. **`diagnostic_apis.py` testait des sources JAMAIS utilisées par le code**
   (CryptoQuant, Coinglass avec une clé au mauvais nom) → faux ❌ systématiques
   — c'est de là que venaient la plupart des « erreurs » constatées. Et il
   ignorait la moitié de la vraie stack (Yahoo, OKX, Farside, miroir
   Coin Metrics, calendrier FRED, DefiLlama emissions). → réécrit : 22 tests
   alignés sur le pipeline réel, statut ⚠️ « dégradé-attendu » distinct des
   vrais ❌ (fallback actif ≠ erreur).
7. **Hygiène** : imports inutilisés (main, tier_resolver), f-strings sans
   placeholder (scripts) — pyflakes 0 sur src/ + scripts/ + diagnostic.

## Sources réparées / fiabilisées

8. **Coin Metrics (MVRV) — repli n°2 sans clé** : l'API community renvoie 403
   depuis les IP datacenter. Nouveau fallback : **miroir CSV GitHub officiel**
   (`raw.githubusercontent.com/coinmetrics/data`), lecture par requêtes Range
   (en-tête + 64 Ko de fin, fichiers 2,5 Mo) avec `Accept-Encoding: identity`
   (piège réel : un Range sur flux gzip est indécodable — détecté en test live).
   MVRV + realized price (dérivé prix/MVRV) + adresses actives, **datés**
   (`as_of`) et marqués `stale` si > 5 j ; le digest affiche « données au
   JJ/MM (miroir, pas temps réel) » — Règle 1 respectée. NVT absent du miroir :
   omis, jamais inventé. **Testé en live : MVRV BTC 1,41 / ETH 0,97.**
   Avec `COINMETRICS_API_KEY` (gratuite), l'API directe reprend la main.
9. **Token Unlocks — endpoint mort remplacé** : `api.unlocks.app` (404 définitif)
   → **DefiLlama `/emissions`** (gratuit, sans clé). Parsing défensif multi-noms
   de champs (tSymbol/gecko_id, nextEvent dict/liste, ts unix/ISO), mapping
   RNDR→RENDER, fenêtre 30 j, montants/% supply calculés seulement si fournis.
   Pire cas = `{available: False}` (identique à avant, zéro régression).
10. **ETF flows (Farside) fiabilisé** : requêtes via le helper http commun
    (retry 429/5xx + en-têtes navigateur — l'UA « bot » nu déclenchait des refus
    Cloudflare intermittents) + sélection de table tolérante (scan de toutes les
    tables, lignes datées) au lieu de « premier `<table>` de la page ».
11. **YouTube : quota ÷100** : `playlistItems` (1 unité) sur la playlist uploads
    (UC→UU) tenté avant `search.list` (100 unités, conservé en repli). 8 chaînes
    × 3 runs/jour passaient ~2 400 unités/jour sur les 10 000 du quota — 1re
    cause des `quotaExceeded` dans les logs. Test garantit que search n'est
    jamais payé quand la playlist répond.

## International + actions ↔ crypto (demande « pas que les USA »)

12. **FRED international** (`sources.yaml`) : `ecb_deposit_rate` (ECBDFR,
    quotidien), `boj_call_rate` (IRSTCI01JPM156N, best-effort — série OCDE),
    `nikkei` (NIKKEI225, recoupe Yahoo). Série inconnue/gelée → omise proprement.
13. **Yahoo international** : Nikkei 225 (^N225), Euro Stoxx 50 (^STOXX50E),
    DAX (^GDAXI) — valeurs + deltas live. Plages de plausibilité ajoutées
    (`_MACRO_RANGES`), pastilles de fiabilité (Nikkei recoupé Yahoo×FRED).
14. **Actions liées crypto** : NVDA, AMD, TSM, COIN, MSTR, MARA (quotes live +
    clôtures datées 3 mois). **`compute_equity_crypto_links`** : corr/β 30 j
    Python entre ces actions et les positions du PTF avec MÉCANISME causal
    (NVDA→RENDER/TAO/FET = demande GPU/IA ; COIN/MSTR/MARA→BTC = proxys cotés).
    β écarté si corrélation < 0,2 (pente sans corrélation = bruit), cap |β| ≤ 4.
15. **Mail matin** : cellule **NVDA** dans le bloc Actions US (prix + % séance +
    « lien RENDER/TAO »), **groupe 4 « International · Europe & Asie »**
    (Stoxx 50 + DAX en sous-label, Nikkei, BCE dépôt, BoJ taux — tables
    mail-safe, flèches 24h, masqué si données absentes), et ligne « Actions ↔
    crypto » (meilleure paire chiffrée). Budget < 60 Ko préservé (échantillon :
    12,3 Ko). Mail soir : structure 8 blocs INCHANGÉE, l'international et les
    actions passent à Gemini via `evening_macro` / `equity_quotes`.
16. **Prompts** : RÈGLE 12 étendue (BCE = liquidité euro, BoJ = carry trade yen
    — un relèvement BoJ est un vent contraire majeur, cf. août 2024 ; Nikkei/
    Stoxx = appétit risque avant l'ouverture US ; transmission actions→crypto
    avec chiffres reçus UNIQUEMENT, corrélation ≠ causalité, mécanisme d'abord).
    Déclinée : prompt matin §6, passe 1 régime macro, soir (B4), hebdo
    (macro_panorama), requête géopolitique (Fed/BCE/BoJ/PBoC nommées).
17. **Catalogue sources** : 23 → 25 (« Marchés internationaux », « Actions ↔
    crypto »), flags d'activation réels (jamais affichés si vides).

## Tests : 105 → 140

- 34 nouveaux (`tests/test_v141_intl.py`) : extraction Yahoo détaillée/datée,
  conversion ^TNX (valeur + delta), cache partagé quotes/deltas, actions,
  pastilles internationales, parsing miroir CSV (ligne tronquée + ligne sparse),
  realized price dérivé + stale, fallback miroir bout-en-bout, DefiLlama
  (fenêtre, mapping RNDR, schéma inattendu), 10 cas `_parse_num`, idempotence
  `fmt_money`, corr/β synthétiques (β≈2 retrouvé), liens non significatifs,
  digests, `_macro_context` (priorité deltas live + garde-fous de plage),
  rendu matin avec/sans international (zéro grid/flex, zéro None, budget 60 Ko).
- 1 test YouTube réécrit (playlist d'abord) + 1 ajouté (search jamais payé).
- `dry_run_morning_sample.html` régénéré avec les nouveaux blocs.

## Non corrigeable (inchangé, documenté)

- **Binance Futures 451** : géo-block GitHub Actions → fallback OKX déjà en
  place (le diagnostic l'affiche désormais ⚠️ « attendu », pas ❌).
- **LunarCrush 402** sur IP datacenter : dégradation propre conservée.
- Le miroir Coin Metrics peut accuser quelques jours de retard : donnée DATÉE
  affichée, jamais présentée comme temps réel.

*v14.1 — 140 tests, 3 templates mail-safe, 25 sources documentées, pyflakes 0.*
