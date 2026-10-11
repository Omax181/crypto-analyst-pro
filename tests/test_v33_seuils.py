"""Aucun seuil ne déclenche une vente, aucun plafond n'existe (audit 01/10).

Omar, 01/10 : « Pas de hard cap d'exposition […] Aucun seuil ne déclenche une
vente ; l'allègement dépend de la conviction. » Le mail réel du 01/10, rejoué
avec le code audité, publiait encore « Tout renfort du jour doit respecter les
plafonds de concentration » et, sur un niveau d'invalidation franchi, « thèse
caduque — statuer (sortie ou réduction) » — y compris pour un ALLÉGER dont le
cours était remonté, c'est-à-dire l'inverse du bon sens.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

import src.main as M
from src.analytics import daily_guards as DG
from src.analytics.prose_guard import strip_unbacked_gestures
from src.tracking import prediction_scoring as ps
from tests.conftest import il_y_a

RACINE = pathlib.Path(__file__).resolve().parents[1]
_INTERDIT = re.compile(r"(?i)plafonds? de concentration|statuer \(sortie|"
                       r"sortie ou réduction|respecter les plafonds|"
                       r"prise de profit partielle|on ne renforce plus|"
                       r"actions tactiques proposées")


def _litteraux_publies(chemin: pathlib.Path) -> list[str]:
    """Chaînes du module, docstrings exclues (ce qui peut être PUBLIÉ)."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    docs = {id(n.value) for n in ast.walk(arbre)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return [n.value for n in ast.walk(arbre)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_aucun_texte_publiable_ne_parle_de_plafond_ni_de_vente_sur_seuil():
    fautes = []
    for f in sorted((RACINE / "src").rglob("*.py")):
        if "prompts" in f.parts:          # consignes au modèle, jamais publiées
            continue
        for s in _litteraux_publies(f):
            if _INTERDIT.search(s):
                fautes.append(f"{f.name}: {s[:80]}")
    for f in sorted((RACINE / "src/reporting/templates").glob("*.j2")):
        sans_commentaires = re.sub(r"\{#.*?#\}", "", f.read_text(encoding="utf-8"), flags=re.S)
        if _INTERDIT.search(sans_commentaires):
            fautes.append(f.name)
    assert not fautes, fautes


def _reco(action, stop, **kw):
    r = {"asset": "RSR", "action": action, "status": "in_progress",
         "entry_price": 0.001464, "created_at": il_y_a(13), "stop_loss": stop}
    r.update(kw)
    return r


def test_un_allegement_invalide_ne_dit_jamais_de_vendre(monkeypatch):
    """ALLÉGER à 0,001464 $, invalidation au-dessus (0,0016 $), cours
    0,001659 $ : la thèse d'allègement tombe — jamais « vends ».

    Audit 02/10 — le RSR RÉEL du 01/10 portait son « invalidation » à
    0,001332 $, SOUS son point de vente : un niveau du mauvais côté, désormais
    tu (test_v33_heritage) — il n'est plus le bon support de ce test."""
    monkeypatch.setattr(ps.mem, "load_active_recommendations",
                        lambda: [_reco("ALLÉGER", 0.0016)])
    out = ps.PredictionTracker().check_invalidations({"RSR": 0.001659})
    assert out and out[0]["status"] == "franchi"
    assert "ne pas alléger" in out[0]["implication"]
    assert not _INTERDIT.search(out[0]["implication"])
    monkeypatch.setattr(ps.mem, "load_active_recommendations",
                        lambda: [_reco("ALLÉGER", 0.001332)])
    assert ps.PredictionTracker().check_invalidations({"RSR": 0.001659}) == []


def test_un_achat_invalide_informe_sans_prescrire_de_vente(monkeypatch):
    monkeypatch.setattr(ps.mem, "load_active_recommendations",
                        lambda: [_reco("RENFORCER", 0.0015)])
    out = ps.PredictionTracker().check_invalidations({"RSR": 0.0014})
    imp = out[0]["implication"]
    assert "caduque" in imp and "aucun niveau de prix ne déclenche de vente" in imp
    # La garde de prose ne doit pas l'effacer (négations) — elle est publiée.
    assert strip_unbacked_gestures(imp, {}, {"RSR"})[0] == imp
    assert strip_unbacked_gestures(
        "thèse d'allègement caduque — ne pas alléger sur ce motif", {}, {"RSR"}
    )[0].startswith("thèse d'allègement caduque")


def test_le_stop_d_une_these_du_modele_ne_deplace_pas_l_alerte(monkeypatch):
    """Avant : le « stop du jour » lu dans les thèses primait sur le niveau
    persisté — une thèse MAINTENIR du modèle portant un stop au-dessus du cours
    publiait « invalidation FRANCHIE » sur une reco active."""
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [{
        "asset": "BTC", "action": "RENFORCER", "status": "in_progress",
        "entry_price": 80000.0, "created_at": il_y_a(5), "stop_loss": 60000.0}])
    payload = {"thesis_of_the_day": [{
        "asset": "BTC", "action": "MAINTENIR", "action_type": "neutral",
        "action_plan": {"stop_loss": 90000.0}}]}
    data = {"invalidations_deterministic": [],
            "all_positions_summary": [{"asset": "BTC", "price": 83966.0}]}
    out = M._merge_python_facts(payload, data, "01/10 07:30")
    assert not any("FRANCHIE" in (r.get("condition") or "")
                   for r in (out.get("invalidation_watch") or []))


def test_la_lecon_hebdo_reprend_l_implication_sans_doublon():
    publies = _litteraux_publies(RACINE / "src/main.py")
    assert not [s for s in publies if "laisser dériver" in s]
    arbre = ast.parse((RACINE / "src/main.py").read_text(encoding="utf-8"))
    # la condition porte déjà l'actif : plus de « RSR : RSR : … »
    for n in ast.walk(arbre):
        if isinstance(n, ast.JoinedStr):
            noms = [ast.unparse(v.value) for v in n.values
                    if isinstance(v, ast.FormattedValue)]
            assert noms[:2] != ["_b0.get('asset')", "_b0.get('condition')"]


def test_le_ton_d_une_fiche_requalifiee_par_le_moteur_est_mesure():
    """Le garde v30 ne voyait plus que ``_gated`` (gate v28 retiré) : une fiche
    RENFORCER refusée par le moteur gardait « opportunité historique »."""
    theses = [{"asset": "TAO", "action": "SURVEILLER", "_v33_gated": "C3",
               "thesis": "Opportunité historique, profil exceptionnel."},
              {"asset": "BTC", "action": "RENFORCER", "engine_view": {"x": 1},
               "thesis": "Bande exceptionnelle décidée par le moteur."}]
    DG.tone_down_gated_theses(theses)
    assert "historique" not in theses[0]["thesis"] and "exceptionnel" not in theses[0]["thesis"]
    assert theses[1]["thesis"] == "Bande exceptionnelle décidée par le moteur."


def test_une_cible_touchee_valide_sans_prescrire_de_vente(monkeypatch):
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [{
        "asset": "INJ", "action": "RENFORCER", "status": "in_progress",
        "entry_price": 8.0, "ct_target": 8.74, "created_at": il_y_a(5)}])
    rows = ps.PredictionTracker().active_for_display({"INJ": 8.9})
    com = " ".join(str(r.get("comment") or r.get("health_comment") or "") for r in rows)
    assert "validée" in com and not _INTERDIT.search(com)


def test_le_bilan_du_soir_n_affiche_plus_la_confiance_du_modele():
    from src.reporting.email_html import render
    html = render({"header": {"date": "x"}, "reco_bilan": [
        {"asset": "RSR", "action": "ALLÉGER", "confidence": 75, "entry": 0.001464,
         "target": 0.001521, "current": 0.001654, "status": "invalidated"}]}, "evening")
    assert "RSR" in html and "(C." not in html


def test_aucun_litteral_du_code_ne_contient_de_surrogate():
    """« U+D83C U+DFB2 Options BTC » (hebdo, depuis la V30) : deux surrogates
    isolés, pas un emoji. Le filet d'envoi les recombinait ; l'écriture UTF-8
    stricte d'un fichier ou d'un message ne le fait pas."""
    fautes = []
    for f in sorted((RACINE / "src").rglob("*.py")):
        for s in _litteraux_publies(f):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in s):
                fautes.append(f"{f.name}: {s[:40]!r}")
    assert not fautes, fautes


def test_aucune_docstring_ne_contient_de_surrogate():
    """Python ≥ 3.13 retire l'indentation des docstrings à la compilation, en
    UTF-8 strict : un surrogate dans une docstring passe en 3.11/3.12 et rend le
    module inimportable en 3.13+. La docstring du test précédent l'a fait le
    11/10 : collecte interrompue dans le Codespace (Python 3.14), déploiement
    annulé."""
    fichiers = [*RACINE.glob("*.py")] + [p for d in ("src", "tests", "scripts", "relay")
                                         for p in (RACINE / d).rglob("*.py")]
    fautes = []
    for f in sorted(fichiers):
        arbre = ast.parse(f.read_text(encoding="utf-8-sig"))
        for n in ast.walk(arbre):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(n, clean=False) or ""
                if any(0xD800 <= ord(c) <= 0xDFFF for c in doc):
                    fautes.append(f"{f.relative_to(RACINE)}:{n.body[0].lineno}")
    assert len(fichiers) > 100 and not fautes, fautes


@pytest.mark.parametrize("phrase", [
    "Si le DXY passe sous 98,9, le biais devient favorable.",
    "Le DXY sous 98,9 serait un signal.",
])
def test_garde_dxy_reconnait_sous_passe_si(phrase):
    """V32 : « sous », « passe » et « si » portaient un caractère RETOUR
    ARRIÈRE (0x08) au lieu de ``\b`` dans la regex des seuils — jamais
    reconnus, la borne était réalignée sur le spot (déjà franchie)."""
    from src.analytics.daily_guards import unify_dxy_thresholds
    etat = {"canon": None, "canon_txt": None, "measured": 98.73}
    out, _ = unify_dxy_thresholds([phrase], etat)
    assert out[0] == phrase


def test_aucun_caractere_de_controle_dans_le_code():
    fautes = []
    for f in sorted(RACINE.glob("src/**/*")):
        if f.suffix not in (".py", ".j2", ".yaml", ".yml", ".json", ".md"):
            continue
        for i, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if any(ord(c) < 32 and c != "\t" for c in ligne):
                fautes.append(f"{f.name}:{i}")
    assert not fautes, fautes


@pytest.mark.parametrize("phrase,garde", [
    # hebdo réel du 01/10 : geste dans le bon sens (radar QNT), taille du modèle
    ("Prendre des profits partiels sur QNT à 180 $ (30% de la position).", False),
    ("Alléger 10% de QNT ce soir.", False),
    ("Renforcer BTC de +5% du portefeuille.", False),
    ("Prendre des profits sur QNT au-dessus de 120 $.", True),   # niveau ≠ taille
    ("Ne pas alléger 30% de la position QNT.", True),           # abstention
    ("QNT pèse 4,4% du portefeuille.", True),                    # constat
])
def test_une_taille_fixee_par_le_modele_n_est_jamais_portee(phrase, garde):
    out, _ = strip_unbacked_gestures(phrase, {"QNT": {"ALLEGER"},
                                             "BTC": {"RENFORCER"}}, {"QNT", "BTC"})
    assert (out == phrase) is garde, out


def test_un_geste_structure_avec_taille_du_modele_est_retire():
    actions = [{"action": "Alléger 25% de QNT", "rationale": "×3"},
               {"action": "Alléger une tranche de QNT", "rationale": "radar"}]
    out, fx = DG.restrict_llm_gestures(actions, {"QNT": {"ALLEGER"}}, {"QNT"})
    assert [a["action"] for a in out] == ["Alléger une tranche de QNT"] and fx


@pytest.mark.parametrize("texte,taille", [
    ("alléger la position de 30% pour sécuriser un ×2 vs PRU.", True),
    ("renforcer la position de conviction de +1% du PTF.", True),
    ("La position a progressé de 30 % cette semaine.", False),
])
def test_la_taille_du_modele_sous_ses_deux_tournures(texte, taille):
    from src.analytics.prose_guard import TAILLE_DU_MODELE
    assert bool(TAILLE_DU_MODELE.search(texte)) is taille


# ── cas extrêmes : +3 % … +400 % (brief d'audit) ──────────────────────────
_PALIERS = [3, 10, 20, 50, 100, 200, 400]


@pytest.mark.parametrize("pnl", _PALIERS)
def test_radar_satellite_et_coeur_sur_toute_l_echelle(pnl):
    from src.analytics.exit_radar import compute_exit_signals
    sat = compute_exit_signals([{"symbol": "QNT", "pnl_pct": float(pnl), "weight_pct": 4.0,
                                 "change_7d": 0.0, "change_24h": 0.0}])
    # satellite : paliers d'Omar +80 / ×2 / ×3 ; cœur (BTC, ETH, TAO, LINK) :
    # jamais d'allègement proposé, quel que soit le gain ou le poids (02/10)
    assert (sat["count"] == 1) is (pnl >= 80)
    for sym in ("BTC", "ETH", "TAO", "LINK"):
        coeur = compute_exit_signals([{"symbol": sym, "pnl_pct": float(pnl), "weight_pct": 45.0,
                                       "change_7d": 60.0, "change_24h": 25.0}])
        assert coeur["count"] == 0, sym


@pytest.mark.parametrize("pnl", _PALIERS)
def test_suivi_d_une_decision_du_moteur_sur_toute_l_echelle(pnl):
    t = ps.PredictionTracker()
    r = {"asset": "BTC", "action": "RENFORCER", "engine": True, "horizon_days": 365,
         "entry_price": 100.0, "signal_price": 100.0, "created_at": il_y_a(10),
         "status": "in_progress", "required_pct": 21.7, "ct_target": 121.7}
    attendu = "validated" if 100.0 * (1 + pnl / 100) >= 121.7 else "in_progress"
    assert t.evaluate_recommendation(r, 100.0 * (1 + pnl / 100)) == attendu
    # et jamais d'échec avant l'horizon, même à −50 %
    assert t.evaluate_recommendation(r, 50.0) == "in_progress"


@pytest.mark.parametrize("texte,garde", [
    # rangée d'invalidation écrite par le modèle (forme nominale V30)
    ("thèse caduque — statuer (sortie ou réduction), ne pas laisser dériver", False),
    ("Objectif touché — envisager une prise de profit partielle.", False),
    ("RSR : envisager un renfort sous 0,0015 $.", False),
    ("Une sortie de capitaux des ETF pèse sur le marché.", True),
    ("La réduction du bilan de la Fed se poursuit.", True),
])
def test_une_prescription_nominale_est_un_geste(texte, garde):
    out, _ = strip_unbacked_gestures(texte, {}, {"RSR", "BTC"})
    assert (out == texte) is garde, out


def test_aucune_decimale_anglaise_dans_le_texte_visible_des_trois_mails():
    """Audit 02/10 — scan des mails RÉELS : « US 10Y 5.298% », « β 1.95 »,
    « cons. 0.3% », « Hashrate 956.91 », « QNT -12.5% », « maintien 67.9% »
    passaient malgré RT-14. Filet final sur les nœuds texte du rendu."""
    from src.reporting.email_html import decimales_francaises, render
    html = decimales_francaises(
        '<style>.a{width:12.5%}</style><td style="width:33.3%">US 10Y 5.298% · '
        'courbe 0.37 · β 1.95 · v1.2.3 · 12:30</td>')
    assert "width:12.5%" in html and 'width:33.3%' in html
    assert "5,298%" in html and "0,37" in html and "1,95" in html
    assert "v1.2.3" in html and "12:30" in html
    soir = render({"header": {"date": "x"}, "daily_pnl": {"top_movers": [
        {"symbol": "QNT", "change": -12.5, "pnl_usd": -21.0}]},
        "evening_macro": {"btc_price": 83917.0, "btc_change_24h": 0.0}}, "evening")
    matin = render({"header": {"date": "x"}, "macro_agenda": {"available": True, "events": [
        {"label": "NFP (US)", "when": "demain", "time": "12:30", "importance": "high",
         "forecast": "0.3%", "previous": "4.1%"}]},
        "btc_network": {"available": True, "hash_rate_ehs": 956.91}}, "morning")
    for h in (soir, matin):
        visible = re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", " ", h, flags=re.S))
        assert not re.findall(r"(?<![\w/.:,])[-+−]?\d+\.\d+(?![\w/.])", visible), \
            re.findall(r".{20}\d+\.\d+.{10}", visible)[:3]
