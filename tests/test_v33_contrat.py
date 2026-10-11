"""Contrats PRODUCTEUR → CONSOMMATEUR du moteur v33 (audit zero-trust 01/10).

Trois clés lues par l'adaptateur n'étaient produites par personne
(``mc_tvl``, ``commits_90d``, ``age_days``) : les tests fabriquaient ces clés,
le chemin était vert en test et mort en production. Ici, chaque test fait
passer la sortie RÉELLE d'un producteur dans le consommateur — jamais un
dictionnaire écrit à la main sous la forme que le consommateur espère.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.analytics import opportunity as O
from src.analytics import opportunity_adapter as A
from src.analytics.exit_radar import compute_exit_signals
from src.analytics.valuation import compute_valuation


def _jours(n: int, depart: float = 100.0, pas=None) -> dict[str, float]:
    """``n`` clôtures quotidiennes DATÉES, toutes antérieures à aujourd'hui."""
    fin = datetime.now(timezone.utc).date() - timedelta(days=1)
    out, px = {}, depart
    for i in range(n):
        d = fin - timedelta(days=n - 1 - i)
        px *= 1.0 + (pas(i) if pas else (0.02 if i % 2 else -0.018))
        out[d.isoformat()] = round(px, 6)
    return out


# ── valorisation → adaptateur ─────────────────────────────────────────────

def test_mc_tvl_est_lu_sous_la_cle_que_le_producteur_ecrit():
    val = compute_valuation({"market_cap": 1.2e9},
                            tvl={"available": True, "tvl_usd": 2e8})
    assert "mc_tvl_ratio" in val["metrics"] and "mc_tvl" not in val["metrics"]
    entry = {"asset": "PROTO", "valuation": val, "value_usd": 50.0}
    c = A.build_candidate(entry, ptf_value_usd=1000.0)
    assert c["mc_tvl"] == pytest.approx(6.0)


def test_une_cle_fabriquee_n_est_jamais_lue():
    entry = {"asset": "PROTO", "valuation": {"metrics": {"mc_tvl": 3.0}}}
    assert A.build_candidate(entry)["mc_tvl"] is None


def test_la_dilution_restante_vient_du_producteur():
    val = compute_valuation({"market_cap": 1e9, "circulating_supply": 50.0,
                             "max_supply": 100.0})
    c = A.build_candidate({"asset": "X", "valuation": val})
    assert c["dilution_remaining_pct"] == pytest.approx(100.0)


# ── activité GitHub → viabilité ───────────────────────────────────────────

@pytest.mark.parametrize("dev,attendu", [
    ({"available": True, "commits_30d": 40, "last_commit_days_ago": 1}, True),
    ({"available": True, "commits_30d": 0, "last_commit_days_ago": 45}, True),
    ({"available": True, "commits_30d": 0, "last_commit_days_ago": 200}, False),
    ({"available": True, "commits_30d": 5, "last_commit_days_ago": None}, True),
    ({"available": True, "commits_30d": 0, "last_commit_days_ago": None}, None),
    ({"available": False, "reason": "pas de repo public connu"}, None),
])
def test_l_activite_de_dev_suit_la_forme_reelle_de_github_dev(dev, attendu):
    """Forme exacte de ``github_dev.get_dev_activity`` : ``commits_30d`` et
    ``last_commit_days_ago`` — pas de ``commits_90d``."""
    assert A.build_candidate({"asset": "X", "dev_activity": dev})["dev_active"] is attendu


# ── Coin Metrics → MVRV ───────────────────────────────────────────────────

def test_le_drapeau_perime_du_producteur_atteint_le_moteur():
    entry = {"asset": "BTC", "onchain_advanced": {"mvrv": 0.9, "stale": True,
                                                  "as_of": "2026-09-01"}}
    c = A.build_candidate(entry)
    assert c["mvrv"] == 0.9 and c["mvrv_stale"] is True


def test_les_statistiques_historiques_du_mvrv():
    from src.data_sources import coinmetrics as C
    debut = datetime(2015, 1, 1)
    rows = [((debut + timedelta(days=i)).date().isoformat(), 0.5 + (i % 300) / 100.0)
            for i in range(4 * 365 + 10)]
    st = C.mvrv_history_stats(rows)
    vals = sorted(v for _, v in rows)
    assert st["min"] == pytest.approx(vals[0]) and st["max"] == pytest.approx(vals[-1])
    assert st["q25"] < st["median"] < st["q75"]
    assert st["n"] == len(rows)
    assert C.mvrv_history_stats(rows[:300]) is None      # tronqué : pas d'ancre


def test_l_historique_mvrv_suit_la_pagination_et_publie_les_clotures(monkeypatch):
    from src.data_sources import coinmetrics as C
    from src.utils.cache import CACHE
    CACHE._store.pop("coinmetrics:mvrv_history", None)
    debut = datetime(2015, 1, 1)

    def ligne(i, asset):
        return {"asset": asset, "time": (debut + timedelta(days=i)).isoformat() + "Z",
                "CapMVRVCur": str(1.0 + (i % 200) / 100), "PriceUSD": str(100 + i)}

    pages = {}

    def faux_get_json(url, params=None, **kw):
        asset = (params or {}).get("assets") or url.split("=")[-1]
        if params:          # page 1
            return {"data": [ligne(i, asset) for i in range(1000)],
                    "next_page_url": f"https://suite?asset={asset}"}
        pages[asset] = True
        return {"data": [ligne(i, asset) for i in range(1000, 2000)]}

    monkeypatch.setattr(C, "get_json", faux_get_json)
    h = C.get_mvrv_history()
    CACHE._store.pop("coinmetrics:mvrv_history", None)
    assert h["available"] and set(h["assets"]) == {"BTC", "ETH"}
    assert h["assets"]["BTC"]["n"] == 2000 and pages.get("btc")
    assert len(h["assets"]["BTC"]["closes"]) == 120


def test_le_repli_coin_metrics_complete_les_series_btc_eth():
    import src.main as M
    hist = {"BTC": {"closes": _jours(120)}, "ETH": {"closes": _jours(30)}}
    closes = {"ETH": _jours(90), "SOL": _jours(90)}
    M._completer_series_v33(closes, hist)
    assert len(closes["BTC"]) == 120          # absente → complétée
    assert len(closes["ETH"]) == 90           # série CoinGecko suffisante gardée
    assert "SOL" in closes


# ── DeFiLlama → pairs de catégorie ────────────────────────────────────────

def test_les_pairs_sont_la_categorie_entiere_candidat_exclu(monkeypatch):
    from src.data_sources import defillama as D
    protos = [
        {"name": "Moi", "category": "Bridge", "mcap": 1e8, "tvl": 1e8},
        {"name": "P1", "category": "Bridge", "mcap": 2e8, "tvl": 1e8},
        {"name": "P2", "category": "Bridge", "mcap": 3e8, "tvl": 1e8},
        {"name": "P3", "category": "Bridge", "mcap": None, "tvl": 1e8},
        {"name": "P4", "category": "Bridge", "mcap": 1e8, "tvl": 0},
        {"name": "L1", "category": "Lending", "mcap": 9e8, "tvl": 1e8},
    ]
    monkeypatch.setattr(D, "_protocols_raw", lambda: protos)
    p = D.get_category_peers("Bridge", exclude_name="Moi")
    assert sorted(p["ratios"]) == [2.0, 3.0] and p["n"] == 2
    assert D.get_category_peers(None)["available"] is False


# ── radar de sortie → allègement du moteur ────────────────────────────────

def test_le_radar_reel_atteint_le_moteur_sous_sa_vraie_forme():
    radar = compute_exit_signals([
        {"symbol": "QNT", "pnl_pct": 321.0, "weight_pct": 4.9, "change_7d": 3.0},
        {"symbol": "HBAR", "pnl_pct": 98.0, "weight_pct": 0.9, "change_7d": 1.0},
        {"symbol": "BTC", "pnl_pct": 40.0, "weight_pct": 38.0, "change_7d": 2.0},
    ])
    par_sym = {s["symbol"]: s for s in radar["signals"]}
    cands = [A.build_candidate({"asset": s, "value_usd": 10.0}) for s in ("QNT", "HBAR", "BTC")]
    res = O.decide_universe(cands, exit_signals=par_sym)
    assert sorted(r["asset"] for r in res["reduce_firm"]) == ["HBAR", "QNT"]
    assert all(r["trigger"] == "radar_sortie" for r in res["reduce_firm"])


# ── séries datées : volatilité et bêta ────────────────────────────────────

def test_la_journee_en_cours_est_exclue():
    today = datetime.now(timezone.utc).date().isoformat()
    s = _jours(30)
    s[today] = 999999.0                       # point « maintenant » CoinGecko
    assert today not in A.complete_days(s)
    assert A.daily_volatility_pct(s) == A.daily_volatility_pct(_jours(30))


def test_un_trou_de_donnees_ne_fabrique_pas_un_rendement_sur_deux_jours():
    s = _jours(40)
    jours = sorted(s)
    del s[jours[20]]
    r = A.dated_log_returns(A.complete_days(s))
    assert jours[21] not in r and len(r) == 37


def test_le_beta_ne_depend_pas_d_un_decalage_de_sources():
    """30/09 : β(BTC) = −0,05 et β(QNT) = −0,66 — séries alignées « par la
    fin », dont certaines portaient le point live et d'autres non."""
    import random
    rnd = random.Random(7)
    chocs = [rnd.gauss(0, 0.02) for _ in range(100)]
    marche = _jours(100, pas=lambda i: chocs[i])
    alt = _jours(100, pas=lambda i: 1.5 * chocs[i] + rnd.gauss(0, 0.005))
    avec_live = dict(alt)
    avec_live[datetime.now(timezone.utc).date().isoformat()] = 1.0
    rets = {"MKT": A.dated_log_returns(A.complete_days(marche))}
    ref = A.portfolio_return_series(rets, {"MKT": 1.0})
    b1 = A.beta_vs_portfolio(alt, ref)
    b2 = A.beta_vs_portfolio(avec_live, ref)
    assert b1 == pytest.approx(b2) and 1.3 < b1 < 1.7


def test_le_beta_est_mesure_ex_soi():
    import random
    rnd = random.Random(3)
    ch = [rnd.gauss(0, 0.02) for _ in range(100)]
    lourd = _jours(100, pas=lambda i: ch[i])
    petit = _jours(100, pas=lambda i: 0.5 * ch[i] + rnd.gauss(0, 0.01))
    entries = [{"asset": "LOURD", "value_usd": 830.0}, {"asset": "PETIT", "value_usd": 170.0}]
    cands = A.build_candidates(entries, ptf_value_usd=1000.0,
                               closes_by_asset={"LOURD": lourd, "PETIT": petit})
    lourd_c = next(c for c in cands if c["asset"] == "LOURD")
    # référence = PETIT seul : β(LOURD vs PETIT) ≈ cov/var ≈ 0,5/0,25+ … ≠ 1
    assert lourd_c["beta_portfolio"] is not None
    assert abs(lourd_c["beta_portfolio"] - 1.0) > 0.2


def test_un_historique_trop_court_ne_tronque_pas_la_reference_de_tous():
    longs = {f"A{i}": A.dated_log_returns(A.complete_days(_jours(95))) for i in range(5)}
    court = {"NEW": A.dated_log_returns(A.complete_days(_jours(15)))}
    ref = A.portfolio_return_series({**longs, **court},
                                    {**{k: 1.0 for k in longs}, "NEW": 1.0})
    assert len(ref) >= 90
