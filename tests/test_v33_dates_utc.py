# -*- coding: utf-8 -*-
"""v33.1 — le calendrier macro raisonne en UTC, pas dans le fuseau du runner.

DÉFAUT TROUVÉ LE 27/08/2026, PRÉSENT DANS LE LIVRABLE DE LA VEILLE
==================================================================

``get_upcoming_releases`` (et deux autres modules de calendrier) calculaient
« aujourd'hui » avec ``date.today()`` — la date **locale de la machine** —
alors que tout le reste du projet raisonne en UTC : workflows, horodatages,
persistance, mails.

Conséquence mesurée à 00 h 08 heure de Casablanca (23 h 08 UTC) : la date
locale valait le 27/08 et la date UTC le 26/08. Une publication attendue
« demain » devenait donc « aujourd'hui », et le test de non-régression
correspondant échouait.

Ce n'est pas cosmétique. Le script de déploiement **exige la suite complète au
vert** : un déploiement lancé depuis la machine d'Omar entre minuit et 1 h
échouait. Le défaut est invisible sur GitHub Actions, dont le runner est en
UTC — c'est précisément pourquoi il a survécu.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

_MODULES_CALENDRIER = (
    "src/data_sources/fred.py",
    "src/data_sources/econ_calendar.py",
    "src/data_sources/macro_calendar.py",
)


@pytest.mark.parametrize("mod", _MODULES_CALENDRIER)
def test_aucun_calendrier_ne_lit_la_date_locale(mod):
    """``date.today()`` ne doit plus apparaître dans le code exécutable.

    Contrôle sur le CODE, commentaires exclus : l'explication du correctif
    cite forcément l'appel fautif, et un grep naïf échouerait sur sa propre
    documentation — le piège RT-3.
    """
    src = pathlib.Path(mod).read_text(encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert not re.search(r"\bdate\.today\(\)", code), (
        f"{mod} calcule « aujourd'hui » dans le fuseau du runner")
    assert "timezone.utc" in code, f"{mod} ne référence pas UTC"


def test_les_publications_a_venir_sont_datees_en_utc(monkeypatch):
    """Preuve fonctionnelle : « demain » en UTC reste à J+1.

    Le test unitaire historique (``test_v11_features``) exerce le même chemin ;
    celui-ci le rend explicite et nomme la raison, pour qu'une régression soit
    lisible plutôt que mystérieuse.
    """
    from src.data_sources import fred

    monkeypatch.setenv("FRED_API_KEY", "x")
    aujourdhui = datetime.now(timezone.utc).date()
    demain = (aujourdhui + timedelta(days=1)).strftime("%Y-%m-%d")
    hier = (aujourdhui - timedelta(days=3)).strftime("%Y-%m-%d")

    monkeypatch.setattr(fred, "get_json",
                        lambda url, params=None, **kw: {
                            "release_dates": [{"date": hier}, {"date": demain}]})
    if hasattr(fred.CACHE, "_store"):
        fred.CACHE._store.clear()

    out = fred.get_upcoming_releases(horizon_days=10)
    assert out["available"] is True
    assert any(e["days_ahead"] == 1 for e in out["events"]), (
        "une publication de demain (UTC) n'est plus à J+1 : "
        "mélange de fuseaux réintroduit")
    # Une date passée n'est jamais retenue.
    assert all(e["days_ahead"] >= 0 for e in out["events"])
