"""Construction de plan — SPEC V31 §2.1 étapes 5-8.

Assemble horizon -> niveaux -> sigma -> cible/stop -> sizing -> viabilité, dans
l'ordre normatif et sans retour arrière. Produit un ``Candidate`` porteur de
son verdict : il n'émet pas, il ne persiste pas, il ne rend pas.

Aucun plan DCA n'est produit : la v30 plaçait la 3e tranche 0,5 % au-dessus de
l'invalidation (« achète encore ici » / « ici la thèse est morte »). V31 émet
un geste unique ; ``tranches`` vaut donc 1 et V4 se réduit au ticket minimum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.core import params, sizing as sizing_mod, viability
from src.core.book import ContractValidityError, Direction, validate_contract
from src.core.horizon import Horizon, HorizonDecision, determine_from_scoring
from src.core.levels import LevelSet, compute as compute_levels, select_stop, select_target
from src.core.volatility import SigmaEstimate, daily_sigma, k_from_probability, sigma_h

TRANCHES = 1


@dataclass
class Candidate:
    """Plan évalué. ``emittable`` <=> verdict VIABLE et contrat valide."""

    asset: str
    direction: Direction
    horizon: Optional[Horizon]
    entry: Optional[float] = None
    target: Optional[float] = None
    stop: Optional[float] = None
    target_basis: Optional[str] = None
    stop_basis: Optional[str] = None
    sigma_h_pct: Optional[float] = None
    sigma_estimator: Optional[str] = None
    sigma_degraded: bool = False
    # Motif EXACT de la dégradation : « estimée sur clôtures » et « fenêtre non
    # couverte » sont deux causes distinctes. Les confondre dans le bandeau
    # afficherait une raison fausse au lecteur.
    sigma_reason: Optional[str] = None
    levels: Optional[LevelSet] = None
    sizing: Optional[sizing_mod.Sizing] = None
    verdict: Optional[viability.ViabilityVerdict] = None
    blocked_reason: Optional[str] = None

    @property
    def emittable(self) -> bool:
        return (self.blocked_reason is None
                and self.verdict is not None and self.verdict.is_viable
                and self.entry is not None and self.target is not None
                and self.stop is not None
                and self.sizing is not None and self.sizing.available)

    @property
    def net_ev_usd(self) -> float:
        """Clé de tri des candidats viables (SPEC §4.5)."""
        if self.verdict and self.verdict.expected_pnl_usd_net is not None:
            return float(self.verdict.expected_pnl_usd_net)
        return float("-inf")

    def rejection_summary(self) -> str:
        """Motif CHIFFRÉ, destiné à l'affichage (SPEC §4.4, jamais silencieux)."""
        if self.blocked_reason:
            return self.blocked_reason
        v = self.verdict
        if v is None:
            return "plan non évalué"
        if v.is_viable:
            return "viable"
        if v.verdict is viability.Verdict.NON_EVALUABLE:
            # Les NOTES portent le motif CHIFFRÉ (« 130 bougies < 365 exigées »)
            # là où ``missing_inputs`` ne porte qu'un nom technique. Les taire
            # rendrait le rejet à moitié muet — le lecteur saurait QUOI manque,
            # jamais COMBIEN.
            detail = " ; ".join(v.notes) if v.notes else ""
            base = ("non évaluable — entrées manquantes : "
                    + ", ".join(v.missing_inputs))
            return f"{base} ({detail})" if detail else base
        bits: list[str] = []
        if "V1" in v.failed_conditions and v.delta_required is not None:
            bits.append(f"exige {v.delta_required * 100:.1f} points d'avantage "
                        f"sur le hasard")
        if "V2" in v.failed_conditions:
            if v.target_in_sigma is not None:
                bits.append(f"cible à {v.target_in_sigma:.2f} sigma")
            if v.stop_in_sigma is not None:
                bits.append(f"stop à {v.stop_in_sigma:.2f} sigma")
        if "V3" in v.failed_conditions and v.expected_pnl_usd_net is not None:
            bits.append(f"espérance nette {v.expected_pnl_usd_net:.2f} $")
        if "V4" in v.failed_conditions and v.notional_usd is not None:
            bits.append(f"geste de {v.notional_usd:.0f} $ sous le ticket minimum")
        if bits:
            return " · ".join(bits)
        # Rejet V2 PRÉCOCE (aucun niveau au-delà du bruit) : le verdict est
        # construit à l'étape 5 du plan, donc SANS target_in_sigma ni
        # stop_in_sigma — les branches ci-dessus n'ont rien à dire. Le motif
        # chiffré vit alors dans les notes : le taire rendrait muet le rejet le
        # plus fréquent du moteur (« aucune cible au-delà de 1,04 sigma »).
        if v.notes:
            return " · ".join(v.notes)
        return "conditions de viabilité non remplies"


def build(
    *,
    asset: str,
    direction: Direction,
    is_core: bool,
    tier: Optional[int],
    signal_scoring: dict[str, Any],
    price: Optional[float],
    closes: Optional[list[float]],
    daily_bars: Optional[list] = None,
    ptf_value_usd: Optional[float] = None,
    current_weight_pct: Optional[float] = None,
    position_value_usd: Optional[float] = None,
    daily_volume_usd: Optional[float] = None,
    budget_consumed_usd: float = 0.0,
    trim_pct: float = 50.0,
) -> Candidate:
    """Construit et évalue un candidat. Ne lève jamais."""
    cand = Candidate(asset=asset, direction=direction, horizon=None)

    # 1 — horizon (déterministe, jamais LLM, jamais choisi pour passer un gate)
    decision: HorizonDecision = determine_from_scoring(
        asset, is_core, tier, signal_scoring)
    cand.horizon = decision.horizon
    if not decision.emittable or decision.spec is None:
        cand.blocked_reason = decision.reason
        return cand
    spec = decision.spec

    if not price or price <= 0:
        cand.blocked_reason = f"{asset} : prix spot indisponible"
        return cand
    cand.entry = float(price)

    # 2 — profondeur d'historique exigée par l'horizon (I22)
    series = [float(c) for c in (closes or [])
              if isinstance(c, (int, float)) and c > 0]
    if len(series) < spec.depth_min:
        cand.verdict = viability.non_evaluable(
            ["history_depth"],
            [f"{len(series)} bougies journalières < {spec.depth_min} exigées "
             f"par l'horizon {spec.horizon.value}"])
        return cand

    # 3 — sigma journalière puis sigma d'horizon
    est: SigmaEstimate = daily_sigma(daily_bars=daily_bars, closes=series,
                                     window=spec.sigma_window_bars)
    if not est.available:
        cand.verdict = viability.non_evaluable(
            ["sigma_h"], [est.reason or "volatilité inestimable"])
        return cand
    cand.sigma_estimator = est.estimator
    cand.sigma_degraded = est.degraded
    cand.sigma_reason = est.reason
    sh = sigma_h(est.value, spec.days) * 100.0      # en points de pourcentage
    cand.sigma_h_pct = round(sh, 4)

    # 4 — multiples de sigma dérivés des probabilités de bruit (décision métier)
    #
    # Le contrôle des paramètres précède la SÉLECTION DES NIVEAUX : sans lui, un
    # paramètre incohérent produirait un rejet en V2 (« aucun niveau au-delà du
    # bruit ») alors que la vraie cause est la configuration. NON_EVALUABLE doit
    # primer sur NON_VIABLE — on ne conclut pas sur l'opportunité quand on ne
    # peut pas l'évaluer.
    missing = params.missing_emission_params()
    k2 = k_from_probability(params.p_target_max())
    k2p = k_from_probability(params.p_stop_max())
    if missing or k2 is None or k2p is None:
        cand.verdict = viability.non_evaluable(
            missing or ["p_target_max", "p_stop_max"])
        return cand

    # 5 — niveaux : filtrage par sigma_H AVANT toute troncature
    up = direction is Direction.LONG_INCREASE
    lv = compute_levels(series, cand.entry, spec)
    cand.levels = lv
    tgt = select_target(lv, sigma_h_pct=sh, k2=k2, direction_up=up)
    stp = select_stop(lv, sigma_h_pct=sh, k2p=k2p, direction_up=up)
    if tgt is None or stp is None:
        missing = []
        if tgt is None:
            missing.append(f"aucune cible au-delà de {k2:.2f} sigma "
                           f"({k2 * sh:.1f}%)")
        if stp is None:
            missing.append(f"aucune invalidation au-delà de {k2p:.2f} sigma "
                           f"({k2p * sh:.1f}%)")
        cand.verdict = viability.ViabilityVerdict(
            verdict=viability.Verdict.NON_VIABLE,
            failed_conditions=["V2"],
            sigma_h_pct=cand.sigma_h_pct,
            sigma_degraded=est.degraded,
            notes=missing)
        return cand
    cand.target, cand.target_basis = tgt.price, tgt.basis
    cand.stop, cand.stop_basis = stp.price, stp.basis

    # 6 — validité structurelle du contrat, AVANT tout calcul économique
    try:
        validate_contract(asset, direction, cand.entry, cand.target, cand.stop)
    except ContractValidityError as exc:
        cand.blocked_reason = str(exc)
        return cand

    # 7 — sizing : règles de risque SEULES, jamais réajusté ensuite (I14)
    if up:
        cand.sizing = sizing_mod.compute_increase(
            is_core=is_core, current_weight_pct=current_weight_pct,
            ptf_value_usd=ptf_value_usd, budget_consumed_usd=budget_consumed_usd)
    else:
        cand.sizing = sizing_mod.compute_reduce(
            position_value_usd=position_value_usd, trim_pct=trim_pct,
            ptf_value_usd=ptf_value_usd)

    # 8 — viabilité
    u = (cand.target - cand.entry) / cand.entry * 100.0 if up else \
        (cand.entry - cand.target) / cand.entry * 100.0
    d = (cand.entry - cand.stop) / cand.entry * 100.0 if up else \
        (cand.stop - cand.entry) / cand.entry * 100.0
    cand.verdict = viability.evaluate(
        upside_pct=u, downside_pct=d, sigma_h_pct=sh,
        sigma_degraded=est.degraded,
        notional_usd=cand.sizing.notional_usd if cand.sizing else None,
        tranches=TRANCHES, daily_volume_usd=daily_volume_usd,
        ptf_value_usd=ptf_value_usd)
    return cand


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """Tri des candidats VIABLES par P&L net attendu décroissant (SPEC §4.5).

    Le score composite ``rr * max(ev, 0.1)`` de la v30 est supprimé : il
    multipliait un ratio par un pourcentage, sans dimension interprétable.
    """
    return sorted([c for c in candidates if c.emittable],
                  key=lambda c: c.net_ev_usd, reverse=True)
