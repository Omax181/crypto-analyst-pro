# -*- coding: utf-8 -*-
"""Tests v27 — moteur de plan par actif (TH1/TH2/ES1/ES2/ES3/RE1/RE2/RE3).

Le plan est 100% déterministe : invalidation chiffrée, cible 30j en
fourchette, cible cycle ancrée ATH, R:R, EV prospectif borné, bull/base/bear
sommant à 100, zone d'accumulation + DCA, sizing plafonné par la
concentration (le cash n'est JAMAIS une contrainte).
"""

from __future__ import annotations

import math

import pytest

from src.analytics.asset_plan import (
    compute_asset_plan,
    suggest_sizing,
    _prob_up_30d,
)


def _series(n: int = 150, base: float = 60000.0) -> list[float]:
    """Série réaliste : tendance douce + vagues → pivots hauts/bas nets."""
    out = []
    for i in range(n):
        wave = math.sin(i / 9.0) * 2500 + math.sin(i / 23.0) * 4000
        drift = i * 12.0
        out.append(base + wave + drift)
    return out


@pytest.fixture(scope="module")
def plan():
    closes = _series()
    return compute_asset_plan("BTC", closes, price=closes[-1], ath=126080.0)


# ── TH1 — invalidation chiffrée ──────────────────────────────────────────
def test_invalidation_below_price_with_basis(plan):
    assert plan["available"] is True
    inv = plan["invalidation"]
    assert inv["level"] < plan["price"]
    assert inv["basis"]
    assert inv["dist_pct"] < 0
    assert inv["level_label"]


def test_short_series_unavailable():
    out = compute_asset_plan("XXX", [1.0] * 10)
    assert out["available"] is False and out["reason"]


# ── ES1/ES2 — cible 30j en fourchette ────────────────────────────────────
def test_target_30d_range_coherent(plan):
    t = plan["target_30d"]
    assert t["level"] > plan["price"]
    assert t["low"] <= t["level"] <= t["high"]
    assert t["upside_pct"] > 0
    assert t["basis"]


def test_target_cycle_anchored_on_ath(plan):
    tc = plan["target_cycle"]
    assert tc is not None
    assert tc["high"] == 126080.0                      # jamais au-delà de l'ATH
    # low = prix + 61,8% du chemin vers l'ATH.
    expected_low = plan["price"] + (126080.0 - plan["price"]) * 0.618
    assert tc["low"] == pytest.approx(expected_low, rel=1e-6)
    assert tc["kind"] in ("cycle", "6-12m")


def test_target_cycle_suspect_or_near_ath_omitted():
    closes = _series()
    p1 = compute_asset_plan("JASMY", closes, price=closes[-1],
                            ath=closes[-1] * 400, ath_suspect=True)
    assert p1["target_cycle"] is None
    p2 = compute_asset_plan("BTC", closes, price=closes[-1],
                            ath=closes[-1] * 1.05)     # < +10% → pas de cycle
    assert p2["target_cycle"] is None


# ── RE2 — R:R ────────────────────────────────────────────────────────────
def test_rr_matches_reward_over_risk(plan):
    px = plan["price"]
    risk = px - plan["invalidation"]["level"]
    reward = plan["target_30d"]["level"] - px
    assert plan["rr_30d"] == pytest.approx(reward / risk, abs=0.05)
    assert plan["rr_30d"] > 0


# ── ES3 — EV prospectif borné et honnête ─────────────────────────────────
def test_prob_up_bounds():
    # RSI extrêmement survendu + funding très négatif + tilt marché haussier
    # → borne haute 0.70, jamais plus.
    p_hi = _prob_up_30d({"rsi": 5, "trend_7d_pct": 20, "ma200_rel_pct": 30},
                        funding_annualized_pct=-50, market_net_tilt=1.0)
    assert p_hi == 0.70
    p_lo = _prob_up_30d({"rsi": 95, "trend_7d_pct": -20, "ma200_rel_pct": -30},
                        funding_annualized_pct=50, market_net_tilt=-1.0)
    assert p_lo == 0.30
    assert _prob_up_30d({}) == 0.5                     # aucune donnée → neutre


def test_ev_consistent_with_prob(plan):
    p = plan["prob_up_30d"]
    up = (plan["target_30d"]["level"] - plan["price"]) / plan["price"] * 100
    down = (plan["invalidation"]["level"] - plan["price"]) / plan["price"] * 100
    assert plan["ev_30d_pct"] == pytest.approx(p * up + (1 - p) * down, abs=0.1)
    assert "indicative" in plan["ev_note"]


# ── TH2 — bull/base/bear par actif ───────────────────────────────────────
def test_scenarios_sum_100_and_levels(plan):
    sc = plan["scenarios"]
    total = sum(sc[k]["probability_pct"] for k in ("bull", "base", "bear"))
    assert total == 100
    assert sc["bull"]["level"] == plan["target_30d"]["high"]
    assert sc["bear"]["level"] < plan["invalidation"]["level"]
    assert sc["base"]["low"] < sc["base"]["high"]
    for k in ("bull", "bear"):
        assert "cassure" in sc[k]["condition"]


# ── RE3 — zone d'accumulation + DCA ──────────────────────────────────────
def test_accumulation_zone_below_price(plan):
    z = plan["accumulation_zone"]
    assert z["low"] < z["high"] <= plan["price"]


def test_dca_three_tranches_weights_100(plan):
    dca = plan["dca"]
    assert len(dca) == 3
    assert sum(t["weight_pct"] for t in dca) == 100
    prices = [t["price"] for t in dca]
    assert prices[0] >= prices[1] >= prices[2]         # tranches décroissantes
    assert all(t["basis"] for t in dca)


def test_plan_line_complete(plan):
    line = plan["plan_line"]
    for needle in ("Invalidation", "Cible 30j", "R:R", "EV 30j", "Zone d'accu"):
        assert needle in line, f"plan_line incomplet : {needle}"


# ── RE1 — sizing plafonné, cash jamais une contrainte ────────────────────
def test_sizing_core_normal_weight():
    s = suggest_sizing(action_type="bullish", weight_pct=10.0,
                       ptf_value_usd=2656.0, is_core=True)
    assert s["add_pct_ptf"] == 2.0
    assert s["add_usd"] == pytest.approx(53, abs=1)
    assert s["weight_after_pct"] == 12.0
    assert "12" in s["note"]


def test_sizing_concentration_cap():
    s = suggest_sizing(action_type="renforcer", weight_pct=22.0,
                       ptf_value_usd=2656.0, is_core=True)
    assert s["add_pct_ptf"] == 0.0
    assert "concentration" in s["note"]
    # Satellite : plafond plus bas (12%).
    s2 = suggest_sizing(action_type="bullish", weight_pct=13.0, is_core=False)
    assert s2["add_pct_ptf"] == 0.0


def test_sizing_trim_and_unknown():
    s = suggest_sizing(action_type="alléger", position_value_usd=100.0,
                       is_core=False)
    assert s["trim_pct_position"] == 50.0
    assert s["trim_usd"] == 50.0
    assert suggest_sizing(action_type="hold") is None


def test_sizing_never_mentions_cash():
    """RE1 : le sizing ne conditionne JAMAIS à du cash / une vente."""
    for kw in (dict(action_type="bullish", weight_pct=10.0, is_core=True),
               dict(action_type="bullish", weight_pct=22.0, is_core=True)):
        s = suggest_sizing(**kw)
        note = (s or {}).get("note", "").lower()
        assert "cash" not in note and "céder" not in note and "vendre" not in note
