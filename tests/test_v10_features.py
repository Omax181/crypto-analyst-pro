"""Tests des améliorations V10 (corrélations macro, on-chain avancé, options,
technique détaillée, feedback loop, digests, chaînage 2 passes).

Compatibles pytest (CI) ET runner offline (fixture ``monkeypatch`` minimale).
Toutes les sources réseau sont moquées : aucun appel réel.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timedelta, timezone


# --------------------------------------------------------------------------- #
# AMÉLIORATION 5 — Deribit options (parsing + max pain + put/call)
# --------------------------------------------------------------------------- #
def test_deribit_parse_instrument() -> None:
    from src.data_sources import deribit

    assert deribit._parse_instrument("BTC-27JUN25-65000-C")[1] == 65000.0
    assert deribit._parse_instrument("BTC-27JUN25-65000-C")[2] == "C"
    assert deribit._parse_instrument("ETH-27JUN25-3000-P")[2] == "P"
    assert deribit._parse_instrument("BTC-PERPETUAL") is None
    assert deribit._parse_instrument("garbage") is None


def test_deribit_max_pain_balanced() -> None:
    from src.data_sources import deribit

    # Calls lourds en bas, puts lourds en haut -> max pain au milieu.
    opts = [
        (60000, "C", 100), (70000, "C", 10),
        (60000, "P", 10), (70000, "P", 100),
        (65000, "C", 5), (65000, "P", 5),
    ]
    assert deribit._max_pain(opts) == 65000
    assert deribit._max_pain([(1, "C", 1)]) is None  # trop peu de strikes


def test_deribit_options_metrics_parsing(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from src.data_sources import deribit

    # Échéance DYNAMIQUEMENT future (~13 mois) : le code ne retient que les
    # options expirant après `now` (deribit.py:114). Une date en dur finit par
    # expirer et casser le test (cas vécu : « 27JUN26 » devenu le jour même).
    _fut = datetime.now(timezone.utc) + timedelta(days=400)
    _M = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    exp = f"{_fut.day}{_M[_fut.month - 1]}{_fut.year % 100:02d}"

    def fake_get_json(url, **k):
        if "book_summary" in url:
            return {"result": [
                {"instrument_name": f"BTC-{exp}-60000-C", "open_interest": 100, "underlying_price": 65000},
                {"instrument_name": f"BTC-{exp}-70000-P", "open_interest": 120, "underlying_price": 65000},
                {"instrument_name": f"BTC-{exp}-65000-C", "open_interest": 50, "underlying_price": 65000},
            ]}
        if "volatility_index" in url:
            return {"result": {"data": [[1, 40, 42, 39, 41.5]]}}
        return None

    monkeypatch.setattr(deribit, "get_json", fake_get_json)
    monkeypatch.setattr(deribit.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    r = deribit.get_options_metrics()
    assert r["available"] is True
    btc = r["assets"]["BTC"]
    assert btc["put_call_ratio"] == round(120 / 150, 2)
    assert btc["dvol"] == 41.5
    assert btc["max_pain"] == 65000


def test_deribit_degrades_on_no_data(monkeypatch) -> None:
    from src.data_sources import deribit

    monkeypatch.setattr(deribit, "get_json", lambda url, **k: None)
    monkeypatch.setattr(deribit.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    assert deribit.get_options_metrics()["available"] is False


# --------------------------------------------------------------------------- #
# AMÉLIORATION 4 — Coin Metrics on-chain avancé
# --------------------------------------------------------------------------- #
def test_coinmetrics_mvrv_zone_and_floats() -> None:
    from src.data_sources import coinmetrics

    assert coinmetrics._mvrv_zone(0.8).startswith("sous")
    assert coinmetrics._mvrv_zone(1.5) == "neutre"
    assert coinmetrics._mvrv_zone(2.5) == "élevé"
    assert coinmetrics._mvrv_zone(4.0) == "surchauffe"
    assert coinmetrics._mvrv_zone(None) is None
    assert coinmetrics._to_float("3.14") == 3.14
    assert coinmetrics._to_float("x") is None and coinmetrics._to_float(None) is None


def test_coinmetrics_parsing(monkeypatch) -> None:
    from src.data_sources import coinmetrics

    rows = [
        {
            "asset": "btc", "time": f"2026-05-{10 + i:02d}T00:00:00Z",
            "PriceUSD": "100000", "CapMVRVCur": "2.1", "NVTAdj": "45",
            "CapRealUSD": "1000000000000", "SplyCur": "19000000",
            "AdrActCnt": str(900000 + i * 1000),
        }
        for i in range(10)
    ]
    monkeypatch.setattr(coinmetrics, "get_json", lambda *a, **k: {"data": rows})
    monkeypatch.setattr(coinmetrics.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    # v28 — les lignes synthétiques sont datées (mai) → stale=True : on
    # neutralise la surcouche bitcoin-data.com (appel réseau) qui pourrait
    # sinon écraser le MVRV BTC et rendre le test non déterministe.
    from src.data_sources import bitcoin_data
    monkeypatch.setattr(bitcoin_data, "get_btc_mvrv", lambda: {"available": False})
    r = coinmetrics.get_onchain_metrics()
    assert r["available"] is True
    btc = r["assets"]["BTC"]
    assert btc["mvrv"] == 2.1 and btc["mvrv_zone"] == "élevé"
    assert btc["nvt"] == 45.0
    assert btc["realized_price"] == round(1000000000000 / 19000000, 2)
    assert "active_addresses_trend_pct" in btc


# --------------------------------------------------------------------------- #
# AMÉLIORATION 1 — Corrélations macro ↔ crypto
# --------------------------------------------------------------------------- #
def test_macro_crypto_correlation_signs() -> None:
    import random

    from src.analytics.correlation import compute_macro_crypto_correlation

    random.seed(7)
    btc, dxy, spx = {}, {}, {}
    base = datetime(2026, 5, 1, tzinfo=timezone.utc).date()
    b, dx, sp = 100000.0, 104.0, 5000.0
    for i in range(35):
        d = (base + timedelta(days=i)).isoformat()
        shock = random.gauss(0, 0.02)
        b *= 1 + shock + random.gauss(0, 0.004)
        dx *= 1 - 0.5 * shock + random.gauss(0, 0.002)   # inverse
        sp *= 1 + 0.8 * shock + random.gauss(0, 0.003)   # même sens
        btc[d], dxy[d], spx[d] = b, dx, sp
    r = compute_macro_crypto_correlation(btc, {"dxy": dxy, "sp500": spx}, window=30)
    assert r["available"] is True
    cd = {c["key"]: c["corr"] for c in r["correlations"]}
    assert cd["dxy"] < -0.3 and cd["sp500"] > 0.3


def test_macro_crypto_correlation_degrades() -> None:
    from src.analytics.correlation import compute_macro_crypto_correlation

    assert compute_macro_crypto_correlation({}, {"dxy": {"2026-05-01": 1.0}})["available"] is False
    assert compute_macro_crypto_correlation({"2026-05-01": 1.0}, {})["available"] is False


# --------------------------------------------------------------------------- #
# AMÉLIORATION 3 — Technique détaillée (SMA / cross / flash)
# --------------------------------------------------------------------------- #
def test_technical_advanced_golden_death_cross(monkeypatch) -> None:
    from src.data_sources import technical_advanced as ta

    up = [{"open": 100 + i, "high": 105 + i, "low": 95 + i, "close": 100 + i} for i in range(210)]
    monkeypatch.setattr(ta.coingecko, "get_ohlc", lambda s, days=90: up)
    monkeypatch.setattr(ta.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    r = ta.get_technical_advanced("BTC")
    assert r["moving_averages"]["cross"] == "golden"
    assert r["moving_averages"]["price_vs_sma200_pct"] > 0
    assert any("golden cross" in f for f in r["flash_signals"])

    down = [{"open": 300 - i, "high": 305 - i, "low": 295 - i, "close": 300 - i} for i in range(210)]
    monkeypatch.setattr(ta.coingecko, "get_ohlc", lambda s, days=90: down)
    r2 = ta.get_technical_advanced("BTC")
    assert r2["moving_averages"]["cross"] == "death"


def test_technical_advanced_short_history(monkeypatch) -> None:
    from src.data_sources import technical_advanced as ta

    short = [{"open": 100, "high": 105, "low": 95, "close": 100 + i} for i in range(40)]
    monkeypatch.setattr(ta.coingecko, "get_ohlc", lambda s, days=90: short)
    monkeypatch.setattr(ta.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    r = ta.get_technical_advanced("BTC")
    assert r["moving_averages"]["sma200"] is None  # pas assez d'historique, pas de crash


# --------------------------------------------------------------------------- #
# AMÉLIORATION 7 — Feedback loop (perf par actif)
# --------------------------------------------------------------------------- #
def test_per_asset_performance(monkeypatch) -> None:
    from src.state import report_memory as mem
    from src.tracking.prediction_scoring import PredictionTracker

    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    now = datetime.now(timezone.utc)

    def iso(d):
        return (now - timedelta(days=d)).isoformat()

    mem.save_prediction_history([
        {"asset": "SOL", "action": "RENFORCER", "status": "invalidated", "created_at": iso(5)},
        {"asset": "SOL", "action": "RENFORCER", "status": "invalidated", "created_at": iso(20)},
        {"asset": "SOL", "action": "RENFORCER", "status": "validated", "created_at": iso(40)},
        {"asset": "BTC", "action": "RENFORCER", "status": "validated", "created_at": iso(10)},
        {"asset": "BTC", "action": "RENFORCER", "status": "validated", "created_at": iso(30)},
    ])
    r = PredictionTracker().compute_per_asset_performance(90)
    assert r["by_asset"]["SOL"]["win_rate_pct"] == 33
    assert r["by_asset"]["BTC"]["win_rate_pct"] == 100
    assert "SOL" in r["caution_assets"] and "BTC" not in r["caution_assets"]
    assert r["recent_errors"][0]["asset"] == "SOL"


# --------------------------------------------------------------------------- #
# Digests compacts (économie de tokens)
# --------------------------------------------------------------------------- #
def test_digests_compact() -> None:
    from src.analytics import digests as D

    tv = {"rsi": 72.3, "macd_hist": 0.001, "stoch_k": 85.0, "adx": 28.0}
    ta = {
        "available": True,
        "bollinger": {"available": True, "position": "upper"},
        "support_resistance": {"available": True, "dist_to_support_pct": 8.0, "dist_to_resistance_pct": 2.0},
        "moving_averages": {"cross": "golden", "price_vs_sma200_pct": 12.5},
        "flash_signals": ["🟢 golden cross"],
    }
    t = D.build_asset_technical(tv, ta)
    assert t["rsi"] == 72.3 and t["cross"] == "golden" and "surachat" in t["summary"]
    assert D.build_asset_technical({}, {"available": False})["summary"]

    cm = {"available": True, "assets": {"BTC": {"mvrv": 2.1, "mvrv_zone": "élevé", "realized_price_ratio": 1.9}}}
    assert "MVRV 2.1" in D.onchain_line(cm)
    opt = {"available": True, "assets": {"BTC": {"put_call_ratio": 1.2, "max_pain": 65000, "dvol": 42.0}}}
    assert "put/call 1.2" in D.options_line(opt) and "DVOL 42" in D.options_line(opt)
    for fn in (D.onchain_line, D.options_line, D.macro_correlation_line, D.feedback_line):
        assert fn({"available": False}) == ""


# --------------------------------------------------------------------------- #
# AMÉLIORATION 6 — Chaînage 2 passes (decision engine)
# --------------------------------------------------------------------------- #
class _FakeClient:
    """Faux client Gemini : enregistre les prompts et renvoie des réponses scriptées."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt, **k):
        self.prompts.append(prompt)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _engine_with(client):
    from src.ai_brain.decision_engine import DecisionEngine

    return DecisionEngine(client=client)


def _morning_kwargs():
    data = {
        "macro_context": {"dxy": 104.0},
        "analytics_digest": {"macro_correlations": "BTC ↔ DXY -0.4", "macro_calendar": "CPI 3.1"},
        "fear_greed": {"value": 40},
        "all_positions_summary": [],
        "win_rate": {},
    }
    return dict(timestamp="04/06 08:00", data=data, portfolio_data={"portfolio": {}}, evening_state={})


def test_morning_two_pass_calls_twice() -> None:
    regime = {"regime": "risk-off", "confidence_pct": 70}
    report = {"executive_summary": "ok", "thesis_of_the_day": []}
    client = _FakeClient([regime, report])
    out = _engine_with(client).generate_morning(**_morning_kwargs())
    # Deux appels : passe 1 (régime) + passe 2 (rapport).
    assert len(client.prompts) == 2
    assert "PASSE 1" in client.prompts[0]
    # Le régime de la passe 1 est injecté dans le prompt de la passe 2.
    assert "risk-off" in client.prompts[1]
    assert out["executive_summary"] == "ok" and not out.get("_degraded")


def test_morning_pass1_fails_pass2_runs() -> None:
    report = {"executive_summary": "ok"}
    client = _FakeClient([RuntimeError("pass1 boom"), report])
    out = _engine_with(client).generate_morning(**_morning_kwargs())
    assert len(client.prompts) == 2  # passe 1 a échoué mais passe 2 a tourné
    assert out["executive_summary"] == "ok" and not out.get("_degraded")


def test_morning_retry_then_degrade() -> None:
    # Passe 1 vide ({}), puis passe 2 renvoie {} deux fois -> dégradé après retries.
    client = _FakeClient([{}, {}, {}])
    out = _engine_with(client).generate_morning(**_morning_kwargs())
    # 1 (passe 1) + 2 tentatives (passe 2) = 3 appels.
    assert len(client.prompts) == 3
    assert out.get("_degraded") is True


def test_morning_quota_degrades_immediately() -> None:
    from src.ai_brain.gemini_client import GeminiQuotaError

    # Passe 1 vide, passe 2 quota -> dégradé sans retry de la passe 2.
    client = _FakeClient([{}, GeminiQuotaError("quota")])
    out = _engine_with(client).generate_morning(**_morning_kwargs())
    assert out.get("_degraded") is True
    assert "Quota" in out["footer"]["note"]


def test_morning_no_client_degrades() -> None:
    eng = _engine_with(None)
    eng.client = None  # simule un init échoué
    eng._init_error = "clé absente"
    out = eng.generate_morning(**_morning_kwargs())
    assert out.get("_degraded") is True
    # Aucun appel n'a été tenté (pas de client).
