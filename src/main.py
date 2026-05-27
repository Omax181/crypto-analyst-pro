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
    coingecko,
    coinglass,
    cryptopanic,
    defillama,
    econ_calendar,
    etf_flows,
    fear_greed,
    fred,
    github_dev,
    kaito,
    lunarcrush,
    onchain_advanced,
    prediction_markets,
    reddit,
    technical_advanced,
    telegram_reader,
    token_unlocks,
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
    """Construit les 9 signaux d'un actif (OHLCV via CoinGecko, non géo-bloqué)."""
    tech = evaluate_technical(tradingview.get_technical(symbol))
    tech_score = tech.get("score")

    # Technique avancée (Fibonacci, Bollinger, support/résistance) via CoinGecko.
    tech_adv = technical_advanced.get_technical_advanced(symbol)
    boll = (tech_adv.get("bollinger") or {}) if tech_adv.get("available") else {}
    # La position Bollinger module le score technique (bas de bande = signal d'achat).
    if boll.get("available"):
        if boll.get("position") == "lower":
            tech_score = min(100.0, (tech_score or 50) + 12)
        elif boll.get("position") == "upper":
            tech_score = max(0.0, (tech_score or 50) - 12)

    # Anomalie de volume via série journalière CoinGecko.
    vol_score = None
    series = coingecko.get_price_volume_series(symbol, days=30)
    if series and len(series.get("volumes", [])) >= 10:
        vols = series["volumes"]
        avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
        if avg > 0:
            ratio = vols[-1] / avg
            vol_score = max(0.0, min(100.0, 50 + (ratio - 1) * 25))

    # Fondamental : dev GitHub + tendance TVL DeFiLlama.
    dev = github_dev.get_dev_activity(symbol)
    tvl = defillama.get_protocol_tvl(symbol)
    fundamental = fundamental_score_from_signals(
        dev_activity=dev, tvl_trend=tvl.get("tvl_trend_7d") if tvl.get("available") else None
    )

    # Social : LunarCrush (Galaxy Score) en priorité, sinon Reddit.
    social_data = lunarcrush.get_social_metrics(symbol)
    if social_data.get("available") and social_data.get("galaxy_score") is not None:
        social = max(0.0, min(100.0, float(social_data["galaxy_score"])))
        social_active = True
    else:
        social = max(0.0, min(100.0, 50 + reddit_sentiment * 25))
        social_active = bool(reddit_sentiment)

    news_score = max(0.0, min(100.0, 50 + news_24h_count * 8))
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
        "social_sentiment": social if social_active else None,
        "fundamental": fundamental,
        "macro_alignment": None,
    }
    score = composite_score(signals)
    return {
        "signals": signals, "score": score, "technical": tech, "dev": dev,
        "tech_advanced": tech_adv, "tvl": tvl, "social": social_data,
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
    telegram = telegram_reader.get_telegram_news(hours=24)
    defi = defillama.get_defi_tvl()
    narratives = kaito.get_trending_narratives()
    social_trending = lunarcrush.get_trending_coins()
    unlocks = token_unlocks.get_upcoming_unlocks(days_ahead=30)

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
            ta = asset.get("tech_advanced") or {}
            eligible.append({
                "asset": sym, "tier": tier,
                "signals_count": asset["score"]["signals_count"],
                "bullish_count": asset["score"]["bullish_count"],
                "bearish_count": asset["score"]["bearish_count"],
                "composite": asset["score"]["total"],
                "price": asset["price"],
                "change_24h": asset["change_24h"],
                "ath_distance_pct": asset["ath_distance_pct"],
                "technical_signal": asset["technical"].get("dominant_signal"),
                "signals_detail": asset["score"]["components"],
                "fibonacci": ta.get("fibonacci") if ta.get("available") else None,
                "bollinger": ta.get("bollinger") if ta.get("available") else None,
                "support_resistance": ta.get("support_resistance") if ta.get("available") else None,
                "tvl": asset.get("tvl") if asset.get("tvl", {}).get("available") else None,
                "social": asset.get("social") if asset.get("social", {}).get("available") else None,
                "dev_activity": asset.get("dev") if asset.get("dev", {}).get("available") else None,
            })
    eligible.sort(key=lambda e: (e["tier"], -e["signals_count"]))

    tracker = PredictionTracker()
    price_lookup = {s: enriched[s].get("price") for s in enriched}
    active_recos = tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(30)

    active_sources = _active_sources(
        market=market, fng=fng, macro=macro, onchain=onchain, polymarket=polymarket,
        etf=etf, telegram=telegram, defi=defi, narratives=narratives,
        social=social_trending, unlocks=unlocks, news=any(news_counts.values()),
    )

    return {
        "market_global": glob, "fear_greed": fng, "macro": macro,
        "economic_calendar": calendar, "onchain_indicators": onchain,
        "polymarket": polymarket, "etf_flows": etf, "reddit": reddit_data,
        "telegram": telegram, "defi_tvl": defi, "kaito_narratives": narratives,
        "social_trending": social_trending, "token_unlocks": unlocks,
        "sector_rotation": rotation, "news_counts": news_counts,
        "active_sources": active_sources,
        "eligible_theses": eligible[:5], "active_recommendations": active_recos,
        "win_rate": win_rate,
        "all_positions_summary": [
            {"asset": s, "tier": enriched[s]["tier"],
             "change_24h": enriched[s]["change_24h"],
             "composite": enriched[s]["score"]["total"],
             "signals_count": enriched[s]["score"]["signals_count"],
             "ath_distance_pct": enriched[s]["ath_distance_pct"]}
            for s in enriched
        ],
        "blind_spots": _blind_spots(onchain, polymarket, etf, telegram, defi),
    }


def _active_sources(**flags: Any) -> list[str]:
    """Liste lisible des sources réellement actives (anti-fabrication)."""
    out: list[str] = []
    mapping = {
        "market": "CoinGecko", "fng": "Fear&Greed", "macro": "FRED",
        "onchain": "On-chain", "polymarket": "Polymarket", "etf": "ETF flows",
        "telegram": "Telegram", "defi": "DeFiLlama", "narratives": "Kaito",
        "social": "LunarCrush", "unlocks": "Token Unlocks", "news": "News",
    }
    for key, label in mapping.items():
        val = flags.get(key)
        ok = bool(val) if isinstance(val, bool) else bool(val and (
            val.get("available") if isinstance(val, dict) else val))
        if ok:
            out.append(label)
    return out


def _blind_spots(*sources: dict[str, Any]) -> str:
    """Construit la phrase d'angles morts à partir des sources indisponibles."""
    labels = ["on-chain avancé", "Polymarket", "ETF flows", "Telegram", "DeFiLlama"]
    missing = [labels[i] for i, src in enumerate(sources)
               if not (src.get("available") if isinstance(src, dict) else src)]
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
    # Graphiques prix+Bollinger pour les thèses retenues.
    from src.reporting import charts
    chart_imgs = charts.charts_for_theses(payload.get("thesis_of_the_day") or [], limit=4)
    html = _render(payload, "morning", charts=chart_imgs)
    ok = send_email(f"\u2600\ufe0f Veille crypto \u00b7 matin \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Matin: %s (cohérence: %d corr · %d graphiques)",
                ok, len(checked["warnings"]), len(chart_imgs))
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
    btc_1h = coingecko.short_window_change_cg("BTC", hours=1)
    if btc_1h is not None and abs(btc_1h) >= cfg["btc_1h_abs_pct"]:
        triggers.append({"type": "btc_move", "detail": f"BTC {btc_1h:+.1f}% en 1h"})
    for sym in symbols:
        ch = coingecko.short_window_change_cg(sym, hours=1)
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


def _render(payload: dict[str, Any], kind: str, charts: dict[str, str] | None = None) -> str:
    """Rend le HTML du rapport selon son type."""
    from src.reporting import email_html
    return email_html.render(payload, kind, charts=charts)


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
