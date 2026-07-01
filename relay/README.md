# Relais Telegram → GitHub Actions (réveil par message)

Ce relais remplace le cron « toutes les 5 min » du bot Telegram par un réveil
**uniquement quand Omar envoie un message**. Résultat : plus de runs à vide,
quota GitHub Actions préservé, et réponse en ~20-30 s au lieu d'attendre le
prochain cron.

```
Omar → Telegram → [webhook] → Cloudflare Worker → file KV + repository_dispatch
                                                          ↓
                                   GitHub Actions (Telegram Bot) → GET /pull → répond
```

Le Worker : [`cloudflare-worker.js`](cloudflare-worker.js).

---

## Pré-requis

- Le bot Telegram existe déjà (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` configurés
  comme secrets GitHub).
- Un compte Cloudflare (gratuit, sans carte) : <https://dash.cloudflare.com/sign-up>.

---

## Étape 1 — Choisir un secret partagé

Invente une longue chaîne aléatoire (≥ 32 caractères), p. ex. via
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.
On l'appelle **`RELAY_SECRET`**. Il sert à la fois :
- au webhook Telegram (en-tête `X-Telegram-Bot-Api-Secret-Token`) ;
- à authentifier le `GET /pull` du bot.

Garde-le de côté, on le réutilise plusieurs fois.

## Étape 2 — Créer un PAT GitHub (pour le dispatch)

1. <https://github.com/settings/personal-access-tokens/new> (fine-grained).
2. **Repository access** → *Only select repositories* → `crypto-analyst-pro`.
3. **Permissions** → *Repository permissions* → **Contents : Read and write**.
4. Générer, copier le token (`github_pat_…`). C'est **`GH_DISPATCH_TOKEN`**.

## Étape 3 — Créer le namespace KV

Dans le dashboard Cloudflare : **Storage & Databases → KV → Create namespace**,
nom `tg-queue`.

## Étape 4 — Créer le Worker

1. **Compute (Workers) → Create → Worker**, nomme-le p. ex. `cap-telegram-relay`,
   *Deploy* (le code par défaut sera remplacé).
2. **Edit code** → colle le contenu de [`cloudflare-worker.js`](cloudflare-worker.js)
   → *Deploy*.
3. Note l'URL du Worker : `https://cap-telegram-relay.<ton-sous-domaine>.workers.dev`.

## Étape 5 — Configurer le Worker (variables, secrets, KV)

Dans **Settings** du Worker :

| Type | Nom | Valeur |
|------|-----|--------|
| Variable | `GH_OWNER` | `Omax181` |
| Variable | `GH_REPO` | `crypto-analyst-pro` |
| Variable | `ALLOWED_CHAT_ID` | `7311655046` |
| Secret (Encrypt) | `RELAY_SECRET` | (étape 1) |
| Secret (Encrypt) | `GH_DISPATCH_TOKEN` | (étape 2) |
| KV binding | `TG_QUEUE` | namespace `tg-queue` (étape 3) |

⚠️ Le nom du binding KV doit être **exactement** `TG_QUEUE`. *Deploy* après modif.

## Étape 6 — Déclarer le webhook Telegram

Ouvre cette URL dans le navigateur (remplace `<TOKEN>`, `<WORKER_URL>`,
`<RELAY_SECRET>`) :

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>/tg&secret_token=<RELAY_SECRET>&allowed_updates=["message"]
```

Réponse attendue : `{"ok":true,"result":true,"description":"Webhook was set"}`.

> ⚠️ Tant qu'un webhook est actif, `getUpdates` est désactivé côté Telegram
> (normal). Pour revenir au mode polling, supprimer le webhook :
> `https://api.telegram.org/bot<TOKEN>/deleteWebhook`.

## Étape 7 — Ajouter les secrets GitHub

Repo **Settings → Secrets and variables → Actions** :

| Secret | Valeur |
|--------|--------|
| `RELAY_PULL_URL` | `<WORKER_URL>/pull` |
| `RELAY_SECRET` | (étape 1, identique au Worker) |

Le workflow `telegram_bot.yml` les injecte ; dès que `RELAY_PULL_URL` est présent,
le bot passe en mode relais automatiquement.

---

## Vérifier

1. Envoie un message au bot sur Telegram.
2. Onglet **Actions** du repo : un run *Telegram Bot* doit démarrer en quelques
   secondes (déclencheur `repository_dispatch`).
3. Le bot répond sur Telegram.

## Dépannage

- **Aucun run ne démarre** → webhook : ouvre
  `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`. `pending_update_count` qui
  monte = le Worker ne dispatche pas (vérifie `GH_DISPATCH_TOKEN` + `GH_OWNER`/`GH_REPO`).
  `last_error_message` renseigne la cause.
- **Run démarre mais « file vide »** → le bot ne joint pas `/pull` (vérifie
  `RELAY_PULL_URL` finit bien par `/pull` et que `RELAY_SECRET` est identique des
  deux côtés).
- **403 au webhook** → `RELAY_SECRET` du Worker ≠ `secret_token` du setWebhook.
- **Revenir en arrière** (réactiver le cron polling) → `deleteWebhook` + supprimer
  le secret `RELAY_PULL_URL` + remettre le bloc `schedule: cron` dans le workflow.
