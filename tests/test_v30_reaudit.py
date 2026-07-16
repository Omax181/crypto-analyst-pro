# -*- coding: utf-8 -*-
"""Verrous du RÉ-AUDIT v30 (v30.1) — bugs trouvés en challengeant la v30 livrée.

Chaque test fige un correctif du ré-audit :
  * faux positifs de fix_broken_decimals (années, dates) ;
  * grammaire de fix_oversold_claims (« zone de en consolidation ») ;
  * détection d'actif de reconcile_evening_actions (rationale trompeuse) ;
  * _fmt_pct_fr(nd=0) qui mutilait « 100 » en « 1 » ;
  * locale FR complète (labels $ des plans, fiche de vie, tuiles, gates) ;
  * _fmt_num_human sur décimale FR (« 4,3 » ≠ 43) ;
  * échappement du titre dans le HTML de secours ;
  * check_report robuste au payload dégénéré (thèse non-dict) ;
  * extract_lesson : la PIRE invalidation, pas la première.
"""

from src.analytics import daily_guards as dg
from src.analytics import weekly_guards as wg


# ── fix_broken_decimals : vrais positifs / faux positifs ──────────────────
def test_broken_decimals_repairs_real_cases():
    out, fixes = dg.fix_broken_decimals("baisse de -48, 7% et prix 4, 86 $")
    assert out == "baisse de -48,7% et prix 4,86 $"
    assert fixes


def test_broken_decimals_spares_years_and_dates():
    # Année suivie d'un % légitime : PAS une décimale cassée.
    out, _ = dg.fix_broken_decimals("En 2024, 15% des altcoins ont surperformé")
    assert out == "En 2024, 15% des altcoins ont surperformé"
    # Date « le 14/07, 3% » : idem.
    out2, _ = dg.fix_broken_decimals("le 14/07, 3% de baisse")
    assert out2 == "le 14/07, 3% de baisse"


# ── fix_oversold_claims : grammaire nom vs adjectif ───────────────────────
def test_oversold_noun_becomes_consolidation_noun():
    out, fixes = dg.fix_oversold_claims("BTC est en zone de survente", 54, "BTC")
    assert out == "BTC est en zone de consolidation"
    assert fixes


def test_oversold_adjective_becomes_en_consolidation():
    out, _ = dg.fix_oversold_claims("configuration survendue sur ETH", 61, "ETH")
    assert out == "configuration en consolidation sur ETH"


def test_oversold_untouched_when_rsi_low():
    txt = "configuration survendue (RSI 25)"
    out, fixes = dg.fix_oversold_claims(txt, 25, "BTC")
    assert out == txt and not fixes


# ── reconcile_evening_actions : l'actif vient de la ligne d'ACTION ────────
def test_evening_action_asset_detected_from_action_not_rationale():
    actions = [{"action": "Alléger 25% de TAO à 270 $",
                "rationale": "Contrairement à BTC qui tient son support, TAO casse.",
                "horizon": "ce soir"}]
    recos = [{"asset": "BTC", "action": "RENFORCER", "status": "in_progress"},
             {"asset": "TAO", "action": "RENFORCER", "status": "in_progress"}]
    kept, fixes = dg.reconcile_evening_actions(actions, recos)
    # L'action vise TAO : elle est requalifiée « couverture CT » pour TAO
    # (pas supprimée, pas attribuée à BTC).
    assert kept and len(kept) == 1
    assert "TAO" in (kept[0].get("horizon") or "")
    assert any("TAO" in f for f in fixes)
    assert not any("BTC" in f for f in fixes)


# ── _fmt_pct_fr : nd=0 ne mutile plus les entiers ─────────────────────────
def test_fmt_pct_fr_nd0_integers_safe():
    assert dg._fmt_pct_fr(100, 0) == "+100%"
    assert dg._fmt_pct_fr(-10, 0) == "−10%"
    assert wg._fmt_pct_fr(100, 0) == "+100%"
    assert dg._fmt_pct_fr(2.5) == "+2,5%"


# ── locale FR : formateurs de labels $ et % ───────────────────────────────
def test_asset_plan_fmt_usd_french():
    from src.analytics.asset_plan import _fmt_usd
    assert _fmt_usd(270.0) == "270,00 $"
    assert _fmt_usd(0.085) == "0,0850 $"
    assert _fmt_usd(61949) == "61 949 $"


def test_weekly_guards_fmt_pct_french():
    assert wg._fmt_pct(3.8) == "+3,8%"
    assert wg._fmt_pct(-0.31) == "−0,31%"
    assert wg._fmt_pct(0.0) == "+0%"


def test_reco_gate_stats_french():
    from src.analytics.reco_gate import apply_reco_gate
    payload = {"thesis_of_the_day": [{
        "asset": "ETH", "action": "RENFORCER", "thesis_type": "tactical",
        "action_plan": {"position_size_pct": 1.0},
        "asset_plan": {"ev_30d_pct": -2.1, "rr_30d": 1.0},
    }]}
    apply_reco_gate(payload)
    t = payload["thesis_of_the_day"][0]
    assert t["action"] == "SURVEILLER"
    assert "−2,1%" in t["gate_note"] and "R:R 1,0" in t["gate_note"]


def test_liquidation_zones_labels_french():
    from src.analytics.liquidation_zones import compute_liquidation_zones
    out = compute_liquidation_zones(100.0)
    assert out["available"]
    # 100 × (1 − 1/10) = 90.00 → « 90,00 $ » (décimale FR).
    assert out["long_zones"][0]["level_label"] == "90,00 $"


def test_tracking_life_line_french(monkeypatch):
    from src.tracking import prediction_scoring as ps
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [{
        "asset": "TAO", "action": "RENFORCER", "status": "in_progress",
        "entry_price": 200.0, "created_at": "2026-07-10T08:00:00+00:00",
        "stop_loss": 190.0, "ct_target": 220.0,
    }])
    rows = ps.PredictionTracker().active_for_display({"TAO": 206.9})
    line = rows[0]["life_line"]
    assert "+3,5%" in line          # progrès en décimale FR
    assert "stop à -8,2%" in line   # distance stop en décimale FR


# ── _fmt_num_human : « 4,3 » est une décimale FR, pas 43 ─────────────────
def test_fmt_num_human_french_decimal_string():
    from src.reporting.email_html import _fmt_num_human
    assert _fmt_num_human("4,3") == "4,3"
    assert _fmt_num_human("63,180") == "63 180"  # milliers US
    assert _fmt_num_human(4.3) == "4,3"


# ── HTML de secours : titre échappé ───────────────────────────────────────
def test_fallback_html_escapes_title():
    from src.reporting.email_html import _fallback_html
    html = _fallback_html({"title": "<script>alert(1)</script>"}, "morning")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ── check_report : payload dégénéré (thèse non-dict) ne plante plus ──────
def test_check_report_survives_non_dict_thesis():
    from src.analytics.coherence_checker import check_report
    out = check_report({"thesis_of_the_day": ["une chaîne au lieu d'un dict",
                                              {"asset": "BTC",
                                               "action": "SURVEILLER"}],
                        "all_positions_summary": ["parasite", {"asset": "ETH",
                                                               "ath_distance_pct": -50}]})
    assert len(out["sanitized_payload"]["thesis_of_the_day"]) == 1
    assert any("dégénéré" in w for w in out["warnings"])


# ── extract_lesson : la PIRE invalidation (pas la première) ───────────────
def test_extract_lesson_picks_most_costly(monkeypatch):
    from src.tracking import prediction_scoring as ps
    now = "2026-07-15T08:00:00+00:00"
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: [
        {"asset": "PETIT", "action": "RENFORCER", "status": "invalidated",
         "created_at": now, "price_change_pct": -2.0},
        {"asset": "GROS", "action": "RENFORCER", "status": "invalidated",
         "created_at": now, "price_change_pct": -18.0},
    ])
    lesson = ps.PredictionTracker().extract_lesson(30)
    assert "GROS" in lesson and "PETIT" not in lesson


# ── check_invalidations : alerte « menacé » en décimale FR ────────────────
def test_check_invalidations_condition_french(monkeypatch):
    from src.tracking import prediction_scoring as ps
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [{
        "asset": "TAO", "action": "RENFORCER", "status": "in_progress",
        "entry_price": 210.0, "created_at": "2026-07-10T08:00:00+00:00",
        "stop_loss": 190.0,
    }])
    out = ps.PredictionTracker().check_invalidations({"TAO": 193.0})
    assert out and out[0]["status"] == "menacé"
    assert "1,6% de l'invalidation" in out[0]["condition"]
    assert "190,00 $" in out[0]["condition"]
