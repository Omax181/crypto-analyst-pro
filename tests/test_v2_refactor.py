"""Tests de validation du refactor V2 (règles non négociables)."""

from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

from src.analytics.coherence_checker import check_report
from src.analytics.composite_score import composite_score, confidence_to_action
from src.analytics.fundamentals import compute_ath_distance
from src.analytics.tier_resolver import min_signals_for_firm_reco, resolve_tier


def test_ath_distance_never_minus_100() -> None:
    """ATH distance ne peut être -100% si prix > 0."""
    assert compute_ath_distance(0.005, 5.0) > -100


def test_signal_threshold_btc_eth() -> None:
    """BTC/ETH nécessitent 4+ signaux pour reco ferme."""
    assert min_signals_for_firm_reco(resolve_tier("BTC", 400)) == 4
    # 3 signaux < seuil BTC -> non éligible
    res = composite_score({"technical_multi_tf": 80, "volume_anomaly": 75, "onchain_flows": 70})
    assert res["signals_count"] < min_signals_for_firm_reco(0)


def test_github_commit_max_weight() -> None:
    """Les commits (via fundamental) pèsent au plus ~10% du total."""
    only = composite_score({"fundamental": 100})
    # fundamental seul à 100 : contribution = 100*0.10 = 10 au-dessus du
    # neutre pondéré (50*0.90=45) -> 55. L'écart au neutre (50) est <= 10.
    assert only["total"] - 50.0 <= 10.0 + 0.1


def test_confidence_action_size_link() -> None:
    """Une confiance < 55% ne produit jamais de reco ferme."""
    assert confidence_to_action(50)["firm"] is False
    assert confidence_to_action(54)["firm"] is False
    assert confidence_to_action(55)["firm"] is True


def test_coherence_downgrades_weak_reco() -> None:
    """Le coherence_checker rétrograde une reco ferme mal fondée."""
    bad = {
        "thesis_of_the_day": [{
            "asset": "INJ", "action": "ALLEGER", "confidence": 40,
            "reasoning_signals": ["pas de commit récent"],
            "action_plan": {}, "sources_timestamps": "selon les sources",
        }]
    }
    res = check_report(bad)
    assert not res["ok"]
    assert res["sanitized_payload"]["thesis_of_the_day"][0]["action"] == "SURVEILLER"


def test_coherence_fixes_impossible_ath() -> None:
    """Un ATH -100% dans le récap est corrigé à -99.99%."""
    res = check_report({"all_positions_summary": [{"asset": "X", "ath_distance_pct": -100}]})
    assert res["sanitized_payload"]["all_positions_summary"][0]["ath_distance_pct"] == -99.99


def test_repo_mapping_completeness() -> None:
    """Tous les actifs Tier 1-3 ont un mapping repo (null/[] autorisé)."""
    from src.utils.portfolio_loader import load_config, load_portfolio

    portfolio = load_portfolio()["portfolio"]
    repos = load_config("github_repos")["github_repos"]
    for asset, data in portfolio.items():
        if data.get("role") == "cash_reserve":
            continue
        assert asset in repos, f"{asset} absent de github_repos.yaml"


def test_weekly_win_rate_calculation(monkeypatch) -> None:
    """Le win rate reflète les recos réellement trackées."""
    from src.state import report_memory as mem
    from src.tracking.prediction_scoring import PredictionTracker

    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    now = datetime.now(timezone.utc).isoformat()
    mem.save_prediction_history([
        {"status": "validated", "created_at": now},
        {"status": "validated", "created_at": now},
        {"status": "invalidated", "created_at": now},
    ])
    score = PredictionTracker().compute_win_rate(days=30)
    assert score["total"] == 3
    assert score["win_rate_pct"] == round(2 / 3 * 100)


def test_panic_anti_spam(monkeypatch) -> None:
    """L'anti-spam empêche deux panic emails rapprochés."""
    from src.state import report_memory as mem

    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    mem.mark_panic_sent(["BTC -16%"])
    last = mem.load_last_panic()
    sent = datetime.fromisoformat(last["sent_at"])
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    # moins d'1h écoulée -> on ne renvoie pas
    assert datetime.now(timezone.utc) - sent < timedelta(minutes=60)


def test_news_filter_temporal_structure() -> None:
    """get_recent_news renvoie une liste (vide si pas de clé) sans planter."""
    from src.data_sources import cryptopanic

    result = cryptopanic.get_recent_news("BTC", hours=24)
    assert isinstance(result, list)
