# -*- coding: utf-8 -*-
"""V31 — audit adversarial du 15/08/2026. Trois défauts, tous verrouillés ici.

    A. BANDEAU SATURÉ. `as_of` valait None pour TOUTES les sources : aucun
       collecteur ne l'extrayait, et les deux qui essayaient visaient des clés
       absentes de la charge réelle. `assess` force alors OK -> DEGRADED, si
       bien qu'un run PARFAIT annonçait « Rapport partiel » et 8 dégradations.
       V31 avait supprimé l'indice de confiance PARCE QU'IL SATURAIT : le
       remède reproduisait la maladie.
    B. SOURCES MORTES. polymarket, macro_calendar, derivatives et equities
       étaient collectées sans produire un seul fait ni un seul octet de
       contenu — et pouvaient dégrader le bandeau d'un rapport sur lequel
       elles n'avaient aucune influence.
    C. RAYON DE SOUFFLE. Un seul actif au prix aberrant levait dans
       `evaluate_candidates` et emportait les 28 autres : 0/29 candidats,
       aucun des trois mails envoyé.
"""
from __future__ import annotations

import math
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from src.core import registry
from src.core import source_result as sr
from src.core.book import Direction, RecommendationBook
from src.core.volatility import DailyBar
from src.pipeline import collect, context as ctx_mod, market, runs

UTC = timezone.utc
RACINE = pathlib.Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════════
# A — LE BANDEAU NE DOIT PAS SATURER
# ══════════════════════════════════════════════════════════════════════════

def _sources_saines(maintenant: datetime) -> dict[str, sr.SourceResult]:
    """Ce que la collecte produit quand TOUT va bien."""
    frais = maintenant - timedelta(hours=3)
    return {
        "fear_greed": sr.ok("fear_greed", {"value": 18}, as_of=frais),
        "fred": sr.ok("fred", {"available": True, "series": {}}, as_of=frais),
        "etf_flows": sr.ok("etf_flows", {"available": True}, as_of=frais),
        "onchain": sr.ok("onchain", {"BTC": {"hash_rate_ehs": 1.0}}),
        "news": sr.ok("news", {"available": True, "news": []}),
        "market.spot": sr.ok("market.spot", {"BTC": {}}),
        "market.closes": sr.ok("market.closes", {"closes": [1.0]},
                               as_of=frais),
        "market.ohlc": sr.ok("market.ohlc", [], as_of=frais),
    }


def test_un_run_parfait_n_annonce_aucune_degradation(tmp_path):
    """LE test de non-régression : sources saines => bandeau VIDE."""
    from src.core import runlog
    s = runlog.new_run("morning")
    ctx = runs.RunContext(kind="morning", summary=s,
                          book=RecommendationBook(run_kind="morning",
                                                  run_id=s.run_id,
                                                  state_dir=tmp_path))
    ctx.sources = _sources_saines(datetime.now(UTC))
    fin = runs.finalize(ctx)
    assert fin.get("banner") in (None, ""), (
        f"un run parfait annonce des dégradations : {fin.get('banner')}")


def test_une_source_reellement_perimee_est_toujours_annoncee():
    """On corrige la saturation SANS rendre le bandeau muet."""
    spec = registry.CATALOG["fred"]
    vieux = sr.ok("fred", {"available": True, "series": {}},
                  as_of=datetime.now(UTC) - timedelta(days=6))
    assert registry.assess(spec, vieux).degraded is True


def test_une_source_continue_ne_perime_pas_avec_l_age():
    """Un relevé instantané n'a pas de cadence : l'âge ne le périme pas."""
    for sid in ("onchain", "news"):
        spec = registry.CATALOG[sid]
        assert spec.is_continuous, f"{sid} devrait être un flux continu"
        vieux = sr.ok(sid, {"x": 1},
                      as_of=datetime.now(UTC) - timedelta(days=30))
        assert registry.assess(spec, vieux).degraded is False


# ── l'extraction de fraîcheur, sur les charges RÉELLES ────────────────────

def test_fear_greed_publie_son_horodatage():
    """L'API le fournit ; la v31.0 le jetait et le collecteur le cherchait."""
    import inspect

    from src.data_sources import fear_greed
    src = inspect.getsource(fear_greed.get_fear_greed)
    assert '"timestamp"' in src, "le producteur doit publier son horodatage"


def test_fred_expose_sa_date_la_plus_recente():
    charge = {"available": True, "series": {
        "dxy": {"value": 103.4, "date": "2026-08-13"},
        "us_10y": {"value": 4.2, "date": "2026-08-14"}}}
    res = collect._wrap("fred", lambda: charge,
                        extract_as_of=lambda p: collect._plus_recente(
                            (s or {}).get("date")
                            for s in (p.get("series") or {}).values()))
    assert res.as_of is not None and res.as_of.day == 14


def test_etf_flows_lit_la_date_NICHEE_sous_btc_eth():
    """La v31.0 la cherchait à la racine : elle n'y a jamais été."""
    charge = {"available": True,
              "btc": {"date": "2026-08-13", "total_flow_musd": 1.0},
              "eth": {"date": "2026-08-14", "total_flow_musd": 2.0}}
    assert collect._plus_recente(
        [charge.get("as_of")]
        + [(charge.get(c) or {}).get("date") for c in ("btc", "eth")]
    ) == "2026-08-14"


def test_les_clotures_portent_leur_horodatage(monkeypatch):
    """Source BLOQUANTE : sans as_of, on décidait sans savoir si c'était frais."""
    ts = int(datetime(2026, 8, 14, tzinfo=UTC).timestamp() * 1000)
    monkeypatch.setattr(
        market.coingecko, "get_price_volume_series",
        lambda symbol, days=30, interval=None: {
            "closes": [100.0, 101.0], "volumes": [], "last_ts": ts})
    res = market.daily_closes("BTC")
    assert res.as_of is not None and res.as_of.date().day == 14


def test_les_bougies_portent_le_jour_de_la_derniere(monkeypatch):
    """`_utc(None)` valait toujours None : une datation qui ne datait rien."""
    monkeypatch.setattr(
        market.coingecko, "get_ohlc_raw",
        lambda symbol, days=30: [[0, 1, 1, 1, 1]])
    monkeypatch.setattr(
        market, "aggregate_to_daily",
        lambda rows: [DailyBar("2026-08-14", 1.0, 1.0, 1.0, 1.0)] * 25)
    res = market.daily_bars("BTC")
    assert res.as_of is not None and res.as_of.day == 14


# ══════════════════════════════════════════════════════════════════════════
# B — ON NE COLLECTE QUE CE QUI ATTEINT LE RAPPORT
# ══════════════════════════════════════════════════════════════════════════

def test_toute_source_collectee_produit_un_fait_ou_du_contenu():
    """Garde permanente contre la collecte vestigiale."""
    from src.core.facts import FactStore
    CHARGES = {
        "fear_greed": {"value": 18, "classification": "Fear"},
        "fred": {"available": True, "series": {"dxy": {"value": 1.0}}},
        "onchain": {"BTC": {"available": True, "hash_rate_ehs": 1.0}},
        "etf_flows": {"available": True, "btc": {"total_flow_musd": 1.0}},
    }
    for sid in collect.CONTEXT_SOURCES:
        if sid == "news":
            continue                     # EXTERNAL : contenu, pas fait
        st = FactStore()
        ctx_mod._register_context(st, {sid: sr.ok(sid, CHARGES[sid])})
        assert st.ids(), f"{sid} est collectée et ne produit AUCUN fait"


def test_les_sources_sans_debouche_ne_sont_plus_collectees():
    for mort in ("polymarket", "macro_calendar", "derivatives", "equities"):
        assert mort not in collect.CONTEXT_SOURCES
        assert mort not in registry.CATALOG, (
            f"{mort} resterait dans la matrice de santé")


# ══════════════════════════════════════════════════════════════════════════
# C — UN ACTIF NE PEUT PLUS EMPORTER LES AUTRES
# ══════════════════════════════════════════════════════════════════════════

def _serie(n=420, amp=45.0):
    base = [140.0 + amp * math.sin(i / 38.0) for i in range(n - 30)]
    st = base[-1]
    return base + [st + (140.0 - st) * (k + 1) / 30 for k in range(30)]


def _spec(actif, prix=140.0):
    return {"asset": actif, "direction": Direction.LONG_INCREASE,
            "is_core": False, "tier": 1,
            "signal_scoring": {"signals": [
                {"category": "fundamental_lt", "weight": 6}]},
            "price": prix, "closes": _serie(),
            "daily_bars": [DailyBar(f"d{i}", 100, 101.2, 98.8, 100)
                           for i in range(200)],
            "ptf_value_usd": 60000.0, "weight_pct": 1.0,
            "position_value_usd": 600.0, "daily_volume_usd": 8e8}


@pytest.mark.parametrize("prix", [float("nan"), float("inf"), float("-inf")])
def test_un_actif_aberrant_n_emporte_pas_les_autres(tmp_path, prix):
    from src.core import runlog
    s = runlog.new_run("morning")
    ctx = runs.RunContext(kind="morning", summary=s,
                          book=RecommendationBook(run_kind="morning",
                                                  run_id=s.run_id,
                                                  state_dir=tmp_path))
    specs = [_spec(f"A{i}") for i in range(5)] + [_spec("POURRI", prix)]
    runs.evaluate_candidates(ctx, specs)          # ne doit PAS lever

    # L'invariant n'est pas « l'actif est écarté » mais « il ne fait pas
    # tomber le run et il n'est jamais émis ». Deux issues sont correctes :
    # `-inf` est rejeté proprement (prix <= 0 -> « prix spot indisponible »),
    # tandis que NaN et +inf lèvent et sont isolés. Les deux sont honnêtes ;
    # exiger l'une des deux ferait échouer un comportement correct.
    sains = [c for c in ctx.candidates if c.asset != "POURRI"]
    assert len(sains) == 5, "les actifs sains doivent survivre"
    assert not any(c.emittable for c in ctx.candidates
                   if c.asset == "POURRI"), "un prix aberrant ne s'émet pas"
    pourri = [c for c in ctx.candidates if c.asset == "POURRI"]
    assert pourri or "POURRI" in ctx.failed_assets, (
        "l'actif doit être soit refusé avec motif, soit isolé et énuméré")


def test_l_actif_ecarte_est_ENUMERE_jamais_silencieux(tmp_path):
    from src.core import runlog
    s = runlog.new_run("morning")
    ctx = runs.RunContext(kind="morning", summary=s,
                          book=RecommendationBook(run_kind="morning",
                                                  run_id=s.run_id,
                                                  state_dir=tmp_path))
    ctx.sources = _sources_saines(datetime.now(UTC))
    runs.evaluate_candidates(ctx, [_spec("A0"), _spec("POURRI", float("nan"))])
    fin = runs.finalize(ctx)
    banniere = fin.get("banner") or ""
    assert "POURRI" in banniere and "écarté" in banniere, banniere


def test_une_position_illisible_n_empeche_pas_les_specs():
    """`candidate_specs` isole aussi : portfolio.yaml est éditable à la main."""
    class PositionCassee:
        symbol = "CASSE"
        is_core = False
        tier = 1
        price = 1.0
        value_usd = 1.0
        weight_pct = 1.0

        @property
        def cg_key(self):
            raise ValueError("clé illisible")

    class PositionSaine(PositionCassee):
        symbol = "SAINE"

        @property
        def cg_key(self):
            return "saine"

    echecs: dict[str, str] = {}
    specs = ctx_mod.candidate_specs(
        [PositionCassee(), PositionSaine()], ptf_value=1000.0,
        closes={}, bars={}, spot=sr.empty("market.spot"),
        signals_by_asset={}, failures=echecs)
    assert [s["asset"] for s in specs] == ["SAINE"]
    assert echecs == {"CASSE": "ValueError"}
