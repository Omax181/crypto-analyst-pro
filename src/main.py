"""Point d'entrée — SPEC V31 §2.1.

``main.py`` n'ORCHESTRE que : il ne calcule rien, ne décide rien, ne formate
rien. Chaque étape appartient à un module qui en est l'AUTORITÉ UNIQUE, et
l'ordre ci-dessous est normatif — il ne peut pas être réarrangé sans violer une
dépendance :

    1  collecte                      pipeline.collect / pipeline.context
    2  transitions d'état            core.book        (matin seul persiste)
    3  candidats                     analytics.signals
    4  horizon                       core.horizon
    5  plan (niveaux, sigma)         core.levels / core.volatility
    6  sizing                        core.sizing
    7  viabilité                     core.viability
    8  sélection et classement       core.plan
    9  FactStore complet, puis SCELLÉ core.facts
   10  rédaction éditoriale          ai_brain.llm
   11  validation du contenu         core.content     (rejet, jamais réparation)
   12  rendu                         reporting.render
   13  ENVOI
   14  persistance + commit          après l'envoi seulement (I53)

Le FactStore est construit en NEUF et non en deux, contrairement à la lecture
littérale de la SPEC : les faits du carnet et des candidats n'existent qu'après
les transitions et l'évaluation. L'invariant qui compte est respecté — le store
est COMPLET puis SCELLÉ avant le moindre appel LLM, et aucun fait n'est créé
après (I36).

La v30 tenait ces quatorze étapes dans 7 735 lignes, avec cinq définitions
concurrentes de l'invalidation et deux autorités de sizing. Il n'en reste
qu'une de chaque.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from src.ai_brain.llm import LLMSession, LLMUnavailable
from src.analytics import signals as signals_mod
from src.core import params
from src.pipeline import context as context_mod, runs
from src.reporting import render
from src.reporting.email_sender import send_email
from src.state import bot_memory
from src.utils.logger import get_logger

logger = get_logger(__name__)

APP_VERSION = render.APP_VERSION


# ── étape 4 — signaux déterministes par actif ─────────────────────────────

def _signals(positions: list[Any], closes: dict[str, Any],
             spot: Any, sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Signaux par actif. Seuls leurs POIDS entrent dans la décision d'horizon."""
    quotes = (spot.value or {}) if spot.usable else {}
    fg_res = sources.get("fear_greed")
    fear_greed = None
    if fg_res is not None and fg_res.usable:
        raw = (fg_res.value or {}).get("value")
        fear_greed = float(raw) if isinstance(raw, (int, float)) else None
    oc_res = sources.get("onchain")
    onchain = (oc_res.value or {}) if oc_res is not None and oc_res.usable else {}

    out: dict[str, dict[str, Any]] = {}
    for p in positions:
        res = closes.get(p.cg_key)
        series = list((res.value or {}).get("closes") or []) \
            if res is not None and res.usable else []
        q = quotes.get(p.cg_key) or {}
        # Les blocs on-chain sont indexés par CHAÎNE (« BTC », « ETH »), pas
        # par actif : un satellite n'a pas de bloc, et c'est normal.
        chain = onchain.get(p.symbol) or {}
        pru_gap = None
        if p.pru and p.price:
            pru_gap = (p.price - p.pru) / p.pru * 100.0
        out[p.symbol] = signals_mod.evaluate(
            asset=p.symbol, tier=p.tier, closes=series,
            change_24h=q.get("change_24h"),
            change_from_ath_pct=q.get("change_from_ath_pct"),
            pru_gap_pct=pru_gap,
            # MVRV : AUCUNE source du périmètre ne le fournit aujourd'hui (le
            # module qui le servait a été retiré). Le paramètre est conservé
            # parce qu'il est optionnel et qu'une source future le remplirait
            # sans changer d'appelant ; en attendant, le signal est simplement
            # ABSENT — il n'est ni inventé, ni annoncé.
            mvrv=chain.get("mvrv"),
            active_addresses_trend_pct=chain.get("active_addresses_trend_pct"),
            fear_greed=fear_greed)
    return out


# ── résumé moteur transmis au LLM (I37 : il lit, il ne décide pas) ────────

def _engine_summary(ctx: runs.RunContext, top: Optional[dict[str, Any]],
                    nothing: Optional[str]) -> dict[str, Any]:
    return {
        "run": ctx.kind,
        "contrats_actifs": len(ctx.book.active()),
        "emissions": [{"actif": e["asset"], "action": e["action"]}
                      for e in ctx.emitted],
        "transitions": [{"actif": t["asset"], "vers": t["to"],
                         "cause": t["cause"]} for t in ctx.transitions],
        "geste_du_jour": ({"actif": top["asset"],
                           "direction": top["direction"]} if top else None),
        "aucun_geste": nothing,
        "candidats_ecartes": [{"actif": c.asset,
                               "motif": c.rejection_summary()}
                              for c in ctx.candidates if not c.emittable][:12],
        "parametres_metier_absents": params.missing_emission_params(),
        "fonctions_desactivees": params.disabled_features(),
    }


# ── run générique ─────────────────────────────────────────────────────────

def _run(kind: str) -> int:
    """Exécute un run complet. Retourne 0 si le mail est parti, 1 sinon."""
    ctx = runs.start(kind)
    full = kind == runs.MORNING

    try:
        with ctx.summary.phase("collect"):
            positions = context_mod.load_universe()
            symbols = context_mod.market_symbols(positions)
            sources, spot, closes, bars = context_mod.collect_all(
                symbols, full=full)
            ctx.sources = dict(sources)
            ctx.sources["market.spot"] = spot
            # Les séries sont par actif ; la matrice de santé raisonne par
            # SOURCE. On agrège donc sur le PIRE état observé : une seule série
            # manquante dégrade la source, sinon un actif muet resterait
            # invisible derrière la réussite des autres.
            ctx.sources.update(_aggregate_series(closes, bars))
            ptf_value = context_mod.value_portfolio(positions, spot)

        # ── 3 — transitions, sur la dernière clôture COMPLÈTE ─────────────
        runs.evaluate_transitions(ctx, context_mod.daily_close_map(positions,
                                                                   closes))
        spot_by_asset = context_mod.spot_map(positions, spot)
        ctx.book.refresh_tracking(prices=spot_by_asset)
        if kind == runs.EVENING:
            runs.flag_intraday_breaches(ctx, spot_by_asset)

        # ── 4 à 9 — candidats, plan, viabilité, émission (MATIN seul) ─────
        top: Optional[dict[str, Any]] = None
        nothing: Optional[str] = None
        if full:
            sig = _signals(positions, closes, spot, sources)
            specs = context_mod.candidate_specs(
                positions, ptf_value=ptf_value, closes=closes, bars=bars,
                spot=spot, signals_by_asset=sig)
            runs.evaluate_candidates(ctx, specs)
            runs.emit_viable(ctx)
            top = runs.top_action(ctx)
            if top is None:
                nothing = runs.nothing_to_do_reason(ctx)

        # ── 2 — FactStore complet, PUIS scellé (I36) ──────────────────────
        with ctx.summary.phase("facts"):
            context_mod.register_facts(
                ctx.store, positions=positions, ptf_value=ptf_value,
                spot=spot, sources=sources)
            runs.register_book_facts(ctx)
            runs.register_candidate_facts(ctx)
            ctx.store.seal()

        finalization = runs.finalize(ctx)

        # ── 10-11 — rédaction puis validation par REJET ───────────────────
        authored: dict[str, Any] = {}
        session = LLMSession(ctx.summary)
        news_res = sources.get("news")
        news_items = (news_res.value or []) if news_res is not None \
            and news_res.usable else []
        try:
            authored = session.compose(
                kind, fact_context=ctx.store.llm_context(),
                engine_summary=_engine_summary(ctx, top, nothing),
                external_items=render.news_view(news_items))
        except LLMUnavailable as exc:
            logger.warning("Rédaction éditoriale indisponible : %s", exc)
        clean, rejections = render.apply_content(ctx, authored)
        ctx.rejections = rejections
        # Le bandeau se recalcule après validation : un rejet massif y figure.
        finalization["banner"] = _rebanner(ctx, finalization)

        # ── 12 — rendu ────────────────────────────────────────────────────
        with ctx.summary.phase("render"):
            images = _charts(ctx, positions, closes) if full else {}
            payload = render.build_payload(
                ctx, kind=kind, positions=positions, ptf_value=ptf_value,
                authored=clean, finalization=finalization,
                news_items=news_items, top=top, nothing_to_do=nothing)
            html = render.render(payload, kind, charts=images)
            subject = render.subject(kind, payload)

        # ── 13 — ENVOI (le contrat n'existe que s'il a été communiqué) ─────
        with ctx.summary.phase("send"):
            sent = send_email(subject, html, inline_images=images or None)
        if not sent:
            runs.abort(ctx, "envoi du mail impossible")
            return 1

        _notify(payload, kind)

        # ── 14 — persistance APRÈS envoi ──────────────────────────────────
        bot_memory.save_report_snapshot(kind, payload)
        runs.persist_after_send(ctx)
        logger.info("Run %s terminé : %s", kind, subject)
        return 0

    except Exception as exc:                                    # noqa: BLE001
        logger.exception("Run %s interrompu : %s", kind, exc)
        runs.abort(ctx, str(exc))
        return 1


_SERIES_RANK = {"OK": 0, "DEGRADED": 1, "EMPTY": 2, "UNAVAILABLE": 3, "DEAD": 4}


def _worst(results: dict[str, Any]) -> Optional[Any]:
    """Pire état observé sur un ensemble de séries, ou ``None`` si vide."""
    if not results:
        return None
    worst = max(results.values(),
                key=lambda r: _SERIES_RANK.get(r.status.value, 0))
    if worst.status.value == "OK":
        return worst
    affected = sum(1 for r in results.values() if r.status is worst.status)
    note = f"{(worst.note or '').strip()} ({affected}/{len(results)} actifs)"
    return worst.with_status(worst.status, note.strip())


def _aggregate_series(closes: dict[str, Any],
                      bars: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source_id, results in (("market.closes", closes),
                               ("market.ohlc", bars)):
        agg = _worst(results)
        if agg is not None:
            out[source_id] = agg
    return out


def _charts(ctx: runs.RunContext, positions: list[Any],
            closes: dict[str, Any]) -> dict[str, bytes]:
    """Graphiques des contrats émis. Un échec ici ne compromet pas le mail."""
    try:
        from src.reporting import charts
        by_asset: dict[str, list[float]] = {}
        for p in positions:
            res = closes.get(p.cg_key)
            if res is not None and res.usable:
                by_asset[p.symbol] = list((res.value or {}).get("closes") or [])
        return charts.charts_for_contracts(ctx.emitted, by_asset)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Graphiques non produits : %s", exc)
        return {}


def _rebanner(ctx: runs.RunContext, finalization: dict[str, Any]
              ) -> Optional[str]:
    from src.core import runlog
    return runlog.degradation_banner(ctx.summary) or finalization.get("banner")


def _notify(payload: dict[str, Any], kind: str) -> None:
    """Notification Telegram. Un échec ici ne compromet pas le run."""
    try:
        from src.telegram_bot import notify
        notify.push(payload, kind)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Notification Telegram non envoyée : %s", exc)


def run_morning() -> int:
    return _run(runs.MORNING)


def run_evening() -> int:
    return _run(runs.EVENING)


def run_weekly() -> int:
    return _run(runs.WEEKLY)


_RUNNERS = {"morning": run_morning, "evening": run_evening,
            "weekly": run_weekly}


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "morning").strip().lower()
    runner = _RUNNERS.get(mode)
    if runner is None:
        logger.error("Mode inconnu : %s (attendu : %s)", mode,
                     ", ".join(_RUNNERS))
        return 2
    return runner()


if __name__ == "__main__":
    raise SystemExit(main())
