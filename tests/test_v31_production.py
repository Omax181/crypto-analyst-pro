# -*- coding: utf-8 -*-
"""V31.1 — les quatre défauts trouvés par le PREMIER RUN RÉEL (15/08/2026).

Aucun d'eux n'était détectable par la suite v31.0 : trois exigeaient le
réseau, le quatrième exigeait la forme réelle d'une charge que les tests
fabriquaient eux-mêmes. Ils sont verrouillés ici.

    1. `get_news` renvoie un DICT ; `main` le passait tel quel à `news_view`,
       qui le tranchait -> « unhashable type: slice ». Aucun mail n'est parti.
    2. CoinGecko REFUSE au-delà de 365 jours (401, error_code 10012). La
       v31.0 demandait 400 : zéro clôture sur les 29 actifs.
    3. Le chien de garde ne pouvait pas alerter (`tenacity` absent du job).
    4. Un 401 était rejoué 3 fois : `HTTPError` descend de `RequestException`.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import requests

from src.data_sources import http
from src.pipeline import market
from src.reporting import render

RACINE = pathlib.Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
# 1 — LA CHARGE DES ACTUALITÉS EST LUE À LA BONNE RACINE
# ══════════════════════════════════════════════════════════════════════════

CHARGE_REELLE = {                       # forme exacte de crypto_rss.get_news
    "available": True,
    "news": [{"title": "ETF spot : collecte nette record",
              "source": "CoinDesk", "published_label": "il y a 2 h"},
             {"title": "La Fed maintient ses taux",
              "source": "The Block", "published_label": "il y a 5 h"}],
    "sources_ok": ["CoinDesk", "The Block"],
    "sources_down": [],
    "count": 2,
}


def test_main_lit_les_actualites_a_la_cle_news():
    """Le défaut exact du run #1 : la racine du dictionnaire au lieu de `news`."""
    src = (RACINE / "src" / "main.py").read_text(encoding="utf-8")
    bloc = src[src.index("news_res = sources.get"):][:400]
    assert '.get("news")' in bloc, (
        "main.py doit lire la clé « news », pas la racine de la charge")
    assert "(news_res.value or [])" not in bloc, (
        "la charge de get_news est un dict, jamais une liste")


def test_news_view_sur_la_charge_reelle():
    vue = render.news_view(CHARGE_REELLE["news"])
    assert len(vue) == 2
    assert vue[0]["title"].startswith("ETF spot")
    assert vue[0]["source"] == "CoinDesk"
    assert vue[0]["when"] == "il y a 2 h"


def test_news_view_ne_tue_jamais_le_run(caplog):
    """Régression du crash : un dict ne doit plus lever, et doit se plaindre."""
    vue = render.news_view(CHARGE_REELLE)          # le dict ENTIER, comme v31.0
    assert len(vue) == 2, "la clé « news » doit être récupérée"
    assert any("dictionnaire" in r.message for r in caplog.records), (
        "l'anomalie de câblage doit être TRACÉE, pas absorbée en silence")


@pytest.mark.parametrize("charge", [None, [], {}, "texte", 42, {"news": None}])
def test_news_view_absorbe_toute_forme_sans_lever(charge):
    assert render.news_view(charge) == []


def test_la_charge_reelle_de_get_news_a_bien_la_cle_news():
    """Confronte le CONSOMMATEUR au PRODUCTEUR, pas à une fixture inventée."""
    import inspect

    from src.data_sources import crypto_rss
    src = inspect.getsource(crypto_rss.get_news)
    assert '"news": deduped' in src, (
        "get_news doit publier ses items sous la clé « news »")


# ══════════════════════════════════════════════════════════════════════════
# 2 — LA PROFONDEUR DEMANDÉE RESTE DANS CE QUE LE FOURNISSEUR SERT
# ══════════════════════════════════════════════════════════════════════════

def test_la_demande_ne_depasse_jamais_le_plafond_mesure():
    """365 : mesuré sans clé — 365 -> 200/366 pts, 366 -> 401 (code 10012)."""
    assert market.DAILY_SERIES_DAYS <= 365
    assert market._PROVIDER_MAX_DAYS == 365


def test_la_profondeur_couvre_quand_meme_position():
    """365 demandés -> 366 points servis, et POSITION exige 365."""
    from src.core.horizon import Horizon, SPECS
    assert SPECS[Horizon.POSITION].enabled
    assert market.DAILY_SERIES_DAYS >= SPECS[Horizon.POSITION].depth_min


def test_un_appelant_ne_peut_pas_forcer_au_dela_du_plafond(monkeypatch):
    """Même en passant days=400 à la main, l'appel sort plafonné."""
    vus: dict[str, int] = {}

    def faux(symbol, days=30, interval=None):
        vus["days"] = days
        return {"closes": [100.0] * days, "volumes": []}

    monkeypatch.setattr(market.coingecko, "get_price_volume_series", faux)
    market.daily_closes("BTC", days=400)
    assert vus["days"] == 365, "400 aurait été refusé par le fournisseur (401)"


def test_aucun_appel_du_projet_ne_demande_plus_de_365_jours():
    """Balayage du code : plus aucune constante de profondeur > 365."""
    coupables = []
    for f in (RACINE / "src").rglob("*.py"):
        for i, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ligne.strip().startswith("#"):
                continue
            for m in re.finditer(r"days\s*[=:]\s*(\d{3,})", ligne):
                if int(m.group(1)) > 365:
                    coupables.append(f"{f.name}:{i} {ligne.strip()[:60]}")
    assert not coupables, coupables


# ══════════════════════════════════════════════════════════════════════════
# 3 — LE CHIEN DE GARDE PEUT PARLER
# ══════════════════════════════════════════════════════════════════════════

def test_le_job_watchdog_installe_de_quoi_alerter():
    """Il détectait le silence et ne pouvait pas le transmettre."""
    wf = (RACINE / ".github" / "workflows" / "watchdog.yml").read_text(
        encoding="utf-8")
    ligne = next(l for l in wf.splitlines() if "pip install" in l)
    for paquet in ("pyyaml", "requests", "tenacity"):
        assert paquet in ligne, f"{paquet} manque au job watchdog"


def test_le_chemin_d_alerte_importe_bien_tenacity():
    """Preuve que la dépendance est RÉELLE et non supposée."""
    import inspect

    from src.data_sources import http as mod
    assert "tenacity" in inspect.getsource(mod)
    from src.telegram_bot import telegram_api
    assert "http" in inspect.getsource(telegram_api)


# ══════════════════════════════════════════════════════════════════════════
# 4 — UN ÉCHEC DÉFINITIF N'EST PAS REJOUÉ
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_les_4xx_definitifs_ne_sont_pas_rejoues(code):
    exc = requests.HTTPError(f"{code} Client Error")
    assert http._est_retentable(exc) is False


def test_le_transitoire_reste_rejoue():
    assert http._est_retentable(http.TransientHTTPError("429")) is True


@pytest.mark.parametrize("exc", [
    requests.ConnectionError("dns"),
    requests.Timeout("trop long"),
    requests.TooManyRedirects("boucle"),
])
def test_les_pannes_reseau_restent_rejouees(exc):
    assert http._est_retentable(exc) is True


def test_un_401_ne_consomme_qu_une_tentative(monkeypatch):
    """Mesuré en prod : 29 actifs × 3 tentatives = 310 s pour zéro donnée."""
    appels = {"n": 0}

    class FausseReponse:
        status_code = 401

        def raise_for_status(self):
            raise requests.HTTPError("401 Client Error")

    def faux_request(*a, **k):
        appels["n"] += 1
        return FausseReponse()

    monkeypatch.setattr(http.requests, "request", faux_request)
    monkeypatch.setattr(http, "_throttle", lambda url: None)
    with pytest.raises(requests.HTTPError):
        http._request("GET", "https://exemple.test/x")
    assert appels["n"] == 1, f"{appels['n']} tentatives sur un 401 définitif"
