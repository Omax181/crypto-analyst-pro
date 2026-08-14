# -*- coding: utf-8 -*-
"""V31 — BUDGET DE RECOMMANDATION × LIFECYCLE (SPEC §4.3, I18).

Deux défauts mesurés lors de l'audit final, corrigés et verrouillés ici :

A. Le carnet se FIGEAIT dès le premier contrat du mois. La révision d'un plan
   opérationnel passait par le chemin de la création, donc par le sizing, donc
   par le budget — alors qu'une révision n'engage AUCUN capital nouveau. Le
   motif affiché (« geste de 0 $ sous le ticket minimum ») désignait de surcroît
   une cause fausse.

B. ``budget_consumed`` comptait les contrats SUPERSEDED. Un contrat remplacé
   n'est pas un apport supplémentaire : mesuré, le plafond de 500 $ était
   dépassé à 800 $.

Le budget reste un FLUX MENSUEL : un contrat clôturé par le marché ne restitue
pas sa part. Seuls SUPERSEDED et CANCELLED sont exclus — exactement les deux
états que ``SCORE_BY_STATE`` met déjà hors comptabilité.
"""
from __future__ import annotations

import math

import pytest

from src.core import params, runlog
from src.core.book import (Direction, RecommendationBook, SCORE_BY_STATE,
                           State, TERMINAL_STATES)
from src.core.horizon import Horizon
from src.core.volatility import DailyBar
from src.pipeline import runs

FUND = {"signals": [{"category": "fundamental_lt", "weight": 6},
                    {"category": "technical_struct", "weight": 2}]}


@pytest.fixture
def shipped():
    params.reset_cache()
    params._cache = None
    yield
    params.reset_cache()


def _series(n=420, amp=45.0):
    base = [140.0 + amp * math.sin(i / 38.0) for i in range(n - 30)]
    st = base[-1]
    return base + [st + (140.0 - st) * (k + 1) / 30 for k in range(30)]


def _bars(n=180, amp=0.012, b=100.0):
    return [DailyBar(f"d{i}", b, b * (1 + amp), b * (1 - amp), b)
            for i in range(n)]


def _spec(asset):
    return {"asset": asset, "direction": Direction.LONG_INCREASE,
            "is_core": False, "tier": 1, "signal_scoring": FUND,
            "price": 140.0, "closes": _series(), "daily_bars": _bars(),
            "ptf_value_usd": 60000.0, "weight_pct": 1.0,
            "position_value_usd": 600.0, "daily_volume_usd": 8e8}


def _run(tmp_path, assets):
    s = runlog.new_run("morning")
    bk = RecommendationBook(run_kind="morning", run_id=s.run_id,
                            state_dir=tmp_path / "b")
    c = runs.RunContext(kind="morning", summary=s, book=bk)
    runs.evaluate_candidates(c, [_spec(a) for a in assets])
    runs.emit_viable(c)
    bk.commit()
    return c


# ══════════════════════════════════════════════════════════════════════════
# A — la révision reste possible budget épuisé
# ══════════════════════════════════════════════════════════════════════════

def test_a_plan_revision_survives_an_exhausted_budget(tmp_path, shipped):
    first = _run(tmp_path, ["AAA"])
    assert len(first.emitted) == 1, "montage invalide : aucune émission"
    assert first.book.budget_consumed() == params.monthly_budget()

    again = _run(tmp_path, ["AAA"])
    assert len(again.emitted) == 1, "la révision doit rester possible"
    book = RecommendationBook(run_kind="bot", run_id="r",
                              state_dir=tmp_path / "b")
    rec = book.active()[0]
    assert len(rec.operational_plan) == 2, "le plan doit être versionné"


def test_a_revision_does_not_inflate_the_consumed_budget(tmp_path, shipped):
    _run(tmp_path, ["AAA"])
    _run(tmp_path, ["AAA"])
    _run(tmp_path, ["AAA"])
    book = RecommendationBook(run_kind="bot", run_id="r",
                              state_dir=tmp_path / "b")
    assert book.budget_consumed() <= params.monthly_budget() + 0.01
    assert len(book.active()) == 1, "une seule position, pas trois"


def test_a_NEW_asset_is_still_refused_when_the_budget_is_gone(tmp_path,
                                                               shipped):
    """La déduction ne doit PAS ouvrir une porte dérobée."""
    _run(tmp_path, ["AAA"])
    other = _run(tmp_path, ["BBB"])
    assert other.emitted == []
    cand = other.candidates[0]
    assert cand.sizing.notional_usd == 0.0
    assert not cand.emittable


def test_the_scored_contract_stays_frozen_across_revisions(tmp_path, shipped):
    """I15 — réviser met à jour le PLAN, jamais l'engagement scoré."""
    _run(tmp_path, ["AAA"])
    book = RecommendationBook(run_kind="bot", run_id="r",
                              state_dir=tmp_path / "b")
    frozen = dict(book.active()[0].scored_contract)
    _run(tmp_path, ["AAA"])
    book2 = RecommendationBook(run_kind="bot", run_id="r2",
                               state_dir=tmp_path / "b")
    assert book2.active()[0].scored_contract == frozen


# ══════════════════════════════════════════════════════════════════════════
# B — états hors comptabilité
# ══════════════════════════════════════════════════════════════════════════

def test_superseded_and_cancelled_are_excluded_from_the_budget(tmp_path,
                                                                shipped):
    _run(tmp_path, ["DDD"])
    bk = RecommendationBook(run_kind="morning", run_id="s",
                            state_dir=tmp_path / "b")
    assert bk.budget_consumed() == 500.0
    # Direction opposée => l'ancien passe SUPERSEDED, un nouveau naît.
    bk.emit(asset="DDD", direction=Direction.LONG_REDUCE,
            horizon=Horizon.SWING, entry=140.0, target=120.0, stop=160.0,
            sizing={"notional_usd": 300.0}, viability={}, p_null=None,
            p_breakeven=None, delta_required=None)
    states = [c.state_value for c in bk.all()]
    assert State.SUPERSEDED in states
    assert bk.budget_consumed() == 300.0, "le remplacé ne compte plus"
    assert bk.budget_consumed() <= params.monthly_budget()


def test_a_market_closed_contract_still_consumes_the_budget(tmp_path, shipped):
    """Le budget est un FLUX : une invalidation ne le rembourse pas."""
    _run(tmp_path, ["CCC"])
    bk = RecommendationBook(run_kind="morning", run_id="t",
                            state_dir=tmp_path / "b")
    rec = bk.active()[0]
    before = bk.budget_consumed()
    bk.evaluate_transitions(daily_closes={"CCC": rec.stop * 0.9})
    assert bk.all()[0].state_value is State.INVALIDATED
    assert bk.budget_consumed() == before


def test_the_exclusion_matches_the_closed_scoring_table():
    """L'exclusion budgétaire n'invente aucune frontière nouvelle."""
    excluded = {s for s in TERMINAL_STATES if SCORE_BY_STATE.get(s) is None}
    assert excluded == {State.SUPERSEDED, State.CANCELLED}


def test_a_dismissed_contract_frees_its_budget(tmp_path, shipped):
    _run(tmp_path, ["EEE"])
    bk = RecommendationBook(run_kind="morning", run_id="c",
                            state_dir=tmp_path / "b")
    assert bk.budget_consumed() == 500.0
    bk.cancel("EEE")
    assert bk.all()[0].state_value is State.CANCELLED
    assert bk.budget_consumed() == 0.0


def test_the_budget_resets_on_a_new_month(tmp_path, shipped):
    _run(tmp_path, ["FFF"])
    bk = RecommendationBook(run_kind="bot", run_id="m",
                            state_dir=tmp_path / "b")
    assert bk.budget_consumed() > 0
    assert bk.budget_consumed(month="2099-01") == 0.0


def test_committed_notional_only_sees_active_contracts_of_that_direction(
        tmp_path, shipped):
    _run(tmp_path, ["GGG"])
    bk = RecommendationBook(run_kind="bot", run_id="k",
                            state_dir=tmp_path / "b")
    assert bk.committed_notional("GGG", Direction.LONG_INCREASE) == 500.0
    assert bk.committed_notional("GGG", Direction.LONG_REDUCE) == 0.0
    assert bk.committed_notional("ZZZ", Direction.LONG_INCREASE) == 0.0


# ══════════════════════════════════════════════════════════════════════════
# I18 — le plafond reste opposable dans tous les cas
# ══════════════════════════════════════════════════════════════════════════

def test_I18_holds_across_many_assets_and_several_runs(tmp_path, shipped):
    for _ in range(3):
        _run(tmp_path, ["H1", "H2", "H3", "H4"])
    bk = RecommendationBook(run_kind="bot", run_id="i18",
                            state_dir=tmp_path / "b")
    assert bk.budget_consumed() <= params.monthly_budget() + 0.01