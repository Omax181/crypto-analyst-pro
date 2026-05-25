"""Orchestrateur principal de l'agent crypto.

Modes (argument CLI) :
- ``morning``  : collecte complète -> rapport matin -> email.
- ``evening``  : idem + delta journée -> rapport soir -> email.
- ``intraday`` : scan léger spikes/news urgentes -> alerte si déclencheur.

Usage : ``python -m src.main morning`` (lancé par GitHub Actions).

Principe de robustesse : chaque source est encapsulée ; une panne isolée
n'interrompt pas le rapport (les champs indisponibles sont marqués).
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.analytics.composite_score import composite_score
from src.analytics.historical_patterns import relevant_patterns
from src.analytics.narratives import sector_rotation
from src.analytics.patterns import detect_patterns
from src.analytics.project_health import project_health
from src.analytics.technical import evaluate_technical
from src.data_sources import (
    binance,
    coingecko,
    coinmarketcap,
    cryptopanic,
    econ_calendar,
    fear_greed,
    fred,
    geopolitics,
    github_dev,
    onchain_btc,
    onchain_eth,
    reddit,
    tradingview,
    youtube,
)
from src.reporting.content_filter import filter_positions
from src.reporting.email_html import render_alert, render_report
from src.reporting.email_sender import send_email
from src.reporting.volatility_assessor import determine_report_style
from src.utils.logger import get_logger
from src.utils.portfolio_loader import (
    exchange_symbol,
    load_config,
    load_portfolio,
)

logger = get_logger(__name__)

TZ = ZoneInfo("Africa/Casablanca")
_TH = load_config("thresholds")


def _now_str() -> str:
    """Horodatage formaté en heure de Casablanca."""
    return datetime.now(TZ).strftime("%A %d %B %Y · %H:%M") + " Casablanca"


# --------------------------------------------------------------------------- #
# Collecte des données
# --------------------------------------------------------------------------- #
def collect_market(symbols: list[str]) -> dict[str, Any]:
    """Collecte prix/marché + cross-check CMC + global."""
    cg = coingecko.get_market_data(symbols)
    cmc = coinmarketcap.get_quotes(symbols)
    discrepancies = coinmarketcap.cross_check(cg, cmc)
    glob = coingecko.get_global()
    return {"per_symbol": cg, "discrepancies": discrepancies, "global": glob}


def collect_macro() -> dict[str, Any]:
    """Collecte macro (FRED), calendrier éco et géopolitique (Gemini search)."""
    return {
        "fred": fred.get_macro(),
        "calendar": econ_calendar.get_economic_calendar(),
        "geopolitics": geopolitics.get_geopolitics(),
    }


def collect_sentiment(symbols: list[str]) -> dict[str, Any]:
    """Collecte news (CryptoPanic), Reddit et corpus YouTube."""
    news = cryptopanic.get_news(currencies=symbols)
    news_scores = cryptopanic.news_score_by_symbol(news, symbols)
    return {
        "news": news,
        "news_scores": news_scores,
        "reddit": reddit.get_reddit_sentiment(),
        "youtube": youtube.get_youtube_corpus(),
    }


def collect_onchain() -> dict[str, Any]:
    """Collecte on-chain BTC et ETH."""
    return {"btc": onchain_btc.get_btc_onchain(), "eth": onchain_eth.get_eth_onchain()}


def analyze_positions(
    portfolio: dict[str, Any],
    market: dict[str, Any],
    sentiment: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Analyse chaque position : technique, patterns, dev, score, santé.

    Returns:
        Dict ``{symbol: enriched_data}``.
    """
    enriched: dict[str, dict[str, Any]] = {}
    per_symbol = market["per_symbol"]
    onchain_syms = {"BTC", "ETH"}
    reddit_sent = sentiment["reddit"].get("sentiment_score", 0.0)

    for sym, info in portfolio.items():
        if info.get("role") == "cash_reserve":
            continue
        ex_sym = exchange_symbol(sym, info)
        mkt = per_symbol.get(sym, {})
        tech = evaluate_technical(tradingview.get_technical(sym))
        dev = github_dev.get_dev_activity(sym)
        news_score = sentiment["news_scores"].get(sym, 0.0)

        # macro_fit simplifié : neutre par défaut, ajusté par le score technique
        # (l'analyse macro fine est laissée à Gemini avec les données globales).
        macro_fit = 50.0

        score = composite_score(
            technical=tech,
            dev_activity=dev,
            news_score=news_score,
            reddit_sentiment=reddit_sent,
            macro_fit=macro_fit,
            onchain_available=sym in onchain_syms,
        )
        health = project_health(symbol=sym, dev_activity=dev, market=mkt)

        # Patterns seulement pour Tier 1/2 (économie d'appels Binance).
        patterns = (
            detect_patterns(sym) if info["tier"] <= 2 else {"available": False, "patterns": []}
        )

        enriched[sym] = {
            "tier": info["tier"],
            "exchange_symbol": ex_sym,
            "notes": info.get("notes"),
            "value_usd": info.get("value_usd"),
            "change_24h": mkt.get("change_24h"),
            "price": mkt.get("price"),
            "change_from_ath_pct": mkt.get("change_from_ath_pct"),
            "news_score": news_score,
            "technical_signal": tech.get("dominant_signal"),
            "technical_score": tech.get("score"),
            "rsi_divergence": tech.get("divergence"),
            "patterns": patterns.get("patterns", []),
            "dev_activity": dev,
            "composite_score": score,
            "health_verdict": health["verdict"],
            "health_detail": health,
        }
    return enriched


# --------------------------------------------------------------------------- #
# Assemblage du payload pour Gemini
# --------------------------------------------------------------------------- #
def build_data_payload(
    portfolio_data: dict[str, Any],
    market: dict[str, Any],
    macro: dict[str, Any],
    sentiment: dict[str, Any],
    onchain: dict[str, Any],
    enriched: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Construit le dict de données passé à Gemini, avec pré-calculs."""
    portfolio = portfolio_data["portfolio"]
    mentioned = filter_positions(portfolio, enriched)

    fng = fear_greed.get_fear_greed()
    rotation = sector_rotation(market["per_symbol"])

    cal = macro["calendar"]
    high_impact_today = _count_high_impact_today(cal)

    style = determine_report_style(
        {
            "macro_high_impact_today": high_impact_today,
            "positions_moving": len(mentioned),
            "narrative_shift": _detect_narrative_shift(rotation),
            "major_news_count": sentiment["news"].get("count", 0),
        }
    )

    # Contexte pour sélectionner les patterns historiques pertinents.
    hist_ctx = {
        "fear_greed": fng.get("value") if fng.get("available") else None,
        "has_cpi": _calendar_has(cal, ("cpi", "inflation")),
        "has_fomc": _calendar_has(cal, ("fomc", "fed interest")),
        "dxy_up": _dxy_up(macro["fred"]),
    }

    # Détection d'opportunité majeure (déclenche la mention du cash réserve).
    opportunity = (
        (fng.get("value") is not None and fng.get("value") < 20)
        or any(enriched[s]["health_verdict"] == "exit" for s in enriched)
    )

    return {
        "report_style": style["style"],
        "volatility_score": style["score"],
        "fear_greed": fng,
        "market_global": market["global"],
        "price_discrepancies": market["discrepancies"],
        "sector_rotation": rotation,
        "macro": macro["fred"],
        "economic_calendar": cal,
        "geopolitics": macro["geopolitics"],
        "news_global_count": sentiment["news"].get("count", 0),
        "news_items": sentiment["news"].get("items", [])[:15],
        "reddit_sentiment": sentiment["reddit"],
        "youtube_corpus": sentiment["youtube"],
        "onchain": onchain,
        "positions_to_mention": [
            {"symbol": s, **enriched[s]} for s in mentioned
        ],
        "all_positions_summary": {
            s: {
                "tier": enriched[s]["tier"],
                "change_24h": enriched[s]["change_24h"],
                "score": enriched[s]["composite_score"]["total"],
            }
            for s in enriched
        },
        "historical_patterns": relevant_patterns(hist_ctx),
        "opportunity_flag": opportunity,
    }


def _count_high_impact_today(calendar: dict[str, Any]) -> int:
    """Compte les événements high-impact datés d'aujourd'hui."""
    if not calendar.get("available"):
        return 0
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return sum(
        1
        for ev in calendar.get("events", [])
        if ev.get("high_impact") and str(ev.get("date", "")).startswith(today)
    )


def _calendar_has(calendar: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    """Indique si le calendrier contient un événement matchant des mots-clés."""
    for ev in calendar.get("events", []):
        name = str(ev.get("event", "")).lower()
        if any(k in name for k in keywords):
            return True
    return False


def _dxy_up(fred_data: dict[str, Any]) -> bool:
    """Indique si le DXY est en hausse sur la dernière observation."""
    series = fred_data.get("series", {})
    dxy = series.get("dxy", {})
    delta = dxy.get("delta")
    return delta is not None and delta > 0


def _detect_narrative_shift(rotation: dict[str, Any]) -> bool:
    """Heuristique : un secteur surperforme nettement (>5%) un autre."""
    sectors = rotation.get("sectors", {})
    if len(sectors) < 2:
        return False
    changes = [v["avg_change_24h"] for v in sectors.values()]
    return (max(changes) - min(changes)) > 5.0


# --------------------------------------------------------------------------- #
# Points d'entrée par mode
# --------------------------------------------------------------------------- #
def run_report(mode: str) -> int:
    """Exécute un rapport complet (morning ou evening)."""
    from src.ai_brain.decision_engine import DecisionEngine
    import yaml

    logger.info("=== Démarrage rapport '%s' ===", mode)
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]

    market = collect_market(symbols)
    macro = collect_macro()
    sentiment = collect_sentiment(symbols)
    onchain = collect_onchain()
    enriched = analyze_positions(portfolio, market, sentiment)

    data = build_data_payload(portfolio_data, market, macro, sentiment, onchain, enriched)
    portfolio_yaml = yaml.safe_dump(portfolio_data, allow_unicode=True, sort_keys=False)

    engine = DecisionEngine()
    timestamp = _now_str()
    if mode == "morning":
        payload = engine.morning_report(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml
        )
        subject = f"☀️ Veille crypto · matin · {datetime.now(TZ):%d/%m}"
    else:
        payload = engine.evening_report(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml
        )
        subject = f"🌙 Veille crypto · soir · {datetime.now(TZ):%d/%m}"

    html = render_report(payload)
    ok = send_email(subject, html)
    logger.info("=== Rapport '%s' terminé (email %s) ===", mode, "OK" if ok else "ÉCHEC")
    return 0 if ok else 1


def run_intraday() -> int:
    """Scan léger : spikes courts + mots-clés urgents. Alerte si déclencheur."""
    from src.ai_brain.decision_engine import DecisionEngine

    logger.info("=== Scan intra-day ===")
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]

    alerts_cfg = _TH["intraday_alerts"]
    spike_threshold = alerts_cfg["price_spike_threshold_pct"]
    window = alerts_cfg["spike_window_hours"]
    keywords = [k.lower() for k in alerts_cfg["ultra_urgent_keywords"]]

    triggers: list[dict[str, Any]] = []

    # 1) Spikes de prix sur fenêtre courte.
    for sym in symbols:
        change = binance.short_window_change(sym, window_hours=window)
        if change is not None and abs(change) >= spike_threshold:
            triggers.append(
                {
                    "symbol": sym,
                    "type": "price_spike",
                    "detail": f"{change:+.1f}% en {window}h",
                }
            )

    # 2) News urgentes (mots-clés critiques) sur positions détenues.
    news = cryptopanic.get_news(currencies=symbols, limit=40)
    for item in news.get("items", []):
        title = (item.get("title") or "").lower()
        matched = [k for k in keywords if k in title]
        if matched and item.get("currencies"):
            held = [c for c in item["currencies"] if c in symbols]
            if held:
                triggers.append(
                    {
                        "symbol": ", ".join(held),
                        "type": "urgent_news",
                        "detail": f"{item.get('title')} (mots-clés: {', '.join(matched)})",
                    }
                )

    if not triggers:
        logger.info("Aucun déclencheur intra-day. Pas d'alerte.")
        return 0

    logger.info("%d déclencheur(s) intra-day détecté(s).", len(triggers))
    engine = DecisionEngine()
    timestamp = _now_str()
    payload = engine.intraday_alert(timestamp=timestamp, triggers=triggers)
    html = render_alert(payload, timestamp)
    sev_icon = {"info": "ℹ️", "warning": "⚠️", "danger": "🚨"}.get(
        payload.get("severity", "warning"), "⚠️"
    )
    subject = f"{sev_icon} Alerte crypto · {payload.get('title', 'mouvement détecté')}"
    ok = send_email(subject, html)
    return 0 if ok else 1


def main() -> int:
    """Point d'entrée CLI."""
    if len(sys.argv) < 2 or sys.argv[1] not in ("morning", "evening", "intraday"):
        print("Usage : python -m src.main {morning|evening|intraday}")
        return 2
    mode = sys.argv[1]
    try:
        if mode == "intraday":
            return run_intraday()
        return run_report(mode)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec fatal du mode %s : %s", mode, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
