# 🔄 Guide de migration · Agent Crypto V1 → V2

Ce guide te fait passer de la version actuelle (1 résumé monosource) à la V2
(analyste multi-sources, mémoire, tracking, 3 rapports + panic mode). Toutes les
étapes se font dans le navigateur, sauf une seule étape locale (optionnelle)
pour Telegram.

> 🟢 **Bonne nouvelle** : l'agent V2 fonctionne dès maintenant avec tes secrets
> actuels (Gemini + Gmail). Les nouvelles sources (Coinglass, Glassnode,
> Telegram, ETF) sont **optionnelles** : sans leurs clés, l'agent les marque
> "indisponibles" dans les angles morts et tourne quand même.

---

## Ce qui change concrètement

| Avant (V1) | Maintenant (V2) |
|---|---|
| 1 rapport générique | 3 rapports complémentaires : matin 08h30, soir 19h30, hebdo dimanche 11h30 |
| Alertes intraday toutes les 15 min | Panic mode (check 5 min, n'alerte que sur événement majeur) |
| Recos basées surtout sur les commits GitHub | Score sur 9 signaux ; commits ≤ 10% du raisonnement |
| Pas de suivi | Tracking des recos + win rate affiché |
| "JASMY -100% ATH", repos manquants | Bugs corrigés + mapping repos complet |
| Matin/soir sans lien | Mémoire inter-rapports (le soir complète le matin) |

---

## Étape 1 · Pousser le code V2 sur GitHub

Tu vas remplacer le contenu du repo par la V2 (le `.git` et tes secrets sont
conservés, seuls les fichiers de code changent).

1. Va sur ton repo `Omax181/crypto-analyst-pro` → bouton vert **Code** →
   **Codespaces** → **Create codespace on main** (comme la dernière fois).
2. Une fois le terminal prêt, **uploade le zip V2** (glisser-déposer dans
   l'explorateur de gauche), puis dans le terminal :

```bash
unzip -o crypto-analyst-pro.zip
cp -rf crypto-analyst-pro/* .
rm -rf crypto-analyst-pro crypto-analyst-pro.zip
git add -A
git commit -m "feat: refactor V2 multi-sources"
git push
```

> Le `git add -A` inclut aussi les suppressions (anciens fichiers retirés).

---

## Étape 2 · Vérifier les workflows

Dans l'onglet **Actions**, tu dois voir **4 workflows** :
`Morning Crypto Report`, `Evening Crypto Report`, `Weekly Crypto Report`,
`Panic Mode · Flash Alert` (+ `Heartbeat`). L'ancien `Intraday Alerts` a
disparu (remplacé par Panic Mode).

> Si GitHub demande de réactiver les workflows après le push, clique
> **"I understand my workflows, enable them"**.

---

## Étape 3 · Secrets : ce que tu as déjà suffit pour démarrer

Tes 5 secrets actuels (`GEMINI_API_KEY`, `GEMINI_MODEL`, `GMAIL_USER`,
`GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`) suffisent pour les 3 rapports.

### Secrets optionnels recommandés (chacun ~2 min, gratuits)

Ajoute-les dans **Settings → Secrets and variables → Actions** au fur et à
mesure. Chaque secret manquant = une source en moins, jamais un blocage.

| Secret | Où l'obtenir | Apporte |
|---|---|---|
| `COINMARKETCAP_API_KEY` | coinmarketcap.com/api | cross-check prix + rotation |
| `CRYPTOPANIC_API_KEY` | cryptopanic.com/developers/api | news <24h filtrées |
| `FRED_API_KEY` | fred.stlouisfed.org/docs/api/api_key.html | macro US (DXY, taux, CPI) |
| `ETHERSCAN_API_KEY` | etherscan.io/apis | on-chain ETH |
| `GH_TOKEN` | github.com/settings/tokens (scope public_repo) | activité dev (signal mineur) |
| `YOUTUBE_API_KEY` | console.cloud.google.com/apis/credentials | synthèse Crypto Pour Tous |
| `COINGLASS_API_KEY` | coinglass.com (free tier) | funding / open interest / liquidations |
| `GLASSNODE_API_KEY` | glassnode.com (free tier) | réserves exchange, adresses actives |

> Polymarket et les ETF flows (Farside) ne demandent **aucune clé**.

---

## Étape 4 (optionnelle) · Activer Telegram (Watcher Guru + BRICS)

Cette source demande une étape locale unique. Si tu ne veux pas, saute-la :
l'agent marquera Telegram "indisponible".

1. Sur ton ordinateur, installe Python puis : `pip install telethon`
2. Va sur https://my.telegram.org/auth, connecte-toi, crée une app → note
   **api_id** et **api_hash**.
3. Télécharge `generate_telegram_session.py` (à la racine du repo) et lance :
   ```bash
   python generate_telegram_session.py
   ```
4. Entre api_id / api_hash, valide le code reçu sur Telegram. Le script affiche
   une **session string**.
5. Dans GitHub, crée 3 secrets : `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
   `TELEGRAM_SESSION_STRING`.

> ⚠️ La session string donne accès à ton compte Telegram. Ne la partage jamais,
> ne la commite pas : uniquement dans les secrets GitHub.

---

## Étape 5 · Tester chaque rapport manuellement

Dans **Actions**, pour chaque workflow : sélectionne-le → **Run workflow** →
**Run workflow**. Attends 1-2 min, vérifie l'email reçu.

Teste dans cet ordre :
1. **Morning Crypto Report** → tu reçois le rapport complet du matin.
2. **Evening Crypto Report** → le soir lit le matin (vérifie qu'il y fait
   référence sans tout répéter).
3. **Weekly Crypto Report** → bilan + win rate (au début : peu de données, le
   win rate se remplit au fil des recos).
4. **Panic Mode** → en temps normal il ne fait rien (pas d'email si pas
   d'événement majeur). C'est voulu.

---

## Étape 6 · Que faire si un test échoue

| Symptôme | Cause probable / solution |
|---|---|
| Job rouge, log "auth failed" | `GMAIL_APP_PASSWORD` incorrect |
| Email "rapport dégradé" | Quota Gemini atteint → réessaie plus tard, ou passe au tier payant |
| Sections vides / "indisponibles" | Clé API de la source manquante (normal en mode dégradé) |
| Push refusé sur le state | Vérifie que les workflows ont la permission `contents: write` (déjà dans les fichiers) |
| Panic n'envoie jamais rien | Normal : il n'alerte que si BTC ±15%/1h, hack, ou token −25%/1h |

Pour voir le détail d'un échec : clique le job rouge → déroule l'étape en
erreur, ou télécharge l'artifact `error-logs-*` en bas de la page.

---

## Comment vérifier que le mail est conforme

Un bon rapport matin V2 contient :
- un **win rate** dans l'en-tête (vide au début, se remplit avec le temps),
- "l'histoire du jour" (3 fils croisés),
- des **thèses** uniquement pour les actifs qui passent le seuil de signaux,
  chacune avec auto-critique + plan d'action (entrée / TP 30-30-40 / stop /
  invalidation),
- **aucune** reco du type "pas de commit → alléger",
- des **sources horodatées** sous les insights,
- un bloc **angles morts** listant les sources inactives.

Si tu vois encore un copier-coller répété ou une stat inventée, utilise le
pouce 👎 et signale-le : le `coherence_checker` rétrograde déjà les recos mal
fondées, mais le réglage fin du prompt peut être ajusté.

---

## Maintenance

- **Horaires** : `cron:` dans `.github/workflows/*.yml` (UTC ; Casablanca = UTC+1).
- **Seuils / pondérations** : `config/thresholds.yaml`.
- **Mapping repos** : `config/github_repos.yaml`.
- **Mémoire** : dossier `state/` (commité automatiquement par les workflows).

Bonne route. 📈
