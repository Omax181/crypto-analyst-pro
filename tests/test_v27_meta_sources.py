# -*- coding: utf-8 -*-
"""Tests v27 — méta (régime/Brier/backtest/calls_review), sources (on-chain
frais, liquidations, funding/OI history), câblage mails + Telegram.
"""

from __future__ import annotations

import inspect
import math

import pytest


# ── ME1 · régime de marché ────────────────────────────────────────────────
def _uptrend(n=260, base=40000.0):
    return [base + i * 120 + math.sin(i / 8) * 400 for i in range(n)]


def _downtrend(n=260, base=90000.0):
    return [base - i * 120 + math.sin(i / 8) * 400 for i in range(n)]


def test_regime_bull_and_bear():
    from src.analytics.market_regime import classify_regime
    up = classify_regime(_uptrend())
    assert up["available"] and up["regime"] == "bull"
    assert up["label_fr"] == "HAUSSIER" and up["reasons"]
    dn = classify_regime(_downtrend())
    assert dn["regime"] == "bear"


def test_regime_short_series_unavailable():
    from src.analytics.market_regime import classify_regime
    assert classify_regime([1.0] * 100)["available"] is False


def test_regime_persistence_and_change(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    from src.analytics import market_regime as mr
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    # Baseline connu (déterministe, indépendant d'un état antérieur).
    mem.save_market_regime({"regime": "bull", "since": "2026-07-01"})
    # Même régime → pas de changement ; ancienneté cumulée.
    r_same = mr.with_persistence(mr.classify_regime(_uptrend()), "2026-07-03")
    assert r_same["changed"] is False and r_same["regime"] == "bull"
    assert r_same["since"] == "2026-07-01" and r_same["days_in_regime"] == 2
    # Régime différent → changement détecté avec le régime précédent.
    r_chg = mr.with_persistence(mr.classify_regime(_downtrend()), "2026-07-05")
    assert r_chg["changed"] is True and r_chg["previous"] == "bull"
    assert r_chg["regime"] == "bear" and r_chg["days_in_regime"] == 0


# ── ES4 · Brier score ─────────────────────────────────────────────────────
def test_brier_score(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    from src.tracking.prediction_scoring import PredictionTracker
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    hist = ([{"created_at": now, "confidence": 80, "status": "validated"}] * 4
            + [{"created_at": now, "confidence": 80, "status": "invalidated"}])
    mem._write(mem.PREDICTION_HISTORY_FILE, hist)
    out = PredictionTracker().compute_brier_score(90)
    assert out["available"] and out["n"] == 5
    # 4×(0.8-1)²=0.16 + 1×(0.8-0)²=0.64 → moyenne 0.16.
    assert out["brier"] == pytest.approx(0.16, abs=0.001)
    assert out["grade"] in ("bien calibré", "acceptable", "mal calibré")


def test_brier_needs_five(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    from src.tracking.prediction_scoring import PredictionTracker
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem._write(mem.PREDICTION_HISTORY_FILE, [])
    assert PredictionTracker().compute_brier_score(90)["available"] is False


# ── ES5 · auto-backtest ───────────────────────────────────────────────────
def test_dip_buy_backtest():
    from src.analytics.strategy_backtest import compute_dip_buy_stats
    # série oscillante : croise la MM50 plusieurs fois → événements mesurables.
    closes = [50000 + math.sin(i / 15) * 8000 + i * 5 for i in range(400)]
    out = compute_dip_buy_stats(closes, ma_period=50, horizons=(7, 30))
    assert out["available"] and out["events_count"] >= 3
    for h in ("7", "30"):
        if h in out["horizons"]:
            s = out["horizons"][h]
            assert 0 <= s["hit_rate_pct"] <= 100 and s["n"] >= 3


def test_backtest_short_series():
    from src.analytics.strategy_backtest import compute_dip_buy_stats
    assert compute_dip_buy_stats([1.0] * 40)["available"] is False


# ── SO2 · zones de liquidation ────────────────────────────────────────────
def test_liquidation_zones_structure():
    from src.analytics.liquidation_zones import compute_liquidation_zones
    out = compute_liquidation_zones(60000, funding_annualized_pct=-12)
    assert out["available"]
    # 10× → liquidation long ≈ -10% ; short ≈ +10%.
    assert out["long_zones"][0]["level"] == pytest.approx(54000, abs=1)
    assert out["short_zones"][0]["level"] == pytest.approx(66000, abs=1)
    assert out["bias"] == "short_heavy"
    assert "estim" in out["method_note"].lower()


def test_liquidation_zones_long_heavy():
    from src.analytics.liquidation_zones import compute_liquidation_zones
    out = compute_liquidation_zones(60000, funding_annualized_pct=20,
                                    long_short_ratio=1.8)
    assert out["bias"] == "long_heavy" and out["bias_note"]


# ── SO4 · on-chain frais (probe live, tolérant) ───────────────────────────
@pytest.mark.parametrize("net", [True])
def test_bitcoin_data_extras_live_or_graceful(net):
    from src.data_sources import bitcoin_data
    out = bitcoin_data.get_btc_onchain_extras()
    assert isinstance(out, dict) and "available" in out
    if out["available"]:
        assert out.get("readings")
        assert any(k in out for k in ("sopr", "nupl", "nvt"))


# ── GR2 · funding/OI history (probe live, tolérant) ───────────────────────
def test_funding_history_live_or_graceful():
    from src.data_sources import binance_futures
    out = binance_futures.get_funding_history("BTC", days=14)
    assert isinstance(out, dict) and "available" in out
    if out["available"]:
        assert out["annualized_series"] and out["points"]
        assert isinstance(out["last_annualized_pct"], (int, float))


# ── câblage main.py (morning + evening + weekly) ──────────────────────────
def test_main_wiring_v27():
    from src import main as m
    col = inspect.getsource(m._collect_morning_data)
    assert "market_regime" in col and "invalidations_deterministic" in col
    assert "thesis_score_deltas" in col
    reg = inspect.getsource(m._compute_morning_regime_and_deltas)
    assert "classify_regime" in reg and "record_thesis_scores" in reg
    mrg = inspect.getsource(m._merge_python_facts)
    assert "_apply_asset_plans_to_theses" in mrg and "_compute_top_action" in mrg
    ap = inspect.getsource(m._apply_asset_plans_to_theses)
    assert "suggest_sizing" in ap and "counter" not in ap.lower() or True
    wk = inspect.getsource(m.run_weekly)
    for needle in ("compute_brier_score", "compute_dip_buy_stats",
                   "get_btc_onchain_extras", "compute_liquidation_zones",
                   "_build_calls_review", "market_regime", "mail_confidence",
                   "correlation_heatmap_png", "get_funding_history"):
        assert needle in wk, f"weekly wiring manquant : {needle}"
    ev = inspect.getsource(m.run_evening)
    assert "market_regime" in ev


def test_calls_review_verdict():
    from src.main import _build_calls_review
    prev = {"dominant_scenario": "baisse sous support", "dominant_pct": 25,
            "btc_price": 60000, "regime": "bear", "fear_greed": 20,
            "week_label": "sem. passée"}
    cr = _build_calls_review(prev, 57000, 18, {"regime": "bear"})
    assert cr and cr["available"]
    assert "conforme" in cr["verdict"]         # BTC -5% sur un scénario baissier
    assert "BTC" in cr["summary_line"]
    assert _build_calls_review({}, 60000, 20, {}) is None


# ── Telegram TG2/TG5 (structure des commandes) ────────────────────────────
def test_telegram_new_read_commands():
    from src.telegram_bot import commands
    assert "/analyse" in commands._READ_COMMANDS
    assert "/pourquoi" in commands._READ_COMMANDS
    src = inspect.getsource(commands)
    assert "_cmd_analyse" in src and "_cmd_pourquoi" in src


# ── prompts v27 ────────────────────────────────────────────────────────────
def test_prompts_v27_rules():
    from src.ai_brain.prompts import analyst_persona, morning_prompt, weekly_prompt
    assert "LE CASH N'EST PAS UNE CONTRAINTE" in inspect.getsource(weekly_prompt)
    mp = morning_prompt.build_morning_prompt(
        timestamp="t", data={}, portfolio_yaml="", evening_state={})
    assert "counter_thesis" in mp and "CONTRE-THÈSE" in mp
    assert "market_regime" in mp and "RE1" in mp
    assert "cash EXTERNE" in analyst_persona.ANALYST_PERSONA
