# -*- coding: utf-8 -*-
"""OB24 — calibrateur de confiance « humble ». Tests de SÉCURITÉ : borné,
humble-only (jamais de boost), lissé (EMA), inerte sans historique, kill-switch.
"""

from __future__ import annotations

import pytest

from src.analytics import confidence_calibration as cc
from src.state import report_memory as mem

# Fixture défensive (cf. fuite d'hygiène test_v14/v15) : restaure le vrai I/O.
_REAL_READ = mem._read
_REAL_WRITE = mem._write


@pytest.fixture
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(mem, "_read", _REAL_READ)
    monkeypatch.setattr(mem, "_write", _REAL_WRITE)
    monkeypatch.delenv("LEARNING_ENABLED", raising=False)
    return tmp_path


class _FakeTracker:
    def __init__(self, cal):
        self._cal = cal

    def compute_calibration(self, period_days=90):
        return self._cal


def _cal(*buckets):
    """buckets: (range, realized_pct, n)."""
    return {"available": True, "buckets": [
        {"range": r, "realized_pct": p, "n": n} for (r, p, n) in buckets]}


# ── SÉCURITÉ : humble-only, borné ──────────────────────────────────────────
def test_overconfident_reduces_confidence(clean_state):
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 70, 12))))
    assert r["available"] and r["enabled"]
    assert 0.70 <= r["multiplier"] < 1.0     # réduit mais borné
    assert "sur-confiance" in r["reason"]


def test_underconfident_NEVER_boosts(clean_state):
    """SÉCURITÉ CRITIQUE : réalisé > annoncé → multiplicateur clampé à 1.00."""
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("70-79%", 100, 15))))
    assert r["multiplier"] == 1.0            # JAMAIS d'augmentation de confiance


def test_well_calibrated_near_one(clean_state):
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("70-79%", 75, 20))))
    assert r["multiplier"] == 1.0


def test_bounded_floor_never_below_070(clean_state):
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 10, 30))))
    assert r["multiplier"] >= 0.70


# ── inertie / kill-switch ──────────────────────────────────────────────────
def test_insufficient_sample_inert(clean_state):
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 60, 3))))
    assert r["available"] is False and r["multiplier"] == 1.0
    assert "insuffisant" in r["reason"]


def test_no_history_inert(clean_state):
    r = cc.compute_confidence_multiplier(_FakeTracker({"available": False}))
    assert r["available"] is False and r["multiplier"] == 1.0


def test_kill_switch_disables(clean_state, monkeypatch):
    monkeypatch.setenv("LEARNING_ENABLED", "0")
    r = cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 60, 30))))
    assert r["enabled"] is False and r["multiplier"] == 1.0


# ── adaptation lente (EMA) + persistance ───────────────────────────────────
def test_ema_slow_adaptation(clean_state):
    r1 = cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 40, 20))))
    assert r1["multiplier"] == 0.70          # 40/90 → clamp 0.70 (pas de prev)
    r2 = cc.compute_confidence_multiplier(_FakeTracker(_cal(("70-79%", 75, 20))))
    # EMA : 0.3*1.0 + 0.7*0.70 = 0.79 → remonte LENTEMENT, pas de saut direct à 1.0
    assert 0.70 < r2["multiplier"] < 1.0


def test_persistence_roundtrip(clean_state):
    cc.compute_confidence_multiplier(_FakeTracker(_cal(("80%+", 70, 12))))
    saved = mem._read(cc._STATE_KEY, {})
    assert isinstance(saved, dict) and saved.get("multiplier", 1.0) < 1.0


# ── application (humble-only aussi à l'usage) ───────────────────────────────
def test_apply_multiplier_humble_only():
    assert cc.apply_multiplier(80, 0.85) == 68
    assert cc.apply_multiplier(80, 1.0) == 80
    assert cc.apply_multiplier(80, 1.5) == 80        # clampé → jamais de boost
    assert cc.apply_multiplier(None, 0.8) is None
    assert cc.apply_multiplier("x", 0.8) == "x"
