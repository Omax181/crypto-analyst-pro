"""VERROUS des ARBITRAGES d'Omar (25/08/2026).

Chaque test fige une décision produit ET le cas réel qui l'a motivée. Un
changement de comportement ici n'est pas une régression technique : c'est une
décision qui a été prise, et qui devra être re-prise explicitement.
"""

from __future__ import annotations

import math

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# 5.12 — Reuters remplacé par les deux flux CNBC (mesurés vivants)
# ═══════════════════════════════════════════════════════════════════════════
def test_reuters_retire_cnbc_ajoute():
    """Le flux Reuters répondait HTTP 200 avec ZÉRO item (mesuré 25/08)."""
    from src.data_sources.crypto_rss import CRYPTO_FEEDS, MACRO_FEEDS

    assert "Reuters" not in MACRO_FEEDS
    assert "CNBC Economy" in MACRO_FEEDS and "CNBC Markets" in MACRO_FEEDS
    assert len(CRYPTO_FEEDS) + len(MACRO_FEEDS) == 16


def test_le_libelle_suit_le_catalogue_apres_le_remplacement():
    from src.data_sources.crypto_rss import CRYPTO_FEEDS, MACRO_FEEDS
    from src.main import _RSS_LABEL

    assert f"{len(CRYPTO_FEEDS) + len(MACRO_FEEDS)} flux" in _RSS_LABEL


# ═══════════════════════════════════════════════════════════════════════════
# 3.4 — l'hebdo dit POURQUOI la cible cycle n'est pas publiée
# ═══════════════════════════════════════════════════════════════════════════
def test_le_gabarit_hebdo_affiche_le_motif():
    """« cible à définir » laissait croire à un manque d'analyse alors que
    c'est un refus délibéré au-delà de +300 %."""
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_weekly.html.j2").read_text(
        encoding="utf-8")
    assert 'r.lt_target_reason or "cible à définir"' in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 2.3 — allègement sur conviction : la cible publiée doit être dépassée
# ═══════════════════════════════════════════════════════════════════════════
def test_allegement_autorise_si_la_cible_est_depassee():
    """24/08 : « Alléger 50 % de INJ à 5,82 $ », cible publiée 5,58 $."""
    from src.analytics.daily_guards import reconcile_evening_actions

    out, fixes = reconcile_evening_actions(
        [{"action": "Alléger 50% de INJ à 5,82 $", "rationale": "pump"}],
        [{"asset": "INJ", "action": "RENFORCER", "status": "in_progress",
          "target_price": 5.58, "current_price": 5.82}])
    assert out and len(out) == 1
    assert "Cible publiée" in out[0]["horizon"]
    assert "dépassée" in out[0]["horizon"]


def test_allegement_supprime_si_la_cible_nest_pas_atteinte():
    from src.analytics.daily_guards import reconcile_evening_actions

    out, fixes = reconcile_evening_actions(
        [{"action": "Alléger 30% de TAO", "rationale": "prudence"}],
        [{"asset": "TAO", "action": "RENFORCER", "status": "in_progress",
          "target_price": 240.0, "current_price": 234.91}])
    assert not out
    assert any("SUPPRIMÉE" in f and "TAO" in f for f in fixes)


def test_sans_cible_publiee_on_retombe_sur_le_comportement_v30():
    """Refuser par défaut supprimerait des gestes légitimes sur les recos
    anciennes qui ne portent pas de cible."""
    from src.analytics.daily_guards import reconcile_evening_actions

    out, _ = reconcile_evening_actions(
        [{"action": "Alléger 20% de RSR", "rationale": "stop approché"}],
        [{"asset": "RSR", "action": "RENFORCER", "status": "in_progress"}])
    assert out and "Couverture tactique CT" in out[0]["horizon"]


# ═══════════════════════════════════════════════════════════════════════════
# 1.7 — sélection hybride : cœur d'abord, puis par score
# ═══════════════════════════════════════════════════════════════════════════
def _selection(theses):
    """Reproduit la règle posée dans ``_merge_python_facts``."""
    from src.main import _TIER0, _parse_num

    fermes = [t for t in theses if any(
        k in (t.get("action") or "").upper()
        for k in ("RENFORC", "ALLÉG", "ALLEG"))]

    def score(t):
        v = _parse_num((t.get("thesis_scoring") or {}).get("score"))
        return v if v is not None else -1.0

    coeur = [t for t in fermes if str(t.get("asset") or "").upper() in _TIER0]
    autres = sorted([t for t in fermes if t not in coeur],
                    key=score, reverse=True)
    return [t["asset"] for t in (coeur + autres)[:3]]


def test_le_coeur_garde_sa_place():
    assert _selection([
        {"asset": "BTC", "action": "RENFORCER", "thesis_scoring": {"score": 4}},
        {"asset": "ETH", "action": "RENFORCER", "thesis_scoring": {"score": 3}},
        {"asset": "INJ", "action": "RENFORCER", "thesis_scoring": {"score": 12}},
        {"asset": "RSR", "action": "RENFORCER", "thesis_scoring": {"score": 9}},
    ]) == ["BTC", "ETH", "INJ"]


def test_hors_coeur_le_score_departage():
    """21/08 : INJ (8) n'avait pas de fiche, TAO (5) en avait une."""
    assert _selection([
        {"asset": "TAO", "action": "RENFORCER", "thesis_scoring": {"score": 5}},
        {"asset": "RENDER", "action": "RENFORCER", "thesis_scoring": {"score": 11}},
        {"asset": "INJ", "action": "RENFORCER", "thesis_scoring": {"score": 8}},
        {"asset": "LINK", "action": "RENFORCER", "thesis_scoring": {"score": 2}},
    ]) == ["RENDER", "INJ", "TAO"]


def test_la_legende_dit_la_regle():
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_morning.html.j2").read_text(
        encoding="utf-8")
    assert ("cœur (BTC/ETH) d'abord, puis les plus forts excédents "
            "(potentiel − requis)") in gabarit
    # L'occurrence restante est dans un commentaire Jinja {# #} qui DOCUMENTE
    # le défaut corrigé : elle n'est jamais rendue. On vérifie donc la phrase
    # effectivement produite, pas le fichier brut.
    assert "★ Détail complet ci-dessous pour les 3 plus fortes" not in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 2.9 — le bilan du soir couvre TOUTES les recos ouvertes
# ═══════════════════════════════════════════════════════════════════════════
def test_le_bilan_du_soir_nest_plus_un_repli(monkeypatch):
    """24/08 : le tableau ne montrait qu'INJ pendant que le mail actionnait
    TAO et signalait RSR proche de son stop."""
    from src import main as m

    monkeypatch.setattr(m.mem, "load_active_recommendations", lambda: [
        {"asset": "TAO", "action": "RENFORCER", "status": "in_progress",
         "entry_price": 220.93, "ct_target": 240.0, "stop_loss": 212.45},
        {"asset": "RSR", "action": "RENFORCER", "status": "in_progress",
         "entry_price": 0.00148831, "ct_target": 0.001614,
         "stop_loss": 0.001446},
    ])
    etat_matin = {"firm_postures": {"INJ": {
        "action": "RENFORCER", "entry": 5.37, "target": 5.58,
        "stop_loss": 5.25, "confidence": 74}}}
    marche = {"INJ": {"price": 5.82}, "TAO": {"price": 240.62},
              "RSR": {"price": 0.001456}}
    out = m._build_evening_reco_bilan(etat_matin, marche)
    assert {r["asset"] for r in out} == {"INJ", "TAO", "RSR"}
    assert len(out) == 3            # aucune ligne dupliquée


def test_le_titre_du_tableau_du_soir_dit_la_verite():
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_evening.html.j2").read_text(
        encoding="utf-8")
    assert "Recos actives · bilan soir" in gabarit
    assert "Recos du matin · bilan soir" not in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 3.6 — réserve explicite sous 10 observations
# ═══════════════════════════════════════════════════════════════════════════
def test_reserve_explicite_sous_dix_observations():
    """24/08 : « 30j : 0 % de hausse, retour médian −16,2 % (n=3) »."""
    import random

    from src.analytics.strategy_backtest import compute_dip_buy_stats

    random.seed(3)
    serie = [100 + 12 * math.sin(i / 6.0) + random.uniform(-3, 3)
             for i in range(120)]
    out = compute_dip_buy_stats(serie)
    assert out["available"]
    assert min(s["n"] for s in out["horizons"].values()) < 10
    assert "trop mince pour conclure" in out["note"]


# ═══════════════════════════════════════════════════════════════════════════
# 5.13 — le funding par défaut n'est plus qualifié de « sain »
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("annualise,attendu", [
    (10.95, "valeur par défaut"),        # LE cas : TAO 21/08, INJ 24/08, LINK
    (12.0, "valeur par défaut"),
    # Les seuils d'extrême valent ±54,75 %/an (0,05 % par période × 3 × 365).
    (60.0, "surchauffe"),
    (-60.0, "short squeeze"),
])
def test_lecture_du_funding(annualise, attendu):
    from src.data_sources.binance_futures import _FUNDINGS_PER_DAY, _interpret

    taux = annualise / (_FUNDINGS_PER_DAY * 365 * 100)
    assert attendu in _interpret(taux)


def test_le_mot_sain_a_disparu_de_la_valeur_par_defaut():
    """« validée par un taux de financement SAIN de 10,95 %/an » (hebdo)."""
    from src.data_sources.binance_futures import _FUNDINGS_PER_DAY, _interpret

    lecture = _interpret(10.95 / (_FUNDINGS_PER_DAY * 365 * 100))
    assert "sain" not in lecture
    assert "AUCUNE thèse" in lecture


# ═══════════════════════════════════════════════════════════════════════════
# 5.20 — le funding neutre ne biaise plus la probabilité (défaut NOUVEAU)
# ═══════════════════════════════════════════════════════════════════════════
def test_un_funding_neutre_ne_deplace_pas_la_probabilite():
    """MESURÉ avant correction : −2,0 points de p_up pour une valeur qui ne
    dit rien — biais baissier sur presque tous les actifs, propagé à
    l'« Espérance 30 j » publiée sur chaque fiche."""
    from src.analytics.asset_plan import _FUNDING_NEUTRE, _prob_up_30d

    readout = {"rsi": 55.0, "trend_7d_pct": 2.0, "ma200_rel_pct": 5.0}
    sans = _prob_up_30d(readout, funding_annualized_pct=None)
    neutre = _prob_up_30d(readout, funding_annualized_pct=_FUNDING_NEUTRE)
    assert neutre == pytest.approx(sans, abs=0.005)


def test_les_vrais_extremes_inclinent_toujours():
    from src.analytics.asset_plan import _prob_up_30d

    readout = {"rsi": 55.0, "trend_7d_pct": 2.0, "ma200_rel_pct": 5.0}
    sans = _prob_up_30d(readout, funding_annualized_pct=None)
    assert _prob_up_30d(readout, funding_annualized_pct=-30.0) > sans
    assert _prob_up_30d(readout, funding_annualized_pct=60.0) < sans


# ═══════════════════════════════════════════════════════════════════════════
# 3.5 — concentration : constat quand elle vient des convictions cœur
# ═══════════════════════════════════════════════════════════════════════════
def test_le_bloc_concentration_ne_conseille_plus_dalleger_le_coeur():
    import pathlib

    src = pathlib.Path("src/main.py").read_text(encoding="utf-8")
    assert "_conviction_prime" in src
    assert "elle est " in src and "assumée, pas subie" in src


# ═══════════════════════════════════════════════════════════════════════════
# 2.8 — une ligne de sources le soir, SEULEMENT si une source est tombée
# ═══════════════════════════════════════════════════════════════════════════
def test_le_soir_signale_une_source_tombee_et_se_tait_sinon():
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_evening.html.j2").read_text(
        encoding="utf-8")
    assert "footer.sources_down" in gabarit
    assert "Source(s) indisponible(s) ce soir" in gabarit
    # Conditionnelle : rien ne s'affiche en régime nominal.
    i = gabarit.index("footer.sources_down")
    assert "{% if" in gabarit[max(0, i - 60):i]


# ═══════════════════════════════════════════════════════════════════════════
# 3.10 — consensus et précédent dans le calendrier hebdo
# ═══════════════════════════════════════════════════════════════════════════
def test_le_calendrier_hebdo_porte_consensus_et_precedent():
    import pathlib

    src = pathlib.Path("src/main.py").read_text(encoding="utf-8")
    # Trois occurrences dans main.py : on vise celle du bloc ``week_ahead``,
    # la dernière (l'agenda du matin portait déjà consensus et précédent).
    i = src.rindex('"already_published": bool(e.get("already_published")),')
    assert '"forecast": e.get("forecast")' in src[i:i + 400]
    gabarit = pathlib.Path(
        "src/reporting/templates/report_weekly.html.j2").read_text(
        encoding="utf-8")
    assert "cons. <strong" in gabarit and "préc. {{ev.previous}}" in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 5.7b — un lien macro dont le chiffre ne correspond à rien est RETIRÉ
# ═══════════════════════════════════════════════════════════════════════════
def test_lien_macro_aberrant_retire():
    """21/08 : « XRP   DXY 994 → » pour un DXY mesuré à 98,73."""
    from src.analytics.prose_guard import drop_unmatched_macro_drivers

    mesures = {"dxy": 98.73, "dxy_delta": -0.35, "nasdaq": 26067.0,
               "nasdaq_delta": -263.92}
    out, fixes = drop_unmatched_macro_drivers([
        {"asset": "XRP", "driver": "DXY 994", "effect": "…"},
        {"asset": "STX", "driver": "Nasdaq -263,92 points", "effect": "…"},
        {"asset": "BTC", "driver": "DXY 98,73", "effect": "…"},
    ], mesures)
    assert [e["asset"] for e in out] == ["STX", "BTC"]
    assert any("XRP" in f for f in fixes)


def test_un_driver_sans_indicateur_connu_est_intouche():
    """On ne retire QUE ce qu'on peut confronter à une mesure."""
    from src.analytics.prose_guard import drop_unmatched_macro_drivers

    out, fixes = drop_unmatched_macro_drivers(
        [{"asset": "TAO", "driver": "BoJ taux +0,114%", "effect": "…"}],
        {"dxy": 98.73})
    assert len(out) == 1 and not fixes


# ═══════════════════════════════════════════════════════════════════════════
# 3.11 — la consigne du move implicite couvre l'erreur inverse
# ═══════════════════════════════════════════════════════════════════════════
def test_la_consigne_couvre_le_confinement_impossible():
    import pathlib

    src = pathlib.Path("src/ai_brain/prompts/weekly_prompt.py").read_text(
        encoding="utf-8")
    assert "L'ERREUR INVERSE" in src
    assert "MAINTENANT LE PRIX ENTRE" in src


# ═══════════════════════════════════════════════════════════════════════════
# 1.14 / 3.13 — décisions de NE PAS changer, figées pour mémoire
# ═══════════════════════════════════════════════════════════════════════════
def test_les_titres_polymarket_restent_en_anglais():
    """Décision d'Omar : l'intitulé EST la définition du contrat ; le traduire
    risquerait d'en altérer un seuil ou une date."""
    import pathlib

    src = pathlib.Path("src/data_sources/prediction_markets.py").read_text(
        encoding="utf-8")
    assert "traduction" not in src.lower() or "Décision" in src


def test_lhebdo_na_quun_seul_declencheur_planifie():
    """Décision d'Omar du 02/10/2026 — RÉVISION de RT-9 (26/08).

    Mesuré le 01/10 sur l'historique public des workflows : en production,
    l'hebdo n'est lancé QUE par le schedule natif (6 runs « schedule »,
    0 « repository_dispatch ») — aucun job cron-job.org hebdo n'existe. Retirer
    le schedule (RT-9) aurait arrêté l'hebdo. Omar tranche : weekly_report.yml
    reste celui de la V30, schedule natif compris. Le seul déclencheur
    PLANIFIÉ est donc ce schedule ; la porte repository_dispatch reste ouverte
    pour un lancement manuel, et le groupe de concurrence empêche deux runs
    simultanés. ⚠ Ne pas créer de job cron-job.org hebdo (doublon).
    """
    import pathlib
    import re as _re

    wf = pathlib.Path(".github/workflows/weekly_report.yml").read_text(
        encoding="utf-8")
    actives = [l for l in wf.splitlines()
               if l.strip() and not l.strip().startswith("#")]
    assert any("schedule:" in l for l in actives)
    crons = [l for l in actives if "cron:" in l]
    assert crons == ["    - cron: '0 11 * * 0'"]
    assert any("concurrency:" in l for l in actives)
    assert "group: weekly-report" in wf
    # Le libellé « prochain hebdo » dérive de CE cron, pas d'une heure en dur.
    import src.main as M
    hh, mm = M._SLOTS_UTC["weekly"]
    assert _re.search(r"'0 (\d+) \* \* 0'", crons[0]).group(1) == str(hh) and mm == 0


def test_les_trois_rapports_ont_le_meme_montage_de_declencheur():
    """Un seul déclencheur PLANIFIÉ par rapport : cron-job.org pour le matin et
    le soir (repository_dispatch, sans schedule natif — v15, mails en double),
    le schedule natif pour l'hebdo (décision du 02/10). La comparaison des
    trois workflows reste le contrôle : c'est elle qui avait révélé l'écart."""
    import pathlib

    for nom in ("morning_report", "evening_report"):
        wf = pathlib.Path(f".github/workflows/{nom}.yml").read_text(
            encoding="utf-8")
        actives = [l for l in wf.splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        assert not any("schedule:" in l for l in actives), nom
        assert any("repository_dispatch:" in l for l in actives), nom
    wf = pathlib.Path(".github/workflows/weekly_report.yml").read_text(encoding="utf-8")
    actives = [l for l in wf.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert sum("schedule:" in l for l in actives) == 1


