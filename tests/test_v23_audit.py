"""Tests v23 — corrections post-audit des 3 mails. Verrouille les correctifs
déterministes : version, format nombres unifié, arrondi des niveaux, rendement 7j
standard (cohérence matin/hebdo), affichage portfolio_risk, label vs ETH, P&L nuit.

Hermétiques : aucun réseau (dicts en dur / monkeypatch).
"""

from __future__ import annotations

import inspect


# --------------------------------------------------------------------------- #
# C1 — version (bumpée v24)
# --------------------------------------------------------------------------- #
def test_app_version_v23():
    from src.reporting.email_html import APP_VERSION
    # Nommage final : le livrable est étiqueté v26 (décision Omar, 2026-07-05).
    assert APP_VERSION == "v28"


# --------------------------------------------------------------------------- #
# C2 — format nombres UNIFIÉ (anglo) entre fmt_money et fmt_price
# --------------------------------------------------------------------------- #
def test_money_and_price_same_convention():
    from src.reporting.email_html import _fmt_money, _fmt_price
    # Même convention : $ préfixe, virgule milliers, point décimale.
    assert _fmt_money(1570).startswith("$") and _fmt_price(1570).startswith("$")
    assert "," in _fmt_money(60314) and "," in _fmt_price(60314)
    assert _fmt_money(1.55) == "$1.55"
    # Entrée FR encore tolérée (rétro-compat), sortie anglo.
    assert _fmt_money("1.570,00 $") == "$1,570.00"


# --------------------------------------------------------------------------- #
# C3 — arrondi des niveaux techniques sur-précis
# --------------------------------------------------------------------------- #
def test_round_num_display():
    from src.main import _round_num_display
    assert _round_num_display(1545.096667) == 1545
    assert _round_num_display(0.140035) == 0.14
    assert _round_num_display(1.583333) == 1.58
    assert _round_num_display("lower") == "lower"   # non-numérique inchangé
    assert _round_num_display(0) == 0


def test_round_levels_recursive():
    from src.main import _round_levels
    out = _round_levels({"support": 1545.096667, "bands": [0.140035, "x"]})
    assert out["support"] == 1545
    assert out["bands"][0] == 0.14
    assert out["bands"][1] == "x"


# --------------------------------------------------------------------------- #
# W2/W3/W5 — rendement 7j STANDARD (cohérence matin/hebdo, source unique)
# --------------------------------------------------------------------------- #
def test_change_7d_standard_return():
    from src.main import _portfolio_snapshot
    enriched = {
        "BTC": {"value_usd": 50.0, "change_24h": 0.0, "change_7d": -50.0, "ath_distance_pct": -10},
        "ETH": {"value_usd": 50.0, "change_24h": 0.0, "change_7d": 0.0, "ath_distance_pct": -10},
    }
    pf = {"BTC": {"quantity": 1}, "ETH": {"quantity": 1}}
    snap = _portfolio_snapshot(pf, enriched)
    # val_7d_ago = 50/0.5 + 50 = 150 ; actuel 100 → rendement standard = -33.33%
    assert snap["change_7d_pct"] == -33.33
    assert snap["change_7d_usd"] == -50.0


def test_change_7d_none_without_data():
    from src.main import _portfolio_snapshot
    enriched = {"BTC": {"value_usd": 50.0, "change_24h": 1.0}}  # pas de change_7d
    snap = _portfolio_snapshot({"BTC": {"quantity": 1}}, enriched)
    assert snap["change_7d_pct"] is None


# --------------------------------------------------------------------------- #
# M1 — garde-fou cohérence du P&L nuit (présent dans _collect_morning_data)
# --------------------------------------------------------------------------- #
def test_overnight_pnl_coherence_guard_wired():
    from src import main
    src = inspect.getsource(main._collect_morning_data)
    assert "_incoherent" in src
    assert "_pct * _ch24 < 0" in src   # signes opposés nuit vs 24h


# --------------------------------------------------------------------------- #
# C5 — portfolio_risk VISIBLE dans le mail matin
# --------------------------------------------------------------------------- #
def test_portfolio_risk_renders_morning():
    from src.reporting.email_html import render
    payload = {
        "header": {"date": "28/06"},
        "portfolio_risk": {"available": True, "readings": [
            "Concentration : 14 positions mais ~5 paris effectifs (HHI).",
            "Stress-test : un choc BTC de -20% entrainerait ~ -24% sur le PTF.",
            "VaR 95% : -6.1%.",
        ]},
    }
    html = render(payload, "morning")
    assert "STRESS-TEST" in html          # v23 — bloc renommé (plus « RISQUE »)
    assert "Stress-test" in html and "VaR 95%" in html


# --------------------------------------------------------------------------- #
# W6 — label benchmark « PTF vs ETH » (plus « (vs ETH … ) » ambigu)
# --------------------------------------------------------------------------- #
def test_weekly_vs_eth_label():
    from src.reporting.email_html import render
    payload = {
        "header": {"date": "28/06"},
        "portfolio_snapshot": {"ptf_7d_pct": -7.3, "eth_7d_pct": -8.4,
                               "btc_7d_pct": -5.6, "vs_eth_7d_pct": 1.1},
    }
    html = render(payload, "weekly")
    assert "PTF vs ETH" in html


# --------------------------------------------------------------------------- #
# C4 — CoinJournal retiré des flux RSS (502 récurrent)
# --------------------------------------------------------------------------- #
def test_coinjournal_removed():
    from src.data_sources.crypto_rss import CRYPTO_FEEDS
    assert "CoinJournal" not in CRYPTO_FEEDS
    assert len(CRYPTO_FEEDS) >= 6   # les autres flux crypto restent


# --------------------------------------------------------------------------- #
# v23 POST-AUDIT — sources « cassées » : solutions gratuites
# --------------------------------------------------------------------------- #
# Poussieres W / SXT / 1000SATS — ids CoinGecko VERIFIES (fini le fallback
# value_usd fige ; 1000SATS valorise live, fin du biais ~5% du PTF).
def test_dust_coingecko_ids_mapped():
    from src.utils.portfolio_loader import load_config
    ids = load_config("sources")["coingecko_ids"]
    assert ids["W"] == "wormhole"
    assert ids["SXT"] == "space-and-time"
    # id deja libelle par unite « 1000 sats » : aucune correction de facteur.
    assert ids["1000SATS"] == "1000sats-ordinals"


def test_dust_symbols_resolvable_no_keyerror():
    # Tous les tickers du portfolio doivent etre mappables (sinon prix fige).
    from src.utils.portfolio_loader import load_config
    ids = load_config("sources")["coingecko_ids"]
    pf = load_config("portfolio").get("portfolio") or {}
    missing = [s for s in pf if s not in ids]
    assert missing == [], f"tickers sans id CoinGecko : {missing}"


# YouTube — channel IDs epingles & verifies (tue les 404 de resolution + quota).
def test_youtube_channel_ids_pinned():
    from src.utils.portfolio_loader import load_config
    conf = load_config("youtube_channels")
    cids = conf.get("channel_ids") or {}
    names = []
    for grp in (conf.get("youtube_channels") or {}).values():
        names.extend(grp)
    assert len(cids) >= len(names)
    for name in names:
        assert name in cids, f"chaine '{name}' non epinglee"
        assert cids[name].startswith("UC") and len(cids[name]) == 24


# On-chain — degel MVRV via prix live / realized price (seul fix ETH gratuit).
def test_apply_live_price_mvrv_refreshes_stale():
    from src.data_sources.coinmetrics import apply_live_price_mvrv
    result = {"available": True, "source": "coinmetrics-github", "assets": {
        "ETH": {"mvrv": 1.5, "realized_price": 2000.0, "stale": True,
                "as_of": "2026-05-24"},
        "BTC": {"mvrv": 1.1, "realized_price": 50000.0, "stale": False},
    }}
    out = apply_live_price_mvrv(result, {"ETH": 2400.0, "BTC": 70000.0})
    eth = out["assets"]["ETH"]
    assert eth["mvrv"] == 1.2                 # 2400 / 2000
    assert eth["mvrv_live_estimate"] is True
    assert eth["realized_price_ratio"] == 1.2
    # Entree BTC deja fraiche (stale=False) : laissee STRICTEMENT intacte.
    assert out["assets"]["BTC"]["mvrv"] == 1.1
    assert "mvrv_live_estimate" not in out["assets"]["BTC"]


def test_apply_live_price_mvrv_graceful():
    from src.data_sources.coinmetrics import apply_live_price_mvrv
    # Pas de realized_price -> inchange (jamais de MVRV invente).
    r = {"assets": {"ETH": {"mvrv": 1.5, "stale": True}}}
    assert "mvrv_live_estimate" not in apply_live_price_mvrv(
        r, {"ETH": 2400.0})["assets"]["ETH"]
    # Pas de prix live -> inchange.
    r2 = {"assets": {"ETH": {"mvrv": 1.5, "realized_price": 2000.0, "stale": True}}}
    assert apply_live_price_mvrv(r2, {})["assets"]["ETH"]["mvrv"] == 1.5


def test_portfolio_evolution_png():
    """v23 — l'évolution PTF est une vraie courbe PNG (remplace les barres)."""
    from src.reporting import charts
    pts = [{"label": f"S{i}", "value": v} for i, v in enumerate(
        [100, 102, 98, 105, 110, 108, 112, 115, 111, 120, 118])]
    png = charts.portfolio_evolution_png(pts)
    assert isinstance(png, (bytes, bytearray)) and png[:4] == b"\x89PNG"
    # avec série BTC alignée (comparaison base 100) → toujours un PNG.
    btc = [60000 + i * 120 for i in range(len(pts))]
    assert charts.portfolio_evolution_png(pts, btc_points=btc)[:4] == b"\x89PNG"
    # < 3 points → None (dégradation gracieuse, le template retombe sur les barres).
    assert charts.portfolio_evolution_png([{"label": "a", "value": 1}]) is None


def test_compute_portfolio_health():
    """v23 — note de SANTÉ /10 (plus haut = plus sain) : 3 axes, driver, improve."""
    from src.main import _compute_portfolio_health
    snap = {"vs_btc_7d_pct": -4.0, "drawdown_ath_pct": -70.0}
    sectors = {"sectors": [{"sector": "L1", "ptf_pct": 70.0},
                           {"sector": "AI", "ptf_pct": 18.0}]}
    h = _compute_portfolio_health(snap, sectors)
    assert 0 <= h["score"] <= 10
    assert [a["label"] for a in h["axes"]] == [
        "Diversification", "Momentum vs BTC", "Solidité (vs ATH)"]
    # Concentration 70% → Diversification très basse (= levier d'amélioration).
    div = next(a for a in h["axes"] if a["label"] == "Diversification")
    assert div["score"] < 3
    assert h["driver"] and h["improve"]
    assert h["level"] in ("fragile", "à risque")          # santé dégradée


def test_compute_portfolio_health_graceful():
    from src.main import _compute_portfolio_health
    assert _compute_portfolio_health({}, {}) == {}        # rien d'exploitable
    h = _compute_portfolio_health({"vs_btc_7d_pct": 4.0}, {})
    assert len(h["axes"]) == 1 and h["axes"][0]["label"] == "Momentum vs BTC"


def test_dedup_theses_by_asset():
    """Anti-doublon : un actif émis 2x (ré-émission) n'apparaît qu'UNE fois,
    on garde la 1re occurrence (liste supposée triée meilleure en tête)."""
    from src.main import _dedup_theses_by_asset
    theses = [
        {"asset": "ETH", "confidence": 85},
        {"asset": "RENDER", "confidence": 75, "_first": True},
        {"asset": "RENDER", "confidence": 75, "_first": False},  # doublon
        {"asset": "LINK", "confidence": 75},
    ]
    out = _dedup_theses_by_asset(theses)
    assert [t["asset"] for t in out] == ["ETH", "RENDER", "LINK"]
    assert out[1]["_first"] is True            # 1re occurrence conservée
    # Robustesse : non-dict toléré, casse insensible (RENDER == render).
    mixed = [{"asset": "BTC"}, "x", {"asset": "btc"}]
    assert _dedup_theses_by_asset(mixed) == [{"asset": "BTC"}, "x"]


def test_onchain_line_labels_live_estimate():
    from src.analytics.digests import onchain_line
    cm = {"available": True, "assets": {"ETH": {
        "mvrv": 1.2, "mvrv_zone": "neutre", "realized_price_ratio": 1.2,
        "stale": True, "as_of": "2026-05-24", "mvrv_live_estimate": True}}}
    line = onchain_line(cm)
    assert "~1.2" in line                       # signal visuel d'estimation
    assert "estimé" in line and "24/05" in line
    assert "miroir, pas temps réel" not in line  # remplace l'ancienne note fige


def test_sector_exposure_six_cases_multi_horizon():
    """v23.x — 6 cases (5 secteurs + Autres), perfs 24h/7j/30j propagées,
    agrégat pondéré par la valeur + holdings fusionnés."""
    from src.main import _compute_sector_exposure
    rotation = {"sectors": {
        f"S{i}": {
            "members": [f"M{i}"],
            "avg_change_24h": float(i),
            "avg_change_7d": float(i) * 2,
            "avg_change_30d": float(i) * 3,
        } for i in range(8)
    }}
    enriched = {f"M{i}": {"value_usd": (8 - i) * 100.0} for i in range(8)}
    out = _compute_sector_exposure(enriched, rotation)["sectors"]
    assert len(out) == 6                                   # 5 individuels + agrégat
    top = out[0]                                           # S0 = le plus gros
    assert top["market_change_7d"] is not None and top["market_change_30d"] is not None
    agg = out[-1]
    assert agg["is_aggregate"] and agg["sector"].startswith("Autres secteurs")
    # tail = S5 (val 300, c30=15) + S6 (val 200, c30=18) + S7 (val 100, c30=21)
    # 30j pondéré = (300×15 + 200×18 + 100×21)/600 = 17.0
    assert agg["market_change_30d"] == 17.0
    assert agg["holdings"] == ["M5", "M6", "M7"]           # holdings fusionnés/triés


def test_heatmap_change_key_7d_vs_24h():
    """v23.x — la heatmap accepte change_key : 7j (weekly) ou 24h (morning, défaut).
    Le tri ET le slot perf suivent la fenêtre choisie ; le défaut reste le 24h."""
    from src.main import _portfolio_heatmap
    enriched = {
        "A": {"value_usd": 100, "change_24h": 0.1, "change_7d": 25.0},
        "B": {"value_usd": 100, "change_24h": 9.0, "change_7d": 1.0},
    }
    hm7 = _portfolio_heatmap(enriched, change_key="change_7d")
    assert hm7["cells"][0]["symbol"] == "A"          # tri par perf 7j (25 > 1)
    assert hm7["cells"][0]["change_24h"] == 25.0     # slot perf = valeur 7j
    hm24 = _portfolio_heatmap(enriched)              # défaut 24h INCHANGÉ
    assert hm24["cells"][0]["symbol"] == "B"          # tri par perf 24h (9 > 0.1)
    assert hm24["cells"][0]["change_24h"] == 9.0


def test_weekly_heatmap_7d_rendered_below_sectors():
    """v23.x — le weekly affiche la heatmap 7j (4 lignes + « +N autres »), libellée
    7j et non 24h."""
    from src.main import _portfolio_heatmap
    from src.reporting.email_html import render
    enriched = {f"C{i}": {"value_usd": 100 + i, "change_7d": (i % 9) - 4.0}
                for i in range(24)}                  # 24 positions > 20 → agrégat
    hm = _portfolio_heatmap(enriched, change_key="change_7d")
    html = render({"header": {"date": "30/06"},
                   "portfolio_snapshot": {"value_usd": 2000},
                   "portfolio_heatmap_7d": hm}, "weekly")
    assert "Heatmap · performance 7j" in html
    assert "perf 7j moyenne pondérée" in html
    assert "autres" in html                          # case agrégée (24 − 19 = 5)
    assert "Heatmap · performance 24h" not in html   # pas la version 24h


def test_thesis_confidence_floor_is_75():
    """v23.x — seuil d'affichage des thèses relevé à 75% (anti-bruit, Omar)."""
    from src.main import THESIS_CONFIDENCE_FLOOR
    assert THESIS_CONFIDENCE_FLOOR == 75


def test_thesis_floor_filters_below_75():
    """Une thèse à 74% est retirée ; à 78% conservée (seuil unique 75%)."""
    from src.main import _merge_python_facts
    theses = [
        {"asset": "ADA", "action": "SURVEILLER", "action_type": "neutral", "confidence": 74},
        {"asset": "ETH", "action": "SURVEILLER", "action_type": "neutral", "confidence": 78},
    ]
    out = _merge_python_facts(
        {"thesis_of_the_day": theses}, {"eligible_theses": []}, "29/06 08:00")
    assert [t["asset"] for t in out["thesis_of_the_day"]] == ["ETH"]
    assert all((t.get("confidence") or 0) >= 75 for t in out["thesis_of_the_day"])


def test_thesis_all_below_75_yields_empty_with_reason():
    """Toutes les pistes < 75% → aucune thèse + raison honnête mentionnant 75%."""
    from src.main import _merge_python_facts
    theses = [{"asset": "INJ", "action": "SURVEILLER", "action_type": "neutral", "confidence": 68}]
    out = _merge_python_facts(
        {"thesis_of_the_day": theses}, {"eligible_theses": []}, "29/06 08:00")
    assert out["thesis_of_the_day"] == []
    assert "75%" in (out.get("thesis_empty_reason") or "")


def test_sector_cells_holdings_5_plus_n():
    """v23.x — le hebdo liste jusqu'à 5 cryptos (virgules) + « +N » pour le reste."""
    from src.reporting.email_html import render
    cells = [{"sector": "L1", "ptf_pct": 55.0, "market_change_24h": -0.4,
              "market_change_7d": 1.2, "market_change_30d": -3.0,
              "holdings": ["ADA", "AR", "ATOM", "DOT", "NEAR", "SOL", "AVAX"]}]
    payload = {"header": {"date": "29/06"},
               "portfolio_snapshot": {"value_usd": 2626},
               "sector_exposure_cells": cells}
    html = render(payload, "weekly")
    assert "ADA, AR, ATOM, DOT, NEAR" in html               # 5 cryptos, virgules
    assert "+2" in html                                     # 7 − 5 = reste
    assert "7j" in html and "30j" in html                   # multi-horizon visible
