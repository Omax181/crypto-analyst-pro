# -*- coding: utf-8 -*-
"""V31 — collecte, contexte, signaux, LLM, migration, chien de garde.

Couvre les modules d'INTERFACE entre le monde extérieur et le noyau : c'est là
que la v30 perdait l'information (transport effondré sur ``None``, paramètres
inventés, modèle de repli fictif).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.core import params, source_result as sr
from src.core.source_result import SourceStatus

UTC = timezone.utc


# ══════════════════════════════════════════════════════════════════════════
# Collecte — l'échec est CLASSÉ, jamais effondré
# ══════════════════════════════════════════════════════════════════════════

def test_a_raising_source_is_classified_not_swallowed(monkeypatch):
    from src.pipeline import collect
    monkeypatch.setattr(collect.registry, "should_skip", lambda *a, **k: False)

    def _boom():
        raise TimeoutError("réseau")

    res = collect._wrap("fear_greed", _boom)
    assert res.status is SourceStatus.UNAVAILABLE
    assert res.failure.retryable is True
    assert res.failure.exception_class == "TimeoutError"


def test_an_empty_payload_is_EMPTY_not_UNAVAILABLE(monkeypatch):
    from src.pipeline import collect
    monkeypatch.setattr(collect.registry, "should_skip", lambda *a, **k: False)
    res = collect._wrap("news", lambda: [])
    assert res.status is SourceStatus.EMPTY
    # EMPTY n'est PAS exploitable : le consommateur ne verra pas une liste vide
    # comme une absence d'actualité confirmée.
    assert res.usable is False


def test_a_partial_payload_is_DEGRADED_with_a_readable_reason(monkeypatch):
    from src.pipeline import collect
    monkeypatch.setattr(collect.registry, "should_skip", lambda *a, **k: False)
    res = collect._wrap("onchain", lambda: {"BTC": {"mvrv": 1.2}, "ETH": {}},
                        empty_when=lambda p: not any(p.values()),
                        degraded_when=lambda p: "chaîne(s) sans donnée : ETH"
                        if not p["ETH"] else None)
    assert res.status is SourceStatus.DEGRADED
    assert "ETH" in res.note
    assert res.usable is True


def test_a_dead_source_is_not_called_again_before_its_reprobe_date(monkeypatch):
    from src.pipeline import collect
    calls = []
    monkeypatch.setattr(collect.registry, "should_skip", lambda *a, **k: True)
    res = collect._wrap("etf_flows", lambda: calls.append(1) or {"x": 1})
    assert res.status is SourceStatus.DEAD
    assert calls == [], "une source DEAD ne doit pas être rappelée"


def test_evening_and_weekly_collect_partially(monkeypatch):
    from src.pipeline import collect
    monkeypatch.setattr(collect, "fear_greed", lambda: sr.empty("fear_greed"))
    monkeypatch.setattr(collect, "news", lambda: sr.empty("news"))
    monkeypatch.setattr(collect, "equities", lambda: sr.empty("equities"))
    monkeypatch.setattr(collect.registry, "mark_dead", lambda *a, **k: None)
    partial = collect.context(["BTC"], full=False)
    # `equities` a été retirée de la collecte (audit du 15/08/2026) : elle ne
    # produisait ni fait ni contenu, et pouvait pourtant dégrader le bandeau.
    assert set(partial) == {"fear_greed", "news"}
    assert "macro_calendar" not in partial


# ══════════════════════════════════════════════════════════════════════════
# Contexte — faits et valorisation
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def universe(monkeypatch):
    from src.pipeline import context as ctx_mod
    monkeypatch.setattr(ctx_mod, "load_portfolio", lambda: {
        "portfolio": {
            "BTC": {"quantity": 0.1, "value_usd": 6000.0, "pru": 40000.0,
                    "tier": 1},
            "RENDER": {"symbol": "RENDER", "quantity": 10.0,
                       "value_usd": 900.0, "pru": 60.0, "tier": 2},
        }})
    return ctx_mod


def test_portfolio_is_valued_live_and_weights_sum_to_one_hundred(universe):
    positions = universe.load_universe()
    spot = sr.ok("market.spot", {"BTC": {"price": 60000.0},
                                 "RENDER": {"price": 90.0}})
    total = universe.value_portfolio(positions, spot)
    assert total == pytest.approx(6900.0)
    assert sum(p.weight_pct for p in positions) == pytest.approx(100.0)


def test_a_missing_live_price_falls_back_to_the_last_known_snapshot(universe):
    positions = universe.load_universe()
    spot = sr.ok("market.spot", {"BTC": {"price": 60000.0}})
    total = universe.value_portfolio(positions, spot)
    # RENDER retombe sur sa valeur de référence, jamais sur zéro.
    assert total == pytest.approx(6000.0 + 900.0)
    render = [p for p in positions if p.symbol == "RENDER"][0]
    assert render.price is None and render.value_usd == 900.0


def test_facts_carry_their_source_and_never_invent_a_zero(universe):
    from src.core.facts import FactStore
    positions = universe.load_universe()
    spot = sr.ok("market.spot", {"BTC": {"price": 60000.0, "change_24h": 1.5},
                                 "RENDER": {"price": 90.0}})
    total = universe.value_portfolio(positions, spot)
    store = FactStore()
    universe.register_facts(store, positions=positions, ptf_value=total,
                            spot=spot, sources={})
    from src.core.formatter import NNBSP
    assert store.formatted("market.btc.price") == f"60{NNBSP}000{NNBSP}$"
    assert store.has("market.btc.chg24")
    # Aucune variation pour RENDER : le fait n'existe PAS (il ne vaut pas 0).
    assert not store.has("market.render.chg24")


def test_a_degraded_source_marks_its_facts_stale_and_unreferenceable(universe):
    from src.core.facts import FactStore
    positions = universe.load_universe()
    spot = sr.degraded("market.spot", {"BTC": {"price": 60000.0}},
                       "1/2 symboles résolus")
    total = universe.value_portfolio(positions, spot)
    store = FactStore()
    universe.register_facts(store, positions=positions, ptf_value=total,
                            spot=spot, sources={})
    assert "market.btc.price" in store.stale_ids()
    assert "market.btc.price" not in store.referenceable_ids()


def test_candidate_specs_cover_every_position_without_pre_filtering(universe):
    positions = universe.load_universe()
    spot = sr.ok("market.spot", {"BTC": {"price": 60000.0},
                                 "RENDER": {"price": 90.0}})
    total = universe.value_portfolio(positions, spot)
    specs = universe.candidate_specs(positions, ptf_value=total, closes={},
                                     bars={}, spot=spot, signals_by_asset={})
    # Aucun seuil d'éligibilité : tout le portefeuille est évalué, et chaque
    # rejet sera chiffré. Un pré-filtre serait une seconde autorité de décision.
    assert {s["asset"] for s in specs} == {"BTC", "RENDER"}


# ══════════════════════════════════════════════════════════════════════════
# Signaux — seuls les POIDS décident de l'horizon
# ══════════════════════════════════════════════════════════════════════════

def _series(n=120, amp=24.0):
    return [100.0 + amp * math.sin(i / 11.0) for i in range(n)]


def test_signals_expose_weights_by_category_and_nothing_else_decidable():
    from src.analytics import signals
    out = signals.evaluate(asset="X", tier=2, closes=_series(),
                           change_24h=-7.0)
    assert set(out) == {"asset", "signals", "weights", "technical"}
    # Aucun verdict d'éligibilité, aucun type de thèse, aucun plafond.
    assert "eligible" not in out and "thesis_type" not in out


def test_a_fundamental_dominant_asset_reaches_an_EMITTABLE_horizon():
    """Verrou anti-régression du biais anti-fondamental.

    Mesuré avant correction : « sous PRU + drawdown + MVRV bas » — le meilleur
    setup d'accumulation du profil — ne produisait aucun contrat, alors qu'un
    rebond purement technique en produisait un. Plus la thèse était solide,
    moins le système pouvait agir.
    """
    from src.analytics import signals
    from src.core.horizon import Horizon, determine_from_scoring
    out = signals.evaluate(asset="X", tier=2, closes=[100.0] * 60,
                           pru_gap_pct=-25.0, mvrv=0.8)
    decision = determine_from_scoring("X", False, 2, out)
    assert decision.horizon is Horizon.POSITION
    assert decision.emittable is True
    assert out["weights"]["fundamental_lt"] > out["weights"]["technical_struct"]


def test_a_stale_fundamental_signal_weighs_less():
    from src.analytics import signals
    fresh = signals.evaluate(asset="X", tier=2, mvrv=0.8)
    stale = signals.evaluate(asset="X", tier=2, mvrv=0.8, mvrv_stale=True)
    assert stale["weights"]["fundamental_lt"] < fresh["weights"]["fundamental_lt"]
    assert "datée" in stale["signals"][0]["label"]


# ══════════════════════════════════════════════════════════════════════════
# LLM — budget, repli honnête, périmètre
# ══════════════════════════════════════════════════════════════════════════

def test_I55_a_fallback_equal_to_the_primary_is_declared_as_no_fallback(
        monkeypatch):
    from src.ai_brain import llm
    monkeypatch.setenv("GEMINI_MODEL", "gemini-x")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-x")
    plan = llm.resolve_models()
    assert plan.fallback is None and plan.fallback_is_real is False
    assert "n'est pas un repli" in plan.note


def test_I54_no_call_is_launched_without_time_to_finish_it(monkeypatch):
    from src.ai_brain import llm
    from src.core import runlog
    summary = runlog.new_run("morning")
    session = llm.LLMSession(summary, job_budget_s=1.0)
    assert session.can_call() is False
    with pytest.raises(llm.LLMUnavailable):
        session.compose("morning", fact_context=[], engine_summary={})
    assert any("temps de job" in d for d in summary.degradations)


def test_I56_the_model_actually_used_is_traced(monkeypatch):
    from src.ai_brain import llm
    from src.core import runlog

    class _Client:
        last_used_model = "gemini-fallback"

        def generate_json(self, prompt, temperature=0.5):
            return {"macro_reading": "texte", "inventé": "hors périmètre"}

    monkeypatch.setenv("GEMINI_MODEL", "gemini-primary")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-fallback")
    summary = runlog.new_run("morning")
    session = llm.LLMSession(summary, client=_Client())
    out = session.compose("morning", fact_context=[], engine_summary={})
    assert summary.models_used == [{"pass": "morning",
                                    "model": "gemini-fallback"}]
    assert any("modèle de repli" in d for d in summary.degradations)
    # Le modèle ne peut pas élargir son propre périmètre.
    assert out == {"macro_reading": "texte"}


def test_the_prompt_forbids_digits_and_lists_only_fresh_facts():
    from src.ai_brain.prompts import v31_prompts
    prompt = v31_prompts.build(
        "morning",
        fact_context=[{"id": "a.b", "value": "1 000 $", "unit": "usd_price",
                       "stale": False, "source": "market.spot"},
                      {"id": "c.d", "value": "2,0%", "unit": "pct",
                       "stale": True, "source": None}],
        engine_summary={"run": "morning"})
    assert "AUCUN CHIFFRE" in prompt
    assert "[[fact:a.b]]" in prompt
    assert "[PÉRIMÉ — non référençable]" in prompt


# ══════════════════════════════════════════════════════════════════════════
# Migration — un contrat invalide n'est jamais « réparé »
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def legacy_state(tmp_path, monkeypatch):
    import json
    from scripts import migrate_v31
    state = tmp_path / "state"
    state.mkdir()
    (state / "active_recommendations.json").write_text(json.dumps([
        {"asset": "RENDER", "entry_price": 3.0, "ct_target": 3.6,
         "stop_loss": 2.7},
        # Stop AU-DESSUS de l'entrée : viole I4, non migrable.
        {"asset": "INJ", "entry_price": 5.09, "ct_target": 6.0,
         "stop_loss": 5.14},
        {"asset": "LINK", "entry_price": 12.0, "ct_target": 14.0},
    ]), encoding="utf-8")
    monkeypatch.setattr(migrate_v31, "STATE", state)
    monkeypatch.setattr(migrate_v31, "BOOK_DIR", state / "book")
    monkeypatch.setattr(migrate_v31, "ARCHIVE", state / "pre_v31")
    return migrate_v31


def test_a_contract_violating_I4_is_not_migrated(legacy_state):
    report = legacy_state.inspect()
    assert [m["asset"] for m in report["migratable"]] == ["RENDER"]
    rejected = {r["asset"]: r["reason"] for r in report["rejected"]}
    assert "stop < entry < target" in rejected["INJ"]
    assert "incomplets" in rejected["LINK"]


def test_migration_is_blocked_without_the_business_parameter(legacy_state,
                                                             monkeypatch):
    params.reset_cache()
    params._cache = {}
    monkeypatch.setattr("sys.argv", ["migrate_v31"])
    assert legacy_state.main() == 2
    params.reset_cache()


def test_marked_migration_imports_out_of_scoring(legacy_state):
    import json
    from src.core.book import State
    report = legacy_state.inspect()
    legacy_state.apply("mark", report)
    data = json.loads(
        (legacy_state.BOOK_DIR / "contracts.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["state"]["value"] == State.SUPERSEDED.value
    assert data[0]["scoring_regime"] == "pre_v31"
    assert (legacy_state.ARCHIVE / "active_recommendations.json").exists()


# ══════════════════════════════════════════════════════════════════════════
# Chien de garde — désactivé sans paramètre, jamais de seuil inventé
# ══════════════════════════════════════════════════════════════════════════

def test_watchdog_is_disabled_without_its_parameter():
    from src.core import runlog
    params.reset_cache()
    params._cache = {}
    verdict = runlog.watchdog_verdict()
    assert verdict == {"enabled": False,
                       "reason": "chien de garde non paramétré"}
    params.reset_cache()


def test_watchdog_alerts_beyond_the_configured_silence(tmp_path):
    from src.core import runlog
    params.reset_cache()
    params._cache = {"watchdog": {"max_silence_hours": 12, "channel": "telegram"}}
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    summary = runlog.RunSummary(run_id="r", kind="morning", started_at=old,
                                ended_at=old, status="success")
    runlog.persist(summary, state_dir=tmp_path)
    verdict = runlog.watchdog_verdict(state_dir=tmp_path)
    assert verdict["enabled"] and verdict["alert"]
    assert verdict["limit_hours"] == 12
    params.reset_cache()


def test_watchdog_script_never_sends_a_report(monkeypatch):
    """Le chien de garde alerte ; il ne produit aucun rapport (R10-1)."""
    import inspect
    from scripts import watchdog
    source = inspect.getsource(watchdog)
    assert "send_email" not in source
    assert "run_morning" not in source


# ══════════════════════════════════════════════════════════════════════════
# Graphiques — une seule figure, formatée par l'autorité unique
# ══════════════════════════════════════════════════════════════════════════

def test_chart_annotations_use_the_single_formatter():
    import inspect
    from src.reporting import charts
    source = inspect.getsource(charts)
    # Aucun formatage local : le contrôle porte sur le CODE, docstring exclue.
    body = source.split('"""', 2)[-1]
    assert "def _fmt_level" not in body
    assert ":,.0f" not in body and ":,.2f" not in body
    assert "fmt.price" in body and "fmt.pct" in body


def test_only_the_contract_chart_survives():
    from src.reporting import charts
    public = [n for n in dir(charts) if not n.startswith("_")
              and callable(getattr(charts, n))]
    assert "contract_png" in public
    for removed in ("chart_for_thesis", "charts_for_theses",
                    "portfolio_evolution_png", "price_bollinger_png",
                    "charts_for_tracked_recos"):
        assert removed not in public
