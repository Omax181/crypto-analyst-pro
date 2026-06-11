"""Constructeur du prompt pour le rapport du soir (différentiel).

Le soir COMPLÈTE le matin : il ne répète pas macro/on-chain/rotation déjà
couverts. Il lit le rapport du matin (mémoire) et produit les deltas.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai_brain.prompts.analyst_persona import (
    ANALYST_PERSONA,
    DISCLAIMER,
    OUTPUT_CONTRACT,
)

_EVENING_SCHEMA = """
{
  "header": {"date","time_casablanca","hours_since_morning (int — déjà fourni dans data, recopie-le)"},
  "portfolio_snapshot": {"value_usd","change_since_morning_pct"},
  "delta_summary": ["3 bullets MAX, denses (1-2 phrases chacun) : LES 3 choses à retenir de la journée. Factuel, chiffré, pas de blabla. Ce sont les conclusions, pas une analyse."],
  "market_changes": [{"status (invalidated|confirmed|unchanged|new)","description (1-2 phrases, le DELTA vs ce matin uniquement — pas une reformulation du matin)","source (nom réel + heure, ex. 'Financial Times 12h48')"}],
  "news_today": [{"title (titre court)","source (nom réel)","time (ex. '12h48')","impact (1 phrase : effet sur le PTF/marché)","status (intégré|actionnable)"}],
  "levels_tonight": [{"asset (BTC/ETH/DXY/… )","level (niveau PRÉCIS ex. '63 000 $')","type (support|resistance|critical|threshold)","trigger (ce qui se passe si cassé/atteint, ACTIONNABLE ex. 'sous 62k → alléger, capitulation probable')"}],
  "tomorrow_checklist": {
    "calendar": "string — événements macro réels des 48h (RECOPIE data.tomorrow_macro_events). Si vide : 'Pas d'événement macro majeur dans les 48h.'",
    "checks": "string — 2-3 vérifs CONCRÈTES liées aux mouvements/recos du jour (ex. 'IMX tient son +12% overnight ? · DXY reste sous 100 ?'). Pas de généralité.",
    "scenario": "string — 1 phrase TRANCHÉE : le scénario le plus probable + sa condition (ex. 'consolidation 63k-65k si DXY < 100'). Jamais 'ça dépend'.",
    "invalidation": "string — 1 condition CHIFFRÉE qui ferait basculer l'analyse (ex. 'BTC sous 62k + VIX > 25 = risk-off confirmé'). Cohérente avec levels_tonight."
  },
  "blind_spots": "string — 1 phrase MAX si un angle mort est critique (ex. flux ETF indisponibles), sinon chaîne vide.",
  "footer": {"next_morning_time (ex. '08h30')"}
}
"""


def build_evening_prompt(
    *, timestamp: str, data: dict[str, Any], morning_state: dict[str, Any]
) -> str:
    """Construit le prompt du rapport du soir.

    Args:
        timestamp: horodatage Casablanca.
        data: données collectées (légères : deltas, prix, news <24h).
        morning_state: contenu du rapport du matin (référence obligatoire).

    Returns:
        Prompt complet pour ``generate_json``.
    """
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    morning_json = json.dumps(morning_state, ensure_ascii=False, default=str)[:6000]
    return f"""{ANALYST_PERSONA}

CONTEXTE · {timestamp}. RAPPORT DU SOIR · complément différentiel du matin.

RAPPORT DU MATIN (référence obligatoire, RÈGLE 7 — NE PAS le répéter) :
{morning_json}

DONNÉES DU SOIR (deltas depuis le matin) :
{data_json}

INSTRUCTIONS :
0. RÈGLE DES CHIFFRES (CRITIQUE). Tout nombre (prix, %, niveau, delta) doit être
   copié VERBATIM depuis le JSON fourni — jamais calculé, extrapolé, mémorisé
   d'ailleurs, ni inventé. Donnée absente = "n/d" ou description sans chiffre. Un
   prix faux affiché en confiance est l'erreur la plus grave de ce rapport.
1. delta_summary : EXACTEMENT 3 puces max, denses et scannables (1-2 phrases
   chacune). CE SONT LES 3 CHOSES À RETENIR de la journée — des conclusions
   chiffrées, pas une analyse qui se développe. PAS de paragraphe.
2. market_changes (« Ce qui a évolué côté marché ») : 4 à 6 items MAX. Chaque
   item = un statut (invalidated ✗ / confirmed ✓ / unchanged → / new ↑) + 1-2
   phrases décrivant UNIQUEMENT le DELTA vs ce matin (jamais une reformulation du
   matin) + la source réelle avec son heure. Inclus aussi les évolutions marché
   autonomes (DXY, ETF, divergence indices) qui sont de la valeur ajoutée —
   v14.1 : dont l'INTERNATIONAL (clôtures Nikkei/Stoxx, BCE/BoJ via
   data.evening_macro) et les actions liées crypto en séance (data.equity_quotes :
   NVDA pour le bloc IA RENDER/TAO/FET, COIN/MSTR/MARA comme proxys BTC) quand
   le mouvement est significatif et CHANGE la lecture du matin. Le
   régime macro du matin (macro_regime_readout du RAPPORT DU MATIN) : s'il a
   bougé, mets-le ICI en 1 ligne (« → INCHANGÉ · régime transition confirmé,
   DXY stable »). Pas de bloc régime séparé.
3. news_today (« Ce qui est tombé depuis ce matin ») : 3 à 5 news MAX, ultra
   compactes. Chaque news = titre court + source réelle + heure + 1 phrase
   d'impact + statut (intégré / actionnable). UNIQUEMENT les news qui CHANGENT
   quelque chose vs le matin. Ne répète pas une news déjà couverte dans
   market_changes. Pas de % de confiance (inutile, le matin a déjà trié).
4. levels_tonight (« Niveaux à surveiller cette nuit ») — bloc le PLUS
   actionnable : 4 à 8 niveaux PRÉCIS. Inclus OBLIGATOIREMENT BTC (≥1 support +
   ≥1 résistance), ETH (idem) et DXY. AJOUTE les positions ayant bougé >8% dans
   la journée (vois data.daily_pnl.top_movers) avec un niveau de TP/résistance.
   Pour chaque niveau : type (support/resistance/critical/threshold) + trigger
   ACTIONNABLE (« sous 62k → alléger »), jamais « à surveiller ». Niveaux ancrés
   techniquement (supports testés, Fibonacci, max pain), pas de ronds arbitraires.
5. tomorrow_checklist (« Demain matin ») — objet à 4 champs :
   - calendar : RECOPIE EXCLUSIVEMENT data.tomorrow_macro_events (dates RÉELLES
     FRED). N'invente AUCUN événement/heure/consensus. Liste vide → « Pas
     d'événement macro majeur dans les 48h. »
   - checks : 2-3 vérifs CONCRÈTES dérivées du jour (position >8% → persistance
     overnight ; seuil macro → tient-il ?). Pas de généralité.
   - scenario : 1 phrase TRANCHÉE (scénario probable + condition). Jamais « ça
     dépend » ni « consolidation dans un contexte incertain ».
   - invalidation : 1 condition CHIFFRÉE, cohérente avec levels_tonight.
6. blind_spots : 1 phrase MAX si un angle mort est critique (ex. flux ETF
   indisponibles), sinon chaîne vide. Si MVRV/on-chain CoinMetrics manque, NE le
   répète pas en boucle (1 mention max).
7. NE PRODUIS PAS de bilan des recos : il est calculé par Python (data fourni en
   aval) et rendu automatiquement, 1 ligne par actif. N'émets donc AUCUN champ
   reco_evolution / reco bilan dans ton JSON.
8. RÈGLE CASH : le portefeuille est 100% crypto, ZÉRO USDC. N'écris jamais
   « rester liquide en USDC », « renforcer USDC » ni « déployer du cash ». Pour
   financer une entrée : alléger une position existante.
9. NOMS DE SOURCES — libellé public TOUJOURS : « CoinGecko » (pas prices_now),
   « Fear & Greed Index » (pas fear_greed), « Yahoo Finance » (pas evening_macro),
   « Farside Investors » (pas etf_flows), « Rapport matin » (pas morning_report).
   Aucun identifiant Python dans le texte rendu.
10. PAS DE SOURCE PLACEHOLDER. Une mention « Source · X » exige un nom RÉEL et un
   horodatage DISTINCT du moment du rapport (pas tout à 20h00). Sinon, omets la
   source entièrement plutôt que d'en inventer une.
NE répète PAS le contexte macro/on-chain/rotation déjà donné le matin.
Le mail tombe à 20h Casablanca = 14h US = MI-SÉANCE américaine (pas la clôture).

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_EVENING_SCHEMA}
"""
