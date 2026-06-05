"""Tests des correctifs et améliorations V11.

Couvre : bêtas par actif vs macro (A5/C8), stats historiques OHLC réelles (A11),
exposition sectorielle (A6), détection de contradictions (C1), fraîcheur du
rapport du soir pour le P&L nuit (A2), calendrier à venir FRED (A10/C6),
neutralisation des stats d'indispo (B10), libellé prochain rapport (B9), et
lignes de digest (beta/calendrier).

Compatibles pytest (CI) ET runner offline. Aucune source réseau réelle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# --------------------------------------------------------------------------- #
# A5 / C8 — bêtas par actif vs facteurs macro
# --------------------------------------------------------------------------- #
def test_per_asset_macro_beta_basic() -> None:
    from src.analytics.correlation import compute_per_asset_macro_beta

    # Série actif = 2 × série DXY (rendements) → bêta attendu ≈ +2, corr ≈ +1.
    dates = [f"2026-05-{d:02d}" for d in range(1, 21)]
    dxy = {d: 100.0 + i for i, d in enumerate(dates)}            # +1 / jour
    asset = {d: 50.0 + 2 * i for i, d in enumerate(dates)}        # +2 / jour
    out = compute_per_asset_macro_beta(
        {"TAO": asset}, {"dxy": dxy}, window=30, factors=("dxy",)
    )
    assert out["available"] is True
    b = out["by_asset"]["TAO"]["dxy"]
    assert b["corr"] is not None and b["corr"] > 0.9
    assert b["beta"] is not None and b["beta"] > 0  # pente positive

def test_per_asset_macro_beta_degrades_when_empty() -> None:
    from src.analytics.correlation import compute_per_asset_macro_beta

    assert compute_per_asset_macro_beta({}, {}, factors=("dxy",))["available"] is False
    # Trop peu de points communs → pas de bêta.
    out = compute_per_asset_macro_beta(
        {"X": {"2026-05-01": 1.0}}, {"dxy": {"2026-05-01": 1.0}}, factors=("dxy",)
    )
    assert out["available"] is False


# --------------------------------------------------------------------------- #
# A11 — statistiques chartistes RÉELLES (calculées sur OHLC)
# --------------------------------------------------------------------------- #
def test_setup_stats_detects_oversold_occurrences() -> None:
    from src.analytics.historical_patterns import compute_setup_stats

    # Cycles avec un repli journalier net de −10% (100→90) suivi d'un rebond.
    # La série finit haut (101) pour forcer la détection via le seuil de repli.
    closes = [100, 90, 96, 101, 106, 101] * 10  # 60 points
    stats = compute_setup_stats(closes, change_24h=-10.0, forward_days=3)
    assert stats["available"] is True
    assert stats["occurrences"] >= 3
    assert "win_rate_pct" in stats and stats["win_rate_pct"] is not None
    assert isinstance(stats["summary"], str) and stats["summary"]

def test_setup_stats_short_history_unavailable() -> None:
    from src.analytics.historical_patterns import compute_setup_stats

    assert compute_setup_stats([1, 2, 3], change_24h=-5.0)["available"] is False
    assert compute_setup_stats([], change_24h=None)["available"] is False


# --------------------------------------------------------------------------- #
# A6 — exposition sectorielle du portefeuille
# --------------------------------------------------------------------------- #
def test_sector_exposure_weights() -> None:
    from src.main import _compute_sector_exposure

    enriched = {
        "BTC": {"value_usd": 600, "change_24h": -1.0},
        "ADA": {"value_usd": 300, "change_24h": -5.0},
        "TAO": {"value_usd": 100, "change_24h": -13.0},
    }
    rotation = {"sectors": {
        "L1": {"members": ["BTC", "ADA"], "avg_change_24h": -3.0},
        "AI": {"members": ["TAO"], "avg_change_24h": -13.0},
    }}
    out = _compute_sector_exposure(enriched, rotation)
    assert out["available"] is True
    by = {r["sector"]: r for r in out["sectors"]}
    assert by["L1"]["ptf_pct"] == 90.0          # (600+300)/1000
    assert by["AI"]["ptf_pct"] == 10.0
    assert by["AI"]["market_change_24h"] == -13.0

def test_sector_exposure_empty() -> None:
    from src.main import _compute_sector_exposure

    assert _compute_sector_exposure({}, {"sectors": {}})["available"] is False


# --------------------------------------------------------------------------- #
# C1 — détection de contradictions de données (DXY)
# --------------------------------------------------------------------------- #
def test_detect_contradictions_dxy_gap() -> None:
    from src.main import _detect_data_contradictions

    out = _detect_data_contradictions({"dxy": 99.0, "dxy_broad": 119.0})
    assert out["has_any"] is True and out["notes"]

def test_detect_contradictions_none_when_close_or_single() -> None:
    from src.main import _detect_data_contradictions

    # Écart < seuil → rien.
    assert _detect_data_contradictions({"dxy": 99.0, "dxy_broad": 100.0})["has_any"] is False
    # Une seule valeur → rien.
    assert _detect_data_contradictions({"dxy": 99.0})["has_any"] is False

def test_detect_contradictions_broad_fallback_flag() -> None:
    from src.main import _detect_data_contradictions

    out = _detect_data_contradictions({"dxy": 118.0, "dxy_is_broad_fallback": True})
    assert out["has_any"] is True


# --------------------------------------------------------------------------- #
# A2 — fraîcheur du rapport du soir (baseline P&L nuit)
# --------------------------------------------------------------------------- #
def test_evening_freshness_recent_vs_stale() -> None:
    from src.main import _evening_report_is_fresh

    now = datetime.now(timezone.utc)
    fresh = {"generated_at": (now - timedelta(hours=10)).isoformat()}
    stale = {"generated_at": (now - timedelta(hours=40)).isoformat()}
    assert _evening_report_is_fresh(fresh, max_age_hours=18) is True
    assert _evening_report_is_fresh(stale, max_age_hours=18) is False
    assert _evening_report_is_fresh({}, max_age_hours=18) is False
    assert _evening_report_is_fresh({"generated_at": "pas une date"}) is False


# --------------------------------------------------------------------------- #
# A10 / C6 — calendrier à venir (FRED /release/dates), mocké
# --------------------------------------------------------------------------- #
def test_upcoming_releases_parses_future_dates(monkeypatch) -> None:
    from src.data_sources import fred

    monkeypatch.setenv("FRED_API_KEY", "x")
    today = datetime.now(timezone.utc).date()
    future = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    past = (today - timedelta(days=5)).strftime("%Y-%m-%d")

    def fake_get_json(url, params=None, **kw):
        return {"release_dates": [
            {"date": past}, {"date": future},
        ]}

    monkeypatch.setattr(fred, "get_json", fake_get_json)
    # Bypass cache (exécute la fonction interne directement via clé unique).
    fred.CACHE._store.clear() if hasattr(fred.CACHE, "_store") else None
    out = fred.get_upcoming_releases(horizon_days=10)
    assert out["available"] is True
    # La première date future (demain) est retenue, jamais la passée.
    assert any(e["days_ahead"] == 1 for e in out["events"])
    assert all(e["days_ahead"] >= 0 for e in out["events"])

def test_upcoming_releases_no_key(monkeypatch) -> None:
    from src.data_sources import fred

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    out = fred.get_upcoming_releases()
    assert out["available"] is False and out["events"] == []


# --------------------------------------------------------------------------- #
# B9 — libellé du prochain rapport, sensible à l'heure
# --------------------------------------------------------------------------- #
def test_next_report_label_values() -> None:
    from src import main

    # Le matin pointe vers un créneau du soir ; le soir vers un créneau du matin.
    assert "20h00" in main._next_report_label("morning")
    assert "08h30" in main._next_report_label("evening")
    assert "08h30" in main._next_report_label("weekly")


# --------------------------------------------------------------------------- #
# B10 — neutralisation des stats d'indispo tant que < 7 jours observés
# --------------------------------------------------------------------------- #
def test_blind_spots_weekly_needs_full_week(monkeypatch) -> None:
    from src.state import report_memory as mem

    # 3 jours de logs seulement → pas de conclusion (available=False).
    now = datetime.now(timezone.utc)
    logs = [
        {"date": (now - timedelta(days=d)).isoformat(), "down": ["CoinGecko"]}
        for d in range(3)
    ]
    monkeypatch.setattr(mem, "_read", lambda *a, **k: logs)
    out = mem.compute_blind_spots_weekly()
    assert out["available"] is False


# --------------------------------------------------------------------------- #
# Digests V11 — ligne bêtas + calendrier avec « à venir »
# --------------------------------------------------------------------------- #
def test_per_asset_beta_line() -> None:
    from src.analytics import digests

    data = {"available": True, "by_asset": {
        "TAO": {"dxy": {"beta": -0.42, "corr": -0.55}, "sp500": {"beta": 0.68, "corr": 0.7}},
    }}
    line = digests.per_asset_beta_line(data)
    assert "TAO" in line and "DXY" in line and "0.42" in line
    assert digests.per_asset_beta_line({"available": False}) == ""

def test_calendar_line_includes_upcoming() -> None:
    from src.analytics import digests

    upcoming = {"available": True, "events": [
        {"key": "10", "label": "Inflation CPI", "date": "2026-06-11", "days_ahead": 1},
    ]}
    line = digests.calendar_line({"available": False, "prints": []},
                                 {"available": False}, upcoming)
    assert "À venir" in line and "Inflation CPI" in line and "demain" in line
