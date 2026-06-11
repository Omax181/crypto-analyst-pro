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
