# -*- coding: utf-8 -*-
"""V31 — FactStore, contrat de contenu, sources, métriques, run (Phases 4-6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core import content, metrics, params, registry, runlog
from src.core import source_result as sr
from src.core.book import Direction, RecommendationBook, State
from src.core.facts import FactStore, Staleness, Unit, UsageRight
from src.core.horizon import Horizon

UTC = timezone.utc


@pytest.fixture
def store():
    return FactStore()


@pytest.fixture
def no_params():
    params.reset_cache()
    params._cache = {}
    yield
    params.reset_cache()


@pytest.fixture
def metric_params():
    params.reset_cache()
    params._cache = {"n_min": 3}
    yield
    params.reset_cache()


# ══════════════════════════════════════════════════════════════════════════
# FactStore — I26, I27, I33, I34
# ══════════════════════════════════════════════════════════════════════════

def test_fact_formatting_delegates_to_the_single_authority(store):
    store.register("market.eth.price", 1915.01, Unit.USD_PRICE)
    store.register("plan.eth.upside", 4.4, Unit.PCT)
    store.register("plan.eth.rr", 1.2, Unit.RATIO)
    assert store.formatted("market.eth.price").endswith("$")
    assert store.formatted("plan.eth.upside") == "+4,4%"
    assert store.formatted("plan.eth.rr") == "1,20"


def test_unknown_fact_formats_as_absent_never_zero(store):
    assert store.formatted("nope") == "—"


def test_I33_derived_fact_inherits_worst_freshness(store):
    old = datetime.now(UTC) - timedelta(days=3)
    new = datetime.now(UTC)
    store.register("a", 1.0, Unit.RATIO, as_of=new)
    store.register("b", 2.0, Unit.RATIO, as_of=old, stale=True)
    d = store.derive("c", 3.0, Unit.RATIO, inputs=["a", "b"])
    assert d.as_of == old                      # MIN des as_of
    assert d.staleness is Staleness.STALE      # OU des staleness


def test_degraded_source_marks_the_fact_stale(store):
    res = sr.degraded("etf_flows", {"btc": 1}, "repli t.me")
    f = store.register("etf.btc", 101.7, Unit.USD_COMPACT, source=res)
    assert f.is_stale is True


def test_stale_facts_are_not_referenceable(store):
    store.register("fresh", 1.0, Unit.RATIO)
    store.register("old", 2.0, Unit.RATIO, stale=True)
    assert "fresh" in store.referenceable_ids()
    assert "old" not in store.referenceable_ids()


def test_I39_display_only_facts_never_reach_the_llm(store):
    store.register("public", 1.0, Unit.RATIO)
    store.register("secret", 2.0, Unit.RATIO,
                   usage_right=UsageRight.DISPLAY_ONLY)
    ids = {c["id"] for c in store.llm_context()}
    assert "public" in ids and "secret" not in ids


def test_store_is_sealed_before_the_llm_call(store):
    store.register("a", 1.0, Unit.RATIO)
    store.seal()
    with pytest.raises(RuntimeError):
        store.register("b", 2.0, Unit.RATIO)


# ══════════════════════════════════════════════════════════════════════════
# Contrat de contenu — I25 à I29
# ══════════════════════════════════════════════════════════════════════════

def test_I25_bare_numeral_in_authored_is_a_violation():
    assert content.validate_authored("Le MVRV est à 0,94.", {"x"})
    assert content.validate_authored("Le MVRV est à [[fact:x]].", {"x"}) == []


def test_unknown_or_stale_token_is_a_violation():
    problems = content.validate_authored("Valeur [[fact:inconnu]].", {"x"})
    assert problems and "inconnu" in problems[0]


def test_substitution_happens_after_escaping(store):
    store.register("p", 1915.0, Unit.USD_PRICE)
    out = content.substitute("<b>ETH</b> à [[fact:p]]", store.render_map())
    assert "&lt;b&gt;" in str(out)          # balise neutralisée
    assert "915" in str(out)                # valeur injectée


def test_rejected_field_falls_back_to_declaration_not_imitation(store):
    store.register("p", 1.0, Unit.RATIO)
    payload = {"counter_thesis": "Si BTC casse 63 050, la thèse tombe."}
    clean, rejections = content.apply_contract(payload, store)
    assert len(rejections) == 1
    assert str(clean["counter_thesis"]) == content.DECL_COUNTER
    # Le repli DIT ce qui manque, il n'invente pas de contre-thèse.
    assert "non disponible" in str(clean["counter_thesis"])


def test_rejected_omission_field_disappears(store):
    payload = {"sector_comment": "Le secteur AI gagne 2,03%."}
    clean, rejections = content.apply_contract(payload, store)
    assert "sector_comment" not in clean and len(rejections) == 1


def test_deterministic_fallback_uses_generated_text(store):
    store.register("x", 3.0, Unit.RATIO)
    payload = {"observation": "Score de 12 points."}
    clean, _ = content.apply_contract(
        payload, store, deterministic={"observation": "Signaux : [[fact:x]]."})
    assert "3,00" in str(clean["observation"])


def test_conforming_field_is_substituted_not_rejected(store):
    store.register("mvrv", 0.94, Unit.RATIO)
    payload = {"macro_reading": "MVRV à [[fact:mvrv]], zone basse."}
    clean, rejections = content.apply_contract(payload, store)
    assert rejections == []
    assert "0,94" in str(clean["macro_reading"])


def test_every_declaration_field_has_a_fallback_text():
    for path, spec in content.SCHEMA.items():
        if spec.fallback is content.FallbackLevel.DECLARATION:
            assert spec.fallback_text, path


def test_rejection_alarm_thresholds():
    assert content.rejection_alarm_level([]) is None
    one = [content.Rejection("a", "r", "d")]
    assert content.rejection_alarm_level(one) == "warning"
    assert content.rejection_alarm_level(one * 3) == "banner"


# ══════════════════════════════════════════════════════════════════════════
# Registre des sources — I32, I34
# ══════════════════════════════════════════════════════════════════════════

def test_criticality_is_derived_not_declared():
    assert registry.CATALOG["market.closes"].criticality == "blocking"
    assert registry.CATALOG["onchain"].criticality == "optional"
    assert registry.CATALOG["etf_flows"].criticality == "optional"


def test_missed_publications_ignores_weekends_by_construction():
    now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)          # lundi
    friday = datetime(2026, 8, 7, 22, 0, tzinfo=UTC)
    # Cadence 1 j, latence déclarée 1 j : vendredi soir lu lundi matin
    # représente une seule publication manquée, pas trois jours de retard.
    missed = registry.missed_publications(
        as_of=friday, cadence_days=1.0, latency_days=1, now=now)
    assert missed == 1


def test_missing_latency_makes_freshness_unknown():
    assert registry.missed_publications(
        as_of=datetime.now(UTC), cadence_days=1.0, latency_days=None) is None


def test_ok_source_with_missed_publication_becomes_degraded(no_params):
    spec = registry.CATALOG["onchain"]
    res = sr.ok("onchain", {"mvrv": 1.2},
                as_of=datetime.now(UTC) - timedelta(days=4))
    h = registry.assess(spec, res)
    assert h.status is sr.SourceStatus.DEGRADED


def test_dead_source_is_skipped_until_reprobe(tmp_path):
    spec = registry.CATALOG["etf_flows"]
    registry.mark_dead("etf_flows", spec, state_dir=tmp_path)
    assert registry.should_skip("etf_flows", state_dir=tmp_path) is True
    future = datetime.now(UTC) + timedelta(days=spec.dead_reprobe_days + 1)
    assert registry.should_skip("etf_flows", state_dir=tmp_path,
                                now=future) is False


def test_matrix_describes_every_source(no_params):
    results = {"market.spot": sr.ok("market.spot", {"BTC": 1}),
               "etf_flows": sr.unavailable("etf_flows",
                                           sr.Failure(http_status=403))}
    m = registry.matrix(results)
    assert len(m) == 2
    assert any("indisponible" in h.describe() for h in m)


# ══════════════════════════════════════════════════════════════════════════
# Métriques — I49 à I52
# ══════════════════════════════════════════════════════════════════════════

def _closed_book(tmp_path, outcomes):
    b = RecommendationBook(run_kind="morning", run_id="r",
                           state_dir=tmp_path / "bk")
    for i, state in enumerate(outcomes):
        rec, _ = b.emit(asset=f"A{i}", direction=Direction.LONG_INCREASE,
                        horizon=Horizon.SWING, entry=100.0, target=110.0,
                        stop=90.0, sizing={"notional_usd": 100.0},
                        viability={"round_trip_cost_pct": 0.2},
                        p_null=0.33, p_breakeven=0.35, delta_required=0.02)
        close = 112.0 if state is State.TARGET_HIT else 85.0
        b.evaluate_transitions(daily_closes={f"A{i}": close})
    return b


def test_I50_nothing_is_published_below_the_floor(tmp_path, metric_params):
    b = _closed_book(tmp_path, [State.TARGET_HIT, State.INVALIDATED])
    m = metrics.win_rate(b)
    assert m.published is False and m.value is None
    assert "3" in m.reason_unpublished


def test_absent_floor_publishes_nothing_at_all(tmp_path, no_params):
    b = _closed_book(tmp_path, [State.TARGET_HIT] * 10)
    assert metrics.win_rate(b).published is False


def test_I49_published_metric_declares_window_and_n(tmp_path, metric_params):
    b = _closed_book(tmp_path, [State.TARGET_HIT, State.TARGET_HIT,
                                State.INVALIDATED])
    m = metrics.win_rate(b)
    assert m.published is True
    assert m.n == 3 and m.window_label
    assert m.value == pytest.approx(66.7, abs=0.1)


def test_realized_edge_reports_bound_and_never_changes_params(tmp_path,
                                                              metric_params):
    b = _closed_book(tmp_path, [State.TARGET_HIT] * 4)
    before = params.get("delta_claimable")
    m = metrics.realized_edge(b)
    assert m.published is True
    assert m.upper_bound is not None
    assert params.get("delta_claimable") == before      # I44


def test_expired_never_counts_as_a_win_or_a_loss(tmp_path, metric_params):
    b = RecommendationBook(run_kind="morning", run_id="r",
                           state_dir=tmp_path / "bk")
    for i in range(4):
        b.emit(asset=f"E{i}", direction=Direction.LONG_INCREASE,
               horizon=Horizon.SWING, entry=100.0, target=110.0, stop=90.0,
               sizing={"notional_usd": 100.0}, viability={}, p_null=0.33,
               p_breakeven=0.35, delta_required=0.02)
    b.evaluate_transitions(daily_closes={f"E{i}": 100.0 for i in range(4)},
                           now=datetime.now(UTC) + timedelta(days=31))
    assert all(c.state_value is State.EXPIRED for c in b.all())
    assert metrics.win_rate(b).published is False        # aucune issue binaire


def test_metric_set_is_exactly_six(tmp_path, metric_params):
    b = _closed_book(tmp_path, [State.TARGET_HIT])
    out = metrics.all_metrics(b, health_matrix=[], candidates_total=0,
                              emitted=0, non_viable=0, non_evaluable=0,
                              reasons={}, authored_fields=0, rejections=0)
    assert len(out) == 6
    assert [m.key for m in out] == ["win_rate", "horizon_calibration",
                                    "realized_edge", "emission",
                                    "content_contract", "sources"]


# ══════════════════════════════════════════════════════════════════════════
# RunSummary et bandeau — I57, I58, BB2
# ══════════════════════════════════════════════════════════════════════════

def test_banner_is_absent_when_nothing_is_degraded():
    s = runlog.new_run("morning")
    assert runlog.degradation_banner(s) is None


def test_banner_enumerates_and_never_grades():
    s = runlog.new_run("morning")
    for d in runlog.build_degradations(
            health_matrix=[], non_evaluable=2,
            missing_params=["fee_rate"], rejections=3,
            sigma_degraded={"RENDER": "volatilité estimée sur clôtures"}):
        s.add_degradation(d)
    banner = runlog.degradation_banner(s)
    assert banner.startswith("Rapport partiel :")
    assert "%" not in banner and "/10" not in banner   # ni note ni pourcentage
    assert "non évaluable" in banner and "fee_rate" in banner


def test_phase_timing_is_recorded(tmp_path):
    s = runlog.new_run("morning")
    with s.phase("collect"):
        pass
    assert "collect" in s.phase_durations


def test_model_traceability_is_recorded():
    s = runlog.new_run("morning")
    s.note_model("analysis", "gemini-3.5-flash")
    assert s.models_used == [{"pass": "analysis", "model": "gemini-3.5-flash"}]


def test_summary_persists_and_reloads(tmp_path):
    s = runlog.new_run("morning")
    s.finish("success")
    runlog.persist(s, state_dir=tmp_path)
    last = runlog.load_last(state_dir=tmp_path)
    assert last["run_id"] == s.run_id and last["status"] == "success"


def test_watchdog_disabled_without_params(tmp_path, no_params):
    v = runlog.watchdog_verdict(state_dir=tmp_path)
    assert v["enabled"] is False


def test_watchdog_alerts_on_silence(tmp_path):
    params.reset_cache()
    params._cache = {"watchdog": {"max_silence_hours": 26, "channel": "telegram"}}
    try:
        s = runlog.new_run("morning")
        s.finish("success")
        runlog.persist(s, state_dir=tmp_path)
        ok = runlog.watchdog_verdict(state_dir=tmp_path)
        assert ok["alert"] is False
        late = runlog.watchdog_verdict(
            state_dir=tmp_path, now=datetime.now(UTC) + timedelta(hours=48))
        assert late["alert"] is True
    finally:
        params.reset_cache()
