"""Tests v14.1 — international (BCE/BoJ/Nikkei/Stoxx), actions ↔ crypto,
fallback miroir Coin Metrics, unlocks DefiLlama, parsing nombres FR, rendu.

Tous hors-ligne (mocks) : aucun appel réseau.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from src.reporting.email_html import render


# ─────────────────────────────────────────────────────────────────────────────
# market_prices — extraction détaillée, clôtures datées, actions
# ─────────────────────────────────────────────────────────────────────────────

def _chart_payload(price, prev=None, chart_prev=None, ts_closes=None):
    meta = {"regularMarketPrice": price}
    if prev is not None:
        meta["regularMarketPreviousClose"] = prev
    if chart_prev is not None:
        meta["chartPreviousClose"] = chart_prev
    result = {"meta": meta}
    if ts_closes:
        result["timestamp"] = [t for t, _ in ts_closes]
        result["indicators"] = {"quote": [{"close": [c for _, c in ts_closes]}]}
    return {"chart": {"result": [result], "error": None}}


def test_extract_detailed_price_prev_delta():
    from src.data_sources.market_prices import _extract_detailed

    d = _extract_detailed(_chart_payload(105.0, prev=100.0))
    assert d["price"] == 105.0 and d["previous_close"] == 100.0
    assert d["delta"] == 5.0 and d["change_pct"] == 5.0


def test_extract_detailed_chart_prev_fallback_and_absent():
    from src.data_sources.market_prices import _extract_detailed

    d = _extract_detailed(_chart_payload(50.0, chart_prev=40.0))
    assert d["previous_close"] == 40.0 and d["delta"] == 10.0
    # Pas de clôture précédente → delta omis (jamais inventé).
    d2 = _extract_detailed(_chart_payload(50.0))
    assert d2 == {"price": 50.0}
    assert _extract_detailed({"chart": {"error": "boom"}}) is None


def test_extract_dated_closes_skips_holes():
    from src.data_sources.market_prices import _extract_dated_closes

    t0 = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    day = 86400
    out = _extract_dated_closes(_chart_payload(
        1, ts_closes=[(t0, 10.0), (t0 + day, None), (t0 + 2 * day, 12.0)]
    ))
    assert out == {"2026-06-01": 10.0, "2026-06-03": 12.0}


def test_macro_quotes_and_deltas_share_fetch_and_convert_10y(monkeypatch):
    from src.data_sources import market_prices as mp

    calls = {"n": 0}

    def fake_get_json(url, headers=None, params=None, **kw):
        calls["n"] += 1
        if "%5ETNX" in url or "^TNX" in url:
            return _chart_payload(44.5, prev=44.0)
        return _chart_payload(200.0, prev=198.0)

    monkeypatch.setattr(mp, "get_json", fake_get_json)
    mp.CACHE._store.clear()
    quotes = mp.get_macro_quotes()
    deltas = mp.get_macro_deltas()
    # ^TNX coté ×10 : valeur ET delta convertis.
    assert quotes["us_10y"] == 4.45
    assert deltas["us_10y"] == pytest.approx(0.05)
    # International présent dans le fetch macro.
    assert "nikkei" in quotes and "stoxx50" in quotes and "dax" in quotes
    n_after_first = calls["n"]
    mp.get_macro_quotes()  # même cache : aucun appel supplémentaire
    assert calls["n"] == n_after_first


def test_get_equity_quotes_parses_all_tickers(monkeypatch):
    from src.data_sources import market_prices as mp

    monkeypatch.setattr(
        mp, "get_json",
        lambda url, headers=None, params=None, **kw: _chart_payload(130.0, prev=127.4),
    )
    mp.CACHE._store.clear()
    q = mp.get_equity_quotes()
    assert set(q) == {"NVDA", "AMD", "TSM", "COIN", "MSTR", "MARA"}
    assert q["NVDA"]["change_pct"] == pytest.approx(2.04, abs=0.01)


def test_macro_source_status_covers_intl():
    from src.data_sources.market_prices import compute_macro_source_status

    ctx = {"nikkei": 40000.0, "stoxx50": 5000.0, "ecb_deposit_rate": 2.0}
    status = compute_macro_source_status(
        ctx,
        yahoo_quotes={"nikkei": 40000.0, "stoxx50": 5000.0},
        # Forme réelle de fred.get_macro()["series"] : dict par clé.
        fred_raw={"nikkei": {"value": 39900.0},
                  "ecb_deposit_rate": {"value": 2.0}},
    )
    assert status.get("nikkei") == "confirmed"      # Yahoo × FRED concordants
    assert status.get("stoxx50") == "single"        # Yahoo seul
    assert status.get("ecb_deposit_rate") == "single"  # FRED seul


# ─────────────────────────────────────────────────────────────────────────────
# coinmetrics — miroir CSV GitHub
# ─────────────────────────────────────────────────────────────────────────────

_CSV_HEADER = "time,AdrActCnt,CapMVRVCur,PriceUSD,SplyCur\n2010-01-01,1,1,1,1\n"


def _csv_tail() -> str:
    # 1re ligne volontairement TRONQUÉE (artefact du Range), puis 9 lignes
    # valides, puis la ligne « sparse » typique du miroir (MVRV absent).
    rows = ["7,1.30,60000,19000000"]
    base = datetime(2026, 5, 15)
    for i in range(9):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append(f"{d},{500000 + i * 1000},1.4{i},7000{i},19800000")
    rows.append("2026-05-24,,,,")
    return "\n".join(rows)


def test_parse_mirror_csv_picks_last_valid_row():
    from src.data_sources.coinmetrics import _parse_mirror_csv

    row = _parse_mirror_csv(_CSV_HEADER, _csv_tail())
    assert row["time"] == "2026-05-23"          # la sparse 24/05 est ignorée
    assert row["CapMVRVCur"] == pytest.approx(1.48)
    assert row["PriceUSD"] == pytest.approx(70008.0)
    assert row["AdrActCnt"] == 508000
    # Tendance adresses ~7 lignes avant la dernière valide.
    assert row["AdrActCnt_prev"] == 501000


def test_entry_from_mirror_derives_realized_price_and_stale():
    from src.data_sources.coinmetrics import _entry_from_mirror

    old = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y-%m-%d")
    e = _entry_from_mirror({
        "time": old, "PriceUSD": 70000.0, "CapMVRVCur": 1.4,
        "AdrActCnt": 500000.0, "AdrActCnt_prev": 480000.0,
    })
    assert e["realized_price"] == pytest.approx(50000.0)  # prix / MVRV
    assert e["mvrv_zone"] and e["active_addresses_trend_pct"] == pytest.approx(4.2)
    assert e["stale"] is True and e["as_of"] == old
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert _entry_from_mirror({"time": fresh, "PriceUSD": 1.0,
                               "CapMVRVCur": 1.0})["stale"] is False


def test_onchain_metrics_falls_back_to_mirror(monkeypatch):
    from src.data_sources import coinmetrics as cm

    monkeypatch.setattr(cm, "get_json", lambda *a, **k: None)  # API morte
    monkeypatch.setattr(
        cm, "_fetch_mirror_asset",
        lambda cm_id: {"time": "2026-05-23", "PriceUSD": 70000.0,
                       "CapMVRVCur": 1.4, "AdrActCnt": 500000.0},
    )
    cm.CACHE._store.clear()
    out = cm.get_onchain_metrics()
    assert out["available"] is True and out["source"] == "coinmetrics-github"
    assert out["assets"]["BTC"]["mvrv"] == 1.4
    assert "nvt" not in out["assets"]["BTC"]  # absent du miroir : jamais inventé


# ─────────────────────────────────────────────────────────────────────────────
# token_unlocks — DefiLlama
# ─────────────────────────────────────────────────────────────────────────────

def test_unlocks_defillama_parsing_window_and_mapping(monkeypatch):
    from src.data_sources import token_unlocks as tu

    in_window = (datetime.now(timezone.utc) + timedelta(days=10)).timestamp()
    out_window = (datetime.now(timezone.utc) + timedelta(days=90)).timestamp()
    payload = [
        {"tSymbol": "RNDR", "tPrice": 5.0, "maxSupply": 1_000_000.0,
         "nextEvent": {"date": in_window, "toUnlock": 10_000.0}},
        {"gecko_id": "arbitrum", "tPrice": 1.0,
         "nextEvent": {"date": out_window, "toUnlock": 99.0}},   # hors fenêtre
        {"tSymbol": "DOGE", "nextEvent": {"date": in_window, "toUnlock": 1.0}},
        "garbage",
    ]
    monkeypatch.setattr(tu, "get_json", lambda *a, **k: payload)
    tu.CACHE._store.clear()
    out = tu.get_upcoming_unlocks(days_ahead=30)
    assert out["available"] is True and out["source"] == "DefiLlama"
    assert out["count"] == 1
    u = out["unlocks"][0]
    assert u["symbol"] == "RENDER"               # RNDR → RENDER
    assert u["amount_usd"] == 50000.0 and u["pct_supply"] == 1.0


def test_unlocks_graceful_on_unexpected_schema(monkeypatch):
    from src.data_sources import token_unlocks as tu

    monkeypatch.setattr(tu, "get_json", lambda *a, **k: {"weird": True})
    tu.CACHE._store.clear()
    out = tu.get_upcoming_unlocks()
    assert out == {"available": False, "unlocks": []}


# ─────────────────────────────────────────────────────────────────────────────
# Parsing nombres FR — _parse_num & fmt_money
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("69.637,63 $", 69637.63),   # format fmt_money du projet (bug v14 corrigé)
    ("1,679.33", 1679.33),       # format US
    ("63,180", 63180.0),         # milliers US
    ("63 180 $", 63180.0),
    ("0,0014", 0.0014),          # décimale FR
    ("1,5", 1.5),
    ("0.0014", 0.0014),
    ("63180", 63180.0),
    ("n/d", None),
    (float("nan"), None),
])
def test_parse_num_locale_aware(raw, expected):
    from src.main import _parse_num

    got = _parse_num(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_fmt_money_idempotent_on_french_output():
    from src.reporting.email_html import _fmt_money

    once = _fmt_money(69637.63)
    assert once == "69.637,63 $"
    assert _fmt_money(once) == once          # re-formater ne casse plus
    assert _fmt_money("69.637,63") == once


# ─────────────────────────────────────────────────────────────────────────────
# correlation — liens actions ↔ crypto
# ─────────────────────────────────────────────────────────────────────────────

def _series(start: float, rets: list[float]) -> dict[str, float]:
    out, v = {}, start
    d0 = datetime(2026, 4, 1)
    out[d0.strftime("%Y-%m-%d")] = v
    for i, r in enumerate(rets, 1):
        v *= (1 + r)
        out[(d0 + timedelta(days=i)).strftime("%Y-%m-%d")] = v
    return out


def test_equity_crypto_links_corr_beta_and_summary():
    from src.analytics.correlation import compute_equity_crypto_links

    eq_rets = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012,
               -0.018, 0.008, 0.01, -0.012, 0.006, 0.009, -0.007]
    cr_rets = [2 * r for r in eq_rets]          # crypto = 2× l'action
    out = compute_equity_crypto_links(
        {"NVDA": _series(100, eq_rets)},
        {"RENDER": _series(5, cr_rets)},
        window=30,
        pairs=[("NVDA", "RENDER", "demande GPU / calcul IA")],
    )
    assert out["available"] is True
    link = out["links"][0]
    assert link["corr"] == pytest.approx(1.0, abs=0.01)
    assert link["beta"] == pytest.approx(2.0, abs=0.05)
    assert link["significant"] is True
    assert "NVDA↔RENDER" in out["summary_line"]
    assert "GPU" in link["reading"]


def test_equity_crypto_links_insignificant_has_no_beta():
    from src.analytics.correlation import compute_equity_crypto_links

    eq = _series(100, [0.01, -0.01] * 8)
    cr = _series(5, [0.0, 0.0, 0.01, -0.01] * 4)   # quasi orthogonal
    out = compute_equity_crypto_links(
        {"COIN": eq}, {"BTC": cr}, pairs=[("COIN", "BTC", "bêta crypto coté")]
    )
    if out["available"]:
        for l in out["links"]:
            if not l["significant"]:
                assert "beta" not in l
                assert "non significatif" in l["reading"]


def test_equity_crypto_links_unavailable_when_no_data():
    from src.analytics.correlation import compute_equity_crypto_links

    assert compute_equity_crypto_links({}, {})["available"] is False
    assert compute_equity_crypto_links(
        {"NVDA": {"2026-01-01": 1.0}}, {"ZZZ": {"2026-01-01": 1.0}}
    )["available"] is False


# ─────────────────────────────────────────────────────────────────────────────
# digests — ligne actions↔crypto + MVRV daté
# ─────────────────────────────────────────────────────────────────────────────

def test_equity_crypto_digest_line():
    from src.analytics.digests import equity_crypto_line

    links = {"available": True, "links": [
        {"equity": "NVDA", "crypto": "RENDER", "corr": 0.62, "beta": 1.4,
         "significant": True},
        {"equity": "MSTR", "crypto": "BTC", "corr": 0.7, "significant": True},
        {"equity": "TSM", "crypto": "TAO", "corr": 0.1, "significant": False},
    ]}
    quotes = {"NVDA": {"change_pct": 2.1}, "MSTR": {"change_pct": -1.3}}
    line = equity_crypto_line(links, quotes)
    assert "NVDA↔RENDER +0.62 (β 1.4)" in line
    assert "MSTR↔BTC +0.70" in line and "TSM" not in line
    assert "NVDA +2.1%" in line and "MSTR -1.3%" in line
    assert equity_crypto_line({"available": False}, {}) == ""


def test_onchain_line_shows_mirror_date_when_stale():
    from src.analytics.digests import onchain_line

    cm = {"available": True, "assets": {"BTC": {
        "mvrv": 1.41, "mvrv_zone": "neutre", "as_of": "2026-05-23", "stale": True,
    }}}
    line = onchain_line(cm)
    assert "données au 23/05" in line and "pas temps réel" in line
    cm["assets"]["BTC"]["stale"] = False
    assert "données au" not in onchain_line(cm)


# ─────────────────────────────────────────────────────────────────────────────
# main — _macro_context international + priorité deltas live
# ─────────────────────────────────────────────────────────────────────────────

def _macro_with(series: dict) -> dict:
    # Forme réelle de fred.get_macro() : series = {name: {value, delta, date}}.
    return {"available": True, "series": dict(series)}


def test_macro_context_intl_keys_and_live_delta_priority():
    from src.main import _macro_context

    macro = _macro_with({
        "nikkei": {"value": 39900.0, "delta": -100.0, "date": "2026-06-09"},
        "ecb_deposit_rate": {"value": 2.0, "delta": 0.0, "date": "2026-06-09"},
        "boj_call_rate": {"value": 0.75, "delta": 0.0, "date": "2026-05-31"},
        "sp500": {"value": 6800.0, "delta": -12.0, "date": "2026-06-08"},
    })
    yq = {"nikkei": 40250.0, "stoxx50": 5100.0, "dax": 24100.0, "sp500": 6852.0}
    yd = {"nikkei": 150.0, "stoxx50": -20.0}     # pas de delta Yahoo pour sp500
    ctx = _macro_context({"BTC": {"price": 70000.0}}, {"available": False},
                         macro, {"available": False}, yq, yd)
    assert ctx["nikkei"] == 40250.0              # valeur Yahoo prioritaire
    assert ctx["nikkei_delta"] == 150.0          # delta LIVE prioritaire
    assert ctx["stoxx50"] == 5100.0 and ctx["stoxx50_delta"] == -20.0
    assert ctx["dax"] == 24100.0 and ctx["dax_delta"] is None
    assert ctx["ecb_deposit_rate"] == 2.0 and ctx["boj_rate"] == 0.75
    assert ctx["sp500_delta"] == -12.0           # fallback FRED si delta absent


def test_macro_context_intl_range_guard():
    from src.main import _macro_context

    ctx = _macro_context({}, {"available": False}, _macro_with({}),
                         {"available": False},
                         {"nikkei": 999999.0}, {})  # hors plage plausible
    assert ctx["nikkei"] is None                 # aberration masquée, pas affichée


def test_active_sources_catalogue_includes_intl():
    from src.main import _ALL_SOURCES_LIST, _active_sources

    assert len(_ALL_SOURCES_LIST) == 25
    out = _active_sources(intl_markets=True, equity_links=True)
    assert "Marchés internationaux (BCE · BoJ · Nikkei · Stoxx)" in out
    assert "Actions ↔ crypto (NVDA · COIN · MSTR…)" in out
    assert _active_sources(intl_markets=False, equity_links=False) == []


def test_sources_yaml_has_intl_fred_series():
    from src.utils.portfolio_loader import load_config

    fred = load_config("sources").get("fred_series") or {}
    assert fred.get("ecb_deposit_rate") == "ECBDFR"
    assert fred.get("nikkei") == "NIKKEI225"
    assert "boj_call_rate" in fred


# ─────────────────────────────────────────────────────────────────────────────
# Rendu — template matin avec international + actions
# ─────────────────────────────────────────────────────────────────────────────

def _rich_morning_payload() -> dict:
    return {
        "header": {"date": "2026-06-10", "time_casablanca": "08:30"},
        "story_of_the_day": {"narrative": "Séance mondiale contrastée."},
        "macro_context": {
            "btc_price": 70123.45, "fear_greed": 41, "fear_greed_label": "Peur",
            "dxy": 99.1, "dxy_delta": 0.2,
            "sp500": 6852.0, "sp500_delta": 19.9,
            "nasdaq": 22500.0, "nasdaq_delta": -30.0, "vix": 14.2, "vix_delta": 0.1,
            "us_10y": 4.21, "us_10y_delta": 0.02,
            "gold_usd": 2400.0, "gold_delta": 24.0,
            "brent_usd": 78.0, "brent_delta": -0.2, "usd_jpy": 151.2,
            "usd_jpy_delta": 0.4, "eur_usd": 1.09,
            "nikkei": 40250.0, "nikkei_delta": 150.0,
            "stoxx50": 5100.0, "stoxx50_delta": -20.0,
            "dax": 24100.0, "dax_delta": 80.0,
            "ecb_deposit_rate": 2.0, "boj_rate": 0.75,
        },
        "equity_quotes": {"NVDA": {"price": 131.5, "delta": 2.7,
                                   "change_pct": 2.1}},
        "equity_crypto_links": {
            "available": True,
            "summary_line": "NVDA↔RENDER corr 30j +0.62 · β 1.40 — demande GPU / calcul IA",
        },
        "footer": {"next_report_at": "ce soir 20h00"},
    }


def test_render_morning_intl_group_and_equity_cell():
    html = render(_rich_morning_payload(), "morning")
    for needle in ("International · Europe", "Euro Stoxx 50", "Nikkei 225",
                   "BCE · dépôt", "BoJ · taux", "NVDA · IA/GPU",
                   "Actions ↔ crypto", "NVDA↔RENDER corr 30j +0.62"):
        assert needle in html, f"manque : {needle}"
    # Mail-safe : aucune grille/flex dans le rendu.
    assert "display:grid" not in html and "display:flex" not in html
    # Sous-label or : delta absolu converti en VRAI % (24/2400 = +1.0%).
    assert "+1.0%" in html
    assert "None" not in html and "NaN" not in html


def test_render_morning_without_intl_hides_group():
    payload = _rich_morning_payload()
    for k in ("nikkei", "stoxx50", "dax", "ecb_deposit_rate", "boj_rate"):
        payload["macro_context"].pop(k, None)
    payload.pop("equity_quotes")
    payload.pop("equity_crypto_links")
    html = render(payload, "morning")
    assert "International · Europe" not in html
    assert "NVDA" not in html and "Actions ↔ crypto" not in html


def test_render_morning_size_budget_with_new_blocks():
    html = render(_rich_morning_payload(), "morning")
    assert len(html.encode("utf-8")) < 60_000   # budget anti-troncature Gmail
