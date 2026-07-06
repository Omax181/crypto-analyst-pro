# -*- coding: utf-8 -*-
"""OB26 — skew des options (risk reversal 25Δ) Deribit.

Teste le calcul déterministe (delta Black-Scholes + sélection 25Δ) sur données
SYNTHÉTIQUES, sans réseau : régimes baissier / haussier / neutre + dégradations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data_sources import deribit


def _now() -> datetime:
    return datetime(2026, 7, 4, tzinfo=timezone.utc)


def _quotes(iv_fn, *, spot=100.0, days=30, lo=70, hi=131, step=5):
    """Construit une chaîne d'options ``(expiry, strike, opt, iv, underlying)``."""
    exp = _now() + timedelta(days=days)
    opts = []
    for k in range(lo, hi, step):
        for opt in ("C", "P"):
            opts.append((exp, float(k), opt, iv_fn(k), spot))
    return opts


# ── delta Black-Scholes ────────────────────────────────────────────────────
def test_bs_delta_atm_is_half():
    d_call = deribit._bs_delta("C", 100.0, 100.0, 0.5, 30 / 365)
    d_put = deribit._bs_delta("P", 100.0, 100.0, 0.5, 30 / 365)
    assert d_call is not None and 0.45 < d_call < 0.60
    assert d_put is not None and -0.60 < d_put < -0.40
    # put + call ≈ 1 en valeur (paires delta).
    assert abs((d_call - d_put) - 1.0) < 1e-9


def test_bs_delta_invalid_inputs():
    assert deribit._bs_delta("C", 0.0, 100.0, 0.5, 0.1) is None
    assert deribit._bs_delta("C", 100.0, 100.0, 0.0, 0.1) is None
    assert deribit._bs_delta("C", 100.0, 100.0, 0.5, 0.0) is None


# ── skew : trois régimes ───────────────────────────────────────────────────
def test_skew_downside_puts_richer():
    """IV plus élevée sur les strikes bas (puts) → RR négatif = prudence."""
    out = deribit._compute_skew(_quotes(lambda k: 60 - 0.3 * (k - 100)), _now())
    assert out, "skew doit être calculable"
    assert out["iv_skew_25d"] < 0
    assert out["put_iv_25d"] > out["call_iv_25d"]
    assert "baissier" in out["skew_reading"]
    assert out["skew_tenor_days"] == 30
    assert "atm_iv" in out


def test_skew_upside_calls_richer():
    out = deribit._compute_skew(_quotes(lambda k: 60 + 0.3 * (k - 100)), _now())
    assert out["iv_skew_25d"] > 0
    assert out["call_iv_25d"] > out["put_iv_25d"]
    assert "haussier" in out["skew_reading"]


def test_skew_neutral_flat_vol():
    out = deribit._compute_skew(_quotes(lambda _k: 60.0), _now())
    assert abs(out["iv_skew_25d"]) < deribit._SKEW_THRESHOLD
    assert "neutre" in out["skew_reading"]


# ── dégradations gracieuses ────────────────────────────────────────────────
def test_skew_insufficient_quotes():
    exp = _now() + timedelta(days=30)
    opts = [(exp, 100.0, "C", 60.0, 100.0), (exp, 95.0, "P", 62.0, 100.0)]
    assert deribit._compute_skew(opts, _now()) == {}


def test_skew_ignores_past_expiries():
    past = _now() - timedelta(days=5)
    opts = [(past, s, o, iv, u)
            for (_e, s, o, iv, u) in _quotes(lambda _k: 60.0)]
    assert deribit._compute_skew(opts, _now()) == {}


def test_skew_targets_nearest_to_30d():
    """Deux échéances : la plus proche de 30 j est retenue (7 j écartée)."""
    near = [(_now() + timedelta(days=7), float(k), o, 60.0, 100.0)
            for k in range(70, 131, 5) for o in ("C", "P")]
    good = _quotes(lambda _k: 60.0, days=32)
    out = deribit._compute_skew(near + good, _now())
    assert out["skew_tenor_days"] == 32


# ── surfaçage : le skew apparaît dans la ligne d'options (anti signal mort) ──
def test_options_line_surfaces_skew():
    from src.analytics import digests
    line = digests.options_line({"available": True, "assets": {
        "BTC": {"put_call_ratio": 1.1, "dvol": 40.0, "iv_skew_25d": -6.6}}})
    assert "skew 25" in line and "-6.6" in line
    assert "protection baissière" in line
    # Absence de skew → la ligne reste valide (rétro-compat).
    line2 = digests.options_line({"available": True, "assets": {
        "BTC": {"put_call_ratio": 0.6}}})
    assert "put/call" in line2 and "skew" not in line2
