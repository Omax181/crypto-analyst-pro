"""Tests des modules analytiques V2 (logique pure, sans réseau)."""

from __future__ import annotations

from src.analytics.composite_score import composite_score, confidence_to_action
from src.analytics.fundamentals import compute_ath_distance, fundamental_score_from_signals
from src.analytics.technical import evaluate_technical
from src.analytics.tier_resolver import min_signals_for_firm_reco, resolve_tier


def test_evaluate_technical_range() -> None:
    """Le score technique reste dans [0,100] et détecte une divergence."""
    tech = {
        "available": True,
        "signals": {
            "1w": {"recommendation": "STRONG_BUY"},
            "1d": {"recommendation": "BUY", "rsi": 25},
            "4h": {"recommendation": "NEUTRAL"},
            "1h": {"recommendation": "BUY"},
        },
    }
    res = evaluate_technical(tech)
    assert 0 <= res["score"] <= 100
    assert res["divergence"] == "oversold"


def test_composite_score_bounds_and_count() -> None:
    """Score borné [0,100] + comptage des signaux convergents."""
    res = composite_score({
        "technical_multi_tf": 80, "volume_anomaly": 75,
        "onchain_flows": 70, "derivatives": 72,
    })
    assert 0 <= res["total"] <= 100
    assert res["signals_count"] == 4
    assert res["bullish_count"] == 4


def test_composite_fundamental_only_is_weak() -> None:
    """Les commits seuls (fundamental) ne suffisent pas : 1 signal, faible poids."""
    res = composite_score({"fundamental": 100})
    assert res["signals_count"] == 1
    assert abs(res["total"] - 55.0) < 0.1  # 100*0.10 + 50*0.90


def test_ath_distance_clamp() -> None:
    """ATH distance jamais <= -100% pour un prix > 0."""
    assert compute_ath_distance(0.0001, 1000.0) == -99.99
    assert round(compute_ath_distance(50, 100), 1) == -50.0
    assert compute_ath_distance(0, 5) is None


def test_fundamental_score_bounded() -> None:
    """Le score fondamental reste borné même avec dev très actif."""
    s = fundamental_score_from_signals(
        dev_activity={"available": True, "commits_30d": 200, "last_commit_days_ago": 1},
        tvl_trend="up", revenue_trend="up",
    )
    assert 0 <= s <= 100


def test_tier_resolution() -> None:
    """Tiers résolus correctement (Tier 0 BTC/ETH + value_usd)."""
    assert resolve_tier("BTC", 404) == 0
    assert resolve_tier("ETH", 0) == 0
    assert resolve_tier("GRT", 80) == 1
    assert resolve_tier("X", 25) == 2
    assert resolve_tier("Y", 5) == 3
    assert resolve_tier("Z", 0.5) == 4


def test_min_signals_thresholds() -> None:
    """Seuils de signaux adaptatifs par tier."""
    assert min_signals_for_firm_reco(0) == 4
    assert min_signals_for_firm_reco(1) == 3
    assert min_signals_for_firm_reco(2) == 2
    assert min_signals_for_firm_reco(4) == 999


def test_confidence_to_action() -> None:
    """Mapping confiance -> taille d'action."""
    assert confidence_to_action(90)["firm"] is True
    assert confidence_to_action(60)["firm"] is True
    assert confidence_to_action(50)["firm"] is False
    assert "silence" in confidence_to_action(20)["label"]
