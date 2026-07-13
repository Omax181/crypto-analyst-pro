# -*- coding: utf-8 -*-
"""Tests v26 — refonte morning post-audit v25 (A1-A20 · B1-B22 · C1/C2).

Couvre :
  A1/A2/B19  tuiles on-chain déterministes (whale jamais « 0 » nu, datation)
  A3/B5      flux ETF structurés depuis le canal Telegram ETF_Flows
  A4/A5/B2   zéro-reco : bullets propres + no_thesis_assets structuré
  A10        filet NFP : férié US → décalage jeudi
  A12/B6     tracking : progression vers la cible, persistance cible/stop
  A14/B17    angles morts sans doublon du footer
  A15        Polymarket : issue mesurée explicite
  A20/B4     bannière jour-sans-reco + graphiques de suivi (B8)
  B18/B21    agenda macro 72h déterministe
  C2         LunarCrush→CoinGecko trending · unlocks CoinMarketCal · adresses BTC
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src import main
from src.main import _build_onchain_tiles, _merge_python_facts
from src.reporting.email_html import APP_VERSION, render


# ─────────────────────────────────────────────────────────────────────────────
# Version produit
# ─────────────────────────────────────────────────────────────────────────────
def test_app_version_v26():
    # Nommage final : le livrable est étiqueté v26 (décision Omar, 2026-07-05).
    assert APP_VERSION == "v29"


# ─────────────────────────────────────────────────────────────────────────────
# A3/B5 — flux ETF depuis Telegram (parsing déterministe)
# ─────────────────────────────────────────────────────────────────────────────
_TG_BTC = ("🟠 Bitcoin ETF Inflow : 2026-07-01 \n\n#IBIT : -$219.4M\n"
           "#GBTC : -$62.8M\n\n📊 Net Inflow : -$325.8M\n⚡ 7-day Avg : -$360.5M\n\n@ETF_FLOWS")
_TG_ETH = ("🔵 ETH ETF Inflow : 2026-07-01 \n\n#ETHA : $36.6M\n"
           "📊 Net Inflow : $14.8M\n⚡ 7-day Avg : -$35.7M\n\n@ETF_FLOWS")
_TG_SUMMARY = ("📊 ETF Flows : 01 Jul 2026\n\n🔻 BTC ETFs : -$325.8M\n"
               "🟢 ETH ETFs : $14.8M\n🟢 SOL ETFs : $500.0K\n\n"
               "🔴 Total: -$309.5M net outflows\n\n@ETF_FLOWS")


def _tg(messages):
    return {"available": True, "messages": messages}


def test_etf_telegram_parse_per_asset_posts():
    from src.data_sources import etf_flows as ef
    out = ef.parse_flows_from_telegram(_tg([
        {"channel": "etf_flows", "text": _TG_BTC, "timestamp": "2026-07-02T05:10:00+00:00"},
        {"channel": "etf_flows", "text": _TG_ETH, "timestamp": "2026-07-02T05:12:00+00:00"},
        {"channel": "etf_flows", "text": _TG_SUMMARY, "timestamp": "2026-07-02T05:25:00+00:00"},
    ]))
    assert out["available"] is True
    assert out["btc"]["total_flow_musd"] == -325.8
    assert out["btc"]["date"] == "2026-07-01"          # passe 1 prime sur le récap
    assert out["btc"]["avg_7d_musd"] == -360.5
    assert out["eth"]["total_flow_musd"] == 14.8
    assert out["eth"]["source"] == "Telegram · ETF_Flows"


def test_etf_telegram_summary_fills_gaps_and_units():
    from src.data_sources import etf_flows as ef
    # Seul le récap est présent : les deux actifs sont comblés depuis lui.
    out = ef.parse_flows_from_telegram(_tg([
        {"channel": "ETF_Flows", "text": _TG_SUMMARY, "timestamp": "2026-07-02T05:25:00+00:00"},
    ]))
    assert out["available"] and out["btc"]["total_flow_musd"] == -325.8
    assert out["eth"]["total_flow_musd"] == 14.8
    # Unité K → millions.
    assert ef._tg_amount_musd("", "500.0", "K") == 0.5
    assert ef._tg_amount_musd("-", "1.2", "B") == -1200.0


def test_etf_telegram_ignores_other_channels_and_garbage():
    from src.data_sources import etf_flows as ef
    out = ef.parse_flows_from_telegram(_tg([
        {"channel": "watcher_guru", "text": _TG_BTC, "timestamp": "2026-07-02T05:10:00+00:00"},
        {"channel": "etf_flows", "text": "gm ☀️", "timestamp": "2026-07-02T05:11:00+00:00"},
    ]))
    assert out["available"] is False and out["btc"] is None
    assert ef.parse_flows_from_telegram(None)["available"] is False


def test_etf_merge_prefers_direct_source(monkeypatch):
    from src.data_sources import etf_flows as ef
    base = {"available": True,
            "btc": {"date": "01 Jul 2026", "total_flow_musd": -325.8, "source": "Farside"},
            "eth": {"date": "01 Jul 2026", "total_flow_musd": 14.8, "source": "Farside"}}
    out = ef.merge_with_telegram(base, _tg([
        {"channel": "etf_flows", "text": _TG_BTC, "timestamp": "x"}]))
    assert out["btc"]["source"] == "Farside"           # jamais écrasé par un repli


def test_etf_merge_falls_back_to_telegram_then_preview(monkeypatch):
    from src.data_sources import etf_flows as ef
    base = {"available": False, "btc": None, "eth": None, "reason": "403"}
    out = ef.merge_with_telegram(base, _tg([
        {"channel": "etf_flows", "text": _TG_BTC, "timestamp": "x"},
        {"channel": "etf_flows", "text": _TG_ETH, "timestamp": "y"},
    ]))
    assert out["available"] is True and out["btc"]["total_flow_musd"] == -325.8
    assert "reason" not in out
    # Telethon vide → l'aperçu t.me est tenté (monkeypatché, pas de réseau).
    called = {}

    def _fake_preview():
        called["hit"] = True
        return {"available": True, "btc": {"total_flow_musd": -1.0}, "eth": None}

    monkeypatch.setattr(ef, "_flows_from_tme_preview", _fake_preview)
    out2 = ef.merge_with_telegram(base, {"available": False, "messages": []})
    assert called.get("hit") and out2["btc"]["total_flow_musd"] == -1.0


# ─────────────────────────────────────────────────────────────────────────────
# A1/A2/B19/B22/B7 — tuiles on-chain déterministes
# ─────────────────────────────────────────────────────────────────────────────
def _tile_data(**over):
    today = datetime.now(main.TZ).strftime("%Y-%m-%d")
    d = {
        "onchain_advanced": {"available": True, "assets": {
            "BTC": {"mvrv": 1.14, "mvrv_zone": "neutre", "as_of": today,
                    "active_addresses": 850000, "active_addresses_trend_pct": 0.2},
            "ETH": {"mvrv": 0.75, "mvrv_zone": "sous-évalué (capitulation)",
                    "as_of": "2026-05-23", "stale": True, "mvrv_live_estimate": True,
                    "active_addresses": 820999, "active_addresses_trend_pct": -12.4},
        }},
        "btc_active_addresses": {"available": True, "value": 858340,
                                 "trend_7d_pct": 2.1, "as_of": today},
        "options_deribit": {"available": True, "assets": {
            "BTC": {"put_call_ratio": 0.57, "max_pain": 61000,
                    "max_pain_gap_pct": -0.3, "dvol": 41.2},
            "ETH": {"dvol": 56.0},
        }},
        "stablecoin_supply": {"available": True, "total_mcap_usd": 262.2e9,
                              "total_change_7d_pct": 0.021},
        "whale_inflows": {"available": True, "large_inflows_count": 0,
                          "threshold_eth": 200.0, "total_eth_in": 0.0},
        "etf_flows": {"available": True,
                      "btc": {"date": "2026-07-01", "total_flow_musd": -325.8,
                              "avg_7d_musd": -360.5, "source": "Telegram · ETF_Flows"},
                      "eth": {"date": "2026-07-01", "total_flow_musd": 14.8,
                              "source": "Telegram · ETF_Flows"}},
        "btc_derivatives": {"available": True, "funding_rate_pct": 0.01,
                            "long_short_ratio": 1.42},
    }
    d.update(over)
    return d


def test_onchain_tiles_whale_zero_never_bare_zero():
    tiles, _ = _build_onchain_tiles(_tile_data())
    whale = next(t for t in tiles if "whales" in t["label"].lower())
    assert whale["value"] == "aucun ≥200 ETH"          # A1 : jamais « 0 » nu
    assert "pas de signal vendeur" in whale["short"]


def test_onchain_tiles_whale_unavailable_omitted():
    tiles, _ = _build_onchain_tiles(
        _tile_data(whale_inflows={"available": False, "reason": "quota"}))
    assert not any("whale" in t["label"].lower() for t in tiles)


def test_onchain_tiles_whale_active_inflows():
    tiles, _ = _build_onchain_tiles(_tile_data(whale_inflows={
        "available": True, "large_inflows_count": 3, "threshold_eth": 200.0,
        "total_eth_in": 2166.0}))
    whale = next(t for t in tiles if "whales" in t["label"].lower())
    assert "2 166 ETH" == whale["value"]
    assert "3 dépôts" in whale["short"]


def test_onchain_tiles_eth_dated_and_note_once():
    tiles, note = _build_onchain_tiles(_tile_data())
    eth_addr = next(t for t in tiles if t["label"] == "Adresses actives ETH")
    # A2 : delta ET date sur la même tuile, grisée.
    assert "-12.4% / 7j" in eth_addr["short"].replace("−", "-")
    assert "au 23/05" in eth_addr["short"]
    assert eth_addr["color"] == "#8a8880"
    assert note and "23/05" in note                     # note de fraîcheur unique
    mvrv_eth = next(t for t in tiles if t["label"] == "MVRV ETH")
    assert "estim. prix live" in mvrv_eth["short"]      # estimation libellée


def test_onchain_tiles_btc_addresses_fresh_pref():
    tiles, _ = _build_onchain_tiles(_tile_data())
    btc_addr = next(t for t in tiles if t["label"] == "Adresses actives BTC")
    assert btc_addr["value"] == "858 340"
    assert "+2.1% / 7j" in btc_addr["short"]
    assert "au " not in btc_addr["short"]               # fraîche → pas de date


def test_onchain_tiles_stablecoins_absolute_and_delta():
    tiles, _ = _build_onchain_tiles(_tile_data())
    st = next(t for t in tiles if t["label"] == "Supply Stablecoins")
    assert "Mds$" in st["value"] and "% / 7j" in st["short"]  # B22 : les deux


def test_onchain_tiles_etf_and_funding_and_cap():
    tiles, _ = _build_onchain_tiles(_tile_data())
    labels = [t["label"] for t in tiles]
    assert "Flux ETF BTC" in labels and "Flux ETF ETH" in labels
    etf_btc = next(t for t in tiles if t["label"] == "Flux ETF BTC")
    assert etf_btc["value"] == "−$325.8M" and "au 01/07" in etf_btc["short"]
    assert "Funding BTC" in labels
    assert len(tiles) <= 12
    mp = next(t for t in tiles if t["label"] == "Max Pain BTC")
    assert "neutre" in mp["short"]                      # |gap| < 1% → pas d'« aimant »


def test_onchain_tiles_max_pain_direction():
    d = _tile_data()
    d["options_deribit"]["assets"]["BTC"]["max_pain_gap_pct"] = 1.5
    tiles, _ = _build_onchain_tiles(d)
    mp = next(t for t in tiles if t["label"] == "Max Pain BTC")
    assert "aimant haussier" in mp["short"]
    d["options_deribit"]["assets"]["BTC"]["max_pain_gap_pct"] = -2.0
    tiles, _ = _build_onchain_tiles(d)
    mp = next(t for t in tiles if t["label"] == "Max Pain BTC")
    assert "aimant baissier" in mp["short"]


def test_merge_overrides_gemini_onchain_metrics():
    payload = {"onchain_indicators": {
        "metrics": [{"label": "Whale Inflows ETH", "value": "0",
                     "short": "pas de signal vendeur"}],
        "verdict": "neutre", "combined_reading": "Bilan neutre."}}
    out = _merge_python_facts(payload, _tile_data(), "02/07 · 08h35")
    metrics = out["onchain_indicators"]["metrics"]
    assert not any(m.get("value") == "0" for m in metrics)   # tuile Gemini écrasée
    assert any(m["label"] == "MVRV BTC" for m in metrics)
    assert out["onchain_indicators"]["verdict"] == "neutre"  # verdict Gemini gardé
    assert out["onchain_indicators"].get("freshness_note")


# ─────────────────────────────────────────────────────────────────────────────
# A4/A5/A20/B2/B4 — zéro-reco structuré
# ─────────────────────────────────────────────────────────────────────────────
def test_zero_reco_banner_and_bullets_split():
    payload = {
        "thesis_of_the_day": [],
        "thesis_empty_reason": ("Aucune thèse n'atteint le seuil ce matin. "
                                "* BTC : rebond technique sans catalyseur. "
                                "* ETH : activité adresses en baisse."),
    }
    data = {"active_recommendations_display": [{"asset": "TAO"}, {"asset": "ETH"}]}
    out = _merge_python_facts(payload, data, "02/07 · 12h35")
    assert "2 recos" in out["thesis_context_note"]           # A20/B4
    assert out["thesis_empty_reason"].startswith("Aucune thèse")
    assert " * " not in out["thesis_empty_reason"]           # A4 : plus de pavé
    assert out["thesis_empty_bullets"] == [
        "BTC : rebond technique sans catalyseur.",
        "ETH : activité adresses en baisse."]


def test_zero_reco_structured_assets_kept():
    payload = {
        "thesis_of_the_day": [],
        "thesis_empty_reason": "Rien d'assez convergent.",
        "no_thesis_assets": [
            {"asset": "BTC", "real_confidence_pct": 68, "cap_pct": 80,
             "why": "pas de catalyseur", "watch_level": "cassure $63,254"}],
    }
    out = _merge_python_facts(payload, {"active_recommendations_display": []}, "t")
    assert out["no_thesis_assets"][0]["real_confidence_pct"] == 68
    assert "thesis_context_note" not in out                  # 0 reco trackée → pas de bannière


def test_zero_reco_template_renders_structured():
    html = render({
        "header": {"active_sources_count": 20},
        "thesis_of_the_day": [],
        "thesis_context_note": "Aucune nouvelle reco ce matin — les 7 recos actives restent en vigueur.",
        "thesis_empty_reason": "Rien d'assez convergent ce matin.",
        "no_thesis_assets": [
            {"asset": "BTC", "real_confidence_pct": 68, "cap_pct": 80,
             "why": "pas de catalyseur fort", "watch_level": "repli vers $58,454"}],
    }, "morning")
    assert "7 recos actives restent en vigueur" in html
    assert "confiance" in html and "68%" in html and "plafond 80%" in html
    assert "repli vers $58,454" in html
    assert "* BTC" not in html                               # A4


# ─────────────────────────────────────────────────────────────────────────────
# A10 — filet NFP : férié US → jeudi
# ─────────────────────────────────────────────────────────────────────────────
def test_nfp_estimate_shifted_off_us_holiday():
    from src.data_sources import macro_calendar as mc
    # Juillet 2026 : 1er vendredi = 3 juillet, veille du 4 juillet (samedi)
    # → férié observé le vendredi → NFP estimé au JEUDI 2 juillet.
    evts = mc._recurring_estimates(date(2026, 6, 28), 10)
    nfp = [e for e in evts if "NFP" in e["label"]]
    assert nfp and nfp[0]["date"] == "2026-07-02"
    assert nfp[0]["estimated"] is True


def test_nfp_estimate_normal_month_unchanged():
    from src.data_sources import macro_calendar as mc
    # Août 2026 : 1er vendredi = 7 août, pas de férié → inchangé.
    evts = mc._recurring_estimates(date(2026, 8, 1), 10)
    nfp = [e for e in evts if "NFP" in e["label"]]
    assert nfp and nfp[0]["date"] == "2026-08-07"


# ─────────────────────────────────────────────────────────────────────────────
# B18/B21 — agenda macro 72h
# ─────────────────────────────────────────────────────────────────────────────
def test_macro_agenda_built_and_rendered():
    payload = {}
    data = {"upcoming_calendar": {"available": True, "events": [
        {"label": "Emploi US (NFP) (US)", "days_ahead": 0, "when": "aujourd'hui",
         "date_label": "jeudi 2 juillet", "time": "13:30", "importance": "high",
         "forecast": "114K", "previous": "172K"},
        {"label": "ISM Services (US)", "days_ahead": 1, "when": "demain",
         "time": "15:00", "importance": "medium", "forecast": "52.0",
         "previous": "51.8"},
        {"label": "PIB (Japon)", "days_ahead": 6, "when": "dans 6j",
         "importance": "high"},                              # hors fenêtre 72h
    ]}}
    out = _merge_python_facts(payload, data, "t")
    items = out["macro_agenda"]["events"]
    assert len(items) == 2                                   # J+6 exclu
    assert items[0]["label"].startswith("Emploi US")
    assert items[0]["forecast"] == "114K" and items[0]["previous"] == "172K"
    html = render({"header": {}, "macro_agenda": out["macro_agenda"]}, "morning")
    assert "Agenda macro · 72h" in html
    assert "cons." in html and "114K" in html and "172K" in html


def test_macro_agenda_cap_keeps_highs_chronological():
    evs = [{"label": f"Ev{i}", "days_ahead": i % 3, "when": "x",
            "importance": ("high" if i < 4 else "medium")} for i in range(10)]
    out = _merge_python_facts({}, {"upcoming_calendar":
                                   {"available": True, "events": evs}}, "t")
    items = out["macro_agenda"]["events"]
    assert len(items) == 6
    assert sum(1 for i in items if i["importance"] == "high") == 4  # aucun high perdu


# ─────────────────────────────────────────────────────────────────────────────
# A14/B17 — angles morts sans doublon
# ─────────────────────────────────────────────────────────────────────────────
def test_blind_spots_no_source_list_and_hidden_when_empty():
    out = main._blind_spots(macro_flags=None, price_discrepancies=None,
                            price_divergences=None)
    assert out == ""                                         # plus de remplissage
    out2 = main._blind_spots(macro_flags=["vix"])
    assert "VIX" in out2 and "Sources indisponibles" not in out2
    # payload : la clé disparaît quand Python n'a rien (Gemini écrasé).
    p = {"blind_spots": "Sources indisponibles : Kaito, LunarCrush."}
    merged = _merge_python_facts(p, {"blind_spots": ""}, "t")
    assert "blind_spots" not in merged


# ─────────────────────────────────────────────────────────────────────────────
# A12/B6 — tracking : cible persistée + progression vers la cible
# ─────────────────────────────────────────────────────────────────────────────
def test_persist_firm_recos_saves_target_and_stop(monkeypatch):
    from src.state import report_memory as mem
    saved = []
    monkeypatch.setattr(mem, "add_recommendation", lambda r: saved.append(r))
    monkeypatch.setattr(mem, "is_recently_dismissed", lambda a, c: False)
    payload = {"thesis_of_the_day": [{
        "asset": "TAO", "action": "RENFORCER", "confidence": 75,
        "targets": {"short_term_30d": 220.24},
        "action_plan": {"entry": 202.98, "stop_loss": 191.24},
    }]}
    data = {"all_positions_summary": [{"asset": "TAO", "price": 202.98}]}
    main._persist_firm_recos(payload, data)
    assert saved and saved[0]["ct_target"] == 220.24
    assert saved[0]["stop_loss"] == 191.24


def test_tracking_badge_path_based(monkeypatch):
    from src.tracking import prediction_scoring as ps
    from src.state import report_memory as mem
    reco = {"asset": "TAO", "action": "RENFORCER", "status": "in_progress",
            "entry_price": 200.0, "ct_target": 220.0, "stop_loss": 190.0,
            "created_at": "2026-07-02T08:00:00+00:00"}
    monkeypatch.setattr(mem, "load_active_recommendations", lambda: [reco])
    tracker = ps.PredictionTracker()
    # +4% de prix = 40% du chemin vers +10% → « En bonne voie », PAS « Sur objectif ».
    rows = tracker.active_for_display({"TAO": 208.0})
    row = rows[0]
    assert row["target_path_pct"] == 40
    assert "Sur objectif" not in (row["health_status"] or "")
    assert "En bonne voie" in row["health_status"]
    # +2% = 20% du chemin → Neutre (avant : « Sur objectif » dès +3%… faux).
    row = tracker.active_for_display({"TAO": 204.0})[0]
    assert "Neutre" in row["health_status"]
    # Cible touchée → Cible atteinte.
    row = tracker.active_for_display({"TAO": 221.0})[0]
    assert "Cible atteinte" in row["health_status"]
    # Près du stop → priorité au rouge.
    row = tracker.active_for_display({"TAO": 192.0})[0]
    assert "Stop approché" in row["health_status"]


def test_no_sur_objectif_badge_left():
    """Le BADGE « ✅ Sur objectif » (attribué dès +3% arbitraires) n'existe plus
    comme valeur de health_status (les commentaires du code peuvent le citer)."""
    import inspect
    from src.tracking import prediction_scoring as ps
    src = inspect.getsource(ps.PredictionTracker.active_for_display)
    assert '"✅ Sur objectif"' not in src


# ─────────────────────────────────────────────────────────────────────────────
# A15 — Polymarket : issue mesurée
# ─────────────────────────────────────────────────────────────────────────────
def test_polymarket_outcome_label():
    from src.data_sources import prediction_markets as pm
    assert pm._first_outcome_label({"outcomes": '["Up","Down"]'}) == "Up"
    assert pm._first_outcome_label({"outcomes": '["Yes","No"]'}) is None
    assert pm._first_outcome_label({"outcomes": ["Higher", "Lower"]}) == "Higher"
    assert pm._first_outcome_label({}) is None


# ─────────────────────────────────────────────────────────────────────────────
# C2 — alternatives sources
# ─────────────────────────────────────────────────────────────────────────────
def test_lunarcrush_falls_back_to_coingecko_trending(monkeypatch):
    from src.data_sources import lunarcrush as lc
    monkeypatch.delenv("LUNARCRUSH_PAID", raising=False)
    monkeypatch.setattr(lc, "get_json", lambda *a, **k: {"coins": [
        {"item": {"symbol": "pepe", "name": "Pepe", "market_cap_rank": 30}}]})
    if hasattr(lc.CACHE, "_store"):
        lc.CACHE._store.clear()
    out = lc.get_trending_coins()
    assert out["available"] is True
    assert out["trending"][0]["symbol"] == "PEPE"
    assert "CoinGecko" in out["source"]


def test_unlocks_from_coinmarketcal():
    from src.data_sources import token_unlocks as tu
    soon = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT00:00:00Z")
    events = {"available": True, "events": [
        {"title": "TAO Token Unlock (cliff)", "date": soon, "coins": ["TAO"]},
        {"title": "Mainnet launch", "date": soon, "coins": ["ARB"]},
    ]}
    out = tu.unlocks_from_coinmarketcal(events, days_ahead=30)
    assert out["available"] is True and out["source"] == "CoinMarketCal"
    assert out["unlocks"][0]["symbol"] == "TAO"
    assert out["unlocks"][0]["amount_usd"] is None           # jamais inventé
    out2 = tu.unlocks_from_coinmarketcal({"available": True, "events": [
        {"title": "Mainnet launch", "date": soon, "coins": ["ARB"]}]})
    assert out2["available"] is False


def test_btc_active_addresses_fresh(monkeypatch):
    from src.data_sources import bitcoin_data as bd
    values = [{"x": 1782000000 + i * 86400, "y": 800000 + i * 1000} for i in range(10)]
    monkeypatch.setattr(bd, "get_json",
                        lambda url, **k: ({"values": values}
                                          if "blockchain.info" in url else None))
    if hasattr(bd.CACHE, "_store"):
        bd.CACHE._store.clear()
    out = bd.get_btc_active_addresses()
    assert out["available"] is True and out["value"] == 809000
    assert out["trend_7d_pct"] is not None and out["source"] == "blockchain.info"


def test_btc_active_addresses_fallback_bitcoin_data(monkeypatch):
    from src.data_sources import bitcoin_data as bd
    def fake(url, **k):
        if "blockchain.info" in url:
            return None
        return {"d": "2026-07-01", "activeAddresses": 847114.0}
    monkeypatch.setattr(bd, "get_json", fake)
    if hasattr(bd.CACHE, "_store"):
        bd.CACHE._store.clear()
    out = bd.get_btc_active_addresses()
    assert out["available"] is True and out["value"] == 847114
    assert out["source"] == "bitcoin-data.com" and out["as_of"] == "2026-07-01"


def test_sources_labels_updated():
    assert "ETF flows" in main._ALL_SOURCES_LIST
    assert "ETF flows (Farside)" not in main._ALL_SOURCES_LIST
    assert "Social trending" in main._ALL_SOURCES_LIST
    assert "LunarCrush" not in main._ALL_SOURCES_LIST


# ─────────────────────────────────────────────────────────────────────────────
# B8/A20 — graphiques de suivi
# ─────────────────────────────────────────────────────────────────────────────
def test_tracking_chart_usefulness_selection():
    from src.reporting import charts
    assert charts._tracking_chart_is_useful({
        "asset": "TAO", "entry_price": 200, "ct_target": 220,
        "target_path_pct": 65, "progress_pct": 6.5}) is True
    assert charts._tracking_chart_is_useful({
        "asset": "TAO", "entry_price": 200, "ct_target": 220,
        "target_path_pct": 10, "progress_pct": 1.0}) is False   # rien à montrer
    assert charts._tracking_chart_is_useful({
        "asset": "TAO", "progress_pct": 50}) is False           # pas de plan
    assert charts._tracking_chart_is_useful({
        "asset": "BTC", "entry_price": 60000, "stop_loss": 58000,
        "health_status": "🔴 Stop approché", "progress_pct": -2}) is True


def test_charts_for_tracked_recos_renders_png(monkeypatch):
    from src.reporting import charts
    closes = [100 + (i % 7) for i in range(180)]
    monkeypatch.setattr(charts, "_load_series",
                        lambda sym, days=180: {"closes": closes,
                                               "volumes": [1000.0] * 180})
    out = charts.charts_for_tracked_recos([
        {"asset": "TAO", "action": "RENFORCER", "entry_price": 100,
         "ct_target": 110, "stop_loss": 95, "progress_pct": 6.0,
         "target_path_pct": 60},
    ], limit=2)
    assert "TAO" in out and out["TAO"][:8] == b"\x89PNG\r\n\x1a\n"


def test_tracking_chart_rendered_in_template():
    html = render({
        "header": {},
        "active_recommendations_tracking": [
            {"asset": "TAO", "action": "RENFORCER", "entry_price": 202.98,
             "ct_target": 220.24, "progress_pct": 4.1, "target_path_pct": 48,
             "dist_to_target_pct": 4.2, "health_status": "🟢 En bonne voie",
             "health_color": "#3B6D11"}],
    }, "morning", charts={"track_TAO": b"png"})
    assert "cid:chart_track_TAO" in html
    # v29 (MB7) — le suivi passe en table compacte + BARRE de progression : le
    # « X% du chemin » (affiché en double avant) devient la barre + « 48% ».
    assert "du chemin" not in html
    assert "48%" in html
    assert "En bonne voie" in html


# ─────────────────────────────────────────────────────────────────────────────
# A17 — libellés hors-cycle (template)
# ─────────────────────────────────────────────────────────────────────────────
def test_offcycle_labels_rendered():
    html = render({
        "header": {"subtitle_context": "run hors-cycle · point intrajournalier",
                   "firm_theses_count": 0},
        "portfolio_snapshot": {"value_usd": 2635.0,
                               "overnight_label": "P&L depuis dernier rapport",
                               "overnight_pnl_pct": 2.1},
    }, "morning")
    assert "P&amp;L depuis dernier rapport" in html
    assert "run hors-cycle · point intrajournalier" in html
    assert "synthèse de la nuit" not in html


def test_default_labels_unchanged():
    html = render({"header": {}, "portfolio_snapshot": {"value_usd": 2635.0}},
                  "morning")
    assert "P&amp;L nuit" in html and "synthèse de la nuit" in html


# ─────────────────────────────────────────────────────────────────────────────
# Grille on-chain : rendu bout-en-bout (note de fraîcheur affichée une fois)
# ─────────────────────────────────────────────────────────────────────────────
def test_onchain_grid_rendered_with_note():
    tiles, note = _build_onchain_tiles(_tile_data())
    html = render({"header": {},
                   "onchain_indicators": {"metrics": tiles,
                                          "freshness_note": note,
                                          "verdict": "neutre",
                                          "combined_reading": "Bilan neutre."}},
                  "morning")
    assert "MVRV BTC" in html and "Flux ETF BTC" in html
    assert html.count("Données différées") == 1
    assert "aucun ≥200 ETH" in html
