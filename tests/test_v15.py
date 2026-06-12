"""Tests v15 — audit Omar (morning/evening/weekly).

Couvre les nouveautés v15 SANS réseau :
- calendrier macro consolidé (repli FOMC/BoJ, dédup, « estimé »)
- Polymarket étendu (barres Fed, dominant, extra markets)
- tracker « la dernière reco prime » (mise à jour + ancrage préservé)
- détail de scoring 100% Python (dédup, delta, holding_days)
- filets thèses (R:R > 8 → SURVEILLER, SL incohérent → SURVEILLER)
- compteur header recos fermes / surveillance
- news : cap 6, dates FR, confiance /5 → %
- heatmap « +N autres », corrélations < 0.4 masquées
- exposition sectorielle : fusion Indexing/Infra + « Divers <1% »
- score de risque : composantes 4 axes
- stats sources hebdo + win rate seuil 5 + regret honnête
- rendus HTML : EN BREF puces, footer v15, P&L neutre, détail hebdo
"""

from __future__ import annotations

import datetime as dt

# --------------------------------------------------------------------------- #
# Calendrier macro consolidé
# --------------------------------------------------------------------------- #
def test_calendar_central_bank_dates_valid():
    from src.data_sources import macro_calendar as mc
    for ev in mc._CENTRAL_BANK_EVENTS:
        dt.datetime.strptime(ev["date"], "%Y-%m-%d")  # lève si invalide
        assert ev["estimated"] is False
        assert ev["importance"] == "high"


def test_calendar_recurring_estimates_flagged():
    from src.data_sources import macro_calendar as mc
    today = dt.date(2026, 6, 1)
    evts = mc._recurring_estimates(today, 30)
    assert evts, "récurrences attendues sur 30j"
    assert all(e["estimated"] for e in evts)
    labels = {e["label"] for e in evts}
    assert any("NFP" in l for l in labels)
    assert any("CPI" in l for l in labels)


def test_calendar_dedup_real_beats_estimate():
    from src.data_sources import macro_calendar as mc
    # même famille + même date → la clé de dédup doit matcher
    k1 = mc._norm_key("Inflation US (CPI)", "2026-06-11")
    k2 = mc._norm_key("CPI (Consumer Price Index)", "2026-06-11")
    assert k1 == k2


def test_calendar_consolidated_never_empty(monkeypatch):
    from src.data_sources import macro_calendar as mc
    from src.utils.cache import CACHE
    monkeypatch.setattr(mc.fred, "get_upcoming_releases",
                        lambda horizon_days=8: {"available": False})
    monkeypatch.setattr(mc, "get_boursorama_calendar",
                        lambda: {"available": False})
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: fn())
    out = mc.get_consolidated_calendar(horizon_days=400)
    # Sur 400 jours, le repli banques centrales garantit des événements.
    assert out["available"] is True
    assert any("FOMC" in e["label"] for e in out["events"])
    # Les récurrences sont marquées « (estimé) » dans le label final.
    assert any("(estimé)" in e["label"] for e in out["events"])
    # Tri par date + days_ahead cohérent.
    dates = [e["date"] for e in out["events"]]
    assert dates == sorted(dates)


# --------------------------------------------------------------------------- #
# Polymarket étendu
# --------------------------------------------------------------------------- #
def test_polymarket_fed_bars_dominant(monkeypatch):
    from src.data_sources import prediction_markets as pm
    from src.utils.cache import CACHE
    fed_markets = [
        {"question": "Will the Fed decrease interest rates after the June 2026 meeting?",
         "probability_pct": 0.2, "end_date": "2026-06-17"},
        {"question": "Will there be no change in Fed interest rates after the June 2026 meeting?",
         "probability_pct": 99.2, "end_date": "2026-06-17"},
        {"question": "Will the Fed increase interest rates after the June 2026 meeting?",
         "probability_pct": 0.4, "end_date": "2026-06-17"},
    ]
    monkeypatch.setattr(pm, "get_fed_cut_probabilities",
                        lambda: {"available": True, "markets": fed_markets})
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: None)
    out = pm.get_key_markets()
    fb = out["fed_bars"]
    assert fb["dominant"] == "maintien"
    assert fb["dominant_pct"] == 99.2
    assert fb["cut_pct"] == 0.2 and fb["hike_pct"] == 0.4
    assert "juin" in (fb.get("meeting_hint") or "")
    assert out["markets"] == fed_markets  # alias rétro-compat


def test_polymarket_extra_filters_themes(monkeypatch):
    from src.data_sources import prediction_markets as pm
    from src.utils.cache import CACHE
    raw = [
        {"id": 1, "question": "US recession in 2026?",
         "outcomePrices": '["0.32","0.68"]', "volumeNum": 9_000_000,
         "endDate": "2026-12-31"},
        {"id": 2, "question": "Will it rain in Paris tomorrow?",
         "outcomePrices": '["0.5","0.5"]', "volumeNum": 99_999_999,
         "endDate": "2026-06-13"},
        {"id": 3, "question": "Bitcoin above $100k by December?",
         "outcomePrices": '["0.99","0.01"]', "volumeNum": 5_000_000,
         "endDate": "2026-12-31"},
    ]
    monkeypatch.setattr(pm, "get_fed_cut_probabilities",
                        lambda: {"available": False, "markets": []})
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: raw)
    out = pm.get_key_markets()
    qs = [e["question"] for e in out["extra_markets"]]
    assert any("recession" in q.lower() for q in qs)
    assert not any("rain" in q.lower() for q in qs)       # hors thèmes
    # Proba 99% = quasi acquis → dépriorisée face à la récession 32%.
    assert qs[0].lower().startswith("us recession")


# --------------------------------------------------------------------------- #
# Tracker — la dernière reco prime
# --------------------------------------------------------------------------- #
def _mem_with_store():
    import src.state.report_memory as rm
    store: dict = {}
    rm._read = lambda f, default: store.get(f, default if default is not None else [])
    rm._write = lambda f, data: store.__setitem__(f, data)
    return rm, store


def test_tracker_reissue_updates_content_keeps_anchor():
    rm, _ = _mem_with_store()
    rm.add_recommendation({"id": "BTC-1", "asset": "BTC", "action": "RENFORCER",
                           "entry_price": 60000, "confidence": 55,
                           "rationale": "v1"})
    rm.add_recommendation({"id": "BTC-2", "asset": "BTC", "action": "RENFORCER",
                           "entry_price": 61000, "confidence": 70,
                           "rationale": "v2 plus récente"})
    recos = rm.load_active_recommendations()
    assert len(recos) == 1
    r = recos[0]
    assert r["entry_price"] == 60000          # ancrage de scoring préservé
    assert r["confidence"] == 70              # contenu : la dernière prime
    assert r["rationale"] == "v2 plus récente"
    assert r["reissues"] == 1
    assert r.get("last_issued_at")


def test_weekly_source_stats_avg():
    rm, store = _mem_with_store()
    now = dt.datetime.now(dt.timezone.utc)
    logs = [
        {"date": (now - dt.timedelta(days=1)).isoformat(), "down": ["A"]},        # 24
        {"date": (now - dt.timedelta(days=2)).isoformat(), "down": ["A", "B", "C"]},  # 22
        {"date": (now - dt.timedelta(days=20)).isoformat(), "down": []},          # hors fenêtre
    ]
    store[rm.SOURCE_HEALTH_FILE] = logs
    out = rm.compute_weekly_source_stats(25)
    assert out["available"] is True
    assert out["days_observed"] == 2
    assert out["avg_active"] == 23            # (24+22)/2
    assert out["best_active"] == 24


# --------------------------------------------------------------------------- #
# Détail de scoring Python (weekly)
# --------------------------------------------------------------------------- #
def test_build_scoring_detail_dedup_and_fields():
    import src.state.report_memory as rm
    from src.tracking.prediction_scoring import PredictionTracker
    rm, store = _mem_with_store()
    now = dt.datetime.now(dt.timezone.utc)
    old = (now - dt.timedelta(days=3)).isoformat()
    newer = (now - dt.timedelta(days=1)).isoformat()
    store[rm.ACTIVE_RECOS_FILE] = [
        {"asset": "CKB", "action": "RENFORCER", "created_at": newer,
         "entry_price": 0.00103, "current_price": 0.00111,
         "status": "in_progress"},
        {"asset": "BTC", "action": "SURVEILLER", "created_at": newer},  # exclu
    ]
    store[rm.PREDICTION_HISTORY_FILE] = [
        {"asset": "CKB", "action": "RENFORCER", "created_at": old,
         "closed_at": old, "entry_price": 0.0012, "status": "invalidated"},
        {"asset": "ETH", "action": "RENFORCER", "created_at": old,
         "closed_at": newer, "entry_price": 1500, "status": "validated"},
    ]
    tracker = PredictionTracker()
    detail = tracker.build_scoring_detail({"CKB": 0.00111, "ETH": 1650}, 7)
    by = {d["asset"]: d for d in detail}
    assert set(by) == {"CKB", "ETH"}          # 1 ligne par actif, SURVEILLER exclu
    assert by["CKB"]["status"] == "in_progress"  # la plus récente prime
    assert by["CKB"]["score"] == 0
    assert by["ETH"]["score"] == 1
    assert by["ETH"]["delta_pct"] == 10.0     # (1650-1500)/1500
    assert by["CKB"]["holding_days"] in (0, 1)
    assert by["ETH"]["entry_date"]            # dd/mm


# --------------------------------------------------------------------------- #
# Filets thèses (R:R, SL) + compteur header
# --------------------------------------------------------------------------- #
def _merged(theses):
    from src.main import _merge_python_facts
    payload = {"thesis_of_the_day": theses}
    return _merge_python_facts(payload, {"eligible_theses": []}, "11/06 08:30")


def test_thesis_gate_rr_over_8_demoted():
    out = _merged([{
        "asset": "STX", "action": "RENFORCER", "action_type": "bullish",
        "confidence": 70,
        "action_plan": {"entry": 100, "stop_loss": 99.4,
                        "take_profit": {"30pct": 108}, "rr": "13:1"},
    }])
    t = out["thesis_of_the_day"][0]
    assert t["action"] == "SURVEILLER"
    assert t["demoted_by_python"] is True
    assert "action_plan" not in t
    assert out["header"]["firm_theses_count"] == 0
    assert out["header"]["watch_theses_count"] == 1


def test_thesis_gate_sl_wrong_side_demoted():
    out = _merged([{
        "asset": "XRP", "action": "RENFORCER", "action_type": "bullish",
        "confidence": 70,
        "action_plan": {"entry": 1.10, "stop_loss": 1.15,
                        "take_profit": {"30pct": 1.25}},
    }])
    assert out["thesis_of_the_day"][0]["action"] == "SURVEILLER"


def test_thesis_gate_healthy_plan_kept():
    out = _merged([{
        "asset": "BTC", "action": "RENFORCER", "action_type": "bullish",
        "confidence": 72,
        "action_plan": {"entry": 63000, "stop_loss": 60500,
                        "take_profit": {"30pct": 68200}, "rr": "2.7:1"},
    }])
    t = out["thesis_of_the_day"][0]
    assert t["action"] == "RENFORCER"
    assert t["rr_favorable"] is True
    assert out["header"]["firm_theses_count"] == 1


def test_thesis_confidence_below_60_filtered():
    out = _merged([{"asset": "ADA", "action": "SURVEILLER",
                    "action_type": "neutral", "confidence": 53}])
    assert out["thesis_of_the_day"] == [] or all(
        (t.get("confidence") or 0) >= 60 for t in out["thesis_of_the_day"])


# --------------------------------------------------------------------------- #
# News : cap 6, confiance /5 → %, dates FR
# --------------------------------------------------------------------------- #
def test_news_cap_and_confidence_scale():
    from src.main import _merge_python_facts
    news = [{"title": f"n{i}", "category": "Macro", "confidence": 4,
             "timestamp": "2026-06-11T08:56:45+00:00"} for i in range(9)]
    out = _merge_python_facts({"news_24h": news}, {}, "11/06")
    assert len(out["news_24h"]) == 6          # cap strict
    n0 = out["news_24h"][0]
    assert n0["confidence"] == 80             # 4/5 → 80%
    assert n0["timestamp_iso"].startswith("2026-06-11")
    assert ":" in n0["timestamp"] and "T" not in n0["timestamp"]  # libellé FR


def test_fr_when_labels():
    from src.main import _fr_when, TZ
    now = dt.datetime.now(TZ)
    today = now.replace(hour=9, minute=5).isoformat()
    assert "09:05" in _fr_when(today)
    yesterday = (now - dt.timedelta(days=1)).isoformat()
    assert _fr_when(yesterday).startswith("hier")
    assert _fr_when("garbage") is None


# --------------------------------------------------------------------------- #
# Heatmap extra + corrélations masquées + normalisations
# --------------------------------------------------------------------------- #
def test_heatmap_extra_weighted_avg():
    from src.main import _portfolio_heatmap
    enriched = {f"A{i}": {"value_usd": 100, "change_24h": 1.0} for i in range(16)}
    enriched["X1"] = {"value_usd": 300, "change_24h": 10.0}
    enriched["X2"] = {"value_usd": 100, "change_24h": -2.0}
    out = _portfolio_heatmap(enriched)
    assert len(out["cells"]) == 16
    ex = out["extra"]
    assert ex["count"] == 2
    # X1 (300) absorbé dans le top par valeur → recalcul : top16 = X1 + 15×A ;
    # restants = 1×A(100,+1.0) + X2(100,−2.0) → moyenne pondérée −0.5.
    assert ex["avg_change_24h"] == -0.5
    assert ex["value_usd"] == 200


def test_quant_correlations_hidden_below_threshold():
    from src.main import _merge_python_facts
    data = {"analytics_digest": {
        "macro_correlations": "BTC ↔ DXY +0.23, VIX +0.23, S&P 500 +0.01",
        "options": "put/call 0.62",
    }}
    out = _merge_python_facts({}, data, "11/06")
    q = out.get("quant_reference") or {}
    assert "correlations" not in q            # max |corr| < 0.4 → masqué
    assert q.get("options")
    data["analytics_digest"]["macro_correlations"] = "BTC ↔ DXY +0.62"
    out2 = _merge_python_facts({}, data, "11/06")
    assert (out2.get("quant_reference") or {}).get("correlations")


def test_normalize_executive_and_invalidation():
    from src.main import _merge_python_facts
    payload = {
        "executive_summary": {"bullets": [
            {"icon": "✓", "text": "1 renforcement BTC"},
            {"icon": "??", "text": "icône invalide → ⚠"},
            "puce string",
        ]},
        "invalidation_watch": ["DXY > 101", {"condition": "S&P < 7200",
                                             "implication": "risk-off confirmé"}],
        "self_critique_global": "une seule phrase.",
    }
    out = _merge_python_facts(payload, {}, "11/06")
    bl = out["executive_summary"]["bullets"]
    assert bl[0]["icon"] == "✓" and bl[1]["icon"] == "⚠"
    assert bl[2] == {"icon": "⚠", "text": "puce string"}
    iw = out["invalidation_watch"]
    assert iw[0] == {"condition": "DXY > 101", "implication": ""}
    assert iw[1]["implication"] == "risk-off confirmé"
    assert out["self_critique_global"]["bullets"] == ["une seule phrase."]


# --------------------------------------------------------------------------- #
# Exposition sectorielle : fusion + « Divers <1% »
# --------------------------------------------------------------------------- #
def test_sector_merge_and_minor_bucket():
    from src.main import _compute_sector_exposure
    enriched = {
        "BTC": {"value_usd": 940}, "GRT": {"value_usd": 30},
        "QNT": {"value_usd": 25}, "SATS": {"value_usd": 5},
    }
    rotation = {"sectors": {
        "L1": {"members": ["BTC"], "avg_change_24h": 1.0},
        "Infra": {"members": ["QNT"], "avg_change_24h": 0.5},
        "Indexing/Infra": {"members": ["GRT"], "avg_change_24h": 0.2},
        "Ordinals": {"members": ["SATS"], "avg_change_24h": 2.0},
    }}
    out = _compute_sector_exposure(enriched, rotation)
    names = [s["sector"] for s in out["sectors"]]
    assert "Indexing/Infra" not in names      # fusionné dans Infra
    infra = next(s for s in out["sectors"] if s["sector"] == "Infra")
    assert infra["value_usd"] == 55.0         # 25 + 30
    assert any(n.startswith("Divers") for n in names)   # SATS 0.5% regroupé
    assert not any(s["ptf_pct"] < 1.0 and not s["sector"].startswith("Divers")
                   for s in out["sectors"])


# --------------------------------------------------------------------------- #
# Score de risque : composantes
# --------------------------------------------------------------------------- #
def test_risk_score_components_structure():
    from src.main import _compute_portfolio_risk_score
    out = _compute_portfolio_risk_score(
        snapshot={"change_7d_pct": -5.6, "drawdown_ath_pct": -75.8},
        sector_exposure={"sectors": [{"sector": "L1", "ptf_pct": 47.0}]},
        macro_context={"fear_greed": 12, "vix": 20.6},
        enriched={"BTC": {"value_usd": 1000, "change_24h": 2.8}},
        portfolio={"BTC": {"value_usd": 1000}},
    )
    comps = {c["label"]: c for c in out["components"]}
    assert set(comps) >= {"Drawdown 7j", "Concentration", "Cash", "Sentiment"}
    assert comps["Cash"]["pts"] == 1.5        # aucune réserve
    assert comps["Sentiment"]["pts"] == 1.0   # F&G 12, VIX < 25
    for c in out["components"]:
        assert 0 <= c["pts"] <= c["max"]


# --------------------------------------------------------------------------- #
# Rendus HTML
# --------------------------------------------------------------------------- #
def test_render_morning_v15_blocks():
    from src.reporting.email_html import render
    html = render({
        "header": {"firm_theses_count": 1, "watch_theses_count": 3,
                   "active_sources_count": 22, "total_sources_count": 25},
        "executive_summary": {"bullets": [
            {"icon": "✓", "text": "1 renforcement BTC"},
            {"icon": "✗", "text": "flux ETF négatifs"}]},
        "risk_score": {"score": 5.3, "level": "modéré", "level_color": "#BA7517",
                       "factors": ["x"], "components": [
                           {"label": "Drawdown 7j", "pts": 1.1, "max": 3.0},
                           {"label": "Cash", "pts": 1.5, "max": 1.5}]},
        "macro_context": {"btc_price": 62785,
                          "polymarket_fed_bars": {"cut_pct": 0.2, "hold_pct": 99.2,
                                                  "hike_pct": 0.4,
                                                  "dominant": "maintien",
                                                  "dominant_pct": 99.2,
                                                  "meeting_hint": "réunion juin 2026"},
                          "dxy": 100.10, "dxy_broad": 120.08},
        "invalidation_watch": [{"condition": "DXY > 101",
                                "implication": "fuite dollar"}],
        "self_critique_global": {"bullets": ["flux ETF indisponibles"]},
        "portfolio_heatmap": {"cells": [{"symbol": "BTC", "change_24h": 2.8,
                                         "value_usd": 372}],
                              "extra": {"count": 12, "avg_change_24h": 1.4,
                                        "value_usd": 220.0},
                              "total_count": 13, "remaining": 0},
        "onchain_indicators": {"metrics": [
            {"label": "Hashrate", "value": "960 EH/s", "color": "#3B6D11",
             "interpretation": "réseau sain"}],
            "combined_reading": "ok"},
    }, "morning")
    assert "1 reco ferme · 3 sous surveillance" in html
    assert "EN BREF" in html and "1 renforcement BTC" in html
    assert "maintien" in html and "99.2%" in html
    assert "indice large Fed 120.08" in html
    assert "+12 autres" in html and "moy. pond." in html
    assert "réseau sain" in html               # tableau on-chain 3 col
    assert "DXY &gt; 101" in html or "DXY > 101" in html
    assert "Crypto Analyst Pro · v15" in html
    # Ordre : invalidation AVANT auto-critique.
    assert html.index("invalider mon scénario") < html.index("Auto-critique de l'analyse")


def test_render_evening_v15_blocks():
    from src.reporting.email_html import render
    html = render({
        "header": {"timing_line": "matin 10h14 · soir 19h32 · Δ9h"},
        "portfolio_snapshot": {"value_usd": 1688},
        "daily_pnl": {"value_usd": 1688, "day_change_usd": 1.0,
                      "day_change_pct": 0.04, "day_change_label": "neutre",
                      "top_movers": [{"symbol": "CKB", "change": 6.3,
                                      "pnl_usd": 4}]},
        "evening_heatmap": {"gainers": [{"symbol": "CKB", "change": 6.3}],
                            "losers": [{"symbol": "FET", "change": -3.1}]},
        "actions_tonight": ["Placer un ordre limite BTC à 60 000 $"],
        "evening_macro": {"btc_price": 62800, "stoxx50": 5400,
                          "nikkei": 39000, "eur_usd": 1.1537},
        "polymarket_facts": {"fed_bars": {"cut_pct": 0.2, "hold_pct": 99.2,
                                          "hike_pct": 0.4, "dominant": "maintien",
                                          "dominant_pct": 99.2}},
    }, "evening")
    assert "matin 10h14 · soir 19h32 · Δ9h" in html
    assert "+0.04%" in html and "journée neutre" in html
    assert "+$1.00" in html                    # $ adaptatif < 10 $
    assert "Actions à poser ce soir" in html and "ordre limite BTC" in html
    assert "International · Europe" in html and "Nikkei 225" in html
    assert "maintien" in html and "99.2%" in html
    assert "Crypto Analyst Pro · v15" in html


def test_render_weekly_v15_blocks():
    from src.reporting.email_html import render
    html = render({
        "header": {"week_number": 24, "year": 2026,
                   "period_covered": "du 5 juin au 12 juin",
                   "sources_week_label": "21/25 sources actives en moyenne cette semaine (pic 23, 6 j observés)"},
        "portfolio_snapshot": {"value_usd": 1686, "weekly_pnl_pct": -4.9,
                               "week_start_value": 1773, "week_end_value": 1686},
        "weekly_summary": "Semaine risk-off.",
        "macro_panorama": "Régime risk-off persistant, FOMC mercredi en juge de paix.",
        "predictions_scoring": {
            "issued": 2, "validated": 1, "invalidated": 0, "open_count": 1,
            "closed_count": 1, "win_rate_pct": None,
            "winrate_gate_label": "Recos clôturées : 1/5 minimum pour calibration",
            "lesson": "Patience.",
            "detail": [
                {"asset": "ETH", "reco": "RENFORCER", "entry_date": "06/06",
                 "entry_price": 1559.38, "current_price": 1655.39,
                 "delta_pct": 6.2, "holding_days": 5, "status": "validated",
                 "score": 1},
                {"asset": "CKB", "reco": "RENFORCER", "entry_date": "08/06",
                 "entry_price": 0.00103, "current_price": 0.00111,
                 "delta_pct": 7.7, "holding_days": 3, "status": "in_progress",
                 "score": 0}]},
        "regret": {"available": False,
                   "empty_reason": "Pas encore de reco clôturée sur la fenêtre · mesure du coût des erreurs disponible dès la première clôture."},
        "my_errors": "Lecture trop prudente sur CKB.",
        "btc_hold_comparison": {"btc_hold_value": 1715, "actual_value": 1686,
                                "outperforms": False, "window_label": "7 jours",
                                "verdict": "Ta gestion active sous-performe un simple BTC hold de 1.7% sur 7 jours (même fenêtre que le P&L semaine)."},
        "ptf_quality_score": {"score": 4.2, "prev_score": None, "delta_wow": None,
                              "axes": [{"label": "Diversification", "score": 5.6,
                                        "detail": "top secteur 47% du PTF"},
                                       {"label": "Réserve cash", "score": 0.0,
                                        "detail": "USDC 0.0% du PTF"}]},
        "week_ahead": [{"label": "Décision FOMC (taux Fed)", "date": "2026-06-17",
                        "when": "dans 5j", "importance": "high",
                        "polymarket_note": "Polymarket : maintien 99.2%"}],
        "long_term_positioning": [{"asset": "BTC", "thesis": "Réserve numérique",
                                   "target": "Retest ATH réel", "status": "accumulation",
                                   "status_color": "#3B6D11"}],
        "ath_facts": {"BTC": {"ath": 108000, "from_ath_pct": -41.9}},
        "strategy_focus": "Biais défensif. Priorité : reconstituer du cash.",
    }, "weekly")
    assert "du 5 juin au 12 juin" in html
    assert "21/25 sources actives en moyenne" in html
    assert "1/5 minimum pour calibration" in html
    assert "06/06" in html and "5j" in html.replace("\u202f", "")  # détail dates+jours
    assert "✓ validée" in html and "● en cours" in html
    assert "dès la première clôture" in html   # regret honnête
    assert "Mon erreur de la semaine" in html
    assert "[fenêtre : 7 jours]" in html
    assert "Qualité PTF" in html and "4.2" in html
    assert "Polymarket : maintien 99.2%" in html
    assert "108" in html and "−41.9%" in html.replace("-41.9%", "−41.9%")
    assert "Stratégie de la semaine" in html
    assert "1\u202f773" in html or "1,773" in html or "1 773" in html  # fenêtre P&L
    assert "Crypto Analyst Pro · v15" in html
    # Ordre : la vue PTF arrive avant le fil rouge macro (P3-1).
    assert html.index("Portfolio · vue d'ensemble") < html.index("Fil rouge macro")
