# -*- coding: utf-8 -*-
"""Tests v26 — audit du mail EVENING v25 (parties A et B).

Couvre : résilience IA (E-A2/E-A13/E-B1), mode dégradé digne (E-A1/E-B2),
deltas matin→soir (E-B10), pertinence des news (E-A3/E-A5/E-B3), horodatage
(E-A4/E-B4), niveaux calculés + readout (E-B5), checklist temporelle
(E-A6/E-B6), formats/tuiles du rendu (E-A7→A12, E-A14, E-B7→B12, E-B14).
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

_BASE = pathlib.Path(__file__).resolve().parent.parent
_TPL = _BASE / "src" / "reporting" / "templates"


def _render(payload, kind="evening"):
    from src.reporting.email_html import render
    return render(payload, kind)


# --------------------------------------------------------------------------- #
# E-A2 / E-B1 — résilience IA : repli de modèle + ultime tentative différée
# --------------------------------------------------------------------------- #
def test_fallback_model_default_and_env(monkeypatch):
    """E-B1a : repli gemini-2.5-flash par défaut ; env override ; vide = off."""
    from src.ai_brain import decision_engine as de

    monkeypatch.delenv("GEMINI_FALLBACK_MODEL", raising=False)
    assert de._fallback_model_from_env() == "gemini-2.5-flash"
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-x")
    assert de._fallback_model_from_env() == "gemini-x"
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "")
    assert de._fallback_model_from_env() is None


def test_engine_wires_fallback_model():
    """E-A2 : DecisionEngine passe ENFIN fallback_model à GeminiClient (la
    capacité existait mais n'était jamais câblée — 503 = mail coquille)."""
    from src.ai_brain.decision_engine import DecisionEngine

    src = inspect.getsource(DecisionEngine.__init__)
    assert "fallback_model=_fallback_model_from_env()" in src


def test_last_chance_pause_then_degrade(monkeypatch):
    """E-B1c : après épuisement des tentatives, pause longue + ultime essai."""
    from src.ai_brain.decision_engine import DecisionEngine

    class _Boom:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **k):
            self.calls += 1
            raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setenv("GEMINI_LAST_CHANCE_PAUSE_S", "7")
    client = _Boom()
    eng = DecisionEngine(client=client)
    sleeps: list[float] = []
    eng._sleep = sleeps.append
    out = eng._safe_json("p", {}, kind="evening")
    assert out.get("_degraded") is True
    assert sleeps == [7], "la pause configurée doit être respectée"
    assert client.calls == 3  # 2 tentatives + 1 ultime


def test_last_chance_pause_can_succeed(monkeypatch):
    """E-B1c : un 503 transitoire résolu pendant la pause → mail COMPLET."""
    from src.ai_brain.decision_engine import DecisionEngine

    class _FailTwiceThenOk:
        def __init__(self):
            self.n = 0

        def generate_json(self, prompt, **k):
            self.n += 1
            if self.n <= 2:
                raise RuntimeError("503 UNAVAILABLE")
            return {"header": {"date": "ok"}}

    monkeypatch.setenv("GEMINI_LAST_CHANCE_PAUSE_S", "5")
    eng = DecisionEngine(client=_FailTwiceThenOk())
    eng._sleep = lambda s: None
    out = eng._safe_json("p", {}, kind="evening")
    assert not out.get("_degraded")
    assert out["header"]["date"] == "ok"


def test_pause_disabled_in_tests_and_parsing(monkeypatch):
    """La pause est neutralisée par conftest (0) et le parse est robuste."""
    import os

    from src.ai_brain import decision_engine as de

    assert os.environ.get("GEMINI_LAST_CHANCE_PAUSE_S") == "0"
    assert de._last_chance_pause_s() == 0
    monkeypatch.setenv("GEMINI_LAST_CHANCE_PAUSE_S", "garbage")
    assert de._last_chance_pause_s() == de._LAST_CHANCE_PAUSE_DEFAULT_S


def test_degraded_evening_emits_delta_summary():
    """E-A13 : la clé morte delta_of_the_day est remplacée par delta_summary
    ({icon, text}) — l'avertissement du mode secours s'affiche enfin."""
    from src.ai_brain.decision_engine import DecisionEngine

    out = DecisionEngine._degraded("evening", {}, "Génération IA indisponible.")
    assert "delta_of_the_day" not in out
    ds = out["delta_summary"]
    assert ds and ds[0]["icon"] == "⚠" and "indisponible" in ds[0]["text"]


def test_gemini_retry_window_widened():
    """E-B1b : 5 essais (waits jusqu'à 30 s) au lieu de 4 (max 16 s)."""
    from src.ai_brain import gemini_client as gc

    src = inspect.getsource(gc)
    assert "stop_after_attempt(5)" in src
    assert "max=30" in src


# --------------------------------------------------------------------------- #
# E-A3 / E-A5 / E-B3 / E-B4 — pertinence news + nettoyage + horodatage
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,expected", [
    # Direct crypto → gardé
    ("Standard Chartered Becomes First Global Bank to Offer Direct USDC "
     "Access to Institutions", True),
    ("Ondo tokenizes BlackRock's IVV ETF and Micron stock", True),
    ("Metaplanet buys 2,823 BTC", True),
    ("SEC sues major exchange over unregistered securities", True),
    # Indirect macro → gardé
    ("Fed holds rates steady as inflation cools", True),
    ("US jobs report comes in below expectations, dollar slides", True),
    ("Gold price hits record as safe haven demand spikes", True),
    ("Will the U.S. invade Iran before 2027", True),
    # Bruit tradfi / hors-sujet → écarté (les 3 vus dans le mail v25)
    ("Franklin U.S. Treasury Bond ETF declares monthly distribution of "
     "$$0.07308", False),
    ("Franklin U.S. Core Bond ETF declares monthly distribution of "
     "$$0.07642", False),
    ("Dynatrace plans FedRAMP high authorization to advance federal "
     "security needs", False),
    ("La France gagne la coupe du monde", False),
    ("Taylor Swift announces new album", False),
    ("Netflix beats earnings estimates", False),
    ("Apple unveils new iPhone", False),
])
def test_news_relevance(title, expected):
    from src.data_sources.news_relevance import is_crypto_relevant

    assert is_crypto_relevant(title) is expected


def test_sanitize_title_double_dollar():
    """E-A5 : « $$0.07308 » → « $0.07308 », espaces multiples réduits."""
    from src.data_sources.news_relevance import sanitize_title

    assert sanitize_title("declares $$0.07308  extra ") == "declares $0.07308 extra"
    assert sanitize_title(None) == ""


def test_fmt_time_local():
    """E-A4/E-B4 : ISO → « 14h58 » heure Casablanca ; garbage → None."""
    from src.data_sources.news_relevance import fmt_time_local
    from src.main import TZ

    assert fmt_time_local("2026-07-02T13:58:09+00:00", TZ) == "14h58"
    assert fmt_time_local("2026-07-02T13:58:09Z", TZ) == "14h58"
    assert fmt_time_local("garbage", TZ) is None
    assert fmt_time_local(None, TZ) is None


def test_run_evening_filters_and_sanitizes_news():
    """E-B3 : le filtre + la sanitation sont câblés dans run_evening, et le
    repli intraday utilise l'horodatage local (E-B4)."""
    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "news_relevance.is_crypto_relevant" in src
    assert "news_relevance.sanitize_title" in src
    assert "news_relevance.fmt_time_local" in src


# --------------------------------------------------------------------------- #
# E-B5 — niveaux calculés + readout technique exhaustif
# --------------------------------------------------------------------------- #
def _btc_series(n=200):
    import math
    return [58000 + 2500 * math.sin(i / 9) + i * 12 for i in range(n)]


def test_key_levels_invariants():
    from src.analytics.key_levels import compute_key_levels

    out = compute_key_levels("BTC", _btc_series(), price=61949.0)
    assert out["available"] and out["symbol"] == "BTC"
    px = out["price"]
    assert out["supports"] and out["resistances"]
    assert all(s["level"] < px for s in out["supports"])
    assert all(r["level"] > px for r in out["resistances"])
    # triés du plus proche au plus loin
    assert out["supports"] == sorted(out["supports"], key=lambda s: -s["level"])
    assert out["resistances"] == sorted(out["resistances"], key=lambda r: r["level"])
    assert len(out["supports"]) <= 3 and len(out["resistances"]) <= 3
    # chaque niveau est ANCRÉ (base nommée) — fini les ronds sans justification
    for lv in out["supports"] + out["resistances"]:
        assert lv["basis"]
        assert isinstance(lv["dist_pct"], float)


def test_key_levels_readout_complete():
    """B5 : analyse profonde — RSI, MACD, Bollinger, ATR, MM, tendance, volume."""
    from src.analytics.key_levels import compute_key_levels

    vols = [4e9 + 1e9 * (i % 7) for i in range(200)]
    out = compute_key_levels("BTC", _btc_series(), vols)
    ro = out["readout"]
    for key in ("rsi", "rsi_zone", "macd_state", "boll_position",
                "boll_width_pct", "atr_pct", "atr_abs", "ma50_rel_pct",
                "ma200_rel_pct", "trend_7d_pct", "volume_trend_pct"):
        assert key in ro, f"readout incomplet : {key} manquant"
    line = out["readout_line"]
    assert "RSI" in line and "MACD" in line and "ATR" in line
    assert out["expected_range"]["low"] < out["price"] < out["expected_range"]["high"]


def test_key_levels_short_series_unavailable():
    from src.analytics.key_levels import compute_key_levels

    out = compute_key_levels("X", [1.0] * 10)
    assert out["available"] is False


def test_levels_tonight_rows_triggers():
    """B5 : lignes prêtes-template avec triggers chaînés déterministes."""
    from src.analytics.key_levels import compute_key_levels, levels_tonight_rows

    rows = levels_tonight_rows(compute_key_levels("BTC", _btc_series()))
    assert rows and len(rows) <= 4
    types = {r["type"] for r in rows}
    assert types <= {"support", "resistance"} and len(types) == 2
    for r in rows:
        assert r["asset"] == "BTC" and r["level"].endswith("$")
        assert r["trigger"].startswith(("Sous", "Au-dessus"))
    assert levels_tonight_rows({"available": False}) == []


def test_run_evening_computes_levels_and_prompt_rule():
    """B5 : run_evening calcule les niveaux (BTC/ETH + movers) et le prompt
    les impose comme source de vérité anti-hallucination."""
    import src.main as main
    from src.ai_brain.prompts import evening_prompt as ep

    src = inspect.getsource(main.run_evening)
    assert "compute_key_levels" in src and "computed_levels" in src
    psrc = inspect.getsource(ep)
    assert "computed_levels" in psrc
    assert "SOURCE DE VÉRITÉ" in psrc
    prompt = ep.build_evening_prompt(
        timestamp="t", data={"computed_levels": {"BTC": {"price": "61 949 $"}}},
        morning_state={})
    assert "computed_levels" in prompt


# --------------------------------------------------------------------------- #
# E-A6 / E-B6 — checklist « demain matin » : temporalité honnête
# --------------------------------------------------------------------------- #
def _cal():
    return {"available": True, "events": [
        {"label": "NFP (US)", "days_ahead": 0, "time": "13:30", "when": "aujourd'hui"},
        {"label": "Discours Fed (US)", "days_ahead": 0, "time": "19:00",
         "when": "aujourd'hui"},
        {"label": "Sans heure (US)", "days_ahead": 0, "when": "aujourd'hui"},
        {"label": "ISM Services (US)", "days_ahead": 1, "time": "15:00",
         "when": "demain"},
        {"label": "FOMC (US)", "days_ahead": 5, "when": "dans 5j"},
    ]}


def test_split_calendar_excludes_passed_today_events():
    from datetime import datetime

    from src.main import TZ, _split_evening_calendar

    now = datetime(2026, 7, 2, 15, 3, tzinfo=TZ)
    tomorrow, upcoming = _split_evening_calendar(_cal(), now)
    labels = [e["label"] for e in tomorrow]
    assert "NFP (US)" not in labels          # 13h30 déjà tombé à 15h03
    assert "Discours Fed (US)" in labels      # 19h00 encore à venir
    assert "Sans heure (US)" in labels        # sans heure → gardé (prudence)
    assert "ISM Services (US)" in labels
    assert "FOMC (US)" not in labels          # > 2j → weekly
    assert len(upcoming) == 5                 # la fenêtre 7j reste complète


def test_split_calendar_relabels_remaining_today():
    from datetime import datetime

    from src.main import TZ, _split_evening_calendar

    aft = _split_evening_calendar(
        _cal(), datetime(2026, 7, 2, 15, 3, tzinfo=TZ))[0]
    fed = next(e for e in aft if e["label"] == "Discours Fed (US)")
    assert fed["when"] == "encore aujourd'hui" and fed["time"] == "19:00"
    eve = _split_evening_calendar(
        _cal(), datetime(2026, 7, 2, 18, 30, tzinfo=TZ))[0]
    fed2 = next(e for e in eve if e["label"] == "Discours Fed (US)")
    assert fed2["when"] == "ce soir"


def test_split_calendar_unavailable():
    from datetime import datetime

    from src.main import TZ, _split_evening_calendar

    now = datetime(2026, 7, 2, 15, 3, tzinfo=TZ)
    assert _split_evening_calendar({}, now) == ([], [])
    assert _split_evening_calendar({"available": False}, now) == ([], [])


def test_evening_template_shows_event_time():
    txt = (_TPL / "report_evening.html.j2").read_text(encoding="utf-8")
    assert "{% if e.time %}" in txt


# --------------------------------------------------------------------------- #
# E-B10 — deltas matin→soir déterministes
# --------------------------------------------------------------------------- #
def _sm_inputs():
    morning_state = {"macro_context": {
        "btc_price": 61207.0, "eth_price": 1614.8, "fear_greed": 19,
        "dxy": 101.04,
        "polymarket_fed_bars": {"dominant": "maintien", "dominant_pct": 86.5}}}
    evening_macro = {"btc_price": 61949.0, "eth_price": 1645.0,
                     "fear_greed": 19, "dxy": 100.70}
    polymarket = {"fed_bars": {"dominant": "maintien", "dominant_pct": 88.5}}
    return morning_state, evening_macro, polymarket


def test_since_morning_facts_full():
    from src.main import _build_since_morning_facts

    ms, em, pm = _sm_inputs()
    out = _build_since_morning_facts(ms, True, em, pm, "12h37")
    assert out and out["available"]
    line = out["line"]
    assert "BTC" in line and "61 207" in line.replace(" ", " ")
    assert "F&G 19 → 19 (stable)" in line
    assert "DXY 101,04 → 100,70" in line
    assert "Fed maintien 86,5% → 88,5%" in line and "+2,0 pts" in line
    assert "ETH" in line


def test_since_morning_facts_guards():
    """Baseline périmée ou absente → AUCUN faux delta (None)."""
    from src.main import _build_since_morning_facts

    ms, em, pm = _sm_inputs()
    assert _build_since_morning_facts(ms, False, em, pm, None) is None
    assert _build_since_morning_facts({}, True, em, pm, None) is None
    assert _build_since_morning_facts(
        {"macro_context": {}}, True, {}, {}, None) is None


def test_macro_context_carries_eth_price():
    """E-B10 : le state matin porte désormais eth_price (baseline du delta)."""
    import src.main as main

    assert '"eth_price"' in inspect.getsource(main._macro_context)


def test_evening_template_renders_since_morning_line():
    txt = (_TPL / "report_evening.html.j2").read_text(encoding="utf-8")
    assert "since_morning_facts" in txt and "Depuis le matin" in txt


# --------------------------------------------------------------------------- #
# E-B9 / E-A14 — ligne « Dérivés & flux » (funding, L/S, ETF datés)
# --------------------------------------------------------------------------- #
def test_derivatives_line_full():
    from src.main import _build_evening_derivatives_line

    line = _build_evening_derivatives_line(
        {"available": True, "funding_rate_pct": 0.01, "long_short_ratio": 1.42},
        {"available": True,
         "btc": {"date": "2026-07-01", "total_flow_musd": -325.8},
         "eth": {"date": "2026-07-01", "total_flow_musd": 14.8}})
    assert "Funding BTC +0,01%" in line
    assert "L/S 1,42" in line
    assert "ETF BTC −325,8 M$ (01/07)" in line
    assert "ETF ETH +14,8 M$ (01/07)" in line


def test_derivatives_line_empty():
    from src.main import _build_evening_derivatives_line

    assert _build_evening_derivatives_line(None, None) is None
    assert _build_evening_derivatives_line(
        {"available": False}, {"available": False}) is None


def test_run_evening_merges_etf_telegram_and_funding():
    """E-A14 : le soir complète l'ETF via Telegram (comme le matin) et collecte
    le funding BTC (E-B9) — plus d'appel réseau à moitié perdu."""
    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "etf_flows.merge_with_telegram" in src
    assert 'binance_futures.get_derivatives("BTC")' in src
    assert "derivatives_flows_line" in src


def test_evening_template_renders_derivatives_line():
    txt = (_TPL / "report_evening.html.j2").read_text(encoding="utf-8")
    assert "derivatives_flows_line" in txt


# --------------------------------------------------------------------------- #
# E-A1 / E-B2 — mode dégradé digne (sections reconstruites en Python)
# --------------------------------------------------------------------------- #
def _degraded_env():
    from src.analytics.key_levels import compute_key_levels

    daily_pnl = {"value_usd": 2692.0, "day_change_usd": 57.0,
                 "day_change_pct": 2.15,
                 "top_movers": [{"symbol": "TAO", "change": 9.1,
                                 "pnl_usd": 32.0}]}
    evening_macro = {"btc_price": 61949.0, "fear_greed": 19,
                     "fear_greed_label": "Peur extrême", "vix": 15.9,
                     "dxy": 100.70}
    polymarket = {"fed_bars": {"dominant": "maintien", "dominant_pct": 88.5}}
    levels = {"BTC": compute_key_levels("BTC", _btc_series(), price=61949.0)}
    return daily_pnl, evening_macro, polymarket, levels


def test_degraded_fallbacks_fill_sections():
    from src.main import _apply_evening_degraded_fallbacks

    daily_pnl, em, pm, levels = _degraded_env()
    payload = {"_degraded": True,
               "delta_summary": [{"icon": "⚠", "text": "IA indisponible."}]}
    _apply_evening_degraded_fallbacks(
        payload, daily_pnl=daily_pnl, evening_macro=em, polymarket=pm,
        computed_levels=levels, since_morning={"available": True, "line": "x"})
    ds = payload["delta_summary"]
    assert 4 <= len(ds) <= 5 and ds[0]["text"] == "IA indisponible."
    assert all(d["icon"] in {"✓", "⚠", "✗"} for d in ds)
    assert any("Fear & Greed à 19" in d["text"] for d in ds)
    assert any("+2,15%" in d["text"] for d in ds)
    assert any("maintien" in d["text"] for d in ds)
    assert payload["levels_tonight"] and payload["levels_readout"]["BTC"]
    tc = payload["tomorrow_checklist"]
    assert tc["scenario"].startswith("Nuit attendue dans le range")
    assert tc["invalidation"].startswith("Clôture sous")
    assert "TAO conserve son +9,1%" in tc["checks"]
    assert payload["since_morning_facts"]["line"] == "x"


def test_degraded_fallbacks_do_not_overwrite_ai_levels():
    """En nominal partiel, les niveaux IA existants ne sont PAS écrasés."""
    from src.main import _apply_evening_degraded_fallbacks

    daily_pnl, em, pm, levels = _degraded_env()
    ai_rows = [{"asset": "BTC", "level": "60 000 $", "type": "support",
                "trigger": "x"}]
    payload = {"_degraded": True, "levels_tonight": list(ai_rows),
               "tomorrow_checklist": {"scenario": "déjà là",
                                      "invalidation": "déjà là",
                                      "checks": "déjà là"}}
    _apply_evening_degraded_fallbacks(
        payload, daily_pnl=daily_pnl, evening_macro=em, polymarket=pm,
        computed_levels=levels, since_morning=None)
    assert payload["levels_tonight"] == ai_rows
    assert payload["tomorrow_checklist"]["scenario"] == "déjà là"


def test_run_evening_wires_degraded_fallbacks():
    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "_apply_evening_degraded_fallbacks" in src
    assert '_degraded' in src


def test_degraded_render_end_to_end():
    """E-A1 : un payload dégradé rend un mail RICHE (à retenir, niveaux,
    checklist) — plus jamais la coquille v25."""
    from src.ai_brain.decision_engine import DecisionEngine
    from src.main import _apply_evening_degraded_fallbacks

    daily_pnl, em, pm, levels = _degraded_env()
    payload = DecisionEngine._degraded("evening", {}, "Génération IA indisponible.")
    payload["daily_pnl"] = daily_pnl
    payload["evening_macro"] = em
    payload["expectancy"] = {"available": False}
    _apply_evening_degraded_fallbacks(
        payload, daily_pnl=daily_pnl, evening_macro=em, polymarket=pm,
        computed_levels=levels, since_morning=None)
    html = _render(payload)
    # v29 (EB1) — boîte noire « À retenir » supprimée (doublon) ; le mode dégradé
    # garde les blocs essentiels ci-dessous.
    assert "retenir aujourd" not in html.lower()
    assert "Niveaux à surveiller cette nuit" in html
    assert "Scénario probable" in html and "Invalidation" in html
    assert "RSI" in html and "MACD" in html
    assert "Génération IA indisponible" in html  # honnêteté conservée


# --------------------------------------------------------------------------- #
# E-A7/E-B7 · E-A8/E-B11 · E-A10/E-B12 · E-A11 · E-A12/E-B14 — rendu
# --------------------------------------------------------------------------- #
def test_header_pnl_same_precision_as_bilan():
    """E-A7/E-B7 : header « +2.15% depuis matin » = même précision que Bilan."""
    html = _render({
        "header": {"win_rate_total": "0/0"},
        "portfolio_snapshot": {"value_usd": 2692.0,
                               "change_since_morning_pct": 2.154},
        "daily_pnl": {"value_usd": 2692.0, "day_change_usd": 57.0,
                      "day_change_pct": 2.154},
        "footer": {},
    })
    assert html.count("+2.15%") >= 2
    assert "+2.2% depuis matin" not in html and "+2.1% depuis matin" not in html


def test_expectancy_hidden_without_recos_visible_with():
    """E-A8/E-B11 : placeholder espérance masqué quand rien à montrer."""
    base = {"header": {}, "portfolio_snapshot": {}, "footer": {},
            "expectancy": {"available": False}}
    html_empty = _render(dict(base))
    assert "Espérance mathématique" not in html_empty
    html_reco = _render({**base, "reco_bilan": [
        {"asset": "ETH", "action": "RENFORCER", "entry": 1614.8,
         "target": 1716.0, "current": 1645.0, "delta_pct": 1.9,
         "status": "on_track"}]})
    assert "Espérance mathématique" in html_reco
    html_val = _render({**base, "expectancy": {
        "available": True, "expectancy_pct": 2.4}})
    assert "+2.4% / reco" in html_val


def test_dxy_broad_labeled_explicitly():
    """E-A10/E-B12 : « large 120.89 » cryptique → « indice élargi » (2 mails)."""
    ev = (_TPL / "report_evening.html.j2").read_text(encoding="utf-8")
    mo = (_TPL / "report_morning.html.j2").read_text(encoding="utf-8")
    assert "indice élargi" in ev and ">large " not in ev
    assert "indice élargi" in mo and "Fed large" not in mo


def test_movers_window_note():
    """E-A11 : la fenêtre 24h des movers est distinguée du P&L depuis matin."""
    txt = (_TPL / "report_evening.html.j2").read_text(encoding="utf-8")
    assert "fenêtre 24h ≠ P&amp;L depuis matin" in txt


def test_short_window_flag_in_timing_line():
    """E-A12/E-B14 : run hors-cycle (<4h après le matin) signalé au header."""
    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "fenêtre courte" in src
    assert "_degenerate_window" in src


# --------------------------------------------------------------------------- #
# E-A9 / E-B8 — Polymarket : géopolitique seulement si vrai signal
# --------------------------------------------------------------------------- #
def test_polymarket_geo_threshold():
    """E-B8 : un pari géopo à 14% n'est plus du bruit quotidien — seuil 25%."""
    from src.data_sources import prediction_markets as pm

    src = inspect.getsource(pm.get_key_markets)
    assert 'e[1]["probability_pct"] >= 25' in src
    # La logique exacte, rejouée : Iran 14% écarté, crise à 30% gardée.
    ranked = [(1, {"question": "BTC > $62k?", "probability_pct": 40,
                   "volume_usd": 100}),
              (3, {"question": "US invade Iran before 2027?",
                   "probability_pct": 14, "volume_usd": 900}),
              (3, {"question": "China blockades Taiwan in 2026?",
                   "probability_pct": 30, "volume_usd": 800})]
    crypto_macro = [e[1] for e in ranked if e[0] <= 2]
    geo = [e[1] for e in ranked
           if e[0] == 3 and e[1]["probability_pct"] >= 25]
    extra = (crypto_macro + geo[:1])[:5]
    questions = [m["question"] for m in extra]
    assert "US invade Iran before 2027?" not in questions
    assert "China blockades Taiwan in 2026?" in questions
    assert questions[0] == "BTC > $62k?"


# --------------------------------------------------------------------------- #
# Rendu global — readout niveaux dans le template + non-régression nominal
# --------------------------------------------------------------------------- #
def test_levels_readout_rendered_under_asset():
    html = _render({
        "header": {}, "portfolio_snapshot": {}, "footer": {},
        "evening_macro": {"btc_price": 61949.0},
        "levels_tonight": [{"asset": "BTC", "level": "59 423 $",
                            "type": "support", "trigger": "Sous → prudence."}],
        "levels_readout": {"BTC": "RSI 54 (neutre) · MACD haussier · ATR 2,1%"},
    })
    assert "RSI 54 (neutre)" in html and "MACD haussier" in html


def test_nominal_payload_regression():
    """Un payload IA nominal (v24-like) rend toutes ses sections comme avant."""
    html = _render({
        "header": {"timing_line": "matin 16h02 · soir 20h01 · Δ4h"},
        "portfolio_snapshot": {"value_usd": 2581.0,
                               "change_since_morning_pct": 0.69},
        "daily_pnl": {"value_usd": 2581.0, "day_change_usd": 18.0,
                      "day_change_pct": 0.69,
                      "top_movers": [{"symbol": "ANKR", "change": 6.3,
                                      "pnl_usd": 3.0}]},
        "delta_summary": [{"icon": "⚠", "text": "Maintien Fed anticipé à 79.5%."}],
        "market_changes": [{"status": "confirmed", "tag": "Macro",
                            "description": "DXY à 101.405.",
                            "source": "Yahoo Finance"}],
        "news_today": [{"title": "OpenUSD et CLARITY", "source": "CryptoSlate",
                        "time": "19h00", "impact": "Perception réglementaire.",
                        "status": "actionnable"}],
        "levels_tonight": [{"asset": "BTC", "level": "59 000 $",
                            "type": "support", "trigger": "Sous → risque."}],
        "tomorrow_checklist": {"checks": "BTC tient 60 000 $ ?",
                               "scenario": "Consolidation.",
                               "invalidation": "BTC sous 59 000 $."},
        "evening_macro": {"btc_price": 60105.0, "fear_greed": 11,
                          "dxy": 101.41, "sp500": 7496.0},
        "expectancy": {"available": False},
        "reco_bilan": [{"asset": "ETH", "action": "RENFORCER", "entry": 1614.8,
                        "target": 1716.0, "current": 1645.0, "delta_pct": 1.9,
                        "status": "on_track"}],
        "footer": {"next_report_at": "demain 08h30"},
    })
    # v29 (EB1) — « À retenir aujourd'hui » retiré des sections attendues.
    for fragment in ("Ce qui a évolué", "Ce qui est tombé",
                     "Niveaux à surveiller", "Demain matin", "on track",
                     "Espérance mathématique"):
        assert fragment.lower() in html.lower(), f"section perdue : {fragment}"
    assert "à retenir aujourd" not in html.lower()
    assert "Génération IA indisponible" not in html
