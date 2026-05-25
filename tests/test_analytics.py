"""Tests des modules analytiques (logique pure, sans réseau)."""

from __future__ import annotations

from src.analytics.composite_score import composite_score
from src.analytics.narratives import sector_rotation
from src.analytics.project_health import project_health
from src.analytics.technical import evaluate_technical
from src.reporting.content_filter import should_mention
from src.reporting.volatility_assessor import determine_report_style


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


def test_evaluate_technical_unavailable() -> None:
    """Données indisponibles -> score None, pas de crash."""
    res = evaluate_technical({"available": False})
    assert res["score"] is None


def test_composite_score_bounds() -> None:
    """Le score composite est borné [0,100]."""
    cs = composite_score(
        technical={"score": 80},
        dev_activity={"available": True, "commits_30d": 100, "last_commit_days_ago": 1},
        news_score=1.0,
        reddit_sentiment=1.0,
        macro_fit=100,
        onchain_available=True,
    )
    assert 0 <= cs["total"] <= 100
    assert set(cs["components"]) == {
        "technical",
        "on_chain",
        "fundamental",
        "sentiment",
        "macro_alignment",
    }


def test_project_health_exit() -> None:
    """Aucun commit + volume nul -> verdict exit."""
    ph = project_health(
        symbol="DEAD",
        dev_activity={"available": True, "commits_30d": 0, "last_commit_days_ago": 300},
        market={"volume_24h": 50, "change_from_ath_pct": -98},
    )
    assert ph["verdict"] == "exit"


def test_project_health_ok() -> None:
    """Projet actif et liquide -> verdict ok."""
    ph = project_health(
        symbol="HEALTHY",
        dev_activity={"available": True, "commits_30d": 60, "last_commit_days_ago": 1},
        market={"volume_24h": 5_000_000, "change_from_ath_pct": -40},
    )
    assert ph["verdict"] == "ok"


def test_sector_rotation_excludes_stable() -> None:
    """USDC (stablecoin) est exclu de la rotation sectorielle."""
    rot = sector_rotation(
        {"USDC": {"change_24h": 0.0}, "TAO": {"change_24h": 5.0}, "BTC": {"change_24h": 1.0}}
    )
    assert "Stablecoin" not in rot["sectors"]


def test_should_mention_thresholds() -> None:
    """Les seuils par tier sont respectés."""
    assert should_mention("X", 1, {"change_24h": 6}) is True
    assert should_mention("X", 1, {"change_24h": 4}) is False
    assert should_mention("X", 3, {"change_24h": 9}) is False
    assert should_mention("X", 4, {"change_24h": 31}) is True


def test_report_style_extremes() -> None:
    """Style calme par défaut, agité quand tout converge."""
    assert determine_report_style({})["style"] == "calm"
    busy = determine_report_style(
        {
            "macro_high_impact_today": 2,
            "positions_moving": 5,
            "narrative_shift": True,
            "major_news_count": 10,
        }
    )
    assert busy["style"] == "active"
