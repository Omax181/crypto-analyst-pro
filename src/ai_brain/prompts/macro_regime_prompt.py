"""Prompt PASSE 1 (chaînage V10) : évaluation du régime macro.

Première passe d'un raisonnement en 2 étapes. Reçoit UNIQUEMENT les données
macro (compactes) et produit un verdict de régime de marché structuré, qui
sera ensuite injecté dans la passe 2 (analyse par actif) pour contraindre les
thèses. Découpe le raisonnement « d'abord le régime, puis les actifs » au lieu
d'un prompt monolithique.

Entrées volontairement minimales (économie de tokens) : contexte macro chiffré,
corrélations BTC↔macro, derniers chiffres + consensus marché, fear & greed.
"""

from __future__ import annotations

import json
from typing import Any

_REGIME_SCHEMA = """
{
  "regime": "risk-on | risk-off | neutre | transition",
  "confidence_pct": 0-100,
  "drivers": ["2-4 moteurs chiffrés, ex. 'DXY 104 en hausse (Δ+0.3)', 'courbe 10Y-2Y inversée -0.2', 'corr BTC/S&P +0.62'"],
  "crypto_bias": "favorable | défavorable | neutre",
  "rate_path_read": "1 phrase : lecture de la trajectoire des taux Fed à partir des derniers chiffres macro + consensus Polymarket (raisonnement causal chômage/inflation → taux → crypto)",
  "key_risks": ["1-3 risques macro à surveiller aujourd'hui"],
  "synthesis": "2-3 phrases : synthèse du régime qui croise dollar/actions/or/VIX/courbe/corrélations, prête à cadrer l'analyse par actif"
}
"""


def build_macro_regime_prompt(*, timestamp: str, data: dict[str, Any]) -> str:
    """Construit le prompt compact de la passe 1 (régime macro).

    Args:
        timestamp: horodatage Casablanca.
        data: dict de données collectées (on n'en extrait que le macro).

    Returns:
        Prompt prêt pour ``generate_json`` (sortie = JSON régime).
    """
    digest = data.get("analytics_digest") or {}
    macro_inputs = {
        "macro_context": data.get("macro_context") or {},
        "correlations_30j": digest.get("macro_correlations") or "n/d",
        "calendrier_et_consensus": digest.get("macro_calendar") or "n/d",
        "fear_greed": data.get("fear_greed") or {},
    }
    macro_json = json.dumps(macro_inputs, ensure_ascii=False, default=str)

    return f"""Tu es un stratégiste macro crypto. PASSE 1/2 · {timestamp}.

OBJECTIF : établir le RÉGIME DE MARCHÉ macro AVANT l'analyse par actif. Tu ne
produis PAS de recommandation par crypto ici — seulement le cadre macro qui
contraindra la passe 2.

DONNÉES MACRO (chiffres à copier verbatim, n'invente aucune valeur absente) :
{macro_json}

MÉTHODE :
1. Lis le dollar (DXY), les actions US (S&P/Nasdaq), l'or, le VIX, la courbe des
   taux (10Y-2Y) et les corrélations 30j BTC↔macro fournies. v14.1 — intègre
   AUSSI l'international quand fourni : Nikkei/Stoxx 50/DAX (appétit risque
   Asie/Europe), taux de dépôt BCE (liquidité euro), taux BoJ (carry trade yen :
   un RELÈVEMENT BoJ = vent contraire majeur sur tous les actifs risqués).
2. Raisonnement causal sur les taux : à partir des derniers chiffres macro
   (inflation, chômage) et du consensus Polymarket, déduis la trajectoire Fed
   probable et son implication crypto (ex. chômage ↑ → biais baisse de taux →
   favorable ; CPI surprise ↑ → Fed hawkish → pression baissière).
3. Tranche : régime risk-on / risk-off / neutre / transition, avec une confiance.
4. Auto-critique implicite : si les signaux se contredisent (ex. VIX calme mais
   courbe inversée), dis-le dans la synthèse plutôt que de forcer un verdict net.

Réponds UNIQUEMENT avec un objet JSON valide (pas de texte hors JSON, pas de
backticks), selon ce schéma :
{_REGIME_SCHEMA}
"""
