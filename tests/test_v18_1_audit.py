"""Tests de l'audit v18.1 — corrections + renforcement du bot Telegram.

Couvre :
  • coherence_checker : ALLÉGER (accentué) désormais soumis aux garde-fous fermes.
  • commands._cmd_resume : rendu propre des puces (plus de dict brut).
  • live_data : valorisation live du PTF + repli baseline + snapshot marché.
  • assistant : détection « recherche » et routage generate vs generate_with_search.
  • context_loader : whitelist élargie + injection des données live.
"""

from __future__ import annotations


import src.main as main
from src.analytics.coherence_checker import check_report
from src.analytics import thesis_scoring
from src.telegram_bot import assistant, commands
from src.telegram_bot import live_data
from src.telegram_bot import context_loader
from src.state import report_memory as mem


# --------------------------------------------------------------------------- #
# thesis_scoring — signal « proche support » (clé nichée, bug manqué par audit)
# --------------------------------------------------------------------------- #
def test_thesis_support_signal_reads_nested_key() -> None:
    """Le signal technique 'proche d'un support' doit s'allumer en lisant la
    VRAIE structure (support_resistance.dist_to_support_pct), pas une clé à plat
    inexistante (qui le rendait mort)."""
    asset = {
        "change_24h": 0.5,
        "tech_advanced": {
            "available": True,
            "support_resistance": {"available": True, "dist_to_support_pct": 1.2},
        },
    }
    res = thesis_scoring.evaluate_thesis_eligibility(asset, tier=2)
    labels = " ".join(s["label"] for s in res["signals"])
    assert "support" in labels
    # Poids 2 (technical_struct) effectivement compté dans le score.
    assert res["score"] >= 2


def test_thesis_support_signal_absent_when_far() -> None:
    asset = {"change_24h": 0.0, "tech_advanced": {
        "available": True,
        "support_resistance": {"available": True, "dist_to_support_pct": 9.0}}}
    res = thesis_scoring.evaluate_thesis_eligibility(asset, tier=2)
    assert "support" not in " ".join(s["label"] for s in res["signals"])


def test_build_asset_signals_stores_news_count(monkeypatch) -> None:
    """_build_asset_signals doit STOCKER news_24h_count sur l'actif, sinon le
    signal 'news récente' de thesis_scoring est mort (clé jamais posée)."""
    from src.data_sources import defillama, github_dev, tradingview
    monkeypatch.setattr(tradingview, "get_technical", lambda s: {"available": False})
    monkeypatch.setattr(github_dev, "get_dev_activity", lambda s: {"available": False})
    monkeypatch.setattr(defillama, "get_protocol_tvl", lambda s: {"available": False})
    asset = main._build_asset_signals(
        "ZK", 3, {"price": 0.05, "change_24h": 1.0, "ath": 0.3},
        0.0, 3, None, {"available": False})
    assert asset["news_24h_count"] == 3
    # Et le consommateur thesis_scoring l'utilise réellement.
    res = thesis_scoring.evaluate_thesis_eligibility(asset, tier=3)
    assert any("news" in s["label"] for s in res["signals"])


def test_cross_signals_strategic_wallets_reading() -> None:
    """Le signal #5 (wallets stratégiques) doit produire une LECTURE dans les
    readings : avant, le producteur ne fournissait pas 'interpretation' → signal
    ajouté sans aucune lecture injectée au prompt."""
    from src.analytics import cross_signals
    sw = {"available": True,
          "movements": [{"label": "Foundation", "eth": 1200, "direction": "sortant"}],
          "interpretation": "1 mouvement de wallet stratégique sur 24h."}
    res = cross_signals.compute_all({}, {}, strategic_wallets=sw)
    assert "strategic_wallets" in res["signals"]
    assert any("mouvement" in r for r in res["readings"])


def test_thesis_unlock_signal_fires_when_imminent() -> None:
    """Le catalyseur token_unlock_soon (poids 2) s'allume quand l'actif a un
    unlock imminent (clé désormais posée par main.py)."""
    asset = {"change_24h": 0.0, "token_unlock_soon": True}
    res = thesis_scoring.evaluate_thesis_eligibility(
        asset, tier=1, token_unlock_soon=bool(asset.get("token_unlock_soon")))
    labels = " ".join(s["label"] for s in res["signals"])
    assert "unlock" in labels
    assert any(s["category"] == "catalyst" and s["weight"] == 2 for s in res["signals"])


# --------------------------------------------------------------------------- #
# coherence_checker — bug accent ALLÉGER
# --------------------------------------------------------------------------- #
def test_coherence_downgrades_alleger_accentue() -> None:
    """Une reco ALLÉGER (ACCENTUÉE, comme Gemini l'émet) mal fondée DOIT être
    rétrogradée — l'ancien code la laissait passer (égalité stricte 'ALLEGER')."""
    bad = {
        "thesis_of_the_day": [{
            "asset": "TAO", "action": "ALLÉGER", "confidence": 40,
            "reasoning_signals": ["pas de commit récent"],
            "action_plan": {}, "sources_timestamps": "selon les sources",
        }]
    }
    res = check_report(bad)
    assert not res["ok"]
    assert res["sanitized_payload"]["thesis_of_the_day"][0]["action"] == "SURVEILLER"


def test_coherence_still_handles_unaccented_alleger() -> None:
    """Non-régression : la forme sans accent reste gérée."""
    bad = {
        "thesis_of_the_day": [{
            "asset": "INJ", "action": "ALLEGER", "confidence": 40,
            "action_plan": {}, "sources_timestamps": "selon les sources",
        }]
    }
    res = check_report(bad)
    assert res["sanitized_payload"]["thesis_of_the_day"][0]["action"] == "SURVEILLER"


def test_coherence_keeps_healthy_alleger() -> None:
    """Une ALLÉGER bien fondée n'est PAS rétrogradée (pas de faux positif)."""
    good = {
        "thesis_of_the_day": [{
            "asset": "FET", "action": "ALLÉGER", "confidence": 68,
            "reasoning_signals": ["RSI hebdo 78 surachat", "résistance W1 testée"],
            "action_plan": {"stop_loss": 1.2, "take_profit": {"40pct": 0.9}},
            "sources_timestamps": "CoinGecko 08h12 · TradingView 08h15",
        }]
    }
    res = check_report(good)
    assert res["sanitized_payload"]["thesis_of_the_day"][0]["action"] == "ALLÉGER"


# --------------------------------------------------------------------------- #
# commands._cmd_resume — rendu des puces
# --------------------------------------------------------------------------- #
def test_resume_renders_bullets_not_raw_dict(monkeypatch) -> None:
    """executive_summary {'bullets':[...]} doit s'afficher en puces lisibles,
    jamais en dict Python brut (ancien bug str(summary))."""
    rep = {
        "header": {"date": "17/06"},
        "executive_summary": {"bullets": [
            {"icon": "⚠", "text": "BTC sous 64k, volume faible"},
            {"icon": "✓", "text": "ETH proche support 1620"},
        ]},
    }
    monkeypatch.setattr(mem, "load_morning_report", lambda: rep)
    monkeypatch.setattr(mem, "load_evening_report", lambda: {})
    out = commands._cmd_resume()
    assert "BTC sous 64k" in out and "ETH proche support 1620" in out
    assert "bullets" not in out and "{'" not in out and "icon" not in out


def test_resume_fallbacks_to_synthesis(monkeypatch) -> None:
    rep = {"header": {"date": "17/06"}, "synthesis": "Marché en range, patience."}
    monkeypatch.setattr(mem, "load_morning_report", lambda: rep)
    monkeypatch.setattr(mem, "load_evening_report", lambda: {})
    assert "range" in commands._cmd_resume()


# --------------------------------------------------------------------------- #
# live_data — valorisation live
# --------------------------------------------------------------------------- #
def _fake_portfolio() -> dict:
    return {"portfolio": {
        "BTC": {"quantity": 0.01422, "value_usd": 911.78, "tier": 1},
        "ETH": {"quantity": 0.3004, "value_usd": 500.12, "tier": 1},
        "ZK": {"quantity": 307.57, "value_usd": 3.37, "tier": 3},
    }}


def test_live_portfolio_snapshot_uses_live_prices(monkeypatch) -> None:
    from src.data_sources import coingecko
    from src.utils import portfolio_loader
    monkeypatch.setattr(portfolio_loader, "load_portfolio", _fake_portfolio)
    monkeypatch.setattr(coingecko, "get_market_data", lambda syms: {
        "BTC": {"price": 70000.0, "change_24h": 2.0, "change_7d": 5.0},
        "ETH": {"price": 2000.0, "change_24h": -1.0, "change_7d": 3.0},
        # ZK sans prix live → repli baseline.
    })
    snap = live_data.get_live_portfolio_snapshot()
    assert snap["available"] is True
    # BTC = 0.01422*70000 = 995.4 ; ETH = 0.3004*2000 = 600.8 ; ZK baseline 3.37
    btc = next(p for p in snap["positions"] if p["symbol"] == "BTC")
    assert abs(btc["value_usd"] - 995.4) < 0.5 and btc["priced_live"] is True
    zk = next(p for p in snap["positions"] if p["symbol"] == "ZK")
    assert zk["priced_live"] is False and abs(zk["value_usd"] - 3.37) < 0.01
    assert snap["positions_priced_live"] == 2 and snap["positions_total"] == 3
    assert abs(snap["total_value_usd"] - (995.4 + 600.8 + 3.37)) < 1.0
    # Tri décroissant + poids renseignés.
    assert snap["positions"][0]["symbol"] == "BTC"
    assert snap["positions"][0]["weight_pct"] is not None


def test_live_portfolio_snapshot_degrades_without_prices(monkeypatch) -> None:
    from src.data_sources import coingecko
    from src.utils import portfolio_loader
    monkeypatch.setattr(portfolio_loader, "load_portfolio", _fake_portfolio)
    monkeypatch.setattr(coingecko, "get_market_data", lambda syms: {})
    snap = live_data.get_live_portfolio_snapshot()
    assert snap["available"] is False


def test_live_market_snapshot(monkeypatch) -> None:
    from src.data_sources import coingecko, fear_greed
    monkeypatch.setattr(coingecko, "get_market_data", lambda syms: {
        "BTC": {"price": 70000.0, "change_24h": 2.0},
        "ETH": {"price": 2000.0, "change_24h": -1.0},
    })
    monkeypatch.setattr(coingecko, "get_global", lambda: {
        "available": True, "btc_dominance_pct": 54.2,
        "market_cap_change_24h_pct": 1.1,
    })
    monkeypatch.setattr(fear_greed, "get_fear_greed", lambda: {
        "available": True, "value": 62, "classification": "Greed",
    })
    snap = live_data.get_live_market_snapshot()
    assert snap["available"] is True
    assert snap["btc"]["price"] == 70000.0
    assert snap["btc_dominance_pct"] == 54.2
    assert snap["fear_greed"] == 62


# --------------------------------------------------------------------------- #
# assistant — détection recherche + routage
# --------------------------------------------------------------------------- #
def test_needs_research_detection() -> None:
    assert assistant._needs_research("quelles sont les dernières news sur ETH ?")
    assert assistant._needs_research("y a-t-il un unlock TAO bientôt ?")
    assert not assistant._needs_research("explique-moi le MVRV")
    assert not assistant._needs_research("combien vaut mon portefeuille ?")


class _FakeClient:
    def __init__(self) -> None:
        self.search_called = False
        self.plain_called = False

    def generate(self, prompt, *, temperature=0.6):
        self.plain_called = True
        return "réponse simple"

    def generate_with_search(self, prompt):
        self.search_called = True
        return ("réponse sourcée", ["http://src"])


def _patch_client(monkeypatch, fake):
    import src.ai_brain.gemini_client as gc
    monkeypatch.setattr(gc, "GeminiClient", lambda *a, **k: fake)


def test_answer_uses_search_for_news(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    out = assistant.answer("dernières news ETF Ethereum ?", {}, [])
    assert fake.search_called and not fake.plain_called
    assert out == "réponse sourcée"


def test_answer_plain_for_explanation(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    out = assistant.answer("explique-moi le MVRV", {}, [])
    assert fake.plain_called and not fake.search_called
    assert out == "réponse simple"


def test_answer_force_search_flag(monkeypatch) -> None:
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    assistant.answer("explique-moi le MVRV", {}, [], use_search=True)
    assert fake.search_called


# --------------------------------------------------------------------------- #
# context_loader — whitelist élargie + injection live
# --------------------------------------------------------------------------- #
def test_summarize_report_keeps_beta_and_crosssignals() -> None:
    rep = {
        "position_correlation": {"beta_btc": 1.4},
        "cross_signals": {"readings": ["signal A"]},
        "sector_rotation": {"top": "AI"},
        "crypto_price_status": {"BTC": "ok"},  # bruit → exclu
    }
    out = context_loader._summarize_report(rep, "morning")
    assert "position_correlation" in out and "cross_signals" in out
    assert "sector_rotation" in out
    assert "crypto_price_status" not in out


def test_load_full_context_injects_live(monkeypatch) -> None:
    monkeypatch.setattr(mem, "load_morning_report", lambda: {})
    monkeypatch.setattr(mem, "load_evening_report", lambda: {})
    monkeypatch.setattr(mem, "load_weekly_report", lambda: {})
    monkeypatch.setattr(mem, "load_active_recommendations", lambda: [])
    monkeypatch.setattr(mem, "load_weekly_snapshots", lambda: [])
    monkeypatch.setattr(context_loader, "_portfolio_live", lambda: {})
    monkeypatch.setattr(
        live_data, "get_live_portfolio_snapshot",
        lambda: {"available": True, "total_value_usd": 1599.6, "positions": []})
    monkeypatch.setattr(
        live_data, "get_live_market_snapshot",
        lambda: {"available": True, "btc": {"price": 70000}})
    ctx = context_loader.load_full_context()
    assert ctx["live_portfolio"]["total_value_usd"] == 1599.6
    assert ctx["live_market"]["btc"]["price"] == 70000
