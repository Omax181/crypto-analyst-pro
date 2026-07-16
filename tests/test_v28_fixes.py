# -*- coding: utf-8 -*-
"""v28 — verrous des correctifs de l'audit du 07/07 (mails v27 en production).

Couvre notamment :
  P1 (4.3)  — Coin Metrics : api_key toujours transmis (même vide), batch
              community validé, API tentée MÊME sur GitHub Actions, realized
              price dérivé prix/MVRV, fraîcheur as_of/stale sur le chemin API.
Les phases suivantes (P2…P11) ajoutent leurs verrous dans ce même fichier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# --------------------------------------------------------------------------- #
# P1 · Coin Metrics (4.3)
# --------------------------------------------------------------------------- #
def _fresh_rows(days_ago_last: int = 1) -> list[dict]:
    """Lignes API synthétiques BTC+ETH, la dernière datée de J-``days_ago_last``."""
    rows = []
    for i in range(9, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=days_ago_last + i))
        for asset, price, mvrv, adr in (("btc", "63000", "1.22", "500000"),
                                        ("eth", "1800", "0.90", "850000")):
            rows.append({
                "asset": asset, "time": d.strftime("%Y-%m-%dT00:00:00Z"),
                "PriceUSD": price, "CapMVRVCur": mvrv, "AdrActCnt": adr,
            })
    return rows


def _neutralize_overlays(monkeypatch):
    from src.data_sources import bitcoin_data
    monkeypatch.setattr(bitcoin_data, "get_btc_mvrv", lambda: {"available": False})


def test_cm_api_key_param_always_present_keyless(monkeypatch):
    """Tier community : « api_key= » vide DOIT être transmis (absent → 403,
    vérifié en live le 07/07/2026) et le batch = lot community validé."""
    from src.data_sources import coinmetrics as cm

    monkeypatch.delenv("COINMETRICS_API_KEY", raising=False)
    _neutralize_overlays(monkeypatch)
    cm.CACHE._store.clear()
    seen: list[dict] = []

    def fake_get_json(url, params=None, **kw):
        seen.append({"url": url, "params": dict(params or {})})
        return {"data": _fresh_rows()}

    monkeypatch.setattr(cm, "get_json", fake_get_json)
    out = cm.get_onchain_metrics()
    assert out["available"] is True and out["source"] == "coinmetrics"
    assert seen, "l'API doit être appelée"
    p = seen[0]["params"]
    assert "api_key" in p and p["api_key"] == ""  # présent ET vide
    assert p["metrics"] == ",".join(cm._METRICS_COMMUNITY)
    assert "NVTAdj" not in p["metrics"]  # 403 garanti sur le tier community
    assert seen[0]["url"] == cm._BASE_COMMUNITY


def test_cm_api_tried_even_on_github_actions(monkeypatch):
    """v28 : le raccourci « Actions sans clé → miroir direct » est supprimé —
    l'API keyless sert des données J-1 (cause du gel on-chain ETH au 23/05)."""
    from src.data_sources import coinmetrics as cm

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("COINMETRICS_API_KEY", raising=False)
    _neutralize_overlays(monkeypatch)
    cm.CACHE._store.clear()
    monkeypatch.setattr(cm, "get_json", lambda *a, **k: {"data": _fresh_rows()})
    monkeypatch.setattr(
        cm, "_fetch_mirror_asset",
        lambda cm_id: (_ for _ in ()).throw(AssertionError("miroir interdit ici")))
    out = cm.get_onchain_metrics()
    assert out["available"] is True
    assert out["source"] == "coinmetrics"  # API, PAS le miroir
    assert out["assets"]["ETH"]["mvrv"] == 0.9


def test_cm_authenticated_uses_full_batch(monkeypatch):
    from src.data_sources import coinmetrics as cm

    monkeypatch.setenv("COINMETRICS_API_KEY", "k-test")
    _neutralize_overlays(monkeypatch)
    cm.CACHE._store.clear()
    seen: list[dict] = []

    def fake_get_json(url, params=None, **kw):
        seen.append({"url": url, "params": dict(params or {})})
        return {"data": _fresh_rows()}

    monkeypatch.setattr(cm, "get_json", fake_get_json)
    out = cm.get_onchain_metrics()
    assert out["available"] is True
    assert seen[0]["url"] == cm._BASE_AUTH
    assert seen[0]["params"]["api_key"] == "k-test"
    assert seen[0]["params"]["metrics"] == ",".join(cm._METRICS_AUTH)


def test_cm_realized_price_derived_without_caprealusd(monkeypatch):
    """Tier community (pas de CapRealUSD) : realized = prix / MVRV, dérivé."""
    from src.data_sources import coinmetrics as cm

    monkeypatch.delenv("COINMETRICS_API_KEY", raising=False)
    _neutralize_overlays(monkeypatch)
    cm.CACHE._store.clear()
    monkeypatch.setattr(cm, "get_json", lambda *a, **k: {"data": _fresh_rows()})
    out = cm.get_onchain_metrics()
    eth = out["assets"]["ETH"]
    assert eth["realized_price"] == round(1800 / 0.90, 2)
    assert eth["realized_price_ratio"] == 0.9
    assert "nvt" not in eth  # jamais inventé (non servi sans clé)


# --------------------------------------------------------------------------- #
# P10/P11 · Weekly template v25 strict + chart PTF + news FR + « · · » (3.B, W-A7)
# --------------------------------------------------------------------------- #
def test_ptf_evolution_chart_endpoint_label_not_clipped(_mpl=None):
    """3.B — le chart PTF se rend ; label endpoint dans la marge droite
    réservée (xlim élargi), plus de rognure comme le 07/07."""
    import importlib.util
    if importlib.util.find_spec("matplotlib") is None:
        import pytest
        pytest.skip("matplotlib absent")
    from src.reporting import charts

    vals = [2608, 2561, 2690, 2770, 2600, 2455, 2452, 2600, 2716]
    pts = [{"label": ("≈30j" if i == 0 else ("auj." if i == len(vals) - 1 else "")),
            "value": v} for i, v in enumerate(vals)]
    png = charts.portfolio_evolution_png(pts)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    png2 = charts.portfolio_evolution_png(pts, btc_points=[60000 + i * 300
                                                           for i in range(len(vals))])
    assert png2 and png2[:8] == b"\x89PNG\r\n\x1a\n"


def test_weekly_news_double_middot_fixed_and_fr_preferred():
    """W-A7 : date_label absent ne produit plus « · · Source » ; le titre FR
    (title_fr) prime sur l'anglais quand fourni."""
    from src.reporting import email_html

    payload = {"weekly_news_digest": [
        {"title": "Bitcoin needs trillions to go parabolic", "source": "CryptoSlate"},
        {"title": "ETFs drew inflows on Monday",
         "title_fr": "Les ETF ont enregistré des entrées lundi",
         "source": "CoinDesk", "date_label": "lun."},
    ]}
    html = email_html.render(payload, "weekly")
    assert "· · " not in html                       # plus de double middot
    assert "· CryptoSlate" in html                  # méta propre (source seule)
    assert "entrées lundi" in html                  # titre FR préféré
    assert "ETFs drew inflows" not in html          # l'anglais est masqué


def test_weekly_render_v25_strict_no_extra_charts():
    """3.B — même en fournissant tous les charts v26/v27, seul le PTF est rendu."""
    from src.reporting import email_html

    payload = {"portfolio_snapshot": {"value_usd": 2716},
               "sector_exposure_cells": [{"sector": "L1", "ptf_pct": 66.7}],
               "portfolio_heatmap_7d": {"cells": [{"symbol": "BTC",
                                        "change_24h": 6.0, "ptf_pct": 42.0}]},
               "correlation_summary_line": "Corrélation moyenne entre positions (30 j) : 0.68 — élevée — diversification limitée."}
    html = email_html.render(payload, "weekly", charts={
        "ptf_evolution": b"x", "sector_donut": b"x", "corr_heatmap": b"x",
        "funding_hist": b"x", "health_gauge": b"x", "perf_bars_7d": b"x",
        "fng_sparkline": b"x", "btc_levels": b"x"})
    assert "cid:chart_ptf_evolution" in html
    assert "cid:chart_corr_heatmap" not in html
    assert "Corrélation moyenne entre positions" in html  # matrice → phrase


# --------------------------------------------------------------------------- #
# P9 · Evening : fenêtre courte, marché US fermé, statuts anti-bruit (2.A + 2.B)
# --------------------------------------------------------------------------- #
def test_next_report_label_evening_offcycle(monkeypatch):
    """E-A1 (repro 07/07) : un soir lancé à 09h46 annonce « aujourd'hui 20h00 »,
    pas « demain 08h30 » ; le run normal de 20h05 garde « demain 08h30 »."""
    import src.main as m
    from datetime import datetime as _dt

    class _FakeDT(_dt):
        _now = None

        @classmethod
        def now(cls, tz=None):
            return cls._now.astimezone(tz) if tz else cls._now

    monkeypatch.setattr(m, "datetime", _FakeDT)
    _FakeDT._now = _dt(2026, 7, 7, 9, 46, tzinfo=m.TZ)
    assert m._next_report_label("evening") == "aujourd'hui 20h00"
    _FakeDT._now = _dt(2026, 7, 7, 20, 5, tzinfo=m.TZ)
    assert m._next_report_label("evening") == "demain 08h30"
    _FakeDT._now = _dt(2026, 7, 7, 3, 0, tzinfo=m.TZ)
    assert m._next_report_label("evening") == "aujourd'hui 08h30"


def test_reco_bilan_status_thresholds():
    """E-A5/E-A6 (repro 07/07) : −0,5% après 68 min = « stable » (bruit), plus
    « sous pression » ; +0,0% n'est plus « au-dessus de l'entrée »."""
    from src.main import _reco_bilan_status

    st, reason = _reco_bilan_status("RENFORCER", 1777.16, 1767.81, 1621.30)
    assert st == "stable" and "proche de l'entrée" in reason  # −0,53%
    st2, _ = _reco_bilan_status("RENFORCER", 100.0, 98.0, 90.0)
    assert st2 == "under_pressure"                            # −2%
    st3, r3 = _reco_bilan_status("RENFORCER", 100.0, 101.0, 90.0)
    assert st3 == "on_track" and "au-dessus de l'entrée" in r3  # +1%
    st4, r4 = _reco_bilan_status("RENFORCER", 4.75, 4.75, 4.49)
    assert st4 == "stable"                                    # +0,0% ≠ au-dessus
    # La raison ne répète plus « repli sous l'entrée · invalidé sous … » :
    assert "invalidation" in r4 and "invalidé sous" not in r4
    st5, _ = _reco_bilan_status("RENFORCER", 100.0, 85.0, 90.0)
    assert st5 == "invalidated"                               # stop franchi


def test_equity_catalysts_filtered_when_market_closed():
    """E-A2/E-A3 + 2.B (repro 07/07) : hors séance US, les « NOUVEAU » équities
    de la veille sont remplacés par une ligne « rien de neuf »."""
    from src.main import _filter_equity_catalysts_when_closed

    mc = [
        {"status": "new", "tag": "Catalyseur",
         "description": "AMD affiche une forte hausse de +6.61% en séance."},
        {"status": "new", "tag": "Catalyseur",
         "description": "TSM progresse de +4.06% en séance."},
        {"status": "new", "tag": "Info",
         "description": "Coinbase (COIN) et Marathon (MARA) en hausse en séance."},
        {"status": "unchanged", "tag": "Info",
         "description": "Pas d'évolution significative crypto."},
        {"status": "new", "tag": "Macro",
         "description": "La banque centrale chinoise ajoute de l'or."},
    ]
    out = _filter_equity_catalysts_when_closed(mc)
    descs = " | ".join(str(c.get("description")) for c in out)
    assert "AMD" not in descs and "TSM" not in descs and "COIN" not in descs
    assert "banque centrale chinoise" in descs   # macro non-équities conservé
    assert "rien de neuf depuis ce matin" in descs
    assert "en séance" not in descs              # plus aucun « en séance »


def test_evening_template_time_aware_labels():
    """E-A1/E-A2 : libellés calculés — matinée = pas de décor nocturne."""
    from src.reporting import email_html

    morning_run = {"header": {
        "us_market_open": False, "is_evening_slot": False,
        "closing_greeting": "Bonne journée.",
        "levels_window_label": "Niveaux à surveiller",
        "checklist_title": "Prochain rapport · check list"},
        # le bloc marchés ne se rend que si evening_macro porte des données
        "evening_macro": {"btc_price": 63010},
        "tomorrow_checklist": {"checks": "BTC 63k ?"},
        "levels_tonight": [{"asset": "BTC"}]}
    html = email_html.render(morning_run, "evening")
    assert "Bonne journée." in html and "Bonne soirée." not in html
    assert "cette nuit" not in html
    assert "instantané" in html and "mi-séance" not in html
    assert "Prochain rapport · check list".upper() in html.upper()
    # Run du soir : décor nocturne conservé.
    evening_run = {"header": {"us_market_open": True, "is_evening_slot": True,
                              "closing_greeting": "Bonne soirée.",
                              "levels_window_label": "Niveaux à surveiller cette nuit",
                              "checklist_title": "Demain matin · check list"},
                   "evening_macro": {"btc_price": 63010},
                   "levels_tonight": [{"asset": "BTC"}]}
    html2 = email_html.render(evening_run, "evening")
    assert "Bonne soirée." in html2 and "cette nuit" in html2
    assert "mi-séance" in html2


def test_evening_stable_status_rendered():
    from src.reporting import email_html

    payload = {"reco_bilan": [{"asset": "ETH", "action": "RENFORCER",
                               "entry": 1777.16, "current": 1767.81,
                               "delta_pct": -0.53, "status": "stable",
                               "reason": "proche de l'entrée (bruit court terme)"}]}
    html = email_html.render(payload, "evening")
    assert "● stable" in html
    assert "sous pression" not in html


# --------------------------------------------------------------------------- #
# P7/P8 · Morning rendu : MVRV daté, rotation, tracking, whale, Polymarket,
#         régimes distincts, jauge F&G supprimée
# --------------------------------------------------------------------------- #
def test_mvrv_stale_signal_halved_and_dated():
    """M-A7 (repro ETH 07/07) : MVRV du 23/05 → poids 2 (pas 3) + date."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    fresh = evaluate_thesis_eligibility({}, tier=0, mvrv=0.81)
    sig_f = next(s for s in fresh["signals"] if "MVRV" in s["label"])
    assert sig_f["weight"] == 3 and "au " not in sig_f["label"]

    stale = evaluate_thesis_eligibility(
        {}, tier=0, mvrv=0.81, mvrv_stale=True, mvrv_as_of="2026-05-23")
    sig_s = next(s for s in stale["signals"] if "MVRV" in s["label"])
    assert sig_s["weight"] == 2
    assert "au 23/05" in sig_s["label"]


def test_rotation_grt_merged_into_infra_and_tiles_shared():
    """M-A9 : plus de « Indexing/Infra » vs « Infra » côte à côte ; la sélection
    de tuiles est UNE fonction partagée (prompt = rendu)."""
    from src.analytics.narratives import NARRATIVES
    from src.main import _select_rotation_tiles

    assert NARRATIVES["GRT"] == "Infra"
    assert "Indexing/Infra" not in set(NARRATIVES.values())
    sec = {f"S{i}": {"avg_change_24h": i - 3.0, "avg_change_7d": 1.0,
                     "avg_change_30d": -1.0, "members": [f"A{i}"]}
           for i in range(8)}
    tiles = _select_rotation_tiles(sec)
    assert len(tiles) == 5  # 4 + agrégat
    assert tiles[-1].get("is_aggregate") is True


def test_tracking_target_fallback_from_plan(monkeypatch):
    """M-A13 (repro INJ 07/07) : reco legacy sans cible → cible 30j du plan du
    jour, étiquetée fallback."""
    from src.tracking import prediction_scoring as ps

    reco = {"asset": "INJ", "action": "RENFORCER", "status": "in_progress",
            "created_at": "2026-07-02T08:00:00+00:00", "entry_price": 4.54}
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [reco])
    tracker = ps.PredictionTracker()
    rows = tracker.active_for_display({"INJ": 4.75},
                                      target_fallbacks={"INJ": 4.96})
    inj = rows[0]
    assert inj["ct_target"] == 4.96 and inj["ct_target_fallback"] is True
    # Sans fallback : comportement inchangé (cible absente).
    rows2 = tracker.active_for_display({"INJ": 4.75})
    assert rows2[0]["ct_target"] is None
    assert rows2[0]["ct_target_fallback"] is False


def test_whale_small_flow_is_neutral():
    """M-A14 (repro 07/07) : 200 ETH = flux négligeable, pas de « pression
    vendeuse » ; ≥ 1000 ETH garde le signal ambre."""
    from src.main import _build_onchain_tiles

    small = {"whale_inflows": {"available": True, "large_inflows_count": 1,
                               "threshold_eth": 200, "total_eth_in": 200}}
    tiles, _ = _build_onchain_tiles(small)
    wh = next(t for t in tiles if t["label"].startswith("Dépôts whales"))
    assert "négligeable" in wh["short"]
    assert "pression vendeuse" not in wh["short"]

    big = {"whale_inflows": {"available": True, "large_inflows_count": 4,
                             "threshold_eth": 200, "total_eth_in": 4200}}
    tiles2, _ = _build_onchain_tiles(big)
    wh2 = next(t for t in tiles2 if t["label"].startswith("Dépôts whales"))
    assert "pression vendeuse possible" in wh2["short"]


def test_dedupe_extra_markets_theme_and_cap():
    """1.B (repro 07/07) : 4 marchés dont 3 « BTC above $X » → 2 lignes max,
    un seul par thème."""
    from src.main import _dedupe_extra_markets

    markets = [
        {"question": "Will Bitcoin reach $65,000 in July?", "probability_pct": 78},
        {"question": "Bitcoin Up or Down on July 7?", "probability_pct": 42},
        {"question": "Will the price of Bitcoin be above $64,000 on July 7?",
         "probability_pct": 18},
        {"question": "Will the price of Bitcoin be above $62,000 on July 7?",
         "probability_pct": 93},
    ]
    out = _dedupe_extra_markets(markets, cap=2)
    assert len(out) == 2
    assert out[0]["question"].startswith("Will Bitcoin reach")
    assert out[1]["question"].startswith("Bitcoin Up or Down")
    # Les deux « above $X » (même thème) ont été fusionnés/écartés.


def test_regime_labels_disambiguated():
    """M-A5 : « Régime BTC (technique) » ET « Régime macro » nommés — plus
    deux « Régime » contradictoires en apparence."""
    from src.reporting import email_html

    payload = {
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "baissier", "days_in_regime": 1},
        "macro_regime_readout": {"regime": "transition", "confidence_pct": 70},
        "macro_context": {"fear_greed": 27},
    }
    html = email_html.render(payload, "morning")
    assert "Régime BTC (technique) : baissier" in html
    assert "Régime macro : transition" in html


def test_morning_has_no_fng_gauge_reference():
    """1.B : la jauge F&G est retirée du template morning (texte uniquement)."""
    tpl = open("src/reporting/templates/report_morning.html.j2",
               encoding="utf-8").read()
    assert "fng_gauge" not in tpl


# --------------------------------------------------------------------------- #
# P6 · Reco gate par type + one-thing exécutable (M-A1/A2/A3/A4)
# --------------------------------------------------------------------------- #
def _thesis(asset, action="RENFORCER", ttype="tactical", ev=2.0, rr=1.6,
            size_pct=1.0, confidence=75):
    return {
        "asset": asset, "action": action, "thesis_type": ttype,
        "confidence": confidence, "action_type": "bullish",
        "asset_plan": {"available": True, "ev_30d_pct": ev, "rr_30d": rr,
                       "invalidation": {"level_label": "100 $"}},
        "action_plan": {"position_size_pct": size_pct,
                        "sizing_note": (f"+{size_pct}% du PTF" if size_pct
                                        else "déjà 13% du PTF (plafond 12%) — "
                                             "renfort non suggéré, concentration")},
    }


def test_gate_cap_reached_becomes_maintenir():
    """M-A1/A2 (repro 07/07) : sizing 0% (plafond) → MAINTENIR, jamais
    « RENFORCER · Taille +0.0% »."""
    from src.analytics.reco_gate import apply_reco_gate

    p = {"thesis_of_the_day": [_thesis("TAO", ttype="conviction", size_pct=0.0)]}
    fixes = apply_reco_gate(p)
    t = p["thesis_of_the_day"][0]
    assert t["action"] == "MAINTENIR"
    assert "plafond" in t["gate_note"]
    assert fixes


def test_gate_tactical_negative_ev_becomes_surveiller():
    """M-A3/A4 (repro BTC 07/07) : tactique avec R:R 1.0 → SURVEILLER."""
    from src.analytics.reco_gate import apply_reco_gate

    p = {"thesis_of_the_day": [_thesis("BTC", ttype="tactical", ev=0.1, rr=1.0)]}
    apply_reco_gate(p)
    t = p["thesis_of_the_day"][0]
    assert t["action"] == "SURVEILLER"
    assert "défavorables" in t["gate_note"]


def test_gate_conviction_keeps_renforcer_with_warning():
    """M-A3 (repro ETH 07/07) : conviction LT à EV −0.7% → RENFORCER conservé
    (DCA) mais confiance ≤ 70 et mention CT frontale."""
    from src.analytics.reco_gate import apply_reco_gate

    p = {"thesis_of_the_day": [
        _thesis("ETH", ttype="conviction", ev=-0.7, rr=0.7, confidence=78)]}
    apply_reco_gate(p)
    t = p["thesis_of_the_day"][0]
    assert t["action"] == "RENFORCER"          # la thèse LT survit
    assert t["confidence"] == 70               # plafonnée
    assert "défavorables" in t["ct_warning"]   # mention frontale


def test_gate_healthy_reco_untouched():
    from src.analytics.reco_gate import apply_reco_gate

    p = {"thesis_of_the_day": [_thesis("TAO", ev=2.3, rr=1.6, size_pct=1.0)]}
    fixes = apply_reco_gate(p)
    t = p["thesis_of_the_day"][0]
    assert t["action"] == "RENFORCER" and not fixes
    assert "ct_warning" not in t and t["confidence"] == 75


def test_top_action_skips_non_executable_and_says_nothing_to_do():
    """M-A1 (repro 07/07) : le « one thing » ne pousse plus un actif plafonné ;
    aucun geste exécutable → « Ne rien faire aujourd'hui »."""
    from src.main import _compute_top_action
    from src.analytics.reco_gate import apply_reco_gate

    # Cas 07/07 : TAO plafonné (meilleur R:R), ETH conviction EV<0, BTC tactique R:R 1.0.
    p = {"thesis_of_the_day": [
        _thesis("TAO", ttype="conviction", ev=2.3, rr=1.6, size_pct=0.0),
        _thesis("ETH", ttype="conviction", ev=-0.7, rr=0.7),
        _thesis("BTC", ttype="tactical", ev=0.1, rr=1.0),
    ]}
    apply_reco_gate(p)
    _compute_top_action(p)
    assert p["top_action"]["is_nothing"] is True
    assert "Ne rien faire aujourd'hui" in p["top_action"]["line"]
    # Un candidat sain existe → il gagne, pas le « ne rien faire ».
    p2 = {"thesis_of_the_day": [
        _thesis("TAO", size_pct=0.0),
        _thesis("LINK", ev=1.8, rr=1.5, size_pct=1.0),
    ]}
    apply_reco_gate(p2)
    _compute_top_action(p2)
    assert p2["top_action"].get("is_nothing") is None
    assert p2["top_action"]["asset"] == "LINK"


def test_morning_template_renders_maintenir_and_nothing_to_do():
    from src.reporting import email_html

    t = _thesis("TAO", ttype="conviction", size_pct=0.0)
    t["action"] = "MAINTENIR"
    t["gate_note"] = "déjà 13% du PTF (plafond 12%) — renfort non suggéré"
    payload = {"thesis_of_the_day": [t],
               "top_action": {"is_nothing": True, "line": "Ne rien faire aujourd'hui — test."}}
    html = email_html.render(payload, "morning")
    assert "MAINTENIR" in html
    assert "renfort non suggéré" in html
    # L'apostrophe est échappée en HTML (&#39;) par le filtre markdown : on
    # vérifie le fragment sans apostrophe + l'icône ⏸ du bloc « rien à faire ».
    assert "Ne rien faire" in html and "⏸" in html
    assert "+0.0% du portefeuille" not in html  # l'incohérence du 07/07 a disparu


# --------------------------------------------------------------------------- #
# P5 · Cross-mail : funding /an, cibles LT uniques, win rate calibration
#      (W-A11, M-A15)
# --------------------------------------------------------------------------- #
def test_funding_tile_annualized_unit():
    """W-A11 : la tuile matin affiche l'ANNUALISÉ « /an » (unité du hebdo),
    repli « /8h » explicite si l'annualisé manque."""
    from src.main import _build_onchain_tiles

    base = {"btc_derivatives": {"available": True, "funding_rate_pct": 0.007,
                                "funding_annualized_pct": 6.06,
                                "long_short_ratio": 1.52}}
    tiles, _ = _build_onchain_tiles(base)
    fr = next(t for t in tiles if t["label"] == "Funding BTC")
    assert fr["value"] == "+6,1%/an"
    assert "L/S 1,52" in fr["short"]
    # Sans annualisé → repli 8h étiqueté (jamais un chiffre sans unité).
    tiles2, _ = _build_onchain_tiles(
        {"btc_derivatives": {"available": True, "funding_rate_pct": 0.007}})
    fr2 = next(t for t in tiles2 if t["label"] == "Funding BTC")
    assert fr2["value"].endswith("%/8h")


def test_evening_derivatives_line_annualized():
    from src.main import _build_evening_derivatives_line

    line = _build_evening_derivatives_line(
        {"available": True, "funding_rate_pct": 0.006,
         "funding_annualized_pct": 6.57, "long_short_ratio": 1.52}, None)
    assert "/an" in line and "6,6" in line
    line2 = _build_evening_derivatives_line(
        {"available": True, "funding_rate_pct": 0.006}, None)
    assert "/8h" in line2


def test_positions_review_uses_deterministic_cycle_targets():
    """W-A11 : la cible LT du tableau hebdo = fourchette asset_plan (source du
    matin), la cible LLM divergente ne sert plus que de repli."""
    from src.main import _build_positions_review

    portfolio = {"ETH": {"pru": 3100.0, "value_usd": 500.0}}
    market = {"ETH": {"price": 1777.0}}
    long_term = [{"asset": "ETH", "status": "accumulation",
                  "target_price": 3500.0, "analysis": "thèse LT"}]
    plans = {"ETH": {"available": True, "target_cycle": {
        "low": 3736.0, "high": 4946.0, "upside_pct": 178.0, "kind": "6-12m"}}}
    rows = _build_positions_review(long_term, [], portfolio, market,
                                   ath_facts={"ETH": {"from_ath_pct": -64.2,
                                                      "ath": 4946.0}},
                                   asset_plans=plans)
    eth = rows[0]
    assert eth["lt_target_low"] == 3736.0 and eth["lt_target_high"] == 4946.0
    assert eth["lt_target_pct"] == 178.0
    assert eth["lt_target"] is None  # la cible LLM (3500) n'est plus affichée
    # Sans plan → repli LLM v26 (cap ATH) inchangé.
    rows2 = _build_positions_review(long_term, [], portfolio, market,
                                    ath_facts={"ETH": {"from_ath_pct": -64.2,
                                                       "ath": 4946.0}})
    assert rows2[0]["lt_target"] == 3500.0


def test_win_rate_headers_show_calibration_under_5():
    """M-A15 : plus de « 100% » géant sur 2/2 — « en calibration (2/2) »
    dans les trois mails tant que < 5 clôtures."""
    from src.reporting import email_html

    hdr = {"header": {"win_rate_30d": 100, "win_rate_total": "2/2"}}
    for kind in ("morning", "evening", "weekly"):
        html = email_html.render(dict(hdr), kind)
        assert "en calibration (2/2)" in html, kind
        # « 100%< » = valeur RENDUE (les width:100%; CSS ne matchent pas).
        assert "100%<" not in html, kind
    # ≥ 5 clôtures : le % réapparaît, en couleur.
    hdr5 = {"header": {"win_rate_30d": 80, "win_rate_total": "4/5"}}
    html5 = email_html.render(dict(hdr5), "morning")
    assert "80%" in html5 and "en calibration" not in html5


# --------------------------------------------------------------------------- #
# P4 · Sources honnêtes : alias ETF, « rétablie ce jour », libellé J-1 (M-A6,
#      W-A8, W-A9)
# --------------------------------------------------------------------------- #
def _health_logs(days_down_map: dict[str, list[int]], today=None):
    """Logs santé synthétiques : {source: [âges en jours où DOWN]} sur 8 jours."""
    today = today or datetime.now(timezone.utc)
    logs = []
    for age in range(8, -1, -1):
        d = today - timedelta(days=age)
        down = [src for src, ages in days_down_map.items() if age in ages]
        logs.append({"date": d.isoformat(), "down": down})
    return logs


def test_blind_spots_merges_source_aliases(monkeypatch):
    """W-A8 (repro 07/07) : « ETF flows (Farside) 6 j/7 » ET « ETF flows
    2 j/7 » = la même source — l'alias fusionne l'historique."""
    from src.state import report_memory as mem

    logs = _health_logs({
        "ETF flows (Farside)": [6, 5, 4, 3],   # anciens libellés (runs pré-v28)
        "ETF flows": [2, 1],                   # nouveaux libellés
        "Kaito": [6, 5, 4, 3, 2, 1, 0],
    })
    monkeypatch.setattr(mem, "_read", lambda *a, **k: logs)
    out = mem.compute_blind_spots_weekly()
    assert out["available"] is True
    names = [e["source"] for e in out["entries"]]
    assert names.count("ETF flows") == 1  # fusionné
    assert "ETF flows (Farside)" not in names
    etf = next(e for e in out["entries"] if e["source"] == "ETF flows")
    assert etf["days_down"] >= 5  # 6 jours cumulés sur la fenêtre 7j


def test_blind_spots_notes_restored_today(monkeypatch):
    """W-A9 (repro 07/07) : « Calendrier macro indispo 6 j/7 » affiché sous un
    calendrier REMPLI → l'item est annoté « rétablie ce jour »."""
    from src.state import report_memory as mem

    logs = _health_logs({"Calendrier macro": [6, 5, 4, 3, 2, 1],  # pas 0 = up auj.
                         "Kaito": [6, 5, 4, 3, 2, 1, 0]})
    monkeypatch.setattr(mem, "_read", lambda *a, **k: logs)
    out = mem.compute_blind_spots_weekly()
    cal = next(e for e in out["entries"] if e["source"] == "Calendrier macro")
    assert "rétablie ce jour" in (cal["note"] or "")
    kaito = next(e for e in out["entries"] if e["source"] == "Kaito")
    assert "rétablie" not in (kaito["note"] or "")  # toujours down aujourd'hui


def test_record_source_health_canonizes(monkeypatch):
    from src.state import report_memory as mem

    written: dict = {}
    monkeypatch.setattr(mem, "_read", lambda *a, **k: [])
    monkeypatch.setattr(mem, "_write", lambda f, v: written.update({"v": v}))
    mem.record_source_health(["ETF flows (Farside)", "Kaito"], ["Kaito"])
    assert written["v"][-1]["down"] == ["ETF flows"]  # alias canonisé à l'écriture


def test_etf_freshness_label():
    """M-A6 : « ETF flows (J-1) » réconcilie footer et chiffres du 06/07."""
    from datetime import date

    from src.main import _etf_freshness_label

    today = date(2026, 7, 7)
    etf = {"available": True, "btc": {"date": "2026-07-06", "total_flow_musd": 254.7}}
    assert _etf_freshness_label(etf, today) == "ETF flows (J-1)"
    assert _etf_freshness_label(
        {"btc": {"date": "2026-07-03"}}, today) == "ETF flows (au 03/07)"
    assert _etf_freshness_label({"btc": {"date": "2026-07-07"}}, today) is None
    assert _etf_freshness_label({"btc": {"date": "n/d"}}, today) is None
    assert _etf_freshness_label({}, today) is None


# --------------------------------------------------------------------------- #
# P3 · Gardes weekly : wording vs BTC, F&G unifié, JASMY dédup (4.4, W-A1/A3/A5)
# --------------------------------------------------------------------------- #
def test_guard_vsbtc_direction_wording_follows_sign():
    """W-A3 (repro 07/07) : chiffre corrigé +0.04% mais « sous-performant »
    resté → le MOT doit suivre le SIGNE."""
    from src.analytics import weekly_guards as wg

    bullets = [{"text": "Le portefeuille a clôturé la semaine sur une hausse de "
                        "+6.06%, sous-performant légèrement le Bitcoin "
                        "(+0.04% vs BTC), mais réduisant son drawdown."}]
    out, fixes = wg.fix_vsbtc_direction_wording(bullets, 0.04)
    assert "surperformant" in out[0]["text"]
    assert "sous-performant" not in out[0]["text"]
    assert fixes
    # Signe négatif : la flexion inverse s'applique aussi.
    b2 = [{"text": "Le PTF surperforme le Bitcoin cette semaine."}]
    out2, _ = wg.fix_vsbtc_direction_wording(b2, -1.2)
    assert "sous-performe" in out2[0]["text"]
    # Une puce parlant d'un ACTIF (pas du PTF) n'est jamais touchée.
    b3 = [{"text": "ADA surperforme le marché face au Bitcoin."}]
    out3, fx3 = wg.fix_vsbtc_direction_wording(b3, -1.2)
    assert out3[0]["text"] == b3[0]["text"] and not fx3


def test_guard_fg_evolution_locked_on_series():
    """W-A1 : « F&G X → Y » et « rebondi de N points » verrouillés sur la
    série 8 j (le 07/07 : 24, 23 et 15 coexistaient pour « il y a 7 j »)."""
    from src.analytics import weekly_guards as wg

    bullets = [
        {"text": "Le sentiment a rebondi de 4 points pour atteindre Peur (F&G 27)."},
        {"text": "F&G 24 → 27 sur la semaine."},
    ]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 6.06}, fear_greed_value=27,
        fear_greed_7d_ago=15)
    assert "rebondi de 12 points" in out[0]["text"]
    assert "F&G 15 → 27" in out[1]["text"]
    assert len(fixes) >= 2
    # Une évolution DÉJÀ correcte n'est pas touchée (pas de sur-correction).
    ok = [{"text": "F&G 15 → 27 : le sentiment a rebondi de 12 points."}]
    out2, fx2 = wg.enforce_summary_figures(
        ok, {"weekly_pnl_pct": 6.06}, fear_greed_value=27, fear_greed_7d_ago=15)
    assert out2[0]["text"] == ok[0]["text"] and not fx2


def test_guard_fg_simple_value_still_enforced_without_series():
    """Sans série 7j (API partielle), la garde v26 (valeur du jour) survit."""
    from src.analytics import weekly_guards as wg

    bullets = [{"text": "La peur domine (F&G 31)."}]
    out, fixes = wg.enforce_summary_figures(
        bullets, {}, fear_greed_value=27)
    assert "F&G 27" in out[0]["text"] and fixes


def test_guard_dedupe_analysis_segments():
    """W-A5 (repro 07/07) : « ATH de référence peu significatif, ATH de
    référence peu significatif (listing illiquide), … » → une seule mention,
    la plus détaillée."""
    from src.analytics import weekly_guards as wg

    entries = [{"asset": "JASMY", "analysis":
                "ATH de référence peu significatif, ATH de référence peu "
                "significatif (listing illiquide), secteur IoT/Data en "
                "émergence : faible conviction LT"}]
    fixes = wg.dedupe_analysis_segments(entries)
    assert fixes
    txt = entries[0]["analysis"]
    assert txt.count("ATH de référence peu significatif") == 1
    assert "(listing illiquide)" in txt  # la variante détaillée a gagné
    assert "IoT/Data" in txt  # le reste est intact


def test_guard_ath_paren_in_free_text():
    """W-A5 : « JASMY (-99.9% ATH) » dans un paragraphe libre (exit plan) est
    neutralisé quand l'ATH CoinGecko est suspect, réaligné quand il diverge."""
    from src.analytics import weekly_guards as wg

    facts = {"JASMY": {"from_ath_pct": -99.9}, "CKB": {"from_ath_pct": -91.0}}
    text = ("Le portefeuille contient 10 poussières. JASMY (-99.9% ATH) et "
            "CKB (-97.9% ATH) sont des exemples de projets à faible retour.")
    new, fixes = wg.sanitize_ath_text(text, facts)
    assert "JASMY (ATH de référence peu significatif)" in new
    assert "-99.9% ATH" not in new
    assert "CKB (−91,0% vs ATH)" in new  # écart > 3 pts → réaligné
    assert len(fixes) == 2
    # Application récursive au payload (exit_plan imbriqué).
    payload = {"exit_plan": {"dust_analysis": text}}
    fx = wg.sanitize_ath_in_payload_texts(payload, facts)
    assert len(fx) == 2
    assert "peu significatif" in payload["exit_plan"]["dust_analysis"]


# --------------------------------------------------------------------------- #
# P2 · Weekly : insistance sur le modèle profond + bandeau dégradé (4.2)
# --------------------------------------------------------------------------- #
class _FakeGemini:
    """Mime GeminiClient : profond en panne N vagues, repli fonctionnel.

    Reproduit le contrat v28 : ``fallback_model`` mutable, ``last_used_model``
    posé sur le modèle qui a RÉELLEMENT répondu, échec → exception.
    """

    def __init__(self, deep_fails: int = 999, quota: bool = False):
        self.fallback_model = "gemini-2.5-flash"
        self.last_used_model = None
        self.deep_fails = deep_fails
        self.quota = quota
        self.calls: list[tuple[str, object]] = []  # (model, fallback_actif)

    def generate_json(self, prompt, *, model=None, **kw):
        from src.ai_brain.gemini_client import GeminiQuotaError

        self.calls.append((model, self.fallback_model))
        deep = "gemini-3.5-flash"
        if model == deep and self.deep_fails > 0:
            self.deep_fails -= 1
            if self.fallback_model and self.fallback_model != model:
                # Contrat RÉEL de GeminiClient._with_fallback : TOUTE exception
                # du primaire (quota inclus) bascule sur le repli s'il existe.
                self.last_used_model = self.fallback_model
                return {"weekly_narrative": "par repli"}
            if self.quota:
                raise GeminiQuotaError("quota")
            raise RuntimeError("503 deep down")
        self.last_used_model = model
        return {"weekly_narrative": "par profond"}


def _engine(fake, monkeypatch):
    from src.ai_brain import decision_engine as de

    monkeypatch.setenv("GEMINI_MODEL_DEEP", "")  # défauts $0 : deep=3.5-flash
    eng = de.DecisionEngine(client=fake)
    sleeps: list[int] = []
    eng._sleep = sleeps.append
    return eng, sleeps


def test_weekly_insists_on_deep_before_fallback(monkeypatch):
    """Profond KO 2 vagues puis OK → résultat PROFOND, pauses différées
    respectées, repli jamais utilisé, aucun bandeau."""
    fake = _FakeGemini(deep_fails=2)
    eng, sleeps = _engine(fake, monkeypatch)
    out = eng._safe_json("p", {}, kind="weekly", insist_primary=True)
    assert out["weekly_narrative"] == "par profond"
    assert "_model_degraded" not in out
    assert sleeps[:2] == [120, 210]  # vagues différées (la 1re est immédiate)
    # Pendant les vagues d'insistance, le repli était DÉSACTIVÉ.
    assert all(fb is None for (_m, fb) in fake.calls[:3])
    # Et restauré après.
    assert fake.fallback_model == "gemini-2.5-flash"


def test_weekly_falls_back_with_degraded_flag(monkeypatch):
    """Profond KO en permanence → insistance épuisée (4 vagues, pauses
    120/210/300 s), repli accepté MAIS taggé _model_degraded (bandeau mail)."""
    fake = _FakeGemini(deep_fails=999)
    eng, sleeps = _engine(fake, monkeypatch)
    out = eng._safe_json("p", {}, kind="weekly", insist_primary=True)
    assert out["weekly_narrative"] == "par repli"
    assert out["_model_degraded"] is True
    assert "gemini-3.5-flash" in out["_model_degraded_note"]
    assert "gemini-2.5-flash" in out["_model_degraded_note"]
    assert sleeps == [120, 210, 300]  # ≈ 10,5 min d'insistance au total
    assert len([1 for (_m, fb) in fake.calls if fb is None]) == 4  # 4 vagues


def test_weekly_insist_quota_aborts_waves(monkeypatch):
    """Quota épuisé sur le profond dès la 1re vague → AUCUNE pause différée
    (attendre ne rend pas du quota), flux normal → repli taggé."""
    fake = _FakeGemini(deep_fails=999, quota=True)
    eng, sleeps = _engine(fake, monkeypatch)
    out = eng._safe_json("p", {}, kind="weekly", insist_primary=True)
    assert sleeps == []  # pas d'insistance différée sur un quota
    assert out["weekly_narrative"] == "par repli"
    assert out.get("_model_degraded") is True


def test_generate_weekly_wires_insist_primary(monkeypatch):
    from src.ai_brain import decision_engine as de

    monkeypatch.setattr(de, "build_weekly_prompt", lambda **k: "PROMPT")
    eng = de.DecisionEngine(client=_FakeGemini(deep_fails=0))
    seen: dict = {}
    orig = eng._safe_json

    def spy(prompt, data, **kw):
        seen.update(kw)
        return orig(prompt, data, **kw)

    eng._safe_json = spy
    out = eng.generate_weekly(timestamp="t", data={}, week_state={})
    assert seen.get("insist_primary") is True
    assert out["weekly_narrative"] == "par profond"


def test_weekly_template_renders_degraded_banner():
    from src.reporting import email_html

    html = email_html.render(
        {"_model_degraded": True,
         "_model_degraded_note": "Modèle profond gemini-3.5-flash indisponible."},
        "weekly")
    assert "mode dégradé" in html
    assert "gemini-3.5-flash" in html
    html_ok = email_html.render({}, "weekly")
    assert "mode dégradé" not in html_ok


def test_morning_no_insistence_but_tags_fallback(monkeypatch):
    """Le matin ne DIFFÈRE pas (pas d'insistance) mais le tag honnête
    s'applique aussi si le repli a produit le rapport."""
    fake = _FakeGemini(deep_fails=999)
    eng, sleeps = _engine(fake, monkeypatch)
    out = eng._safe_json("p", {}, kind="morning")
    assert sleeps == []  # aucune vague différée hors hebdo
    assert out.get("_model_degraded") is True


def test_cm_api_freshness_flags(monkeypatch):
    """Chemin API : as_of posé ; J-1 → stale False ; vieille donnée → stale True."""
    from src.data_sources import coinmetrics as cm

    monkeypatch.delenv("COINMETRICS_API_KEY", raising=False)
    _neutralize_overlays(monkeypatch)

    cm.CACHE._store.clear()
    monkeypatch.setattr(cm, "get_json", lambda *a, **k: {"data": _fresh_rows(1)})
    fresh = cm.get_onchain_metrics()["assets"]["ETH"]
    assert fresh["stale"] is False and fresh["as_of"]

    cm.CACHE._store.clear()
    monkeypatch.setattr(cm, "get_json", lambda *a, **k: {"data": _fresh_rows(30)})
    old = cm.get_onchain_metrics()["assets"]["ETH"]
    assert old["stale"] is True
    # L'estimation prix-live sait alors rafraîchir le MVRV (comportement v23).
    refreshed = cm.apply_live_price_mvrv(
        {"available": True, "assets": {"ETH": dict(old)}}, {"ETH": 2000.0})
    assert refreshed["assets"]["ETH"]["mvrv_live_estimate"] is True
