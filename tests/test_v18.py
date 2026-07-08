"""Tests v18 — corrections de l'audit de passage v17 → v18 (~100 points).

Verrouille les comportements INTRODUITS en v18 (sans réseau). Couvre les
chantiers Morning (M-*), Evening (E-*) et Weekly (W-*) :

MORNING
- M-A16  : tuile BTC Δ24h CHIFFRÉ (pct24) au lieu d'une flèche seule
- M-A18  : positions vs marché séparées ▲ hausse / ▼ baisse
- M-A23  : rotation sectorielle triée par |variation| + « Autres secteurs »
- M-A24  : conversion 10Y adaptative (^TNX % vs %×10)
- M-B2   : blocklist sport/divertissement (FIFA jamais affiché)
- M-B9   : BCE + BoJ inlinés « Taux directeurs »
- M-B12/14: garde-fou macro déterministe (VIX≥25 / peur / DXY)
- M-B17  : heatmap triée par |variation 24h|

EVENING
- E-A12/13: micro-prix 4 chiffres significatifs
- E-B4   : risque inchangé depuis matin → note compacte (holdings_snapshot)

WEEKLY
- W-A11  : thèses LT tronquées au mot près
- W-B12  : espérance mathématique
- W-B13  : alerte rééquilibrage
- W-B14  : mémoire des thèses invalidées
- W-B15  : exposition sectorielle en cases
- W-B16  : sparkline SVG de l'évolution du portefeuille
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import types
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _stub_tenacity() -> None:
    """Neutralise tenacity (réseau) pour pouvoir importer src.main hors-ligne."""
    ten = types.ModuleType("tenacity")
    ten.retry = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    ten.stop_after_attempt = ten.wait_exponential = ten.retry_if_exception_type = (
        lambda *a, **k: None
    )
    ten.before_sleep_log = lambda *a, **k: (lambda *aa, **kk: None)
    sys.modules["tenacity"] = ten


# --------------------------------------------------------------------------- #
# MORNING
# --------------------------------------------------------------------------- #
def test_m_a16_btc_tile_shows_numeric_delta():
    """M-A16 — la tuile BTC du matin affiche un Δ24h chiffré (+X%), pas qu'une flèche."""
    _stub_tenacity()
    from src.reporting.email_html import render

    p = {
        "header": {"date": "x", "active_sources_count": 16, "total_sources_count": 23},
        "portfolio_snapshot": {"value_usd": 1700},
        "macro_context": {"btc_price": 63180, "btc_change_24h": 2.17, "fear_greed": 8,
                          "dxy": 99.9, "polymarket_fed_cut_pct": 0.2},
    }
    html = render(p, "morning")
    assert "+2.2%" in html  # 2.17 arrondi à 1 décimale


def test_m_a18_movers_section_removed_from_morning():
    """v23.x — la section « TOP MOUVEMENTS MARCHÉ · 24h » ET la boîte « Tes
    positions ▲/▼ » ont été RETIRÉES du morning (demande d'Omar) : même avec des
    données market_movers/portfolio_movers présentes, rien ne doit s'afficher."""
    _stub_tenacity()
    from src.reporting.email_html import render

    p = {
        "header": {"date": "x", "active_sources_count": 16, "total_sources_count": 23},
        "portfolio_snapshot": {"value_usd": 1700},
        "market_movers": {
            "available": True,
            "gainers": [{"symbol": "NOT", "change_24h": 36}],
            "losers": [{"symbol": "COAI", "change_24h": -39}],
            "portfolio_movers": [
                {"symbol": "JASMY", "change_24h": 9.0},
                {"symbol": "AXL", "change_24h": -5.3},
                {"symbol": "ARB", "change_24h": -4.4},
            ],
        },
    }
    html = render(p, "morning")
    assert "Tes positions en hausse" not in html
    assert "Tes positions en baisse" not in html
    assert "Top mouvements marché" not in html
    # la microcap hors PTF ne fuit plus dans le morning
    assert "COAI" not in html


def test_m_a23_sector_rotation_by_abs_change_and_aggregate():
    """M-A23 — rotation triée par |variation| + case « Autres secteurs » pondérée."""
    _stub_tenacity()
    from src.main import _merge_python_facts

    data = {"sector_rotation": {"sectors": {
        "IoT": {"avg_change_24h": 9.21, "members": ["JASMY"]},
        "Interop": {"avg_change_24h": -4.93, "members": ["AXL"]},
        "L2": {"avg_change_24h": -3.72, "members": ["ARB", "CFX", "IMX"]},
        "Infra": {"avg_change_24h": -3.02, "members": ["ANKR"]},
        "AI": {"avg_change_24h": -2.03, "members": ["TAO", "FET"]},
        "Payments": {"avg_change_24h": -2.15, "members": ["XRP"]},
        "L1": {"avg_change_24h": -1.61, "members": ["BTC", "ETH"]},
        "DeFi": {"avg_change_24h": -1.75, "members": ["INJ"]},
    }}}
    out = _merge_python_facts({}, data, "t")
    rot = out["sector_rotation"]
    # Max 5 cases : 4 individuelles + 1 agrégat.
    assert len(rot) == 5
    # Trié par |variation| : IoT (9.21) en tête, Interop (4.93) ensuite.
    assert rot[0]["sector"] == "IoT"
    assert rot[1]["sector"] == "Interop"
    # Dernière case = agrégat.
    assert rot[-1].get("is_aggregate") is True
    assert "Autres secteurs" in rot[-1]["sector"]


def test_m_a24_10y_adaptive_conversion(monkeypatch):
    """M-A24 — ^TNX déjà en % (4.487) n'est PAS divisé ; reste dans [0.5, 12]."""
    _stub_tenacity()
    import pytest
    from src.data_sources import market_prices as mp

    def fake_get_json(url, headers=None, params=None, **kw):
        val = 4.487 if ("%5ETNX" in url or "^TNX" in url) else 200.0
        prev = 4.46 if ("%5ETNX" in url or "^TNX" in url) else 198.0
        return {"chart": {"result": [{
            "meta": {"regularMarketPrice": val, "chartPreviousClose": prev,
                     "previousClose": prev},
            "timestamp": [1, 2],
            "indicators": {"quote": [{"close": [prev, val]}]},
        }]}}

    monkeypatch.setattr(mp, "get_json", fake_get_json)
    mp.CACHE._store.clear()
    quotes = mp.get_macro_quotes()
    # 4.487 < 20 → conservé tel quel (PAS divisé en 0.4487).
    assert quotes["us_10y"] == pytest.approx(4.487)
    assert 0.5 <= quotes["us_10y"] <= 12.0  # dans la plage valide, jamais masqué


def test_m_b2_polymarket_blocklist_sports():
    """M-B2 + v23.x — sport/divertissement bloqués ; élections/nominations
    écartées (hors-tier) ; crypto/macro/géo gardés et PRIORISÉS crypto-first."""
    from src.data_sources.prediction_markets import _EXTRA_BLOCKLIST, _market_tier

    def passes(q: str) -> bool:
        ql = q.lower()
        if any(b in ql for b in _EXTRA_BLOCKLIST):
            return False
        return _market_tier(ql) is not None

    assert passes("Will Netherlands win the 2026 FIFA World Cup?") is False
    assert passes("New Rihanna Album before GTA VI?") is False
    # v23.x : les marchés électoraux/nominations n'ont plus de valeur → écartés.
    assert passes("Will Jon Stewart win the 2028 Democratic nomination?") is False
    assert passes("Will Gavin Newsom win the 2028 presidential election?") is False
    assert passes("US recession in 2026?") is True
    assert passes("BTC above 100k end-2026?") is True
    assert passes("Will Bitcoin drop below $50k in 2026?") is True
    # « Canada » ne doit PAS matcher le ticker ADA (mot entier).
    assert _market_tier("will canada hold elections in 2026") is None
    # crypto-first : le tier crypto est prioritaire (plus petit) sur le macro.
    assert _market_tier("bitcoin above 100k") < _market_tier("us recession 2026")


def test_m_b9_central_bank_rates_inline():
    """M-B9 — BCE + BoJ inlinés en une ligne « Taux directeurs »."""
    _stub_tenacity()
    from src.reporting.email_html import render

    p = {
        "header": {"date": "x", "active_sources_count": 16, "total_sources_count": 23},
        "portfolio_snapshot": {"value_usd": 1700},
        "macro_context": {"btc_price": 63000, "stoxx50": 6188, "nikkei": 66020,
                          "ecb_deposit_rate": 2.0, "boj_rate": 0.5},
    }
    html = render(p, "morning")
    assert "Taux directeurs" in html
    assert "BCE dépôt 2.00%" in html
    assert "BoJ 0.50%" in html


def test_m_b12_macro_guardrail_active_and_inactive():
    """M-B12/14 — garde-fou macro actif si VIX≥25, inactif si marché calme."""
    _stub_tenacity()
    from src.main import _compute_macro_guardrail

    g = _compute_macro_guardrail({"vix": 28, "fear_greed": 12, "dxy": 99})
    assert g["active"] is True
    assert any("VIX" in t for t in g["triggers"])
    assert any("28" in t for t in g["triggers"])

    g2 = _compute_macro_guardrail({"vix": 15, "fear_greed": 55, "dxy": 99})
    assert g2["active"] is False


def test_m_b17_heatmap_sorted_by_movement():
    """v28 (M-A11) — la heatmap est triée par IMPACT (poids × |variation|) :
    ce qui bouge le portefeuille d'abord, plus le bruit des poussières."""
    _stub_tenacity()
    from src.main import _portfolio_heatmap

    enriched = {f"A{i}": {"value_usd": 100 - i, "change_24h": (i - 9) * 1.0}
                for i in range(22)}  # > 15 pour déclencher l'agrégat
    enriched["BIG"] = {"value_usd": 200, "change_24h": 18.0}
    enriched["CRASH"] = {"value_usd": 150, "change_24h": -15.0}
    hm = _portfolio_heatmap(enriched)
    # Les 2 plus gros IMPACTS (mouvement × poids) en tête.
    assert hm["cells"][0]["symbol"] == "BIG"
    assert hm["cells"][1]["symbol"] == "CRASH"
    # Agrégat = positions au moindre impact.
    assert hm["extra"] is not None


# --------------------------------------------------------------------------- #
# EVENING
# --------------------------------------------------------------------------- #
def test_e_a12_micro_price_four_sig_figs():
    """E-A12/13 — micro-prix à 4 chiffres significatifs (0.00523, pas 0.00522973)."""
    from src.reporting.email_html import _fmt_price, _fmt_money

    assert _fmt_price(0.00522973) == "$0.00523"
    assert _fmt_price(0.00103287) == "$0.001033"
    # 1e-8 reste affiché entièrement (jamais arrondi à zéro).
    assert _fmt_price(0.00000001) == "$0.00000001"
    # Les prix normaux ne changent pas.
    assert _fmt_price(63180) == "$63,180"
    assert _fmt_price(8.98) == "$8.98"
    assert _fmt_money(0.00522973) == "$0.00523"


def test_e_b4_risk_unchanged_compact_note():
    """E-B4 (v23) — quand la SANTÉ est inchangée depuis le matin, note compacte
    (pas de barres ni de driver/improve)."""
    _stub_tenacity()
    from src.reporting.email_html import render

    p = {
        "header": {"date": "x", "time_casablanca": "20:00"},
        "portfolio_snapshot": {"value_usd": 1700},
        "daily_pnl": {"value_usd": 1700, "day_change_pct": 0.1, "day_change_usd": 2},
        "health_score": {"score": 5.0, "level": "correct",
                         "axes": [{"label": "Diversification", "score": 3.0, "max": 10.0}],
                         "driver": "Pénalisée par Diversification (3.0/10).",
                         "improve": "Alléger le secteur dominant."},
        "health_unchanged_since_morning": {
            "active": True,
            "note": "Santé stable depuis ce matin (5.0/10 → 5.0/10) — composition du portefeuille inchangée.",
        },
    }
    html = render(p, "evening")
    assert "Santé stable depuis ce matin" in html
    # Les barres ET le driver/improve sont masqués (vue compacte).
    assert "Diversification" not in html


def test_e_b4_holdings_snapshot_excludes_zero():
    """E-B4 — holdings_snapshot ne contient que les positions à valeur > 0."""
    _stub_tenacity()
    from src.main import _compute_portfolio_risk_score

    out = _compute_portfolio_risk_score(
        {"change_7d_pct": -5},
        {"sectors": [{"sector": "L1", "ptf_pct": 40}]},
        {"fear_greed": 30},
        {"BTC": {"value_usd": 1000, "change_24h": 1}},
        {"BTC": {"value_usd": 1000}, "USDC": {"value_usd": 0}},
    )
    assert out["holdings_snapshot"] == ["BTC"]


# --------------------------------------------------------------------------- #
# WEEKLY
# --------------------------------------------------------------------------- #
def test_w_a11_lt_thesis_truncated_at_word():
    """v19/W-A11 + v23.x — l'ANALYSE LT (tableau fusionné positions_review) n'est
    PLUS tronquée par « … » : elle apparaît entière, dernier mot inclus."""
    _stub_tenacity()
    from src.reporting.email_html import render

    long_analysis = ("-45% sous ATH halving digere dominance en hausse zone "
                     "accumulation du coeur renforcer par paliers sur repli profond")
    p = {
        "header": {"date": "x"},
        "portfolio_snapshot": {"value_usd": 2626},
        "positions_review": [{
            "asset": "BTC", "conviction": True, "current_price": 60000,
            "pru_pct": 5.0, "h30": {"reco": "RENFORCER", "delta_pct": -0.4,
                                    "status": "in_progress"},
            "lt_status": "accumulation", "lt_target": 126000, "lt_target_pct": 110,
            "analysis": long_analysis, "action": "renforcer"}],
    }
    html = render(p, "weekly")
    # L'analyse COMPLÈTE apparaît (dernier mot inclus), sans coupe.
    assert long_analysis in html
    assert "profond" in html
    # Aucun fragment de mot tronqué par l'ancienne logique.
    assert "accumulatio…" not in html and "profon…" not in html


def test_w_b12_expectancy_math():
    """W-B12 — espérance = gain_moyen × winrate + perte_moyenne × (1−winrate)."""
    _stub_tenacity()
    import src.state.report_memory as mem

    d = tempfile.mkdtemp()
    mem._STATE_DIR = pathlib.Path(d)
    now = datetime.now(timezone.utc).isoformat()
    hist = []
    for i in range(4):  # 4 validées : entrée 100 → cible 115 = +15%
        hist.append({"asset": f"A{i}", "action": "RENFORCER", "status": "validated",
                     "created_at": now, "entry_price": 100, "ct_target": 115,
                     "stop_loss": 92})
    for i in range(2):  # 2 invalidées : entrée 100 → stop 92 = −8%
        hist.append({"asset": f"B{i}", "action": "RENFORCER", "status": "invalidated",
                     "created_at": now, "entry_price": 100, "ct_target": 115,
                     "stop_loss": 92})
    mem._write(mem.PREDICTION_HISTORY_FILE, hist)

    from src.tracking.prediction_scoring import PredictionTracker

    exp = PredictionTracker().compute_expectancy(30)
    assert exp["available"] is True
    assert exp["win_rate_pct"] == 67  # 4/6
    assert exp["avg_gain_pct"] == 15.0
    assert exp["avg_loss_pct"] == -8.0
    # 15 × 0.667 + (−8) × 0.333 = 7.3
    assert exp["expectancy_pct"] == 7.3


def test_w_b12_expectancy_requires_five_closed():
    """W-B12 — sous 5 recos clôturées, l'espérance n'est pas publiée."""
    _stub_tenacity()
    import src.state.report_memory as mem

    d = tempfile.mkdtemp()
    mem._STATE_DIR = pathlib.Path(d)
    now = datetime.now(timezone.utc).isoformat()
    mem._write(mem.PREDICTION_HISTORY_FILE, [
        {"asset": "A", "action": "RENFORCER", "status": "validated",
         "created_at": now, "entry_price": 100, "ct_target": 115},
    ])
    from src.tracking.prediction_scoring import PredictionTracker

    exp = PredictionTracker().compute_expectancy(30)
    assert exp["available"] is False
    assert exp["sample"] == 1


def test_w_b13_rebalance_alert_renders():
    """W-B13 / v23.x — l'alerte de concentration s'affiche dans le bloc UNIQUE
    « Lecture concentration » (fusion des 2 anciens blocs)."""
    _stub_tenacity()
    from src.reporting.email_html import render

    p = {
        "header": {"date": "x"},
        "portfolio_snapshot": {"value_usd": 2626},
        "rebalance_alert": {"active": True, "top_sector": "L1", "top_pct": 61.1,
                            "message": "Portefeuille tres concentre (secteurs L1 61% du PTF)."},
    }
    html = render(p, "weekly")
    assert "Lecture concentration" in html      # un seul bloc, libellé unifié
    assert "Alerte rééquilibrage" not in html    # plus de 2e bloc séparé
    assert "secteurs L1 61%" in html


def test_w_b13_single_concentration_block_no_duplicate():
    """v23.x — quand une alerte est active, la lecture qualitative IA n'est PAS
    affichée en plus (un seul bloc, pas de doublon)."""
    _stub_tenacity()
    from src.reporting.email_html import render
    p = {
        "header": {"date": "x"},
        "portfolio_snapshot": {"value_usd": 2626},
        "concentration_reading": "BLABLA_IA_REDONDANT",
        "rebalance_alert": {"active": True, "message": "Portefeuille tres concentre (secteurs L1 61% du PTF)."},
    }
    html = render(p, "weekly")
    assert "secteurs L1 61%" in html
    assert "BLABLA_IA_REDONDANT" not in html     # l'alerte prime, pas de doublon
    assert html.count("Lecture concentration") == 1


def test_w_b14_invalidation_lessons_detects_repeats():
    """W-B14 — la mémoire d'apprentissage détecte un actif invalidé ≥ 2 fois."""
    _stub_tenacity()
    import src.state.report_memory as mem

    d = tempfile.mkdtemp()
    mem._STATE_DIR = pathlib.Path(d)
    now = datetime.now(timezone.utc).isoformat()
    mem._write(mem.PREDICTION_HISTORY_FILE, [
        {"asset": "TAO", "status": "invalidated", "created_at": now},
        {"asset": "TAO", "status": "invalidated", "created_at": now},
        {"asset": "FET", "status": "invalidated", "created_at": now},
    ])
    from src.tracking.prediction_scoring import PredictionTracker

    les = PredictionTracker().compute_invalidation_lessons(60)
    assert les["available"] is True
    assert les["count"] == 3
    assert les["repeated_assets"] == [{"asset": "TAO", "times": 2}]


def test_w_b15_sector_cells_render():
    """W-B15 — l'exposition sectorielle s'affiche en cases (pas en barres)."""
    _stub_tenacity()
    from src.reporting.email_html import render

    cells = [
        {"sector": "L1", "ptf_pct": 61.1, "market_change_24h": 0.7, "holdings": ["ADA"]},
        {"sector": "Autres secteurs (3)", "ptf_pct": 5.5, "market_change_24h": 0.5,
         "holdings": [], "is_aggregate": True},
    ]
    html = render(
        {"header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2626},
         "sector_exposure_cells": cells},
        "weekly",
    )
    assert "61.1%" in html
    assert "Autres secteurs (3)" in html


def test_w_b16_ptf_evolution_bars_gmail_safe():
    """v20 (audit C1) — l'évolution PTF s'affiche en mini-barres HTML/CSS
    (Gmail-safe) ; la sparkline SVG inline a été RETIRÉE (Gmail supprime les
    <svg>), donc le HTML weekly ne doit plus contenir de <svg>."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "19/06"},
        "portfolio_snapshot": {"value_usd": 2626.0},
        "ptf_evolution": [
            {"label": f"S{20 + i}", "value": v}
            for i, v in enumerate([1694, 2100, 1950, 2432, 2626])
        ],
    }
    html = render(payload, "weekly")
    assert "<svg" not in html  # plus aucun SVG inline (invisible dans Gmail)
    assert "Évolution PTF" in html  # libellé du bloc barres
    assert "border-radius:2px 2px 0 0" in html  # barres HTML rendues


# --------------------------------------------------------------------------- #
# CHANTIER E — Signaux d'analyse transverses (Partie 4)
# --------------------------------------------------------------------------- #
def test_e_liquidity_regime_expansion():
    """#9 — M2 en hausse sur ~3 mois → régime d'expansion."""
    from src.analytics import cross_signals as cs

    fred = {"m2": {"2026-01-01": 21000, "2026-02-01": 21050,
                   "2026-03-01": 21100, "2026-04-01": 21300}}
    out = cs.liquidity_regime(fred)
    assert out["available"] is True
    assert out["trend"] == "expansion"
    assert out["change_pct"] > 0


def test_e_credit_risk_widening():
    """#11 — spreads HY au-dessus de leur moyenne → écartement (risk-off)."""
    from src.analytics import cross_signals as cs

    # 5 valeurs basses puis un saut → la dernière est nettement > moyenne.
    fred = {"hy_spread": {f"2026-04-{d:02d}": (3.0 if d < 6 else 3.6)
                          for d in range(1, 7)}}
    out = cs.credit_risk(fred)
    assert out["available"] is True
    assert out["widening"] is True


def test_e_seasonality_present_for_all_months():
    """#12 — la saisonnalité renvoie un contexte pour les 12 mois."""
    from datetime import datetime, timezone
    from src.analytics import cross_signals as cs

    for m in range(1, 13):
        out = cs.seasonality(datetime(2026, m, 15, tzinfo=timezone.utc))
        assert out["available"] is True
        assert out["month"] == m
        assert out["reading"]


def test_e_market_structure_detects_trends():
    """#17 — structure HH/HL = haussière, LH/LL = baissière."""
    from src.analytics import cross_signals as cs

    up = [100, 102, 101, 104, 103, 107, 105, 110, 108, 113, 111, 116]
    down = [116, 114, 115, 110, 111, 107, 108, 103, 104, 100, 101, 97]
    out = cs.market_structure({"AAA": up, "BBB": down})
    assert out["available"] is True
    assert out["structures"]["AAA"] == "haussière"
    assert out["structures"]["BBB"] == "baissière"


def test_e_confirmation_bias_three_same_direction():
    """#16 — 3 dernières thèses même sens sur un actif → garde-fou actif."""
    from src.analytics import cross_signals as cs

    theses = [
        {"asset": "TAO", "action_type": "bullish"},
        {"asset": "TAO", "action_type": "bullish"},
        {"asset": "TAO", "action_type": "bullish"},
        {"asset": "ETH", "action_type": "bullish"},
        {"asset": "ETH", "action_type": "bearish"},
    ]
    out = cs.confirmation_bias_guard(theses)
    assert out["active"] is True
    flagged = {f["asset"] for f in out["flagged_assets"]}
    assert "TAO" in flagged  # 3 bullish d'affilée
    assert "ETH" not in flagged  # directions mixtes


def test_e_mvrv_context_zones():
    """#7 — MVRV < 1 = accumulation, > 3.5 = euphorie."""
    from src.analytics import cross_signals as cs

    assert cs.mvrv_context(0.85)["zone"] == "accumulation"
    assert cs.mvrv_context(4.0)["zone"] == "euphorie"
    assert cs.mvrv_context(2.0)["zone"] == "neutre"
    assert cs.mvrv_context(None)["available"] is False


def test_e_compute_all_aggregates_readings():
    """compute_all agrège les lectures disponibles en une liste exploitable."""
    from src.analytics import cross_signals as cs

    fred = {
        "m2": {"2026-01-01": 21000, "2026-02-01": 21100, "2026-03-01": 21200,
               "2026-04-01": 21400},
        "dxy": {f"2026-04-{d:02d}": 100 - d * 0.15 for d in range(1, 12)},
    }
    out = cs.compute_all(fred, {}, mvrv_value=0.9)
    assert "signals" in out and "readings" in out
    # Au moins liquidité + DXY + MVRV doivent être présents.
    assert len(out["readings"]) >= 3
    assert "liquidity_regime" in out["signals"]
    assert "mvrv_context" in out["signals"]


def test_e_realized_vol_regime_compression():
    """#4 — vol récente nettement < vol antérieure → compression."""
    from src.analytics import cross_signals as cs

    # 10 points très volatils puis 11 points calmes → compression récente.
    volatile = [100, 108, 94, 110, 92, 112, 90, 113, 95, 109, 100]
    calm = [100, 100.5, 100.2, 100.6, 100.3, 100.7, 100.4, 100.8, 100.5, 100.9]
    prices = {"AAA": volatile + calm}
    out = cs.realized_vol_regime(prices)
    assert out["available"] is True
    assert out["regime"] == "compression"


def test_e_allocation_gap_over_under():
    """#1 — sur/sous-pondération vs cibles ; dégrade sans cibles."""
    from src.analytics import cross_signals as cs

    pf = {"portfolio": {"BTC": {"target_pct": 30}, "ETH": {"target_pct": 20},
                        "TAO": {"target_pct": 10}}}
    enr = {"BTC": {"value_usd": 500}, "ETH": {"value_usd": 500},
           "TAO": {"value_usd": 100}}
    out = cs.compute_allocation_gap(pf, enr)
    assert out["available"] is True
    by_asset = {p["asset"]: p for p in out["positions"]}
    # BTC 45.5% vs 30% → surpondéré ; TAO 9.1% vs 10% → à la cible.
    assert by_asset["BTC"]["status"] == "surpondérée"
    assert by_asset["TAO"]["status"] == "à la cible"
    # Sans cibles → indisponible (dégradation propre).
    assert cs.compute_allocation_gap({"portfolio": {"BTC": {}}}, enr)["available"] is False


def test_e_price_fundamentals_divergence():
    """#6 — prix↓ + activité↑ = divergence haussière ; prix↑ + activité↓ = prudence."""
    from src.analytics import cross_signals as cs

    oc = {"ETH": {"active_addresses_trend_pct": 6.0},
          "BTC": {"active_addresses_trend_pct": -5.0}}
    mk = {"ETH": {"change_7d": -8.0}, "BTC": {"change_7d": 5.0}}
    out = cs.compute_price_fundamentals_divergence(oc, mk)
    assert out["available"] is True
    by_asset = {d["asset"]: d["type"] for d in out["divergences"]}
    assert by_asset["ETH"] == "haussière"
    assert by_asset["BTC"] == "prudence"


def test_e_similar_context_matches_history():
    """#8 — un contexte macro proche d'un snapshot passé est retrouvé."""
    from src.analytics import cross_signals as cs

    snaps = [
        {"week_label": "S18", "vix": 14, "fear_greed": 22, "dxy": 99, "value_usd": 2000},
        {"week_label": "S19", "vix": 30, "fear_greed": 50, "dxy": 103, "value_usd": 2100},
        {"week_label": "S20", "vix": 15, "fear_greed": 20, "dxy": 99.2, "value_usd": 2050},
        {"week_label": "S21", "vix": 28, "fear_greed": 45, "dxy": 102, "value_usd": 2200},
        {"week_label": "S22", "vix": 16, "fear_greed": 25, "dxy": 99.5, "value_usd": 2300},
    ]
    cur = {"vix": 14.5, "fear_greed": 21, "dxy": 99.1}
    out = cs.compute_similar_context(cur, snaps)
    assert out["available"] is True
    assert out["match"]["distance"] < 1.0
    assert "next_week_pct" in out["match"]


def test_e_implied_move_from_dvol():
    """#2 — move implicite dérivé de DVOL ; mentionne un événement proche."""
    from src.analytics import cross_signals as cs

    out = cs.compute_implied_move(55, [{"label": "FOMC", "days_ahead": 1}])
    assert out["available"] is True
    assert out["move_2d_pct"] > 0
    assert "FOMC" in out["reading"]
    # Sans DVOL → indisponible.
    assert cs.compute_implied_move(None)["available"] is False


def test_e_derivatives_funding_signal():
    """#14 — funding élevé = longs en excès ; négatif = shorts en excès."""
    from src.analytics import cross_signals as cs

    out = cs.compute_derivatives_signal({
        "BTC": {"funding_annualized_pct": 45, "available": True},
        "ETH": {"funding_annualized_pct": -15, "available": True},
        "SOL": {"funding_annualized_pct": 5, "available": True},  # neutre
    })
    assert out["available"] is True
    states = {s["asset"]: s["state"] for s in out["signals"]}
    assert states["BTC"] == "longs en excès"
    assert states["ETH"] == "shorts en excès"
    assert "SOL" not in states  # funding neutre, pas signalé


def test_e_target_calibration_optimistic():
    """#15 — calibration des cibles : détecte des cibles trop optimistes."""
    import pathlib
    import tempfile
    from datetime import datetime, timezone

    _stub_tenacity()
    import src.state.report_memory as mem

    d = tempfile.mkdtemp()
    mem._STATE_DIR = pathlib.Path(d)
    now = datetime.now(timezone.utc).isoformat()
    hist = []
    for i in range(3):  # cibles atteintes
        hist.append({"asset": f"A{i}", "status": "validated", "created_at": now,
                     "entry_price": 100, "ct_target": 115, "exit_price": 116})
    for i in range(3):  # cibles ratées de loin
        hist.append({"asset": f"B{i}", "status": "invalidated", "created_at": now,
                     "entry_price": 100, "ct_target": 115, "exit_price": 105})
    mem._write(mem.PREDICTION_HISTORY_FILE, hist)

    from src.tracking.prediction_scoring import PredictionTracker

    tc = PredictionTracker().compute_target_calibration(90)
    assert tc["available"] is True
    assert tc["hit_rate_pct"] == 50
    # 3 hits à 116% + 3 ratés à ~33% → moyenne ~70% → légèrement optimistes.
    assert tc["bias"] in ("légèrement optimistes", "trop optimistes")


def test_e_target_calibration_requires_five():
    """#15 — sous 5 recos avec cible+sortie, pas de calibration publiée."""
    import pathlib
    import tempfile
    from datetime import datetime, timezone

    _stub_tenacity()
    import src.state.report_memory as mem

    d = tempfile.mkdtemp()
    mem._STATE_DIR = pathlib.Path(d)
    now = datetime.now(timezone.utc).isoformat()
    mem._write(mem.PREDICTION_HISTORY_FILE, [
        {"asset": "A", "status": "validated", "created_at": now,
         "entry_price": 100, "ct_target": 115, "exit_price": 116},
    ])
    from src.tracking.prediction_scoring import PredictionTracker

    assert PredictionTracker().compute_target_calibration(90)["available"] is False


def test_e_narrative_lifecycle_stages():
    """#3 — stade du cycle narratif inféré du momentum 7j/30j."""
    from src.analytics import cross_signals as cs

    rot = {"sectors": {
        "AI": {"avg_change_7d": 5.0, "avg_change_30d": 15.0},    # euphorie
        "L2": {"avg_change_7d": -2.0, "avg_change_30d": 12.0},   # rotation
        "RWA": {"avg_change_7d": 4.0, "avg_change_30d": 2.0},    # émergence
        "Meme": {"avg_change_7d": -1.0, "avg_change_30d": -5.0}, # consolidation
    }}
    out = cs.compute_narrative_lifecycle(rot)
    assert out["available"] is True
    stages = {n["sector"]: n["stage"] for n in out["narratives"]}
    assert stages["AI"] == "euphorie"
    assert stages["L2"] == "rotation"
    assert stages["RWA"] == "émergence"
    assert stages["Meme"] == "consolidation"


def test_e_strategic_wallets_in_compute_all():
    """#5 — l'activité des wallets stratégiques est intégrée si disponible."""
    from src.analytics import cross_signals as cs

    sw = {"available": True,
          "movements": [{"label": "Ethereum Foundation", "eth": 150,
                         "direction": "sortant"}],
          "interpretation": "1 mouvement de wallet stratégique (EF, 150 ETH sortant)"}
    out = cs.compute_all({}, {}, strategic_wallets=sw)
    assert "strategic_wallets" in out["signals"]
    assert any("stratégique" in r for r in out["readings"])

    # Indisponible → pas dans les signaux.
    out2 = cs.compute_all({}, {}, strategic_wallets={"available": False})
    assert "strategic_wallets" not in out2["signals"]


# --------------------------------------------------------------------------- #
# CHANTIER F — Scoring pondéré des thèses (Partie 5)
# --------------------------------------------------------------------------- #
def test_f_weighted_score_calm_lt_opportunity_eligible():
    """F — un actif CALME mais MVRV<1 + sous PRU devient éligible (conviction).

    C'est le cœur de l'audit : l'ancien comptage binaire ratait ces entrées
    d'accumulation qui arrivent sans mouvement de prix.
    """
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 1.2, "tech_advanced": {}}, tier=1,
        mvrv=0.85, pru_gap_pct=-15,
    )
    # 2 signaux fondamentaux LT à poids 3 = score 6 ≥ seuil Tier 1 (4).
    assert out["score"] >= 4
    assert out["eligible"] is True
    assert out["thesis_type"] == "conviction"


def test_f_tactical_thesis_from_technical_catalyst():
    """F — mouvement + BB squeeze + catalyseur → thèse tactique éligible."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 7.0, "news_24h_count": 1,
         "tech_advanced": {"bb_squeeze": True}}, tier=1,
        upcoming_catalyst_days=3,
    )
    assert out["eligible"] is True
    assert out["thesis_type"] == "tactical"


def test_f_empty_signals_not_eligible():
    """F — sans signal significatif, pas d'éligibilité."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 0.4, "tech_advanced": {}}, tier=2,
    )
    assert out["score"] == 0
    assert out["eligible"] is False


def test_f_single_strong_lt_signal_enough_for_tier1():
    """v21 (#73) — un signal LT UNIQUE (1 famille, poids 3) ne suffit PLUS, même
    à Tier 3 : la convergence (≥2 familles, ou cluster fondamental fort) est
    désormais requise. Avant v21 ce cas passait à Tier 3 — règle volontairement
    durcie pour supprimer les thèses mono-dimension."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 1.0, "tech_advanced": {}}, tier=1, mvrv=0.9,
    )
    assert out["score"] == 3
    assert out["eligible"] is False  # seuil Tier 1 = 4
    # Tier 3 : score 3 ≥ seuil 2, MAIS 1 seule famille → non convergent → inéligible.
    out3 = evaluate_thesis_eligibility(
        {"change_24h": 1.0, "tech_advanced": {}}, tier=3, mvrv=0.9,
    )
    assert out3["eligible"] is False
    assert out3["convergent"] is False and out3["families_count"] == 1


def test_f_confidence_bounds_by_type():
    """F/v23.x — bornes : seuil d'affichage UNIQUE 75% ; tactique 75-80,
    conviction 75-85, cap 80 si <5 dims."""
    from src.analytics.thesis_scoring import confidence_bounds

    tac = confidence_bounds("tactical", 3)
    assert tac["floor"] == 75 and tac["cap"] == 80      # tactique peut atteindre 75

    conv_many = confidence_bounds("conviction", 6)
    assert conv_many["floor"] == 75 and conv_many["cap"] == 85

    # < 5 dimensions → plafond ramené à 80 même en conviction.
    conv_few = confidence_bounds("conviction", 3)
    assert conv_few["cap"] == 80


def test_f_alleger_signal_on_token_unlock():
    """F — un unlock imminent est un signal (catalyseur baissier = ALLÉGER)."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 2.0, "tech_advanced": {}}, tier=2,
        token_unlock_soon=True,
    )
    labels = " ".join(s["label"] for s in out["signals"])
    assert "unlock" in labels.lower()


def test_f_technical_signals_use_real_tech_advanced_keys():
    """F (audit) — les signaux techniques lisent la VRAIE structure tech_advanced.

    Régression-test du bug d'audit : thesis_scoring lisait des clés à plat
    inexistantes (bollinger_position, ma_cross, bb_squeeze) alors que
    technical_advanced produit des sous-objets (bollinger.position,
    moving_averages.cross, bollinger.width_pct). Toute la catégorie technique
    était morte.
    """
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    ta = {
        "bollinger": {"position": "lower", "width_pct": 6.0},
        "moving_averages": {"cross": "golden"},
        "dist_to_support_pct": 1.0,
    }
    out = evaluate_thesis_eligibility(
        {"change_24h": 0.5, "tech_advanced": ta}, tier=2,
    )
    tech = [s for s in out["signals"] if s["category"] == "technical_struct"]
    # Au moins bollinger basse + support proche + golden cross + squeeze.
    assert len(tech) >= 4
    labels = " ".join(s["label"] for s in tech)
    assert "Bollinger" in labels
    assert "golden cross" in labels


def test_f_fundamental_divergence_reads_onchain_key():
    """F (audit) — la divergence prix/fonda lit asset['onchain']['active_addresses_trend_pct']."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    asset = {"change_24h": -4.0, "tech_advanced": {},
             "onchain": {"active_addresses_trend_pct": 8.0}}
    out = evaluate_thesis_eligibility(asset, tier=1)
    labels = " ".join(s["label"] for s in out["signals"])
    assert "activité réseau" in labels


def test_f_drawdown_signal_uses_ath_distance():
    """F (audit) — le signal drawdown profond s'évalue (poids 3, tier 0-2)."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"change_24h": 0.0, "tech_advanced": {}}, tier=1,
        drawdown_from_ath_pct=-72,
    )
    labels = " ".join(s["label"] for s in out["signals"])
    assert "drawdown" in labels.lower()
    # Drawdown profond = signal fondamental poids 3.
    assert out["fundamental_weight"] >= 3
