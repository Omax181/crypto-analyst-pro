# -*- coding: utf-8 -*-
"""Audit final v26 — verrous des correctifs de l'audit ultra-exhaustif.

Chaque test rejoue le défaut EXACT détecté à l'audit final (faux positifs des
gardes hebdo, stablecoin dans le radar de sortie, invalidation négative sur
micro-prix, kill-switch OB24 inerte en prod, bruit « Google Gemini » dans les
news) et verrouille le comportement corrigé.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# F1 — « 25 points de base » (Fed) n'est JAMAIS réécrit en perf 7j d'indice
# --------------------------------------------------------------------------- #
def test_fix_equity_points_ignores_basis_points():
    from src.analytics import weekly_guards as wg

    txt = ("L'or brille tandis que la Fed a réduit ses taux de 25 points "
           "de base cette semaine.")
    out, fixes = wg.fix_equity_points(txt, {"gold": 2.4})
    assert "25 points de base" in out          # intact
    assert fixes == []


def test_fix_equity_points_still_fixes_real_index_points():
    from src.analytics import weekly_guards as wg

    txt = "Le Nasdaq a perdu -230.5 points."
    out, fixes = wg.fix_equity_points(txt, {"nasdaq": -1.2})
    assert "points" not in out
    assert "−1,2% (7j)" in out and len(fixes) == 1


# --------------------------------------------------------------------------- #
# F2 — la perf d'un ACTIF nommé n'est pas confondue avec la perf PTF
# --------------------------------------------------------------------------- #
def test_enforce_summary_keeps_named_asset_perf():
    from src.analytics import weekly_guards as wg

    bullets = ["La performance de BTC cette semaine : +2.1%, pendant que le "
               "portefeuille souffre."]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3})
    assert "+2.1%" in out[0]                   # perf BTC intacte
    assert fixes == []


def test_enforce_summary_still_fixes_ptf_perf():
    from src.analytics import weekly_guards as wg

    bullets = ["Performance du portefeuille : +2.32% sur la semaine."]
    out, fixes = wg.enforce_summary_figures(
        bullets, {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3})
    assert "+3,8%" in out[0] and "2.32" not in out[0]
    assert len(fixes) == 1


# --------------------------------------------------------------------------- #
# F3 — radar de sortie : un stablecoin n'est JAMAIS un signal d'allègement
# --------------------------------------------------------------------------- #
def test_exit_radar_skips_stablecoins():
    from src.analytics.exit_radar import compute_exit_signals

    out = compute_exit_signals([
        # USDC surpondéré (du cash, pas un risque de concentration volatile).
        {"symbol": "USDC", "pnl_pct": 0.1, "weight_pct": 25.0,
         "tier": "satellite", "change_7d": 0.0, "change_24h": 0.0},
        # Satellite réellement surpondéré → signal attendu.
        {"symbol": "JASMY", "pnl_pct": 5.0, "weight_pct": 15.0,
         "tier": "satellite", "change_7d": 2.0, "change_24h": 1.0},
    ])
    syms = [s["symbol"] for s in out["signals"]]
    assert "USDC" not in syms
    assert "JASMY" in syms


# --------------------------------------------------------------------------- #
# F4 — plan d'actif : l'invalidation n'est jamais ≤ 0 (micro-prix, ATR énorme)
# --------------------------------------------------------------------------- #
def test_asset_plan_invalidation_never_negative():
    from src.analytics.asset_plan import compute_asset_plan

    # 1 seul support et ATR > support → « s0 − 1 ATR » serait NÉGATIF.
    kl = {
        "available": True, "symbol": "PEPE", "price": 0.001,
        "supports": [{"level": 0.0002, "basis": "pivot"}],
        "resistances": [{"level": 0.002, "basis": "pivot"}],
        "readout": {"atr_abs": 0.0009},
    }
    plan = compute_asset_plan("PEPE", [0.001] * 40, price=0.001,
                              key_levels_result=kl)
    assert plan["available"] is True
    assert plan["invalidation"]["level"] > 0
    assert plan["invalidation"]["level"] < 0.001


# --------------------------------------------------------------------------- #
# F5 — zones de liquidation : bornes citées dans le SENS du mouvement
# --------------------------------------------------------------------------- #
def test_liquidation_short_squeeze_bounds_in_path_order():
    from src.analytics.liquidation_zones import compute_liquidation_zones

    out = compute_liquidation_zones(100.0, funding_annualized_pct=-15.0)
    assert out["bias"] == "short_heavy"
    note = out["bias_note"]
    # 104 $ (25×, la plus proche) doit être citée AVANT 110 $ (10×).
    assert note.index("104") < note.index("110")


# --------------------------------------------------------------------------- #
# F6 — kill-switch OB24 + pause ultime tentative CÂBLÉS dans les workflows
# --------------------------------------------------------------------------- #
def test_workflows_pass_learning_and_pause_env():
    wf = _ROOT / ".github" / "workflows"
    morning = (wf / "morning_report.yml").read_text(encoding="utf-8")
    assert "LEARNING_ENABLED" in morning, (
        "kill-switch OB24 non transmis : poser le secret serait inerte")
    for name in ("morning_report.yml", "evening_report.yml", "weekly_report.yml"):
        content = (wf / name).read_text(encoding="utf-8")
        assert "GEMINI_LAST_CHANCE_PAUSE_S" in content, name


def test_portfolio_buy_without_prior_pru_never_fabricates_gains():
    """PRU inconnu + achat : PRU = prix d'achat, jamais « stock gratuit »."""
    from src.utils import portfolio_editor as pe

    sample = (
        "portfolio:\n"
        "  OLD:\n"
        "    quantity: 100\n"
        "    value_usd: 500\n"
        "    tier: 3\n"
    )
    _, s = pe.apply_quantity_change(sample, "OLD", "buy", 10, 5.0)
    # Ancien bug : (0 + 10×5)/110 = 0.4545 → +1000% de faux gain latent.
    assert s["new_pru"] == 5.0


def test_news_google_gemini_is_not_crypto():
    from src.data_sources.news_relevance import is_crypto_relevant

    assert is_crypto_relevant(
        "Google's Gemini 3 tops AI benchmarks in latest release") is False
    assert is_crypto_relevant(
        "Gemini exchange expands custody services in Europe") is True
    assert is_crypto_relevant(
        "Winklevoss twins announce new fund") is True
