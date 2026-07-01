"""Tests v21 — AUDIT (les 79 points). Verrouille les comportements nouveaux :
résilience sources (OKX/bitcoin-data/402 gracieux), convergence des thèses (#73),
markdown→HTML mails (#A), heatmap 5×3 (#72), graphiques adaptatifs (#71),
win-rate None, consolidation calibration (W8).

Hermétiques : aucun réseau réel (monkeypatch/mocks).
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# WS-H (#73) — convergence ≥2 familles (ou cluster fondamental fort)
# --------------------------------------------------------------------------- #
def test_thesis_single_family_not_eligible():
    """2 signaux d'UNE SEULE famille (court terme) → non convergent → inéligible."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility
    out = evaluate_thesis_eligibility(
        {"change_24h": 6.0, "news_24h_count": 1, "tech_advanced": {}}, tier=3,
    )
    assert out["score"] >= out["threshold"]      # le score passerait...
    assert out["families_count"] == 1
    assert out["convergent"] is False
    assert out["eligible"] is False              # ...mais pas la convergence


def test_thesis_two_families_eligible():
    """Court terme + technique = 2 familles → convergent → éligible."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility
    out = evaluate_thesis_eligibility(
        {"change_24h": 6.0, "tech_advanced": {"bollinger": {"position": "lower"}}},
        tier=3,
    )
    assert out["families_count"] >= 2
    assert out["convergent"] is True
    assert out["eligible"] is True


def test_thesis_strong_fundamental_cluster_eligible_alone():
    """MVRV<1 + sous PRU (2 signaux fondamentaux, 1 famille) → cluster fort éligible."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility
    out = evaluate_thesis_eligibility(
        {"change_24h": 0.5, "tech_advanced": {}}, tier=1, mvrv=0.9, pru_gap_pct=-15,
    )
    assert out["families_count"] == 1
    assert out["strong_fundamental_cluster"] is True
    assert out["convergent"] is True and out["eligible"] is True


# --------------------------------------------------------------------------- #
# WS-E (M11/W6) — win rate None (pas 0%) quand aucune reco clôturée
# --------------------------------------------------------------------------- #
def test_win_rate_none_when_no_closed(monkeypatch):
    from src.tracking.prediction_scoring import PredictionTracker
    from src.state import report_memory as mem
    monkeypatch.setattr(mem, "load_prediction_history", lambda: [])
    wr = PredictionTracker().compute_win_rate(30)
    assert wr["total"] == 0
    assert wr["win_rate_pct"] is None


# --------------------------------------------------------------------------- #
# WS-A (#A) — markdown converti même dans un champ SANS filtre |md explicite
# --------------------------------------------------------------------------- #
def test_mdify_converts_bold_globally():
    from src.reporting.email_html import render
    payload = {
        "header": {"date": "26/06"},
        "scenarios": [{"type": "bearish", "label": "BAISSIER", "probability_pct": 25,
                       "description": "Cassure **L1**.", "action": "Alléger **CKB**."}],
        "exit_plan": {"monitoring": "Pour **NOT**, attendre."},
    }
    html = render(payload, "weekly")
    assert "**" not in html
    assert "<strong>" in html


# --------------------------------------------------------------------------- #
# WS-G (#72 / v23.x) — heatmap 5×4 = 19 cellules + 1 agrégat = 20 cases
# --------------------------------------------------------------------------- #
def test_heatmap_20_cells():
    from src.main import _portfolio_heatmap
    enriched = {f"C{i}": {"value_usd": 100 + i, "change_24h": (i % 9) - 4.0}
                for i in range(28)}
    hm = _portfolio_heatmap(enriched)
    assert len(hm["cells"]) == 19            # 19 cases pleines (4 lignes × 5 − 1)
    assert hm["extra"]["count"] == 9         # 28 − 19 = 9 → « +9 autres »


# --------------------------------------------------------------------------- #
# v23 — graphique d'analyse RICHE unifié (MM50/100/200 + S/R + Fibonacci + RSI)
# remplace la sélection adaptative ; plus de ligne « ATH » mal-labellisée.
# --------------------------------------------------------------------------- #
def test_rich_chart_returns_png(monkeypatch):
    from src.data_sources import coingecko
    from src.reporting import charts
    closes = [100 + i * 0.4 + (i % 9) for i in range(260)]  # >200 → MM200 calculable
    monkeypatch.setattr(coingecko, "get_price_volume_series",
                        lambda sym, days=90: {"closes": closes})
    png = charts.chart_for_thesis(
        {"asset": "TAO", "support_resistance": {"support": 110, "resistance": 230}})
    assert isinstance(png, (bytes, bytearray)) and png[:4] == b"\x89PNG"


def test_fib_and_sma_helpers():
    from src.reporting.charts import _sma, _fib_levels
    assert _sma([1, 2, 3, 4], 2)[-1] == 3.5
    assert _sma([1, 2], 5) == [None, None]          # historique trop court → None
    fib = _fib_levels(200, 100)
    assert abs(fib["0.5"] - 150) < 1e-9             # retracement 50%
    assert _fib_levels(100, 200) == {}              # high <= low → vide


def test_chart_analysis_selection():
    """v23 — l'analyse graphique s'ADAPTE au signal qui porte la thèse."""
    from src.reporting.charts import _select_analysis as k
    assert k({"thesis_scoring": {"signals": [{"label": "à 1% d'un support clé"}]}}) == "support_resistance"
    assert k({"thesis_scoring": {"signals": [{"label": "RSI 28 survente"}]}}) == "rsi"
    assert k({"thesis_scoring": {"signals": [{"label": "bande basse de Bollinger"}]}}) == "bollinger"
    assert k({"thesis_scoring": {"signals": [{"label": "drawdown 70% vs ATH"}]}}) == "fibonacci"
    assert k({"thesis_scoring": {"signals": [{"label": "croisement MM50/MM200"}]}}) == "trend"
    assert k({"thesis_scoring": {"signals": [], "thesis_type": "conviction"}}) == "fibonacci"
    assert k({"thesis_scoring": {"signals": [{"label": "news récente"}]}}) == "trend"


# --------------------------------------------------------------------------- #
# WS-B — résilience sources
# --------------------------------------------------------------------------- #
def test_funding_okx_first(monkeypatch):
    from src.data_sources import binance_futures as bf
    bf.CACHE._store.clear()
    monkeypatch.setattr(bf, "_fetch_okx",
                        lambda sym, inst: {"available": True, "source": "OKX",
                                           "funding_rate_pct": 0.01})
    # Si Binance était appelé, get_json lèverait (on le rend explosif).
    monkeypatch.setattr(bf, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Binance ne doit pas être appelé")))
    out = bf.get_derivatives("BTC")
    assert out["available"] and out["source"] == "OKX"


def test_funding_skips_binance_on_actions(monkeypatch):
    from src.data_sources import binance_futures as bf
    bf.CACHE._store.clear()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(bf, "_fetch_okx", lambda sym, inst: {"available": False})
    monkeypatch.setattr(bf, "get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Binance évité sur Actions")))
    out = bf.get_derivatives("BTC")
    assert out["available"] is False
    assert "Actions" in out.get("reason", "")


def test_lunarcrush_disabled_free_tier(monkeypatch):
    from src.data_sources import lunarcrush
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "x")
    monkeypatch.delenv("LUNARCRUSH_PAID", raising=False)
    out = lunarcrush.get_social_metrics("BTC")
    assert out["available"] is False


def test_unlocks_disabled_by_default(monkeypatch):
    from src.data_sources import token_unlocks as tu
    monkeypatch.delenv("DEFILLAMA_PAID", raising=False)
    tu.CACHE._store.clear()
    out = tu.get_upcoming_unlocks()
    assert out["available"] is False
    assert "payant" in out.get("reason", "")


def test_onchain_btc_freshness_overlay(monkeypatch):
    """Miroir BTC périmé + bitcoin-data.com frais → MVRV BTC rafraîchi (stale False)."""
    from src.data_sources import coinmetrics as cm
    from src.data_sources import bitcoin_data
    cm.CACHE._store.clear()
    monkeypatch.setattr(cm, "get_json", lambda *a, **k: None)  # API morte
    monkeypatch.setattr(cm, "_fetch_mirror_asset",
                        lambda cm_id: {"time": "2026-05-23", "PriceUSD": 60000.0,
                                       "CapMVRVCur": 1.40})
    monkeypatch.setattr(bitcoin_data, "get_btc_mvrv",
                        lambda: {"available": True, "mvrv": 1.14, "as_of": "2026-06-26",
                                 "mvrv_zscore": 0.23, "source": "bitcoin-data.com"})
    out = cm.get_onchain_metrics()
    btc = out["assets"]["BTC"]
    assert btc["mvrv"] == 1.14
    assert btc["stale"] is False
    assert btc["mvrv_source"] == "bitcoin-data.com"
    assert btc["as_of"] == "2026-06-26"
