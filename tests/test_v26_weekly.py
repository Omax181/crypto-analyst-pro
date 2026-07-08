# -*- coding: utf-8 -*-
"""Tests v26 — audit du mail WEEKLY v25 (parties A et B).

Couvre : verrou chiffres du bilan (W-A1/B1), calendrier J0 passé + off-cycle
(W-A2/A15/B2), réconciliation reco↔watchlist/plan/tableau Positions
(W-A3/A4/A7/B6), plausibilité ATH + narratifs périmés + MVRV cross-actif
(W-A5/A10/B10), range 7j déterministe des scénarios (W-A6), fraîcheur
on-chain / équities en % / DXY (W-A8/A9/A11), footer + facts strip + ETF
Telegram (W-A12/A13/A14), cibles LT crédibles + taxonomie + wording
(W-A16/A17/A18/A19/B5), analyse profonde + graphiques (W-B3/B4/B7/B9).
"""

from __future__ import annotations

import inspect
import pathlib
from datetime import datetime

import pytest

_BASE = pathlib.Path(__file__).resolve().parent.parent
_TPL = _BASE / "src" / "reporting" / "templates"


def _render(payload, kind="weekly"):
    from src.reporting.email_html import render
    return render(payload, kind)


def _render_charts(payload, charts, kind="weekly"):
    from src.reporting.email_html import render
    return render(payload, kind, charts=charts)


# --------------------------------------------------------------------------- #
# W-A1/B1 — verrou Python sur les chiffres du bilan
# --------------------------------------------------------------------------- #
def test_enforce_summary_perf_rewritten():
    """Le « +2.32% sur la semaine » du v25 est réécrit sur le KPI (+3.8%)."""
    from src.analytics import weekly_guards as wg

    bullets = ["Le portefeuille a enregistré une performance positive de "
               "+2.32% sur la semaine, avec un +0.03% vs BTC."]
    snap = {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3}
    out, fixes = wg.enforce_summary_figures(bullets, snap)
    assert "+3.8%" in out[0]
    assert "2.32" not in out[0]
    assert "0.03" not in out[0]
    assert "+0.3%" in out[0]
    assert len(fixes) == 2


def test_enforce_summary_correct_value_untouched():
    """Une perf déjà exacte (à ±0,15 pt) n'est PAS réécrite."""
    from src.analytics import weekly_guards as wg

    bullets = ["Performance du portefeuille de +3.8% sur la semaine."]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3})
    assert out == bullets and fixes == []


def test_enforce_summary_ignores_unrelated_pcts():
    """Un « 67% du PTF » (poids) ne doit jamais être pris pour une perf."""
    from src.analytics import weekly_guards as wg

    bullets = ["Le portefeuille reste concentré : le secteur L1 pèse 67% "
               "du PTF cette semaine."]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3})
    assert out == bullets and fixes == []


def test_enforce_summary_fear_greed_single_value():
    """F&G : une seule valeur autorisée (celle des données)."""
    from src.analytics import weekly_guards as wg

    bullets = [{"text": "Le régime reste risk-off avec un Fear & Greed à 24."}]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8}, fear_greed_value=19)
    assert "19" in out[0]["text"] and "24" not in out[0]["text"]
    assert fixes


def test_enforce_summary_drawdown_not_confused_with_perf():
    """Le « -67.8% par rapport à son ATH » ne doit pas être réécrit (>25%)."""
    from src.analytics import weekly_guards as wg

    bullets = ["Le portefeuille (P&L stable) reste en drawdown de -67.8% "
               "sur la semaine par rapport à son ATH."]
    out, _ = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3})
    assert "-67.8%" in out[0] or "−67.8%" in out[0]


# --------------------------------------------------------------------------- #
# W-A9 — indices actions en % 7j, jamais en points
# --------------------------------------------------------------------------- #
def test_fix_equity_points_sp500():
    from src.analytics import weekly_guards as wg

    txt = ("Les marchés actions ont divergé : S&P 500 en baisse (-16.13 "
           "points) tandis que le DAX a gagné +242.63 points.")
    out, fixes = wg.fix_equity_points(
        txt, {"sp500": -0.31, "dax": 1.9})
    assert "points" not in out
    assert "−0.31% (7j)" in out and "+1.9% (7j)" in out
    assert len(fixes) == 2


def test_fix_equity_points_no_data_untouched():
    from src.analytics import weekly_guards as wg

    txt = "S&P 500 en baisse de -16.13 points."
    out, fixes = wg.fix_equity_points(txt, {})
    assert out == txt and fixes == []


def test_weekly_data_has_markets_week_pct_wiring():
    """run_weekly injecte data.markets_week_pct + applique la garde puces."""
    from src import main as m

    src = inspect.getsource(m.run_weekly)
    assert "get_macro_week_pct()" in src
    assert "markets_week_pct" in src
    assert "fix_equity_points_in_bullets" in src


def test_get_macro_week_pct_computation(monkeypatch):
    """% 7j = dernière clôture vs clôture ≥7 jours avant (par ticker)."""
    from src.data_sources import market_prices as mp

    closes = {f"2026-06-{d:02d}": 100.0 + d for d in range(20, 31)}
    # 30/06 : 130 ; base ≤ 23/06 → 123 ⇒ +5.69%

    def _fake_get_json(url, **k):
        return {"__fake__": True}

    monkeypatch.setattr(mp, "get_json", _fake_get_json)
    monkeypatch.setattr(mp, "_extract_dated_closes", lambda payload: dict(closes))
    monkeypatch.setattr(mp.CACHE, "get_or_compute", lambda key, ttl, fn: fn())
    out = mp.get_macro_week_pct()
    assert out, "au moins un ticker calculé"
    for v in out.values():
        assert v == pytest.approx((130 - 123) / 123 * 100, abs=0.01)


# --------------------------------------------------------------------------- #
# W-A3 — reco RENFORCER active ⇒ pas de SORTIE watchlist / allègement plan
# --------------------------------------------------------------------------- #
_SCORING_RSR = [
    {"asset": "RSR", "reco": "RENFORCER", "status": "in_progress", "score": 0},
    {"asset": "TAO", "reco": "RENFORCER", "status": "in_progress", "score": 0},
    {"asset": "ZK", "reco": "ALLÉGER", "status": "in_progress", "score": 0},
]


def test_reconcile_removes_exit_on_reinforced_asset():
    from src.analytics import weekly_guards as wg

    payload = {
        "watchlist": [
            {"asset": "RSR", "direction": "sortie", "trigger": "+15%"},
            {"asset": "TAO", "direction": "entrée", "trigger": "repli 205"},
            {"asset": "JASMY", "direction": "sortie", "trigger": "rebond"},
        ],
        "weekly_action_plan": [
            {"priority": 1, "action": "Renforcer TAO sous $205", "rationale": "x"},
            {"priority": 2, "action": "Si RSR rebondit de +15% → alléger 50% de "
                                      "la position RSR", "rationale": "poussière"},
            {"priority": 3, "action": "Renforcer ETH sous $1,650", "rationale": "y"},
        ],
    }
    fixes = wg.reconcile_recos(payload, _SCORING_RSR)
    wl_assets = [(w["asset"], w["direction"]) for w in payload["watchlist"]]
    assert ("RSR", "sortie") not in wl_assets
    assert ("TAO", "entrée") in wl_assets          # entrée cohérente gardée
    assert ("JASMY", "sortie") in wl_assets        # pas de reco achat → gardée
    plan_txt = " ".join(a["action"] for a in payload["weekly_action_plan"])
    assert "RSR" not in plan_txt
    # Renumérotation sans trou après retrait.
    assert [a["priority"] for a in payload["weekly_action_plan"]] == [1, 2]
    assert len(fixes) == 2


def test_reconcile_no_reinforced_assets_noop():
    from src.analytics import weekly_guards as wg

    payload = {"watchlist": [{"asset": "RSR", "direction": "sortie"}]}
    fixes = wg.reconcile_recos(payload, [])
    assert payload["watchlist"] and fixes == []


# --------------------------------------------------------------------------- #
# W-A5/B10 — plausibilité des « −X% sous ATH »
# --------------------------------------------------------------------------- #
def test_sanitize_ath_suspect_neutralized():
    """JASMY −99.9% (ATH de listing illiquide) → mention neutralisée."""
    from src.analytics import weekly_guards as wg

    entries = [{"asset": "JASMY",
                "analysis": "−99.9% sous ATH, faible volume, sortie sur rebond."}]
    fixes = wg.sanitize_ath_claims(entries, {"JASMY": {"from_ath_pct": -99.9}})
    assert "99.9" not in entries[0]["analysis"]
    assert "peu significatif" in entries[0]["analysis"]
    assert fixes


def test_sanitize_ath_wrong_value_rewritten():
    from src.analytics import weekly_guards as wg

    entries = [{"asset": "JASMY", "analysis": "−99.9% sous ATH, projet IoT."}]
    fixes = wg.sanitize_ath_claims(entries, {"JASMY": {"from_ath_pct": -91.2}})
    assert "−91,2% sous ATH" in entries[0]["analysis"]
    assert fixes


def test_sanitize_ath_close_value_untouched():
    from src.analytics import weekly_guards as wg

    entries = [{"asset": "BTC", "analysis": "−51.2% sous ATH, accumulation."}]
    fixes = wg.sanitize_ath_claims(entries, {"BTC": {"from_ath_pct": -51.0}})
    assert entries[0]["analysis"] == "−51.2% sous ATH, accumulation."
    assert fixes == []


def test_ath_is_suspect_bounds():
    from src.analytics import weekly_guards as wg

    assert wg.ath_is_suspect(-99.9) and wg.ath_is_suspect(-99.5)
    assert not wg.ath_is_suspect(-99.4) and not wg.ath_is_suspect(None)


# --------------------------------------------------------------------------- #
# W-A10/B10 — narratifs périmés + MVRV cross-actif
# --------------------------------------------------------------------------- #
def test_scrub_stale_eth2_narrative():
    from src.analytics import weekly_guards as wg

    payload = {"positions_review": [
        {"asset": "ETH",
         "analysis": "−65.7% sous ATH, transition vers ETH 2.0 en cours."}]}
    fixes = wg.scrub_stale_narratives(payload)
    txt = payload["positions_review"][0]["analysis"]
    assert "ETH 2.0" not in txt
    assert "post-Merge" in txt
    assert fixes


def test_scrub_stale_walks_scenarios_and_summary():
    from src.analytics import weekly_guards as wg

    payload = {
        "weekly_summary": ["Ethereum 2.0 avance."],
        "scenarios": [{"points": ["la transition vers ETH 2.0 en cours aide"]}],
    }
    wg.scrub_stale_narratives(payload)
    assert "2.0" not in payload["weekly_summary"][0]
    assert "2.0" not in payload["scenarios"][0]["points"][0]


def test_mvrv_cross_asset_replaced_or_removed():
    from src.analytics import weekly_guards as wg

    entries = [
        {"asset": "ETH", "analysis": "accumulation, MVRV à 1.14 (neutre) : ok."},
        {"asset": "TAO", "analysis": "leader IA, MVRV à 1.14, shorts en excès."},
        {"asset": "BTC", "analysis": "MVRV à 1.14 (neutre), accumulation."},
    ]
    fixes = wg.sanitize_cross_asset_mvrv(
        entries, {"ETH": {"mvrv": 0.85}, "BTC": {"mvrv": 1.14}})
    assert "0,85" in entries[0]["analysis"]          # valeur ETH réelle
    assert "MVRV" not in entries[1]["analysis"]      # TAO : pas de donnée → retiré
    assert "MVRV à 1.14" in entries[2]["analysis"]   # BTC jamais touché
    assert len(fixes) == 2


# --------------------------------------------------------------------------- #
# W-A18 — actif détenu : « pas de renfort », pas « absence de position »
# --------------------------------------------------------------------------- #
def test_held_opportunity_wording():
    from src.analytics import weekly_guards as wg

    txt = ("Cependant, l'absence de reco sur FET (qui a fait +12.2%) "
           "représente un coût d'opportunité manqué.")
    out, fixes = wg.fix_held_opportunity_wording(txt, {"FET", "BTC"})
    assert "l'absence de renfort sur FET (position déjà détenue)" in out
    assert fixes


def test_held_opportunity_not_held_untouched():
    from src.analytics import weekly_guards as wg

    txt = "l'absence de position sur SOL a coûté."
    out, fixes = wg.fix_held_opportunity_wording(txt, {"BTC"})
    assert out == txt and fixes == []


# --------------------------------------------------------------------------- #
# W-A6 — scénarios : range 7j déterministe + horizon des niveaux
# --------------------------------------------------------------------------- #
def _scaffold(**kw):
    from src.analytics.scenarios import compute_scenario_scaffold
    base = dict(
        btc_price=61000.0, implied_move_7d_pct=5.6,
        polymarket={"available": True,
                    "fed_bars": {"dominant": "maintien", "dominant_pct": 89.5}},
        vix=18.0, dxy_trend="up", fear_greed=19, btc_funding_pct=1.2,
        btc_support=58454.0, btc_resistance=82416.0,
        btc_trend_pct=-2.0, btc_rsi=45.0, btc_change_7d=3.5,
        calendar_events=[],
    )
    base.update(kw)
    return compute_scenario_scaffold(**base)


def test_scaffold_expected_range_7d():
    sc = _scaffold()
    er = sc["expected_range_7d"]
    assert er["low"] == pytest.approx(61000 * (1 - 0.056), abs=1)
    assert er["high"] == pytest.approx(61000 * (1 + 0.056), abs=1)
    assert "DVOL" in er["label"]


def test_scaffold_levels_carry_horizon():
    """Résistance à +35% = « long terme » ; support à −4% = « hebdo »."""
    sc = _scaffold()
    kl = sc["key_levels"]
    assert kl["resistance_detail"]["horizon"] == "long terme"
    assert kl["support_detail"]["horizon"] == "hebdo"
    assert kl["resistance_detail"]["distance_pct"] == pytest.approx(35.1, abs=0.2)


def test_scaffold_neutral_driver_uses_expected_range():
    """Le driver neutre cite le range ATTENDU (DVOL), plus jamais le range
    support↔résistance technique (source du « 58 454–82 416 » du v25)."""
    sc = _scaffold()
    neutral = " ".join(sc["drivers"]["neutral"])
    assert "range attendu 7j" in neutral
    assert "82416" not in neutral.replace(" ", "").replace(" ", "")
    bullish = " ".join(sc["drivers"]["bullish"])
    assert "long terme" in bullish  # la résistance est citée AVEC son horizon


def test_scaffold_without_move_no_expected_range():
    sc = _scaffold(implied_move_7d_pct=None)
    assert sc["expected_range_7d"] is None


# --------------------------------------------------------------------------- #
# W-A17 — taxonomie : W (Wormhole) = Interop, pas AI
# --------------------------------------------------------------------------- #
def test_wormhole_sector_is_interop():
    from src.analytics.narratives import NARRATIVES

    assert NARRATIVES["W"] == "Interop"
    assert NARRATIVES["TAO"] == "AI"  # non-régression


# --------------------------------------------------------------------------- #
# W-B3 — F&G : historique 8j + évolution WoW
# --------------------------------------------------------------------------- #
def test_fear_greed_history_and_7d(monkeypatch):
    from src.data_sources import fear_greed as fg

    fake = {"data": [{"value": str(19 + i),
                      "value_classification": "Extreme Fear"}
                     for i in range(8)]}  # récent → ancien : 19,20,...26
    monkeypatch.setattr(fg.CACHE, "get_or_compute", lambda k, t, f: fake)
    out = fg.get_fear_greed()
    assert out["available"] and out["value"] == 19
    assert out["value_yesterday"] == 20 and out["delta"] == -1
    assert out["history"] == [26, 25, 24, 23, 22, 21, 20, 19]
    assert out["value_7d_ago"] == 26 and out["delta_7d"] == -7


# --------------------------------------------------------------------------- #
# W-A2/B2 — calendrier : J0 déjà tombé marqué « déjà publié »
# --------------------------------------------------------------------------- #
def _cal(events):
    return {"available": True, "events": events}


def test_mark_published_events_j0_passed():
    from src.main import TZ, _mark_published_events

    now = datetime(2026, 7, 2, 20, 42, tzinfo=TZ)
    cal = _cal([
        {"label": "Non-Farm Employment Change", "days_ahead": 0, "time": "13:30"},
        {"label": "Discours BCE", "days_ahead": 0, "time": "22:00"},
        {"label": "BOE Gov Bailey Speaks", "days_ahead": 1, "time": "10:00"},
        {"label": "Sans heure", "days_ahead": 0},
    ])
    out = _mark_published_events(cal, now)
    evs = {e["label"]: e for e in out["events"]}
    assert evs["Non-Farm Employment Change"]["already_published"] is True
    assert "déjà publié aujourd'hui (13h30)" in evs["Non-Farm Employment Change"]["when"]
    assert evs["Discours BCE"]["already_published"] is False   # 22h > 20h42
    assert evs["BOE Gov Bailey Speaks"]["already_published"] is False
    assert evs["Sans heure"]["already_published"] is False     # heure inconnue


def test_mark_published_events_passthrough_unavailable():
    from src.main import TZ, _mark_published_events

    now = datetime(2026, 7, 2, 20, 42, tzinfo=TZ)
    assert _mark_published_events({"available": False}, now) == {"available": False}


# --------------------------------------------------------------------------- #
# W-A4/A7/A16/B5/B6 — tableau Positions réconcilié + cibles crédibles
# --------------------------------------------------------------------------- #
_PTF = {
    "RSR": {"pru": 0.0065, "quantity": 1},
    "INJ": {"pru": 17.4, "quantity": 1},
    "ATOM": {"pru": 4.95, "quantity": 1},
    "JASMY": {"pru": 0.0247, "quantity": 1},
}
_MKT = {
    "RSR": {"price": 0.001155, "ath": 0.1189},
    "INJ": {"price": 4.62, "ath": 52.62},
    "ATOM": {"price": 1.55, "ath": 44.45},
    "JASMY": {"price": 0.004508, "ath": 4.99},
}
_ATH_FACTS = {
    "RSR": {"ath": 0.1189, "from_ath_pct": -99.0},
    "INJ": {"ath": 52.62, "from_ath_pct": -91.2},
    "ATOM": {"ath": 44.45, "from_ath_pct": -96.5},
    "JASMY": {"ath": 4.99, "from_ath_pct": -99.9, "suspect": True},
}


def test_positions_review_reco_without_lt_gets_fallbacks():
    """W-A4 : RSR (reco active, pas de thèse LT) n'affiche plus « — / — »."""
    from src.main import _build_positions_review

    rows = _build_positions_review(
        [], [{"asset": "RSR", "reco": "RENFORCER", "status": "in_progress",
              "delta_pct": 5.0}],
        _PTF, _MKT, ath_facts=_ATH_FACTS)
    r = rows[0]
    assert r["asset"] == "RSR"
    assert r["lt_status"] == "capitulation"   # −99% → capitulation (calculé)
    assert r["action"] == "renforcer"          # aligné sur la reco active
    assert r["h30"]["reco"] == "RENFORCER"


def test_positions_review_action_aligned_with_active_reco():
    """W-A7 : INJ « RENFORCER en cours » ne peut plus afficher « Garder »."""
    from src.main import _build_positions_review

    rows = _build_positions_review(
        [{"asset": "INJ", "status": "capitulation", "action": "garder",
          "analysis": "x.", "target_price": 52.62}],
        [{"asset": "INJ", "reco": "RENFORCER", "status": "in_progress",
          "delta_pct": 1.8}],
        _PTF, _MKT, ath_facts=_ATH_FACTS)
    assert rows[0]["action"] == "renforcer"


def test_positions_review_target_clamped_to_ath_and_kind():
    """W-A16 : cible > ATH clampée ; ≥ +250% = « cycle », sinon « 6-12m »."""
    from src.main import _build_positions_review

    rows = _build_positions_review(
        [{"asset": "ATOM", "status": "capitulation", "action": "garder",
          "analysis": "x.", "target_price": 60.0},   # > ATH 44.45 → clamp
         {"asset": "INJ", "status": "capitulation", "action": "garder",
          "analysis": "y.", "target_price": 9.0}],   # +95% → 6-12m
        [], _PTF, _MKT, ath_facts=_ATH_FACTS)
    atom = next(r for r in rows if r["asset"] == "ATOM")
    inj = next(r for r in rows if r["asset"] == "INJ")
    assert atom["lt_target"] == pytest.approx(44.45)
    assert atom["lt_target_kind"] == "cycle"          # +2768% → cycle
    assert inj["lt_target_kind"] == "6-12m"


def test_positions_review_suspect_ath_no_target():
    """W-A5/A16 : ATH suspect (JASMY) → aucune cible affichée."""
    from src.main import _build_positions_review

    rows = _build_positions_review(
        [{"asset": "JASMY", "status": "capitulation", "action": "sortir",
          "analysis": "z.", "target_price": 0.05}],
        [], _PTF, _MKT, ath_facts=_ATH_FACTS)
    assert rows[0]["lt_target"] is None
    assert rows[0]["lt_target_kind"] is None


# --------------------------------------------------------------------------- #
# W-A14/A13/A8/A11 — câblage run_weekly (ETF Telegram, facts, fraîcheur, DXY)
# --------------------------------------------------------------------------- #
def test_run_weekly_wiring_v26():
    from src import main as m

    src = inspect.getsource(m.run_weekly)
    # A14 — ETF fusionné avec l'aperçu Telegram, comme matin/soir.
    assert "etf_flows.merge_with_telegram(etf_flows.get_etf_flows(), None)" in src
    # A2 — calendrier marqué (J0 passé) + horizon élargi (B9).
    assert "_mark_published_events(calendar" in src
    assert "horizon_days=10" in src
    # A13 — polymarket_facts posé au format template (available + markets).
    assert '"markets": (polymarket.get("markets") or [])[:3]' in src
    # A8/A11/B3 — fraîcheur on-chain, DXY ICE, structure, niveaux calculés.
    for needle in ("onchain_as_of", "dxy_ice", "market_structure",
                   "computed_levels", "weekly_facts_lines",
                   "week_over_week", "offcycle_note", "weekly_news_digest"):
        assert needle in src, f"câblage manquant : {needle}"
    # A1 — gardes appliquées post-génération.
    for needle in ("enforce_summary_figures", "reconcile_recos",
                   "scrub_stale_narratives", "sanitize_ath_claims",
                   "sanitize_cross_asset_mvrv", "fix_held_opportunity_wording"):
        assert needle in src, f"garde non câblée : {needle}"


def test_weekly_prompt_v26_rules():
    from src.ai_brain.prompts.weekly_prompt import build_weekly_prompt

    p = build_weekly_prompt(timestamp="t", data={}, week_state={})
    assert "expected_range_7d" in p
    assert "computed_levels" in p
    assert "already_published" in p
    assert "markets_week_pct" in p
    assert "DXY (ICE)" in p
    assert "ETH 2.0" in p          # règle d'interdiction du narratif périmé
    assert "suspect" in p          # ATH suspect
    assert "pas de renfort" in p   # W-A18


# --------------------------------------------------------------------------- #
# Rendu template weekly v26
# --------------------------------------------------------------------------- #
_MINIMAL = {
    "header": {"time_casablanca": "jeudi 2 juillet, 20:41", "week_number": 27,
               "year": 2026, "period_covered": "du 25 juin au 2 juillet"},
    "portfolio_snapshot": {"value_usd": 2656.0, "weekly_pnl_pct": 3.8,
                           "weekly_pnl_usd": 97.0, "vs_btc_7d_pct": 0.3,
                           "usdc_pct": 0.0},
    "weekly_summary": ["**PTF +3.8%** sur la semaine."],
    "footer": {"next_morning": "vendredi 3 juillet, 08:30",
               "next_weekly": "dimanche 5 juillet 2026, 12:00 Casablanca"},
    "app_version": "v26",
}


def test_render_weekly_facts_lines_and_offcycle():
    payload = dict(_MINIMAL)
    payload["header"] = dict(_MINIMAL["header"],
                             offcycle_note="Run hors-cycle (jeudi) — l'hebdo "
                                           "est planifié le dimanche 12:00.")
    payload["weekly_facts_lines"] = [
        "📊 Probas taux Fed (Polymarket) · maintien 89.5%",
        "💵 Dollar · DXY (ICE) 101.20 · indice élargi (Fed) 120.10",
        "😨 Fear & Greed · 19 (Extreme Fear) · il y a 7 j : 24 (−5 pts)",
    ]
    html = _render(payload)
    assert "Repères chiffrés" in html
    assert "DXY (ICE) 101.20" in html
    assert "Run hors-cycle (jeudi)" in html
    assert "il y a 7 j : 24" in html


def test_render_weekly_wow_block():
    payload = dict(_MINIMAL)
    payload["week_over_week"] = {
        "available": True,
        "lines": ["Valeur PTF 2 805 $ → 2 656 $ (−5,3%)", "F&G 24 → 19 (−5 pts)"],
    }
    html = _render(payload)
    assert "Depuis le hebdo précédent" in html
    assert "F&amp;G 24 → 19" in html or "F&G 24 → 19" in html


def test_render_weekly_footer_single_line():
    """W-A12 : une seule ligne footer, formats absolus homogènes."""
    html = _render(dict(_MINIMAL))
    assert ("Prochain rapport · vendredi 3 juillet, 08:30 · Prochain hebdo · "
            "dimanche 5 juillet 2026, 12:00 Casablanca") in html


def test_render_weekly_already_published_badge():
    payload = dict(_MINIMAL)
    payload["week_ahead"] = [
        {"label": "Non-Farm Employment Change", "date": "2026-07-02",
         "when": "déjà publié aujourd'hui (13h30)", "days_ahead": 0,
         "importance": "high", "already_published": True,
         "date_label": "jeudi 2 juillet"},
        {"label": "BOE Gov Bailey Speaks", "date": "2026-07-03",
         "when": "demain", "days_ahead": 1, "importance": "low",
         "already_published": False, "date_label": "vendredi 3 juillet"},
    ]
    html = _render(payload)
    # autoescape Jinja : « aujourd'hui » → « aujourd&#39;hui » dans le HTML.
    assert "✓ déjà publié" in html and "(13h30)" in html
    assert ">demain<" in html


def test_render_weekly_news_digest():
    payload = dict(_MINIMAL)
    payload["weekly_news_digest"] = [
        {"title": "BlackRock ETF inflows record", "source": "CoinDesk",
         "date_label": "lun 30/06"},
    ]
    html = _render(payload)
    assert "Ce qui a marqué la semaine" in html
    assert "BlackRock ETF inflows record" in html
    assert "lun 30/06" in html


def test_render_weekly_target_cycle_label():
    payload = dict(_MINIMAL)
    payload["positions_review"] = [
        {"asset": "ATOM", "conviction": False, "current_price": 1.55,
         "pru_pct": -68.7, "h30": None, "lt_status": "capitulation",
         "lt_target": 44.45, "lt_target_pct": 2768, "lt_target_kind": "cycle",
         "analysis": "x.", "action": "garder"},
        {"asset": "INJ", "conviction": False, "current_price": 4.62,
         "pru_pct": -73.5, "h30": None, "lt_status": "capitulation",
         "lt_target": 9.0, "lt_target_pct": 95, "lt_target_kind": "6-12m",
         "analysis": "y.", "action": "garder"},
    ]
    html = _render(payload)
    assert "reconquête de l'ATH" in html
    # 1 occurrence dans la ligne ATOM + 1 dans la légende ; la cible « 6-12m »
    # d'INJ reste un simple « cible ».
    assert html.count("cible cycle") == 2


def test_render_weekly_heatmap_thresholds_7d():
    """W-A19 : +7% sur 7j = vert CLAIR (pas fort) ; +12% = vert fort."""
    payload = dict(_MINIMAL)
    payload["portfolio_heatmap_7d"] = {"cells": [
        {"symbol": "ETH", "change_24h": 7.0, "ptf_pct": 19.2},
        {"symbol": "FET", "change_24h": 12.2, "ptf_pct": 1.3},
        {"symbol": "RSR", "change_24h": -5.3, "ptf_pct": 1.9},
    ]}
    html = _render(payload)
    import re as _re
    eth_cell = _re.search(r'background:(#\w{6});[^>]*>\s*<div[^>]*>ETH<', html)
    fet_cell = _re.search(r'background:(#\w{6});[^>]*>\s*<div[^>]*>FET<', html)
    rsr_cell = _re.search(r'background:(#\w{6});[^>]*>\s*<div[^>]*>RSR<', html)
    assert eth_cell and eth_cell.group(1) == "#EAF3DE"   # ≥3 <10 → clair
    assert fet_cell and fet_cell.group(1) == "#C0DD97"   # ≥10 → fort
    assert rsr_cell and rsr_cell.group(1) == "#FCEBEB"   # ≤−3 >−10 → rouge clair


def test_render_weekly_momentum_detail_in_pct():
    """W-A19 : « +0.3% vs BTC 7j » (plus de « pts »)."""
    payload = dict(_MINIMAL)
    payload["ptf_quality_score"] = {
        "score": 3.3, "delta_wow": None, "improve": None,
        "axes": [
            {"label": "Diversification", "score": 1.7,
             "detail": "top secteur 67% du PTF"},
            {"label": "Momentum vs BTC", "score": 5.2,
             "detail": "+0.3% vs BTC 7j"},
            {"label": "Solidité (vs ATH)", "score": 2.9,
             "detail": "drawdown pondéré -67.8% vs ATH"},
        ],
    }
    html = _render(payload)
    assert "+0.3% vs BTC 7j" in html
    assert "pts vs BTC 7j" not in html
    assert "-67.8% vs ATH" in html


def test_render_weekly_nominal_v25_like_no_regression():
    """Payload nominal type v25 : les sections historiques rendent toujours."""
    payload = dict(_MINIMAL)
    payload.update({
        "predictions_scoring": {"issued": 7, "validated": 0, "invalidated": 0,
                                "win_rate_pct": None, "no_history": False,
                                "closed_count": 0, "min_closed_for_winrate": 5,
                                "win_rate_30d": None,
                                "winrate_gate_label":
                                    "Recos clôturées : 0/5 minimum pour calibration",
                                "lesson": "Discipline maintenue."},
        "scenarios": [{"type": "neutral", "label": "range et attente macro",
                       "probability_pct": 55,
                       "triggers": ["NFP conforme au consensus"],
                       "points": ["range attendu 7j 57 584–64 416 $ (±5.6% DVOL)"],
                       "action": "Maintenir les positions cœur."}],
        "strategy_focus": ["Biais neutre."],
        "macro_panorama": ["**NFP** publié : réaction à analyser."],
        "watchlist": [{"asset": "TAO", "direction": "entrée",
                       "trigger": "repli sous $205", "rationale": "conviction"}],
        "exit_plan": {"subtitle": "Poussières", "diagnosis": "RAS.",
                      "monitoring": "Suivi."},
        "sources_review": {"summary": "Sources macro + marché.",
                           "gaps": "ETF flows (Farside) en aperçu Telegram."},
    })
    html = _render(payload)
    for expected in ("Rapport hebdomadaire", "Bilan semaine", "Scénarios",
                     "Plan d'action", "Watchlist", "range et attente macro",
                     "Prochain hebdo"):
        assert expected in html, f"section absente : {expected}"


# --------------------------------------------------------------------------- #
# W-B4 — graphiques hebdo (best-effort, PNG valides)
# --------------------------------------------------------------------------- #
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def _mpl():
    return pytest.importorskip("matplotlib")


def test_sector_donut_png(_mpl):
    from src.reporting import charts

    png = charts.sector_donut_png([
        {"sector": "L1", "ptf_pct": 66.7}, {"sector": "AI", "ptf_pct": 16.4},
        {"sector": "DeFi", "ptf_pct": 3.6},
    ])
    assert png and png[:8] == _PNG_MAGIC


def test_sector_donut_needs_two_sectors(_mpl):
    from src.reporting import charts

    assert charts.sector_donut_png([{"sector": "L1", "ptf_pct": 100.0}]) is None
    assert charts.sector_donut_png([]) is None


def test_weekly_perf_bars_png(_mpl):
    from src.reporting import charts

    cells = [{"symbol": s, "change_24h": v}
             for s, v in (("FET", 12.2), ("ADA", 12.0), ("RSR", -5.3),
                          ("BTC", 3.5), ("ETH", 8.0))]
    cells.append({"symbol": "+10 autres", "change_24h": -0.2, "is_extra": True})
    png = charts.weekly_perf_bars_png(cells)
    assert png and png[:8] == _PNG_MAGIC


def test_fng_sparkline_png(_mpl):
    from src.reporting import charts

    assert charts.fng_sparkline_png([26, 25, 24, 23, 22, 21, 20, 19])[:8] == _PNG_MAGIC
    assert charts.fng_sparkline_png([19]) is None


def test_btc_levels_png(_mpl):
    from src.reporting import charts

    closes = [58000 + i * 60 for i in range(80)]
    png = charts.btc_levels_png(closes, supports=[58454.0],
                                resistances=[63200.0], price=61490.0)
    assert png and png[:8] == _PNG_MAGIC
    assert charts.btc_levels_png(closes[:5]) is None


def test_render_weekly_chart_cids():
    """v28 (3.B) — template V25 STRICT : UN seul chart (courbe valeur PTF).
    Les charts ajoutés v26/v27 (donut, barres perf, sparkline F&G, BTC niveaux,
    matrice corr, funding, jauge santé) sont SUPPRIMÉS du rendu."""
    payload = dict(_MINIMAL)
    payload["sector_exposure_cells"] = [{"sector": "L1", "ptf_pct": 66.7}]
    payload["portfolio_heatmap_7d"] = {"cells": [{"symbol": "BTC",
                                        "change_24h": 6.0, "ptf_pct": 42.0}]}
    # Même en FOURNISSANT tous les anciens charts, ils ne sont plus rendus.
    html = _render_charts(payload, {
        "ptf_evolution": b"x", "sector_donut": b"x", "btc_levels": b"x",
        "perf_bars_7d": b"x", "fng_sparkline": b"x", "corr_heatmap": b"x",
        "funding_hist": b"x", "health_gauge": b"x"})
    assert "cid:chart_ptf_evolution" in html          # le seul conservé
    for _dead in ("sector_donut", "btc_levels", "perf_bars_7d",
                  "fng_sparkline", "corr_heatmap", "funding_hist",
                  "health_gauge"):
        assert f"cid:chart_{_dead}" not in html, _dead
