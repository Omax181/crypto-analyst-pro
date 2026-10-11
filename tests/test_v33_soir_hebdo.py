"""Soir, hebdo, Telegram et horaires — le modèle ne décide nulle part (01/10).

L'audit zero-trust a trouvé la règle « le LLM ne décide ni RENFORCER ni
ALLÉGER, ni taille, ni probabilité » appliquée au seul mail du matin :
le soir publiait ``actions_tonight`` (« Alléger 10 % de TAO à 270 $ »),
l'hebdo un plan, une watchlist et des scénarios pondérés par des probabilités
inventées, et la revue des positions reprenait l'action du modèle.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import src.main as M
from src.analytics import daily_guards as DG
from src.analytics.prose_guard import (_GESTE_ACHAT, _GESTE_VENTE,
                                       strip_unbacked_gestures)

RACINE = pathlib.Path(__file__).resolve().parents[1]
U = {"BTC", "ETH", "TAO", "INJ", "LINK", "QNT"}


# ── gestes structurés (soir : actions ; hebdo : plan, watchlist) ──────────

def test_un_geste_du_modele_sans_decision_est_retire():
    actions = [{"action": "Alléger 10% de TAO à 270$", "rationale": "RSI 78"},
               {"action": "Renforcer BTC sur repli", "rationale": "MVRV"},
               {"action": "Surveiller le DXY à 101", "rationale": "macro"},
               {"action": "Renforcer le cœur", "rationale": "générique"}]
    out, fx = DG.restrict_llm_gestures(actions, {"BTC": {"RENFORCER"}}, U)
    assert [a["action"] for a in out] == ["Renforcer BTC sur repli",
                                          "Surveiller le DXY à 101"]
    assert len(fx) == 2


def test_un_allegement_porte_par_le_radar_est_conserve():
    out, _ = DG.restrict_llm_gestures([{"action": "Alléger une tranche de QNT"}],
                                      {"QNT": {"ALLEGER"}}, U)
    assert out and out[0]["action"].startswith("Alléger")


def test_la_watchlist_est_jugee_sur_sa_direction():
    wl = [{"asset": "ETH", "direction": "entrée", "trigger": "sous 1 500 $"},
          {"asset": "QNT", "direction": "sortie", "trigger": "au-dessus de 120 $"},
          {"asset": "BTC", "direction": "surveillance", "trigger": "60 000 $"}]
    out, _ = DG.restrict_llm_gestures(wl, {"QNT": {"ALLEGER"}}, U,
                                      champ_geste="trigger", champ_direction="direction")
    assert [w["asset"] for w in out] == ["QNT", "BTC"]


def test_une_liste_videe_devient_none():
    out, _ = DG.restrict_llm_gestures([{"action": "Acheter INJ"}], {}, U)
    assert out is None


def test_les_gestes_deterministes_du_jour():
    matin = {"thesis_of_the_day": [
        {"asset": "BTC", "action": "RENFORCER", "engine_view": {"x": 1}},
        {"asset": "ETH", "action": "RENFORCER"},                  # sans moteur
        {"asset": "QNT", "action": "ALLÉGER", "v33_trigger": "radar_sortie"}],
        "exit_signals": {"signals": [{"symbol": "HBAR"}]}}
    actives = [{"asset": "INJ", "action": "RENFORCER", "engine": True,
                "status": "in_progress"},
               {"asset": "RENDER", "action": "RENFORCER", "status": "in_progress"}]
    a = DG.deterministic_gestures(matin, actives)
    assert a == {"BTC": {"RENFORCER"}, "QNT": {"ALLEGER"}, "HBAR": {"ALLEGER"},
                 "INJ": {"RENFORCER"}}


# ── prose ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase,garde", [
    ("Allège TAO sur ce rebond.", False),
    ("Renforcer BTC sur repli vers 60 000 $.", True),
    ("Ne pas renforcer ETH dans la hausse.", True),
    ("Aucun allègement n'est justifié sur TAO.", True),
    ("Si ETH replie sous 1 500 $, renforcer le cœur.", False),
    ("INJ a été allégé la semaine dernière.", True),
    ("Prendre des profits sur QNT au-dessus de 120 $.", True),
    ("Accumuler LINK et TAO progressivement.", False),
    ("Le marché reste calme.", True),
])
def test_une_phrase_prescriptive_non_portee_est_retiree(phrase, garde):
    out, _ = strip_unbacked_gestures(phrase, {"BTC": {"RENFORCER"},
                                             "QNT": {"ALLEGER"}}, U)
    assert (out == phrase) is garde, out


def test_un_element_de_liste_vide_disparait_et_les_champs_structures_restent():
    node = {"bullets": [{"icon": "•", "text": "Allège TAO."},
                        {"icon": "•", "text": "BTC tient."}],
            "action": "RENFORCER", "asset": "TAO", "direction": "sortie"}
    out, _ = strip_unbacked_gestures(node, {}, U)
    assert out["bullets"] == [{"icon": "•", "text": "BTC tient."}]
    assert out["asset"] == "TAO" and out["direction"] == "sortie"


# ── hebdo : revue des positions, scénarios, bilan ─────────────────────────

def _revue(**kw):
    portefeuille = {"BTC": {"pru": 50000.0}, "TAO": {"pru": 300.0},
                    "QNT": {"pru": 30.0}}
    marche = {"BTC": {"price": 83500.0}, "TAO": {"price": 290.0},
              "QNT": {"price": 110.0}}
    lt = [{"asset": "TAO", "action": "sortir", "target_price": 2000.0,
           "analysis": "TAO sur support ; alléger TAO sur rebond."},
          {"asset": "BTC", "action": "renforcer", "target_price": 250000.0,
           "analysis": "BTC en expansion."},
          {"asset": "QNT", "action": "garder", "analysis": "QNT très étendu."}]
    detail = [{"asset": "BTC", "reco": "RENFORCER", "status": "in_progress",
               "delta_pct": 2.0, "engine": True}]
    return M._build_positions_review(lt, detail, portefeuille, marche,
                                     vols={"BTC": 2.0, "TAO": 4.1}, **kw)


def test_l_action_de_la_revue_est_deterministe():
    par = {r["asset"]: r for r in _revue(radar_syms={"QNT"})}
    assert par["BTC"]["action"] == "renforcer"     # décision active du moteur
    assert par["QNT"]["action"] == "alléger"       # règle de prise de profit
    assert par["TAO"]["action"] == "garder"        # « sortir » du modèle ignoré


def test_la_cible_long_terme_est_une_fourchette_mesuree_jamais_une_cible_du_modele():
    par = {r["asset"]: r for r in _revue()}
    assert par["TAO"]["lt_target_kind"] == "fourchette"
    assert par["TAO"]["lt_target_low"] < 290.0 < par["TAO"]["lt_target_high"]
    assert par["TAO"]["lt_target_high"] < 2000.0
    assert par["QNT"]["lt_target_high"] is None    # pas de volatilité → rien
    assert "volatilité" in par["QNT"]["lt_target_reason"]


def test_les_probabilites_de_scenarios_sont_retirees():
    p = {"scenarios": [{"type": "bullish", "probability_pct": 55},
                       {"type": "bearish", "probability_pct": 25}]}
    M._retirer_probabilites_scenarios(p)
    assert all("probability_pct" not in s for s in p["scenarios"])


def test_le_bilan_hebdo_ne_compare_que_des_faits():
    r = M._build_calls_review({"btc_price": 80000.0, "regime": "bull",
                               "fear_greed": 50}, 84000.0, 60, {"regime": "bull"})
    assert r and "Semaine précédente" in r["header_line"]
    assert "%)" not in r["header_line"]
    ancien = M._build_calls_review({"dominant_scenario": "haussier", "dominant_pct": 55,
                                    "btc_price": 80000.0}, 84000.0, 60, {})
    assert "55" not in ancien["header_line"]          # jamais la probabilité


def test_les_lectures_de_risque_et_de_sante_ne_prescrivent_aucun_geste():
    for texte in list(M._RISK_AXIS_ACTION.values()) + list(M._HEALTH_AXIS_IMPROVE.values()):
        assert not _GESTE_VENTE.search(texte) and not _GESTE_ACHAT.search(texte), texte
    src = (RACINE / "src/main.py").read_text(encoding="utf-8")
    assert "diversifier progressivement vers d'autres narratifs" not in src


# ── horaires : dérivés des créneaux UTC ───────────────────────────────────

@pytest.mark.parametrize("zone,matin,soir", [("UTC", "07h30", "19h00"),
                                              ("Etc/GMT-1", "08h30", "20h00")])
def test_les_libelles_d_heure_suivent_le_fuseau(monkeypatch, zone, matin, soir):
    """Maroc : UTC+0 permanent depuis le 20/09/2026 (IANA tzdata 2026c)."""
    monkeypatch.setattr(M, "TZ", ZoneInfo(zone))
    m = M._slot_local("morning", datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    e = M._slot_local("evening", datetime(2026, 10, 1, 12, tzinfo=timezone.utc))
    assert f"{m:%H}h{m:%M}" == matin and f"{e:%H}h{e:%M}" == soir
    assert M._next_report_label("morning").endswith(soir)


# ── Telegram ──────────────────────────────────────────────────────────────

def test_le_digest_hebdo_n_invente_pas_de_scenario_dominant():
    from src.telegram_bot import notify
    lignes = notify._weekly_action_block({
        "scenarios": [{"label": "baissier"}, {"label": "haussier"}],
        "weekly_action_plan": [{"action": "Surveiller BTC à 60 000 $"}]})
    assert not any("dominant" in ligne for ligne in lignes)


def test_l_action_du_jour_telegram_ne_depend_pas_des_theses_du_modele():
    from src.telegram_bot import notify
    base = {"top_action": {"is_nothing": True, "line": "x"},
            "opportunity_summary": {"closest": {"asset": "BTC",
                                                "reason": "potentiel 10,0 % sous le requis 21,7 %"}}}
    seul = notify._morning_action_block(dict(base, thesis_of_the_day=[]))
    avec = notify._morning_action_block(dict(base, thesis_of_the_day=[
        {"asset": "TAO", "action": "SURVEILLER", "gate_note": "refus moteur"}]))
    assert seul == avec and "*BTC*" in seul[1]


def test_le_bot_recoit_la_decision_du_moteur():
    from src.telegram_bot import context_loader as CL
    assert {"opportunity_summary", "top_action", "exit_signals"} <= CL._NOYAU_RAPPORT
    from src.telegram_bot import assistant
    assert "SEULE SOURCE DES RECOMMANDATIONS" in assistant._SYSTEM_PROMPT


def test_les_prompts_rappellent_que_le_moteur_decide():
    from src.ai_brain.prompts import evening_prompt, morning_prompt, weekly_prompt
    for mod in (morning_prompt, evening_prompt, weekly_prompt):
        src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert re.search(r"00\. v33", src)


def test_la_legende_de_la_revue_decrit_la_fourchette_pas_l_ath():
    from src.reporting.email_html import render
    html = render({"header": {"date": "x"}, "positions_review": [
        {"asset": "BTC", "current_price": 84166.0, "lt_target_kind": "fourchette",
         "lt_target_low": 50264.0, "lt_target_high": 140935.0, "action": "garder"}]},
        "weekly")
    assert "reconquête de l'ATH, horizon" not in html and "« cible cycle »" not in html


@pytest.mark.parametrize("direction,attendu", [
    ("entrée", "▲ ENTRÉE"), ("sortie", "▼ SORTIE"), ("surveillance", "◇ SURVEILLANCE"),
    ("", "◇ SURVEILLANCE")])
def test_une_ligne_de_surveillance_n_est_pas_une_sortie(direction, attendu):
    """Hebdo réel rejoué : « BTC · surveillance · 60 000 $ » rendu « ▼ SORTIE »."""
    from src.reporting.email_html import render
    html = render({"header": {"date": "x"}, "watchlist": [
        {"asset": "BTC", "direction": direction, "trigger": "60 000 $"}]}, "weekly")
    assert attendu in html
    assert ("▼ SORTIE" in html) is (attendu == "▼ SORTIE")


def test_les_scenarios_ne_parlent_plus_de_probabilites():
    from src.reporting.email_html import render
    html = render({"header": {"date": "x"}, "scenarios": [
        {"type": "bullish", "label": "haussier", "description": "x"}]}, "weekly")
    assert "Probabilités ancrées" not in html


# Rejeu V30→V32 (01/10) : la garde supprimait des CONSTATS dont le verbe a un
# sujet (« Cette annonce renforce l'adoption… », mail du matin ; « les flux
# ETF … renforce le soutien du marché », mail du soir). Un geste n'est
# prescrit qu'en position d'instruction.
@pytest.mark.parametrize("phrase,garde", [
    ("Cette annonce renforce l'adoption grand public des produits dérivés crypto.", True),
    ("La continuité des flux positifs dans les ETF Bitcoin renforce le soutien "
     "structurel du marché, mais l'information est probablement déjà intégrée.", True),
    ("Les ETF pourraient renforcer la demande sur ETH.", True),
    ("BlackRock accumule du BTC depuis 10 jours.", True),
    ("Les baleines profitent du rebond pour vendre leurs BTC.", True),
    ("Les baleines vendent leurs BTC depuis une semaine.", True),
    ("Le setup est propre : renforcer BTC.", True),
    ("Le setup est propre : renforcer SOL.", False),
    ("Je recommande de renforcer SOL.", False),
    ("Si ETH replie, alors accumuler ETH.", False),
    ("Mieux vaut alléger TAO.", False),
    ("Il serait prudent d'alléger TAO.", False),
    ("Profite du rebond pour alléger TAO.", False),
    ("Écrêter QNT sur force (> 180 $) et sécuriser les gains de RSR.", False),
    ("Rebond technique à exploiter pour couper proprement RSR.", False),
    ("• Renforce massivement INJ ce soir", False),
])
def test_un_geste_n_est_prescrit_qu_en_position_d_instruction(phrase, garde):
    out, _ = strip_unbacked_gestures(phrase, {"BTC": {"RENFORCER"}, "QNT": {"ALLEGER"}},
                                     U | {"SOL", "RSR"})
    assert (out == phrase) is garde, out


def test_la_dedup_des_segments_ne_casse_pas_les_decimales():
    """Hebdo réel rejoué (V30 et V32) : « MVRV à 1,58 » → « MVRV à 1, 58 »."""
    from src.analytics.weekly_guards import _dedupe_segments
    assert _dedupe_segments("Drawdown -33,0%, MVRV à 1,58 neutre, ratio 0,0318") == \
        "Drawdown -33,0%, MVRV à 1,58 neutre, ratio 0,0318"
    assert _dedupe_segments("ATH peu significatif, ATH peu significatif (listing)") == \
        "ATH peu significatif (listing)"


@pytest.mark.parametrize("phrase,garde", [
    ("Drawdown -98,5 %, thèse invalidée sous 0,0013 $ : sortie progressive sur rebond.", False),
    ("Allègement partiel conseillé sur QNT.", True),          # QNT porté par le radar
    ("Une sortie de capitaux des ETF pèse sur BTC.", True),
    ("La réduction progressive du bilan de la Fed se poursuit.", True),
])
def test_une_prescription_nominale_avec_modalite_est_un_geste(phrase, garde):
    """Hebdo réel rejoué (02/10) : « sortie progressive sur rebond » (RSR)."""
    out, _ = strip_unbacked_gestures(phrase, {"QNT": {"ALLEGER"}}, U | {"RSR"})
    assert (out == phrase) is garde, out


def test_une_reco_v30_heritee_ne_dicte_plus_l_action_de_la_revue():
    """Revue hebdo : INJ/RENDER (RENFORCER V30 décidés par le modèle) et RSR
    (ALLÉGER V30) affichaient « renforcer » / « alléger ». Seule une décision du
    moteur dit « renforcer », seul le radar dit « alléger »."""
    detail = [{"asset": "INJ", "reco": "RENFORCER", "status": "in_progress",
               "delta_pct": -6.4, "engine": False},
              {"asset": "RSR", "reco": "ALLÉGER", "status": "in_progress",
               "delta_pct": 13.8, "engine": False},
              {"asset": "BTC", "reco": "RENFORCER", "status": "in_progress",
               "delta_pct": 0.2, "engine": True}]
    pf = {"INJ": {"pru": 17.5}, "RSR": {"pru": 0.0065}, "BTC": {"pru": 58000.0}}
    mk = {"INJ": {"price": 7.49}, "RSR": {"price": 0.001666}, "BTC": {"price": 84166.0}}
    rows = {r["asset"]: r for r in M._build_positions_review([], detail, pf, mk)}
    assert rows["INJ"]["action"] == "garder" and rows["RSR"]["action"] == "garder"
    assert rows["BTC"]["action"] == "renforcer"


def test_une_reco_v30_cloturee_ne_masque_pas_la_decision_du_moteur(monkeypatch):
    """Transition V30 → V33 : BTC RENFORCER V30 émis l'avant-veille, décision
    du moteur BTC RENFORCER la veille, la reco V30 touche son stop ce matin.
    La clé (actif, action) faisait primer la ligne V30 clôturée (« la plus
    récente prime ») : la décision en cours disparaissait et la revue disait
    « garder »."""
    from src.tracking import prediction_scoring as ps
    now = datetime.now(timezone.utc)
    iso = lambda j: (now - __import__("datetime").timedelta(days=j)).isoformat()
    moteur = {"asset": "BTC", "action": "RENFORCER", "created_at": iso(1),
              "entry_price": 84000.0, "status": "in_progress", "engine": True}
    v30 = {"asset": "BTC", "action": "RENFORCER", "created_at": iso(2),
           "closed_at": iso(0), "entry_price": 86000.0,
           "status": "invalidated"}
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [moteur])
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: [v30])
    detail = ps.PredictionTracker().build_scoring_detail({"BTC": 84500.0}, 7)
    assert sorted((d["engine"], d["status"]) for d in detail) == [
        (False, "invalidated"), (True, "in_progress")]
    rows = M._build_positions_review(
        [], detail, {"BTC": {"pru": 58000.0}}, {"BTC": {"price": 84500.0}})
    assert rows[0]["action"] == "renforcer"
    assert rows[0]["h30"]["legacy"] is False
