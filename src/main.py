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
    coinmarketcap,
    binance_futures,
    boursorama_calendar,
    coinglass,
    newsapi,
    defillama,
    econ_calendar,
    etf_flows,
    fear_greed,
    fred,
    geopolitics,
    github_dev,
    kaito,
    lunarcrush,
    onchain_advanced,
    onchain_btc,
    prediction_markets,
    reddit,
    stablecoins,
    technical_advanced,
    telegram_reader,
    token_unlocks,
    tradingview,
    whale_tracker,
    youtube,
)
from src.analytics.correlation import compute_correlation_analysis
from src.reporting.email_sender import send_email
from src.state import report_memory as mem
from src.tracking.prediction_scoring import PredictionTracker
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_portfolio

logger = get_logger(__name__)

TZ = ZoneInfo("Africa/Casablanca")
_TIER0 = {"BTC", "ETH"}

_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]


def _fr_date(dt: datetime, with_time: bool = True) -> str:
    """Formate une date en français sans dépendre de la locale système.

    Ex. ``mardi 26 mai 2026 · 08:30``. GitHub Actions n'a pas forcément la
    locale fr_FR installée, donc on mappe manuellement jours et mois.
    """
    jour = _JOURS_FR[dt.weekday()]
    mois = _MOIS_FR[dt.month - 1]
    base = f"{jour} {dt.day} {mois} {dt.year}"
    if with_time:
        base += f" · {dt:%H:%M}"
    return base


def _now_str() -> str:
    """Horodatage formaté en heure de Casablanca (français)."""
    return _fr_date(datetime.now(TZ)) + " Casablanca"


def _next_report_label(mode: str) -> str:
    """Libellé du prochain rapport pour le footer (heure Casablanca)."""
    return {
        "morning": "ce soir 20h00",
        "evening": "demain matin 08h30",
        "weekly": "demain matin 08h30",
    }.get(mode, "prochain créneau")


def _build_asset_signals(
    symbol: str, tier: int, market: dict[str, Any], reddit_sentiment: float,
    news_24h_count: int, sector_change: float | None, derivatives: dict[str, Any],
) -> dict[str, Any]:
    """Construit les 9 signaux d'un actif (OHLCV via CoinGecko, non géo-bloqué).

    Filtrage économique des appels OHLC :
    - Tier 0/1 : technique avancée + volume systématiques
    - Tier 2 : seulement si mouvement 24h significatif (>= 5%)
    - Tier 3/4 : pas d'OHLC (signal technique léger via TradingView seulement)
    """
    tech = evaluate_technical(tradingview.get_technical(symbol))
    tech_score = tech.get("score")

    change_24h = abs(market.get("change_24h") or 0)
    do_deep_ohlc = tier <= 1 or (tier == 2 and change_24h >= 5.0)

    # Technique avancée (Fibonacci, Bollinger) : uniquement tokens éligibles.
    tech_adv: dict[str, Any] = {"available": False}
    if do_deep_ohlc:
        tech_adv = technical_advanced.get_technical_advanced(symbol)
        boll = (tech_adv.get("bollinger") or {}) if tech_adv.get("available") else {}
        if boll.get("available"):
            if boll.get("position") == "lower":
                tech_score = min(100.0, (tech_score or 50) + 12)
            elif boll.get("position") == "upper":
                tech_score = max(0.0, (tech_score or 50) - 12)

    # Anomalie de volume : même règle que technique avancée (1 appel CoinGecko de plus).
    vol_score = None
    price_series_30d: list[float] = []
    if do_deep_ohlc:
        series = coingecko.get_price_volume_series(symbol, days=30)
        if series and len(series.get("volumes", [])) >= 10:
            vols = series["volumes"]
            avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
            if avg > 0:
                ratio = vols[-1] / avg
                vol_score = max(0.0, min(100.0, 50 + (ratio - 1) * 25))
        # On garde la série de prix pour l'analyse de corrélation (réutilisée,
        # aucun appel API supplémentaire).
        if series and series.get("prices"):
            price_series_30d = series["prices"]

    # Fondamental : dev GitHub + tendance TVL DeFiLlama.
    dev = github_dev.get_dev_activity(symbol)
    tvl = defillama.get_protocol_tvl(symbol)
    fundamental = fundamental_score_from_signals(
        dev_activity=dev, tvl_trend=tvl.get("tvl_trend_7d") if tvl.get("available") else None
    )

    # Social : LunarCrush par-token UNIQUEMENT pour Tier 0 (BTC/ETH) — le free
    # tier renvoie 402/429 sur tous les autres. Pour le reste, Reddit + trending
    # global (calculé une seule fois côté collecte).
    social_data: dict[str, Any] = {"available": False}
    if tier == 0:
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
        "change_7d": market.get("change_7d"),
        "price_series_30d": price_series_30d,
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
    # Cross-check des prix BTC/ETH via CoinMarketCap (détecte une donnée
    # CoinGecko aberrante : écart > 2%). Dégradation gracieuse si clé absente.
    price_discrepancies = {}
    try:
        cmc_quotes = coinmarketcap.get_quotes(["BTC", "ETH"])
        if cmc_quotes:
            price_discrepancies = coinmarketcap.cross_check(
                {s: market.get(s, {}) for s in ("BTC", "ETH")}, cmc_quotes
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("Cross-check CMC ignoré : %s", exc)
    fng = fear_greed.get_fear_greed()
    macro = fred.get_macro()
    calendar = econ_calendar.get_economic_calendar()
    onchain = onchain_advanced.get_onchain_indicators()
    polymarket = prediction_markets.get_fed_cut_probabilities()
    etf = etf_flows.get_etf_flows()
    reddit_data = reddit.get_reddit_sentiment()
    reddit_sent = reddit_data.get("sentiment_score", 0.0)
    rotation = sector_rotation(market)

    # NewsAPI : UN SEUL appel global (free tier limité à 100 req/jour) puis
    # filtrage Python par symbole/hint. Évite 37 appels par run.
    news_global_items = newsapi.get_recent_news(None, hours=24)
    macro_news_items = newsapi.get_macro_news(hours=24)
    news_counts: dict[str, int] = {}
    for s in symbols:
        hint = (newsapi._QUERY_HINTS.get(s) or "").lower()
        sl = s.lower()
        count = 0
        for item in news_global_items:
            title = (item.get("title") or "").lower()
            if sl in title or (hint and hint in title):
                count += 1
        news_counts[s] = count
    telegram = telegram_reader.get_telegram_news(hours=24)
    defi = defillama.get_defi_tvl()
    narratives = kaito.get_trending_narratives()
    social_trending = lunarcrush.get_trending_coins()
    unlocks = token_unlocks.get_upcoming_unlocks(days_ahead=30)
    youtube_corpus = youtube.get_youtube_corpus()
    geopol = geopolitics.get_geopolitics()
    # V6 : santé réseau BTC (hashrate/difficulty), flux stablecoins, whale tracking.
    btc_network = onchain_btc.get_btc_onchain()
    stablecoin_supply = stablecoins.get_stablecoin_supply()
    whale_inflows = whale_tracker.get_exchange_inflows()
    boursorama_cal = boursorama_calendar.get_boursorama_calendar()

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
        # Dérivés : Binance Futures (API publique gratuite) en primaire.
        # Coinglass seulement si tier payant activé (COINGLASS_PAID=1).
        if tier <= 1:
            derivatives = binance_futures.get_derivatives(sym)
            if not derivatives.get("available"):
                cg = coinglass.get_derivatives(sym)
                if cg.get("available"):
                    derivatives = cg
        else:
            derivatives = {"available": False}
        asset = _build_asset_signals(
            sym, tier, market.get(sym, {}), reddit_sent, news_counts.get(sym, 0),
            sector_change, derivatives,
        )
        asset["tier"] = tier
        asset["value_usd"] = info.get("value_usd")
        enriched[sym] = asset
        needed = min_signals_for_firm_reco(tier)
        sig_count = asset["score"]["signals_count"]
        change = abs(asset.get("change_24h") or 0)
        # Éligibilité standard : signaux >= seuil du tier.
        is_eligible = needed < 999 and sig_count >= needed
        # Priorité grandes cryptos (Tier 0-1, long terme) : on abaisse le seuil
        # si au moins 2 signaux convergents ET mouvement notable (>3%), pour ne
        # jamais rater une thèse importante sur BTC/ETH/Tier 1.
        if not is_eligible and tier <= 1 and sig_count >= 2 and change >= 3.0:
            is_eligible = True
        if is_eligible:
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

    # V6 : analyse de corrélation entre positions (réutilise les séries de prix
    # déjà récupérées, aucun appel API supplémentaire).
    price_series = {
        s: a.get("price_series_30d")
        for s, a in enriched.items()
        if a.get("price_series_30d")
    }
    position_values = {s: (a.get("value_usd") or 0) for s, a in enriched.items()}
    correlation = compute_correlation_analysis(price_series, position_values)

    tracker = PredictionTracker()
    price_lookup = {s: enriched[s].get("price") for s in enriched}
    active_recos = tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(30)
    reco_changes = mem.recent_reco_changes(7)

    active_sources = _active_sources(
        market=market, fng=fng, macro=macro, onchain=onchain, polymarket=polymarket,
        etf=etf, telegram=telegram, defi=defi, narratives=narratives,
        social=social_trending, unlocks=unlocks, news=bool(news_global_items),
        youtube=youtube_corpus, geopolitics=geopol,
        btc_network=btc_network, stablecoins=stablecoin_supply, whales=whale_inflows,
        macro_news=bool(macro_news_items), macro_calendar=boursorama_cal,
    )

    # Enregistre la santé des sources (alimente le bilan hebdo des angles morts).
    _ALL_SOURCES = ["CoinGecko", "Fear&Greed", "FRED", "On-chain", "Polymarket",
                    "ETF flows", "Telegram", "DeFiLlama", "Kaito", "LunarCrush",
                    "Token Unlocks", "News", "YouTube", "Géopolitique", "BTC Network",
                    "Stablecoins", "Whale Tracking", "Reuters/Bloomberg", "Calendrier macro"]
    try:
        mem.record_source_health(_ALL_SOURCES, active_sources)
    except Exception as exc:  # noqa: BLE001
        logger.info("record_source_health ignoré : %s", exc)

    # Portfolio snapshot calculé côté Python (Gemini n'a pas à l'inventer).
    snapshot = _portfolio_snapshot(portfolio, enriched)
    # Macro context : valeurs chiffrées injectées directement.
    macro_context = _macro_context(market, fng, macro, polymarket)

    return {
        "header_meta": {
            "active_sources_count": len(active_sources),
            "active_sources": active_sources,
            "price_discrepancies": price_discrepancies,
            "win_rate_30d_pct": win_rate.get("win_rate_pct"),
            "win_rate_count": f"{win_rate.get('validated', 0)}/{win_rate.get('total', 0)}",
        },
        "portfolio_snapshot": snapshot,
        "macro_context": macro_context,
        "market_global": glob, "fear_greed": fng, "macro": macro,
        "economic_calendar": calendar, "onchain_indicators": onchain,
        "polymarket": polymarket, "etf_flows": etf, "reddit": reddit_data,
        "telegram": telegram, "defi_tvl": defi, "kaito_narratives": narratives,
        "social_trending": social_trending, "token_unlocks": unlocks,
        "sector_rotation": rotation, "news_counts": news_counts,
        "news_24h_global": news_global_items[:12],
        "macro_news": macro_news_items[:10],
        "boursorama_calendar": boursorama_cal,
        "youtube_corpus": youtube_corpus, "geopolitics": geopol,
        "btc_network": btc_network, "stablecoin_supply": stablecoin_supply,
        "whale_inflows": whale_inflows, "position_correlation": correlation,
        "active_sources": active_sources,
        "eligible_theses": eligible, "active_recommendations": active_recos,
        "reco_changes": reco_changes,
        "win_rate": win_rate,
        "all_positions_summary": _positions_summary(enriched, active_recos),
        "portfolio_heatmap": _portfolio_heatmap(enriched),
        "blind_spots": _blind_spots(onchain, polymarket, etf, telegram, defi),
    }


def _positions_summary(
    enriched: dict[str, dict[str, Any]], active_recos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Construit le récap de toutes les positions avec commentaire factuel.

    Le commentaire est généré de façon déterministe (pas par Gemini) à partir
    des signaux calculés, pour rester fiable. action_active est repris des
    recommandations actives en cours.

    Args:
        enriched: dict ``{symbol: {tier, change_24h, score, ...}}``.
        active_recos: liste des recommandations actives en cours.

    Returns:
        Liste de dicts ``{asset, tier, change_24h, comment, action_active}``.
    """
    # Map symbole -> action active (RENFORCER / ALLÉGER / SORTIR).
    action_by_asset: dict[str, str] = {}
    for r in active_recos or []:
        asset = r.get("asset")
        action = (r.get("action") or "").upper()
        if asset and action:
            for kw in ("RENFORC", "ALLÉG", "ALLEG", "SORT", "VENT"):
                if kw in action:
                    action_by_asset[asset] = action.split()[0]
                    break

    out: list[dict[str, Any]] = []
    for s in enriched:
        e = enriched[s]
        score = e.get("score", {})
        sig = score.get("signals_count", 0)
        bull = score.get("bullish_count", 0)
        bear = score.get("bearish_count", 0)
        change = e.get("change_24h")
        tier = e.get("tier")
        # Commentaire factuel court, fondé sur les signaux réels.
        if sig == 0:
            comment = "RAS · pas de signal notable"
        elif bull > bear:
            comment = f"{sig} signaux, biais haussier ({bull}↑/{bear}↓)"
        elif bear > bull:
            comment = f"{sig} signaux, biais baissier ({bull}↑/{bear}↓)"
        else:
            comment = f"{sig} signaux, neutre ({bull}↑/{bear}↓)"
        ath = e.get("ath_distance_pct")
        if ath is not None and ath <= -70:
            comment += f" · {ath:.0f}% sous ATH"
        out.append({
            "asset": s,
            "tier": tier,
            "change_24h": change,
            "comment": comment,
            "action_active": action_by_asset.get(s),
        })
    # Tri : actions actives d'abord, puis par tier, puis par |variation|.
    out.sort(key=lambda p: (
        p["action_active"] is None,
        p["tier"] if p["tier"] is not None else 9,
        -abs(p["change_24h"] or 0),
    ))
    return out


def _portfolio_heatmap(enriched: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Construit une heatmap simple des positions : symbole, valeur, perf 24h.

    Triée par valeur décroissante (les plus grosses positions d'abord). Le
    template colore chaque case selon la perf 24h (vert/rouge). Données 100%
    factuelles (aucune intervention Gemini).

    Returns:
        Liste ``[{symbol, value_usd, change_24h}]`` triée par valeur.
    """
    cells: list[dict[str, Any]] = []
    for sym, e in enriched.items():
        val = e.get("value_usd")
        if val is None or val <= 0:
            continue
        cells.append(
            {
                "symbol": sym,
                "value_usd": round(val, 2),
                "change_24h": e.get("change_24h"),
            }
        )
    cells.sort(key=lambda c: c["value_usd"], reverse=True)
    return cells


def _portfolio_snapshot(
    portfolio: dict[str, Any], enriched: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Calcule la valeur totale et les variations 24h/7j du portefeuille.

    Inclut la performance vs BTC sur 7j (PTF outperform/underperform), le %
    de cash USDC et le drawdown moyen pondéré vs ATH.
    """
    total = 0.0
    delta_24h = 0.0
    delta_7d = 0.0
    usdc_value = 0.0
    drawdown_sum_weighted = 0.0
    counted = 0
    has_7d = False
    for sym, info in portfolio.items():
        v = info.get("value_usd") or 0
        total += v
        if info.get("role") == "cash_reserve":
            usdc_value += v
            continue
        a = enriched.get(sym) or {}
        ch24 = a.get("change_24h")
        if ch24 is not None and v:
            delta_24h += v * (ch24 / 100.0)
        ch7d = a.get("change_7d")
        if ch7d is not None and v:
            delta_7d += v * (ch7d / 100.0)
            has_7d = True
        ath_d = a.get("ath_distance_pct")
        if ath_d is not None and v:
            drawdown_sum_weighted += v * ath_d
            counted += v
    change_7d_pct = round((delta_7d / total) * 100, 2) if (total and has_7d) else None
    btc_7d = (enriched.get("BTC") or {}).get("change_7d")
    vs_btc_7d = round(change_7d_pct - btc_7d, 2) if (change_7d_pct is not None and btc_7d is not None) else None
    return {
        "value_usd": round(total, 2),
        "change_24h_pct": round((delta_24h / total) * 100, 2) if total else None,
        "change_7d_pct": change_7d_pct,
        "vs_btc_7d_pct": vs_btc_7d,
        "drawdown_ath_pct": round(drawdown_sum_weighted / counted, 1) if counted else None,
        "usdc_pct": round((usdc_value / total) * 100, 1) if total else None,
        "usdc_usd": round(usdc_value, 2),
    }


def _fred_value(macro: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Extrait {value, delta, date} d'une série FRED par nom (tolérant casse/alias).

    Renvoie un dict avec ``value`` (float ou None), ``delta`` (variation vs
    observation précédente) et ``date``. Gère les valeurs string de FRED.
    """
    series = macro.get("series") or {}
    obs = None
    for k in keys:
        obs = series.get(k) or series.get(k.upper()) or series.get(k.lower())
        if obs:
            break
    if isinstance(obs, list) and obs:
        obs = obs[-1]
    if not isinstance(obs, dict):
        return {"value": None, "delta": None, "date": None}
    val = obs.get("value")
    if isinstance(val, str):
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = None
    delta = obs.get("delta")
    return {
        "value": round(val, 4) if isinstance(val, (int, float)) else None,
        "delta": round(delta, 4) if isinstance(delta, (int, float)) else None,
        "date": obs.get("date"),
    }


def _macro_context(
    market: dict[str, Any], fng: dict[str, Any], macro: dict[str, Any],
    polymarket: dict[str, Any],
) -> dict[str, Any]:
    """Agrège les chiffres macro pour l'en-tête et l'analyse.

    V6 : expose, en plus de BTC/F&G/DXY/Fed cut, les actifs macro hors-crypto
    (Gold, S&P 500, Nasdaq, Brent, WTI, EUR/USD, USD/JPY) et les taux/volatilité
    (VIX, US 10Y, US 2Y, yield curve 10Y-2Y) — tous issus de FRED. Gemini les
    reçoit chiffrés et le template peut les afficher dans un contexte élargi.
    """
    btc_price = (market.get("BTC") or {}).get("price")
    fng_val = fng.get("value") if fng.get("available") else None

    dxy = _fred_value(macro, "dxy", "DXY")
    vix = _fred_value(macro, "vix")
    us_10y = _fred_value(macro, "us_10y")
    us_2y = _fred_value(macro, "us_2y")
    yield_curve = _fred_value(macro, "yield_curve")
    gold = _fred_value(macro, "gold")
    sp500 = _fred_value(macro, "sp500")
    nasdaq = _fred_value(macro, "nasdaq")
    brent = _fred_value(macro, "brent")
    wti = _fred_value(macro, "wti")
    eur_usd = _fred_value(macro, "eur_usd")
    usd_jpy = _fred_value(macro, "usd_jpy")

    fed_cut = None
    if polymarket.get("available"):
        for m in polymarket.get("markets", []):
            q = (m.get("question") or "").lower()
            if "rate cut" in q or "fed" in q:
                fed_cut = m.get("probability_pct")
                break

    return {
        # Cœur (header principal, comme V5).
        "btc_price": round(btc_price, 2) if btc_price else None,
        "fear_greed": fng_val,
        "dxy": dxy["value"],
        "dxy_delta": dxy["delta"],
        "polymarket_fed_cut_pct": fed_cut,
        # Taux & volatilité (V6).
        "vix": vix["value"],
        "us_10y": us_10y["value"],
        "us_2y": us_2y["value"],
        "yield_curve_10y_2y": yield_curve["value"],
        # Actifs macro hors-crypto (V6).
        "gold_usd": gold["value"],
        "gold_delta": gold["delta"],
        "sp500": sp500["value"],
        "sp500_delta": sp500["delta"],
        "nasdaq": nasdaq["value"],
        "nasdaq_delta": nasdaq["delta"],
        "brent_usd": brent["value"],
        "wti_usd": wti["value"],
        "eur_usd": eur_usd["value"],
        "usd_jpy": usd_jpy["value"],
    }


def _active_sources(**flags: Any) -> list[str]:
    """Liste lisible des sources réellement actives (anti-fabrication)."""
    out: list[str] = []
    mapping = {
        "market": "CoinGecko", "fng": "Fear&Greed", "macro": "FRED",
        "onchain": "On-chain", "polymarket": "Polymarket", "etf": "ETF flows",
        "telegram": "Telegram", "defi": "DeFiLlama", "narratives": "Kaito",
        "social": "LunarCrush", "unlocks": "Token Unlocks", "news": "News",
        "youtube": "YouTube", "geopolitics": "Géopolitique",
        "btc_network": "BTC Network", "stablecoins": "Stablecoins", "whales": "Whale Tracking",
        "macro_news": "Reuters/Bloomberg", "macro_calendar": "Calendrier macro",
    }
    for key, label in mapping.items():
        if _is_truly_active(flags.get(key)):
            out.append(label)
    return out


def _is_truly_active(val: Any) -> bool:
    """Une source est ACTIVE seulement si elle a renvoyé un contenu non-vide."""
    if isinstance(val, bool):
        return val
    if isinstance(val, dict):
        if not val.get("available"):
            return False
        meaningful = {
            k: v for k, v in val.items()
            if k not in ("available", "reason", "source") and v not in (None, [], {}, "")
        }
        return bool(meaningful)
    if isinstance(val, list):
        return len(val) > 0
    return bool(val)


def _blind_spots(*sources: dict[str, Any]) -> str:
    """Construit la phrase d'angles morts à partir des sources indisponibles."""
    labels = ["on-chain avancé", "Polymarket", "ETF flows", "Telegram", "DeFiLlama"]
    missing = [labels[i] for i, src in enumerate(sources)
               if not (src.get("available") if isinstance(src, dict) else src)]
    base = "Arkham non actif · Bloomberg/Reuters non accessibles"
    return base + (" · indisponibles : " + ", ".join(missing) if missing else "")


def _merge_python_facts(payload: dict[str, Any], data: dict[str, Any], timestamp: str) -> dict[str, Any]:
    """Injecte les valeurs Python calculées dans le payload (priorité aux faits).

    Gemini peut inventer ou répondre "données indisponibles" alors qu'on a la
    donnée côté Python. On écrase ces champs avec les chiffres vérifiés.
    """
    # Header chiffré
    header = payload.setdefault("header", {})
    meta = data.get("header_meta", {})
    header.setdefault("date", timestamp.split(" · ")[0] if " · " in timestamp else timestamp)
    header.setdefault("time_casablanca", timestamp)
    header["active_sources_count"] = meta.get("active_sources_count")
    header["win_rate_30d"] = meta.get("win_rate_30d_pct")
    header["win_rate_total"] = meta.get("win_rate_count")

    # Snapshot et macro : remplacés par les chiffres Python
    snap = data.get("portfolio_snapshot") or {}
    if snap.get("value_usd"):
        payload["portfolio_snapshot"] = snap
    macro_ctx = data.get("macro_context") or {}
    if any(v is not None for v in macro_ctx.values()):
        existing = payload.get("macro_context") or {}
        existing.update({k: v for k, v in macro_ctx.items() if v is not None})
        payload["macro_context"] = existing

    # Rotation sectorielle : on injecte directement les chiffres réels
    sec = (data.get("sector_rotation") or {}).get("sectors", {})
    if sec:
        rot_list = []
        for name, sd in sec.items():
            rot_list.append({
                "sector": name,
                "change_24h": round(sd.get("avg_change_24h") or 0, 2),
                "your_holdings": sd.get("members", []),
            })
        # Tri par variation décroissante
        rot_list.sort(key=lambda r: r["change_24h"], reverse=True)
        payload["sector_rotation"] = rot_list

    # all_positions_summary : on garde ce que Python a calculé (37 actifs)
    if data.get("all_positions_summary"):
        payload["all_positions_summary"] = data["all_positions_summary"]
    # M8 : heatmap portfolio (factuelle, calculée Python)
    if data.get("portfolio_heatmap"):
        payload["portfolio_heatmap"] = data["portfolio_heatmap"]

    # Tracking : remplacer par la liste réelle des recos actives
    if data.get("active_recommendations") is not None:
        payload["active_recommendations_tracking"] = data["active_recommendations"]
    # V6 : changements d'avis récents (versioning des recos)
    if data.get("reco_changes"):
        payload["reco_changes"] = data["reco_changes"]

    # Blind spots : utiliser la phrase Python (factuelle)
    if data.get("blind_spots"):
        payload["blind_spots"] = data["blind_spots"]

    # On-chain : injecter les chiffres réels
    onc = data.get("onchain_indicators") or {}
    if onc.get("available"):
        payload.setdefault("onchain_indicators", {}).update({
            k: v for k, v in onc.items() if k != "available" and v is not None
        })

    # V6 : santé réseau, stablecoins, whale tracking, corrélation positions.
    # Ces blocs sont factuels (calculés Python) -> on les passe tels quels pour
    # affichage direct dans le template (Gemini ne les invente pas).
    if data.get("btc_network", {}).get("available"):
        payload["btc_network"] = data["btc_network"]
    if data.get("stablecoin_supply", {}).get("available"):
        payload["stablecoin_supply"] = data["stablecoin_supply"]
    if data.get("whale_inflows", {}).get("available"):
        payload["whale_inflows"] = data["whale_inflows"]
    if data.get("position_correlation", {}).get("available"):
        payload["position_correlation"] = data["position_correlation"]

    # M5 : hiérarchiser les thèses. action_type bullish/bearish = "action"
    # (décision à prendre), neutral = "watch" (surveillance). On trie pour
    # afficher les thèses actionnables en premier, puis par confiance décroissante.
    theses = payload.get("thesis_of_the_day") or []
    if isinstance(theses, list):
        for t in theses:
            if not isinstance(t, dict):
                continue
            at = (t.get("action_type") or "").lower()
            t["priority"] = "action" if at in ("bullish", "bearish") else "watch"
        def _thesis_rank(t: dict) -> tuple:
            if not isinstance(t, dict):
                return (2, 0)
            prio = 0 if t.get("priority") == "action" else 1
            conf = t.get("confidence") or 0
            return (prio, -conf)
        payload["thesis_of_the_day"] = sorted(theses, key=_thesis_rank)

    return payload


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
    # FUSION : on écrase les champs factuels avec les valeurs Python.
    payload = _merge_python_facts(payload, data, _now_str())
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("morning")
    payload.setdefault("footer", {})["active_sources"] = data.get("active_sources", [])
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
    news_global = newsapi.get_recent_news(None, hours=12)
    macro = fred.get_macro()
    polymarket = prediction_markets.get_fed_cut_probabilities()
    boursorama_cal = boursorama_calendar.get_boursorama_calendar()
    morning_state = mem.load_morning_report()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    active = tracker.refresh_active(price_lookup)

    # Delta de valeur du portfolio depuis le matin (basé sur le snapshot stocké)
    morning_snap = morning_state.get("portfolio_snapshot") or {}
    current_value = sum(
        (portfolio[s].get("value_usd") or 0) * (1 + (market.get(s, {}).get("change_24h") or 0) / 100 / 2)
        for s in symbols
    ) + sum(portfolio[s].get("value_usd") or 0 for s in symbols if portfolio[s].get("role") == "cash_reserve")
    delta_morning = current_value - (morning_snap.get("value_usd") or current_value)

    # S1 : écart horaire réel matin (08h30) -> maintenant.
    now_local = datetime.now(TZ)
    hours_since_morning = max(1, round(now_local.hour + now_local.minute / 60 - 8.5))

    # S5 : bilan P&L du jour + top movers (sur la base des variations 24h).
    movers = sorted(
        ({"symbol": s, "change": round(market.get(s, {}).get("change_24h") or 0, 1)}
         for s in symbols if market.get(s, {}).get("change_24h") is not None),
        key=lambda m: abs(m["change"]), reverse=True,
    )[:5]
    daily_pnl = {
        "value_usd": round(current_value, 2),
        "day_change_usd": round(delta_morning, 2),
        "day_change_pct": round(delta_morning / morning_snap["value_usd"] * 100, 2)
        if morning_snap.get("value_usd") else None,
        "top_movers": movers,
    }

    # S3 : macro de clôture US (S&P, Nasdaq, DXY) — dispo en soirée Casablanca.
    ev_macro_ctx = _macro_context(market, fng, macro, polymarket)
    evening_macro = {
        "sp500": ev_macro_ctx.get("sp500"),
        "sp500_delta": ev_macro_ctx.get("sp500_delta"),
        "nasdaq": ev_macro_ctx.get("nasdaq"),
        "dxy": ev_macro_ctx.get("dxy"),
    }

    # S4 : événements macro de demain (calendrier Boursorama, best-effort).
    tomorrow_macro_events: list[dict[str, Any]] = []
    if boursorama_cal.get("available"):
        tomorrow_macro_events = boursorama_cal.get("events", [])[:5]

    data = {
        "prices_now": price_lookup,
        "changes_24h": {s: market.get(s, {}).get("change_24h") for s in symbols},
        "fear_greed": fng, "etf_flows": etf, "news_12h": news_global[:8],
        "active_recommendations": active,
        "daily_pnl": daily_pnl, "evening_macro": evening_macro,
        "tomorrow_macro_events": tomorrow_macro_events,
        "hours_since_morning": hours_since_morning,
    }
    engine = DecisionEngine()
    payload = engine.generate_evening(
        timestamp=_now_str(), data=data, morning_state=morning_state,
    )
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    header = payload.setdefault("header", {})
    header["date"] = _now_str()
    header["time_casablanca"] = _now_str()
    header["hours_since_morning"] = hours_since_morning
    header["ptf_value_delta_since_morning"] = round(delta_morning, 2)
    if morning_snap.get("value_usd"):
        header["ptf_value_pct_since_morning"] = round(
            delta_morning / morning_snap["value_usd"] * 100, 2
        )
        payload.setdefault("portfolio_snapshot", {})["change_since_morning_pct"] = round(
            delta_morning / morning_snap["value_usd"] * 100, 2
        )
    payload.setdefault("portfolio_snapshot", {})["value_usd"] = round(current_value, 2)

    # S5/S3/S4/S6 : injecter les blocs factuels (calculés Python, non hallucinés).
    payload["daily_pnl"] = daily_pnl
    if any(evening_macro.values()):
        payload["evening_macro"] = evening_macro
    if tomorrow_macro_events:
        payload["tomorrow_macro_events"] = tomorrow_macro_events
    # S6 : si Gemini n'a pas produit de résumé news intraday, on fournit les titres bruts.
    if not payload.get("intraday_news") and news_global:
        payload["intraday_news"] = [
            {"title": n.get("title"), "source": n.get("source"),
             "timestamp": n.get("published_at")}
            for n in news_global[:5]
        ]

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
    polymarket = prediction_markets.get_fed_cut_probabilities()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(7)
    lesson = tracker.extract_lesson(7)
    dust = [{"asset": s, "value_usd": portfolio[s].get("value_usd")}
            for s in symbols if (portfolio[s].get("value_usd") or 0) < 5]

    # V6 : corrélation entre positions principales (>$5) pour les clusters de
    # risque. On récupère les séries de prix des positions significatives
    # seulement (limite les appels CoinGecko sur le run hebdo).
    significant = [
        s for s in symbols if (portfolio[s].get("value_usd") or 0) >= 5
    ][:15]
    weekly_price_series: dict[str, list[float]] = {}
    for s in significant:
        series = coingecko.get_price_volume_series(s, days=30)
        if series and series.get("prices"):
            weekly_price_series[s] = series["prices"]
    weekly_positions = {s: (portfolio[s].get("value_usd") or 0) for s in significant}
    correlation = compute_correlation_analysis(weekly_price_series, weekly_positions)

    # Valeur courante du PTF (positions + cash).
    current_value = sum(portfolio[s].get("value_usd") or 0 for s in portfolio)
    btc_price_now = (market.get("BTC") or {}).get("price")

    # H7 : enregistrer le snapshot de la semaine, puis charger l'historique.
    mem.record_weekly_snapshot(current_value, btc_price_now)
    snapshots = mem.load_weekly_snapshots()
    ptf_evolution = [
        {"label": s.get("week_label"), "value": s.get("value_usd")}
        for s in snapshots if s.get("value_usd") is not None
    ]

    # H6 : comparaison vs BTC hold. On compare au snapshot le plus ancien dispo
    # (jusqu'à ~30j) : si on avait mis toute la valeur d'alors en BTC, que vaudrait
    # le portefeuille aujourd'hui ?
    btc_hold_comparison = None
    ref_snaps = [s for s in snapshots if s.get("btc_price") and s.get("value_usd")]
    if len(ref_snaps) >= 2 and btc_price_now:
        ref = ref_snaps[0]  # le plus ancien
        btc_perf = btc_price_now / ref["btc_price"]
        btc_hold_value = ref["value_usd"] * btc_perf
        outperforms = current_value >= btc_hold_value
        diff_pct = ((current_value - btc_hold_value) / btc_hold_value * 100) if btc_hold_value else 0
        btc_hold_comparison = {
            "btc_hold_value": round(btc_hold_value, 2),
            "actual_value": round(current_value, 2),
            "outperforms": outperforms,
            "verdict": (
                f"Ta gestion active {'surperforme' if outperforms else 'sous-performe'} "
                f"un simple BTC hold de {abs(diff_pct):.1f}% sur la période "
                f"({len(ref_snaps)} semaines de recul)."
            ),
        }

    # H8 : top gagnants / perdants de la semaine (sur la base du change 7j).
    movers_7d = sorted(
        ({"symbol": s, "change": round(market.get(s, {}).get("change_7d") or 0, 1)}
         for s in symbols if market.get(s, {}).get("change_7d") is not None),
        key=lambda m: m["change"],
    )
    weekly_movers = None
    if movers_7d:
        losers = [m for m in movers_7d if m["change"] < 0][:3]
        gainers = [m for m in reversed(movers_7d) if m["change"] > 0][:3]
        if gainers or losers:
            weekly_movers = {"gainers": gainers, "losers": losers}

    # H3 : tendance du win rate (semaine vs 30j).
    win_rate_30d = tracker.compute_win_rate(30)
    win_rate_trend = None
    trend_direction = "flat"
    trend_note = None
    wr_week = win_rate.get("win_rate_pct")
    wr_month = win_rate_30d.get("win_rate_pct")
    if wr_week is not None and wr_month is not None:
        if wr_week > wr_month + 2:
            trend_direction = "up"
            win_rate_trend = f"↗ vs {wr_month}% le mois"
            trend_note = f"Win rate {wr_week}% cette semaine vs {wr_month}% sur 30j — en amélioration."
        elif wr_week < wr_month - 2:
            trend_direction = "down"
            win_rate_trend = f"↘ vs {wr_month}% le mois"
            trend_note = f"Win rate {wr_week}% cette semaine vs {wr_month}% sur 30j — en repli."
        else:
            win_rate_trend = f"≈ {wr_month}% le mois"
            trend_note = f"Win rate {wr_week}% cette semaine, stable vs 30j ({wr_month}%)."

    # Snapshot pour l'header hebdo
    week_state = {"last_morning": mem.load_morning_report(),
                  "last_evening": mem.load_evening_report()}
    last_snap = (week_state["last_morning"] or {}).get("portfolio_snapshot") or {}
    data = {"win_rate": win_rate, "lesson": lesson, "economic_calendar": calendar,
            "polymarket": polymarket, "dust_positions": dust, "prices_now": price_lookup}
    engine = DecisionEngine()
    payload = engine.generate_weekly(timestamp=_now_str(), data=data, week_state=week_state)
    checked = check_report(payload)
    payload = checked["sanitized_payload"]
    header = payload.setdefault("header", {})
    header["week"] = f"Semaine {datetime.now(TZ).strftime('%V')} · {_fr_date(datetime.now(TZ), with_time=False)}"
    if win_rate.get("total", 0) == 0:
        # 1re semaine sans historique : message clair
        payload.setdefault("predictions_scoring", {})
        payload["predictions_scoring"].update({
            "win_rate_pct": None, "validated": 0, "invalidated": 0,
            "lesson": "Première semaine : pas encore d'historique de recos clôturées. Patience.",
            "no_history": True,
        })
    else:
        payload.setdefault("predictions_scoring", {}).update(win_rate)
        payload["predictions_scoring"]["lesson"] = lesson
        # 'issued' = total des recos de la fenêtre (validées + invalidées + neutres).
        payload["predictions_scoring"].setdefault(
            "issued",
            (win_rate.get("validated", 0) + win_rate.get("invalidated", 0)
             + win_rate.get("neutral", 0)),
        )
    # Drawdown depuis le dernier snapshot
    if last_snap.get("drawdown_ath_pct") is not None:
        payload.setdefault("portfolio_overview", {})["drawdown_pct"] = last_snap["drawdown_ath_pct"]
    # V6 : matrice de corrélation chiffrée des positions (clusters de risque)
    if correlation.get("available"):
        payload["position_correlation"] = correlation
    # H3 : tendance du win rate
    if win_rate_trend:
        sc = payload.setdefault("predictions_scoring", {})
        sc["win_rate_trend"] = win_rate_trend
        sc["trend_direction"] = trend_direction
        if trend_note:
            sc["win_rate_trend_note"] = trend_note
    # H6 : comparaison vs BTC hold
    if btc_hold_comparison:
        payload["btc_hold_comparison"] = btc_hold_comparison
    # H7 : évolution du PTF (sparkline)
    if len(ptf_evolution) >= 2:
        payload["ptf_evolution"] = ptf_evolution
    # H8 : top gagnants / perdants de la semaine
    if weekly_movers:
        payload["weekly_movers"] = weekly_movers
    # NOUVEAU #4 : calibration confiance vs réalisé
    calibration = tracker.compute_calibration(30)
    if calibration.get("available"):
        payload["calibration"] = calibration
    # NOUVEAU #5 : coût des erreurs (regret)
    regret = tracker.compute_regret(7)
    if regret.get("available"):
        payload["regret"] = regret
    # NOUVEAU #11 : bilan des angles morts récurrents
    blind_spots_weekly = mem.compute_blind_spots_weekly()
    if blind_spots_weekly.get("available"):
        payload["blind_spots_weekly"] = blind_spots_weekly
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
    hack = newsapi.check_keywords_recent(cfg["hack_keywords"], hours=1, symbols=symbols)
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
