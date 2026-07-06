# -*- coding: utf-8 -*-
"""OB1 — radar de sortie / allègement (moteur déterministe)."""

from __future__ import annotations

from src.analytics import exit_radar


def _pos(sym, *, pnl=None, weight=None, tier="satellite", c7=None, c24=None):
    return {"symbol": sym, "pnl_pct": pnl, "weight_pct": weight,
            "tier": tier, "change_7d": c7, "change_24h": c24}


def _by_sym(res):
    return {s["symbol"]: s for s in res["signals"]}


# ── paliers de prise de profit (satellites) ────────────────────────────────
def test_satellite_x3_ladder():
    r = exit_radar.compute_exit_signals([_pos("PEPE", pnl=250.0)])
    s = r["signals"][0]
    assert s["urgency"] == 3 and "×3" in s["reason"]
    assert "grosse tranche" in s["action"]


def test_satellite_x2_ladder():
    s = exit_radar.compute_exit_signals([_pos("WIF", pnl=120.0)])["signals"][0]
    assert s["urgency"] == 3 and "×2" in s["reason"]


def test_satellite_80_ladder():
    s = exit_radar.compute_exit_signals([_pos("INJ", pnl=85.0)])["signals"][0]
    assert s["urgency"] == 2 and "+80" in s["reason"]


def test_satellite_below_80_no_ladder():
    r = exit_radar.compute_exit_signals([_pos("ARB", pnl=40.0)])
    assert r["available"] is False


# ── protection du CŒUR ─────────────────────────────────────────────────────
def test_core_moderate_gain_not_flagged():
    """+150 % sur un cœur = on garde, aucun signal (contrairement au satellite)."""
    r = exit_radar.compute_exit_signals([_pos("BTC", pnl=150.0, tier="core")])
    assert r["available"] is False


def test_core_extreme_gain_small_tranche():
    s = exit_radar.compute_exit_signals(
        [_pos("ETH", pnl=350.0, tier="core")])["signals"][0]
    assert s["urgency"] == 2 and "PETITE tranche" in s["action"]


def test_core_detected_even_without_tier_field():
    """TAO/LINK sont protégés même si le champ tier est absent/incorrect."""
    r = exit_radar.compute_exit_signals([_pos("TAO", pnl=150.0, tier=None)])
    assert r["available"] is False  # traité comme cœur (protégé), pas de palier ×...


# ── pump momentum + concentration ──────────────────────────────────────────
def test_satellite_pump_offload_window():
    s = exit_radar.compute_exit_signals(
        [_pos("NOT", pnl=15.0, c7=50.0, c24=25.0)])["signals"][0]
    assert s["urgency"] == 2 and "pump" in s["reason"] and "24h" in s["reason"]
    assert "offloader" in s["action"]


def test_pump_ignored_if_underwater():
    r = exit_radar.compute_exit_signals([_pos("XYZ", pnl=-10.0, c7=50.0)])
    assert r["available"] is False


def test_satellite_overconcentration():
    s = exit_radar.compute_exit_signals(
        [_pos("JASMY", pnl=5.0, weight=15.0)])["signals"][0]
    assert s["urgency"] == 1 and "surpondéré" in s["reason"]


def test_ladder_takes_priority_over_concentration():
    """Une position à la fois surpondérée ET ×2 → un seul signal, le plus urgent."""
    r = exit_radar.compute_exit_signals([_pos("A", pnl=120.0, weight=20.0)])
    assert len(r["signals"]) == 1 and r["signals"][0]["urgency"] == 3


# ── ordonnancement + robustesse ────────────────────────────────────────────
def test_signals_sorted_by_urgency():
    r = exit_radar.compute_exit_signals([
        _pos("LOW", weight=13.0),           # concentration (urg 1)
        _pos("HIGH", pnl=250.0),            # ×3 (urg 3)
        _pos("MID", pnl=85.0),              # +80 (urg 2)
    ])
    assert [s["symbol"] for s in r["signals"]] == ["HIGH", "MID", "LOW"]
    assert r["count"] == 3 and "à considérer" in r["summary"]


def test_empty_and_malformed_positions():
    assert exit_radar.compute_exit_signals([])["available"] is False
    assert exit_radar.compute_exit_signals(
        [{"symbol": "", "pnl_pct": 300}])["available"] is False
    # champs non numériques → ignorés sans crash
    assert exit_radar.compute_exit_signals(
        [_pos("BAD", pnl="n/a", weight="x")])["available"] is False
