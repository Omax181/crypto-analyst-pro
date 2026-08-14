# Crypto Analyst Pro — V31

Agent d'analyse crypto personnel. Trois rapports par jour (matin, soir,
hebdomadaire), un carnet de recommandations falsifiable, un bot Telegram en
lecture.

---

## Ce que V31 change

V31 est une refonte du **noyau décisionnel**, pas un habillage. Cinq
renversements structurent tout le reste.

**1. Un contrat, pas une opinion.** Une recommandation est un objet à cycle de
vie : `CANDIDATE → ACTIVE → {TARGET_HIT, INVALIDATED, EXPIRED, SUPERSEDED,
CANCELLED}`. Le contrat scoré — entrée, cible, invalidation, échéance — est
écrit une fois et n'est jamais modifié. Les révisions vivent dans un plan
opérationnel versionné qui, lui, n'est jamais scoré. En v30, cinq modules
maintenaient chacun leur définition d'« invalidation » ; il n'en reste qu'une,
dans `core/book.py`.

**2. Deux horizons, chacun avec son échelle.** `SWING` (30 j) sert les thèses
techniques et de catalyseur ; `POSITION` (180 j) sert les thèses fondamentales
et le cœur du portefeuille. L'horizon est déterminé par les **poids des signaux
déterministes**, jamais par le LLM ni par le résultat économique recherché, et
il fixe tout le reste : fenêtre de niveaux, échelle de volatilité, profondeur
d'historique exigée, date d'expiration.

> Une première intégration avait désactivé `POSITION` au motif d'une profondeur
> « non fournie par le pipeline ». C'était une limite auto-infligée, et son
> effet était l'inverse de la stratégie : un actif « sous PRU + drawdown
> profond + MVRV bas » — le meilleur setup d'accumulation du profil — ne
> pouvait produire aucun contrat, tandis qu'un simple rebond technique en
> produisait un. Le pipeline fournit désormais les 365 clôtures que `POSITION`
> exige ; un actif trop jeune reçoit un refus **chiffré**, pas catégorique.

**3. Un geste doit valoir la peine, en devise.** Avant d'exister, un plan
franchit quatre conditions :

| | Condition |
|---|---|
| **V1** | l'avantage exigé sur le hasard, `Δ = c/(u+d)`, reste revendicable — et la revendication elle-même reste énonçable |
| **V2** | cible et invalidation sortent du bruit de leur propre horizon (`k·σ_H`) |
| **V3** | l'espérance nette après coûts atteint la référence de matérialité |
| **V4** | le geste est exécutable (ticket minimum) |

Sous une marche sans dérive à deux barrières, l'espérance vaut `−c` pour tout
couple (u, d) : **tout geste émis revendique implicitement un avantage**. V1
rend cette revendication explicite et la borne. C'est ce qui rend inexprimable
le « acheter ETH à 2 000 $, cible 2 015 $ » de la v30.

**4. Le LLM formule, il ne décide pas.** Chaque champ d'un rapport est
`DERIVED` (Python), `AUTHORED` (LLM) ou `EXTERNAL` (citation). Un champ
AUTHORED ne contient **aucun chiffre** : les nombres sont référencés par jeton
`[[fact:id]]` pris dans un catalogue de faits construit avant l'appel. La
validation se fait **par rejet, jamais par réparation** — une réparation
prétend connaître l'intention de l'auteur.

**5. Rien n'est publié sans son incertitude.** L'indice de confiance
(`mail_confidence`) et le compteur « X/25 sources » sont supprimés : ils
saturaient. À la place, une **matrice d'état par source** et un **bandeau de
dégradation** qui ÉNUMÈRE ce qui est dégradé. Absence de bandeau = aucune
dégradation détectée, jamais « rapport fiable ».

---

## Le carburant : `config/params.yaml`

Les neuf paramètres qui conditionnent l'émission sont **renseignés**, et chacun
porte sa provenance dans le fichier :

| Nature | Sens | Paramètres |
|---|---|---|
| `[FAIT]` | observable ou public, vérifiable | `fee_rate` (barème Binance spot) |
| `[PROFIL]` | déclaration de l'investisseur | `monthly_budget`, `ticket_min`, `materiality_reference` |
| `[RISQUE]` | arbitrage d'aversion, assumé et instrumenté | `delta_claimable`, `p_target_max`, `p_stop_max`, `k3`, `liquidity_bands` |

Deux protections, appliquées et non commentées :

- **une valeur absente reste absente.** Le code n'invente jamais un budget ni un
  barème : tout verdict devient `NON_EVALUABLE`, et le mail le dit.
- **une valeur présente mais incohérente est traitée comme absente** —
  `p_stop_max ≥ p_target_max`, ticket au-dessus du budget, probabilité hors
  `]0,1[`… Elle n'est jamais « corrigée » au passage : la corriger reviendrait à
  inventer un paramètre métier.

Le couplage `k3 × monthly_budget` est explicité dans le fichier : il détermine
l'amplitude minimale d'un contrat. Mesuré sur une grille de σ allant de 6 à
25 %, le réglage livré retient **88 % des contrats structurellement valides** —
il tranche la queue basse sans stériliser le moteur, et un test verrouille cette
plage.

---

## Les trois runs

| Run | Rôle | Écriture |
|---|---|---|
| **Matin** | collecte complète, transitions, émission | **seul run habilité** |
| **Soir** | delta depuis le matin, franchissements EN SÉANCE | lecture seule |
| **Hebdo** | contrats clôturés, mesures, enseignements | lecture seule |

Les clôtures étant journalières UTC, matin et soir voient la même dernière
clôture complète : seul le matin peut en observer une nouvelle. Le soir signale
un franchissement intraday **sans transitionner** — la transition sera évaluée
sur la clôture.

Un contrat n'existe que s'il a été **communiqué** : la persistance intervient
après un envoi réussi, et le commit d'état des workflows est conditionné au
succès du job.

---

## Démarrer

```bash
pip install -r requirements.txt
cp .env.example .env        # renseigner les clés
python -m src.main morning  # ou evening / weekly
```

Migration depuis un état pré-V31 — `historical_treatment` vaut `purge` :

```bash
python -m scripts.migrate_v31          # simulation, n'écrit rien
python -m scripts.migrate_v31 --apply  # archive l'ancien état, carnet vierge
```

**Rien n'est détruit** : l'ancien état est déplacé dans `state/pre_v31/`. Il
n'est pas importé parce qu'il a été produit sous un régime de scoring
incompatible (clôture sur délai, stop révisable, cinq définitions concurrentes
de l'invalidation) — le mélanger aux mesures V31 les invaliderait. Un contrat
hérité dont l'invalidation est du mauvais côté de l'entrée n'est de toute façon
pas migrable : le corriger reviendrait à inventer un contrat jamais communiqué.

---

## Bot Telegram

`/carnet` `/ptf` `/sources` `/resume` `/memoire` `/aide`

Le bot **lit** le carnet. Une seule commande écrit : `/dismiss ACTIF`, qui
produit la transition `CANCELLED` — un contrat annulé sort du scoring, il ne
compte ni en réussite ni en échec. `/validate` et `/snooze` sont supprimées :
une issue ne se décrète pas, elle s'observe sur la clôture.

---

## Tests

```bash
python -m pytest tests -q
```

Les invariants critiques sont des tests, pas des commentaires : autorité
d'écriture, carnet inchangé octet pour octet le soir, rejet du contenu sans
réparation, locale française sur la sortie rendue, `NON_EVALUABLE` jamais
assimilé à `VIABLE`.
