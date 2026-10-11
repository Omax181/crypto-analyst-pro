"""Audit 02/10 (seconde passe) — l'héritage V30 dans ce que lit Omar.

Le déploiement emporte l'état de production : trois recos V30 actives (RSR
ALLÉGER du 18/09, INJ et RENDER RENFORCER du 23/09), décidées par le modèle de
langage. Le rejeu de la chaîne réelle a montré ce qu'elles produisaient encore :
« ✅ Cible atteinte » et « invalidation FRANCHIE » sur la même reco RSR (niveaux
d'un plan d'ACHAT sur un allègement), aucune étiquette V30 au suivi du matin
ni dans le bot, une calibration qui conseillait de « réduire le sizing », une
lecture de backtest qui conseillait d'« étaler le DCA », la « confiance » du
régime macro inventée par le modèle, et « F&G 74 greed » le dimanche.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import src.main as M
from src.reporting.email_html import render
from src.telegram_bot import commands, notify
from src.tracking import prediction_scoring as ps


def _il_y_a(jours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()


def _rsr() -> dict:
    """La reco de production, telle quelle (seule la date est rapprochée)."""
    return {"id": "RSR-2026-09-18-ALLEGER", "asset": "RSR", "action": "ALLEGER",
            "confidence": 75.0, "entry_price": 0.00146424,
            "signal_price": 0.00146424, "created_at": _il_y_a(3),
            "status": "in_progress", "ct_target": 0.001521,
            "stop_loss": 0.001332, "current_price": 0.00167819}


# ── niveaux du mauvais côté ──────────────────────────────────────────────

def test_un_niveau_du_mauvais_cote_n_est_pas_publie():
    assert ps.niveaux_publiables(_rsr()) == (None, None)
    # RENDER (production) : stop relevé au-dessus de l'entrée, sous la cible —
    # un stop suiveur, cohérent.
    render_ = {"action": "RENFORCER", "entry_price": 1.86, "ct_target": 2.0,
               "stop_loss": 1.872514}
    assert ps.niveaux_publiables(render_) == (2.0, 1.872514)
    moteur = {"action": "RENFORCER", "entry_price": 100.0, "ct_target": 111.4,
              "engine": True}
    assert ps.niveaux_publiables(moteur) == (111.4, None)
    vente = {"action": "ALLEGER", "entry_price": 10.0, "signal_price": 10.0,
             "ct_target": 9.0, "stop_loss": 11.0}
    assert ps.niveaux_publiables(vente) == (9.0, 11.0)
    achat_inverse = {"action": "RENFORCER", "entry_price": 10.0,
                     "ct_target": 9.0, "stop_loss": 11.0}
    assert ps.niveaux_publiables(achat_inverse) == (None, 11.0)


def test_rsr_ni_cible_atteinte_ni_invalidation_franchie(monkeypatch):
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [_rsr()])
    t = ps.PredictionTracker()
    row = t.active_for_display({"RSR": 0.001659})[0]
    assert row["ct_target"] is None and row["stop_loss"] is None
    assert "atteinte" not in str(row.get("health_status"))
    assert row["legacy"] is True
    assert t.check_invalidations({"RSR": 0.001659}) == []


def test_le_suivi_n_emprunte_plus_la_cible_du_plan_v30(monkeypatch):
    assert "target_fallbacks" not in inspect.signature(
        ps.PredictionTracker.active_for_display).parameters
    reco = {"asset": "INJ", "action": "RENFORCER", "status": "in_progress",
            "created_at": _il_y_a(5), "entry_price": 4.54}
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [reco])
    row = ps.PredictionTracker().active_for_display({"INJ": 4.75})[0]
    assert row["ct_target"] is None and "ct_target_fallback" not in row


# ── étiquette V30 : matin, bot ───────────────────────────────────────────

def test_le_suivi_du_matin_etiquette_une_reco_v30():
    base = {"asset": "INJ", "action": "RENFORCER", "entry_price": 8.0,
            "current_price": 7.44, "progress_pct": -7.0, "days_open": 8}
    html = render({"header": {"date": "x"}, "active_recommendations_tracking": [
        dict(base, legacy=True),
        dict(base, asset="BTC", legacy=False, action_today=None)]}, "morning")
    assert html.count("reco V30") == 1
    html2 = render({"header": {"date": "x"}, "active_recommendations_tracking": [
        dict(base, legacy=True, action_today="SURVEILLER")]}, "morning")
    assert "reco V30 RENFORCER · posture du moteur aujourd&#39;hui" in html2 \
        or "reco V30 RENFORCER · posture du moteur aujourd'hui" in html2
    lignes = notify._morning_thesis_rows(
        {"active_recommendations_tracking": [dict(base, legacy=True)]})
    assert any("INJ" in li and "reco V30" in li for li in lignes)


def test_le_bot_dit_l_origine_de_chaque_reco(monkeypatch):
    moteur = {"asset": "BTC", "action": "RENFORCER", "status": "in_progress",
              "created_at": _il_y_a(1), "entry_price": 84000.0,
              "ct_target": 93500.0, "engine": True}
    monkeypatch.setattr(commands.mem, "load_active_recommendations",
                        lambda: [_rsr(), moteur])
    monkeypatch.setattr(commands.mem, "load_morning_report", lambda: {})
    out = commands._cmd_recos()
    assert "RSR — ALLÉGER (en cours · reco V30)" in out
    assert "BTC — RENFORCER (en cours · moteur)" in out
    assert "in_progress" not in out
    pq = commands._cmd_pourquoi(["RSR"])
    assert "Reco V30 héritée" in pq
    assert "0,001521" not in pq and "0,001332" not in pq
    assert "barre de succès 12 mois 93" in commands._cmd_pourquoi(["BTC"])
    assert "Reco V30" not in commands._cmd_pourquoi(["BTC"])


def test_l_aide_ne_promet_plus_le_plan_v30():
    aide = commands._cmd_help()
    assert "R:R" not in aide and "scénarios" not in aide


# ── libellés et lectures ─────────────────────────────────────────────────

def test_le_fear_and_greed_est_en_francais_dans_les_trois_messages():
    assert notify._fear_greed({"fear_greed": {"value": 74, "label": "Greed"}}) \
        == (74, "Avidité")
    assert notify._fear_greed({"macro_context": {
        "fear_greed": 20, "fear_greed_label": "Extreme Fear"}}) == (20, "Peur extrême")
    for v in range(0, 101):
        assert notify._fg_label_fr(v) == M._fng_label_fr(v)


def _historique_calibre(n: int = 6) -> list[dict]:
    return [{"asset": f"A{i}", "action": "RENFORCER", "confidence": 75.0,
             "created_at": _il_y_a(5), "closed_at": _il_y_a(2),
             "status": "validated" if i % 2 else "invalidated"}
            for i in range(n)]


def test_calibration_et_brier_sont_un_bilan_v30_sans_consigne(monkeypatch):
    monkeypatch.setattr(ps.mem, "load_prediction_history", _historique_calibre)
    t = ps.PredictionTracker()
    for res in (t.compute_calibration(30), t.compute_brier_score(90)):
        assert res.get("available"), res
        lecture = res["reading"]
        assert "V30" in lecture
        for consigne in ("sizing", "Réduire", "resserrer", "revoir"):
            assert consigne not in lecture
    html = render({"header": {"date": "x"}, "brier_score": t.compute_brier_score(90),
                   "predictions_scoring": {"issued": 6},
                   "calibration": t.compute_calibration(30)}, "weekly")
    assert "score de Brier · recos V30" in html
    assert "confiance annoncée vs réalisée · recos V30" in html


def test_la_lecture_du_backtest_decrit_sans_prescrire():
    lecture = M._lecture_backtest(30.0, 60.0)
    assert lecture == ("Lecture : à 7 j, hausse dans 30% des cas, contre 60% "
                       "à 30 j — statistique, pas un signal d'achat.")
    assert M._lecture_backtest(55.0, 60.0) is None
    assert M._lecture_backtest(None, 60.0) is None


def test_la_confiance_du_regime_macro_n_est_plus_publiee():
    html = render({"header": {"date": "x"}, "macro_regime_readout": {
        "regime": "risk-on", "confidence_pct": 70, "crypto_bias": "neutre"}},
        "morning")
    assert "Régime macro : risk-on" in html
    assert "confiance 70" not in html


# ── seconde lecture des sorties finales (02/10) ──────────────────────────

def test_une_prescription_d_achat_nominale_avec_modalite_est_un_geste():
    from src.analytics.prose_guard import strip_unbacked_gestures
    U = {"RENDER", "INJ", "BTC"}
    for p in ("Drawdown -85,1%, structure haussière daily : accumulation "
              "tactique du leader GPU.",
              "Drawdown -85,3%, structure haussière daily : renforcement actif "
              "initié à 8,00 $."):
        assert strip_unbacked_gestures(p, {}, U)[0] == "", p
    for constat in ("Drawdown -33,0%, MVRV à 1,58 neutre : pilier central du "
                    "portefeuille en accumulation.",
                    "BTC teste sa zone d'accumulation sous 83 000 $.",
                    "Accumulation — phase de cycle sous 50 % de l'ATH."):
        assert strip_unbacked_gestures(constat, {}, U)[0] == constat, constat


def test_telegram_publie_les_micro_prix_comme_le_mail():
    assert notify._fmt_usd(0.00146424) == "0,001464 $"
    assert notify._fmt_usd(0.073) == "0,073 $"
    assert notify._fmt_usd(1.86) == "1,86 $"


def test_le_plus_proche_sur_telegram_sans_parentheses_imbriquees():
    payload = {"top_action": {"is_nothing": True}, "opportunity_summary": {
        "closest": {"asset": "BTC", "reason": (
            "potentiel mesuré 8,6% sous le rendement requis 11,4% (MVRV 1,58 → "
            "médiane historique 1,72 (Q1 1,28, 5920 jours, 2010-07-18 → 2026-10-01))")}}}
    ligne = notify._morning_action_block(payload)[1]
    assert ligne == ("Rien à exécuter ce matin — le plus proche : *BTC*, potentiel "
                     "mesuré 8,6% sous le rendement requis 11,4%.")
    assert "(" not in ligne


def test_le_scenario_dominant_archive_est_etiquete_v30():
    rev = M._build_calls_review({"dominant_scenario": "neutre", "btc_price": 84526},
                                84166.0, 74, {"regime": "bull"})
    assert rev["header_line"].startswith("Scénario dominant annoncé (hebdo V30) : neutre")


def test_une_decision_du_moteur_a_une_barre_pas_une_cible():
    """Audit 02/10 — Telegram et suivi : « cible 93 505 $ » pour une décision
    du moteur, quand sa fiche, le bilan du soir et /pourquoi disent « barre de
    succès à 12 mois »."""
    base = {"asset": "BTC", "action": "RENFORCER", "entry_price": 83966.0,
            "current_price": 84166.0, "progress_pct": 0.2, "ct_target": 93504.54}
    moteur = notify._morning_thesis_rows(
        {"active_recommendations_tracking": [dict(base, legacy=False)]})
    v30 = notify._morning_thesis_rows(
        {"active_recommendations_tracking": [dict(base, legacy=True)]})
    assert any("barre 12 mois 93" in li for li in moteur)
    assert any("cible 93" in li for li in v30)
    html = render({"header": {"date": "x"}, "active_recommendations_tracking": [
        dict(base, legacy=False)]}, "morning")
    assert "barre 12 mois" in html
