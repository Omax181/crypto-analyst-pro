# -*- coding: utf-8 -*-
"""V31 — verrous sur les trois blockers levés avant le premier run décisionnel.

B1  paramètres économiques : présents, cohérents, et V3 économiquement PERTINENT
B2  horizon POSITION : réellement actif, thèses fondamentales réellement traitées
B3  historical_treatment : migration exécutable, premier run sûr
"""
from __future__ import annotations

import json
import math

import pytest

from src.core import params, plan as plan_mod, viability
from src.core.book import Direction, RecommendationBook
from src.core.horizon import (POSITION_SPEC, SPECS, SWING_SPEC, Horizon,
                              determine)
from src.core.volatility import DailyBar, daily_sigma, sigma_h
from src.pipeline.market import DAILY_SERIES_DAYS


@pytest.fixture
def shipped():
    """Paramètres tels que LIVRÉS dans config/params.yaml."""
    params.reset_cache()
    params._cache = None
    yield
    params.reset_cache()


def _long_series(n=420, amp=45.0):
    base = [140.0 + amp * math.sin(i / 38.0) for i in range(n - 30)]
    start = base[-1]
    return base + [start + (140.0 - start) * (k + 1) / 30 for k in range(30)]


def _bars(n, amp=0.012, b=100.0):
    return [DailyBar(f"d{i}", b, b * (1 + amp), b * (1 - amp), b)
            for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════
# B1 — PARAMÈTRES ÉCONOMIQUES
# ══════════════════════════════════════════════════════════════════════════

def test_the_nine_blocking_parameters_are_present_and_coherent(shipped):
    assert params.missing_emission_params() == []
    assert params.incoherent_params() == {}
    assert params.disabled_features() == []


def test_every_shipped_value_is_within_its_structural_bounds(shipped):
    assert 0.0 <= params.fee_rate() < 0.05
    assert 0.0 < params.delta_claimable() < 1.0
    assert 0.0 < params.p_stop_max() < params.p_target_max() < 1.0
    assert params.k3() > 0
    assert 0 < params.ticket_min() <= params.monthly_budget()
    assert params.materiality_reference() in params.MATERIALITY_REFERENCES
    bands = params.liquidity_bands()
    assert bands and all(b["spread_pct"] >= 0 and b["slippage_pct"] >= 0
                         for b in bands)


def test_liquidity_cost_grows_when_liquidity_falls(shipped):
    """Le biais de coût est CHOISI : moins liquide => plus cher, jamais l'inverse."""
    deep, _ = viability.round_trip_cost(daily_volume_usd=1e9,
                                        notional_usd=500.0)
    thin, _ = viability.round_trip_cost(daily_volume_usd=1e5,
                                        notional_usd=500.0)
    assert thin > deep


def test_cost_is_declared_estimated_not_measured(shipped):
    """Un coût non mesuré ne doit jamais se présenter comme mesuré."""
    _, conf = viability.round_trip_cost(daily_volume_usd=1e9,
                                        notional_usd=500.0)
    assert conf is viability.CostConfidence.ESTIMATED


@pytest.mark.parametrize("u,d,expected_viable", [
    (20.0, 25.0, True),      # SWING satellite typique : franchit V3
    (16.0, 20.0, True),      # plancher de bruit tout juste respecté
    (3.0, 3.0, False),       # amplitude trop faible : non matériel
    (1.0, 1.0, False),       # bruit pur
])
def test_V3_is_demanding_without_being_sterile(shipped, u, d, expected_viable):
    """V3 doit écarter le bruit SANS interdire un contrat normal.

    Un gate que rien ne franchit n'est pas exigeant : il est inopérant.
    """
    v = viability.evaluate(upside_pct=u, downside_pct=d, sigma_h_pct=12.0,
                           sigma_degraded=False,
                           notional_usd=params.monthly_budget(), tranches=1,
                           daily_volume_usd=5e8, ptf_value_usd=10000.0)
    assert v.is_viable is expected_viable, (u, d, v.failed_conditions,
                                            v.expected_pnl_usd_net)


def test_the_materiality_threshold_stays_reachable_at_full_size(shipped):
    """Amplitude minimale exigée par V3 au notional plein — bornée et lisible."""
    cost, _ = viability.round_trip_cost(daily_volume_usd=5e8,
                                        notional_usd=params.monthly_budget())
    floor = params.k3() * params.monthly_budget()
    needed = (floor * 100.0 / params.monthly_budget() + cost) \
        / params.delta_claimable()
    # 40 % d'amplitude totale est déjà large pour un SWING ; au-delà, le gate
    # deviendrait inatteignable en pratique.
    assert needed <= 40.0, f"V3 exige {needed:.1f} % d'amplitude totale"


def test_k3_trims_the_low_tail_without_decimating_the_distribution(shipped):
    """CALIBRAGE MESURÉ de k3, pas supposé.

    Un gate de matérialité doit écarter la queue basse des contrats
    structurellement valides (V1 et V2 franchis), pas la majorité d'entre eux.
    Trop bas il est inopérant, trop haut il stérilise le moteur.

    Grille : sigma_H de 6 à 25 % (satellites SWING jusqu'à POSITION), cible et
    invalidation placées du plancher de bruit jusqu'à 2,5 fois ce plancher.
    """
    from src.core.volatility import k_from_probability
    k2 = k_from_probability(params.p_target_max())
    k2p = k_from_probability(params.p_stop_max())
    structural = passed = 0
    for s in (6, 8, 10, 12, 15, 18, 22, 25):
        for mu in (1.0, 1.25, 1.5, 2.0, 2.5):
            for md in (1.0, 1.25, 1.5, 2.0, 2.5):
                v = viability.evaluate(
                    upside_pct=k2 * s * mu, downside_pct=k2p * s * md,
                    sigma_h_pct=s, sigma_degraded=False,
                    notional_usd=params.monthly_budget(), tranches=1,
                    daily_volume_usd=5e8, ptf_value_usd=30000.0)
                if {"V1", "V2"} & set(v.failed_conditions):
                    continue
                structural += 1
                if v.is_viable:
                    passed += 1
    ratio = passed / structural
    assert structural >= 100, "grille trop pauvre pour conclure"
    assert 0.55 <= ratio <= 0.95, (
        f"k3={params.k3()} retient {ratio:.0%} des contrats valides — "
        f"hors de la plage « tranche la queue basse »")


def test_an_incoherent_value_is_treated_exactly_like_an_absent_one():
    """Une valeur présente mais fausse est plus dangereuse qu'une absente."""
    base = {"fee_rate": 0.001,
            "liquidity_bands": [{"min_daily_volume_usd": 0, "spread_pct": 0.02,
                                 "slippage_pct": 0.02}],
            "delta_claimable": 0.05, "p_target_max": 0.30, "p_stop_max": 0.20,
            "materiality_reference": "monthly_budget", "k3": 0.01,
            "monthly_budget": 500.0, "ticket_min": 40.0}
    broken = [
        ("p_stop_max", 0.40, "invalidation plus exposée que la cible"),
        ("p_target_max", 1.5, "probabilité hors ]0,1["),
        ("delta_claimable", 0.0, "avantage nul"),
        ("k3", -1.0, "multiple négatif"),
        ("ticket_min", 5000.0, "ticket au-dessus du budget"),
        ("materiality_reference", "inventée", "référence inconnue"),
        ("fee_rate", 0.5, "frais aberrants"),
    ]
    for key, bad_value, label in broken:
        params.reset_cache()
        params._cache = dict(base, **{key: bad_value})
        assert key in params.incoherent_params(), label
        assert key in params.missing_emission_params(), label
        v = viability.evaluate(upside_pct=20, downside_pct=25, sigma_h_pct=12,
                               sigma_degraded=False, notional_usd=500,
                               tranches=1, daily_volume_usd=5e8,
                               ptf_value_usd=10000)
        assert v.verdict is viability.Verdict.NON_EVALUABLE, label
        assert v.is_viable is False, label
    params.reset_cache()


def test_an_incoherent_parameter_never_degrades_into_NON_VIABLE(shipped):
    """NON_EVALUABLE prime sur NON_VIABLE : on ne conclut pas sans référentiel."""
    params.reset_cache()
    params._cache = dict(params._load() or {}, p_stop_max=0.9)
    c = plan_mod.build(asset="X", direction=Direction.LONG_INCREASE,
                       is_core=False, tier=1,
                       signal_scoring={"signals": [
                           {"category": "technical_struct", "weight": 4}]},
                       price=140.0, closes=_long_series(),
                       daily_bars=_bars(40), ptf_value_usd=10000.0,
                       current_weight_pct=3.0, daily_volume_usd=5e8)
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE
    assert "p_stop_max" in c.verdict.missing_inputs
    params.reset_cache()


# ══════════════════════════════════════════════════════════════════════════
# B2 — HORIZON / POSITION
# ══════════════════════════════════════════════════════════════════════════

def test_both_horizons_are_active():
    assert SWING_SPEC.enabled and POSITION_SPEC.enabled
    assert all(s.disabled_reason is None for s in SPECS.values())


def test_the_pipeline_supplies_what_the_longest_horizon_demands():
    """La profondeur collectée est DÉRIVÉE des horizons, pas choisie."""
    assert DAILY_SERIES_DAYS >= max(s.depth_min for s in SPECS.values()
                                    if s.enabled)
    assert POSITION_SPEC.depth_min == 365


def test_a_fundamental_accumulation_thesis_produces_a_real_contract(shipped):
    """LE verrou de ce cycle : la thèse d'accumulation doit ÉMETTRE.

    Mesuré avant correction : « sous PRU + drawdown + MVRV bas » — le meilleur
    setup du profil contrarian — ne produisait aucun contrat, tandis qu'un
    rebond purement technique en produisait un.
    """
    c = plan_mod.build(
        asset="TAO", direction=Direction.LONG_INCREASE, is_core=True, tier=1,
        signal_scoring={"signals": [
            {"category": "fundamental_lt", "weight": 6},
            {"category": "technical_struct", "weight": 2}]},
        price=140.0, closes=_long_series(), daily_bars=_bars(180),
        ptf_value_usd=12000.0, current_weight_pct=6.0,
        position_value_usd=720.0, daily_volume_usd=8e8)
    assert c.horizon is Horizon.POSITION
    assert c.blocked_reason is None
    assert c.verdict.is_viable, (c.verdict.failed_conditions,
                                 c.verdict.missing_inputs, c.verdict.notes)
    assert c.emittable is True
    assert c.sigma_h_pct > 0


def test_a_core_asset_can_now_hold_a_contract(shipped):
    d = determine(asset="BTC", is_core=True, tier=1, fundamental_weight=9,
                  catalyst_weight=0, technical_struct_weight=0)
    assert d.horizon is Horizon.POSITION and d.emittable is True


def test_insufficient_history_yields_a_QUANTIFIED_refusal(shipped):
    """Un historique court refuse — en CHIFFRANT, jamais catégoriquement."""
    c = plan_mod.build(
        asset="NEW", direction=Direction.LONG_INCREASE, is_core=True, tier=1,
        signal_scoring={"signals": [{"category": "fundamental_lt", "weight": 6}]},
        price=100.0, closes=[100.0 + i * 0.1 for i in range(120)],
        daily_bars=_bars(30), ptf_value_usd=10000.0, current_weight_pct=3.0,
        daily_volume_usd=5e8)
    assert c.horizon is Horizon.POSITION
    assert c.blocked_reason is None
    assert c.verdict.verdict is viability.Verdict.NON_EVALUABLE
    assert "history_depth" in c.verdict.missing_inputs
    summary = c.rejection_summary()
    assert "365" in summary and "120" in summary


def test_the_horizon_is_never_chosen_to_pass_a_gate(shipped):
    """I24 — l'horizon dépend des POIDS, jamais du résultat économique."""
    weights = {"fundamental_weight": 9, "catalyst_weight": 0,
               "technical_struct_weight": 2}
    first = determine(asset="X", is_core=False, tier=1, **weights)
    for budget in (50.0, 500.0, 50000.0):
        params.reset_cache()
        params._cache = dict(params._load() or {}, monthly_budget=budget)
        again = determine(asset="X", is_core=False, tier=1, **weights)
        assert again.horizon is first.horizon
    params.reset_cache()


def test_a_sigma_window_shorter_than_the_horizon_is_flagged():
    """Extrapoler sigma sans le dire serait un mensonge silencieux."""
    short = daily_sigma(daily_bars=_bars(30), closes=None,
                        window=POSITION_SPEC.sigma_window_bars)
    full = daily_sigma(daily_bars=_bars(180), closes=None,
                       window=POSITION_SPEC.sigma_window_bars)
    assert short.degraded is True and "extrapolé" in short.reason
    assert full.degraded is False and full.reason is None
    assert short.value == pytest.approx(full.value, rel=1e-6)


def test_position_sigma_scales_with_its_own_horizon():
    est = daily_sigma(daily_bars=_bars(180), closes=None, window=180)
    s_swing = sigma_h(est.value, SWING_SPEC.days)
    s_pos = sigma_h(est.value, POSITION_SPEC.days)
    assert s_pos > s_swing
    assert s_pos == pytest.approx(est.value * math.sqrt(180))


def test_a_long_moving_average_outranks_a_round_number(shipped):
    """MM200 doit primer au clustering, sinon POSITION perd son ancrage."""
    from src.core.levels import _rank
    assert _rank("MM200") < _rank("seuil rond")
    assert _rank("MM200") < _rank("MM20")
    assert _rank("MM50") == _rank("MM200")


def test_degradation_banner_states_the_REAL_reason(shipped):
    from src.core import runlog
    lines = runlog.build_degradations(
        health_matrix=[], non_evaluable=0, missing_params=[], rejections=0,
        sigma_degraded={"TAO": "volatilité estimée sur 30 bougies pour une "
                               "fenêtre de 180 — horizon extrapolé",
                        "RSR": "OHLC indisponible — volatilité estimée sur "
                               "clôtures (sous-estimation)"})
    joined = " | ".join(lines)
    assert "TAO" in joined and "RSR" in joined
    assert "extrapolé" in joined and "clôtures" in joined
    # Les deux causes ne sont PAS confondues.
    assert len(lines) == 2


# ══════════════════════════════════════════════════════════════════════════
# B3 — HISTORICAL_TREATMENT ET PREMIER RUN
# ══════════════════════════════════════════════════════════════════════════

def test_historical_treatment_is_decided(shipped):
    assert params.historical_treatment() == "purge"
    assert params.feature_enabled("migration") is True


def test_migration_archives_without_destroying(tmp_path, shipped, monkeypatch):
    from scripts import migrate_v31
    state = tmp_path / "state"
    state.mkdir()
    (state / "active_recommendations.json").write_text(json.dumps([
        {"asset": "TAO", "entry_price": 300.0, "ct_target": 360.0,
         "stop_loss": 270.0},
        {"asset": "INJ", "entry_price": 5.09, "ct_target": 6.0,
         "stop_loss": 5.14},
    ]), encoding="utf-8")
    monkeypatch.setattr(migrate_v31, "STATE", state)
    monkeypatch.setattr(migrate_v31, "BOOK_DIR", state / "book")
    monkeypatch.setattr(migrate_v31, "ARCHIVE", state / "pre_v31")

    report = migrate_v31.inspect()
    assert [m["asset"] for m in report["migratable"]] == ["TAO"]
    assert report["rejected"][0]["asset"] == "INJ"

    migrate_v31.apply("purge", report)
    # RIEN n'est détruit : l'ancien état est déplacé, pas supprimé.
    assert (state / "pre_v31" / "active_recommendations.json").exists()
    assert not (state / "active_recommendations.json").exists()
    # Le carnet V31 ne reçoit AUCUN contrat hérité.
    assert not (state / "book" / "contracts.json").exists()


def test_first_run_starts_on_an_empty_book_and_says_so(tmp_path, shipped):
    book = RecommendationBook(run_kind="morning", run_id="first",
                              state_dir=tmp_path / "book")
    assert book.all() == []
    assert book.budget_consumed() == 0.0
    view = book.view()
    assert view["counts"] == {"active": 0, "terminal_30d": 0}


def test_first_run_cannot_exceed_the_monthly_budget(tmp_path, shipped):
    """Budget plein au premier run : le plafond reste opposable."""
    from src.core import runlog
    from src.pipeline import runs
    summ = runlog.new_run("morning")
    book = RecommendationBook(run_kind="morning", run_id=summ.run_id,
                              state_dir=tmp_path / "book")
    ctx = runs.RunContext(kind="morning", summary=summ, book=book)
    specs = [{
        "asset": a, "direction": Direction.LONG_INCREASE, "is_core": False,
        "tier": 1, "signal_scoring": {"signals": [
            {"category": "technical_struct", "weight": 4}]},
        "price": 140.0, "closes": _long_series(), "daily_bars": _bars(40),
        "ptf_value_usd": 60000.0, "weight_pct": 1.0,
        "position_value_usd": 600.0, "daily_volume_usd": 5e8}
        for a in ("A", "B", "C", "D")]
    runs.evaluate_candidates(ctx, specs)
    runs.emit_viable(ctx)
    assert ctx.book.budget_consumed() <= params.monthly_budget() + 0.01