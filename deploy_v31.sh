#!/usr/bin/env bash
# Déploiement V31 — Crypto Analyst Pro.
# À exécuter depuis la RACINE du dépôt git, avec cap-v31-COMPLETE.zip à côté.
#
#   bash deploy_v31.sh              # déploie, puis affiche le commit à faire
#   bash deploy_v31.sh --push       # déploie ET commite/pousse sur la branche
#
set -euo pipefail

ZIP="cap-v31-COMPLETE.zip"
MD5_ATTENDU="7661fe2ce0d848a7343ecd98d3c7cf5d"
TESTS_ATTENDUS=263
ENTREES_ATTENDUES=111
MOI="$(basename "$0")"
PUSH=0

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    -*) echo "✗ option inconnue : $arg (attendu : --push)"; exit 2 ;;
    *)  ZIP="$arg" ;;
  esac
done

echo "══ Déploiement V31 — Crypto Analyst Pro ══"
echo

# ═════════════════════════════════════════════════════════════════════════
echo "── 1/12 · contrôles préalables"
# ═════════════════════════════════════════════════════════════════════════
[ -d .git ] || { echo "✗ pas un dépôt git (lance depuis la racine)"; exit 1; }
[ -f "$ZIP" ] || { echo "✗ $ZIP introuvable"; exit 1; }
command -v python3 >/dev/null || { echo "✗ python3 introuvable"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' || {
  echo "✗ Python ≥ 3.11 requis (c'est la version des six workflows)"; exit 1; }
echo "  ✓ python3 $(python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"

# Le ZIP livré DOIT être celui qui a été audité et testé. Un déploiement qui
# ne le vérifie pas déploie « un zip nommé v31 », pas la V31.
MD5="$(python3 -c 'import hashlib,sys
print(hashlib.md5(open(sys.argv[1],"rb").read()).hexdigest())' "$ZIP")"
if [ "$MD5" != "$MD5_ATTENDU" ]; then
  echo "✗ empreinte du ZIP inattendue :"
  echo "     obtenue : $MD5"
  echo "     attendue: $MD5_ATTENDU"
  echo "  Ce n'est pas le livrable audité — déploiement interrompu."
  exit 1
fi
echo "  ✓ $ZIP conforme au livrable audité (md5 ${MD5:0:12}…)"

# Le dépôt doit être propre — MAIS les artefacts de livraison eux-mêmes (le
# zip, ce script, sa notice) viennent d'être déposés et apparaissent forcément
# en `??`. Les compter comme « dépôt sale » faisait déjà échouer un
# déploiement du projet voisin ; la leçon est reportée ici.
DIRTY="$(git status --porcelain \
  | grep -vF -e "$ZIP" -e "$MOI" -e "DEPLOY_V31.md" || true)"
if [ -n "$DIRTY" ]; then
  echo "✗ dépôt non propre — commit ou stash d'abord :"
  echo "$DIRTY"
  exit 1
fi
echo "  ✓ dépôt propre (hors artefacts de livraison)"

BRANCHE="$(git rev-parse --abbrev-ref HEAD)"
echo "  ✓ branche courante : $BRANCHE"

# ═════════════════════════════════════════════════════════════════════════
echo "── 2/12 · mise à jour du checkout"
# ═════════════════════════════════════════════════════════════════════════
# Ce projet commite `state/` après CHAQUE run (matin, soir, hebdo, bot) et
# `config/` après chaque mise à jour de portefeuille — depuis GitHub Actions,
# jamais depuis le Codespace. Un Codespace ouvert la veille porte donc un état
# EN RETARD. Or les étapes 4 et 7 sauvegardent puis restaurent cet état depuis
# le checkout local : déployer sans se mettre à jour ferait RÉGRESSER l'état
# de production. On se met donc à jour, en fast-forward seulement.
if git fetch origin --quiet 2>/dev/null && \
   git rev-parse --verify --quiet "origin/$BRANCHE" >/dev/null; then
  RETARD="$(git rev-list --count "HEAD..origin/$BRANCHE")"
  AVANCE="$(git rev-list --count "origin/$BRANCHE..HEAD")"
  if [ "$RETARD" -ne 0 ] && [ "$AVANCE" -ne 0 ]; then
    echo "✗ le checkout a DIVERGÉ d'origin/$BRANCHE"
    echo "     $AVANCE commit(s) local(aux), $RETARD commit(s) distant(s)"
    echo "  L'état de production ne peut pas être déterminé automatiquement."
    echo "  Réconcilie d'abord, puis relance."
    exit 1
  fi
  if [ "$RETARD" -ne 0 ]; then
    echo "  ◦ $RETARD commit(s) de retard — mise à jour fast-forward"
    git pull --ff-only origin "$BRANCHE" --quiet
    echo "  ✓ à jour ($(git rev-parse --short HEAD))"
  else
    echo "  ✓ déjà à jour avec origin/$BRANCHE ($(git rev-parse --short HEAD))"
  fi
else
  echo "  ⚠ origin/$BRANCHE injoignable — impossible de garantir que ce"
  echo "     checkout porte l'état de production. Vérifie state/ à la main."
fi

# ═════════════════════════════════════════════════════════════════════════
echo "── 3/12 · branche de secours"
# ═════════════════════════════════════════════════════════════════════════
# Elle part d'origin/<branche> et non du HEAD local : une sauvegarde qui ne
# restaure pas l'état RÉELLEMENT déployé n'est pas une sauvegarde.
BACKUP="backup-pre-v31-$(date +%Y%m%d-%H%M%S)"
if git rev-parse --verify --quiet "origin/$BRANCHE" >/dev/null; then
  git branch "$BACKUP" "origin/$BRANCHE"
  echo "  ✓ $BACKUP créée depuis origin/$BRANCHE ($(git rev-parse --short "origin/$BRANCHE"))"
else
  git branch "$BACKUP"
  echo "  ⚠ origin injoignable — $BACKUP créée depuis le HEAD LOCAL"
  echo "     ($(git rev-parse --short HEAD)) : vérifie que c'est bien l'état déployé"
fi
if git push -u origin "$BACKUP" >/dev/null 2>&1; then
  echo "  ✓ $BACKUP poussée sur origin"
else
  echo "  ⚠ $BACKUP en LOCAL seulement (push impossible)"
fi

# ═════════════════════════════════════════════════════════════════════════
echo "── 4/12 · sauvegarde de l'état vivant"
# ═════════════════════════════════════════════════════════════════════════
# DEUX objets sont produits par la production et NE SONT PAS reconstructibles :
#
#   state/                  le carnet de contrats, le registre des sources, la
#                           mémoire du bot — commité par les workflows.
#   config/portfolio.yaml   quantités et PRU, réécrits par
#                           scripts/update_portfolio.py, par le bot Telegram
#                           (portfolio_edit) et par portfolio_loader, puis
#                           commités par update_portfolio.yml (`git add config/`).
#
# Le ZIP contient une copie de portfolio.yaml, mais c'est la BASELINE de
# livraison : elle est identique octet pour octet depuis la v29. L'extraire
# par-dessus la production écraserait le portefeuille réel par un instantané
# vieux de plusieurs versions. On le sauvegarde donc comme state/.
TMP="$(mktemp -d)"
if [ -d state ]; then
  cp -a state "$TMP/state"          # si ça échoue, set -e arrête ici :
  N_ETAT="$(find state -type f ! -name .gitkeep | wc -l | tr -d ' ')"
  echo "  ✓ state/ : $N_ETAT fichier(s) sauvegardé(s)"
else                                # on ne repart JAMAIS d'un state vide
  mkdir -p "$TMP/state"             # par accident.
  echo "  ◦ pas de state/ à sauvegarder (premier déploiement ?)"
fi
if [ -f config/portfolio.yaml ]; then
  cp -a config/portfolio.yaml "$TMP/portfolio.yaml"
  echo "  ✓ config/portfolio.yaml sauvegardé ($(wc -c <config/portfolio.yaml | tr -d ' ') o)"
else
  echo "  ◦ pas de config/portfolio.yaml — celui du ZIP servira de base"
fi

# ═════════════════════════════════════════════════════════════════════════
echo "── 5/12 · extraction de $ZIP"
# ═════════════════════════════════════════════════════════════════════════
python3 - "$ZIP" "$ENTREES_ATTENDUES" <<'PY'
import sys, zipfile
attendues = int(sys.argv[2])
with zipfile.ZipFile(sys.argv[1]) as z:
    noms = z.namelist()
    assert not any("\\" in n for n in noms), (
        "ZIP construit avec des séparateurs Windows — structure perdue")
    mauvais = [n for n in noms if n.startswith("/") or ".." in n.split("/")]
    assert not mauvais, f"chemins non relatifs dans le ZIP : {mauvais[:3]}"
    # Le ZIP porte `state/.gitkeep` (vide) pour que le dossier existe dans git.
    # TOUTE autre entrée sous state/ écraserait l'état de production.
    etat = [n for n in noms if n.startswith("state/")]
    assert etat == ["state/.gitkeep"], f"le ZIP écrit dans state/ : {etat}"
    assert z.getinfo("state/.gitkeep").file_size == 0, (
        "state/.gitkeep n'est pas vide")
    assert len(noms) == attendues, (
        f"{len(noms)} entrées, {attendues} attendues — livrable incomplet")
    z.extractall(".")
    print(f"  ✓ {len(noms)} entrée(s) extraite(s)")
PY

# ═════════════════════════════════════════════════════════════════════════
echo "── 6/12 · purge des anciens chemins"
# ═════════════════════════════════════════════════════════════════════════
# V31 n'est pas un correctif posé sur la v30 : c'est une refonte du noyau qui
# SUPPRIME 62 modules et 54 fichiers de tests. Une extraction seule les
# laisserait en place — et les 54 anciens tests, collectés par pytest,
# feraient échouer une suite pourtant saine. On supprime donc tout fichier des
# arbres de code qui n'est pas dans le livrable.
python3 - "$ZIP" <<'PY'
import os, shutil, subprocess, sys, zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    livres = set(z.namelist())

RACINES = ("src", "tests", "scripts", os.path.join(".github", "workflows"))
IGNORES = ("__pycache__", ".pytest_cache")

morts, dechets = [], []
for racine in RACINES:
    if not os.path.isdir(racine):
        continue
    for dossier, sous, fichiers in os.walk(racine):
        sous[:] = [d for d in sous if d not in IGNORES]
        if any(p in IGNORES for p in dossier.split(os.sep)):
            continue
        for f in fichiers:
            chemin = os.path.join(dossier, f)
            relatif = chemin.replace(os.sep, "/")
            if relatif in livres:
                continue
            (dechets if f.endswith((".pyc", ".pyo")) else morts).append(relatif)

# À la racine, on ne touche QUE ce que git suit : un fichier de travail non
# suivi appartient à son auteur, pas au déploiement.
suivis = subprocess.run(["git", "ls-files"], text=True,
                        capture_output=True).stdout.splitlines()
morts += [p for p in suivis
          if "/" not in p and p.endswith(".py") and p not in livres]

for p in sorted(set(morts)) + dechets:
    try:
        os.remove(p)
    except OSError as exc:
        print(f"  ⚠ {p} non supprimé ({exc})")

# Le bytecode compilé part AVANT le balayage des dossiers vides : un
# `__pycache__` oublié suffit à faire survivre le squelette d'un paquet
# supprimé (src/tracking/), et il n'apparaît dans aucun `git status`.
for racine in RACINES:
    for dossier, sous, _f in os.walk(racine):
        for cache in [d for d in sous if d in IGNORES]:
            shutil.rmtree(os.path.join(dossier, cache), ignore_errors=True)
        sous[:] = [d for d in sous if d not in IGNORES]

# Dossiers devenus vides (src/tracking/, anciens paquets…)
for racine in RACINES:
    for dossier, _s, _f in os.walk(racine, topdown=False):
        if dossier != racine and not os.listdir(dossier):
            os.rmdir(dossier)

par_racine = {}
for p in sorted(set(morts)):
    par_racine.setdefault(p.split("/")[0] if "/" in p else "(racine)",
                          []).append(p)
if par_racine:
    for racine, items in sorted(par_racine.items()):
        print(f"  ✓ {racine:<12} {len(items):>3} fichier(s) obsolète(s) "
              f"supprimé(s)")
else:
    print("  ◦ aucun ancien chemin à supprimer (déploiement déjà en V31)")

# Ce qui disparaît AILLEURS que dans les arbres de code n'est pas supprimé
# d'office — un document ou un fichier de travail appartient à son auteur.
# C'est signalé, la décision revient au lecteur.
#
# `state/` et `logs/` sont EXCLUS de ce signalement : ils portent l'état de
# production, dont l'absence du livrable est la règle et non l'anomalie. Les
# lister ici laisserait croire qu'ils sont de trop.
HORS = ("src/", "tests/", "scripts/", ".github/", "state/", "logs/")
restants = sorted(p for p in suivis
                  if p not in livres
                  and not p.startswith(HORS)
                  and os.path.exists(p))
if restants:
    print("  ◦ hors arbres de code, absents du livrable (NON supprimés — "
          "à toi de voir) :")
    for p in restants:
        print(f"      {p}")
PY

# ═════════════════════════════════════════════════════════════════════════
echo "── 7/12 · restauration de l'état vivant (JAMAIS écrasé par le ZIP)"
# ═════════════════════════════════════════════════════════════════════════
rm -rf state
cp -a "$TMP/state" state
touch state/.gitkeep          # c'est lui qui fait exister state/ dans git
mkdir -p logs && touch logs/.gitkeep
if [ -f "$TMP/portfolio.yaml" ]; then
  cp -a "$TMP/portfolio.yaml" config/portfolio.yaml
  echo "  ✓ config/portfolio.yaml de production restauré"
fi
rm -rf "$TMP"
N_ETAT="$(find state -type f ! -name .gitkeep | wc -l | tr -d ' ')"
echo "  ✓ state/ : $N_ETAT fichier(s) d'état conservé(s)"

# ═════════════════════════════════════════════════════════════════════════
echo "── 8/12 · dépendances et relecture de l'état vivant"
# ═════════════════════════════════════════════════════════════════════════
if python3 -m pip install --quiet --disable-pip-version-check \
     -r requirements.txt 2>/dev/null; then
  echo "  ✓ requirements.txt installé"
else
  echo "  ⚠ pip install a échoué — le contrôle d'imports ci-dessous tranchera"
fi
python3 - <<'PY'
import importlib, sys
manquants = []
for module, paquet in (("yaml", "pyyaml"), ("jinja2", "jinja2"),
                       ("markupsafe", "markupsafe"), ("requests", "requests"),
                       ("bs4", "beautifulsoup4"), ("tenacity", "tenacity"),
                       ("matplotlib", "matplotlib"), ("google.genai",
                                                      "google-genai"),
                       ("pytest", "pytest")):
    try:
        importlib.import_module(module)
    except ImportError:
        manquants.append(paquet)
if manquants:
    print(f"✗ dépendance(s) absente(s) : {', '.join(manquants)}")
    print("  Le projet ne peut pas tourner. `pip install -r requirements.txt`.")
    raise SystemExit(1)
print("  ✓ les 9 dépendances du runtime sont importables")
PY

# L'état vivant qu'on vient de restaurer vient d'un régime ANTÉRIEUR. On ne
# suppose pas qu'il est lisible : on le lit.
python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from src.core import formatter as fmt
from src.utils import portfolio_loader

data = portfolio_loader.load_portfolio()
actifs = data["portfolio"]
total = portfolio_loader.total_value_usd(data)
print(f"  ✓ portfolio.yaml relu par V31 : {len(actifs)} actifs · "
      f"{fmt.usd(total)} de baseline")

from src.core.book import RecommendationBook
livre = RecommendationBook(run_kind="deploy", run_id="deploy")
print(f"  ✓ carnet relu par V31 : {len(livre.active())} contrat(s) actif(s) "
      f"sur {len(livre.all())}")
PY

# ═════════════════════════════════════════════════════════════════════════
echo "── 9/12 · suite de tests"
# ═════════════════════════════════════════════════════════════════════════
LOG="$(mktemp)"
set +e
python3 -m pytest tests -q -p no:cacheprovider >"$LOG" 2>&1
RC=$?
set -e
tail -3 "$LOG"
[ "$RC" -eq 0 ] || { echo "✗ tests en échec — déploiement interrompu"; exit 1; }
N="$(grep -oE '[0-9]+ passed' "$LOG" | grep -oE '[0-9]+' | tail -1 || echo 0)"
[ "$N" -ge "$TESTS_ATTENDUS" ] || {
  echo "✗ $N tests exécutés, $TESTS_ATTENDUS attendus — suite incomplète"
  echo "  (une suite amputée passe au vert sans rien prouver)"
  exit 1; }
echo "  ✓ $N tests verts"
rm -f "$LOG"

# ═════════════════════════════════════════════════════════════════════════
echo "── 10/12 · invariants V31"
# ═════════════════════════════════════════════════════════════════════════
# Ce que la suite de tests ne peut PAS prouver : elle tourne sur les fichiers
# livrés, pas sur le dépôt tel qu'il est après purge. Les cinq contrôles
# ci-dessous portent sur l'installation réelle.
python3 - <<'PY'
import importlib.util, pathlib, sys
sys.path.insert(0, ".")
mauvais = []

# 1. Les neuf paramètres économiques. Absents ou incohérents, le moteur
#    n'émet plus rien — et il le dit, ce qui est correct mais inutile.
from src.core import params
manquants = params.missing_emission_params()
incoherents = params.incoherent_params()
if manquants:
    mauvais.append(f"paramètres d'émission manquants : {manquants}")
if incoherents:
    mauvais.append(f"paramètres incohérents : {incoherents}")
traitement = params.historical_treatment()
if traitement not in ("purge", "mark", "reset"):
    mauvais.append(f"historical_treatment non décidé : {traitement!r}")
print(f"  ✓ 9 paramètres d'émission présents et cohérents · "
      f"historical_treatment = {traitement}")

# 2. Les deux horizons. POSITION désactivé, aucune thèse fondamentale ni
#    d'accumulation ne peut produire de contrat : c'est l'inverse de la
#    stratégie, et ça s'était déjà produit.
from src.core.horizon import SPECS
eteints = sorted(h.value for h, s in SPECS.items() if not s.enabled)
allumes = sorted(h.value for h, s in SPECS.items() if s.enabled)
if eteints:
    mauvais.append(f"horizon(s) désactivé(s) : {eteints}")
print(f"  ✓ horizons actifs : {', '.join(allumes) or 'AUCUN'}"
      f"{'  · éteints : ' + ', '.join(eteints) if eteints else ''}")

# 3. Version applicative.
from src.reporting import render
if render.APP_VERSION != "v31":
    mauvais.append(f"APP_VERSION = {render.APP_VERSION!r}, attendu 'v31'")
print(f"  ✓ APP_VERSION = {render.APP_VERSION}")

# 4. Aucun ancien chemin ne doit RESTER IMPORTABLE. C'est la seule preuve que
#    la purge a fait son travail : une double logique se dénonce ici.
ANCIENS = ("src.state.report_memory", "src.ai_brain.decision_engine",
           "src.analytics.asset_plan", "src.analytics.thesis_scoring",
           "src.analytics.reco_gate", "src.analytics.daily_guards",
           "src.analytics.weekly_guards", "src.analytics.coherence_checker",
           "src.analytics.key_levels", "src.reporting.email_html",
           "src.tracking.prediction_scoring")
survivants = []
for nom in ANCIENS:
    try:
        if importlib.util.find_spec(nom) is not None:
            survivants.append(nom)
    except (ImportError, AttributeError, ValueError):
        pass
if survivants:
    mauvais.append(f"ancien(s) module(s) encore importable(s) : {survivants}")
print(f"  ✓ {len(ANCIENS)} anciens modules : aucun n'est importable")

anciens_tests = sorted(p.name for p in pathlib.Path("tests").glob("test_*.py")
                       if not p.name.startswith("test_v31_"))
if anciens_tests:
    mauvais.append(f"ancien(s) fichier(s) de tests : {anciens_tests}")
print(f"  ✓ tests/ ne contient que la suite V31 "
      f"({len(list(pathlib.Path('tests').glob('test_v31_*.py')))} fichiers)")

# 5. Les trois gabarits, et les six workflows attendus.
gabarits = sorted(p.name for p in
                  pathlib.Path("src/reporting/templates").glob("*.j2"))
attendus = ["_v31_macros.html.j2", "v31_evening.html.j2",
            "v31_morning.html.j2", "v31_weekly.html.j2"]
if gabarits != attendus:
    mauvais.append(f"gabarits inattendus : {gabarits}")
wf = sorted(p.name for p in pathlib.Path(".github/workflows").glob("*.yml"))
if "heartbeat.yml" in wf:
    mauvais.append("heartbeat.yml (v30) survit — il se déclencherait encore")
print(f"  ✓ 4 gabarits V31 · {len(wf)} workflows : {', '.join(wf)}")

if mauvais:
    print()
    for m in mauvais:
        print(f"✗ {m}")
    raise SystemExit(1)
PY

# ═════════════════════════════════════════════════════════════════════════
echo "── 11/12 · migration de l'état pré-V31"
# ═════════════════════════════════════════════════════════════════════════
# `historical_treatment: purge` — l'historique v30 vient d'un régime de scoring
# incompatible (clôture sur délai, stop révisable, cinq définitions de
# l'invalidation) ; l'importer polluerait les mesures V31. RIEN N'EST DÉTRUIT :
# l'ancien état part dans state/pre_v31/. L'opération est idempotente.
migrer() {                  # sans ce garde, un code 2 tuerait le script SANS
  set +e                    # dire pourquoi : `| sed` avale le statut.
  python3 -m scripts.migrate_v31 "$@" 2>&1 | sed 's/^/     /'
  local rc=${PIPESTATUS[0]}
  set -e
  [ "$rc" -eq 0 ] || {
    echo "✗ migration refusée (code $rc) — voir le motif ci-dessus."
    echo "  Le dépôt est déployé mais l'état pré-V31 n'a PAS été archivé."
    exit 1; }
}
echo "  ── simulation"
migrer
echo "  ── application"
migrer --apply
if [ -d state/pre_v31 ]; then
  N_ARCH="$(find state/pre_v31 -type f | wc -l | tr -d ' ')"
  echo "  ✓ $N_ARCH fichier(s) archivé(s) dans state/pre_v31/"
fi

# ═════════════════════════════════════════════════════════════════════════
echo "── 12/12 · lint, version, secrets"
# ═════════════════════════════════════════════════════════════════════════
# pyflakes n'est PAS dans requirements.txt : outil de développement, absent du
# runtime des workflows. On le lance s'il est là, on ne bloque pas s'il manque.
if python3 -c "import pyflakes" 2>/dev/null; then
  if python3 -m pyflakes src scripts tests; then
    echo "  ✓ pyflakes 0"
  else
    echo "✗ pyflakes signale les lignes ci-dessus — déploiement interrompu"
    exit 1
  fi
else
  echo "  ◦ pyflakes non installé ici — contrôle fait côté source"
fi
if python3 -m compileall -q src scripts tests >/dev/null; then
  echo "  ✓ compileall 0"
else
  echo "✗ un fichier ne compile pas — déploiement interrompu"
  exit 1
fi

# Les secrets vivent dans GitHub Actions, PAS dans le Codespace : ce script ne
# peut ni les lire ni les vérifier, et n'essaie pas de le faire semblant. Il
# se contente de dresser la liste exacte que les workflows réclament.
python3 - <<'PY'
import pathlib, re
noms = set()
for p in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    noms |= set(re.findall(r"secrets\.([A-Z0-9_]+)", p.read_text(
        encoding="utf-8")))
noms = sorted(noms)
print(f"  ◦ {len(noms)} secrets réclamés par les workflows "
      f"(Settings → Secrets → Actions) :")
for i in range(0, len(noms), 3):
    print("      " + " ".join(f"{n:<26}" for n in noms[i:i + 3]).rstrip())
PY

echo
echo "══════════════════════════════════════════════════════════════════"
echo "✓ V31 déployée dans le checkout."
echo "══════════════════════════════════════════════════════════════════"

if [ "$PUSH" -eq 1 ]; then
  echo "── commit et push (--push)"
  git add -A
  if git diff --cached --quiet; then
    echo "  ◦ rien à committer — le dépôt était déjà en V31"
  else
    git commit -q -m "V31 — refonte du noyau décisionnel (contrats, horizons, viabilité)"
    git push origin "$BRANCHE"
    echo "  ✓ poussé sur origin/$BRANCHE ($(git rev-parse --short HEAD))"
  fi
else
  echo
  echo "  Vérifie « git status » et « git diff », puis :"
  echo "    git add -A"
  echo "    git commit -m 'V31 — refonte du noyau décisionnel'"
  echo "    git push origin $BRANCHE"
fi

cat <<'FIN'

  PREMIER RUN — dans cet ordre :

    1. Settings → Secrets → Actions : les secrets listés ci-dessus.
    2. Actions → « Morning Crypto Report » → Run workflow  (à blanc, à la main)
    3. Lis le mail. Le bandeau de dégradation ÉNUMÈRE ce qui manque ; son
       absence signifie « aucune dégradation détectée », jamais « fiable ».
    4. Les rapports sont déclenchés par cron-job.org via repository_dispatch
       (types : morning / evening / weekly). Le schedule GitHub natif reste
       retiré volontairement : deux déclencheurs = mails en double. Le seul
       schedule natif est celui du watchdog, et c'est délibéré — un chien de
       garde qui dépend du cron qu'il surveille tombe avec lui.

  CE QUE LA V31 VA FAIRE DIFFÉREMMENT, ET QUI EST NORMAL :

    · Un matin peut n'émettre AUCUNE recommandation, et le dire avec son
      motif chiffré. C'est le comportement voulu : sous marche sans dérive,
      tout geste revendique un avantage, et V1-V4 bornent cette revendication.
    · La σ de l'horizon POSITION sera signalée DÉGRADÉE tant que les bougies
      OHLC ne couvriront pas 180 barres. C'est une honnêteté, pas une panne.
    · Le carnet repart VIERGE (historical_treatment: purge). L'ancien état est
      consultable dans state/pre_v31/.

FIN
echo "  Rollback : git reset --hard $BACKUP"
echo
