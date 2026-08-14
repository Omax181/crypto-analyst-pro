"""Les trois runs — SPEC V31 §2 (I45, I46, I47, I53, I58).

Rôles VERROUILLÉS :

  MATIN   collecte complète · SEUL run habilité à créer un contrat et à
          produire une transition · rend carnet + contexte + émissions du jour
  SOIR    collecte partielle · LECTURE SEULE · rend l'état du carnet, le delta
          depuis le matin et les signalements EN SÉANCE (sans transitionner)
  HEBDO   collecte partielle · LECTURE SEULE · rend le carnet, le contexte et
          les niveaux de surveillance DÉRIVÉS des contrats actifs

Les clôtures étant des clôtures de journée UTC, matin (07:30 UTC) et soir
(19:00 UTC) voient la MÊME dernière clôture complète : seul le matin peut
observer une clôture nouvelle. Les transitions du soir et de l'hebdo sont donc
idempotentes et vides — elles sont évaluées, jamais persistées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.core import content, metrics as metrics_mod
from src.core import params, plan as plan_mod, registry, runlog, viability
from src.core.book import Direction, RecommendationBook, State
from src.core.facts import FactStore, Unit, UsageRight
from src.core.source_result import SourceResult
from src.utils.logger import get_logger

logger = get_logger(__name__)

MORNING, EVENING, WEEKLY = "morning", "evening", "weekly"
READ_ONLY_RUNS = frozenset({EVENING, WEEKLY})


@dataclass
class RunContext:
    """État d'un run. Un seul objet traverse toutes les phases."""

    kind: str
    summary: runlog.RunSummary
    book: RecommendationBook
    store: FactStore = field(default_factory=FactStore)
    sources: dict[str, SourceResult] = field(default_factory=dict)
    candidates: list[plan_mod.Candidate] = field(default_factory=list)
    emitted: list[Any] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    intraday_warnings: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[content.Rejection] = field(default_factory=list)
    health: list[registry.SourceHealth] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        return self.kind == MORNING


def start(kind: str) -> RunContext:
    summary = runlog.new_run(kind)
    book = RecommendationBook(run_kind=kind, run_id=summary.run_id)
    logger.info("=== RUN %s · %s ===", kind.upper(), summary.run_id)
    return RunContext(kind=kind, summary=summary, book=book)


# ── étape 3 — transitions (seule le matin peut les persister) ──────────────

def evaluate_transitions(ctx: RunContext,
                         daily_closes: dict[str, float]) -> None:
    """Évalue sur la DERNIÈRE CLÔTURE JOURNALIÈRE COMPLÈTE (SPEC §2.4)."""
    with ctx.summary.phase("transitions"):
        ctx.transitions = ctx.book.evaluate_transitions(
            daily_closes=daily_closes)
        for t in ctx.transitions:
            logger.info("Transition %s -> %s (%s)", t["asset"], t["to"],
                        t["cause"])


def flag_intraday_breaches(ctx: RunContext,
                           spot: dict[str, float]) -> None:
    """Signalement EN SÉANCE, sans transition (soir).

    Le carnet n'est pas touché : la transition sera évaluée sur la clôture.
    """
    out: list[dict[str, Any]] = []
    for rec in ctx.book.active():
        px = spot.get(rec.asset)
        if not isinstance(px, (int, float)) or px <= 0:
            continue
        up = rec.dir_enum is Direction.LONG_INCREASE
        breached = px <= rec.stop if up else px >= rec.stop
        reached = px >= rec.target if up else px <= rec.target
        if breached or reached:
            out.append({
                "asset": rec.asset,
                "kind": "stop" if breached else "target",
                "level": rec.stop if breached else rec.target,
                "spot": float(px),
                "note": ("franchi en séance — la transition sera évaluée "
                         "sur la clôture journalière"),
            })
    ctx.intraday_warnings = out


# ── étape 8-9 — candidats, verdicts, classement ───────────────────────────

def evaluate_candidates(ctx: RunContext, specs: list[dict[str, Any]]) -> None:
    """Construit et évalue les candidats. Aucune émission ici."""
    with ctx.summary.phase("viability"):
        out: list[plan_mod.Candidate] = []
        budget_consumed = ctx.book.budget_consumed()
        for s in specs:
            # Un contrat DÉJÀ actif sur ce couple actif/direction a son
            # notional compris dans ``budget_consumed`` : le déduire évite de
            # facturer deux fois une simple RÉVISION de plan opérationnel, qui
            # n'engage aucun capital nouveau (SPEC §1.4, §4.3).
            already = ctx.book.committed_notional(
                s["asset"], s.get("direction", Direction.LONG_INCREASE))
            cand = plan_mod.build(
                asset=s["asset"], direction=s.get("direction",
                                                  Direction.LONG_INCREASE),
                is_core=bool(s.get("is_core")), tier=s.get("tier"),
                signal_scoring=s.get("signal_scoring") or {},
                price=s.get("price"), closes=s.get("closes"),
                daily_bars=s.get("daily_bars"),
                ptf_value_usd=s.get("ptf_value_usd"),
                current_weight_pct=s.get("weight_pct"),
                position_value_usd=s.get("position_value_usd"),
                daily_volume_usd=s.get("daily_volume_usd"),
                budget_consumed_usd=max(0.0, budget_consumed - already))
            out.append(cand)
            if cand.emittable:
                # Le budget se consomme au fil des émissions du run. Une
                # RÉVISION remplace le notional déjà engagé au lieu de s'y
                # ajouter : on ne compte que le DELTA.
                budget_consumed += (cand.sizing.notional_usd or 0.0) - already
        ctx.candidates = out


def emit_viable(ctx: RunContext) -> None:
    """Émet les candidats viables, par ordre de P&L net attendu (I45).

    Interdit hors du run du matin : le carnet lève.
    """
    if not ctx.writable:
        raise RuntimeError(
            f"run {ctx.kind} : émission interdite (SPEC §2.2/§2.3)")
    with ctx.summary.phase("emission"):
        for cand in plan_mod.rank(ctx.candidates):
            blocked = ctx.book.reemission_blocked(
                cand.asset, cand.direction, cand.stop, cand.horizon,
                cand.sigma_h_pct or 0.0)
            if blocked:
                cand.blocked_reason = blocked
                logger.info("Émission bloquée : %s", blocked)
                continue
            rec, action = ctx.book.emit(
                asset=cand.asset, direction=cand.direction,
                horizon=cand.horizon, entry=cand.entry, target=cand.target,
                stop=cand.stop, sizing=cand.sizing.to_dict(),
                viability=cand.verdict.to_dict(),
                p_null=cand.verdict.p_null,
                p_breakeven=cand.verdict.p_breakeven,
                delta_required=cand.verdict.delta_required,
                levels={"target_basis": cand.target_basis,
                        "stop_basis": cand.stop_basis})
            ctx.emitted.append({"asset": cand.asset, "action": action,
                                "candidate": cand})
            logger.info("Contrat %s %s (%s)", cand.asset,
                        cand.direction.value, action)


def top_action(ctx: RunContext) -> Optional[dict[str, Any]]:
    """Geste n°1 : meilleur contrat émis ou révisé DANS CE RUN (SPEC §4.5).

    DERIVED : le LLM ne choisit ni l'actif, ni l'ordre.
    """
    if not ctx.emitted:
        return None
    best = max(ctx.emitted,
               key=lambda e: e["candidate"].net_ev_usd)
    c = best["candidate"]
    return {
        "asset": c.asset, "direction": c.direction.value,
        "notional_usd": c.sizing.notional_usd,
        "entry": c.entry, "target": c.target, "stop": c.stop,
        "expected_pnl_usd_net": c.verdict.expected_pnl_usd_net,
        "delta_required": c.verdict.delta_required,
        "p_breakeven": c.verdict.p_breakeven,
    }


def _group_reasons(candidates: list[plan_mod.Candidate], limit: int = 3
                   ) -> str:
    """Motifs distincts, agrégés par nombre d'actifs concernés.

    Vingt actifs cœur produisent vingt fois le même motif ; les énumérer un par
    un noierait l'information au lieu de la donner.
    """
    counts: dict[str, int] = {}
    for c in candidates:
        reason = c.rejection_summary()
        # Les motifs sont préfixés du symbole (« BTC : actif cœur -> … ») :
        # sans le retirer, chaque actif produirait un motif « distinct » et
        # l'agrégation ne regrouperait jamais rien.
        prefix = f"{c.asset} : "
        if reason.startswith(prefix):
            reason = reason[len(prefix):]
        counts[reason] = counts.get(reason, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    bits = [f"{reason} ({n} actif{'s' if n > 1 else ''})"
            for reason, n in ordered[:limit]]
    if len(ordered) > limit:
        bits.append(f"et {len(ordered) - limit} autre(s) motif(s)")
    return " · ".join(bits)


def nothing_to_do_reason(ctx: RunContext) -> str:
    """Message honnête quand aucun geste n'est émis (SPEC §4.5).

    Trois situations sont RIGOUREUSEMENT distinctes, et les confondre serait
    mentir au lecteur :

      NON_VIABLE     un plan a été construit, chiffré, puis écarté — « rien ne
                     vaut la peine » est alors une conclusion légitime ;
      NON_EVALUABLE  le verdict n'a pas pu être rendu (paramètre métier absent,
                     profondeur d'historique insuffisante, volatilité
                     inestimable) — on ne sait pas, on ne conclut pas ;
      NON ÉVALUÉ     le plan n'a même pas été construit (horizon désactivé,
                     prix indisponible, contrat structurellement impossible).

    La v30 servait la première phrase dans les trois cas. Un matin sans réseau
    annonçait donc sereinement qu'aucune opportunité ne méritait d'être saisie.
    """
    if not ctx.candidates:
        return "Aucun actif à évaluer ce matin."

    # Un candidat VIABLE mais porteur d'un motif de blocage a été arrêté par la
    # contrainte de ré-émission (§3) : il y avait bien un geste.
    reemission = [c for c in ctx.candidates
                  if c.blocked_reason and c.verdict is not None
                  and c.verdict.is_viable]
    non_viable = [c for c in ctx.candidates
                  if c.verdict and c.verdict.verdict is viability.Verdict.NON_VIABLE]
    non_eval = [c for c in ctx.candidates
                if c.verdict and c.verdict.verdict is viability.Verdict.NON_EVALUABLE]
    not_built = [c for c in ctx.candidates
                 if c.verdict is None and c.blocked_reason]

    if reemission:
        return ("Un geste était viable mais reste bloqué : "
                + " · ".join(c.blocked_reason for c in reemission[:2]))

    if non_viable:
        return ("Rien à faire aujourd'hui — aucun geste évalué ne franchit les "
                "conditions de viabilité. S'abstenir est aussi une décision. "
                "Motifs : " + _group_reasons(non_viable))

    if non_eval:
        missing = sorted({m for c in non_eval for m in c.verdict.missing_inputs})
        return ("Aucune recommandation n'est évaluable — entrées manquantes : "
                + ", ".join(missing)
                + ". Ce n'est pas « rien ne vaut la peine », c'est « on ne "
                  "peut pas trancher ».")

    if not_built:
        return ("Aucun plan n'a pu être construit ce matin — "
                + _group_reasons(not_built)
                + ". Aucune conclusion n'est tirée sur la valeur des "
                  "opportunités : elles n'ont pas été évaluées.")

    return "Aucun candidat éligible ce matin."


# ── étape 2 — faits de base communs aux trois mails ───────────────────────

def register_book_facts(ctx: RunContext) -> None:
    """Faits DÉRIVÉS du carnet, identiques dans les trois mails (I13)."""
    store = ctx.store
    active = ctx.book.active()
    store.register("book.active_count", len(active), Unit.COUNT)
    store.register("book.invalidated_today",
                   sum(1 for t in ctx.transitions
                       if t["to"] == State.INVALIDATED.value), Unit.COUNT)
    store.register("book.target_hit_today",
                   sum(1 for t in ctx.transitions
                       if t["to"] == State.TARGET_HIT.value), Unit.COUNT)
    store.register("book.expired_today",
                   sum(1 for t in ctx.transitions
                       if t["to"] == State.EXPIRED.value), Unit.COUNT)
    budget = params.monthly_budget()
    if budget is not None:
        store.register("book.budget_consumed", ctx.book.budget_consumed(),
                       Unit.USD_AMOUNT)
        store.register("book.budget_total", budget, Unit.USD_AMOUNT)
    for rec in active:
        a = rec.asset.lower()
        store.register(f"book.{a}.entry", rec.entry, Unit.USD_PRICE)
        store.register(f"book.{a}.target", rec.target, Unit.USD_PRICE)
        store.register(f"book.{a}.stop", rec.stop, Unit.USD_PRICE)
        store.register(f"book.{a}.days", rec.tracking.get("days_elapsed"),
                       Unit.DAYS)
        cur = rec.tracking.get("current_price")
        if isinstance(cur, (int, float)) and rec.entry:
            store.register(f"book.{a}.delta",
                           (cur - rec.entry) / rec.entry * 100.0, Unit.PCT)


def register_candidate_facts(ctx: RunContext) -> None:
    """Faits des candidats, y compris REJETÉS : un rejet est chiffré, pas muet."""
    for c in ctx.candidates:
        a = c.asset.lower()
        v = c.verdict
        if v is None:
            continue
        if v.delta_required is not None:
            ctx.store.register(f"plan.{a}.delta_required",
                               v.delta_required * 100.0, Unit.PCT,
                               usage_right=UsageRight.CONTEXT)
        if v.target_in_sigma is not None:
            ctx.store.register(f"plan.{a}.target_sigma", v.target_in_sigma,
                               Unit.SIGMA, usage_right=UsageRight.CONTEXT)
        if v.expected_pnl_usd_net is not None:
            ctx.store.register(f"plan.{a}.net_pnl", v.expected_pnl_usd_net,
                               Unit.USD_AMOUNT, usage_right=UsageRight.CONTEXT)


# ── étape finale — santé, dégradations, métriques ─────────────────────────

def finalize(ctx: RunContext) -> dict[str, Any]:
    """Matrice de sources, bandeau, six métriques. Aucun scalaire agrégé."""
    ctx.health = registry.matrix(ctx.sources)
    non_eval = sum(1 for c in ctx.candidates
                   if c.verdict
                   and c.verdict.verdict is viability.Verdict.NON_EVALUABLE)
    non_viable = sum(1 for c in ctx.candidates
                     if c.verdict
                     and c.verdict.verdict is viability.Verdict.NON_VIABLE)
    sigma_degraded = {c.asset: c.sigma_reason for c in ctx.candidates
                      if c.sigma_degraded}
    for d in runlog.build_degradations(
            health_matrix=ctx.health, non_evaluable=non_eval,
            missing_params=params.missing_emission_params(),
            rejections=len(ctx.rejections),
            sigma_degraded=sigma_degraded):
        ctx.summary.add_degradation(d)

    reasons: dict[str, int] = {}
    for c in ctx.candidates:
        if c.verdict and c.verdict.failed_conditions:
            for cond in c.verdict.failed_conditions:
                reasons[cond] = reasons.get(cond, 0) + 1

    ctx.summary.counters.update({
        "candidates": len(ctx.candidates), "emitted": len(ctx.emitted),
        "non_viable": non_viable, "non_evaluable": non_eval,
        "authored_rejections": len(ctx.rejections),
        "transitions": len(ctx.transitions),
    })

    six = metrics_mod.all_metrics(
        ctx.book, health_matrix=ctx.health,
        candidates_total=len(ctx.candidates), emitted=len(ctx.emitted),
        non_viable=non_viable, non_evaluable=non_eval, reasons=reasons,
        authored_fields=len(content.SCHEMA), rejections=len(ctx.rejections))

    ctx.summary.source_matrix = [
        {"source": h.spec.id, "status": h.status.value, "tier": h.tier,
         "missed": h.missed_publications, "note": h.note} for h in ctx.health]

    return {
        "banner": runlog.degradation_banner(ctx.summary),
        "metrics": [m.to_dict() for m in six],
        "health": [h.describe() for h in ctx.health],
    }


def persist_after_send(ctx: RunContext) -> None:
    """I53 — un contrat n'existe que s'il a été communiqué.

    L'ordre est : construire -> valider -> rendre -> ENVOYER -> persister.
    La v30 persistait AVANT l'envoi, avec ``if: always()`` sur le commit : un
    run interrompu créait des contrats jamais communiqués.
    """
    with ctx.summary.phase("persist"):
        if ctx.writable:
            ctx.book.commit()
        ctx.summary.finish("success")
        runlog.persist(ctx.summary)


def abort(ctx: RunContext, reason: str) -> None:
    """Échec : RIEN n'est persisté (ni carnet, ni résumé de succès)."""
    logger.error("Run %s interrompu : %s", ctx.summary.run_id, reason)
    ctx.summary.finish("failed")
    runlog.persist(ctx.summary)
