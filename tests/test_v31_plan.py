# -*- coding: utf-8 -*-
"""V31 — niveaux, sizing, construction de plan (Phases 1-3).

Démontre notamment que la cause de fond du cas « ETH 2 000 -> 2 015 » est
éliminée : le filtrage par sigma_H précède la troncature, et le repli sans
plancher n'existe plus.
"""
from __future__ import annotations

import math

import pytest

from src.core import levels as lv
from src.core import params, plan, sizing as sz, viability
from src.core.book import Direction
from src.core.horizon import SWING_SPEC, Horizon
from src.core.volatility import DailyBar

FULL_PARAMS = {
    "fee_rate": 0.001,
    "liquidity_bands": [
        {"min_daily_volume_usd": 0, "spread_pct": 0.10, "slippage_pct": 0.10},
        {"min_daily_volume_usd": 100_000_000, "spread_pct": 0.02,
         "slippage_pct": 0.02},
    ],
    "delta_claimable": 0.05,
    "p_target_max": 0.30,
    "p_stop_max": 0.20,
    "materiality_reference": "monthly_budget",
    "k3": 0.02,
    "monthly_budget": 500.0,
    "ticket_min": 40.0,
}


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


def _trending_series(n=140, start=100.0, drift=0.0015, amp=0.02):
    """Série journalière synthétique avec tendance douce et oscillation."""
    return [start * (1 + drift * i) * (1 + amp * math.sin(i / 5.0))
            for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════
# Niveaux : filtrage AVANT troncature, aucun repli
# ══════════════════════════════════════════════════════════════════════════

def test_levels_are_not_truncated_before_filtering():
    """Régression ETH 2 000 -> 2 015 : la vraie cible ne doit pas être évincée
    par trois résistances plus proches mais inexploitables."""
    price = 100.0
    ls = lv.LevelSet(
        supports=[lv.Level(99.0, "pivot", -1.0), lv.Level(88.0, "MM50", -12.0)],
        resistances=[lv.Level(100.5, "pivot", 0.5),
                     lv.Level(101.0, "seuil rond", 1.0),
                     lv.Level(101.8, "Bollinger haute", 1.8),
                     lv.Level(115.0, "MM50", 15.0)],
        price=price, bars_used=120)
    # sigma_H = 8 %, k2 = 1.0 -> plancher 8 % : seule la résistance à +15 % passe.
    tgt = lv.select_target(ls, sigma_h_pct=8.0, k2=1.0, direction_up=True)
    assert tgt is not None and tgt.price == 115.0
    stp = lv.select_stop(ls, sigma_h_pct=8.0, k2p=1.0, direction_up=True)
    assert stp is not None and stp.price == 88.0


def test_no_fallback_when_nothing_clears_the_noise_floor():
    ls = lv.LevelSet(
        supports=[lv.Level(99.4, "pivot", -0.6)],
        resistances=[lv.Level(100.75, "pivot", 0.75)],
        price=100.0, bars_used=120)
    assert lv.select_target(ls, sigma_h_pct=8.2, k2=1.0, direction_up=True) is None
    assert lv.select_stop(ls, sigma_h_pct=8.2, k2p=1.0, direction_up=True) is None


def test_compute_uses_horizon_window_and_returns_both_sides():
    series = _trending_series()
    out = lv.compute(series, series[-1], SWING_SPEC)
    assert out.bars_used == SWING_SPEC.level_window_bars
    assert out.supports and out.resistances
    assert all(s.price < out.price for s in out.supports)
    assert all(r.price > out.price for r in out.resistances)
    # Tri : du plus proche au plus loin de part et d'autre.
    assert out.supports == sorted(out.supports, key=lambda x: -x.price)
    assert out.resistances == sorted(out.resistances, key=lambda x: x.price)


def test_stop_selection_picks_nearest_beyond_floor_not_deepest():
    ls = lv.LevelSet(
        supports=[lv.Level(95.0, "pivot", -5.0), lv.Level(90.0, "MM50", -10.0),
                  lv.Level(70.0, "Fibo 61,8%", -30.0)],
        resistances=[], price=100.0, bars_used=120)
    stp = lv.select_stop(ls, sigma_h_pct=8.0, k2p=1.0, direction_up=True)
    assert stp.price == 90.0        # le plus proche AU-DELÀ du plancher


# ══════════════════════════════════════════════════════════════════════════
# Sizing : autorité unique, flux unidirectionnel
# ══════════════════════════════════════════════════════════════════════════

def test_sizing_absent_budget_yields_nothing(no_params):
    s = sz.compute_increase(is_core=False, current_weight_pct=2.0,
                            ptf_value_usd=2714.0, budget_consumed_usd=0.0)
    assert s.available is False and s.notional_usd is None


def test_sizing_is_bounded_by_the_binding_constraint(full_params):
    # Budget 500 $, plafond satellite 12 % de 2714 $ = 326 $ de marge à 0 %.
    s = sz.compute_increase(is_core=False, current_weight_pct=0.0,
                            ptf_value_usd=2714.0, budget_consumed_usd=0.0)
    assert s.binding_constraint == "concentration"
    assert s.notional_usd == pytest.approx(325.68, abs=0.01)
    # Marge de concentration réduite -> c'est elle qui lie encore.
    s2 = sz.compute_increase(is_core=False, current_weight_pct=10.0,
                             ptf_value_usd=2714.0, budget_consumed_usd=0.0)
    assert s2.binding_constraint == "concentration"
    assert s2.notional_usd < s.notional_usd


def test_sizing_respects_remaining_budget(full_params):
    s = sz.compute_increase(is_core=False, current_weight_pct=0.0,
                            ptf_value_usd=100_000.0, budget_consumed_usd=420.0)
    assert s.binding_constraint == "budget"
    assert s.notional_usd == pytest.approx(80.0)


def test_sizing_returns_zero_at_cap(full_params):
    s = sz.compute_increase(is_core=False, current_weight_pct=12.0,
                            ptf_value_usd=2714.0, budget_consumed_usd=0.0)
    assert s.notional_usd == 0.0 and "plafond" in s.reason


def test_sizing_zero_when_budget_exhausted(full_params):
    s = sz.compute_increase(is_core=False, current_weight_pct=0.0,
                            ptf_value_usd=2714.0, budget_consumed_usd=500.0)
    assert s.notional_usd == 0.0 and "épuisé" in s.reason


def test_core_and_satellite_caps_differ():
    assert sz.cap_for(True) > sz.cap_for(False)


# ══════════════════════════════════════════════════════════════════════════
# Plan : chaîne complète
# ══════════════════════════════════════════════════════════════════════════

_TECH_SCORING = {"signals": [{"category": "catalyst", "weight": 5},
                             {"category": "technical_struct", "weight": 3},
                             {"category": "fundamental_lt", "weight": 1}]}
_FUND_SCORING = {"signals": [{"category": "fundamental_lt", "weight": 7},
                             {"category": "catalyst", "weight": 1}]}


def _bars(n=40, base=100.0):
    return [DailyBar(f"d{i}", base, base * 1.03, base * 0.97, base)
            for i in range(n)]


def test_core_asset_routes_to_position_and_depth_is_the_only_gate(full_params):
    """Le cœur n'est plus bloqué par principe : il l'est par la PROFONDEUR.

    Avec un historique court, le refus reste — mais il est désormais CHIFFRÉ
    (« n bougies < 365 exigées ») au lieu d'être catégorique et muet.
    """
    c = plan.build(asset="BTC", direction=Direction.LONG_INCREASE, is_core=True,
                   tier=0, signal_scoring=_TECH_SCORING, price=64963.0,
                   closes=_trending_series(), ptf_value_usd=2714.0)
    assert c.horizon is Horizon.POSITION
    assert c.blocked_reason is None, "l'horizon ne bloque plus a priori"
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE
    assert "history_depth" in c.verdict.missing_inputs
    assert any("365" in n for n in c.verdict.notes)


def test_fundamental_satellite_routes_to_position_without_being_muted(
        full_params):
    c = plan.build(asset="ETH", direction=Direction.LONG_INCREASE, is_core=False,
                   tier=1, signal_scoring=_FUND_SCORING, price=1915.0,
                   closes=_trending_series(), ptf_value_usd=2714.0)
    assert c.horizon is Horizon.POSITION
    assert c.blocked_reason is None
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE


def test_insufficient_history_is_non_evaluable_not_non_viable(full_params):
    c = plan.build(asset="RENDER", direction=Direction.LONG_INCREASE,
                   is_core=False, tier=1, signal_scoring=_TECH_SCORING,
                   price=1.32, closes=_trending_series(n=40),
                   ptf_value_usd=2714.0)
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE
    assert "history_depth" in c.verdict.missing_inputs


def test_no_params_makes_plan_non_evaluable(no_params):
    c = plan.build(asset="RENDER", direction=Direction.LONG_INCREASE,
                   is_core=False, tier=1, signal_scoring=_TECH_SCORING,
                   price=100.0, closes=_trending_series(),
                   daily_bars=_bars(), ptf_value_usd=2714.0)
    assert c.emittable is False
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE


def test_full_chain_produces_a_verdict_and_never_raises(full_params):
    series = _trending_series()
    c = plan.build(asset="RENDER", direction=Direction.LONG_INCREASE,
                   is_core=False, tier=1, signal_scoring=_TECH_SCORING,
                   price=series[-1], closes=series, daily_bars=_bars(),
                   ptf_value_usd=2714.0, current_weight_pct=1.9,
                   daily_volume_usd=5e8, budget_consumed_usd=0.0)
    assert c.horizon is Horizon.SWING
    assert c.verdict is not None
    assert c.sigma_h_pct and c.sigma_h_pct > 0
    # Quel que soit le verdict, le motif est CHIFFRÉ et jamais vide.
    assert c.rejection_summary()


def test_sigma_degraded_flag_survives_to_the_candidate(full_params):
    series = _trending_series()
    c = plan.build(asset="RENDER", direction=Direction.LONG_INCREASE,
                   is_core=False, tier=1, signal_scoring=_TECH_SCORING,
                   price=series[-1], closes=series, daily_bars=None,
                   ptf_value_usd=2714.0, daily_volume_usd=5e8)
    assert c.sigma_estimator == "logret"
    assert c.sigma_degraded is True


def test_rank_orders_by_net_pnl_and_drops_non_emittable():
    a = plan.Candidate(asset="A", direction=Direction.LONG_INCREASE,
                       horizon=Horizon.SWING, entry=1, target=2, stop=0.5,
                       sizing=sz.Sizing(1.0, 100.0, 1000.0, 500.0, "budget"),
                       verdict=viability.ViabilityVerdict(
                           verdict=viability.Verdict.VIABLE,
                           expected_pnl_usd_net=12.0))
    b = plan.Candidate(asset="B", direction=Direction.LONG_INCREASE,
                       horizon=Horizon.SWING, entry=1, target=2, stop=0.5,
                       sizing=sz.Sizing(1.0, 100.0, 1000.0, 500.0, "budget"),
                       verdict=viability.ViabilityVerdict(
                           verdict=viability.Verdict.VIABLE,
                           expected_pnl_usd_net=30.0))
    c = plan.Candidate(asset="C", direction=Direction.LONG_INCREASE,
                       horizon=Horizon.SWING,
                       verdict=viability.ViabilityVerdict(
                           verdict=viability.Verdict.NON_VIABLE,
                           failed_conditions=["V3"]))
    out = plan.rank([a, b, c])
    assert [x.asset for x in out] == ["B", "A"]


def test_rejection_summary_is_quantified(full_params):
    c = plan.Candidate(
        asset="ETH", direction=Direction.LONG_INCREASE, horizon=Horizon.SWING,
        verdict=viability.ViabilityVerdict(
            verdict=viability.Verdict.NON_VIABLE,
            failed_conditions=["V1", "V2", "V3", "V4"],
            delta_required=0.1926, target_in_sigma=0.09, stop_in_sigma=0.07,
            expected_pnl_usd_net=-0.05, notional_usd=27.0))
    s = c.rejection_summary()
    assert "19.3 points" in s or "19,3" in s
    assert "0.09 sigma" in s
    assert "27 $" in s
