# -*- coding: utf-8 -*-
"""V31 — CÂBLAGE producteur -> consommateur (anti signal mort).

L'audit final a trouvé QUATRE faits qui ne pouvaient jamais naître : le
consommateur lisait des clés absentes de la charge réelle.

    fred      lu à la racine    -> get_macro renvoie {available, series:{…}}
    onchain   lu « mvrv »       -> les sources servent hashrate / gas
    etf_flows lu « net_flow_usd »-> la source sert btc/eth.total_flow_musd

Aucun crash, aucun faux chiffre : juste une absence silencieuse. C'est la
définition même du signal mort, et c'est indétectable sans confronter le
consommateur à la FORME RÉELLE du producteur — ce que fait ce fichier.
"""
from __future__ import annotations

import inspect

import pytest

from src.core import source_result as sr
from src.core.facts import FactStore
from src.pipeline import context as ctx_mod


# ══════════════════════════════════════════════════════════════════════════
# Les fonctions appelées existent, avec les paramètres utilisés
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("module,func,params", [
    ("src.data_sources.fear_greed", "get_fear_greed", ()),
    ("src.data_sources.fred", "get_macro", ()),
    ("src.data_sources.onchain_btc", "get_btc_onchain", ()),
    ("src.data_sources.onchain_eth", "get_eth_onchain", ()),
    ("src.data_sources.etf_flows", "get_etf_flows", ()),
    ("src.data_sources.prediction_markets", "get_key_markets", ()),
    ("src.data_sources.crypto_rss", "get_news", ("limit",)),
    ("src.data_sources.macro_calendar", "get_consolidated_calendar",
     ("horizon_days",)),
    ("src.data_sources.binance_futures", "get_derivatives", ("symbol",)),
    ("src.data_sources.market_prices", "get_macro_quotes_detailed", ()),
    ("src.data_sources.coingecko", "get_market_data", ("symbols",)),
    ("src.data_sources.coingecko", "get_price_volume_series",
     ("symbol", "days", "interval")),
    ("src.data_sources.coingecko", "get_ohlc_raw", ("symbol", "days")),
])
def test_every_wired_source_function_exists(module, func, params):
    import importlib
    fn = getattr(importlib.import_module(module), func, None)
    assert callable(fn), f"{module}.{func} introuvable"
    sig = inspect.signature(fn).parameters
    for p in params:
        assert p in sig, f"{module}.{func} n'accepte pas « {p} »"


# ══════════════════════════════════════════════════════════════════════════
# Les faits naissent de la FORME RÉELLE des charges
# ══════════════════════════════════════════════════════════════════════════

def _store(sources):
    st = FactStore()
    ctx_mod._register_context(st, sources)
    return st


def test_fred_facts_are_read_from_the_series_subtree():
    """Forme réelle : {available, series: {nom: {value, date, previous, delta}}}."""
    payload = {"available": True, "series": {
        "dxy": {"value": 103.4, "date": "2026-08-09"},
        "us_10y": {"value": 4.21, "date": "2026-08-09"},
        "vix": {"value": 14.8, "date": "2026-08-09"},
    }}
    st = _store({"fred": sr.ok("fred", payload)})
    assert st.has("macro.dxy") and st.has("macro.us_10y")
    assert st.has("macro.vix")
    assert st.formatted("macro.dxy") == "103,40"


def test_fred_root_level_keys_would_produce_nothing():
    """Régression : lire à la racine ne doit plus rien produire (et c'est vu)."""
    st = _store({"fred": sr.ok("fred", {"available": True, "dxy": 103.4})})
    assert not st.has("macro.dxy"), "aucune clé macro à la racine"


def test_onchain_facts_match_what_the_sources_actually_serve():
    """BTC sert hash_rate_ehs / miners_revenue_usd ; ETH sert le gas."""
    payload = {
        "BTC": {"available": True, "hash_rate_ehs": 642.5,
                "miners_revenue_usd": 41_200_000.0, "difficulty": 9.2e13},
        "ETH": {"available": True, "gas_safe_gwei": 3.1,
                "gas_propose_gwei": 4.2, "gas_fast_gwei": 6.0},
    }
    st = _store({"onchain": sr.ok("onchain", payload)})
    assert st.has("onchain.btc.hashrate")
    assert st.has("onchain.btc.miners_revenue")
    assert st.has("onchain.eth.gas")
    assert not st.has("onchain.btc.mvrv"), "MVRV n'est servi par aucune source"


def test_etf_flows_are_converted_from_millions_to_dollars():
    """La source sert des MILLIONS : la conversion doit être explicite."""
    payload = {"available": True,
               "btc": {"date": "2026-08-08", "total_flow_musd": 173.7,
                       "source": "Farside"},
               "eth": {"date": "2026-08-08", "total_flow_musd": -12.4,
                       "source": "Farside"}}
    st = _store({"etf_flows": sr.ok("etf_flows", payload)})
    assert st.has("macro.etf_btc_flow") and st.has("macro.etf_eth_flow")
    assert st.get("macro.etf_btc_flow").value == pytest.approx(173_700_000.0)
    assert "M$" in st.formatted("macro.etf_btc_flow")
    assert st.formatted("macro.etf_eth_flow").startswith("−")


def test_fear_greed_reads_value_and_label():
    st = _store({"fear_greed": sr.ok(
        "fear_greed", {"value": 18, "classification": "Extreme Fear"})})
    assert st.formatted("macro.fear_greed") == "18"
    assert st.formatted("macro.fear_greed_label") == "Extreme Fear"


# ══════════════════════════════════════════════════════════════════════════
# Aucun fait ne naît d'une source inutilisable — jamais de zéro inventé
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("result", [
    sr.empty("fred"),
    sr.unavailable("fred", sr.Failure(http_status=503, retryable=True)),
    sr.dead("fred", sr.Failure(http_status=410)),
])
def test_an_unusable_source_creates_no_fact_at_all(result):
    st = _store({"fred": result, "onchain": result, "etf_flows": result,
                 "fear_greed": result})
    assert st.ids() == []


def test_a_degraded_source_still_feeds_facts_but_marks_them_stale():
    st = _store({"fear_greed": sr.degraded(
        "fear_greed", {"value": 22, "classification": "Fear"}, "repli")})
    assert st.has("macro.fear_greed")
    assert "macro.fear_greed" in st.stale_ids()
    assert "macro.fear_greed" not in st.referenceable_ids()


def test_a_partial_payload_creates_only_the_facts_it_carries():
    st = _store({"onchain": sr.ok("onchain", {"BTC": {"available": True,
                                                      "hash_rate_ehs": 600.0}})})
    assert st.has("onchain.btc.hashrate")
    assert not st.has("onchain.btc.miners_revenue")
    assert not st.has("onchain.eth.gas")