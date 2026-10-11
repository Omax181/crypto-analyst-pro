"""Suivi ex post des décisions du moteur (audit zero-trust 01/10).

Une décision du moteur affirme : « cette allocation vaut au moins son
rendement requis sur 12 mois ». Le suivi V30 la jugeait comme un trade : +10 %
en 30 jours, sinon échec — c'est-à-dire sur la volatilité, pas sur la décision.
Il exigeait aussi une confiance du modèle ≥ 55 pour enregistrer la reco.
"""

from __future__ import annotations


import pytest

from src.state import report_memory as mem
from src.tracking import prediction_scoring as ps
from tests.conftest import il_y_a


def _reco_moteur(**kw):
    r = {"id": "BTC-x-RENFORCER", "asset": "BTC", "action": "RENFORCER",
         "engine": True, "horizon_days": 365, "entry_price": 100.0,
         "signal_price": 100.0, "created_at": il_y_a(1), "status": "in_progress",
         "required_pct": 21.7, "ct_target": 121.7}
    r.update(kw)
    return r


@pytest.fixture()
def etat(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    return tmp_path


def test_une_decision_est_validee_a_sa_barre_sans_plafond_a_dix_pour_cent():
    t = ps.PredictionTracker()
    assert t.evaluate_recommendation(_reco_moteur(), 115.0) == "in_progress"
    assert t.evaluate_recommendation(_reco_moteur(), 121.8) == "validated"


def test_une_decision_n_echoue_qu_a_l_expiration_de_son_horizon():
    t = ps.PredictionTracker()
    assert t.evaluate_recommendation(_reco_moteur(created_at=il_y_a(40)), 95.0) == "in_progress"
    assert t.evaluate_recommendation(_reco_moteur(created_at=il_y_a(300)), 95.0) == "in_progress"
    assert t.evaluate_recommendation(_reco_moteur(created_at=il_y_a(366)), 95.0) == "invalidated"


def test_une_reco_v30_garde_ses_regles_d_origine():
    t = ps.PredictionTracker()
    v30 = {"asset": "INJ", "action": "RENFORCER", "entry_price": 8.0,
           "ct_target": 8.74, "created_at": il_y_a(31), "status": "in_progress"}
    assert t.evaluate_recommendation(v30, 8.1) == "invalidated"      # 30 j écoulés
    v30_recent = dict(v30, created_at=il_y_a(5))
    assert t.evaluate_recommendation(v30_recent, 8.8) == "validated"


def test_la_reemission_fige_la_barre_d_origine(etat):
    mem.add_recommendation(_reco_moteur(created_at=il_y_a(3)))
    mem.add_recommendation(_reco_moteur(id="BTC-y-RENFORCER", entry_price=80.0,
                                        required_pct=30.0, ct_target=104.0,
                                        created_at=il_y_a(0)))
    actives = mem.load_active_recommendations()
    assert len(actives) == 1
    r = actives[0]
    assert (r["entry_price"], r["required_pct"], r["ct_target"]) == (100.0, 21.7, 121.7)
    assert r["reissues"] == 1


def test_une_decision_du_moteur_ne_fusionne_pas_avec_une_reco_v30(etat):
    mem.add_recommendation({"id": "BTC-old-RENFORCER", "asset": "BTC",
                            "action": "RENFORCER", "entry_price": 60000.0,
                            "created_at": il_y_a(10), "status": "in_progress",
                            "confidence": 70})
    mem.add_recommendation(_reco_moteur(entry_price=83500.0, ct_target=101620.0))
    actives = mem.load_active_recommendations()
    assert len(actives) == 2
    moteur = next(r for r in actives if r.get("engine"))
    assert moteur["entry_price"] == 83500.0


def test_la_calibration_ignore_les_decisions_sans_confiance(etat, monkeypatch):
    hist = [_reco_moteur(status="validated", closed_at=il_y_a(1)) for _ in range(6)]
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: hist)
    t = ps.PredictionTracker()
    assert t.compute_brier_score(90)["available"] is False
    cal = t.compute_calibration(90)
    assert isinstance(cal, dict)


def test_le_suivi_explique_l_echec_d_une_decision_par_son_horizon(etat, monkeypatch):
    r = _reco_moteur(created_at=il_y_a(370), status="invalidated")
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [r])
    rows = ps.PredictionTracker().active_for_display({"BTC": 95.0})
    assert rows and "Horizon écoulé" in (rows[0].get("comment") or rows[0].get("health_comment") or str(rows[0]))


def test_le_bilan_du_soir_porte_la_barre_de_succes_d_une_decision_du_moteur():
    """Chaîne matin→soir rejouée (01/10) : « BTC RENFORCER · Cible — »."""
    import src.main as M
    matin = {"thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER",
        "action_plan": {"entry": 83966.0},
        "engine_view": {"price": 83966.0, "required_price": 93504.54}}]}
    fp = M._postures_fermes(matin)
    assert fp["BTC"]["target"] == pytest.approx(93504.54)
    rows = M._build_evening_reco_bilan({"firm_postures": fp}, {"BTC": {"price": 83917.0}})
    assert rows[0]["target"] == pytest.approx(93504.54)
