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
  "delta_highlights": [{"headline (GRAS, 12-18 mots : quoi + chiffre clé)","detail (1 ligne ≤20 mots, factuel)"}],
  "intraday_news": [{"title","source","timestamp (ex. '14h32')","impact (1 ligne : lien court sur le PTF, ex. 'renforce le bear macro CT')"}],
  "reco_evolution": [{
    "asset","action (RENFORCER/ALLÉGER/SURVEILLER… telle qu'émise le matin)",
    "status_label (Validation en cours / Confirmation early / Invalidation early / INVALIDÉE / CIBLE TOUCHÉE / Inchangé / Trigger touché)",
    "status_bg (hex)","status_color (hex)",
    "move_since_morning (ex. '+11.3% · +0.0098$')",
    "commentary (1 phrase ≤25 mots justifiant le statut)"
  }],
  "reco_evolution_empty_reason": "string (REQUIS si reco_evolution vide)",
  "market_changes": [{"tag (✓ CONFIRMÉ / → S'ESSOUFFLE / ✗ INVALIDÉ / ↑ NOUVEAU / → INCHANGÉ)","tag_color (hex)","text (1 phrase : signal d'origine du matin → évolution du jour → verdict ; OU évolution marché autonome DXY/ETF)"}],
  "overnight_events": [{"time (ex. '21h GMT')","time_bg (hex)","time_color (hex)","title","detail"}],
  "tomorrow_setup": {
    "checks": ["string — DÉRIVÉ des événements du jour (gros mouvements >5%, signaux émergents, recos à un seuil critique). Génériques SEULEMENT si rien de spécifique."],
    "actions_tonight": "string (PROSE : actions ANCRÉES dans tes positions actives, leurs niveaux clés du jour, et les recos du matin — ordres limite à poser, allègements en cours)"
  },
  "tomorrow_macro_events": [{"label","date","when (demain/aujourd'hui)","source"}],
  "blind_spots": "string",
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
1. "Le delta du jour" : EXACTEMENT 3 puces max, SCANNABLES. Chaque puce =
   headline en gras (12-18 mots, quoi + chiffre clé) + 1 ligne d'explication
   (≤20 mots). PAS de paragraphe de 4-5 lignes. Condense, n'développe pas.
2. "Évolution des recos du matin" : pour CHAQUE reco émise le matin (parcours
   thesis_of_the_day et active_recommendations du RAPPORT DU MATIN), produis UNE
   ligne reco_evolution. AUCUNE reco du matin ne doit disparaître. Pour chacune :
   statut d'évolution, mouvement de prix depuis le matin (move_since_morning, %
   ET valeur), et 1 phrase ≤25 mots justifiant. Si trigger d'invalidation touché
   → status "INVALIDÉE" (rouge). Si cible CT atteinte → "CIBLE TOUCHÉE".
3. "Ce qui a évolué côté marché" : pour chaque signal majeur du matin (rotation
   sectorielle, on-chain, news macro), une ligne de SUIVI : signal d'origine →
   évolution du jour → verdict (✓ confirmé / → s'essouffle / ✗ invalidé).
   Conserve AUSSI les évolutions marché autonomes (DXY/USD, ETF flows) non liées
   au matin — c'est de la valeur ajoutée. Uniquement le NOUVEAU.
4. "À surveiller / demain matin" :
   - overnight_events : événements session asiatique, discours US tardifs,
     niveaux techniques à surveiller cette nuit. Vide si rien.
   - tomorrow_setup.checks : DÉRIVÉS des événements du jour (position ayant bougé
     >5% → check persistance overnight ; signal émergent → check confirmation ;
     reco à un seuil critique → check du seuil). Génériques SEULEMENT si rien de
     spécifique ne ressort.
   - tomorrow_setup.actions_tonight : ANCRÉES dans tes positions actives et leurs
     niveaux clés du jour, et les recos du matin (ordres limite à poser,
     allègements en cours). PAS d'actions génériques déconnectées du jour.
5. Angles morts : sources manquantes / incertitudes du jour.
5bis. ÉVÉNEMENTS MACRO DEMAIN — recopie EXCLUSIVEMENT data.tomorrow_macro_events
   (dates RÉELLES issues de FRED). N'INVENTE AUCUN événement, aucune heure, aucun
   consensus : si la liste est vide, ne mets rien (pas d'ISM/PMI improvisés). Le
   P&L par position en $ (24h) est déjà calculé dans data.daily_pnl.top_movers
   (champ pnl_usd) — ne le recalcule pas, le rendu l'affiche tel quel.
6. NOMS DE SOURCES — utilise TOUJOURS le libellé public :
   "CoinGecko" (pas "prices_now"), "Fear & Greed Index" (pas "fear_greed"),
   "Yahoo Finance" (pas "evening_macro"), "Farside Investors" (pas "etf_flows"),
   "Rapport matin" (pas "morning_report"), "Blockchain.com" (pas "btc_network"),
   "DeFiLlama" (pas "stablecoin_supply"). Les identifiants Python ne doivent
   JAMAIS apparaître dans le texte rendu.
7. PAS DE SOURCE PLACEHOLDER. N'écris une ligne "Source · X" que si tu as un
   nom de source RÉEL et un horodatage DISTINCT du moment du rapport (pas tous
   à 20h00). Pas de "Source · Analyse technique 20h00" générique — mieux vaut
   PAS de source du tout que fausse source. Si tu n'as pas d'horodatage réel
   distinct, omets entièrement la mention de source.
8. DISTINGUER "rien à dire" vs "source indisponible". Si une liste est vide
   parce qu'il n'y a rien de notable → écris dans *_empty_reason :
   "Aucune évolution significative depuis ce matin." Si c'est parce qu'une
   source est down → écris : "Source indisponible ce soir · [nom source] ·
   signalé dans les angles morts." Ne mélange pas les deux cas.
NE répète PAS le contexte macro/on-chain/rotation déjà donné le matin.
Le mail tombe à 20h Casablanca = 14h US = MI-SÉANCE américaine (pas la clôture).

{OUTPUT_CONTRACT}
Disclaimer footer : "{DISCLAIMER}"

SCHÉMA JSON ATTENDU :
{_EVENING_SCHEMA}
"""
