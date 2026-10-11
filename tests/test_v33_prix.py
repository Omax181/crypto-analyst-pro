"""Audit 02/10 — un prix invraisemblable cité dans la narration est retiré.

Le rapport du 01/10 le laissait en limite : les chiffres de la prose n'étaient
vérifiés que pour quelques indicateurs, et « BTC 888 888 $ » (modèle hostile,
analyse de la revue hebdo) restait publié. Un montant n'est jugé que s'il est
en POSITION DE PRIX : une valeur de position, un gain ou un montant macro ne
l'est jamais.
"""

from __future__ import annotations

import pytest

from src.analytics.prose_guard import prix_invraisemblable, strip_unbacked_gestures

PRIX = {"BTC": (84166.0, 126080.0), "ETH": (4000.0, 4950.0),
        "JASMY": (0.0052, 4.79), "TAO": (270.0, 760.0), "SXT": (0.06, None),
        "RSR": (0.001666, 0.117)}


@pytest.mark.parametrize("phrase, attendu", [
    ("BTC 888 888 $.", "BTC"),
    ("BTC : 8 416 $ ce matin.", "BTC"),
    ("ETH sous 300 $ est invraisemblable.", "ETH"),
    ("JASMY à 72 $ serait absurde.", "JASMY"),
    ("BTC teste le support à 80 268 $ en clôture.", None),
    ("TAO vise son ATH de 760 $.", None),
    ("ETH casse 3 600 $ puis BTC 83 000 $.", None),
    ("RSR : invalidation 0,001332 $ franchie.", None),
    ("SXT (0,16 $) est une poussière.", None),
])
def test_un_prix_cite_est_juge_contre_le_cours_et_l_ath(phrase, attendu):
    assert prix_invraisemblable(phrase, PRIX) == attendu


@pytest.mark.parametrize("phrase", [
    "BTC (38,5 % du PTF, 1 531 $) reste le cœur.",
    "BTC pèse 1 531 $ dans le portefeuille.",
    "JASMY pèse 72 $, soit 2 % du PTF.",
    "BTC a généré un gain de 300 $ cette semaine.",
    "Le S&P 500 à 7 633 et l'or à 4 198 $.",
    "Les ETF BTC ont reçu 150 M$ d'entrées.",
])
def test_une_valeur_de_position_un_gain_ou_un_montant_macro_n_est_pas_un_prix(phrase):
    assert prix_invraisemblable(phrase, PRIX) is None


def test_la_garde_de_prose_retire_la_phrase_au_prix_invraisemblable():
    revue = [{"asset": "BTC", "analysis": "Expansion, dominance en hausse. BTC 888 888 $."}]
    out, fx = strip_unbacked_gestures(revue, {}, set(PRIX), PRIX)
    assert out[0]["analysis"] == "Expansion, dominance en hausse."
    assert fx and fx[0].startswith("prix invraisemblable retiré")
    # Sans prix de référence, la garde ne juge rien (jamais de faux retrait).
    out2, _ = strip_unbacked_gestures("BTC 888 888 $.", {}, {"BTC"})
    assert out2 == "BTC 888 888 $."
