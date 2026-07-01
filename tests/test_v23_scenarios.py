"""v23.x — ÉCHAFAUDAGE DÉTERMINISTE des scénarios hebdomadaires (deepthink Omar).

Verrouille : (1) `compute_scenario_scaffold` (tilts par dimension, dispersion,
prior de probabilités sommant à 100, réponses directionnelles correctes,
dégradation gracieuse) ; (2) son câblage dans run_weekly (data.scenario_scaffold).

Hermétique : pur calcul, aucun réseau.
"""

from __future__ import annotations

from src.analytics.scenarios import compute_scenario_scaffold


def _calme():
    return compute_scenario_scaffold(
        btc_price=64000, implied_move_7d_pct=3.5,
        polymarket={"fed_bars": {"dominant": "maintien", "dominant_pct": 99}},
        vix=13.0, dxy_trend="down", fear_greed=55, btc_funding_pct=5.0,
        btc_support=60000, btc_resistance=66000, btc_trend_pct=8.0, btc_rsi=58,
        btc_change_7d=4.0, calendar_events=[])


def _stress():
    return compute_scenario_scaffold(
        btc_price=55000, implied_move_7d_pct=11.0,
        polymarket={"fed_bars": {"dominant": "baisse", "dominant_pct": 55}},
        vix=28.0, dxy_trend="up", fear_greed=22, btc_funding_pct=40.0,
        btc_support=52000, btc_resistance=60000, btc_trend_pct=-9.0, btc_rsi=33,
        btc_change_7d=-12.0,
        calendar_events=[{"label": "FOMC", "days_ahead": 1},
                         {"label": "CPI (inflation US)", "days_ahead": 4}])


def test_prior_sums_to_100():
    for s in (_calme(), _stress()):
        p = s["prior"]
        assert p["bearish"] + p["neutral"] + p["bullish"] == 100


def test_directional_response():
    """Régime risk-on/haussier → bullish > bearish ; risque-off/baissier → l'inverse."""
    calme, stress = _calme(), _stress()
    assert calme["net_tilt"] > 0 and calme["prior"]["bullish"] > calme["prior"]["bearish"]
    assert stress["net_tilt"] < 0 and stress["prior"]["bearish"] > stress["prior"]["bullish"]


def test_dispersion_compresses_neutral():
    """Plus de catalyseurs + vol haute → dispersion ↑ → neutre ↓."""
    calme, stress = _calme(), _stress()
    assert stress["dispersion"] > calme["dispersion"]
    assert stress["prior"]["neutral"] < calme["prior"]["neutral"]


def test_dvol_primary_event_topup_small():
    """La vol implicite (DVOL) price déjà le calendrier : un FOMC quasi-certain
    (Polymarket ≥80%) n'ajoute presque pas de dispersion."""
    base = compute_scenario_scaffold(
        btc_price=60000, implied_move_7d_pct=6.0,
        polymarket={"fed_bars": {"dominant": "maintien", "dominant_pct": 90}},
        vix=18.0, fear_greed=50, btc_support=58000, btc_resistance=63000,
        btc_trend_pct=1.0, btc_rsi=50, btc_change_7d=0.0,
        calendar_events=[{"label": "FOMC", "days_ahead": 2}])  # Fed pricé
    surprise = compute_scenario_scaffold(
        btc_price=60000, implied_move_7d_pct=6.0,
        polymarket={"fed_bars": {"dominant": "maintien", "dominant_pct": 90}},
        vix=18.0, fear_greed=50, btc_support=58000, btc_resistance=63000,
        btc_trend_pct=1.0, btc_rsi=50, btc_change_7d=0.0,
        calendar_events=[{"label": "NFP (emploi US)", "days_ahead": 2}])  # surprise
    # Un FOMC pricé n'élargit pas les queues ; un NFP (non pricé) si.
    assert surprise["dispersion"] > base["dispersion"]


def test_factor_tilts_and_levels_present():
    s = _stress()
    dims = {t["dimension"] for t in s["factor_tilts"]}
    assert {"Macro", "Technique", "Sentiment", "Dérivés", "Momentum"} <= dims
    assert all("note" in t for t in s["factor_tilts"])  # notes chiffrées citées
    assert s["key_levels"]["support"] == 52000 and s["key_levels"]["resistance"] == 60000
    assert s["polymarket"]["fed_dominant"] == "baisse"
    assert s["event_risk"]["count"] == 2
    # drivers par scénario non vides quand il y a de la matière
    assert s["drivers"]["bearish"] and s["drivers"]["neutral"]


def test_sentiment_contrarian():
    """F&G en peur extrême = tilt sentiment POSITIF (contrarian)."""
    s = _stress()  # F&G 22
    sent = next(t for t in s["factor_tilts"] if t["dimension"] == "Sentiment")
    assert sent["tilt"] > 0


def test_graceful_degradation():
    assert compute_scenario_scaffold().get("available") is False
    # Une seule dimension et pas de DVOL → indisponible (pas de prior fabriqué).
    assert compute_scenario_scaffold(fear_greed=50).get("available") is False
    # DVOL seul suffit à produire un prior (dispersion calculable).
    assert compute_scenario_scaffold(implied_move_7d_pct=5.0).get("available") is True


def test_wired_into_run_weekly():
    """Le scaffold est calculé et injecté dans data.scenario_scaffold du hebdo."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_weekly)
    assert "compute_scenario_scaffold(" in src
    assert '"scenario_scaffold": _scenario_scaffold' in src
