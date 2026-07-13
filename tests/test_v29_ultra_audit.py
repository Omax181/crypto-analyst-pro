"""v29 — verrous de l'AUDIT ULTRA (sécurité, code mort, honnêteté, hardening).

Chaque test verrouille un correctif de l'audit exhaustif du zip v29 :
  * SEC1 — plus de mot de passe d'édition PAR DÉFAUT dans le code ;
  * SEC2 — plus d'``eval`` ni d'interpolation d'inputs dans les workflows ;
  * CLEAN — les modules morts (Kaito, CryptoPanic, binance spot, patterns,
    volatility_assessor, content_filter, youtube_cpt) ont disparu, ainsi que
    les 7 fonctions charts retirées du rendu en v28 ;
  * HON — l'étiquette de la source narratifs dit la vérité (CoinGecko) ;
  * ROB — le digest Telegram garde un Markdown équilibré même sur des
    données hostiles (ticker avec ``*``/``_``, header balisé).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# SEC1 — mot de passe d'édition : jamais de défaut en dur
# --------------------------------------------------------------------------- #
def test_no_default_edit_password_anywhere():
    for rel in ("src/telegram_bot/portfolio_edit.py",
                ".github/workflows/telegram_bot.yml"):
        txt = (_ROOT / rel).read_text(encoding="utf-8")
        assert "Omax181" not in txt, f"défaut en dur résiduel dans {rel}"


def test_env_example_documents_required_secrets():
    txt = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("PORTFOLIO_EDIT_PASSWORD", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "NEWSAPI_KEY", "LEARNING_ENABLED",
                "GEMINI_MODEL_DEEP", "RELAY_PULL_URL", "RELAY_SECRET"):
        assert var in txt, f"{var} absent de .env.example"
    # Variables MORTES retirées (jamais lues par le code).
    assert "CRYPTOQUANT_API_KEY" not in txt
    assert "TIMEZONE" not in txt
    assert "CRYPTOPANIC_API_KEY" not in txt


# --------------------------------------------------------------------------- #
# SEC2 — workflows : pas d'eval, pas d'inputs interpolés dans le shell
# --------------------------------------------------------------------------- #
def test_update_portfolio_workflow_no_shell_injection():
    txt = (_ROOT / ".github/workflows/update_portfolio.yml").read_text(encoding="utf-8")
    assert "eval " not in txt and "eval\n" not in txt
    # Les inputs ne sont interpolés QUE dans des blocs env: (jamais dans run:).
    in_run = False
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("run:"):
            in_run = True
            continue
        if s.startswith(("- name:", "env:", "uses:")):
            in_run = False
        if in_run:
            assert "${{ inputs." not in line, f"input interpolé dans run: {s}"


def test_workflows_no_dead_kaito_key():
    for wf in ("morning_report.yml", "evening_report.yml", "weekly_report.yml"):
        txt = (_ROOT / ".github/workflows" / wf).read_text(encoding="utf-8")
        assert "KAITO_API_KEY" not in txt, f"clé du module mort dans {wf}"


# --------------------------------------------------------------------------- #
# CLEAN — modules et fonctions morts supprimés
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("dead", [
    "src/data_sources/kaito.py", "src/data_sources/youtube_cpt.py",
    "src/data_sources/cryptopanic.py", "src/data_sources/binance.py",
    "src/analytics/patterns.py", "src/reporting/volatility_assessor.py",
    "src/reporting/content_filter.py",
])
def test_dead_modules_removed(dead):
    assert not (_ROOT / dead).exists(), f"module mort encore présent : {dead}"


def test_dead_chart_functions_removed():
    from src.reporting import charts
    for fn in ("sector_donut_png", "weekly_perf_bars_png", "fng_sparkline_png",
               "btc_levels_png", "correlation_heatmap_png",
               "funding_history_png", "gauge_png"):
        assert not hasattr(charts, fn), f"fonction chart morte : {fn}"
    # Les producteurs VIVANTS restent intacts.
    for fn in ("charts_for_theses", "charts_for_tracked_recos",
               "portfolio_evolution_png"):
        assert hasattr(charts, fn), f"fonction vivante disparue : {fn}"


def test_dead_source_helpers_removed():
    from src.data_sources import binance_futures, market_prices
    from src.utils import portfolio_editor, portfolio_loader
    assert not hasattr(binance_futures, "get_funding_history")
    assert not hasattr(binance_futures, "get_oi_history")
    assert not hasattr(market_prices, "cross_check_prices")
    assert not hasattr(portfolio_loader, "exchange_symbol")
    assert not hasattr(portfolio_editor, "add_asset")
    # Les fonctions vivantes du même module ne sont pas touchées.
    assert hasattr(binance_futures, "get_derivatives")
    assert hasattr(portfolio_editor, "apply_quantity_change")


def test_sources_yaml_no_dead_endpoints():
    txt = (_ROOT / "config/sources.yaml").read_text(encoding="utf-8")
    assert "cryptopanic" not in txt
    # binance_symbols reste (tradingview + binance_futures) mais plus
    # d'endpoint spot binance (module supprimé).
    assert "binance_symbols" in txt
    assert 'binance: "https' not in txt


# --------------------------------------------------------------------------- #
# HON — la source narratifs dit sa vraie origine (CoinGecko, plus Kaito)
# --------------------------------------------------------------------------- #
def test_narratives_source_label_is_honest():
    import inspect

    from src import main as m
    assert "Narratifs (CoinGecko)" in m._ALL_SOURCES_LIST
    assert "Kaito" not in m._ALL_SOURCES_LIST
    src = inspect.getsource(m._active_sources)
    assert "Narratifs (CoinGecko)" in src
    # v29 (audit) — le drapeau « narratifs » est branché sur la VRAIE source
    # (hot_narratives / CoinGecko), plus sur la sortie du module Kaito mort.
    col = inspect.getsource(m._collect_morning_data)
    assert "narratives=hot_narratives" in col
    assert "kaito" not in col.lower()


# --------------------------------------------------------------------------- #
# ROB — Markdown Telegram équilibré même sur données hostiles
# --------------------------------------------------------------------------- #
def test_notify_markdown_survives_hostile_payload():
    from src.telegram_bot import notify

    hostile = {
        "header": {"time_casablanca": "lundi *13* _juillet_ `x`"},
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "BAISSIER", "price_vs_ma200_pct": -8.0},
        "macro_context": {"fear_greed": 26, "fear_greed_label": "Peur*"},
        "top_action": {"line": "RENFORCER *ETH* _now_"},
        "thesis_of_the_day": [{"asset": "E*H_", "action": "RENFORCER",
                               "confidence": 70, "observation": "obs"}],
        "active_recommendations_tracking": [
            {"asset": "B_TC", "issued_at": "02/07", "confidence": 85,
             "entry_price": 60000, "current_price": 63822, "progress_pct": 6.2,
             "health_status": "🟢 En bonne voie"}],
        "reco_bilan": [{"asset": "E*H", "action": "RENFORCER", "entry": 100.0,
                        "current": 80.0, "target": 120.0, "delta_pct": -20.0,
                        "status": "invalidated", "reason": "stop *franchi*"}],
        "predictions_scoring": {"detail": [
            {"asset": "T_A_O", "reco": "RENFORCER", "entry_date": "20/06",
             "entry_price": 340, "current_price": 319, "delta_pct": -6.2,
             "status": "in_progress", "score": 0, "confidence": 70}]},
        "weekly_action_plan": [{"action": "Si X alors Y"}],
    }
    for kind in ("morning", "evening", "weekly"):
        d = notify._build_digest(hostile, kind)
        assert d.count("*") % 2 == 0, f"{kind}: gras déséquilibré"
        assert d.count("_") % 2 == 0, f"{kind}: italique déséquilibré"


def test_notify_no_double_ellipsis_period():
    """Une lecture tronquée (« … ») ne reçoit pas de point final (« …. »)."""
    from src.telegram_bot import notify

    p = {
        "header": {"time_casablanca": "x"},
        "predictions_scoring": {"detail": [
            {"asset": "ETH", "reco": "RENFORCER", "entry_date": "28/06",
             "entry_price": 1980, "current_price": 1850, "delta_pct": -6.6,
             "status": "in_progress", "score": 0, "confidence": 78}]},
        "positions_review": [
            {"asset": "ETH", "lt_status": "accumulation",
             "analysis": ("une analyse volontairement interminable qui dépasse "
                          "largement la limite de quatre-vingt-dix caractères "
                          "pour forcer la troncature par clip")}],
        "weekly_action_plan": [{"action": "Si " + "y" * 300}],
    }
    d = notify._build_digest(p, "weekly")
    assert "…." not in d
