# 🤖 Crypto Analyst Pro

Agent d'analyse crypto personnel et autonome. Il t'envoie **2 rapports par jour**
par email (matin + soir) plus des **alertes intra-day** en cas de mouvement
majeur, en croisant **14 sources de données** gratuites et un **cerveau IA**
(Gemini) qui rédige une analyse d'analyste senior — pas un perroquet de chiffres.

100 % gratuit, hébergé sur **GitHub Actions** (aucun serveur à gérer).

---

## Ce qu'il fait

- **Rapport du matin** (~08:45 Casablanca) : état du marché, macro du jour,
  positions qui méritent attention, santé des projets.
- **Rapport du soir** (~19:45 Casablanca) : delta de la journée + setup pour demain.
- **Alertes intra-day** (toutes les 15 min) : spike de prix > 30 % en 4 h, ou
  news critique (hack, exploit, delisting, depeg…) sur une position détenue.
- **Rapports adaptatifs** : courts les jours calmes, développés les jours agités.
- **Silence sur l'inactif** : il ne parle que de ce qui bouge vraiment.

## Philosophie d'analyse

L'agent ne donne un **avis tranché** que lorsque 3 conditions sont réunies :
plusieurs signaux indépendants convergent, les chiffres appuient, et les
conditions d'invalidation sont nommées. Il croise en permanence
**macro ↔ micro ↔ géopolitique** et fait des liens historiques chiffrés.

---

## Les 14 sources

| Catégorie | Sources |
|-----------|---------|
| Prix / marché | CoinGecko (primaire), CoinMarketCap (cross-check), Binance (OHLCV) |
| Technique | TradingView (multi-timeframe) |
| Sentiment | Fear & Greed Index, CryptoPanic (news), Reddit |
| On-chain | blockchain.info (BTC), Etherscan (ETH) |
| Fondamental | GitHub (activité dev = santé projet) |
| Macro | FRED (Fed, DXY, 10Y, VIX, CPI…), Trading Economics (calendrier) |
| Contexte | YouTube (transcripts synthétisés), Gemini + Google Search (géopolitique) |

Chaque source est **isolée** : une panne d'une API ne casse pas le rapport
(le champ concerné est simplement marqué indisponible).

---

## Architecture

```
src/
├── data_sources/     # 14 connecteurs (dégradation gracieuse)
├── analytics/        # technique, score composite, santé projet,
│                     #   patterns, narratives, cas historiques
├── ai_brain/         # client Gemini + moteur de décision + prompts
├── reporting/        # filtre de contenu, style adaptatif, template HTML, envoi
├── utils/            # config, cache TTL, logs
└── main.py           # orchestrateur (modes morning / evening / intraday)
config/               # portfolio.yaml + sources/thresholds/youtube
.github/workflows/    # 4 workflows (matin, soir, intraday, heartbeat)
tests/                # tests analytics / sources / flux complet
```

**Pipeline** : collecte (14 sources) → analyse (scores 0-100 par dimension) →
filtrage (quoi mentionner) → évaluation de volatilité (style du rapport) →
Gemini (rédaction JSON structurée) → rendu HTML → email.

---

## Installation rapide (local)

```bash
git clone <ton-repo>
cd crypto-analyst-pro
pip install -r requirements.txt
cp .env.example .env        # puis remplis tes clés
python -m src.main morning  # test immédiat
```

Pour le déploiement automatisé sur GitHub Actions (recommandé, gratuit),
suis le guide pas-à-pas : **[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## Configuration

- **`config/portfolio.yaml`** — tes positions, réparties en 4 tiers. Chaque
  actif a un `tier`, une `value_usd` et des `notes`. L'USDC est marqué
  `role: cash_reserve` (mentionné uniquement si une opportunité majeure surgit).
- **`config/thresholds.yaml`** — tous les seuils : déclenchement par tier
  (T1 : 5 %, T2 : 5 %, T3 : 10 %, T4 : 30 %), alertes intra-day, pondérations
  du score composite, TTL de cache, style de rapport.
- **`config/sources.yaml`** — IDs et endpoints des sources.
- **`config/youtube_channels.yaml`** — chaînes de référence (synthèse anonymisée).

### Mettre à jour le portfolio en langage naturel

```bash
python -m src.utils.portfolio_loader --update "J'ai vendu la moitié de mon ETH et acheté 50$ de LINK"
python -m src.utils.portfolio_loader --show
```

(nécessite `GEMINI_API_KEY`).

---

## Choix du modèle Gemini

Par défaut : **`gemini-2.5-flash`** (free tier fiable, ~1500 requêtes/jour,
contexte 1M tokens). Le spec d'origine visait `gemini-2.5-pro`, mais celui-ci
est désormais restreint au tier payant ou à un quota très bas selon la région.
Pour basculer (si tu as un tier payant), change `GEMINI_MODEL=gemini-2.5-pro`.

---

## Coût

**0 €.** Tous les services utilisés ont un free tier suffisant pour 2 rapports
quotidiens + alertes, et GitHub Actions est gratuit pour les dépôts publics.

---

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

---

## Avertissement

Cet agent produit une **analyse informative**, pas un conseil en investissement.
Fais toujours tes propres recherches avant toute décision.
