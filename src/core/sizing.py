"""Sizing — AUTORITÉ UNIQUE (SPEC V31 §4.3, I14, I18, I21).

Flux STRICTEMENT unidirectionnel :

    règles de risque ──► notional ──► viabilité ──► verdict
                         (AUCUNE flèche retour)

Le notional n'est JAMAIS ajusté pour satisfaire une condition de viabilité.
Si une opportunité solide échoue en matérialité parce que le plafond force une
taille minuscule, la sortie est « non actionnable à ton allocation actuelle »,
jamais « augmente la taille » : l'inverse reviendrait à dimensionner en
fonction de l'espoir de gain, c'est-à-dire à supprimer la gestion du risque.

La v30 comportait DEUX autorités contradictoires : ``suggest_sizing`` (1-2 %
en dur) et ``thresholds.yaml:confidence_to_action`` (2-10 % selon la confiance
LLM), soit un écart de 4 à 6x. Les deux sont supprimées au profit de celle-ci.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.core import params

# Plafonds de concentration par classe d'actif — règle produit HÉRITÉE (v27),
# jamais remise en cause par l'audit. Elle est ici parce que la SPEC §4.3 exige
# « plafond de concentration par classe d'actif » comme entrée du sizing.
CONCENTRATION_CAP_CORE_PCT = 20.0
CONCENTRATION_CAP_SATELLITE_PCT = 12.0


@dataclass(frozen=True)
class Sizing:
    """Résultat du dimensionnement. ``notional_usd`` None => non dimensionnable."""

    pct_ptf: Optional[float]
    notional_usd: Optional[float]
    ptf_value_at_issue: Optional[float]
    budget_remaining_at_issue: Optional[float]
    binding_constraint: str          # concentration | budget | none
    weight_before_pct: Optional[float] = None
    weight_after_pct: Optional[float] = None
    reason: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.notional_usd is not None and self.notional_usd > 0

    def to_dict(self) -> dict:
        return {
            "pct_ptf": self.pct_ptf,
            "notional_usd": self.notional_usd,
            "ptf_value_at_issue": self.ptf_value_at_issue,
            "budget_remaining_at_issue": self.budget_remaining_at_issue,
            "binding_constraint": self.binding_constraint,
            "weight_before_pct": self.weight_before_pct,
            "weight_after_pct": self.weight_after_pct,
            "reason": self.reason,
        }


def cap_for(is_core: bool) -> float:
    return CONCENTRATION_CAP_CORE_PCT if is_core else CONCENTRATION_CAP_SATELLITE_PCT


def compute_increase(
    *,
    is_core: bool,
    current_weight_pct: Optional[float],
    ptf_value_usd: Optional[float],
    budget_consumed_usd: float,
) -> Sizing:
    """Taille d'un renfort : le plus GRAND geste que les règles de risque permettent.

    Entrées EXCLUSIVES (SPEC §4.3) :
      1. plafond de concentration et exposition existante ;
      2. budget de recommandation restant sur la période ;
      3. ticket minimum (appliqué en aval par V4, pas ici).

    La combinaison est le minimum des deux contraintes : c'est la seule qui les
    utilise toutes sans introduire de règle de cadencement non spécifiée.
    """
    budget = params.monthly_budget()
    if budget is None or ptf_value_usd is None or ptf_value_usd <= 0:
        return Sizing(None, None, ptf_value_usd, None, "none",
                      reason="budget mensuel ou valeur du portefeuille absent")

    remaining_budget = max(0.0, float(budget) - float(budget_consumed_usd))
    if params.budget_rollover():
        # Le report éventuel est porté par le budget déclaré ; aucune règle
        # supplémentaire n'est inventée ici.
        pass

    cap = cap_for(is_core)
    weight = float(current_weight_pct or 0.0)
    headroom_pct = max(0.0, cap - weight)
    headroom_usd = headroom_pct / 100.0 * float(ptf_value_usd)

    if headroom_usd <= 0:
        return Sizing(0.0, 0.0, ptf_value_usd, remaining_budget, "concentration",
                      weight_before_pct=round(weight, 1),
                      weight_after_pct=round(weight, 1),
                      reason=f"plafond {cap:.0f}% atteint ({weight:.1f}% du PTF)")
    if remaining_budget <= 0:
        return Sizing(0.0, 0.0, ptf_value_usd, 0.0, "budget",
                      weight_before_pct=round(weight, 1),
                      weight_after_pct=round(weight, 1),
                      reason="budget de recommandation du mois épuisé")

    notional = min(headroom_usd, remaining_budget)
    binding = "concentration" if headroom_usd <= remaining_budget else "budget"
    pct = notional / float(ptf_value_usd) * 100.0
    return Sizing(
        pct_ptf=round(pct, 2),
        notional_usd=round(notional, 2),
        ptf_value_at_issue=round(float(ptf_value_usd), 2),
        budget_remaining_at_issue=round(remaining_budget, 2),
        binding_constraint=binding,
        weight_before_pct=round(weight, 1),
        weight_after_pct=round(weight + pct, 1),
    )


def compute_reduce(
    *,
    position_value_usd: Optional[float],
    trim_pct: float,
    ptf_value_usd: Optional[float],
) -> Sizing:
    """Taille d'un allègement AVEC rachat visé (donc une recommandation).

    Un allègement SANS rachat relève de la réduction de risque ou de l'hygiène
    (SPEC §5.4) et ne passe pas par le carnet.
    """
    if position_value_usd is None or position_value_usd <= 0:
        return Sizing(None, None, ptf_value_usd, None, "none",
                      reason="valeur de position inconnue")
    notional = float(position_value_usd) * float(trim_pct) / 100.0
    pct = (notional / float(ptf_value_usd) * 100.0) if ptf_value_usd else None
    return Sizing(
        pct_ptf=round(pct, 2) if pct is not None else None,
        notional_usd=round(notional, 2),
        ptf_value_at_issue=round(float(ptf_value_usd), 2) if ptf_value_usd else None,
        budget_remaining_at_issue=None,
        binding_constraint="position",
    )
