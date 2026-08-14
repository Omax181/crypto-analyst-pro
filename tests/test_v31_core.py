# -*- coding: utf-8 -*-
"""V31 — invariants du noyau, implémentés comme de VRAIES protections.

Chaque test porte le numéro d'invariant qu'il démontre. Les cas de régression
issus de l'audit v30 sont rejoués nommément : ils doivent désormais être
INEXPRIMABLES, pas seulement corrigés.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.core import params, viability
from src.core import formatter as fmt
from src.core import source_result as sr
from src.core import volatility as vol
from src.core import horizon as hz
from src.core.book import (
    BookWriteError, ContractValidityError, Direction, RecommendationBook,
    SCORE_BY_STATE, BINARY_OUTCOME_STATES, State, validate_contract,
)

UTC = timezone.utc

# Jeu de paramètres COMPLET, uniquement pour les tests qui exigent un verdict.
# Les valeurs n'ont aucune portée métier : elles servent à prouver la mécanique.
FULL_PARAMS = {
    "fee_rate": 0.001,
    "liquidity_bands": [
        {"min_daily_volume_usd": 0, "spread_pct": 0.10, "slippage_pct": 0.10},
        {"min_daily_volume_usd": 100_000_000, "spread_pct": 0.02,
         "slippage_pct": 0.02, "measured": True},
    ],
    "delta_claimable": 0.05,
    "p_target_max": 0.30,
    "p_stop_max": 0.20,
    "materiality_reference": "monthly_budget",
    "k3": 0.02,
    "monthly_budget": 500.0,
    "ticket_min": 40.0,
    "n_min": 20,
    "cooldown_days": 5,
}


@pytest.fixture
def no_params():
    params.reset_cache()
    params._cache = {}
    yield
    params.reset_cache()


@pytest.fixture
def full_params():
    params.reset_cache()
    params._cache = dict(FULL_PARAMS)
    yield
    params.reset_cache()


@pytest.fixture
def book(tmp_path):
    return RecommendationBook(run_kind="morning", run_id="run-test",
                              state_dir=tmp_path / "book")


# ══════════════════════════════════════════════════════════════════════════
# §9.3 — paramètres absents => NON_EVALUABLE, jamais de défaut
# ══════════════════════════════════════════════════════════════════════════

def test_params_absent_returns_none(no_params):
    assert params.fee_rate() is None
    assert params.delta_claimable() is None
    assert params.monthly_budget() is None


def test_params_empty_value_counts_as_absent():
    params.reset_cache()
    params._cache = {"materiality_reference": "", "liquidity_bands": []}
    try:
        assert params.materiality_reference() is None
        assert params.liquidity_bands() is None
    finally:
        params.reset_cache()


def test_missing_emission_params_is_ordered_and_complete(no_params):
    assert params.missing_emission_params() == list(params.EMISSION_BLOCKING)


def test_partial_liquidity_band_is_absent():
    params.reset_cache()
    params._cache = {"liquidity_bands": [{"min_daily_volume_usd": 0,
                                          "spread_pct": 0.02}]}
    try:
        assert params.liquidity_bands() is None
    finally:
        params.reset_cache()


def test_I20_no_params_means_non_evaluable_never_viable(no_params):
    v = viability.evaluate(
        upside_pct=10.0, downside_pct=5.0, sigma_h_pct=8.0,
        sigma_degraded=False, notional_usd=200.0, tranches=1,
        daily_volume_usd=1e9, ptf_value_usd=2700.0)
    assert v.verdict is viability.Verdict.NON_EVALUABLE
    assert v.is_viable is False
    assert v.missing_inputs


# ══════════════════════════════════════════════════════════════════════════
# I27 / I59 / I60 — autorité unique de formatage
# ══════════════════════════════════════════════════════════════════════════

def test_formatter_french_convention():
    assert fmt.price(64963) == f"64{fmt.NNBSP}963{fmt.NNBSP}$"
    # >= 1000 : 0 décimale (règle adaptative de la SPEC §1.3)
    assert fmt.price(1915.01) == f"1{fmt.NNBSP}915{fmt.NNBSP}$"
    assert fmt.price(196.77) == f"196,77{fmt.NNBSP}$"
    assert fmt.price(0.001223) == f"0,001223{fmt.NNBSP}$"
    assert fmt.pct(3.4) == "+3,4%"
    assert fmt.pct(-12.2) == f"{fmt.MINUS}12,2%"
    assert fmt.ratio(1.2) == "1,20"
    assert fmt.integer(534028) == f"534{fmt.NNBSP}028"


def test_formatter_absent_is_dash_never_zero():
    assert fmt.price(None) == fmt.ABSENT
    assert fmt.price(0) == fmt.ABSENT
    assert fmt.pct(None) == fmt.ABSENT
    assert fmt.usd(None) == fmt.ABSENT


def test_formatter_never_emits_minus_zero():
    assert fmt.pct(-0.02) == "+0,0%"


def test_I60_violation_detector_catches_v30_regressions():
    # Les quatre fautes réellement livrées en v30.1.
    assert fmt.find_format_violations("R:R 1.2")
    assert fmt.find_format_violations("EV 30j +0.9%")
    assert fmt.find_format_violations("S&P 500 7,758")
    assert fmt.find_format_violations("BoJ 0.84%")
    # Les sorties du formateur ne déclenchent jamais le détecteur.
    for s in (fmt.price(64963), fmt.pct(-12.2), fmt.ratio(1.2),
              fmt.integer(534028), fmt.compact_usd(2.29e12)):
        assert fmt.find_format_violations(s) == [], s


# ══════════════════════════════════════════════════════════════════════════
# §1.1 — classement d'erreur normatif (I30, I31, I32)
# ══════════════════════════════════════════════════════════════════════════

def test_classify_410_is_dead():
    r = sr.classify("coinmarketcal", http_status=410)
    assert r.status is sr.SourceStatus.DEAD
    assert r.failure.retryable is False


def test_classify_404_becomes_dead_after_three_runs():
    assert sr.classify("x", http_status=404, consecutive_404=2).status \
        is sr.SourceStatus.UNAVAILABLE
    assert sr.classify("x", http_status=404, consecutive_404=3).status \
        is sr.SourceStatus.DEAD


def test_classify_403_unavailable_non_retryable():
    r = sr.classify("farside", http_status=403)
    assert r.status is sr.SourceStatus.UNAVAILABLE
    assert r.failure.retryable is False


def test_classify_429_and_5xx_are_retryable():
    for code in (429, 500, 503):
        r = sr.classify("cg", http_status=code)
        assert r.status is sr.SourceStatus.UNAVAILABLE
        assert r.failure.retryable is True


def test_empty_is_distinct_from_unavailable():
    assert sr.empty("tg").status is sr.SourceStatus.EMPTY
    assert sr.empty("tg").usable is False


def test_degraded_is_usable_and_carries_provenance():
    r = sr.degraded("etf", {"btc": 1}, "repli t.me", fallback_rank=2)
    assert r.usable and r.degraded
    assert r.provenance.tier is sr.Tier.FALLBACK
    assert "repli 2" in r.provenance.label()


# ══════════════════════════════════════════════════════════════════════════
# §4.2 — volatilité
# ══════════════════════════════════════════════════════════════════════════

def test_aggregate_to_daily_collapses_intraday_candles():
    day = int(datetime(2026, 8, 7, tzinfo=UTC).timestamp() * 1000)
    h = 4 * 3600 * 1000
    candles = [[day + i * h, 100 + i, 105 + i, 95 + i, 102 + i] for i in range(6)]
    bars = vol.aggregate_to_daily(candles)
    assert len(bars) == 1
    assert bars[0].high == 110 and bars[0].low == 95
    assert bars[0].open == 100 and bars[0].close == 107


def test_parkinson_needs_minimum_bars():
    bars = [vol.DailyBar(f"2026-08-{i:02d}", 100, 102, 98, 100)
            for i in range(1, 10)]
    assert vol.parkinson_sigma(bars, 30) is None


def test_daily_sigma_prefers_parkinson_and_flags_fallback():
    bars = [vol.DailyBar(f"d{i}", 100, 103, 97, 100) for i in range(40)]
    est = vol.daily_sigma(daily_bars=bars, closes=None, window=30)
    assert est.estimator == "parkinson" and est.degraded is False

    closes = [100 * (1.01 ** (i % 5)) for i in range(60)]
    est2 = vol.daily_sigma(daily_bars=None, closes=closes, window=30)
    assert est2.estimator == "logret" and est2.degraded is True

    est3 = vol.daily_sigma(daily_bars=None, closes=None, window=30)
    assert est3.available is False and est3.degraded is True


def test_k_from_probability_matches_spec_reference_table():
    for p, expected in ((0.50, 0.674), (0.32, 0.994), (0.20, 1.282),
                        (0.13, 1.514), (0.05, 1.960)):
        assert vol.k_from_probability(p) == pytest.approx(expected, abs=0.005)


def test_touch_probability_is_inverse_of_k():
    k = vol.k_from_probability(0.20)
    assert vol.touch_probability(k * 8.0, 8.0) == pytest.approx(0.20, abs=1e-6)


def test_sigma_h_scales_with_sqrt_of_horizon():
    assert vol.sigma_h(0.015, 30) == pytest.approx(0.015 * math.sqrt(30))


# ══════════════════════════════════════════════════════════════════════════
# §4.1 — horizon (I24 : jamais choisi pour passer un gate)
# ══════════════════════════════════════════════════════════════════════════

def test_core_asset_gets_position_and_position_is_ACTIVE():
    """Le cœur va en POSITION — et POSITION émet.

    La v31 initiale désactivait POSITION au motif d'une profondeur
    « non fournie par le pipeline ». C'était une limite auto-infligée, et sa
    conséquence était que les thèses d'accumulation fondamentale — le cœur du
    profil — ne pouvaient produire AUCUN contrat.
    """
    d = hz.determine(asset="BTC", is_core=True, tier=0,
                     fundamental_weight=9, catalyst_weight=0,
                     technical_struct_weight=0)
    assert d.horizon is hz.Horizon.POSITION
    assert d.emittable is True
    assert "cœur" in d.reason


def test_no_horizon_is_disabled_anymore():
    assert all(s.enabled for s in hz.SPECS.values())
    assert all(s.disabled_reason is None for s in hz.SPECS.values())


def test_tier4_dust_gets_no_horizon_at_all():
    d = hz.determine(asset="SXT", is_core=False, tier=4,
                     fundamental_weight=0, catalyst_weight=9,
                     technical_struct_weight=0)
    assert d.horizon is None and d.emittable is False


def test_technical_dominant_satellite_is_swing():
    d = hz.determine(asset="RENDER", is_core=False, tier=1,
                     fundamental_weight=2, catalyst_weight=5,
                     technical_struct_weight=1)
    assert d.horizon is hz.Horizon.SWING and d.emittable is True


def test_weight_tie_falls_back_to_most_restrictive():
    d = hz.determine(asset="INJ", is_core=False, tier=1,
                     fundamental_weight=3, catalyst_weight=3,
                     technical_struct_weight=0)
    assert d.horizon is hz.Horizon.POSITION and d.emittable is True


def test_a_fundamental_thesis_is_no_longer_structurally_mute():
    """Le setup d'accumulation du profil DOIT pouvoir produire un contrat.

    « sous PRU + drawdown profond + MVRV bas » est le meilleur signal d'entrée
    selon le profil investisseur. Il était muet tant que POSITION l'était.
    """
    d = hz.determine(asset="RSR", is_core=False, tier=1,
                     fundamental_weight=9, catalyst_weight=0,
                     technical_struct_weight=4)
    assert d.horizon is hz.Horizon.POSITION
    assert d.emittable is True


def test_depth_min_is_derived_not_chosen():
    s = hz.SWING_SPEC
    assert s.depth_min == max(s.sigma_window_bars, s.longest_ma_period,
                              s.fib_window_bars)
    assert hz.POSITION_SPEC.depth_min == 365


def test_horizon_from_scoring_ignores_llm_field():
    scoring = {"signals": [{"category": "fundamental_lt", "weight": 6},
                           {"category": "catalyst", "weight": 1}],
               "thesis_type": "tactical"}  # champ LLM : doit être ignoré
    d = hz.determine_from_scoring("ETH", False, 1, scoring)
    assert d.horizon is hz.Horizon.POSITION


# ══════════════════════════════════════════════════════════════════════════
# §4.4 — viabilité (I8, I14)
# ══════════════════════════════════════════════════════════════════════════

def _eval(u, d, sigma_h, notional, *, tranches=1, vol_usd=1e9, ptf=2714.0):
    return viability.evaluate(
        upside_pct=u, downside_pct=d, sigma_h_pct=sigma_h,
        sigma_degraded=False, notional_usd=notional, tranches=tranches,
        daily_volume_usd=vol_usd, ptf_value_usd=ptf)


def test_regression_eth_2000_to_2015_is_rejected_by_several_conditions(full_params):
    """Cas de référence de l'audit : cible +0,75 %, stop −0,6 %, 27 $."""
    v = _eval(0.75, 0.6, 8.2, 27.0)
    assert v.verdict is viability.Verdict.NON_VIABLE
    # Le rejet est SUR-DÉTERMINÉ : aucune condition unique ne le porte.
    assert {"V1", "V2", "V3", "V4"} <= set(v.failed_conditions)
    # L'avantage exigé dépasse de plusieurs fois ce qui est revendicable :
    # le rejet est insensible au réglage de delta_claimable.
    assert v.delta_required > 3 * FULL_PARAMS["delta_claimable"]
    assert v.target_in_sigma < 0.12         # cible à 0,09 sigma
    assert v.expected_pnl_usd_net < 0.10    # espérance nette dérisoire


def test_delta_required_formula_is_cost_over_width(full_params):
    v = _eval(10.0, 5.0, 8.0, 200.0)
    expected = v.round_trip_cost_pct / (v.upside_pct + v.downside_pct)
    assert v.delta_required == pytest.approx(expected, abs=1e-4)
    assert v.p_null == pytest.approx(5.0 / 15.0, abs=1e-4)
    assert v.p_breakeven == pytest.approx(
        (5.0 + v.round_trip_cost_pct) / 15.0, abs=1e-4)


def test_ordinary_trade_passes_when_params_present(full_params):
    """Cible 2,5 sigma, stop 1,4 sigma, 900 $ : les quatre conditions passent."""
    v = _eval(20.0, 11.0, 8.0, 900.0)
    assert v.verdict is viability.Verdict.VIABLE
    assert v.failed_conditions == []
    assert v.delta_required < FULL_PARAMS["delta_claimable"]
    assert v.expected_pnl_usd_net > FULL_PARAMS["k3"] * FULL_PARAMS["monthly_budget"]


def test_expected_pnl_is_evaluated_at_the_claimed_edge(full_params):
    """pnl% = delta_claimable * (u + d) - c — cohérent avec V1, jamais à p=0,5."""
    v = _eval(20.0, 11.0, 8.0, 900.0)
    expected_pct = (FULL_PARAMS["delta_claimable"] * (20.0 + 11.0)
                    - v.round_trip_cost_pct)
    assert v.expected_pnl_usd_net == pytest.approx(
        expected_pct / 100.0 * 900.0, abs=1e-6)


def test_v3_never_rewards_a_losing_trade(full_params):
    """Une amplitude trop étroite reste perdante quelle que soit la taille.

    Régression du piège de la valeur absolue : un P&L attendu NÉGATIF ne peut
    plus franchir V3, et un notional énorme ne le sauve pas.
    """
    v = _eval(0.8, 0.7, 8.0, 5000.0)
    assert v.expected_pnl_usd_net < 0
    assert "V3" in v.failed_conditions


def test_size_cannot_rescue_a_structurally_unprofitable_trade(full_params):
    small = _eval(0.8, 0.7, 8.0, 100.0)
    huge = _eval(0.8, 0.7, 8.0, 50_000.0)
    assert "V3" in small.failed_conditions and "V3" in huge.failed_conditions


def test_v2_rejects_target_inside_horizon_noise(full_params):
    v = _eval(1.0, 9.0, 8.0, 300.0)
    assert "V2" in v.failed_conditions


def test_v4_rejects_dca_tranches_below_ticket(full_params):
    single = _eval(14.0, 7.0, 8.0, 60.0, tranches=1)
    split = _eval(14.0, 7.0, 8.0, 60.0, tranches=3)
    assert "V4" not in single.failed_conditions
    assert "V4" in split.failed_conditions


def test_illiquid_asset_pays_the_most_expensive_band(full_params):
    liquid = _eval(14.0, 7.0, 8.0, 200.0, vol_usd=1e9)
    illiquid = _eval(14.0, 7.0, 8.0, 200.0, vol_usd=1_000.0)
    assert illiquid.round_trip_cost_pct > liquid.round_trip_cost_pct


def test_I14_sizing_is_an_input_never_recomputed(full_params):
    """Un notional trop petit échoue V3/V4 ; il n'est JAMAIS relevé."""
    v = _eval(14.0, 7.0, 8.0, 5.0)
    assert v.notional_usd == 5.0
    assert set(v.failed_conditions) & {"V3", "V4"}


def test_sigma_degraded_is_propagated_to_verdict(full_params):
    v = viability.evaluate(
        upside_pct=14.0, downside_pct=7.0, sigma_h_pct=8.0,
        sigma_degraded=True, notional_usd=200.0, tranches=1,
        daily_volume_usd=1e9, ptf_value_usd=2714.0)
    assert v.sigma_degraded is True
    assert any("clôtures" in n for n in v.notes)


def test_missing_sigma_is_non_evaluable_not_non_viable(full_params):
    v = _eval(14.0, 7.0, None, 200.0)
    assert v.verdict is viability.Verdict.NON_EVALUABLE
    assert "sigma_h" in v.missing_inputs


# ══════════════════════════════════════════════════════════════════════════
# §1.4 / §3 — carnet : validité, autorité, machine à états
# ══════════════════════════════════════════════════════════════════════════

def test_regression_inj_stop_above_entry_is_inexpressible():
    """v30 : INJ entrée 5,09 $ / stop 5,14 $ — chimère de millésimes."""
    with pytest.raises(ContractValidityError):
        validate_contract("INJ", Direction.LONG_INCREASE, 5.09, 5.66, 5.14)


def test_validate_rejects_absurd_rr():
    with pytest.raises(ContractValidityError):
        validate_contract("X", Direction.LONG_INCREASE, 100.0, 200.0, 99.0)


def test_validate_long_reduce_requires_inverted_order():
    validate_contract("LINK", Direction.LONG_REDUCE, 8.17, 7.80, 8.60)
    with pytest.raises(ContractValidityError):
        validate_contract("LINK", Direction.LONG_REDUCE, 8.17, 8.60, 7.80)


def test_I45_I46_evening_and_weekly_cannot_write(tmp_path):
    for kind in ("evening", "weekly"):
        b = RecommendationBook(run_kind=kind, run_id="r",
                               state_dir=tmp_path / kind)
        assert b.writable is False
        with pytest.raises(BookWriteError):
            b.emit(asset="RENDER", direction=Direction.LONG_INCREASE,
                   horizon=hz.Horizon.SWING, entry=1.32, target=1.60,
                   stop=1.15, sizing={}, viability={}, p_null=None,
                   p_breakeven=None, delta_required=None)
        with pytest.raises(BookWriteError):
            b.commit()


def _emit(book, asset="RENDER", entry=1.32, target=1.60, stop=1.15):
    return book.emit(
        asset=asset, direction=Direction.LONG_INCREASE,
        horizon=hz.Horizon.SWING, entry=entry, target=target, stop=stop,
        sizing={"pct_ptf": 1.0, "notional_usd": 200.0},
        viability={"verdict": "VIABLE", "round_trip_cost_pct": 0.24},
        p_null=0.35, p_breakeven=0.37, delta_required=0.017)


def test_emission_creates_active_contract(book):
    rec, action = _emit(book)
    assert action == "created" and rec.is_active
    assert len(book.active()) == 1
    assert rec.scored_contract["horizon"] == hz.Horizon.SWING.value


def test_I15_reissue_updates_plan_but_freezes_scored_contract(book):
    rec, _ = _emit(book)
    frozen = dict(rec.scored_contract)
    rec2, action = _emit(book, entry=1.28, target=1.90, stop=1.05)
    assert action == "revised"
    assert rec2 is rec
    assert rec.scored_contract == frozen           # contrat figé
    assert len(rec.operational_plan) == 2          # plan opérationnel versionné
    assert rec.counters["reissues"] == 1


def test_opposite_direction_supersedes(book):
    rec, _ = _emit(book)
    book.emit(asset="RENDER", direction=Direction.LONG_REDUCE,
              horizon=hz.Horizon.SWING, entry=1.40, target=1.20, stop=1.55,
              sizing={"notional_usd": 100.0}, viability={}, p_null=None,
              p_breakeven=None, delta_required=None)
    assert rec.state_value is State.SUPERSEDED
    assert len(book.active()) == 1


def test_transition_priority_stop_beats_target(book):
    """Garde la plus défavorable si plusieurs sont vraies sur la même clôture."""
    rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
    fired = book.evaluate_transitions(daily_closes={"RENDER": 85.0})
    assert fired[0]["to"] == State.INVALIDATED.value
    assert rec.state_value is State.INVALIDATED


def test_target_hit_records_realized_pnl_net_of_cost(book):
    rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
    book.evaluate_transitions(daily_closes={"RENDER": 112.0})
    assert rec.state_value is State.TARGET_HIT
    # 12 % bruts - 0,24 % de coût, appliqués au notional réel.
    assert rec.outcome["realized_pnl_pct"] == pytest.approx(11.76, abs=1e-6)
    assert rec.outcome["realized_pnl_usd_net"] == pytest.approx(23.52, abs=1e-6)


def test_expiry_is_neutral_and_distinct_from_invalidated(book):
    rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
    later = datetime.now(UTC) + timedelta(days=31)
    book.evaluate_transitions(daily_closes={"RENDER": 100.0}, now=later)
    assert rec.state_value is State.EXPIRED
    assert SCORE_BY_STATE[State.EXPIRED] == 0
    assert State.EXPIRED not in BINARY_OUTCOME_STATES
    assert State.INVALIDATED in BINARY_OUTCOME_STATES


def test_terminal_states_scoring_table_is_closed():
    assert SCORE_BY_STATE[State.TARGET_HIT] == 1
    assert SCORE_BY_STATE[State.INVALIDATED] == -1
    assert SCORE_BY_STATE[State.SUPERSEDED] is None
    assert SCORE_BY_STATE[State.CANCELLED] is None


def test_no_transition_from_terminal_state(book):
    rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
    book.evaluate_transitions(daily_closes={"RENDER": 85.0})
    fired = book.evaluate_transitions(daily_closes={"RENDER": 80.0})
    assert fired == []                              # idempotent, pas d'erreur
    assert rec.state_value is State.INVALIDATED


def test_reemission_blocked_after_invalidation(book, full_params):
    rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
    book.evaluate_transitions(daily_closes={"RENDER": 85.0})
    # Stop à peine déplacé : (a) non satisfaite, (b) cooldown non écoulé,
    # (c) même horizon -> bloqué.
    blocked = book.reemission_blocked("RENDER", Direction.LONG_INCREASE,
                                      89.0, hz.Horizon.SWING, sigma_h_pct=8.0)
    assert blocked is not None
    # Stop réellement plus profond (> 1 sigma_H) -> autorisé.
    assert book.reemission_blocked("RENDER", Direction.LONG_INCREASE,
                                   80.0, hz.Horizon.SWING,
                                   sigma_h_pct=8.0) is None


def test_consecutive_expired_is_tracked(book):
    for i in range(2):
        rec, _ = _emit(book, entry=100.0, target=110.0, stop=90.0)
        book.evaluate_transitions(
            daily_closes={"RENDER": 100.0},
            now=datetime.now(UTC) + timedelta(days=31 + i))
    assert book.consecutive_expired("RENDER",
                                    Direction.LONG_INCREASE.value) == 2


def test_I18_budget_counts_recommended_notionals(book):
    _emit(book, asset="RENDER")
    _emit(book, asset="FET")
    assert book.budget_consumed() == pytest.approx(400.0)


def test_cancel_is_the_only_write_allowed_outside_morning(tmp_path):
    b = RecommendationBook(run_kind="morning", run_id="r",
                           state_dir=tmp_path / "b")
    _emit(b)
    b.commit()
    ro = RecommendationBook(run_kind="evening", run_id="r2",
                            state_dir=tmp_path / "b")
    assert ro.cancel("RENDER") == 1
    assert ro.active() == []


def test_I53_commit_persists_and_reloads(tmp_path):
    b = RecommendationBook(run_kind="morning", run_id="r",
                           state_dir=tmp_path / "b")
    _emit(b)
    b.commit()
    again = RecommendationBook(run_kind="morning", run_id="r2",
                               state_dir=tmp_path / "b")
    assert len(again.active()) == 1
    assert again.active()[0].asset == "RENDER"


def test_I61_events_are_segmented_by_month(tmp_path):
    b = RecommendationBook(run_kind="morning", run_id="r",
                           state_dir=tmp_path / "b")
    _emit(b)
    b.commit()
    segment = datetime.now(UTC).strftime("%Y-%m")
    assert (tmp_path / "b" / f"events-{segment}.json").exists()


def test_view_is_the_single_read_surface(book):
    _emit(book)
    v = book.view()
    assert v["counts"]["active"] == 1
    assert v["active"][0]["asset"] == "RENDER"


def test_upside_downside_are_positive_and_direction_agnostic(book):
    inc, _ = _emit(book, entry=100.0, target=110.0, stop=95.0)
    assert inc.upside_pct() == pytest.approx(10.0)
    assert inc.downside_pct() == pytest.approx(5.0)
    red, _ = book.emit(asset="LINK", direction=Direction.LONG_REDUCE,
                       horizon=hz.Horizon.SWING, entry=100.0, target=90.0,
                       stop=105.0, sizing={"notional_usd": 100.0},
                       viability={}, p_null=None, p_breakeven=None,
                       delta_required=None)
    assert red.upside_pct() == pytest.approx(10.0)
    assert red.downside_pct() == pytest.approx(5.0)
