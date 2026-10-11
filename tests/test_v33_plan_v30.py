"""Le plan V30 (stop, cible, R:R, EV, p↑) ne revient par aucun chemin (01/10).

Rejeu de la chaîne réelle du 01/10 avec le code audité : le mail ne montrait
plus le plan V30, mais trois chemins le gardaient en vie — l'application du
moteur pouvait échouer sans verrou (les postures fermes du modèle restaient
fermes), ``/pourquoi`` relisait le ``plan_line`` attaché aux thèses, et
``/analyse`` publiait « EV 30j (p↑ 55 %) » et des scénarios pondérés.
"""

from __future__ import annotations

import re

import pytest

import src.main as M
from src.analytics import opportunity_adapter as A
from src.telegram_bot import commands as C
from tests.conftest import il_y_a

_V30 = re.compile(r"(?i)\bEV\b|p↑|R:R|Cible 30j|Scénarios 30j|bull \d|reconquête ATH")


# ── verrou fail-closed ────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["RENFORCER", "ALLÉGER", "MAINTENIR", "ACCUMULER"])
def test_une_posture_ferme_sans_porteur_est_requalifiee(action):
    t = {"asset": "SOL", "action": action, "confidence": 92,
         "targets": {"short_term_30d": 999.0}, "action_plan": {"stop_loss": 1.0}}
    fx = A.verrouiller_postures_fermes([t])
    assert t["action"] == "SURVEILLER" and fx
    assert "targets" not in t and "confidence" not in t and t["action_plan"] == {}


def test_les_postures_portees_restent():
    moteur = {"asset": "BTC", "action": "RENFORCER", "engine_view": {"x": 1}}
    radar = {"asset": "QNT", "action": "ALLÉGER", "v33_trigger": "radar_sortie"}
    # un porteur ne vaut que pour SA posture
    croise = {"asset": "ETH", "action": "ALLÉGER", "engine_view": {"x": 1}}
    A.verrouiller_postures_fermes([moteur, radar, croise])
    assert (moteur["action"], radar["action"], croise["action"]) == (
        "RENFORCER", "ALLÉGER", "SURVEILLER")


def test_une_application_du_moteur_en_echec_ne_laisse_passer_aucune_posture(monkeypatch):
    def boum(payload, data):
        raise RuntimeError("panne au milieu de l'application")
    monkeypatch.setattr(A, "apply_decisions_to_theses", boum)
    payload = {"thesis_of_the_day": [
        {"asset": "SOL", "action": "RENFORCER", "action_type": "bullish",
         "confidence": 95, "thesis": "x"}]}
    out = M._merge_python_facts(payload, {"opportunity": {"available": True}}, "t")
    assert not [t for t in out.get("thesis_of_the_day") or []
                if t.get("action") in ("RENFORCER", "ALLÉGER")]


# ── plan V30 plus attaché ; taille radar sans l'éligibilité V30 ──────────

def test_aucun_plan_v30_n_est_attache_aux_theses():
    plan = {"available": True, "plan_line": "Invalidation 1 $ · Cible 30j 2 $ · R:R 2 · EV 30j +3 % (p↑ 55%)",
            "invalidation": {"level": 1.0}, "target_30d": {"level": 2.0}}
    payload = {"thesis_of_the_day": [{"asset": "INJ", "action": "SURVEILLER"}]}
    M._apply_asset_plans_to_theses(payload, {"eligible_theses": [
        {"asset": "INJ", "asset_plan": plan, "value_usd": 75.0}]})
    t = payload["thesis_of_the_day"][0]
    assert "plan_line" not in t and "asset_plan" not in t and "targets" not in t


def test_la_taille_d_un_allegement_radar_ne_depend_pas_de_l_eligibilite_v30():
    """TAO absent des « eligible_theses » un jour de 429 CoinGecko : la taille
    vient des candidats du moteur (portefeuille entier)."""
    payload = {"thesis_of_the_day": [
        {"asset": "HBAR", "action": "ALLÉGER", "v33_trigger": "radar_sortie"}]}
    data = {"eligible_theses": [],
            "opportunity": {"candidates": [{"asset": "HBAR", "value_usd": 34.0}]},
            "portfolio_snapshot": {"value_usd": 3953.0}}
    M._apply_asset_plans_to_theses(payload, data)
    ap = payload["thesis_of_the_day"][0]["action_plan"]
    assert ap["position_size_pct"] == -50.0 and ap["position_size_usd"] == pytest.approx(17.0)


# ── garde « survendu » : RSI lu dans les données ──────────────────────────

def test_la_garde_survendu_voit_aussi_les_theses_du_moteur():
    payload = {"thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER", "engine_view": {"x": 1},
        "observation": "BTC en configuration survendue."}]}
    data = {"eligible_theses": [{"asset": "BTC", "asset_plan": {"readout": {"rsi": 58}}}]}
    M._apply_morning_guards(payload, data)
    assert "survendu" not in payload["thesis_of_the_day"][0]["observation"]


# ── Telegram ──────────────────────────────────────────────────────────────

def _serie(n=120):
    px, out = 100.0, []
    for i in range(n):
        px *= 1.02 if i % 2 else 0.982
        out.append(round(px, 4))
    return {"closes": out, "volumes": [1e6] * n}


def test_analyse_ne_publie_ni_plan_ni_probabilite(monkeypatch):
    import src.reporting.charts as CH
    from src.data_sources import binance_futures as BF
    monkeypatch.setattr(CH, "_load_series", lambda sym, days=180: _serie())
    monkeypatch.setattr(BF, "get_derivatives", lambda sym: {"available": False})
    monkeypatch.setattr(C.mem, "load_morning_report", lambda: {"thesis_of_the_day": [
        {"asset": "BTC", "action": "RENFORCER", "engine_view": {"x": 1}}]})
    txt = C._cmd_analyse(["btc"])
    assert not _V30.search(txt), txt
    assert "30 j (80 %)" in txt and "Moteur ce matin : RENFORCER" in txt
    autre = C._cmd_analyse(["sol"])
    assert "aucune décision sur cet actif" in autre


def test_pourquoi_ne_relit_plus_de_plan_v30(monkeypatch):
    monkeypatch.setattr(C.mem, "load_active_recommendations", lambda: [{
        "asset": "BTC", "action": "RENFORCER", "status": "in_progress",
        "engine": True, "entry_price": 83966.0, "ct_target": 93504.5,
        "created_at": il_y_a(0)}])
    monkeypatch.setattr(C.mem, "load_morning_report", lambda: {"thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER", "observation": "MVRV sous sa médiane.",
        "plan_line": "Invalidation 70 000 $ · Cible 30j 90 000 $ · R:R 2 · EV 30j +3 % (p↑ 55%)",
        "engine_view": {"display": {"pot_req": "+63,4 % / 11,4 %"}}}]})
    txt = C._cmd_pourquoi(["BTC"])
    assert not _V30.search(txt), txt
    assert "+63,4 % / 11,4 %" in txt and "invalidation" not in txt.lower()
    assert "barre de succès 12 mois 93" in txt and "93504.5" not in txt
    assert "confiance" not in txt
