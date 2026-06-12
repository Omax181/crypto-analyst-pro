# Déploiement v15 — procédure (10 min)

⚠️ Rappel critique découvert sur la v14.1 : **cron-job.org et `repository_dispatch`
exécutent TOUJOURS `main`**. Tes 3 mails audités du 11/06 venaient du code main
(footer « v13 »), pas de la branche v14.1-deploy. Donc : tant que v15 n'est pas
**mergée sur main**, les mails quotidiens ne changeront pas.

## 1. Codespace — remplacer le code
```bash
cd /workspaces/crypto-analyst-pro
git checkout -b v15-deploy
find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
unzip -o ~/crypto-analyst-pro-v15.zip -d .   # adapter le chemin du zip uploadé
pip install -r requirements.txt
```
(State : tu repars de zéro comme convenu — seuls les secrets GitHub comptent,
ils sont inchangés, aucun nouveau secret en v15.)

## 2. Vérifier
```bash
python -m pytest -q          # attendu : ~153 verts (130 hérités + 23 nouveaux v15)
python diagnostic_apis.py    # regarder en particulier les 2 nouveaux checks :
                             #  · « Polymarket étendu (v15) » → dominant + N marchés
                             #  · « Calendrier macro consolidé (v15) » → jamais vide
```
En local Codespace, les ❌ « variable d'env absente » sont normaux (secrets sur
GitHub uniquement).

## 3. Pousser + test à blanc SUR LA BRANCHE
```bash
git add -A && git commit -m "v15: refonte audit 11/06 (calendrier consolidé, scoring Python, garde-fous recos, lisibilité)" && git push -u origin v15-deploy
```
GitHub → Actions → Morning Crypto Report → **Run workflow → Branch: v15-deploy**
(⚠️ ne pas laisser main, sinon tu testes l'ancien code). Idem Evening puis Weekly.

## 4. Checklist de recette sur les 3 mails reçus
- [ ] Footer : « Crypto Analyst Pro · v15 » (les 3 mails)
- [ ] Morning : EN BREF en puces ✓/⚠/✗ · barres Polymarket avec « maintien » en
      tête · header « X recos fermes · Y sous surveillance » · news ≤ 6 datées en
      FR · on-chain en tableau 3 colonnes · ordre de fin Invalidation → Auto-critique
- [ ] Morning : si une reco ferme a un SL fantaisiste → carte « ⛉ Garde-fou :
      dégradée en SURVEILLER » (c'est voulu)
- [ ] Evening : ligne « matin HHhMM · soir HHhMM · Δ » · P&L 2 décimales ·
      bloc sombre « Actions à poser ce soir » · ligne International
- [ ] Weekly : « Bilan du X au Y » · tableau Détail avec dates d'entrée + jours,
      1 ligne par actif · « Recos clôturées : N/5 minimum » · BTC hold
      « [fenêtre : 7 jours] » · semaine à venir avec badge Polymarket sur le FOMC ·
      score Qualité PTF · bloc « Stratégie de la semaine »
- [ ] Un seul mail matin / un seul mail soir (schedule natif retiré du repo —
      le fix Codespace est maintenant DANS le code)
- [ ] Calendrier : plus jamais « Aucun événement macro majeur » si un FOMC/CPI
      tombe dans la fenêtre (le repli banques centrales l'empêche)

## 5. Mise en prod
```bash
git checkout main && git merge v15-deploy && git push
```
Les mails de demain (cron-job.org → main) seront en v15. Le tracker repart
proprement : le détail hebdo se remplira au fil des recos (win rate affiché à
partir de 5 clôturées — c'est le comportement attendu, pas un bug).

## Notes de fonctionnement v15 (à savoir)
- Une reco ré-émise plusieurs matins de suite = UNE ligne, contenu à jour, mais
  prix/date d'entrée d'ORIGINE conservés pour un scoring honnête (champ
  `reissues` visible dans state/active_recommendations.json).
- Boursorama reste best-effort (4 URL tentées) : s'il est down, le calendrier
  tient sur FRED + FOMC/BoJ officiels + récurrences « (estimé) ».
- Première semaine post-reset : calibration et coût des erreurs affichent
  honnêtement « pas encore d'historique » — ça se remplit tout seul.
