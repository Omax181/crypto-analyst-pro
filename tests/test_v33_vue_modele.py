"""Audit 02/10 (seconde passe) — ce que le modèle VOIT, ce qu'il peut PUBLIER.

Le modèle recevait pour chaque actif éligible le plan V30 (« R:R 0,7 · EV 30j
−0,9 % (p↑ 47 %) », scénarios pondérés 22/54/24, cible « fib 0,618 → ATH »,
échelle DCA), le prior de probabilités des scénarios hebdo et la confiance des
recos héritées — avec, au matin, la consigne de « citer » ce plan, et aucune
garde ne retirait une phrase qui les citait sans verbe de geste. Le persona
partagé lui demandait encore une taille, un stop, un take-profit et une
confiance. Les poussières de l'hebdo recevaient des consignes de vente
rédigées par le modèle (décision d'Omar 02/10 : liste informative).
"""

from __future__ import annotations

import json

from src.ai_brain.prompts import analyst_persona, evening_prompt, weekly_prompt
from src.ai_brain.prompts.morning_prompt import build_morning_prompt
from src.ai_brain.prompts.vue_modele import CLES_V30, vue_modele
from src.analytics.prose_guard import (METRIQUE_V30, retirer_consignes_de_vente,
                                       strip_unbacked_gestures)
from src.reporting.email_html import render

_PLAN_V30 = {"available": True, "rr_30d": 0.7, "prob_up_30d": 0.47,
             "ev_30d_pct": -0.9, "plan_line": "R:R 0,7 · EV 30j −0,9% (p↑ 47%)",
             "scenarios": {"bull": {"probability_pct": 22}},
             "target_cycle": {"basis": "fib 0.618 → ATH réel"}}


def _eligible() -> dict:
    return {"asset": "BTC", "price": 83966.0, "asset_plan": _PLAN_V30,
            "projection": {"volatility": {"atr_pct_daily": 1.26},
                           "levels_above": [{"level": 84882, "basis": "résistance"}],
                           "short_term_30d": {"target": 84882},
                           "short_term_30d_bear": {"target": 80356},
                           "long_term_6_12m": {"low": 100054, "high": 126080},
                           "stop_suggestion": {"level": 79552}}}


# ── entrée : la vue du modèle ────────────────────────────────────────────

def test_le_modele_ne_voit_plus_le_plan_v30():
    vue = vue_modele({"eligible_theses": [_eligible()],
                      "scenario_scaffold": {"prior": {"bearish": 30},
                                            "net_tilt": 0.1}})
    texte = json.dumps(vue, ensure_ascii=False)
    for cle in CLES_V30:
        assert f'"{cle}"' not in texte, cle
    for fuite in ("rr_30d", "p↑", "fib 0.618", "79552", "126080"):
        assert fuite not in texte, fuite
    # Le descriptif reste : niveaux techniques, volatilité, échafaudage.
    assert "levels_above" in texte and "atr_pct_daily" in texte
    assert "net_tilt" in texte


def test_les_recos_heritees_sont_assainies_pour_le_modele():
    rsr = {"asset": "RSR", "action": "ALLEGER", "entry_price": 0.00146424,
           "signal_price": 0.00146424, "ct_target": 0.001521,
           "stop_loss": 0.001332, "confidence": 75.0}
    moteur = {"asset": "BTC", "action": "RENFORCER", "entry_price": 84000.0,
              "ct_target": 93500.0, "engine": True}
    vue = vue_modele({"active_recommendations": [rsr, moteur]})
    r, b = vue["active_recommendations"]
    assert r["ct_target"] is None and r["stop_loss"] is None
    assert "confidence" not in r and r["origine"] == "reco V30 héritée"
    assert b["ct_target"] == 93500.0 and b["origine"] == "moteur"
    assert rsr["ct_target"] == 0.001521          # les données ne sont pas modifiées


def test_les_trois_prompts_sont_construits_sur_la_vue():
    data = {"eligible_theses": [_eligible()],
            "scenario_scaffold": {"prior": {"bearish": 31, "neutral": 47}}}
    textes = [
        build_morning_prompt(timestamp="x", data=data, portfolio_yaml="",
                             evening_state={"eligible_theses": [_eligible()]}),
        evening_prompt.build_evening_prompt(timestamp="x", data=data,
                                            morning_state=data),
        weekly_prompt.build_weekly_prompt(timestamp="x", data=data, week_state=data),
    ]
    for t in textes:
        assert "p↑ 47" not in t and "rr_30d" not in t and '"prior"' not in t
        assert "79552" not in t


# ── consignes : le système décide, le modèle analyse ─────────────────────

def test_les_prompts_ne_demandent_plus_de_decision_au_modele():
    p = analyst_persona.ANALYST_PERSONA
    assert "RÈGLE 0 · v33 — LE SYSTÈME DÉCIDE, TU ANALYSES" in p
    assert "UTC+1" not in p
    assert "Take profit échelonné" not in p and "propose juste le\ngeste" not in p
    assert "liée explicitement à la taille" not in p
    m = build_morning_prompt(timestamp="x", data={}, portfolio_yaml="",
                             evening_state={})
    assert "cite-les, ne les contredis pas" not in m
    assert "calcule action_plan.rr" not in m
    assert "position_size_pct" not in m
    assert "PLAN V30 RETIRÉ" in m
    import inspect
    es = inspect.getsource(evening_prompt)
    assert "Alléger 10% de TAO" not in es and "rebuy (" not in es
    ws = inspect.getsource(weekly_prompt)
    assert "PARS de ce prior" not in ws
    assert "ANCRÉ sur data.scenario_scaffold.prior" not in ws
    assert "+2% du PTF" not in ws and "alléger TAO : 25%" not in ws
    assert "LISTE INFORMATIVE" in ws


# ── sortie : la garde des métriques V30 ──────────────────────────────────

def test_une_phrase_qui_cite_une_metrique_v30_est_retiree():
    for phrase in ("BTC : R:R 0,7, EV 30j −0,9 % (p↑ 47 %).",
                   "Le scénario base (54 %) reste dominant.",
                   "ETH offre un excellent ratio risque/récompense sous 1 500 $.",
                   "Objectif TP1 à 95 000 $.",
                   "Ma confiance plafonnée à 65 % reflète les trous de données."):
        assert METRIQUE_V30.search(phrase), phrase
        out, fx = strip_unbacked_gestures(f"Le DXY recule. {phrase}", {}, {"BTC", "ETH"})
        assert out == "Le DXY recule." and fx and "métrique V30" in fx[0], (phrase, out)
    for legit in ("Polymarket : maintien 66,9 %, baisse 0,4 %.",
                  "Les probabilités de taux de la Fed ont bougé de 5 points.",
                  "L'indice de confiance des consommateurs ressort à 55,3 %.",
                  "Le scénario baissier se déclenche sous 83 479 $."):
        assert not METRIQUE_V30.search(legit), legit


def test_la_justification_d_un_element_conserve_passe_par_la_garde():
    plan = [{"action": "Surveiller ETH sous 1 500 $",
             "rationale": "Excellent ratio risque/récompense : R:R 3,2:1."}]
    out, fx = strip_unbacked_gestures(plan, {}, {"ETH"})
    assert out[0]["action"] == "Surveiller ETH sous 1 500 $"
    assert out[0]["rationale"] == "" and fx


# ── poussières : liste informative (décision d'Omar, 02/10) ──────────────

def test_la_prose_des_poussieres_ne_porte_aucune_consigne_de_vente():
    reel = ("Tes 5 lignes de poussières (NOT, AXL, ZK, W, SXT) représentent une "
            "valeur totale de 22,83 $. SXT (0,16 $) doit être abandonné car les "
            "frais de transaction dépassent sa valeur résiduelle. Pour NOT, AXL, "
            "ZK et W, l'objectif est une liquidation immédiate sur tout sursaut "
            "technique de +30% pour nettoyer le portefeuille. ZK garde une "
            "activité de développement soutenue (GitHub).")
    out, fx = retirer_consignes_de_vente(reel)
    assert out == ("Tes 5 lignes de poussières (NOT, AXL, ZK, W, SXT) représentent "
                   "une valeur totale de 22,83 $. ZK garde une activité de "
                   "développement soutenue (GitHub).")
    assert len(fx) == 2
    suivi, _ = retirer_consignes_de_vente(
        "Surveillance quotidienne des volumes anormaux pour exécuter les ordres "
        "de vente sans état d'âme.")
    assert suivi == ""


def test_le_titre_de_la_section_dit_liste_informative():
    html = render({"header": {"date": "x"}, "exit_plan": {
        "dust_table": [{"ticker": "NOT", "value": 8.17}],
        "subtitle": "Positions de moins de 10 $ — information"}}, "weekly")
    assert "Poussières · liste informative" in html
    assert "positions condamnées" not in html and "NOT" in html


# ── bot : même vue que les mails ─────────────────────────────────────────

def test_le_contexte_du_bot_passe_par_la_vue():
    from src.telegram_bot.context_loader import context_to_text
    texte = context_to_text({"morning_report": {"eligible_theses": [_eligible()]},
                             "active_recommendations": [{
                                 "asset": "INJ", "action": "RENFORCER",
                                 "entry_price": 8.0, "ct_target": 8.74,
                                 "stop_loss": 6.61, "confidence": 70.0}]})
    assert "rr_30d" not in texte and "79552" not in texte
    assert '"confidence"' not in texte and "reco V30 héritée" in texte
