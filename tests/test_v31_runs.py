# -*- coding: utf-8 -*-
"""V31 — orchestration des trois runs (SPEC §2, I45-I47, I53)."""
from __future__ import annotations

import math
from datetime import timezone

import pytest

from src.core import params, runlog
from src.core.book import Direction, RecommendationBook, State
from src.core.volatility import DailyBar
from src.pipeline import runs

UTC = timezone.utc

FULL_PARAMS = {
    "fee_rate": 0.001,
    "liquidity_bands": [{"min_daily_volume_usd": 0, "spread_pct": 0.02,
                         "slippage_pct": 0.02}],
    "delta_claimable": 0.05,
    "p_target_max": 0.30,
    "p_stop_max": 0.20,
    "materiality_reference": "monthly_budget",
    "k3": 0.01,
    "monthly_budget": 500.0,
    "ticket_min": 40.0,
    "n_min": 3,
}

_TECH = {"signals": [{"category": "catalyst", "weight": 5},
                     {"category": "technical_struct", "weight": 3}]}


@pytest.fixture
def full_params():
    params.reset_cache()
    params._cache = dict(FULL_PARAMS)
    yield
    params.reset_cache()


@pytest.fixture
def no_params():
    params.reset_cache()
    params._cache = {}
    yield
    params.reset_cache()


def _series(n=140, start=100.0):
    """Range de +/-20 % finissant en milieu de canal : la résistance haute est
    à ~+18 % et le support bas à ~-21 %, tous deux au-delà du plancher de bruit
    d'un satellite volatil (sigma_30 ~15 %)."""
    return [start + 20.0 * math.sin(i / 11.0) for i in range(n)]


def _bars(n=40, base=100.0):
    """Amplitude journalière ~4,8 % -> sigma_jour ~2,8 %, sigma_30 ~15,4 %."""
    return [DailyBar(f"d{i}", base, base * 1.024, base * 0.976, base)
            for i in range(n)]


def _ctx(kind, tmp_path):
    summary = runlog.new_run(kind)
    book = RecommendationBook(run_kind=kind, run_id=summary.run_id,
                              state_dir=tmp_path / "bk")
    return runs.RunContext(kind=kind, summary=summary, book=book)


def _spec(asset="RENDER", series=None):
    s = series or _series()
    return {"asset": asset, "direction": Direction.LONG_INCREASE,
            "is_core": False, "tier": 1, "signal_scoring": _TECH,
            "price": s[-1], "closes": s, "daily_bars": _bars(),
            "ptf_value_usd": 2714.0, "weight_pct": 1.0,
            "daily_volume_usd": 5e8}


# ══════════════════════════════════════════════════════════════════════════
# Autorité d'écriture
# ══════════════════════════════════════════════════════════════════════════

def test_I45_only_morning_may_emit(tmp_path, full_params):
    for kind in ("evening", "weekly"):
        ctx = _ctx(kind, tmp_path)
        runs.evaluate_candidates(ctx, [_spec()])
        with pytest.raises(RuntimeError):
            runs.emit_viable(ctx)


def test_I46_readonly_runs_leave_the_book_byte_identical(tmp_path, full_params):
    morning = _ctx("morning", tmp_path)
    runs.evaluate_candidates(morning, [_spec()])
    runs.emit_viable(morning)
    morning.book.commit()
    before = (tmp_path / "bk" / "contracts.json").read_bytes()

    evening = _ctx("evening", tmp_path)
    runs.evaluate_transitions(evening, {"RENDER": 100.0})
    runs.flag_intraday_breaches(evening, {"RENDER": 100.0})
    after = (tmp_path / "bk" / "contracts.json").read_bytes()
    assert after == before


def test_intraday_breach_warns_without_transitioning(tmp_path, full_params):
    morning = _ctx("morning", tmp_path)
    runs.evaluate_candidates(morning, [_spec()])
    runs.emit_viable(morning)
    morning.book.commit()
    rec = morning.book.active()[0]

    evening = _ctx("evening", tmp_path)
    runs.flag_intraday_breaches(evening, {rec.asset: rec.stop * 0.99})
    assert evening.intraday_warnings
    w = evening.intraday_warnings[0]
    assert w["kind"] == "stop" and "clôture" in w["note"]
    assert evening.book.active()[0].state_value is State.ACTIVE


def test_transitions_use_daily_close_and_are_idempotent(tmp_path, full_params):
    morning = _ctx("morning", tmp_path)
    runs.evaluate_candidates(morning, [_spec()])
    runs.emit_viable(morning)
    rec = morning.book.active()[0]
    runs.evaluate_transitions(morning, {rec.asset: rec.stop * 0.95})
    assert morning.transitions and \
        morning.transitions[0]["to"] == State.INVALIDATED.value
    again = morning.book.evaluate_transitions(
        daily_closes={rec.asset: rec.stop * 0.90})
    assert again == []


# ══════════════════════════════════════════════════════════════════════════
# Émission, classement, geste n°1
# ══════════════════════════════════════════════════════════════════════════

def test_top_action_is_derived_from_net_pnl(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec("RENDER"), _spec("FET")])
    runs.emit_viable(ctx)
    if ctx.emitted:
        top = runs.top_action(ctx)
        assert top["asset"] in {"RENDER", "FET"}
        assert top["expected_pnl_usd_net"] == max(
            e["candidate"].net_ev_usd for e in ctx.emitted)
    else:
        assert runs.top_action(ctx) is None


def test_I18_emitted_notionals_never_exceed_the_budget(tmp_path, full_params):
    """Seuls les gestes ÉMIS consomment le budget de recommandation."""
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec("A"), _spec("B"), _spec("C")])
    emitted_total = sum(c.sizing.notional_usd or 0 for c in ctx.candidates
                        if c.emittable)
    assert emitted_total <= FULL_PARAMS["monthly_budget"] * 1.0001
    runs.emit_viable(ctx)
    assert ctx.book.budget_consumed() <= FULL_PARAMS["monthly_budget"] * 1.0001


def test_nothing_to_do_distinguishes_evaluable_from_not(tmp_path, no_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    msg = runs.nothing_to_do_reason(ctx)
    assert "évaluable" in msg.lower()
    assert "fee_rate" in msg
    assert "Rien à faire" not in msg     # distinct de « rien ne vaut la peine »


def test_nothing_to_do_when_all_non_viable(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    flat = [100.0 + 0.01 * i for i in range(140)]      # aucun niveau exploitable
    runs.evaluate_candidates(ctx, [_spec("FLAT", flat)])
    if not any(c.emittable for c in ctx.candidates):
        msg = runs.nothing_to_do_reason(ctx)
        assert "Rien à faire" in msg or "non évaluable" in msg.lower()


# ══════════════════════════════════════════════════════════════════════════
# Faits, dégradations, métriques
# ══════════════════════════════════════════════════════════════════════════

def test_book_facts_are_registered_for_the_three_mails(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    runs.emit_viable(ctx)
    runs.evaluate_transitions(ctx, {})
    runs.register_book_facts(ctx)
    assert ctx.store.has("book.active_count")
    assert ctx.store.formatted("book.active_count") != "—"


def test_rejected_candidates_still_produce_quantified_facts(tmp_path,
                                                            full_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    runs.register_candidate_facts(ctx)
    assert any(i.startswith("plan.") for i in ctx.store.ids())


def test_finalize_produces_banner_and_exactly_six_metrics(tmp_path, no_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    out = runs.finalize(ctx)
    assert len(out["metrics"]) == 6
    assert out["banner"] is not None
    assert "paramètres métier absents" in out["banner"]
    assert "%" not in out["banner"]


def test_finalize_banner_is_none_when_clean(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    out = runs.finalize(ctx)
    assert out["banner"] is None


def test_I53_failure_does_not_persist_contracts(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    runs.emit_viable(ctx)
    runs.abort(ctx, "envoi impossible")
    assert not (tmp_path / "bk" / "contracts.json").exists()


def test_persist_after_send_writes_the_book(tmp_path, full_params, monkeypatch):
    monkeypatch.setattr(runlog, "persist", lambda s, state_dir=None: None)
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    runs.emit_viable(ctx)
    runs.persist_after_send(ctx)
    assert (tmp_path / "bk" / "contracts.json").exists()
    assert ctx.summary.status == "success"


def test_readonly_run_persist_does_not_touch_the_book(tmp_path, full_params,
                                                      monkeypatch):
    monkeypatch.setattr(runlog, "persist", lambda s, state_dir=None: None)
    ctx = _ctx("weekly", tmp_path)
    runs.persist_after_send(ctx)
    assert not (tmp_path / "bk" / "contracts.json").exists()


def test_phases_are_all_timed(tmp_path, full_params):
    ctx = _ctx("morning", tmp_path)
    runs.evaluate_candidates(ctx, [_spec()])
    runs.evaluate_transitions(ctx, {})
    runs.emit_viable(ctx)
    for phase in ("viability", "transitions", "emission"):
        assert phase in ctx.summary.phase_durations
