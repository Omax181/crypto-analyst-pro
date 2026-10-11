"""V32.1 (11/10/2026) — vente totale de QNT, chaîne Telegram Fin_Watch.

Omar : « J'ai vendu toute ma position QNT : elle ne doit plus faire partie de
l'analyse ni du portefeuille » ; « ajouter Fin Watch (t.me/Fin_Watch) : ces
informations doivent être triées — géopolitique, crypto, FX, nominations,
actualités utiles à l'analyse ».
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
import yaml

from src.data_sources import telegram_reader as T

RACINE = pathlib.Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "update_portfolio_v32_1", RACINE / "scripts" / "update_portfolio.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PTF = """portfolio:
  # === TIER 1 (analyse deep) ===
  INJ:
    quantity: 10.07
    tier: 1
  QNT:
    quantity: 0.653
    pru: 70.64
    tier: 1
  STX:
    quantity: 232.29
    tier: 1
  # === TIER 4 (poussieres) ===
  W:
    quantity: 227.69
    tier: 4
"""


def test_la_vente_totale_retire_l_actif_et_rien_d_autre():
    out = _script().action_remove(_PTF, "QNT")
    assert "QNT" not in out
    assert out == _PTF.replace("  QNT:\n    quantity: 0.653\n    pru: 70.64\n    tier: 1\n", "")
    assert set(yaml.safe_load(out)["portfolio"]) == {"INJ", "STX", "W"}


def test_la_vente_totale_du_dernier_actif_d_un_tier_garde_les_commentaires():
    out = _script().action_remove(_PTF, "STX")
    assert "# === TIER 4 (poussieres) ===" in out and "STX" not in out
    assert set(yaml.safe_load(out)["portfolio"]) == {"INJ", "QNT", "W"}


def test_la_vente_totale_d_un_actif_absent_echoue_sans_rien_ecrire():
    with pytest.raises(SystemExit):
        _script().action_remove(_PTF, "SOL")


def test_le_portefeuille_est_reecrit_en_lf_quel_que_soit_l_os(tmp_path):
    cible = tmp_path / "portfolio.yaml"
    _script()._save_yaml_raw(cible, _PTF)
    assert b"\r" not in cible.read_bytes() and cible.read_text(encoding="utf-8") == _PTF


def test_qnt_ne_fait_plus_partie_du_portefeuille_analyse():
    from src.utils.portfolio_loader import load_portfolio
    pf = (load_portfolio() or {}).get("portfolio") or {}
    assert pf and "QNT" not in pf


def test_fin_watch_est_configuree_et_filtree_par_ses_propres_mots_cles():
    canaux = T._channels()
    assert canaux["Fin_Watch"]["filter"] is True
    assert {"bitcoin", "fed", "yield", "dollar", "iran", "tariff", "nominat"} \
        <= set(canaux["Fin_Watch"]["keywords"])
    # les autres chaînes ne changent pas
    assert canaux["bricsnews"] == {"filter": True, "keywords": []}
    assert canaux["WatcherGuru"]["filter"] is False


@pytest.mark.parametrize("titre", [
    "US 10Y, 20Y and 30Y yield highest since 2002.",
    "JUST IN: The Federal Reserve says most participants saw another rate hike as likely",
    "JUST IN: Iran's IRGC says it has 'full control' of the Strait of Hormuz.",
    "Market Overview: SPX: $7818, NASDAQ: $27599, DXY: $102, Gold: $4096, Bitcoin: $83452",
    "President Trump says it's time for Ukraine to get a new president.",
    "India's central bank raises interest rates for the first time since 2023.",
    "Senate confirms the nominee for Fed chair.",
])
def test_fin_watch_garde_marches_macro_geopolitique_nominations(titre):
    assert T._pertinent(titre, T._channels()["Fin_Watch"])


@pytest.mark.parametrize("titre", [
    "Magnitude 8.0 earthquake strikes Panama.",
    "BREAKING: Elon Musk takes aim at a rival tycoon over Starlink.",
    "JUST IN: Amazon to lay off over 1,000 workers in its Stores unit",
    "Corporate software award announced in Bengaluru.",
])
def test_fin_watch_ecarte_le_hors_sujet(titre):
    assert not T._pertinent(titre, T._channels()["Fin_Watch"])


def test_les_mots_cles_propres_se_lisent_en_debut_de_mot():
    cfg = {"filter": True, "keywords": ["rate", "war"]}
    assert T._pertinent("Rates rise; trade war fears", cfg)
    assert not T._pertinent("corporate software award", cfg)


def test_sans_liste_propre_le_filtre_commun_reste_une_sous_chaine():
    cfg = {"filter": True, "keywords": []}
    assert T._pertinent("Petrodollar system under pressure", cfg)   # « dollar »
    assert not T._pertinent("Local football results", cfg)
    assert T._pertinent("Local football results", {"filter": False, "keywords": []})
