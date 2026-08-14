# Déploiement V31 — Crypto Analyst Pro

## En une commande

Dans le Codespace, **à la racine du dépôt** :

```bash
unzip -o cap-v31-DEPLOY.zip && bash deploy_v31.sh
```

Si `unzip` manque, Python fait aussi bien :

```bash
python3 -c "import zipfile;zipfile.ZipFile('cap-v31-DEPLOY.zip').extractall('.')" && bash deploy_v31.sh
```

Le script fait tout : mise à jour du checkout, branche de secours, sauvegarde
de l'état vivant, extraction, purge des anciens chemins, restauration,
dépendances, tests, invariants, migration. Il s'arrête à la première anomalie
plutôt que de laisser un dépôt à moitié déployé.

Pour committer et pousser dans la foulée :

```bash
bash deploy_v31.sh --push
```

Sans `--push`, le script affiche les trois commandes à jouer. C'est le
comportement par défaut : on relit `git diff` avant de pousser en production.

---

## Ce que le script protège, et pourquoi

**`state/`** — le carnet de contrats, le registre des sources, la mémoire du
bot. Il est commité par les workflows après chaque run, jamais depuis le
Codespace. Il est sauvegardé avant extraction et restauré après ; le ZIP ne
peut pas l'écraser, une assertion le vérifie.

**`config/portfolio.yaml`** — quantités et PRU. Réécrit en production par
`scripts/update_portfolio.py`, par le bot Telegram et par `portfolio_loader`,
puis commité par `update_portfolio.yml` (`git add config/`). Le ZIP en contient
une copie, mais c'est la **baseline de livraison** : elle est identique octet
pour octet depuis la v29. L'extraire par-dessus la production remplacerait le
portefeuille réel par un instantané vieux de trois versions. Il est donc
sauvegardé et restauré comme `state/`, puis **relu par le chargeur V31** — on
ne suppose pas qu'il est lisible, on le lit.

**Le checkout lui-même** — le script se met à jour en fast-forward avant de
toucher à quoi que ce soit. Un Codespace ouvert la veille porte un `state/` en
retard sur celui qu'Actions a commité depuis ; déployer par-dessus le ferait
régresser. En cas de divergence réelle (commits locaux **et** distants), le
script refuse et le dit.

---

## Ce que le script supprime, et pourquoi c'est nécessaire

V31 est une refonte du noyau, pas un correctif : **62 modules et 54 fichiers de
tests disparaissent**. Une extraction seule les laisserait en place. Les
54 anciens tests seraient collectés par pytest et feraient échouer une suite
pourtant saine ; les anciens modules laisseraient survivre une double logique
que V31 a précisément éliminée.

Le script supprime donc, dans `src/`, `tests/`, `scripts/` et
`.github/workflows/`, **tout fichier absent du livrable** — dont
`heartbeat.yml`, remplacé par `watchdog.yml`. Les fichiers racine suivis par
git et absents du livrable (`diagnostic_apis.py`, `generate_telegram_session.py`,
`run_tests_local.py`) partent aussi : ils importent des modules qui n'existent
plus. Tout ce qui est **hors** de ces arbres est signalé mais jamais supprimé.

---

## Les douze étapes

| | Étape | Bloquant |
|---|---|---|
| 1 | dépôt git, ZIP présent, Python ≥ 3.11, **md5 du ZIP**, dépôt propre | oui |
| 2 | mise à jour fast-forward du checkout | oui si divergence |
| 3 | branche de secours `backup-pre-v31-…` depuis `origin/<branche>` | non |
| 4 | sauvegarde de `state/` et `config/portfolio.yaml` | oui |
| 5 | extraction (chemins relatifs, 111 entrées, `state/` intouché) | oui |
| 6 | purge des anciens chemins | oui |
| 7 | restauration de l'état vivant | oui |
| 8 | dépendances + relecture de `portfolio.yaml` et du carnet par V31 | oui |
| 9 | `pytest tests -q` — **263 tests minimum** | oui |
| 10 | invariants V31 (ci-dessous) | oui |
| 11 | migration `historical_treatment: purge` | oui |
| 12 | pyflakes, compileall, liste des secrets | non pour pyflakes |

Le contrôle md5 mérite un mot : il refuse tout ZIP qui ne serait pas le
livrable audité. Sans lui, le script déploierait « un fichier nommé v31 »,
pas la V31.

Le seuil de 263 tests n'est pas décoratif. Une suite amputée passe au vert
sans rien prouver — c'est exactement ainsi qu'une régression traverse un
déploiement.

---

## Les cinq invariants vérifiés sur l'installation réelle

La suite de tests tourne sur les fichiers livrés. Elle ne peut pas prouver
l'état du dépôt **après** purge et restauration. L'étape 10 s'en charge :

1. **Les neuf paramètres économiques** sont présents et cohérents, et
   `historical_treatment` est décidé. Un paramètre absent ou incohérent rend
   tout verdict `NON_EVALUABLE` : le moteur n'émet plus rien. Il le dit — c'est
   correct, et parfaitement inutile.
2. **Les deux horizons sont actifs.** `POSITION` désactivé, aucune thèse
   fondamentale ni d'accumulation ne peut produire de contrat. C'est l'inverse
   de la stratégie, et c'était le blocker n° 2 de l'audit.
3. **`APP_VERSION == "v31"`.**
4. **Aucun ancien module n'est importable** (onze vérifiés, dont
   `report_memory`, `decision_engine`, `asset_plan`, `thesis_scoring`,
   `email_html`) et **`tests/` ne contient que la suite V31**. C'est la seule
   preuve que la purge a fait son travail.
5. **Quatre gabarits V31, six workflows, et pas de `heartbeat.yml`.**

---

## Migration de l'état pré-V31

`historical_treatment` vaut `purge`. L'historique v30 vient d'un régime de
scoring incompatible — clôture sur délai, stop révisable, cinq définitions
concurrentes de l'invalidation. Le mélanger aux mesures V31 les invaliderait.

**Rien n'est détruit** : l'ancien état est déplacé dans `state/pre_v31/`, et
reste consultable. L'opération est idempotente : relancer le script ne
rejouera rien.

---

## Après le déploiement

1. **Secrets** — Settings → Secrets → Actions. Le script en liste seize à la
   dernière étape. Il ne peut pas les vérifier depuis le Codespace, et ne
   prétend pas le faire.
2. **Premier run à la main** — Actions → « Morning Crypto Report » → Run
   workflow. Lis le mail avant de laisser tourner le cron.
3. **Déclenchement** — les trois rapports viennent de cron-job.org via
   `repository_dispatch` (types `morning` / `evening` / `weekly`). Le schedule
   GitHub natif reste retiré : deux déclencheurs produiraient des mails en
   double. Le seul schedule natif est celui du watchdog, et c'est délibéré —
   un chien de garde déclenché par le cron qu'il surveille tombe avec lui.

### Trois comportements V31 qui surprennent, et qui sont normaux

- **Un matin peut n'émettre aucune recommandation**, avec son motif chiffré.
  Sous marche sans dérive à deux barrières, l'espérance vaut `−c` pour tout
  couple (cible, invalidation) : tout geste émis revendique implicitement un
  avantage. V1–V4 bornent cette revendication au lieu de la laisser tacite.
- **La σ de l'horizon POSITION sera signalée dégradée** tant que les bougies
  OHLC ne couvriront pas 180 barres. C'est une honnêteté affichée, pas une
  panne.
- **Le carnet repart vierge.** Voir la migration ci-dessus.

---

## À quoi ressemble `git status` après coup

Mesuré sur un déploiement depuis la v30, chaque ligne s'explique :

| | Compte | Quoi |
|---|---|---|
| supprimés | **126** | 120 anciens chemins + les 6 fichiers d'état que la migration a **déplacés** vers `state/pre_v31/` |
| modifiés | **18** | exactement les fichiers présents dans les deux versions avec un contenu différent. `config/portfolio.yaml` **n'en fait pas partie** |
| nouveaux | **31** | les fichiers propres à V31, `state/pre_v31/`, `logs/`, et les artefacts de livraison |

Zéro ligne inexpliquée. Si tu en vois une, c'est un signal.

`git add -A` versionnera aussi `deploy_v31.sh` et `DEPLOY_V31.md` — le ZIP,
lui, reste ignoré (`*.zip` dans `.gitignore`). C'est voulu : le dépôt garde la
trace de la façon dont cette version a été déployée.

---

## Rollback

Le script crée `backup-pre-v31-AAAAMMJJ-HHMMSS` depuis `origin/<branche>` et la
pousse si possible. Il affiche la commande exacte en dernière ligne :

```bash
git reset --hard backup-pre-v31-AAAAMMJJ-HHMMSS
```

---

## Contenu du package

| Fichier | md5 | Rôle |
|---|---|---|
| `deploy_v31.sh` | — | le déploiement en douze étapes |
| `cap-v31-COMPLETE.zip` | `7661fe2ce0d848a7343ecd98d3c7cf5d` | le livrable audité, 111 entrées |
| `DEPLOY_V31.md` | — | cette notice |
