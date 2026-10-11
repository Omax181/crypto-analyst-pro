"""Ce que le modèle de langage VOIT des données — audit zero-trust 02/10.

Le modèle recevait, pour chacun des 23 actifs éligibles, le plan V30
(``asset_plan`` : invalidation, cible 30 j, cible de cycle « Fibonacci 0,618 →
ATH », R:R, EV à probabilité heuristique p↑, scénarios bull/base/bear
pondérés, zone d'accumulation, échelle DCA), les cibles et le stop suggéré des
projections (``short_term_30d``, ``short_term_30d_bear``, ``long_term_6_12m``,
``stop_suggestion``), le prior de probabilités des scénarios hebdo
(``scenario_scaffold.prior``) et les recos V30 avec leur confiance — et, au
matin, la consigne de « citer » ce plan. Omar a écarté ces critères : le
système ne les publie plus, et aucune garde de prose ne retirait une phrase
qui les citait sans verbe de geste.

Un nombre absent des données ne peut pas être cité (règle des chiffres de
chaque prompt) : on les retire de ce que le modèle lit. Les données
DESCRIPTIVES (niveaux techniques, volatilité, on-chain, valorisation, décisions
du moteur) restent intactes. Les données elles-mêmes ne sont pas modifiées :
le code déterministe continue de lire ce qu'il lit.
"""

from __future__ import annotations

from typing import Any

# Clés retirées partout où elles apparaissent.
CLES_V30 = frozenset({
    "asset_plan",            # plan V30 complet (R:R, EV, p↑, scénarios, DCA…)
    "short_term_30d",        # cible 30 j haussière des projections
    "short_term_30d_bear",   # cible 30 j baissière
    "long_term_6_12m",       # « retracement 0,382 → retour ATH »
    "stop_suggestion",       # stop de prix suggéré
    "prior",                 # probabilités a priori des scénarios hebdo
    "confidence",            # confiance du modèle (recos V30, thèses)
    "prev_confidence",
})


def _niveaux(reco: dict[str, Any]) -> dict[str, Any]:
    """Reco : niveaux du mauvais côté tus, origine explicite."""
    from src.tracking.prediction_scoring import niveaux_publiables
    cible, stop = niveaux_publiables(reco)
    out = dict(reco)
    for cle in ("ct_target", "target_price"):
        if cle in out:
            out[cle] = cible
    if "stop_loss" in out:
        out["stop_loss"] = stop
    out["origine"] = "moteur" if reco.get("engine") else "reco V30 héritée"
    return out


def _est_reco(n: dict[str, Any]) -> bool:
    return (bool(n.get("action")) and "entry_price" in n
            and any(k in n for k in ("stop_loss", "ct_target", "target_price")))


def vue_modele(node: Any) -> Any:
    """Copie de ``node`` sans les clés V30, recos assainies."""
    if isinstance(node, dict):
        src = _niveaux(node) if _est_reco(node) else node
        return {k: vue_modele(v) for k, v in src.items() if k not in CLES_V30}
    if isinstance(node, list):
        return [vue_modele(x) for x in node]
    return node
