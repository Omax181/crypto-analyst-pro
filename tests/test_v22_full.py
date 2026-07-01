"""Tests v22 — P1/P2/P3 (analyse complète). Verrouille les nouveaux modules et
sources, tous hermétiques (aucun réseau réel : dicts en dur / monkeypatch).

Couvre : valorisation fondamentale (FDV/MC, dilution, P/F, P/S, MC/TVL),
indicateurs techniques locaux (stochastique, ATR%), risque portefeuille (bêta-BTC,
concentration HHI, stress-test, VaR), force relative vs BTC, macro étendue
(courbe, taux réels, liquidité Fed, altseason), DeFiLlama fees, OKX long/short,
extraction CoinGecko FDV/supply.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# P1 — Valorisation fondamentale
# --------------------------------------------------------------------------- #
def test_valuation_metrics_and_signals():
    from src.analytics.valuation import compute_valuation
    market = {
        "market_cap": 1_000_000_000, "fully_diluted_valuation": 3_000_000_000,
        "circulating_supply": 300_000_000, "max_supply": 1_000_000_000,
    }
    tvl = {"available": True, "tvl_usd": 2_000_000_000}
    fees = {"available": True, "fees_annualized": 50_000_000, "revenue_annualized": 10_000_000}
    out = compute_valuation(market, tvl, fees)
    m = out["metrics"]
    assert out["available"] is True
    assert m["fdv_mc_ratio"] == 3.0          # 3Md / 1Md
    assert m["circulating_pct"] == 30.0
    assert m["dilution_remaining_pct"] > 200  # (1Md-300M)/300M
    assert m["pf_ratio"] == 20.0             # 1Md / 50M
    assert m["ps_ratio"] == 100.0            # 1Md / 10M
    assert m["mc_tvl_ratio"] == 0.5          # 1Md / 2Md → sous-évalué
    assert any("MC/TVL" in s for s in out["signals"])
    assert any("dilution" in s.lower() or "circulation" in s for s in out["signals"])


def test_valuation_empty():
    from src.analytics.valuation import compute_valuation
    assert compute_valuation({})["available"] is False


# --------------------------------------------------------------------------- #
# P2/P3 — Indicateurs techniques locaux ajoutés
# --------------------------------------------------------------------------- #
def test_stochastic_and_atr():
    from src.analytics.technical_local import compute_stochastic, compute_atr_pct
    up = [100 + i for i in range(20)]
    assert compute_stochastic(up) == 100.0           # prix au sommet du range
    assert compute_atr_pct(up) is not None and compute_atr_pct(up) > 0
    assert compute_stochastic([1, 2]) is None


def test_local_technical_includes_new_indicators():
    from src.analytics.technical_local import compute_local_technical
    out = compute_local_technical([100 + (i % 6) for i in range(40)])
    assert "stochastic_k" in out and "atr_pct" in out


# --------------------------------------------------------------------------- #
# P1 — Risque portefeuille & force relative
# --------------------------------------------------------------------------- #
def test_relative_strength_vs_btc():
    from src.analytics.portfolio_risk import relative_strength_vs_btc
    dates = [f"2026-05-{d:02d}" for d in range(1, 21)]
    alt = {d: 100 + i * 2 for i, d in enumerate(dates)}     # +2/j
    btc = {d: 100 + i * 0.5 for i, d in enumerate(dates)}   # +0.5/j
    out = relative_strength_vs_btc(alt, btc)
    assert out["available"] is True
    assert out["rs"]["7d"] > 0                              # surperforme BTC
    assert "surperforme" in out["reading"]


def test_portfolio_risk_block():
    from src.analytics.portfolio_risk import compute_portfolio_risk
    dates = [f"2026-05-{d:02d}" for d in range(1, 25)]
    btc = {d: 100 + i for i, d in enumerate(dates)}
    eth = {d: 100 + i * 1.5 for i, d in enumerate(dates)}
    asset_dated = {"BTC": btc, "ETH": eth}
    values = {"BTC": 6000.0, "ETH": 4000.0}
    out = compute_portfolio_risk(asset_dated, btc, values)
    assert out["available"] is True
    assert out["concentration"]["positions"] == 2
    assert out["concentration"]["effective_bets"] is not None
    assert out["stress_test"]["btc_shock_pct"] == -20.0
    # PTF baisse si BTC baisse (bêta positif).
    assert out["stress_test"]["ptf_estimated_move_pct"] < 0
    assert "readings" in out and out["readings"]


def test_portfolio_risk_no_values():
    from src.analytics.portfolio_risk import compute_portfolio_risk
    assert compute_portfolio_risk({}, {}, {})["available"] is False


# --------------------------------------------------------------------------- #
# P2 — Macro étendue (cross_signals)
# --------------------------------------------------------------------------- #
def test_yield_curve_inverted():
    from src.analytics.cross_signals import yield_curve
    fs = {"us_10y": {"d1": 3.5, "d2": 3.8}, "us_2y": {"d1": 4.0, "d2": 4.5}}
    out = yield_curve(fs)
    assert out["available"] is True and out["spread"] < 0
    assert "INVERSÉE" in out["reading"]


def test_real_rates_levels():
    from src.analytics.cross_signals import real_rates
    assert "élevé" in real_rates({"real_10y": {"d": 2.4}})["reading"]
    assert "bas" in real_rates({"real_10y": {"d": 0.2}})["reading"]
    assert real_rates({})["available"] is False


def test_fed_liquidity_trend():
    from src.analytics.cross_signals import fed_liquidity
    contraction = {"fed_assets": {"2026-01-01": 8000, "2026-02-01": 7800},
                   "reverse_repo": {"d": 500}}
    out = fed_liquidity(contraction)
    assert out["available"] is True and out["trend"] == "contraction"


def test_altseason_context():
    from src.analytics.cross_signals import altseason_context
    assert "DÉFAVORABLES" in altseason_context({"btc_dominance_pct": 60})["reading"]
    assert "alts" in altseason_context({"btc_dominance_pct": 40})["reading"]
    assert altseason_context({})["available"] is False


def test_cross_signals_compute_all_includes_v22(monkeypatch):
    """compute_all expose les nouvelles lectures macro + altseason."""
    from src.analytics import cross_signals
    fs = {
        "us_10y": {"a": 3.5, "b": 3.6}, "us_2y": {"a": 4.2, "b": 4.3},
        "real_10y": {"a": 2.3}, "fed_assets": {"2026-01-01": 8000, "2026-02-01": 7700},
        "reverse_repo": {"a": 400},
    }
    out = cross_signals.compute_all(fs, {}, global_market={"btc_dominance_pct": 58})
    assert "yield_curve" in out["signals"]
    assert "real_rates" in out["signals"]
    assert "fed_liquidity" in out["signals"]
    assert "altseason" in out["signals"]


# --------------------------------------------------------------------------- #
# Sources — DeFiLlama fees, OKX long/short, CoinGecko extraction
# --------------------------------------------------------------------------- #
def test_defillama_fees_parsing(monkeypatch):
    from src.data_sources import defillama
    defillama.CACHE._store.clear()
    monkeypatch.setattr(defillama, "get_json",
                        lambda url, params=None, **k: {"total24h": 1_000_000, "total30d": 30_000_000})
    out = defillama.get_protocol_fees("AAVE")
    assert out["available"] is True
    assert out["fees_24h"] == 1_000_000
    assert out["fees_annualized"] == round(30_000_000 / 30 * 365, 0)


def test_defillama_fees_unknown_symbol():
    from src.data_sources import defillama
    assert defillama.get_protocol_fees("JASMY")["available"] is False


def test_okx_long_short_ratio(monkeypatch):
    from src.data_sources import binance_futures as bf

    def fake_get_json(url, params=None, **k):
        if "funding-rate-history" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        if "mark-price" in url:
            return {"data": [{"markPx": "60000"}]}
        if "open-interest" in url:
            return {"data": [{"oiCcy": "1000"}]}
        if "long-short-account-ratio" in url:
            return {"data": [["1690000000000", "1.85"]]}
        return None

    monkeypatch.setattr(bf, "get_json", fake_get_json)
    out = bf._fetch_okx("BTC", "BTC-USDT-SWAP")
    assert out["available"] is True
    assert out["long_short_ratio"] == 1.85


def test_coingecko_extracts_fdv_and_supply(monkeypatch):
    from src.data_sources import coingecko
    coingecko.CACHE._store.clear()
    monkeypatch.setattr(coingecko, "get_json", lambda *a, **k: [{
        "id": "bitcoin", "current_price": 60000, "market_cap": 1_200_000_000_000,
        "fully_diluted_valuation": 1_260_000_000_000, "circulating_supply": 19_700_000,
        "total_supply": 19_700_000, "max_supply": 21_000_000, "ath": 73000, "atl": 67,
    }])
    out = coingecko.get_market_data(["BTC"])
    assert out["BTC"]["fully_diluted_valuation"] == 1_260_000_000_000
    assert out["BTC"]["circulating_supply"] == 19_700_000
    assert out["BTC"]["max_supply"] == 21_000_000


def test_fred_new_series_configured():
    from src.data_sources.fred import _CORR_SERIES
    for k in ("us_2y", "real_10y", "fed_assets", "reverse_repo"):
        assert k in _CORR_SERIES


# --------------------------------------------------------------------------- #
# Intégration — valuation câblée + complétude
# --------------------------------------------------------------------------- #
def test_build_asset_signals_attaches_valuation(monkeypatch):
    from src import main
    from src.data_sources import defillama, github_dev, tradingview
    monkeypatch.setattr(tradingview, "get_technical", lambda s: {"available": False})
    monkeypatch.setattr(github_dev, "get_dev_activity", lambda s: {"available": False})
    monkeypatch.setattr(defillama, "get_protocol_tvl",
                        lambda s: {"available": True, "tvl_usd": 2_000_000_000})
    monkeypatch.setattr(defillama, "get_protocol_fees", lambda s: {"available": False})
    market = {"price": 5.0, "change_24h": 1.0, "ath": 20.0,
              "market_cap": 1_000_000_000, "fully_diluted_valuation": 2_000_000_000}
    asset = main._build_asset_signals("LINK", 1, market, 0.0, 0, None, {"available": False})
    assert asset["valuation"]["available"] is True
    assert asset["valuation"]["metrics"]["mc_tvl_ratio"] == 0.5


def test_completeness_counts_valuation():
    from src.analytics.thesis_scoring import compute_completeness
    asset = {"valuation": {"available": True, "metrics": {"mc_tvl_ratio": 0.5}}}
    cf = compute_completeness(asset)
    assert cf["available"]["fondamental"] is True


# --------------------------------------------------------------------------- #
# P3 — Tradabilité (garde-fou liquidité)
# --------------------------------------------------------------------------- #
def test_tradability_levels():
    from src.analytics.valuation import compute_tradability
    assert compute_tradability(500_000)["liquidity"] == "faible"
    assert compute_tradability(5_000_000)["liquidity"] == "modérée"
    assert compute_tradability(50_000_000)["liquidity"] == "bonne"
    assert compute_tradability(None)["available"] is False
    out = compute_tradability(10_000_000, 100_000)
    assert out["position_vs_volume_pct"] == 1.0


# --------------------------------------------------------------------------- #
# P2 — CoinMarketCal (catalyseurs crypto datés)
# --------------------------------------------------------------------------- #
def test_coinmarketcal_disabled_without_key(monkeypatch):
    from src.data_sources import coinmarketcal
    monkeypatch.delenv("COINMARKETCAL_API_KEY", raising=False)
    assert coinmarketcal.get_events()["available"] is False


def test_coinmarketcal_parsing(monkeypatch):
    from src.data_sources import coinmarketcal
    coinmarketcal.CACHE._store.clear()
    monkeypatch.setenv("COINMARKETCAL_API_KEY", "x")
    monkeypatch.setattr(coinmarketcal, "get_json", lambda *a, **k: {"body": [
        {"title": {"en": "Mainnet launch"}, "date_event": "2026-07-01",
         "coins": [{"symbol": "INJ"}], "categories": [{"name": "Release"}]},
    ]})
    out = coinmarketcal.get_events()
    assert out["available"] is True
    ev = out["events"][0]
    assert ev["title"] == "Mainnet launch"
    assert ev["coins"] == ["INJ"]
    assert ev["category"] == "Release"


# --------------------------------------------------------------------------- #
# INTÉGRATION — points de câblage dans _collect_morning_data (anti-régression)
# --------------------------------------------------------------------------- #
def test_collect_morning_wires_v22_blocks():
    """Les nouveaux blocs sont bien appelés/exposés dans la collecte matin."""
    import inspect
    from src import main
    src_collect = inspect.getsource(main._collect_morning_data)
    # Risque PTF, force relative, catalyseurs crypto, macro tilt, altseason.
    assert "_prisk.compute_portfolio_risk(" in src_collect
    assert "relative_strength_vs_btc(" in src_collect
    assert "coinmarketcal.get_events(" in src_collect
    assert "macro_alignment_score" in src_collect
    assert "global_market=glob" in src_collect
    # Exposés dans le dict data.
    assert '"portfolio_risk": portfolio_risk' in src_collect
    assert '"crypto_events": crypto_events' in src_collect
    # L'entrée éligible expose les nouveaux champs consommés par le prompt
    # (anti signal mort) : derivatives (funding + long_short), valuation, tradability.
    assert '"derivatives": asset.get("derivatives")' in src_collect
    assert '"valuation": asset.get("valuation")' in src_collect
    assert "compute_tradability(asset.get(\"volume_24h\")" in src_collect
    assert "relative_strength_vs_btc(" in src_collect


def test_morning_payload_exposes_v22(monkeypatch):
    """_merge_python_facts propage portfolio_risk + crypto_events au payload."""
    import inspect
    from src import main
    src_merge = inspect.getsource(main._merge_python_facts)
    assert 'payload["portfolio_risk"] = data["portfolio_risk"]' in src_merge
    assert 'payload["crypto_events"] = data["crypto_events"]' in src_merge
