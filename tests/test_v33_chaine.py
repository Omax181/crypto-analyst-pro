"""Chaîne RÉELLE du matin, de la sortie du modèle au mail et au carnet (v33).

Remplace ``test_v33_chaine_reelle.py`` : ses entrées portaient des clés que les
producteurs n'écrivent pas (``mc_tvl``, ``commits_90d``) et sa chaîne s'arrêtait
avant ``check_report`` — l'étape qui, rejouée sur les vraies données le 01/10,
rétrogradait la décision du moteur et la faisait disparaître du tableau.

On rejoue ici EXACTEMENT la séquence de ``run_morning`` après la collecte :
restriction au schéma → fusion → ``check_report`` → gardes du matin →
persistance → rendu HTML → digest Telegram. Trois modèles de langage : muet,
normal, hostile. Le contenu DÉCISIONNEL doit être identique dans les trois.
"""

from __future__ import annotations

import copy
import html as H
import re
from datetime import datetime, timedelta, timezone

import pytest

import src.main as M
from src.analytics import opportunity as O
from src.analytics import opportunity_adapter as A
from src.analytics.coherence_checker import check_report
from src.analytics.exit_radar import compute_exit_signals
from src.reporting import email_html
from src.state import report_memory as mem
from tests.test_v33_moteur import HIST_BTC, HIST_ETH


def _jours(n, depart, vol, graine):
    import random
    rnd = random.Random(graine)
    fin = datetime.now(timezone.utc).date() - timedelta(days=1)
    out, px = {}, depart
    for i in range(n):
        px *= 1.0 + rnd.gauss(0, vol)
        out[(fin - timedelta(days=n - 1 - i)).isoformat()] = round(px, 6)
    return out


def _entree(sym, *, prix, valeur, mcap, dil, couv, mvrv=None, conviction=False,
            tier=2, mctvl=None, cat=None):
    e = {"asset": sym, "tier": tier, "conviction": conviction,
         "tier_label": "Tier 0 · cœur (BTC/ETH)" if tier == 0 else "Tier 2 · mid cap",
         "signals_count": 3, "price": prix, "value_usd": valeur,
         "market_cap": mcap, "ath_distance_pct": -40.0,
         "valuation": {"available": True, "metrics": {"dilution_remaining_pct": dil}},
         "thesis_scoring": {"completeness": {"pct": couv}},
         "dev_activity": {"available": True, "commits_30d": 20,
                          "last_commit_days_ago": 2}}
    if mvrv is not None:
        e["onchain_advanced"] = {"mvrv": mvrv, "stale": False, "as_of": "2026-09-30"}
    if mctvl is not None:
        e["valuation"]["metrics"]["mc_tvl_ratio"] = mctvl
        e["tvl"] = {"available": True, "tvl_usd": mcap / mctvl, "category": cat,
                    "name": sym}
    return e


def donnees(mvrv_btc: float = 0.9, moteur: bool = True) -> dict:
    ptf = 3944.0
    elig = [
        _entree("BTC", prix=83522.0, valeur=1500.0, mcap=1.66e12, dil=4.5, couv=83,
                mvrv=mvrv_btc, conviction=True, tier=0),
        _entree("ETH", prix=2900.0, valeur=800.0, mcap=3.5e11, dil=0.0, couv=67,
                mvrv=1.14, conviction=True, tier=0),
        _entree("TAO", prix=290.0, valeur=490.0, mcap=3.4e9, dil=85.0, couv=50,
                conviction=True),
        _entree("QNT", prix=110.0, valeur=193.0, mcap=1.6e9, dil=0.5, couv=50),
        _entree("RSR", prix=0.0017, valeur=71.0, mcap=1.05e8, dil=60.0, couv=67,
                mctvl=2.74, cat="Indexes"),
        _entree("RENDER", prix=1.91, valeur=75.0, mcap=9.9e8, dil=24.0, couv=67),
    ]
    closes = {e["asset"]: _jours(95, e["price"], 0.02 + 0.005 * i, i)
              for i, e in enumerate(elig)}
    volumes = {"BTC": 3.4e10, "ETH": 1.4e10, "TAO": 2.5e8, "QNT": 3.4e8,
               "RSR": 5e6, "RENDER": 6.5e7}
    radar = compute_exit_signals([
        {"symbol": "QNT", "pnl_pct": 321.0, "weight_pct": 4.9, "change_7d": 3.0}])
    cands = A.build_candidates(elig, ptf_value_usd=ptf, closes_by_asset=closes,
                               volume_by_asset=volumes,
                               mvrv_history={"BTC": dict(HIST_BTC), "ETH": dict(HIST_ETH)},
                               peers_by_asset={"RSR": {"available": True,
                                                       "category": "Indexes",
                                                       "ratios": [0.4, 1.0, 2.7, 6.0, 11.0, 30.0],
                                                       "n": 6}})
    opp = O.decide_universe(cands, ptf_value_usd=ptf,
                            exit_signals={s["symbol"]: s for s in radar["signals"]},
                            intouchables={"BTC", "ETH", "TAO", "LINK"})
    opp["coverage"] = A.coverage_report(cands)
    opp["candidates"] = cands
    if not moteur:
        opp = {"available": False, "error": "test", "decisions": [], "firm": []}
    return {
        "eligible_theses": elig, "opportunity": opp, "exit_signals": radar,
        "macro_context": {"dxy": 98.7, "fear_greed": 74},
        "portfolio_snapshot": {"value_usd": ptf, "change_24h_pct": 1.7},
        "active_recommendations": [], "onchain_advanced": {"assets": {}},
        "upcoming_calendar": {"available": False, "events": []},
        "etf_flows": {"available": True},
        "all_positions_summary": [{"asset": e["asset"], "price": e["price"]}
                                  for e in elig],
        "header_meta": {"active_sources_count": 16, "total_sources_count": 26},
    }


LLM_NORMAL = {
    "header": {"date": "01/10", "time_casablanca": "07:30"},
    "executive_summary": {"bullets": [{"icon": "•", "text": "Marché calme, BTC stable."}]},
    "thesis_of_the_day": [
        {"asset": "BTC", "name": "Bitcoin", "action": "RENFORCER", "confidence": 41,
         "action_type": "bullish", "thesis_type": "tactical",
         "observation": "Le MVRV reste bas : renforcer BTC sur ce repli.",
         "price_line": "$99 999 · position $1 500", "signals_summary": "score 9 · seuil 2",
         "historical_pattern": {"verified": True, "win_rate": "88 %"},
         "reasoning_signals": ["MVRV sous sa médiane"],
         "action_plan": {"entry": 55555.5, "stop_loss": 12000, "rr": "9.9:1",
                         "take_profit": {"30pct": 999999}, "position_size_pct": 50},
         "targets": {"short_term_30d": 999999, "long_term_6_12m_low": 888888}},
        {"asset": "ETH", "action": "SURVEILLER", "confidence": 60,
         "observation": "ETH sans signal net."},
    ],
}

LLM_HOSTILE = {
    "header": {"date": "1er janvier 1999", "time_casablanca": "FAUX"},
    "top_action": {"asset": "SXT", "action": "RENFORCER", "line": "RENFORCER SXT x10 — FAUX"},
    "firm_postures": {"SXT": {"action": "RENFORCER"}},
    "opportunity_summary": {"available": True, "count": 9, "empty_line": "FAUX"},
    "executive_summary": {"bullets": [
        {"icon": "•", "text": "Renforcer TAO massivement maintenant."},
        {"icon": "•", "text": "Allège ETH de 50 %."},
        {"icon": "•", "text": "Le marché est calme."}]},
    "thesis_of_the_day": [
        {"asset": a, "action": "RENFORCER", "confidence": 99, "action_type": "bullish",
         "engine_view": {"potential_pct": 400, "required_pct": 1,
                         "display": {"row_note": "FAUX"}},
         "v33_decision": {"decided": True, "action": "RENFORCER"},
         "action_plan": {"entry": 1, "stop_loss": 0.5, "position_size_pct": 90,
                         "take_profit": {"30pct": 999999}},
         "targets": {"short_term_30d": 999999}}
        for a in ("BTC", "ETH", "TAO", "SXT")
    ] + [{"asset": "RENDER", "action": "ALLÉGER", "confidence": 99,
          "action_plan": {"position_size_pct": -90}, "observation": "Vends tout RENDER."}],
}


@pytest.fixture()
def etat(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    return tmp_path


def chaine(llm: dict, data: dict) -> dict:
    """Séquence de ``run_morning`` après la collecte et l'appel au modèle."""
    payload = M._restreindre_au_schema(copy.deepcopy(llm), "morning")
    payload = M._merge_python_facts(payload, data, "jeudi 1 octobre 2026 · 07:30 Casablanca")
    payload = check_report(payload, M._confidence_caps_from_data(data))["sanitized_payload"]
    M._apply_morning_guards(payload, data)
    M._persist_firm_recos(payload, data)
    html = email_html.render(payload, "morning")
    from src.telegram_bot import notify
    tg = notify._build_digest(payload, "morning")
    return {"payload": payload, "html": html, "tg": tg,
            "texte": re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ",
                            re.sub(r"<style.*?</style>", " ", html, flags=re.S)))),
            "carnet": mem.load_active_recommendations()}


def segments(r: dict) -> dict:
    t = r["texte"]

    def seg(a, b):
        i = t.find(a)
        if i < 0:
            return ""
        j = t.find(b, i + len(a))
        return t[i:j if j > 0 else i + 500]
    return {
        "moteur": seg("Moteur d'allocation ·", "</div>"),
        "tableau": seg("Actif Action Actuel", "★ Détail complet"),
        "decision": seg("Décision du moteur", "Succès jugé"),
        "top": (r["payload"].get("top_action") or {}).get("line"),
        "carnet": [(x["asset"], x["action"], x.get("ct_target"))
                   for x in r["carnet"] if x.get("engine")],
        # Telegram : la DÉCISION (ligne « → » et actifs listés). L'argument
        # narratif du modèle peut s'y ajouter — c'est permis (« la partie
        # narrative peut disparaître ») — et n'entre pas dans la comparaison.
        "tg": [ligne.split(" · ")[0] if ligne.startswith(" •") else ligne
               for ligne in (re.search(r"🎯 \*Action du jour\*.*?(?=\n\n|📊|$)",
                                       r["tg"], re.S).group(0).splitlines()
                             if "Action du jour" in r["tg"] else [])],
    }


# ══════════════════════════════════════════════════════════════════════════

def test_la_decision_du_moteur_traverse_toute_la_chaine(etat):
    r = chaine({}, donnees(0.9))
    t = r["texte"]
    assert "Moteur d'allocation · 1 recommandation" in t
    assert re.search(r"BTC ★ RENFORCER .* / \d+,\d %", t)
    assert "Décision du moteur" in t and "Succès jugé à 12 mois" in t
    assert r["payload"]["top_action"]["line"].startswith("RENFORCER BTC")
    btc = [x for x in r["carnet"] if x["asset"] == "BTC"]
    assert len(btc) == 1 and btc[0]["engine"] is True
    assert btc[0].get("confidence") is None
    assert btc[0]["ct_target"] == pytest.approx(
        83522.0 * (1 + btc[0]["required_pct"] / 100), rel=1e-6)
    assert "RENFORCER BTC" in r["tg"]


@pytest.mark.parametrize("mvrv", [0.9, 1.56])
def test_le_contenu_decisionnel_est_identique_que_le_modele_parle_se_taise_ou_mente(
        tmp_path, monkeypatch, mvrv):
    vus = []
    for i, llm in enumerate(({}, LLM_NORMAL, LLM_HOSTILE)):
        monkeypatch.setattr(mem, "_STATE_DIR", tmp_path / str(i))
        vus.append(segments(chaine(llm, donnees(mvrv))))
    assert vus[0] == vus[1] == vus[2]


def test_aucun_chiffre_ni_confiance_du_modele_n_atteint_le_mail(etat):
    for llm in (LLM_NORMAL, LLM_HOSTILE):
        r = chaine(llm, donnees(0.9))
        for sonde in ("999 999", "888 888", "55 555", "99 999", "(C.", "C.41",
                      "Confiance ·", "88 %", "score 9", "9,9:1", "FAUX", "1999",
                      "x10"):
            assert sonde not in r["texte"] and sonde not in r["tg"], sonde


def test_le_modele_ne_cree_aucune_recommandation(etat):
    r = chaine(LLM_HOSTILE, donnees(1.56))           # moteur : aucune décision
    assert "Moteur d'allocation · aucune recommandation" in r["texte"]
    assert "Actif Action Actuel" not in r["texte"]
    assert r["carnet"] == []
    assert r["payload"]["top_action"].get("is_nothing") is True
    for t in r["payload"]["thesis_of_the_day"]:
        assert t.get("action") not in ("RENFORCER", "ALLÉGER")


def test_la_prose_qui_prescrit_un_geste_non_decide_est_retiree(etat):
    r = chaine(LLM_HOSTILE, donnees(1.56))
    assert "massivement" not in r["texte"] and "Allège ETH" not in r["texte"]
    assert "Le marché est calme." in r["texte"]


def test_les_cles_hors_schema_et_les_champs_reserves_sont_retires(etat):
    r = chaine(LLM_HOSTILE, donnees(0.9))
    p = r["payload"]
    assert p["header"]["date"] == "jeudi 1 octobre 2026"   # date posée par Python
    assert p["opportunity_summary"]["count"] == 1         # synthèse recalculée
    assert "SXT" not in (p.get("firm_postures") or {})
    for t in p["thesis_of_the_day"]:
        if t.get("engine_view"):
            assert t["asset"] == "BTC" and t["engine_view"]["potential_pct"] != 400


def test_le_veto_par_contradiction_majeure_retire_la_reco_du_mail_et_du_carnet(etat):
    llm = copy.deepcopy(LLM_NORMAL)
    llm["thesis_of_the_day"][0]["contradictions"] = [
        {"severity": "majeure", "text": "le MVRV est contredit par l'on-chain"}]
    r = chaine(llm, donnees(0.9))
    assert "Actif Action Actuel" not in r["texte"]
    assert [x for x in r["carnet"] if x.get("engine")] == []


def test_une_contradiction_ordinaire_ne_retire_rien(etat):
    llm = copy.deepcopy(LLM_NORMAL)
    llm["thesis_of_the_day"][0]["contradictions"] = [
        {"severity": "mineure", "text": "volume un peu faible"}]
    r = chaine(llm, donnees(0.9))
    assert "BTC ★ RENFORCER" in r["texte"]


def test_un_allegement_du_modele_n_existe_que_sur_regle_de_prise_de_profit(etat):
    llm = {"thesis_of_the_day": [
        {"asset": "QNT", "action": "ALLÉGER", "confidence": 99,
         "action_plan": {"position_size_pct": -90, "stop_loss": 1}},
        {"asset": "TAO", "action": "ALLÉGER", "confidence": 99}]}
    r = chaine(llm, donnees(1.56))
    par = {t["asset"]: t for t in r["payload"]["thesis_of_the_day"]}
    assert par["QNT"]["action"] == "ALLÉGER" and par["QNT"]["v33_trigger"] == "radar_sortie"
    assert par["QNT"]["action_plan"].get("position_size_pct") != -90
    assert par["TAO"]["action"] == "SURVEILLER"
    assert all(x["asset"] != "QNT" for x in r["carnet"])   # règle, pas jugement suivi


def test_moteur_indisponible_aucune_posture_ferme(etat):
    r = chaine(LLM_NORMAL, donnees(0.9, moteur=False))
    assert all(t.get("action") not in ("RENFORCER", "ALLÉGER")
               for t in r["payload"]["thesis_of_the_day"])
    assert r["carnet"] == []


def test_check_report_ne_retrograde_pas_une_decision_du_moteur():
    data = donnees(0.9)
    p = M._merge_python_facts({}, data, "jeudi 1 octobre 2026 · 07:30 Casablanca")
    p = check_report(p, {})["sanitized_payload"]
    btc = next(t for t in p["thesis_of_the_day"] if t["asset"] == "BTC")
    assert btc["action"] == "RENFORCER" and not btc.get("_downgraded")


def test_aucune_decimale_anglaise_dans_le_mail(etat):
    for mvrv in (0.9, 1.56):
        t = chaine(LLM_NORMAL, donnees(mvrv))["texte"]
        fautes = re.findall(r"\d+\.\d+\s?%|\$\s?\d+\.\d+|\d+\.\d+\s?\$|MVRV \d\.\d", t)
        assert not fautes, fautes


def test_l_abstention_affiche_le_motif_du_moteur_dans_tous_les_modes(tmp_path, monkeypatch):
    for i, llm in enumerate(({}, LLM_NORMAL, LLM_HOSTILE)):
        monkeypatch.setattr(mem, "_STATE_DIR", tmp_path / str(i))
        r = chaine(llm, donnees(1.56))
        assert "aucun actif n'a un potentiel MESURÉ" in r["texte"]
        assert "Le plus proche : BTC" in r["texte"]
        assert "Ne rien faire aujourd'hui" in r["texte"]
        assert "convergence de signaux" not in r["texte"]


def test_le_suivi_du_jour_porte_la_barre_de_succes(etat):
    r = chaine({}, donnees(0.9))
    rows = r["payload"]["active_recommendations_tracking"]
    btc = next(x for x in rows if x["asset"] == "BTC")
    ev = next(t for t in r["payload"]["thesis_of_the_day"] if t["asset"] == "BTC")["engine_view"]
    assert btc["ct_target"] == pytest.approx(ev["required_price"])


def test_la_reemission_garde_l_entree_et_la_barre_d_origine(etat):
    chaine({}, donnees(0.9))
    premier = next(x for x in mem.load_active_recommendations() if x["asset"] == "BTC")
    M._persist_firm_recos({"thesis_of_the_day": [
        {"asset": "BTC", "action": "RENFORCER",
         "engine_view": {"price": 70000.0, "required_pct": 30.0, "horizon_days": 365}}]},
        {})
    apres = [x for x in mem.load_active_recommendations() if x["asset"] == "BTC"]
    assert len(apres) == 1
    assert apres[0]["entry_price"] == premier["entry_price"]
    assert apres[0]["ct_target"] == premier["ct_target"]
    assert apres[0]["required_pct"] == premier["required_pct"]


# ── garde-fous unitaires de la chaîne ─────────────────────────────────────

def test_l_alias_ne_vide_plus_jamais_le_payload():
    """F0 : une garde qui renvoie son entrée vidait tout le mail."""
    p = {"a": 1, "b": [2]}
    M._remplacer_sur_place(p, p)
    assert p == {"a": 1, "b": [2]}
    q = {"x": 1}
    M._remplacer_sur_place(q, {"y": 2})
    assert q == {"y": 2}


def test_les_gardes_du_matin_preservent_le_payload_quand_les_etf_sont_disponibles():
    data = donnees(0.9)
    p = M._merge_python_facts(copy.deepcopy(LLM_NORMAL), data, "jeudi 1 octobre 2026 · 07:30 Casablanca")
    avant = set(p)
    M._apply_morning_guards(p, data)
    assert avant <= set(p) and len(p) > 10


def test_la_sortie_du_modele_est_restreinte_a_son_schema():
    p = M._restreindre_au_schema({"header": {}, "top_action": {}, "firm_postures": {},
                                  "_degraded": True, "inconnue": 1}, "morning")
    assert set(p) == {"header", "_degraded"}


def test_le_modele_recoit_une_decision_compacte():
    import json
    data = donnees(0.9)
    brief = M._opportunity_brief(data["opportunity"])
    assert len(json.dumps(brief, ensure_ascii=False)) < 10000
    assert "candidates" not in brief and brief["recommandations"][0]["asset"] == "BTC"
    assert len(json.dumps(data["opportunity"], default=str)) > 3 * len(json.dumps(brief))


def test_un_allegement_protecteur_reste_visible_meme_si_le_modele_se_tait(etat):
    """La règle de prise de profit s'affiche dans le bloc radar, déterministe."""
    r = chaine({}, donnees(1.56))
    assert "À considérer pour allègement" in r["texte"]
    assert "QNT" in r["texte"] and "×3 atteint" in r["texte"]
