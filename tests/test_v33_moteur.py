"""Moteur d'opportunité v33 — propriétés de décision (audit zero-trust 01/10).

Remplace ``test_v33_opportunity.py``, ``test_v33_preuve.py``,
``test_v33_corrections.py`` et la partie moteur de ``test_v33_audit.py`` :
ces fichiers étaient écrits pour un moteur qui lisait une clé ``mc_tvl`` que
rien ne produit, ancrait le MVRV à 1,0, comparait un actif aux AUTRES POSITIONS
DÉTENUES et plafonnait l'exposition. Chaque intention encore valable est
reprise ici, sur des candidats à la forme EXACTE de l'adaptateur.

Règle d'écriture : aucune assertion ne fige un rendement requis chiffré — on
le calcule avec le moteur et on raisonne relativement à lui.
"""

from __future__ import annotations

import ast
import copy
import pathlib
import re

import pytest

from src.analytics import evidence as EV
from src.analytics import opportunity as O
from src.analytics.exit_radar import compute_exit_signals

RACINE = pathlib.Path(__file__).resolve().parents[1]

# Distribution historique MESURÉE (Coin Metrics, 30/09/2026).
HIST_BTC = {"median": 1.7158, "q25": 1.2828, "q75": 2.2316, "min": 0.3868,
            "max": 146.0383, "n": 5919, "first": "2010-07-18",
            "last": "2026-09-30"}
HIST_ETH = {"median": 1.2128, "q25": 0.9649, "q75": 1.5555, "min": 0.2958,
            "max": 6.0863, "n": 4072, "first": "2015-08-08",
            "last": "2026-09-30"}


def alt(sym: str, **kw) -> dict:
    """Satellite « médian » à la forme de sortie de l'adaptateur."""
    c = {"asset": sym, "weight_pct": 2.0, "value_usd": 80.0, "price": 10.0,
         "daily_vol_pct": 4.0, "beta_portfolio": 0.8, "volume_to_mcap": 0.05,
         "volume_24h_usd": 5e7, "dilution_remaining_pct": 20.0,
         "coverage": 0.6, "mvrv": None, "mvrv_stale": None,
         "mvrv_history": None, "mc_tvl": None, "mc_tvl_peers": None,
         "dev_active": True, "sources": {}}
    c.update(kw)
    return c


def btc(mvrv: float = 1.56, **kw) -> dict:
    c = alt("BTC", weight_pct=38.0, value_usd=1500.0, price=83500.0,
            daily_vol_pct=2.0, beta_portfolio=1.0, volume_to_mcap=0.02,
            volume_24h_usd=3e10, dilution_remaining_pct=4.5, coverage=0.83,
            mvrv=mvrv, mvrv_stale=False, mvrv_history=dict(HIST_BTC))
    c.update(kw)
    return c


def eth(mvrv: float = 1.14, **kw) -> dict:
    c = alt("ETH", weight_pct=20.0, value_usd=800.0, price=2900.0,
            daily_vol_pct=2.9, beta_portfolio=1.1, volume_to_mcap=0.04,
            volume_24h_usd=1.5e10, dilution_remaining_pct=0.0, coverage=0.67,
            mvrv=mvrv, mvrv_stale=False, mvrv_history=dict(HIST_ETH))
    c.update(kw)
    return c


def univers(*cands: dict) -> list[dict]:
    """Un univers réaliste : les candidats donnés + des satellites médians."""
    base = [alt(f"S{i}", daily_vol_pct=3.5 + 0.25 * i,
                volume_24h_usd=3e7 + 1e7 * i,
                dilution_remaining_pct=10.0 + 5 * i, coverage=0.5 + 0.03 * i)
            for i in range(7)]
    return list(cands) + base


def decide(c: dict, cands: list[dict] | None = None, **kw) -> dict:
    u = O.measure_universe(cands if cands is not None else univers(c))
    return O.decide(c, u, ptf_value_usd=kw.pop("ptf", 4000.0), **kw)


def requis(c: dict, cands: list[dict] | None = None) -> float:
    u = O.measure_universe(cands if cands is not None else univers(c))
    return O.required_return(c, u)["required_pct"]


# ══════════════════════════════════════════════════════════════════════════
# 1 · STRUCTURE — le requis ne dépend jamais de la taille ni du poids
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("poids", [0.0, 2.0, 20.0, 38.0, 80.0])
def test_le_requis_ne_depend_ni_du_poids_ni_de_la_taille(poids):
    ref = requis(btc(), univers(btc()))
    c = btc(weight_pct=poids)
    assert requis(c, univers(c)) == ref


def test_une_grosse_taille_ne_sauve_jamais_une_mauvaise_these():
    """Un C4 reste C4 quel que soit le capital engagé ou le poids détenu."""
    for ptf in (1e3, 1e5, 1e7):
        for poids in (0.0, 50.0):
            d = decide(btc(mvrv=1.6, weight_pct=poids), ptf=ptf)
            assert d["condition_failed"] == "C4" and not d["decided"]


def test_l_actif_median_exige_exactement_le_repere_declare():
    """Le repère d'Omar (≈ 20 %) est le requis de l'actif MÉDIAN, rien d'autre."""
    cands = [alt(f"M{i}") for i in range(9)]
    r = O.required_return(cands[0], O.measure_universe(cands))
    assert r["multiplier"] == 1.0
    assert r["required_pct"] == pytest.approx(
        O._cfg("required_return_reference_pct"), abs=0.01)


def test_un_actif_risque_exige_plus_un_actif_solide_moins():
    ref = O._cfg("required_return_reference_pct")
    risque = alt("RISK", daily_vol_pct=9.0, volume_24h_usd=1e6,
                 dilution_remaining_pct=150.0, coverage=0.5)
    assert requis(risque) > ref
    assert requis(btc()) < ref


@pytest.mark.parametrize("champ", ["daily_vol_pct", "volume_24h_usd",
                                   "dilution_remaining_pct", "coverage"])
def test_une_donnee_absente_ne_recompense_jamais(champ):
    c = alt("X")
    mesure = requis(c)
    c_abs = dict(c, **{champ: None})
    assert requis(c_abs) >= mesure


def test_la_liquidite_se_lit_en_volume_absolu_pas_en_rotation():
    """BTC tourne 2 % de sa capitalisation par jour (moins qu'un alt) mais
    c'est l'actif le PLUS liquide : le facteur doit le dire (audit F26)."""
    u = O.measure_universe(univers(btc()))
    f = O.modulation_multiplier(btc(), u)["factors"]["liquidity"]
    assert f["measured"] and f["value"] == O._cfg("modulation.liquidity.min")


# ══════════════════════════════════════════════════════════════════════════
# 2 · CONFIGURATION — ancrages métier sans repli, aucun seuil en dur
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("chemin", [
    "required_return_reference_pct", "cash_benchmark_annual_pct",
    "round_trip_cost_pct", "reference_horizon_days", "core_sizing_assets",
    "sizing_bands.core.normal", "sizing_bands.core.exceptional",
    "modulation.volatility.max", "preuve.couverture_min",
])
def test_un_ancrage_manquant_desarme_sans_seuil_invente(monkeypatch, chemin):
    cfg = copy.deepcopy(O._CFG)
    node, parts = cfg, chemin.split(".")
    for p in parts[:-1]:
        node = node[p]
    del node[parts[-1]]
    monkeypatch.setattr(O, "_CFG", cfg)
    d = decide(btc(mvrv=0.9))
    assert d["condition_failed"] == "CONFIG" and not d["decided"]


def test_les_decisions_metier_d_omar_sont_dans_la_configuration():
    assert O._cfg("cash_benchmark_annual_pct") == 5.0
    assert O._cfg("required_return_reference_pct") == 20.0
    assert sorted(O._cfg("core_sizing_assets")) == ["BTC", "ETH"]
    assert O._cfg("sizing_bands.core.normal") == [5.0, 10.0]
    assert O._cfg("sizing_bands.core.exceptional") == [15.0, 20.0]
    assert O._cfg("sizing_bands.satellite.normal") == [3.0, 5.0]
    assert O._cfg("sizing_bands.satellite.exceptional") == [5.0, 8.0]
    # Décision d'Omar (01/10) : AUCUN plafond d'exposition, aucune matérialité.
    for retire in ("concentration_caps", "materiality", "exceptional_margin"):
        assert O._cfg(retire) is None


def _identifiants_et_litteraux(chemin: pathlib.Path):
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    noms, nombres = set(), []
    docstrings = {id(n.body[0].value) for n in ast.walk(arbre)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
    for n in ast.walk(arbre):
        if isinstance(n, ast.Name):
            noms.add(n.id.lower())
        elif isinstance(n, ast.Attribute):
            noms.add(n.attr.lower())
        elif (isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
              and not isinstance(n.value, bool) and id(n) not in docstrings):
            nombres.append(n.value)
    imports = [a.name for n in ast.walk(arbre) if isinstance(n, ast.Import)
               for a in n.names]
    imports += [n.module or "" for n in ast.walk(arbre)
                if isinstance(n, ast.ImportFrom)]
    return noms, nombres, imports


@pytest.mark.parametrize("fichier", ["src/analytics/opportunity.py",
                                     "src/analytics/opportunity_adapter.py",
                                     "src/analytics/forecast.py"])
def test_aucune_logique_bannie_dans_le_moteur(fichier):
    noms, _, imports = _identifiants_et_litteraux(RACINE / fichier)
    assert not ({"ath", "ath_distance_pct", "kelly", "argmax", "confidence",
                 "probability_pct", "prob_up_30d"} & noms)
    assert not any("ai_brain" in i or "gemini" in i for i in imports)


def test_aucun_seuil_metier_en_dur_dans_le_moteur():
    """Les seuls littéraux tolérés sont des constantes mathématiques ou de
    format (0, 1, 2, 100, 365, quantiles) — aucun 20, 5, 25, 12, 1,5…"""
    _, nombres, _ = _identifiants_et_litteraux(RACINE / "src/analytics/opportunity.py")
    toleres = {0, 1, 2, 3, 100, 365, 1.0, 0.0, 2.0, 100.0, 365.0, 9, 4, 5, 6,
               1e6}                                  # 1e6 : affichage en M$
    suspects = [n for n in nombres if n not in toleres]
    assert not suspects, suspects
    # 4/5/6 n'apparaissent QUE comme rangs de sévérité de l'abstention.
    src = (RACINE / "src/analytics/opportunity.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<![\d.])(20\.0|5\.0|1\.5|25\.0|12\.0)(?![\d])", src)


# ══════════════════════════════════════════════════════════════════════════
# 3 · PREUVE — MVRV vers SA médiane historique ; amplitude ≠ crédibilité
# ══════════════════════════════════════════════════════════════════════════

def test_le_mvrv_est_ramene_a_sa_mediane_historique_mesuree():
    p = O.measured_potential(btc(mvrv=1.0), {})
    assert p["available"] and p["anchor"] == "mvrv"
    assert p["potential_pct"] == pytest.approx((1.7158 - 1) * 100, abs=0.01)
    assert p["potential_conservative_pct"] == pytest.approx((1.2828 - 1) * 100, abs=0.01)
    assert p["preuve"]["classe"] == EV.CALCUL
    assert any("prémisse" in r for r in p["preuve"]["reserves"])


@pytest.mark.parametrize("mvrv", [0.01, 0.2, 0.38, 200.0])
def test_un_mvrv_hors_du_support_historique_est_une_donnee_cassee(mvrv):
    d = decide(btc(mvrv=mvrv))
    assert d["condition_failed"] == "C0" and not d["decided"]
    assert "support historiquement observé" in d["reason"]


def test_un_mvrv_perime_n_est_pas_un_fait_du_jour():
    d = decide(btc(mvrv=0.8, mvrv_stale=True))
    assert d["condition_failed"] == "C0" and "périmé" in d["reason"]


def test_sans_historique_mesure_il_n_existe_pas_d_ancre():
    d = decide(btc(mvrv=0.8, mvrv_history=None))
    assert d["condition_failed"] == "C0" and "historique" in d["reason"]


@pytest.mark.parametrize("couverture", [None, 0.0, 0.3, 0.49])
def test_une_couverture_inconnue_ou_insuffisante_declasse_en_scenario(couverture):
    d = decide(btc(mvrv=0.6, coverage=couverture))
    assert d["condition_failed"] == "C0" and not d["decided"]


def _mvrv_pour(potentiel_pct: float, hist=HIST_BTC) -> float:
    return hist["median"] / (1 + potentiel_pct / 100.0)


@pytest.mark.parametrize("libelle,potentiel,couverture,attendu", [
    ("+3 % ordinaire", 3.0, 0.83, False),
    ("+10 % preuve robuste", 10.0, 0.83, False),
    ("+50 % preuve robuste", 50.0, 0.83, True),
    ("+100 % preuve robuste", 100.0, 0.83, True),
    ("+100 % données insuffisantes", 100.0, 0.2, False),
    ("+200 % preuve faible", 200.0, 0.3, False),
    ("+400 % preuve faible", 400.0, 0.3, False),
    ("+400 % hors du support observé", 400.0, 0.83, False),
])
def test_les_cas_imposes_amplitude_vs_credibilite(libelle, potentiel, couverture, attendu):
    c = btc(mvrv=_mvrv_pour(potentiel), coverage=couverture)
    d = decide(c)
    assert bool(d["decided"]) is attendu, (libelle, d.get("reason"))


def test_une_preuve_faible_ne_se_compense_jamais_par_l_amplitude():
    for pot in (60.0, 150.0, 300.0):
        d = decide(btc(mvrv=_mvrv_pour(pot), coverage=0.3))
        assert d["condition_failed"] == "C0"


def test_btc_tres_attractif_est_recommande_btc_neutre_ne_l_est_pas():
    assert decide(btc(mvrv=0.9))["decided"] is True
    assert decide(btc(mvrv=1.56))["decided"] is False      # 30/09 : +10 %
    assert decide(eth(mvrv=1.14), univers(eth(mvrv=1.14)))["decided"] is False


# ══════════════════════════════════════════════════════════════════════════
# 4 · MC/TVL — pairs de la MÊME CATÉGORIE, comparabilité, support observé
# ══════════════════════════════════════════════════════════════════════════

def _pairs(ratios, cat="Lending"):
    return {"available": True, "category": cat, "ratios": list(ratios),
            "n": len(ratios), "source": "test"}


HOMOGENES = [5.6, 6.0, 6.3, 6.8, 7.0, 7.4, 7.9, 8.4]


def test_des_pairs_homogenes_donnent_un_potentiel_dans_le_support():
    c = alt("PROTO", mc_tvl=5.8, mc_tvl_peers=_pairs(HOMOGENES))
    p = O.measured_potential(c, {})
    assert p["available"] and p["anchor"] == "mc_tvl_peer"
    med = EV.dispersion_log(HOMOGENES)["mediane"]
    assert p["potential_pct"] == pytest.approx((med / 5.8 - 1) * 100, abs=0.05)
    assert p["comparabilite"]["categorie"] == "Lending"


def test_des_pairs_aussi_disperses_que_dans_la_realite_ne_sont_pas_une_reference():
    """Mesuré le 30/09 : MAD_log 0,88 à 2,88 dans TOUTES les catégories
    DeFiLlama. Une telle dispersion n'est pas une référence de valorisation."""
    disperses = [0.05, 0.2, 0.5, 0.9, 1.3, 3.0, 8.0, 25.0, 60.0]
    assert EV.dispersion_log(disperses)["mad_log"] > O._cfg("preuve.mad_log_max")
    d = decide(alt("PROTO", mc_tvl=0.3, mc_tvl_peers=_pairs(disperses)))
    assert d["condition_failed"] == "C0" and "hétérogènes" in d["reason"]


def test_trop_peu_de_pairs_n_est_pas_une_reference():
    d = decide(alt("PROTO", mc_tvl=5.0, mc_tvl_peers=_pairs([6.0, 7.0, 8.0])))
    assert d["condition_failed"] == "C0" and "insuffisant" in d["reason"]


def test_sans_pair_de_meme_categorie_pas_de_potentiel():
    d = decide(alt("PROTO", mc_tvl=5.0, mc_tvl_peers=None))
    assert d["condition_failed"] == "C0"


def test_hors_du_support_c_est_un_signalement_jamais_une_recommandation():
    c = alt("PROTO", mc_tvl=1.0, mc_tvl_peers=_pairs(HOMOGENES), coverage=0.9)
    res = O.decide_universe(univers(c), ptf_value_usd=4000.0)
    d = next(x for x in res["decisions"] if x["asset"] == "PROTO")
    assert d["condition_failed"] == "C0" and not d["decided"]
    assert [s["asset"] for s in res["signalements"]] == ["PROTO"]
    assert all(f["asset"] != "PROTO" for f in res["firm"])


def test_la_garde_mad_borne_un_pair_corrompu():
    corrompus = HOMOGENES + [0.01]           # un TVL aberrant côté bon marché
    disp = EV.dispersion_log(corrompus)
    comp = EV.comparabilite(3.0, disp, k_aberrant=O._cfg("preuve.k_aberrant"),
                            mad_max=O._cfg("preuve.mad_log_max"),
                            n_min=O._cfg("valuation.peer_min_sample"))
    assert comp["borne_liante"].startswith("garde MAD")


def test_k_vaut_trois_sigma_exprimes_en_mad():
    assert O._cfg("preuve.k_aberrant") == pytest.approx(3 * 1.4826, abs=1e-3)


# ══════════════════════════════════════════════════════════════════════════
# 5 · TAILLE — bandes d'Omar, ancre prudente, AUCUN plafond d'exposition
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sym,palier", [("BTC", "core"), ("ETH", "core"),
                                        ("TAO", "satellite"), ("LINK", "satellite"),
                                        ("QNT", "satellite")])
def test_seuls_btc_et_eth_ont_les_bandes_coeur(sym, palier):
    b = O.size_bands(alt(sym))
    assert b["tier"] == palier
    assert b["normal"] == O._cfg(f"sizing_bands.{palier}.normal")
    assert b["exceptional"] == O._cfg(f"sizing_bands.{palier}.exceptional")


def test_la_bande_exceptionnelle_exige_que_l_ancre_prudente_couvre_le_requis():
    r = requis(btc())
    # médiane couvre le requis, premier quartile non → bande NORMALE
    mvrv_normal = HIST_BTC["median"] / (1 + (r + 5) / 100)
    assert HIST_BTC["q25"] / mvrv_normal - 1 < r / 100
    d = decide(btc(mvrv=mvrv_normal))
    assert d["decided"] and d["size"]["band_label"] == "normale"
    assert d["size"]["band"] == [5.0, 10.0]
    # premier quartile couvre aussi le requis → bande EXCEPTIONNELLE
    mvrv_exc = HIST_BTC["q25"] / (1 + (r + 2) / 100)
    d = decide(btc(mvrv=mvrv_exc))
    assert d["decided"] and d["size"]["band_label"] == "exceptionnelle"
    assert d["size"]["band"] == [15.0, 20.0]


@pytest.mark.parametrize("poids", [38.0, 60.0, 90.0])
def test_aucun_plafond_d_exposition_ne_bloque_un_renfort(poids):
    """Omar, 01/10 : « Toute crypto peut être renforcée si la conviction le
    justifie, y compris BTC/ETH au-delà de 25 %. »"""
    d = decide(btc(mvrv=0.9, weight_pct=poids))
    assert d["decided"] and d["action"] == "RENFORCER"
    assert d["size_bands"]["weight_pct"] == poids          # publié, pas plafonné


def test_la_taille_est_la_borne_basse_de_la_bande_jamais_un_argmax():
    d = decide(btc(mvrv=0.9), ptf=4000.0)
    lo, hi = d["size"]["band"]
    assert d["size"]["pct"] == lo
    assert d["size"]["usd_band"] == [4000.0 * lo / 100, 4000.0 * hi / 100]


def test_le_beta_n_entre_dans_aucune_decision():
    a = decide(btc(mvrv=0.9, beta_portfolio=0.2))
    b = decide(btc(mvrv=0.9, beta_portfolio=2.5))
    assert (a["decided"], a["required"]["required_pct"], a["size"]["band"]) == \
           (b["decided"], b["required"]["required_pct"], b["size"]["band"])
    assert b["size_bands"]["effective_weight_pct"] == pytest.approx(38.0 * 2.5)


# ══════════════════════════════════════════════════════════════════════════
# 6 · VIABILITÉ ET ALLÈGEMENT — aucun seuil ne déclenche une vente
# ══════════════════════════════════════════════════════════════════════════

def test_une_liquidite_eteinte_ferme_le_renfort_mais_ne_vend_jamais():
    c = btc(mvrv=0.9, volume_to_mcap=0.001)
    assert decide(c)["condition_failed"] == "C1"
    assert O.decide_reduce(c)["decided"] is False


def test_un_developpement_mort_mesure_ferme_le_renfort():
    d = decide(btc(mvrv=0.9, dev_active=False))
    assert d["condition_failed"] == "C1" and "90 jours" in d["reason"]


def test_une_anomalie_de_donnee_n_est_ni_un_refus_ni_une_vente():
    """JASMY, 30/09 : « ALLÉGER — prix ou historique suspect »."""
    c = btc(mvrv=0.9, price_suspect=True, data_age_days=30)
    assert decide(c)["decided"] is True
    assert O.decide_reduce(c)["decided"] is False


@pytest.mark.parametrize("poids", [12.0, 38.0, 90.0])
def test_un_poids_eleve_ne_declenche_jamais_d_allegement(poids):
    assert O.decide_reduce(alt("SAT", weight_pct=poids))["decided"] is False
    assert O.decide_reduce(btc(weight_pct=poids))["decided"] is False


def test_l_allegement_ne_vient_que_des_regles_de_prise_de_profit():
    sig = {"symbol": "QNT", "reason": "×3 atteint (+321% vs PRU)",
           "action": "allège une grosse tranche (prise de profit)"}
    r = O.decide_reduce(alt("QNT"), exit_signal=sig)
    assert r["decided"] and r["trigger"] == "radar_sortie"
    assert "×3 atteint" in r["reason"]


def test_le_radar_n_a_plus_de_regle_de_concentration():
    sat_lourd = {"symbol": "SAT", "pnl_pct": 5.0, "weight_pct": 30.0,
                 "change_7d": 2.0, "change_24h": 1.0}
    assert compute_exit_signals([sat_lourd])["count"] == 0
    palier = dict(sat_lourd, pnl_pct=85.0)
    assert compute_exit_signals([palier])["signals"][0]["symbol"] == "SAT"
    coeur = {"symbol": "BTC", "pnl_pct": 150.0, "weight_pct": 60.0,
             "change_7d": 50.0, "change_24h": 25.0}
    assert compute_exit_signals([coeur])["count"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 7 · CAS B — arbitrage réellement évalué, jamais un intouchable vendu
# ══════════════════════════════════════════════════════════════════════════

def _mesurable(sym, mvrv, **kw):
    kw.setdefault("coverage", 0.8)
    return alt(sym, mvrv=mvrv, mvrv_stale=False, mvrv_history=dict(HIST_BTC), **kw)


def test_un_arbitrage_est_evalue_et_exige_une_dominance_mesurable():
    achat = _mesurable("AAA", 0.9, dilution_remaining_pct=5.0, volume_24h_usd=9e7)
    vente = _mesurable("BBB", 2.4, dilution_remaining_pct=40.0, volume_24h_usd=2e7,
                       weight_pct=4.0, coverage=0.7)
    res = O.decide_universe(univers(achat, vente), ptf_value_usd=4000.0,
                            intouchables={"BTC", "ETH", "TAO", "LINK"})
    arb = [(a["asset"], a["sell_asset"]) for a in res["arbitrages"]]
    assert ("AAA", "BBB") in arb
    # même paire, mais l'achat est MOINS liquide : plus de dominance → refus
    achat2 = dict(achat, volume_24h_usd=1e6)
    d = O.decide(achat2, O.measure_universe(univers(achat2, vente)),
                 funding_case="B", funded_by=vente)
    assert d["condition_failed"] in ("C5", "C4") and not d["decided"]


def test_un_intouchable_n_est_jamais_vendu_pour_en_financer_un_autre():
    achat = _mesurable("AAA", 0.9, dilution_remaining_pct=5.0, volume_24h_usd=9e10)
    res = O.decide_universe(univers(achat, btc(mvrv=2.6)), ptf_value_usd=4000.0,
                            intouchables={"BTC", "ETH", "TAO", "LINK"})
    assert all(a["sell_asset"] != "BTC" for a in res["arbitrages"])


def test_vendre_sur_une_hypothese_est_exclu():
    achat = _mesurable("AAA", 0.9)
    vente = alt("BBB", weight_pct=4.0)                      # aucun potentiel mesuré
    d = O.decide(achat, O.measure_universe(univers(achat, vente)),
                 funding_case="B", funded_by=vente)
    assert d["condition_failed"] == "C0" and not d["decided"]


# ══════════════════════════════════════════════════════════════════════════
# 8 · ABSTENTION, HORIZON, CLASSE DU REQUIS, LOCALISATION
# ══════════════════════════════════════════════════════════════════════════

def test_zero_recommandation_est_normal_et_motive():
    res = O.decide_universe(univers(btc(), eth()), ptf_value_usd=4000.0)
    assert res["count"] == 0 and res["firm"] == []
    assert res["closest_miss"]["asset"] in ("BTC", "ETH")
    assert res["closest_miss"]["reason"]
    assert res["potential_measurable"] == 2


def test_aucun_quota_le_nombre_de_recos_ne_depend_que_des_actifs():
    a = O.decide_universe(univers(btc(mvrv=0.9)), ptf_value_usd=4000.0)
    b = O.decide_universe(univers(btc(mvrv=0.9)) + [alt(f"Z{i}") for i in range(30)],
                          ptf_value_usd=4000.0)
    assert [f["asset"] for f in a["firm"]] == [f["asset"] for f in b["firm"]] == ["BTC"]


@pytest.mark.parametrize("h", [7, 14, 30, 90, 180])
def test_aucun_horizon_court_ne_declenche(h):
    d = decide(btc(mvrv=0.5), horizon_days=h)
    assert not d["decided"] and d["condition_failed"] in ("C6", "C0")
    r_court = O.required_return(btc(), O.measure_universe(univers(btc())), horizon_days=h)
    assert r_court["required_pct"] >= requis(btc())


def test_le_requis_est_un_scenario_tant_que_les_couts_sont_supposes(monkeypatch):
    r = O.required_return(btc(), O.measure_universe(univers(btc())))
    assert r["preuve"]["classe"] == EV.SCENARIO and not r["costs_measured"]
    cfg = copy.deepcopy(O._CFG)
    cfg["cost_is_measured"] = True
    monkeypatch.setattr(O, "_CFG", cfg)
    r2 = O.required_return(btc(), O.measure_universe(univers(btc())))
    assert r2["preuve"]["classe"] == EV.CALCUL


def test_les_motifs_publies_sont_en_decimale_francaise():
    res = O.decide_universe(univers(btc(), eth(), btc(asset="BTX", mvrv=0.9)),
                            ptf_value_usd=4000.0)
    for d in res["decisions"]:
        assert not re.search(r"\d\.\d", d.get("reason") or ""), d["reason"]
        assert not re.search(r"\d\.\d", (d.get("potential") or {}).get("basis") or "")


def test_types_faux_et_univers_degenere_ne_levent_jamais():
    bizarres = [alt("X", daily_vol_pct="abc", coverage=float("nan"),
                    volume_24h_usd=-5, mvrv=True), {}, alt("Y", weight_pct=None)]
    res = O.decide_universe(bizarres, ptf_value_usd=None)
    assert res["available"] and res["count"] == 0
    assert O.decide_universe([], ptf_value_usd=0)["count"] == 0


# ── reprises des anciens fichiers (intentions toujours valables) ──────────

def test_cas_a_et_cas_b_different_structurellement():
    u = O.measure_universe(univers(btc()))
    a = O.required_return(btc(), u, funding_case="A")
    vente = alt("VND", daily_vol_pct=6.0)
    b = O.required_return(btc(), u, funding_case="B", funded_by=vente)
    assert a["components"]["costs_legs"] == 1 and b["components"]["costs_legs"] == 2
    assert a["components"]["benchmark_pct"] > 0 and b["components"]["benchmark_pct"] == 0
    assert b["components"]["differential"] is True


def test_la_dominance_refuse_l_egalite_stricte():
    x = alt("X")
    assert O.check_dominance(x, dict(x, asset="Y"))["dominates"] is False


def test_la_viabilite_signale_les_inconnues_sans_les_sanctionner():
    v = O.check_viability(alt("X", volume_to_mcap=None, dilution_remaining_pct=None,
                              dev_active=None))
    assert v["viable"] is True
    assert set(v["unknown"]) == {"liquidité", "dilution restante",
                                 "activité de développement"}


def test_un_portefeuille_mono_actif_reste_decidable():
    res = O.decide_universe([btc(mvrv=0.9, weight_pct=100.0)], ptf_value_usd=1000.0)
    assert res["count"] == 1 and res["firm"][0]["asset"] == "BTC"


def test_la_sensibilite_aux_couts_est_publiee():
    r = O.required_return(btc(), O.measure_universe(univers(btc())))
    assert any("sensibilité" in x for x in r["preuve"]["reserves"])


def test_sans_potentiel_mesure_il_n_y_a_pas_de_plus_proche():
    """Chaîne rejouée, historique MVRV coupé : « Le plus proche : 1000SATS —
    preuve insuffisante » (premier par ordre alphabétique)."""
    import src.main as M
    res = O.decide_universe(univers(alt("1000SATS"), alt("ACH")), ptf_value_usd=4000.0)
    assert res["potential_measurable"] == 0
    s = M._opportunity_summary({"opportunity": res})
    assert s["closest"] is None and "potentiel mesurable." in s["empty_line"]
    vide = M._opportunity_summary({"opportunity": O.decide_universe([], ptf_value_usd=0.0)})
    assert "univers vide" in vide["empty_line"]
    # avec un actif mesuré, le plus proche est publié
    res2 = O.decide_universe(univers(btc(), alt("ACH")), ptf_value_usd=4000.0)
    assert M._opportunity_summary({"opportunity": res2})["closest"]["asset"] == "BTC"
