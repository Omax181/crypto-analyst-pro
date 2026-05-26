"""Orchestrateur principal V2 de l'agent crypto.

Modes (argument CLI) :
- ``morning``      : collecte complète -> rapport matin -> email + state.
- ``evening``      : différentiel depuis le matin -> rapport soir -> email + state.
- ``weekly``       : bilan semaine + scoring -> rapport hebdo -> email + state.
- ``panic_check``  : scan flash (BTC ±15%/1h, hack, -25% token) -> alerte si besoin.

Robustesse : chaque source est isolée ; une panne n'interrompt pas le rapport.
Cohérence : la mémoire (state/) relie matin/soir/hebdo ; le tracking calcule le
win rate ; le coherence_checker valide le JSON avant envoi.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.analytics.coherence_checker import check_report
from src.analytics.composite_score import composite_score
from src.analytics.fundamentals import compute_ath_distance, fundamental_score_from_signals
from src.analytics.narratives import sector_rotation
from src.analytics.technical import evaluate_technical
from src.analytics.tier_resolver import min_signals_for_firm_reco, resolve_tier
from src.data_sources import (
    binance,
    coingecko,
    coinglass,
    cryptopanic,
    econ_calendar,
    etf_flows,
    fear_greed,
    fred,
    github_dev,
    onchain_advanced,
    prediction_markets,
    reddit,
    telegram_channels,
    tradingview,
)
from src.reporting.email_sender import send_email
from src.state import report_memory as mem
from src.tracking.prediction_scoring import PredictionTracker
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_portfolio

logger = get_logger(__name__)

TZ = ZoneInfo("Africa/Casablanca")
_TIER0 = {"BTC", "ETH"}


def _now_str() -> str:
    """Horodatage formaté en heure de Casablanca."""
    return datetime.now(TZ).strftime("%A %d %B %Y · %H:%M") + " Casablanca"


def _next_report_label(mode: str) -> str:
    """Libellé du prochain rapport pour le footer."""
    return {
        "morning": "ce soir ~19h30",
        "evening": "demain matin ~08h30",
        "weekly": "demain matin ~08h30",
    }.get(mode, "prochain créneau")


def _build_asset_signals(
    symbol: str, market: dict[str, Any], reddit_sentiment: float,
    news_24h_count: int, sector_change: float | None, derivatives: dict[str, Any],
) -> dict[str, Any]:
    """Construit les 9 signaux d'un actif pour le score composite."""
    tech = evaluate_technical(tradingview.get_technical(symbol))
    tech_score = tech.get("score")

    vol_score = None
    klines = binance.get_klines(symbol, interval="4h", limit=31)
    if klines and len(klines) >= 10:
        vols = [k["volume"] for k in klines]
        avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
        if avg > 0:
            ratio = vols[-1] / avg
            vol_score = max(0.0, min(100.0, 50 + (ratio - 1) * 25))

    dev = github_dev.get_dev_activity(symbol)
    fundamental = fundamental_score_from_signals(dev_activity=dev)
    news_score = max(0.0, min(100.0, 50 + news_24h_count * 8))
    social = max(0.0, min(100.0, 50 + reddit_sentiment * 25))
    sector_score = (
        max(0.0, min(100.0, 50 + sector_change * 2)) if sector_change is not None else None
    )
    deriv_score = None
    if derivatives.get("available") and derivatives.get("funding_rate") is not None:
        fr = derivatives["funding_rate"]
        deriv_score = max(0.0, min(100.0, 50 - fr * 1000))

    signals = {
        "technical_multi_tf": tech_score,
        "volume_anomaly": vol_score,
        "onchain_flows": 55.0 if symbol in _TIER0 else None,
        "derivatives": deriv_score,
        "sector_rotation": sector_score,
        "news_24h": news_score if news_24h_count else None,
        "social_sentiment": social if reddit_sentiment else None,
        "fundamental": fundamental,
        "macro_alignment": None,
    }
    score = composite_score(signals)
    return {
        "signals": signals, "score": score, "technical": tech, "dev": dev,
        "derivatives": derivatives, "price": market.get("price"),
        "change_24h": market.get("change_24h"),
        "ath_distance_pct": compute_ath_distance(
            market.get("price") or 0, market.get("ath") or 0
        ),
    }


def _collect_morning_data(portfolio_data: dict[str, Any]) -> dict[str, Any]:
    """Collecte et assemble toutes les données pour le rapport du matin."""
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]

    market = coingecko.get_market_data(symbols)
    glob = coingecko.get_global()
    fng = fear_greed.get_fear_greed()
    macro = fred.get_macro()
    calendar = econ_calendar.get_economic_calendar()
    onchain = onchain_advanced.get_onchain_indicators()
    polymarket = prediction_markets.get_fed_cut_probabilities()
    etf = etf_flows.get_etf_flows()
    reddit_data = reddit.get_reddit_sentiment()
    reddit_sent = reddit_data.get("sentiment_score", 0.0)
    rotation = sector_rotation(market)

    news_counts = {s: len(cryptopanic.get_recent_news(s, hours=24)) for s in symbols}
    telegram = telegram_channels.get_telegram_messages()

    enriched: dict[str, dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    sectors = rotation.get("sectors", {})
    for sym in symbols:
        info = portfolio[sym]
        tier = resolve_tier(sym, info.get("value_usd"))
        sector_change = None
        for sec_data in sectors.values():
            if sym in sec_data.get("members", []):
                sector_change = sec_data.get("avg_change_24h")
                break
        derivatives = coinglass.get_derivatives(sym) if tier <= 1 else {"available": False}
        asset = _build_asset_signals(
            sym, market.get(sym, {}), reddit_sent, news_counts.get(sym, 0),
            sector_change, derivatives,
        )
        asset["tier"] = tier
        asset["value_usd"] = info.get("value_usd")
        enriched[sym] = asset
        needed = min_signals_for_firm_reco(tier)
        if needed < 999 and asset["score"]["signals_count"] >= needed:
            eligible.append({
                "asset": sym, "tier": tier,
                "signals_count": asset["score"]["signals_count"],
                "composite": asset["score"]["total"],
                "change_24h": asset["change_24h"],
                "technical_signal": asset["technical"].get("dominant_signal"),
            })
    eligible.sort(key=lambda e: (e["tier"], -e["signals_count"]))

    tracker = PredictionTracker()
    price_lookup = {s: enriched[s].get("price") for s in enriched}
    active_recos = tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(30)

    return {
        "market_global": glob, "fear_greed": fng, "macro": macro,
        "economic_calendar": calendar, "onchain_indicators": onchain,
        "polymarket": polymarket, "etf_flows": etf, "reddit": reddit_data,
        "telegram": telegram, "sector_rotation": rotation, "news_counts": news_counts,
        "eligible_theses": eligible[:5], "active_recommendations": active_recos,
        "win_rate": win_rate,
        "all_positions_summary": [
            {"asset": s, "tier": enriched[s]["tier"],
             "change_24h": enriched[s]["change_24h"],
             "composite": enriched[s]["score"]["total"],
             "ath_distance_pct": enriched[s]["ath_distance_pct"]}
            for s in enriched
        ],
        "blind_spots": _blind_spots(onchain, polymarket, etf, telegram),
    }


def _blind_spots(*sources: dict[str, Any]) -> str:
    """Construit la phrase d'angles morts à partir des sources indisponibles."""
    labels = ["on-chain avancé", "Polymarket", "ETF flows", "Telegram"]
    missing = [labels[i] for i, src in enumerate(sources) if not src.get("available")]
    base = "Arkham non actif · Bloomberg/Reuters non accessibles"
    return base + (" · indisponibles : " + ", ".join(missing) if missing else "")


def run_morning() -> int:
    """Génère et envoie le rapport du matin."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT MATIN ===")
    portfolio_data = load_portfolio()
    data = _collect_morning_data(portfolio_data)
    evening_state = mem.load_evening_report()
    engine = DecisionEngine()
    payload = engine.generate_morning(
        timestamp=_now_str(), data=data, portfolio_data=portfolio_data,
        evening_state=evening_state,
    )
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("morning")
    mem.save_morning_report(payload)
    html = _render(payload, "morning")
    ok = send_email(f"\u2600\ufe0f Veille crypto \u00b7 matin \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Matin: %s (alertes cohérence: %d)", ok, len(checked["warnings"]))
    return 0 if ok else 1


def run_evening() -> int:
    """Génère et envoie le rapport du soir (différentiel)."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT SOIR ===")
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]
    market = coingecko.get_market_data(symbols)
    fng = fear_greed.get_fear_greed()
    etf = etf_flows.get_etf_flows()
    news_global = cryptopanic.get_recent_news(None, hours=12)
    morning_state = mem.load_morning_report()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    active = tracker.refresh_active(price_lookup)
    data = {
        "prices_now": price_lookup,
        "changes_24h": {s: market.get(s, {}).get("change_24h") for s in symbols},
        "fear_greed": fng, "etf_flows": etf, "news_12h": news_global[:8],
        "active_recommendations": active,
    }
    engine = DecisionEngine()
    payload = engine.generate_evening(
        timestamp=_now_str(), data=data, morning_state=morning_state,
    )
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("evening")
    mem.save_evening_report(payload)
    html = _render(payload, "evening")
    ok = send_email(f"\U0001f319 Veille crypto \u00b7 soir \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Soir: %s", ok)
    return 0 if ok else 1


def run_weekly() -> int:
    """Génère et envoie le rapport hebdomadaire."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT HEBDO ===")
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]
    market = coingecko.get_market_data(symbols)
    calendar = econ_calendar.get_economic_calendar(days_ahead=7)
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(7)
    lesson = tracker.extract_lesson(7)
    dust = [{"asset": s, "value_usd": portfolio[s].get("value_usd")}
            for s in symbols if (portfolio[s].get("value_usd") or 0) < 5]
    data = {"win_rate": win_rate, "lesson": lesson, "economic_calendar": calendar,
            "dust_positions": dust, "prices_now": price_lookup}
    week_state = {"last_morning": mem.load_morning_report(),
                  "last_evening": mem.load_evening_report()}
    engine = DecisionEngine()
    payload = engine.generate_weekly(timestamp=_now_str(), data=data, week_state=week_state)
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("weekly")
    mem.save_weekly_report(payload)
    html = _render(payload, "weekly")
    ok = send_email(f"\U0001f4ca Bilan hebdo crypto \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Hebdo: %s", ok)
    return 0 if ok else 1


def run_panic_check() -> int:
    """Scan flash : déclenche une alerte uniquement en cas d'événement majeur."""
    from src.ai_brain.decision_engine import DecisionEngine
    from src.utils.portfolio_loader import load_config
    cfg = load_config("thresholds")["panic_mode"]
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]
    triggers: list[dict[str, Any]] = []
    btc_1h = binance.short_window_change("BTC", window_hours=1)
    if btc_1h is not None and abs(btc_1h) >= cfg["btc_1h_abs_pct"]:
        triggers.append({"type": "btc_move", "detail": f"BTC {btc_1h:+.1f}% en 1h"})
    for sym in symbols:
        ch = binance.short_window_change(sym, window_hours=1)
        if ch is not None and ch <= cfg["asset_1h_drop_pct"]:
            triggers.append({"type": "asset_crash", "detail": f"{sym} {ch:.0f}% en 1h"})
    hack = cryptopanic.check_keywords_recent(cfg["hack_keywords"], hours=1, symbols=symbols)
    if hack:
        triggers.append({"type": "hack", "detail": hack.get("title", "alerte sécurité")})
    if not triggers:
        logger.info("Panic check : RAS.")
        return 0
    last = mem.load_last_panic()
    if last.get("sent_at"):
        from datetime import timezone as _tz, timedelta as _td
        try:
            sent = datetime.fromisoformat(last["sent_at"])
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=_tz.utc)
            if datetime.now(_tz.utc) - sent < _td(minutes=cfg["anti_spam_minutes"]):
                logger.info("Panic anti-spam : email récent.")
                return 0
        except ValueError:
            pass
    engine = DecisionEngine()
    payload = engine.generate_panic(timestamp=_now_str(), triggers=triggers)
    html = _render(payload, "panic")
    sev = {"info": "\u2139\ufe0f", "warning": "\u26a0\ufe0f", "danger": "\U0001f6a8"}.get(
        payload.get("severity", "warning"), "\u26a0\ufe0f")
    ok = send_email(f"{sev} PANIC \u00b7 {payload.get('title', 'mouvement majeur')}", html)
    if ok:
        mem.mark_panic_sent([t["detail"] for t in triggers])
    return 0 if ok else 1


def _render(payload: dict[str, Any], kind: str) -> str:
    """Rend le HTML du rapport selon son type."""
    from src.reporting import email_html
    return email_html.render(payload, kind)


def main() -> int:
    """Point d'entrée CLI."""
    modes = {"morning", "evening", "weekly", "panic_check"}
    if len(sys.argv) < 2 or sys.argv[1] not in modes:
        print("Usage : python -m src.main {morning|evening|weekly|panic_check}")
        return 2
    mode = sys.argv[1]
    try:
        return {"morning": run_morning, "evening": run_evening,
                "weekly": run_weekly, "panic_check": run_panic_check}[mode]()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec fatal mode %s : %s", mode, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
