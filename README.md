# 🤖 Crypto Analyst Pro · v26

Agent d'analyse crypto personnel et autonome. Analyste **multi-sources à voix
critique** (et non plus résumeur monosource) : il croise jusqu'à 14 sources, un
cerveau IA (Gemini), une mémoire inter-rapports et un tracking de ses propres
prédictions, puis t'envoie par email :

- ☀️ **Rapport du matin** (~08h30 Casablanca) · point d'entrée complet : histoire
  du jour, contexte macro/on-chain, rotation sectorielle, thèses fondées.
- 🌙 **Rapport du soir** (~19h30) · différentiel : ce qui a évolué depuis le matin,
  suivi des recos, setup pour demain.
- 📊 **Bilan hebdo** (dimanche ~11h30) · win rate, leçons, 3 scénarios, cibles LT.

100 % gratuit, hébergé sur **GitHub Actions** (aucun serveur).

---

## Principes de la V2

1. **Score sur 9 signaux pondérés** · technique, volume, on-chain, dérivés,
   rotation, news 24h, social, fondamental, macro. Les commits GitHub pèsent
   ≤ 10 % du raisonnement (fini les "pas de commit → alléger").
2. **Seuils adaptatifs par tier** · BTC/ETH exigent 4+ signaux convergents,
   Tier 1 : 3+, Tier 2-3 : 2+, poussières : jamais de reco ferme.
3. **Confiance → taille d'action** · une confiance < 55 % ne produit jamais de
   reco ferme, seulement de la surveillance.
4. **Mémoire inter-rapports** · le soir complète le matin, l'hebdo agrège la
   semaine (dossier `state/`, commité par les workflows).
5. **Tracking des prédictions** · chaque reco est évaluée sur prix réels ; le win
   rate s'affiche dans les rapports.
6. **Garde-fou factuel** · un `coherence_checker` rétrograde avant envoi toute
   reco mal fondée, corrige les ATH impossibles, refuse les sources vagues.

---

## Architecture

```
src/
├── data_sources/     # 14+ connecteurs (dégradation gracieuse totale)
│   ├── coingecko, coinmarketcap, binance, tradingview, fear_greed
│   ├── cryptopanic (news <24h), reddit, fred, econ_calendar
│   ├── onchain_btc/eth/advanced, coinglass (dérivés)
│   ├── prediction_markets (Polymarket), etf_flows (Farside)
│   ├── telegram_channels (Telethon), youtube / youtube_cpt
├── analytics/        # composite_score (9 signaux), tier_resolver,
│                     #   fundamentals (ATH safe), coherence_checker, narratives
├── ai_brain/         # gemini_client, decision_engine, prompts (persona +
│                     #   morning/evening/weekly)
├── state/            # report_memory (mémoire inter-rapports)
├── tracking/         # prediction_scoring (win rate, leçons)
├── reporting/        # email_html (dispatcher) + templates/*.j2 + email_sender
└── main.py           # orchestrateur : morning / evening / weekly
scripts/              # update_portfolio.py (mise à jour du PTF après un trade)
config/               # portfolio, thresholds, sources, github_repos,
                      #   youtube_channels, telegram_channels
.github/workflows/    # morning, evening, weekly, heartbeat, update_portfolio
state/                # JSON de mémoire (commités automatiquement)
tests/                # analytics, data_sources, full_flow, v2_refactor
```

---

## Démarrage

L'agent tourne dès maintenant avec **Gemini + Gmail**. Les autres sources sont
optionnelles (sans leur clé, elles sont marquées "indisponibles" et n'empêchent
rien). Voir **[MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)** pour le pas-à-pas
complet (secrets, Telegram, tests).

```bash
pip install -r requirements.txt
cp .env.example .env   # remplir les clés
python -m src.main morning     # test immédiat
pytest -q                      # suite de tests
```

---

## Coût

**0 €.** Tous les services ont un free tier suffisant, GitHub Actions est gratuit
pour les dépôts publics.

## Avertissement

Analyse **informative**, pas un conseil en investissement. Fais toujours tes
propres recherches.
