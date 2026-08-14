"""Moteur de viabilité — SPEC V31 §4.4 (I8, I14, I20).

Évalue si un plan mérite d'exister, EN DEVISE, avant émission. Aucune
probabilité estimée n'entre dans un gate (I41) : le critère est l'AVANTAGE
EXIGÉ SUR LE HASARD, entièrement déterministe.

    c   = 2 * (fee + spread/2 + slippage)          coût aller-retour, en points
    p0  = d / (u + d)                              point neutre sans information
    p*  = (d + c) / (u + d)                        seuil d'équilibre
    Δ   = p* - p0 = c / (u + d)                    avantage exigé sur le hasard

``u`` et ``d`` sont des DISTANCES POSITIVES en % depuis ``entry`` (SPEC §1.4),
ce qui rend l'ensemble direction-agnostique.

Sous une marche sans dérive à deux barrières, l'espérance vaut -c pour tout
couple (u, d) : tout geste émis revendique donc implicitement un avantage. V1
rend cette revendication explicite et la borne.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.core import params
from src.core.volatility import k_from_probability


class Verdict(str, Enum):
    VIABLE = "VIABLE"
    NON_VIABLE = "NON_VIABLE"
    NON_EVALUABLE = "NON_EVALUABLE"


class CostConfidence(str, Enum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ViabilityVerdict:
    """SPEC §1.5. ``missing_inputs`` non vide <=> NON_EVALUABLE (I20)."""

    verdict: Verdict
    failed_conditions: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    upside_pct: Optional[float] = None
    downside_pct: Optional[float] = None
    sigma_h_pct: Optional[float] = None
    target_in_sigma: Optional[float] = None
    stop_in_sigma: Optional[float] = None
    round_trip_cost_pct: Optional[float] = None
    cost_confidence: CostConfidence = CostConfidence.UNKNOWN
    p_null: Optional[float] = None
    p_breakeven: Optional[float] = None
    delta_required: Optional[float] = None
    notional_usd: Optional[float] = None
    expected_pnl_usd_net: Optional[float] = None
    sigma_degraded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_viable(self) -> bool:
        """Seul prédicat autorisé pour décider d'une émission (I8, I20)."""
        return self.verdict is Verdict.VIABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "failed_conditions": list(self.failed_conditions),
            "missing_inputs": list(self.missing_inputs),
            "upside_pct": self.upside_pct,
            "downside_pct": self.downside_pct,
            "sigma_h_pct": self.sigma_h_pct,
            "target_in_sigma": self.target_in_sigma,
            "stop_in_sigma": self.stop_in_sigma,
            "round_trip_cost_pct": self.round_trip_cost_pct,
            "cost_confidence": self.cost_confidence.value,
            "p_null": self.p_null,
            "p_breakeven": self.p_breakeven,
            "delta_required": self.delta_required,
            "notional_usd": self.notional_usd,
            "expected_pnl_usd_net": self.expected_pnl_usd_net,
            "sigma_degraded": self.sigma_degraded,
            "notes": list(self.notes),
        }


def non_evaluable(missing: list[str], notes: Optional[list[str]] = None
                  ) -> ViabilityVerdict:
    return ViabilityVerdict(
        verdict=Verdict.NON_EVALUABLE,
        missing_inputs=list(missing),
        notes=list(notes or []),
    )


# ── coût aller-retour ──────────────────────────────────────────────────────

def _select_band(daily_volume_usd: Optional[float]) -> Optional[dict[str, Any]]:
    """Classe l'actif dans une bande de liquidité.

    Le CLASSEMENT est déductible (volume échangé) ; les VALEURS de spread et de
    slippage sont une décision métier portée par ``params.liquidity_bands()``.
    """
    bands = params.liquidity_bands()
    if not bands:
        return None
    vol = daily_volume_usd if isinstance(daily_volume_usd, (int, float)) else 0.0
    eligible = [b for b in bands
                if float(b.get("min_daily_volume_usd") or 0.0) <= vol]
    if not eligible:
        # Volume inférieur à toutes les bandes : on prend la MOINS liquide,
        # c'est-à-dire la plus coûteuse. Jamais l'inverse.
        return min(bands, key=lambda b: float(b.get("min_daily_volume_usd") or 0.0))
    return max(eligible, key=lambda b: float(b.get("min_daily_volume_usd") or 0.0))


def _slippage_pct(band: dict[str, Any], notional_usd: Optional[float]) -> float:
    """Slippage de la bande, éventuellement barémé par notional.

    Si la bande déclare un ``slippage_schedule``, il est appliqué ; sinon la
    valeur plate ``slippage_pct``. Aucun barème n'est inventé.
    """
    schedule = band.get("slippage_schedule")
    if isinstance(schedule, list) and schedule and notional_usd is not None:
        applicable = [s for s in schedule
                      if isinstance(s, dict) and s.get("pct") is not None
                      and float(s.get("max_notional_usd") or 0.0) >= notional_usd]
        if applicable:
            return float(min(applicable,
                             key=lambda s: float(s["max_notional_usd"]))["pct"])
        return float(max(schedule,
                         key=lambda s: float(s.get("max_notional_usd") or 0.0)
                         )["pct"])
    return float(band.get("slippage_pct") or 0.0)


def round_trip_cost(
    *, daily_volume_usd: Optional[float], notional_usd: Optional[float]
) -> tuple[Optional[float], CostConfidence]:
    """Coût aller-retour en POINTS DE POURCENTAGE, ou ``None`` si indéterminable.

    Toutes les recommandations, y compris ``LONG_REDUCE`` (qui vise un rachat),
    utilisent un coût ALLER-RETOUR (SPEC §4.4).
    """
    fee = params.fee_rate()
    band = _select_band(daily_volume_usd)
    if fee is None or band is None:
        return None, CostConfidence.UNKNOWN
    fee_pct = float(fee) * 100.0
    spread_pct = float(band.get("spread_pct") or 0.0)
    slip_pct = _slippage_pct(band, notional_usd)
    cost = 2.0 * (fee_pct + spread_pct / 2.0 + slip_pct)
    conf = (CostConfidence.MEASURED if band.get("measured")
            else CostConfidence.ESTIMATED)
    return cost, conf


# ── référence de matérialité (V3) ──────────────────────────────────────────

def materiality_reference_usd(
    *, ptf_value_usd: Optional[float], daily_ptf_sigma_usd: Optional[float]
) -> tuple[Optional[float], Optional[str]]:
    """Valeur en USD de la référence de matérialité choisie par le métier.

    Trois références nommées, toutes ancrées sur une grandeur du système :
      monthly_budget  — un mois d'apport (profil investisseur)
      ptf_daily_noise — bruit journalier du portefeuille, en USD
      ticket_min      — ticket de référence d'exécution
    Aucune n'est choisie ici : le nom vient de ``params.materiality_reference()``.
    """
    name = params.materiality_reference()
    if name is None:
        return None, None
    if name == "monthly_budget":
        return params.monthly_budget(), name
    if name == "ticket_min":
        return params.ticket_min(), name
    if name == "ptf_daily_noise":
        if isinstance(daily_ptf_sigma_usd, (int, float)) and daily_ptf_sigma_usd > 0:
            return float(daily_ptf_sigma_usd), name
        return None, name
    return None, name


# ── évaluation ─────────────────────────────────────────────────────────────

def evaluate(
    *,
    upside_pct: float,
    downside_pct: float,
    sigma_h_pct: Optional[float],
    sigma_degraded: bool,
    notional_usd: Optional[float],
    tranches: int,
    daily_volume_usd: Optional[float],
    ptf_value_usd: Optional[float],
    daily_ptf_sigma_usd: Optional[float] = None,
) -> ViabilityVerdict:
    """Applique V1 ^ V2 ^ V3 ^ V4 dans l'ordre normatif de la SPEC §4.4.

    ``upside_pct`` et ``downside_pct`` sont des distances POSITIVES en points de
    pourcentage. Le sizing est une ENTRÉE : il n'est jamais recalculé ici (I14).
    """
    missing = params.missing_emission_params()
    notes: list[str] = []

    if sigma_h_pct is None or sigma_h_pct <= 0:
        missing = missing + ["sigma_h"]
    if notional_usd is None:
        missing = missing + ["notional"]
    if missing:
        return non_evaluable(missing, notes)

    u = abs(float(upside_pct))
    d = abs(float(downside_pct))
    if u <= 0 or d <= 0:
        return ViabilityVerdict(
            verdict=Verdict.NON_VIABLE,
            failed_conditions=["V2"],
            upside_pct=u, downside_pct=d, sigma_h_pct=sigma_h_pct,
            notional_usd=notional_usd, sigma_degraded=sigma_degraded,
            notes=["cible ou invalidation nulle — plan incohérent"])

    cost, cost_conf = round_trip_cost(daily_volume_usd=daily_volume_usd,
                                      notional_usd=notional_usd)
    if cost is None:
        return non_evaluable(missing + ["round_trip_cost"], notes)

    width = u + d
    p_null = d / width
    p_breakeven = (d + cost) / width
    delta_required = cost / width

    k2 = k_from_probability(params.p_target_max())
    k2p = k_from_probability(params.p_stop_max())
    if k2 is None or k2p is None:
        return non_evaluable(missing + ["p_target_max", "p_stop_max"], notes)

    target_in_sigma = u / sigma_h_pct
    stop_in_sigma = d / sigma_h_pct

    # ÉCART ASSUMÉ À LA SPEC §4.4 (contradiction interne relevée à
    # l'implémentation) : la SPEC définissait le P&L indicatif à p = 0,5, alors
    # que V1 raisonne sur l'avantage AU-DESSUS de p0. Avec p = 0,5, un plan très
    # asymétrique (u << d) affiche une espérance fortement NÉGATIVE dont la
    # VALEUR ABSOLUE franchissait V3 : plus le plan était mauvais, plus il
    # paraissait « matériel ». Le P&L est donc évalué à l'avantage REVENDIQUÉ,
    # seul point cohérent avec V1 : p = p0 + delta_claimable, et V3 teste une
    # valeur SIGNÉE.
    #
    # Sous la marche sans dérive, p0*u - (1-p0)*d = 0 exactement, d'où la forme
    # close  pnl% = delta_claimable * (u + d) - c  lorsque p reste dans [0, 1].
    # Cette simplification masque toutefois un cas DÉGÉNÉRÉ : quand le point
    # neutre est déjà proche de 1 (cible minuscule, invalidation lointaine),
    # p0 + delta dépasse 1 — l'avantage revendiqué n'est alors pas ÉNONÇABLE, et
    # la forme close en tirerait une espérance confortablement positive. On borne
    # donc p à 1 pour le calcul, ET on rejette explicitement le plan en V1 : une
    # revendication impossible ne doit pas dépendre d'un autre gate pour tomber.
    delta_claimable = params.delta_claimable()
    claim_statable = (p_null + delta_claimable) <= 1.0
    p_claimed = min(1.0, p_null + delta_claimable)
    expected_pnl_pct = p_claimed * u - (1.0 - p_claimed) * d - cost
    expected_pnl_usd = expected_pnl_pct / 100.0 * float(notional_usd)

    failed: list[str] = []

    # V1 — l'avantage exigé sur le hasard doit rester revendicable, ET la
    # revendication elle-même doit être énonçable.
    if delta_required > delta_claimable:
        failed.append("V1")
    elif not claim_statable:
        failed.append("V1")
        notes.append(
            f"avantage revendiqué non énonçable : le point neutre est déjà à "
            f"{p_null * 100:.1f} %")

    # V2 — cible et stop hors du bruit de leur propre horizon.
    if target_in_sigma < k2 or stop_in_sigma < k2p:
        failed.append("V2")

    # V3 — matérialité en devise.
    ref_usd, ref_name = materiality_reference_usd(
        ptf_value_usd=ptf_value_usd, daily_ptf_sigma_usd=daily_ptf_sigma_usd)
    k3 = params.k3()
    if ref_usd is None:
        return non_evaluable(missing + ["materiality_reference_value"],
                             notes + [f"référence « {ref_name} » non résoluble"])
    if expected_pnl_usd < k3 * ref_usd:
        failed.append("V3")

    # V4 — exécutabilité opérationnelle.
    ticket_min = params.ticket_min()
    n_tranches = max(1, int(tranches or 1))
    if notional_usd < ticket_min or (notional_usd / n_tranches) < ticket_min:
        failed.append("V4")

    if sigma_degraded:
        notes.append("sigma estimée sur clôtures (repli) — verdict dégradé")

    return ViabilityVerdict(
        verdict=Verdict.NON_VIABLE if failed else Verdict.VIABLE,
        failed_conditions=failed,
        upside_pct=round(u, 4),
        downside_pct=round(d, 4),
        sigma_h_pct=round(sigma_h_pct, 4),
        target_in_sigma=round(target_in_sigma, 4),
        stop_in_sigma=round(stop_in_sigma, 4),
        round_trip_cost_pct=round(cost, 4),
        cost_confidence=cost_conf,
        p_null=round(p_null, 4),
        p_breakeven=round(p_breakeven, 4),
        delta_required=round(delta_required, 4),
        notional_usd=round(float(notional_usd), 2),
        expected_pnl_usd_net=round(expected_pnl_usd, 4),
        sigma_degraded=sigma_degraded,
        notes=notes,
    )
