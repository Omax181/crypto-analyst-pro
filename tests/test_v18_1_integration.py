"""Tests d'intégration bout-en-bout v18.1.

Exercent le VRAI pipeline ``run_morning`` (fusion des faits Python, garde-fou de
cohérence, persistance des recos, rendu HTML, persistance des signaux croisés)
en ne mockant QUE les frontières externes : collecte de données, appel Gemini,
graphiques matplotlib et envoi SMTP. Objectif : attraper les ruptures de contrat
entre modules que les tests unitaires isolés ratent (la classe de bug n°1 du
projet, cf. audit v18).
"""

from __future__ import annotations

import pathlib
import tempfile

import src.main as main
from src.ai_brain.decision_engine import DecisionEngine
from src.reporting import charts
from src.state import report_memory as mem
from src.telegram_bot import context_loader


def _fake_data() -> dict:
    """Dict renvoyé par _collect_morning_data (forme réaliste, bornée)."""
    return {
        "header_meta": {"active_sources_count": 12, "total_sources_count": 28,
                        "win_rate_30d_pct": 60, "win_rate_count": "3/5"},
        "portfolio_snapshot": {"value_usd": 2630.0, "change_24h_pct": 1.2,
                               "change_7d_pct": -3.4},
        "macro_context": {"btc_price": 64800.0, "fear_greed": 22, "dxy": 104.2,
                          "vix": 18.0, "dxy_delta": -0.2},
        "risk_score": {
            "score": 4.2, "level": "modéré", "level_color": "#BA7517",
            "factors": ["drawdown 7j -3.4%"],
            "components": [
                {"label": "Drawdown 7j", "pts": 0.7, "max": 3.0},
                {"label": "Concentration", "pts": 1.2, "max": 2.5},
                {"label": "Volatilité 24h", "pts": 0.8, "max": 2.0},
                {"label": "Sentiment", "pts": 1.0, "max": 1.5},
            ],
            "dominant_axes": [{"label": "Concentration", "pts": 1.2, "max": 2.5,
                               "ratio_pct": 48}],
            "holdings_snapshot": ["BTC", "ETH", "TAO"],
        },
        "all_positions_summary": [
            {"asset": "ETH", "tier": 1, "price": 1640.0, "change_24h": 2.1,
             "comment": "support testé", "action_active": "RENFORCER"},
            {"asset": "TAO", "tier": 1, "price": 250.0, "change_24h": -4.0,
             "comment": "", "action_active": None},
        ],
        "eligible_theses": [
            {"asset": "ETH", "tier_label": "Tier 1 · large cap"},
        ],
        "cross_signals": {
            "signals": {"mvrv_context": {"available": True, "zone": "accumulation"}},
            "readings": ["MVRV BTC à 0.95 (< 1) : zone d'accumulation."],
        },
        "active_sources": ["coingecko", "fred"],
    }


def _fake_payload() -> dict:
    """Payload renvoyé par DecisionEngine.generate_morning (sortie Gemini)."""
    return {
        "header": {"date": "17/06", "time_casablanca": "08:30"},
        "executive_summary": {"bullets": [
            {"icon": "⚠", "text": "BTC en peur extrême (F&G 22), patience."},
            {"icon": "✓", "text": "ETH proche support, setup d'accumulation."},
        ]},
        "synthesis": "Marché en peur, fenêtre d'accumulation LT sur ETH.",
        "thesis_of_the_day": [{
            "asset": "ETH", "name": "Ethereum", "tier_label": "Tier 1 · large cap",
            "action": "RENFORCER", "action_type": "bullish",
            "thesis_type": "conviction", "confidence": 78,  # ≥ seuil d'affichage v23.x (75%)
            "reasoning_signals": ["MVRV < 1", "proche support W1", "F&G extrême"],
            "sources_timestamps": "CoinGecko 08h12 · TradingView 08h15",
            "action_plan": {
                "entry": 1640.0, "position_size_pct": 2,
                "take_profit": {"40pct": 2100.0}, "stop_loss": 1480.0,
                "stop_loss_basis": "invalidation : cassure support W1 1480",
            },
        }],
    }


def test_run_morning_end_to_end(monkeypatch) -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    monkeypatch.setattr(main, "_collect_morning_data", lambda pd: _fake_data())
    monkeypatch.setattr(DecisionEngine, "generate_morning",
                        lambda self, **kw: _fake_payload())
    monkeypatch.setattr(charts, "charts_for_theses", lambda theses, limit=4: {})
    sent = {}
    monkeypatch.setattr(main, "send_email",
                        lambda subject, html, inline_images=None: sent.update(subject=subject, html=html) or True)

    rc = main.run_morning()
    assert rc == 0, "run_morning doit réussir"

    # 1) Mail rendu et envoyé, contenu réel présent.
    assert "html" in sent and len(sent["html"]) > 2000
    assert "Veille crypto" in sent["html"]

    # 2) Rapport persisté avec la thèse, les firm_postures et les signaux croisés.
    saved = mem.load_morning_report()
    assert saved.get("thesis_of_the_day"), "la thèse doit être persistée"
    assert saved["thesis_of_the_day"][0]["asset"] == "ETH"
    assert "firm_postures" in saved and "ETH" in saved["firm_postures"]
    assert saved.get("cross_signals", {}).get("readings"), \
        "v18.1 : les signaux croisés doivent être persistés pour le bot"

    # 3) La reco ferme ETH RENFORCER est bien trackée (entrée = prix réel Python).
    recos = mem.load_active_recommendations()
    eth = next((r for r in recos if r["asset"] == "ETH"), None)
    assert eth is not None and eth["action"] == "RENFORCER"
    assert eth["entry_price"] == 1640.0  # prix de all_positions_summary, pas Gemini

    # 4) Le bot récupère bien ce rapport (intégration context_loader).
    summary = context_loader._summarize_report(saved, "morning")
    assert "cross_signals" in summary and "risk_score" in summary
    assert "thesis_of_the_day" in summary


def test_run_morning_degraded_mode(monkeypatch) -> None:
    """Sans Gemini (client None), le rapport part quand même (mode dégradé)."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    monkeypatch.setattr(main, "_collect_morning_data", lambda pd: _fake_data())
    # Force le client à None → generate_morning renvoie le payload dégradé réel.
    monkeypatch.setattr(DecisionEngine, "__init__", lambda self, client=None: None)
    monkeypatch.setattr(DecisionEngine, "client", None, raising=False)
    monkeypatch.setattr(DecisionEngine, "_init_error", "pas de clé (test)", raising=False)
    monkeypatch.setattr(charts, "charts_for_theses", lambda theses, limit=4: {})
    monkeypatch.setattr(main, "send_email", lambda subject, html, inline_images=None: True)

    rc = main.run_morning()
    assert rc == 0
    saved = mem.load_morning_report()
    assert saved.get("_degraded") is True


def _seed_morning(tmp) -> None:
    """Écrit un rapport matin réaliste (du jour) lu par le soir."""
    mem.save_morning_report({
        "header": {"date": "17/06"},
        "portfolio_snapshot": {"value_usd": 2630.0, "change_7d_pct": -3.0},
        "macro_context": {"fear_greed": 22, "btc_price": 64800.0},
        "risk_score": {"score": 4.2, "level": "modéré",
                       "components": [], "holdings_snapshot": []},
        "thesis_of_the_day": [{"asset": "ETH", "action": "RENFORCER"}],
    })


def _patch_evening_sources(monkeypatch) -> None:
    from src.data_sources import (coingecko, crypto_rss, etf_flows, fear_greed,
                                  fred, macro_calendar, market_prices, newsapi,
                                  prediction_markets)
    monkeypatch.setattr(coingecko, "get_market_data", lambda syms: {})
    monkeypatch.setattr(fear_greed, "get_fear_greed", lambda: {"available": False})
    monkeypatch.setattr(etf_flows, "get_etf_flows", lambda: {"available": False})
    monkeypatch.setattr(newsapi, "get_recent_news", lambda q, hours=24: [])
    monkeypatch.setattr(crypto_rss, "get_news", lambda **k: {"available": False})
    monkeypatch.setattr(fred, "get_macro", lambda: {"available": False})
    monkeypatch.setattr(prediction_markets, "get_key_markets", lambda: {"available": False})
    monkeypatch.setattr(market_prices, "get_macro_quotes", lambda: {})
    monkeypatch.setattr(market_prices, "get_macro_deltas", lambda: {})
    monkeypatch.setattr(market_prices, "get_equity_quotes", lambda: {})
    monkeypatch.setattr(market_prices, "compute_macro_source_status", lambda *a, **k: {})
    monkeypatch.setattr(macro_calendar, "get_consolidated_calendar",
                        lambda horizon_days=7: {"available": False})


def test_run_evening_degradation_end_to_end(monkeypatch) -> None:
    """Soir : avec toutes les sources DOWN, le rapport part quand même (rendu +
    persistance), sans crash — chemin de dégradation gracieuse critique."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    _seed_morning(tmp)
    _patch_evening_sources(monkeypatch)
    monkeypatch.setattr(DecisionEngine, "generate_evening",
                        lambda self, **kw: {"header": {},
                                            "delta_summary": [{"icon": "✓", "text": "RAS"}],
                                            "synthesis": "Soir calme."})
    sent = {}
    monkeypatch.setattr(main, "send_email",
                        lambda subject, html, inline_images=None: sent.update(html=html) or True)
    rc = main.run_evening()
    assert rc == 0
    assert len(sent.get("html", "")) > 1500
    saved = mem.load_evening_report()
    assert saved.get("portfolio_snapshot", {}).get("value_usd") is not None
    assert saved.get("daily_pnl") is not None


def _patch_weekly_sources(monkeypatch) -> None:
    from src.data_sources import (binance_futures, coingecko, coinmetrics,
                                  deribit, etf_flows, fred, macro_calendar,
                                  market_prices, prediction_markets)
    monkeypatch.setattr(coingecko, "get_market_data", lambda syms: {})
    monkeypatch.setattr(coingecko, "get_price_volume_series", lambda s, days=30: None)
    monkeypatch.setattr(macro_calendar, "get_consolidated_calendar",
                        lambda horizon_days=8: {"available": False})
    monkeypatch.setattr(prediction_markets, "get_key_markets", lambda: {"available": False})
    monkeypatch.setattr(etf_flows, "get_etf_flows", lambda: {"available": False})
    monkeypatch.setattr(fred, "get_macro_series", lambda n=40: {})
    monkeypatch.setattr(coinmetrics, "get_onchain_metrics", lambda: {"available": False})
    monkeypatch.setattr(deribit, "get_options_metrics", lambda: {"available": False})
    monkeypatch.setattr(binance_futures, "get_derivatives", lambda s: {"available": False})
    monkeypatch.setattr(market_prices, "compute_crypto_price_status", lambda *a, **k: {})


def test_run_weekly_degradation_end_to_end(monkeypatch) -> None:
    """Hebdo : sources DOWN → rapport rendu et persisté sans crash."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    _seed_morning(tmp)
    _patch_weekly_sources(monkeypatch)
    monkeypatch.setattr(DecisionEngine, "generate_weekly",
                        lambda self, **kw: {"header": {}, "weekly_narrative": "Semaine calme."})
    monkeypatch.setattr(main, "send_email", lambda subject, html, inline_images=None: True)
    rc = main.run_weekly()
    assert rc == 0
    saved = mem.load_weekly_report()
    assert saved  # un payload weekly a bien été persisté
