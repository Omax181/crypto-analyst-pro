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
    from src.data_sources import econ_calendar as _ec
    monkeypatch.setattr(_ec, "get_econ_calendar",
                        lambda horizon_days=8: {"available": False, "events": []})
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
def _mem_with_store(monkeypatch):
    # Audit v26 final — monkeypatch (auto-restauré) au lieu d'une assignation
    # directe qui écrasait le vrai I/O pour TOUS les tests suivants (fuite).
    import src.state.report_memory as rm
    store: dict = {}
    monkeypatch.setattr(rm, "_read",
                        lambda f, default: store.get(f, default if default is not None else []))
    monkeypatch.setattr(rm, "_write", lambda f, data: store.__setitem__(f, data))
    return rm, store


def test_tracker_reissue_updates_content_keeps_anchor(monkeypatch):
    rm, _ = _mem_with_store(monkeypatch)
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


def test_weekly_source_stats_avg(monkeypatch):
    rm, store = _mem_with_store(monkeypatch)
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
def test_build_scoring_detail_dedup_and_fields(monkeypatch):
    from src.tracking.prediction_scoring import PredictionTracker
    rm, store = _mem_with_store(monkeypatch)
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
        "confidence": 78,
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
        "confidence": 78,
        "action_plan": {"entry": 1.10, "stop_loss": 1.15,
                        "take_profit": {"30pct": 1.25}},
    }])
    assert out["thesis_of_the_day"][0]["action"] == "SURVEILLER"


def test_thesis_gate_healthy_plan_kept():
    out = _merged([{
        "asset": "BTC", "action": "RENFORCER", "action_type": "bullish",
        "confidence": 78,
        "action_plan": {"entry": 63000, "stop_loss": 60500,
                        "take_profit": {"30pct": 68200}, "rr": "2.7:1"},
    }])
    t = out["thesis_of_the_day"][0]
    assert t["action"] == "RENFORCER"
    assert t["rr_favorable"] is True
    assert out["header"]["firm_theses_count"] == 1


def test_thesis_confidence_below_75_filtered():
    # v23.x — 70% passait l'ancien seuil 60% mais PAS le nouveau seuil 75%
    # (filtre anti-bruit voulu par Omar). Toute thèse < 75% est retirée.
    out = _merged([{"asset": "ADA", "action": "SURVEILLER",
                    "action_type": "neutral", "confidence": 70}])
    assert out["thesis_of_the_day"] == []
    assert all((t.get("confidence") or 0) >= 75
               for t in (out["thesis_of_the_day"] or []))


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
    # v19/M-A9 : hors jour même → date ABSOLUE « DD/MM HH:MM », plus de « hier ».
    yesterday = now - dt.timedelta(days=1)
    label = _fr_when(yesterday.isoformat())
    assert "hier" not in label
    assert yesterday.strftime("%d/%m") in label
    assert _fr_when("garbage") is None


# --------------------------------------------------------------------------- #
# Heatmap extra + corrélations masquées + normalisations
# --------------------------------------------------------------------------- #
def test_heatmap_extra_weighted_avg():
    from src.main import _portfolio_heatmap
    enriched = {f"A{i}": {"value_usd": 100, "change_24h": 1.0} for i in range(23)}
    enriched["X1"] = {"value_usd": 300, "change_24h": 10.0}
    enriched["X2"] = {"value_usd": 100, "change_24h": -2.0}
    out = _portfolio_heatmap(enriched)
    # v28 (M-A11) — 25 positions → 15 cases pleines (tri par IMPACT) + agrégat.
    assert len(out["cells"]) == 15
    ex = out["extra"]
    # tri par impact (poids×|perf|) : X1 puis X2 (gros poids/mouvement) puis
    # les A. Top 15 = X1 + X2 + 13×A. Restants = 10×A (+1.0), valeur 1000.
    assert ex["count"] == 10
    # Moyenne pondérée des 10 A restants : (100·1)·10 / 1000 = 1.0.
    assert ex["avg_change_24h"] == 1.0
    assert ex["value_usd"] == 1000
    # Les 2 plus gros mouvements sont en tête.
    assert out["cells"][0]["symbol"] == "X1"
    assert out["cells"][1]["symbol"] == "X2"
    # v16 — % PTF présent sur chaque case et sur l'agrégat.
    assert all("ptf_pct" in c for c in out["cells"])
    assert ex["ptf_pct"] is not None
    # Total PTF = 300 + 100 + 23·100 = 2700 → X1 = 300/2700 = 11.1%.
    assert out["cells"][0]["ptf_pct"] == 11.1


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
    # v18 (M-B8) : 4 axes, le Cash a été retiré.
    assert set(comps) >= {"Drawdown 7j", "Concentration", "Volatilité 24h", "Sentiment"}
    assert "Cash" not in comps
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
                   "active_recos_count": 9,
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
                                         "value_usd": 372, "ptf_pct": 21.8}],
                              "extra": {"count": 12, "avg_change_24h": 1.4,
                                        "value_usd": 220.0, "ptf_pct": 12.9},
                              "total_count": 13, "remaining": 0},
        "onchain_indicators": {"metrics": [
            {"label": "Hashrate", "value": "960 EH/s", "color": "#3B6D11",
             "short": "réseau sain", "interpretation": "réseau sain"}],
            "verdict": "neutre",
            "combined_reading": "pas de signal d'entrée fort, attendre confirmation"},
    }, "morning")
    assert "1 nouvelle reco · 9 en suivi" in html
    assert "EN BREF" in html and "1 renforcement BTC" in html
    assert "maintien" in html and "99.2%" in html
    # v19/V18-M4 : 2e valeur DXY discrète. v28 (M-A20) : « DXY (ICE) » sur la
    # tuile + « indice élargi (Fed) » — les deux indices nommés sans note ².
    assert "indice élargi (Fed) · 120.08" in html
    assert "DXY (ICE)" in html
    assert "+12 autres" in html and "moy." in html  # v16 : case agrégée %PTF
    assert "12.9% du PTF" in html                    # v16 : poids agrégat
    assert "réseau sain" in html                     # v16 : grille on-chain horizontale
    assert "Bilan on-chain : neutre" in html         # v16 : verdict-first
    assert "DXY &gt; 101" in html or "DXY > 101" in html
    assert "Crypto Analyst Pro · v29" in html
    # v29 (MB6) — « À surveiller » + « invalider » fusionnés en « À surveiller ·
    # seuils d'invalidation », AVANT l'auto-critique (qui reste séparée).
    assert html.index("seuils d'invalidation") < html.index("Auto-critique de l'analyse")


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
                          "nikkei": 39000, "eur_usd": 1.1537, "usd_jpy": 161.5},
        "polymarket_facts": {"fed_bars": {"cut_pct": 0.2, "hold_pct": 99.2,
                                          "hike_pct": 0.4, "dominant": "maintien",
                                          "dominant_pct": 99.2}},
    }, "evening")
    assert "matin 10h14 · soir 19h32 · Δ9h" in html
    assert "+0.04%" in html and "journée neutre" in html
    assert "+$1.00" in html                    # $ adaptatif < 10 $
    assert "Actions à poser ce soir" in html and "ordre limite BTC" in html
    # v29 (ZB5) — International du soir allégé : USD/JPY (carry) conservé,
    # Nikkei/Stoxx/EUR-USD retirés (déjà couverts le matin).
    assert "USD/JPY" in html and "carry trade yen" in html
    assert "Nikkei 225" not in html and "Stoxx 50" not in html
    assert "maintien" in html and "99.2%" in html
    assert "Crypto Analyst Pro · v29" in html


def test_render_weekly_v15_blocks():
    from src.reporting.email_html import render
    html = render({
        "header": {"week_number": 24, "year": 2026,
                   "period_covered": "du 5 juin au 12 juin",
                   "upcoming_week": "13 juin – 20 juin",
                   "sources_week_label": "21/25 sources actives en moyenne cette semaine (pic 23, 6 jours observés)"},
        "portfolio_snapshot": {"value_usd": 1686, "weekly_pnl_pct": -4.9,
                               "week_start_value": 1773, "week_end_value": 1686},
        "weekly_summary": [
            {"text": "**S&P 500 +2,1%** et **DXY -0,7%** → léger soutien au risque"},
            {"text": "**Peur Extrême (F&G 12)** persistante malgré la hausse actions"},
            "Bloc **AI -5,4%** sur la semaine, pression sur le PTF"],
        "macro_panorama": "Régime risk-off persistant, FOMC mercredi en juge de paix.",
        "sector_exposure_computed": {"available": True, "sectors": [
            {"sector": "L1", "ptf_pct": 46.6, "market_change_24h": -1.3,
             "holdings": ["BTC", "ETH", "ADA", "ATOM"]},
            {"sector": "AI", "ptf_pct": 25.8, "market_change_24h": 0.9,
             "holdings": ["TAO", "RENDER", "FET"]}]},
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
        "my_errors": "J'ai sous-estimé la persistance des sorties d'ETF.",
        "ptf_quality_score": {"score": 4.2, "prev_score": None, "delta_wow": None,
                              "axes": [{"label": "Diversification", "score": 5.6,
                                        "detail": "top secteur 47% du PTF"},
                                       {"label": "Solidité (vs ATH)", "score": 4.7,
                                        "detail": "drawdown pondéré -50% vs ATH"}]},
        "week_ahead": [{"label": "Décision FOMC (taux Fed)", "date": "2026-06-17",
                        "when": "dans 5j", "importance": "high",
                        "polymarket_note": "Polymarket : maintien 99.2%"}],
        "positions_review": [
            {"asset": "ETH", "conviction": True, "current_price": 1655.39,
             "pru_pct": -8.0, "h30": {"reco": "RENFORCER", "delta_pct": 6.2,
                                      "status": "validated"},
             "lt_status": "accumulation", "lt_target": 4900, "lt_target_pct": 196,
             "analysis": "Cassure de résistance, flux ETF de retour.",
             "action": "renforcer"},
            {"asset": "CKB", "conviction": False, "current_price": 0.00111,
             "pru_pct": 3.0, "h30": {"reco": "RENFORCER", "delta_pct": 7.7,
                                     "status": "in_progress"},
             "lt_status": "capitulation", "lt_target": None, "lt_target_pct": None,
             "analysis": "Poussière, offload sur force.", "action": "alléger"}],
        "ath_facts": {"BTC": {"ath": 108000, "from_ath_pct": -41.9}},
        "strategy_focus": "Biais défensif. Priorité : reconstituer du cash.",
    }, "weekly")
    assert "du 5 juin au 12 juin" in html
    assert "21/25 sources actives en moyenne" in html
    assert "1/5 minimum pour calibration" in html
    # v23.x — perf reco 30j fusionnée dans le tableau positions_review (colonne Horizon 30j) :
    assert "Horizon 30j" in html and "Long terme" in html
    assert "✓ validée" in html and "● en cours" in html
    assert "dès la première clôture" in html   # regret honnête
    assert "Mon erreur de la semaine" in html
    # v16 — BTC hold SUPPRIMÉ : le doublon « [fenêtre : 7 jours] » ne doit plus exister.
    assert "[fenêtre : 7 jours]" not in html
    assert "Gestion active vs BTC hold" not in html
    # v16 — bilan en bullets avec gras.
    assert "S&amp;P 500 +2,1%" in html or "S&P 500 +2,1%" in html
    assert "<strong>" in html  # le gras Markdown est rendu
    # v16 — secteur avec actifs exemples.
    assert "L1" in html and "BTC, ETH, ADA" in html
    # v16 — corrélation entre positions supprimée.
    assert "Corrélation entre tes positions" not in html
    # v16 — solidité affiche un détail réel (plus de « n/d »).
    assert "drawdown pondéré -50% vs ATH" in html
    assert "Santé du portefeuille" in html and "4.2" in html
    assert "Polymarket : maintien 99.2%" in html
    # v23.x — ATH/description retirés ; le tableau fusionné montre phase de cycle
    # + action déterministe (couleurs logiques).
    assert "Accumulation" in html and "Capitulation" in html
    # v29 (WB6) — la flèche « → Renforcer » est SUPPRIMÉE quand le badge Horizon
    # 30j porte déjà RENFORCER (dédup) ; « → Alléger » (action ≠ reco 30j) reste.
    assert "RENFORCER" in html          # badge Horizon 30j (ETH)
    assert "→ Alléger" in html          # flèche non dupliquée (CKB)
    assert "→ Renforcer" not in html    # dédup WB6
    assert "Stratégie de la semaine" in html
    assert "1\u202f773" in html or "1,773" in html or "1 773" in html  # fenêtre P&L
    assert "Crypto Analyst Pro · v29" in html
    # Ordre : la vue PTF arrive avant le fil rouge macro (P3-1).
    assert html.index("Portfolio · vue d'ensemble") < html.index("Fil rouge macro")
