# 🚀 Guide de déploiement pas-à-pas

Ce guide te mène d'un dépôt vide à un agent qui t'envoie des rapports
automatiquement, **sans serveur et gratuitement**. Compte ~45 minutes la
première fois (surtout la création des comptes API).

> 💡 Tu n'as **aucune ligne de code à écrire**. Tu vas juste créer des comptes,
> copier des clés, et les coller dans GitHub.

---

## Étape 1 — Mettre le code sur GitHub

1. Crée un compte sur [github.com](https://github.com) si tu n'en as pas.
2. Crée un **nouveau dépôt** (bouton « New »), nomme-le par ex.
   `crypto-analyst-pro`, garde-le **public** (gratuité des Actions).
3. Uploade le contenu de ce projet dans le dépôt (glisser-déposer via
   « Add file → Upload files », ou `git push` si tu connais).

---

## Étape 2 — Créer tes clés API

Crée un compte (gratuit) sur chaque service ci-dessous et récupère la clé.
**Toutes ne sont pas obligatoires** : l'agent fonctionne en mode dégradé si
certaines manquent, mais plus tu en as, plus l'analyse est riche.

### Indispensables

| Service | Où obtenir la clé | Note |
|---------|-------------------|------|
| **Gemini** (le cerveau) | https://aistudio.google.com/apikey | Gratuit. **Le plus important.** |
| **Gmail** (envoi email) | voir Étape 3 ci-dessous | Mot de passe d'application |

### Fortement recommandées

| Service | Où obtenir la clé |
|---------|-------------------|
| **CoinMarketCap** | https://coinmarketcap.com/api/ |
| **CryptoPanic** (news) | https://cryptopanic.com/developers/api/ |
| **FRED** (macro US) | https://fred.stlouisfed.org/docs/api/api_key.html |
| **Etherscan** (on-chain ETH) | https://etherscan.io/apis |
| **GitHub Token** (santé projets) | https://github.com/settings/tokens (scope `public_repo`) |

### Optionnelles

| Service | Où obtenir la clé |
|---------|-------------------|
| **CoinGecko** | https://www.coingecko.com/en/api/pricing (marche sans clé, limites plus basses) |
| **YouTube Data API** | https://console.cloud.google.com/apis/credentials |

> CoinGecko, Fear & Greed, Binance, Reddit, blockchain.info et Trading Economics
> fonctionnent **sans clé**.

---

## Étape 3 — Configurer Gmail pour l'envoi

Gmail exige un **mot de passe d'application** (pas ton mot de passe habituel).

1. Active la **validation en 2 étapes** sur ton compte Google :
   https://myaccount.google.com/security
2. Va sur https://myaccount.google.com/apppasswords
3. Crée un mot de passe d'application (nom libre, ex. « crypto-agent »).
4. Google affiche un code de **16 lettres** — c'est ton `GMAIL_APP_PASSWORD`
   (copie-le sans les espaces).

---

## Étape 4 — Ajouter les clés dans GitHub (Secrets)

> ⚠️ Ne mets **jamais** tes clés directement dans le code. On utilise les
> « Secrets » chiffrés de GitHub.

1. Dans ton dépôt GitHub : **Settings** → **Secrets and variables** →
   **Actions** → bouton **New repository secret**.
2. Ajoute un secret pour chaque ligne ci-dessous (Nom = à gauche,
   Valeur = ta clé). Saute ceux que tu n'as pas.

| Nom du secret | Valeur |
|---------------|--------|
| `GEMINI_API_KEY` | ta clé Gemini |
| `GEMINI_MODEL` | `gemini-2.5-flash` |
| `GMAIL_USER` | ton adresse Gmail |
| `GMAIL_APP_PASSWORD` | le code 16 lettres de l'étape 3 |
| `RECIPIENT_EMAIL` | où recevoir les rapports (souvent le même email) |
| `COINMARKETCAP_API_KEY` | ta clé CMC |
| `CRYPTOPANIC_API_KEY` | ta clé CryptoPanic |
| `FRED_API_KEY` | ta clé FRED |
| `ETHERSCAN_API_KEY` | ta clé Etherscan |
| `GH_TOKEN` | ton GitHub token |
| `COINGECKO_API_KEY` | ta clé CoinGecko (optionnel) |
| `YOUTUBE_API_KEY` | ta clé YouTube (optionnel) |

---

## Étape 5 — Renseigner ton portfolio

1. Dans le dépôt, ouvre **`config/portfolio.yaml`** (bouton crayon ✏️ pour éditer).
2. Mets à jour tes positions : pour chaque crypto, le `tier` (1 = cœur de
   portefeuille → 4 = paris spéculatifs) et la `value_usd` actuelle.
3. Garde l'USDC avec `role: cash_reserve`.
4. Commit (bouton « Commit changes »).

---

## Étape 6 — Premier test manuel

1. Onglet **Actions** de ton dépôt.
2. Si GitHub demande d'activer les workflows, clique **« I understand… enable »**.
3. Choisis **« Morning Crypto Report »** dans la liste de gauche.
4. Bouton **« Run workflow »** → **« Run workflow »**.
5. Attends ~1-2 min. Si tout est vert ✅, **regarde ta boîte mail** : ton
   premier rapport est arrivé.

> ❌ Si le job échoue (croix rouge) : clique dessus pour voir les logs, et
> télécharge l'artifact `error-logs-morning` en bas de page. La cause la plus
> fréquente est un secret mal nommé ou le mot de passe Gmail incorrect.

---

## Étape 7 — C'est automatique !

Une fois le test réussi, **tu n'as plus rien à faire.** Les rapports partent
seuls :

| Workflow | Quand | Heure Casablanca |
|----------|-------|------------------|
| Morning report | `30 7 * * *` | ~08:30 |
| Evening report | `0 19 * * *` | ~20:00 |
| Weekly report | `0 11 * * 0` (dimanche) | ~12:00 |
| Heartbeat | 1× par mois | garde les crons actifs |
| Mettre à jour le portfolio | manuel (onglet Actions) | à la demande |

> ℹ️ Les horaires GitHub sont en **UTC** et peuvent avoir quelques minutes de
> retard en période de forte charge — c'est normal. Pour décaler une heure,
> modifie la ligne `cron:` du workflow concerné.

---

## Maintenance

- **Changer un horaire** : édite `.github/workflows/*.yml`, ligne `cron:`.
- **Ajuster les seuils d'alerte** : édite `config/thresholds.yaml`.
- **Mettre à jour le portfolio** : édite `config/portfolio.yaml` (ou en local,
  `python -m src.utils.portfolio_loader --update "..."`).
- **Le heartbeat** empêche GitHub de désactiver les crons après 60 jours
  d'inactivité du dépôt — laisse-le tourner.

---

## Dépannage rapide

| Symptôme | Cause probable / solution |
|----------|---------------------------|
| Aucun email reçu, job vert | Vérifie `RECIPIENT_EMAIL` et les spams |
| Échec « authentication failed » | `GMAIL_APP_PASSWORD` incorrect (pas le mdp habituel) |
| Rapport « dégradé » / partiel | Quota Gemini atteint → réessaie au prochain créneau, ou passe en tier payant |
| Sections vides | Clé API de la source concernée manquante (normal en mode dégradé) |
| Crons qui ne partent plus | Le dépôt était inactif > 60 j ; lance un workflow manuellement, le heartbeat reprend |

Bon trading. 📈
