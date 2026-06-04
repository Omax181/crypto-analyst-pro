"""Orchestrateur principal V2 de l'agent crypto.

Modes (argument CLI) :
- ``morning``      : collecte complète -> rapport matin -> email + state.
- ``evening``      : différentiel depuis le matin -> rapport soir -> email + state.
- ``weekly``       : bilan semaine + scoring -> rapport hebdo -> email + state.

Robustesse : chaque source est isolée ; une panne n'interrompt pas le rapport.
Cohérence : la mémoire (state/) relie matin/soir/hebdo ; le tracking calcule le
win rate ; le coherence_checker valide le JSON avant envoi.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, Optional
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
    coinmetrics,
    cryptobubbles,
    crypto_rss,
    deribit,
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
    market_prices,
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
    yahoo_finance,
    youtube,
)
from src.analytics import digests
from src.analytics.correlation import (
    compute_correlation_analysis,
    compute_macro_crypto_correlation,
)
from src.reporting.email_sender import send_email
from src.state import report_memory as mem
from src.tracking.prediction_scoring import PredictionTracker
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_portfolio

logger = get_logger(__name__)

TZ = ZoneInfo("Africa/Casablanca")

# Sources interrogées chaque run (catalogue de référence pour l'angle "X / N
# sources actives ce matin" et le bilan hebdo des angles morts). Conserver
# en sync avec ce qui est réellement tenté côté collecte.
_ALL_SOURCES_LIST = [
    "CoinGecko", "Fear&Greed", "FRED", "On-chain", "Polymarket",
    "ETF flows", "Telegram", "DeFiLlama", "Kaito", "LunarCrush",
    "Token Unlocks", "News", "YouTube", "Géopolitique", "BTC Network",
    "Stablecoins", "Whale Tracking", "Yahoo Finance", "Calendrier macro",
    "RSS news (crypto + macro · 16 flux)",
    "On-chain avancé (Coin Metrics)", "Options (Deribit)", "Corrélations macro",
]
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
    tv_raw = tradingview.get_technical(symbol)
    tech = evaluate_technical(tv_raw)
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
        "tv_daily": (tv_raw.get("signals", {}) or {}).get("1d", {}),
        "derivatives": derivatives, "price": market.get("price"),
        "change_24h": market.get("change_24h"),
        "change_7d": market.get("change_7d"),
        "change_30d": market.get("change_30d"),
        "volume_24h": market.get("volume_24h"),
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
    # Cross-check des prix : double recoupement pour fiabiliser CHAQUE prix.
    # CoinGecko = source primaire. CoinMarketCap (batch, toutes les positions) +
    # Yahoo (grosses positions, sans clé) = sources de recoupement. Garde-fou
    # unique : 10% d'écart. Le statut par crypto (confirmed / single / diverged)
    # pilote les pastilles de fiabilité et le masquage des prix divergents.
    # Dégradation gracieuse totale (clé absente / source down → statut "single").
    cmc_quotes: dict[str, Any] = {}
    try:
        cmc_quotes = coinmarketcap.get_quotes(symbols) or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("CMC quotes indisponibles : %s", exc)
    yahoo_crypto: dict[str, float] = {}
    try:
        yahoo_crypto = market_prices.get_crypto_quotes() or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("Yahoo crypto quotes indisponibles : %s", exc)

    # Statut de fiabilité par crypto (pour pastilles + masquage des divergents).
    crypto_price_status = market_prices.compute_crypto_price_status(
        market, cmc_quotes, yahoo_crypto, tolerance_pct=10.0
    )
    # Divergences (>10%) → angles morts. Le prix sera masqué côté rendu.
    price_divergences = [
        {"symbol": sym, "gap_pct": st["gap_pct"], "sources": st["sources"]}
        for sym, st in crypto_price_status.items()
        if st["status"] == "diverged"
    ]
    # Compat : on conserve price_discrepancies (BTC/ETH) pour l'historique.
    price_discrepancies = {}
    try:
        if cmc_quotes:
            price_discrepancies = coinmarketcap.cross_check(
                {s: market.get(s, {}) for s in ("BTC", "ETH")},
                {s: cmc_quotes[s] for s in ("BTC", "ETH") if s in cmc_quotes},
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("Cross-check CMC (compat) ignoré : %s", exc)
    fng = fear_greed.get_fear_greed()
    macro = fred.get_macro()
    calendar = econ_calendar.get_economic_calendar()
    onchain = onchain_advanced.get_onchain_indicators()
    polymarket = prediction_markets.get_fed_cut_probabilities()
    # V10 — sources analytiques avancées (gratuites, sans clé, dégradation
    # gracieuse totale : un échec n'affecte aucune autre source ni le pipeline).
    onchain_cm = coinmetrics.get_onchain_metrics()      # MVRV / NVT / realized price
    options_deribit = deribit.get_options_metrics()     # put/call · max pain · DVOL
    macro_series = fred.get_macro_series(35)            # séries datées (corrélations)
    calendar_prints = fred.get_calendar_prints()        # derniers chiffres macro publiés
    etf = etf_flows.get_etf_flows()
    reddit_data = reddit.get_reddit_sentiment()
    reddit_sent = reddit_data.get("sentiment_score", 0.0)
    rotation = sector_rotation(market)

    # NewsAPI : UN SEUL appel global (free tier limité à 100 req/jour) puis
    # filtrage Python par symbole/hint. Évite 37 appels par run.
    news_global_items = newsapi.get_recent_news(None, hours=24)
    macro_news_items = newsapi.get_macro_news(hours=24)
    # Complément/fallback gratuit : Yahoo Finance RSS (Reuters/Bloomberg sont
    # réservés au plan payant de NewsAPI). On fusionne en dédupliquant.
    yahoo_macro = yahoo_finance.get_macro_news(limit=12)
    if yahoo_macro:
        seen_titles = {(n.get("title") or "").lower()[:60] for n in macro_news_items}
        for ym in yahoo_macro:
            key = (ym.get("title") or "").lower()[:60]
            if key and key not in seen_titles:
                macro_news_items.append(ym)
                seen_titles.add(key)
    # Sources RSS crypto gratuites (CoinDesk, Cointelegraph, Decrypt, The Block,
    # Bitcoin Magazine, CryptoSlate, CoinJournal). Enrichit news_global_items.
    rss_news = crypto_rss.get_news(hours=24, limit=25, category="crypto")
    if rss_news.get("available"):
        seen_global = {(n.get("title") or "").lower()[:60] for n in news_global_items}
        for rn in rss_news.get("news", []):
            key = (rn.get("title") or "").lower()[:60]
            if key and key not in seen_global:
                news_global_items.append({
                    "title": rn.get("title"),
                    "source": rn.get("source"),
                    "url": rn.get("url"),
                    "published_at": rn.get("published_iso"),
                    "summary": rn.get("summary"),
                })
                seen_global.add(key)
    # Sources RSS macro/finance (Reuters, MarketWatch, Investing.com, FT,
    # Seeking Alpha, Barron's, Stocktwits). Enrichit macro_news_items.
    rss_macro = crypto_rss.get_news(hours=24, limit=15, category="macro")
    if rss_macro.get("available"):
        seen_macro = {(n.get("title") or "").lower()[:60] for n in macro_news_items}
        for rn in rss_macro.get("news", []):
            key = (rn.get("title") or "").lower()[:60]
            if key and key not in seen_macro:
                macro_news_items.append({
                    "title": rn.get("title"),
                    "source": rn.get("source"),
                    "url": rn.get("url"),
                    "published_at": rn.get("published_iso"),
                })
                seen_macro.add(key)
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
    # Crypto Bubbles : top mouvements du marché + focus PTF (source complémentaire).
    market_movers = cryptobubbles.get_market_movers(symbols, top_n=8)

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
        asset["value_usd"] = _position_value(info, market.get(sym))
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
            # V10 — détail technique compact (valeurs brutes : RSI/MACD/Stoch/
            # ADX/SMA cross/Bollinger/SR + signaux qui flashent).
            technical_detail = digests.build_asset_technical(
                asset.get("tv_daily") or {}, ta
            )
            entry = {
                "asset": sym, "tier": tier,
                "signals_count": asset["score"]["signals_count"],
                "bullish_count": asset["score"]["bullish_count"],
                "bearish_count": asset["score"]["bearish_count"],
                "composite": asset["score"]["total"],
                "price": asset["price"],
                "change_24h": asset["change_24h"],
                "ath_distance_pct": asset["ath_distance_pct"],
                "technical_signal": asset["technical"].get("dominant_signal"),
                "technical_detail": technical_detail,
                "signals_detail": asset["score"]["components"],
                "fibonacci": ta.get("fibonacci") if ta.get("available") else None,
                "bollinger": ta.get("bollinger") if ta.get("available") else None,
                "support_resistance": ta.get("support_resistance") if ta.get("available") else None,
                "moving_averages": ta.get("moving_averages") if ta.get("available") else None,
                "tvl": asset.get("tvl") if asset.get("tvl", {}).get("available") else None,
                "social": asset.get("social") if asset.get("social", {}).get("available") else None,
                "dev_activity": asset.get("dev") if asset.get("dev", {}).get("available") else None,
            }
            # On-chain avancé + options pour les actifs couverts (BTC/ETH).
            cm_asset = (onchain_cm.get("assets") or {}).get(sym)
            if cm_asset:
                entry["onchain_advanced"] = cm_asset
            opt_asset = (options_deribit.get("assets") or {}).get(sym)
            if opt_asset:
                entry["options"] = opt_asset
            eligible.append(entry)
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

    # V10 — corrélations macro ↔ crypto : BTC (clôtures datées) vs séries FRED
    # (or/DXY/S&P/VIX/10Y). 1 appel CoinGecko (caché 30min) pour aligner par date.
    btc_dated = coingecko.get_dated_closes("BTC", 35)
    macro_correlations = compute_macro_crypto_correlation(btc_dated, macro_series, window=30)

    tracker = PredictionTracker()
    price_lookup = {s: enriched[s].get("price") for s in enriched}
    active_recos = tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(30)
    # V10 — boucle de feedback : performance par actif + erreurs récentes
    # (réinjectées dans le prompt pour que l'IA apprenne de ses échecs).
    per_asset_perf = tracker.compute_per_asset_performance(90)
    reco_changes = mem.recent_reco_changes(7)

    active_sources = _active_sources(
        market=market, fng=fng, macro=macro, onchain=onchain, polymarket=polymarket,
        etf=etf, telegram=telegram, defi=defi, narratives=narratives,
        social=social_trending, unlocks=unlocks, news=bool(news_global_items),
        youtube=youtube_corpus, geopolitics=geopol,
        btc_network=btc_network, stablecoins=stablecoin_supply, whales=whale_inflows,
        macro_news=bool(macro_news_items), macro_calendar=boursorama_cal,
        crypto_rss=rss_news.get("available"),
        onchain_adv=onchain_cm, options=options_deribit, macro_corr=macro_correlations,
    )

    # Enregistre la santé des sources (alimente le bilan hebdo des angles morts).
    _ALL_SOURCES = _ALL_SOURCES_LIST  # alias local pour compat héritée
    try:
        mem.record_source_health(_ALL_SOURCES, active_sources)
    except Exception as exc:  # noqa: BLE001
        logger.info("record_source_health ignoré : %s", exc)

    # Portfolio snapshot calculé côté Python (Gemini n'a pas à l'inventer).
    snapshot = _portfolio_snapshot(portfolio, enriched)
    # P&L nuit (20h→8h) : valeur actuelle − valeur du dernier rapport du soir.
    # Affiché dans les métriques (remplace le drawdown). n/d au tout premier run.
    _ev_prev = (mem.load_evening_report() or {}).get("portfolio_snapshot") or {}
    _ev_prev_val = _ev_prev.get("value_usd")
    if isinstance(_ev_prev_val, (int, float)) and _ev_prev_val and snapshot.get("value_usd"):
        _overnight = snapshot["value_usd"] - _ev_prev_val
        snapshot["overnight_pnl_usd"] = round(_overnight, 2)
        snapshot["overnight_pnl_pct"] = round(_overnight / _ev_prev_val * 100, 2)
    else:
        snapshot["overnight_pnl_usd"] = None
        snapshot["overnight_pnl_pct"] = None
    # Prix macro temps réel (Yahoo) — prioritaires sur FRED pour Gold/Brent/WTI/
    # indices/FX, qui sont en retard ou gelés côté FRED.
    yahoo_quotes = market_prices.get_macro_quotes()
    # Macro context : valeurs chiffrées injectées directement.
    macro_context = _macro_context(market, fng, macro, polymarket, yahoo_quotes)
    # Statut de fiabilité par métrique macro (pastilles : vert si Yahoo+FRED
    # concordent, orange si une seule source).
    macro_source_status = market_prices.compute_macro_source_status(
        macro_context, yahoo_quotes, (macro or {}).get("series"), tolerance_pct=10.0
    )

    # V10 — DIGEST ANALYTIQUE COMPACT : lignes condensées (économie de tokens)
    # que l'IA exploite pour un raisonnement CROISÉ. Chaque ligne est vide si la
    # source correspondante est indisponible (jamais d'invention).
    analytics_digest = {
        "macro_correlations": digests.macro_correlation_line(macro_correlations),
        "macro_calendar": digests.calendar_line(calendar_prints, polymarket),
        "onchain_advanced": digests.onchain_line(onchain_cm),
        "options": digests.options_line(options_deribit),
        "feedback": digests.feedback_line(per_asset_perf),
    }

    return {
        "header_meta": {
            "active_sources_count": len(active_sources),
            "total_sources_count": len(_ALL_SOURCES),
            "active_sources": active_sources,
            "price_discrepancies": price_discrepancies,
            "price_divergences": price_divergences,
            "win_rate_30d_pct": win_rate.get("win_rate_pct"),
            "win_rate_count": f"{win_rate.get('validated', 0)}/{win_rate.get('total', 0)}",
        },
        "crypto_price_status": crypto_price_status,
        "macro_source_status": macro_source_status,
        "portfolio_snapshot": snapshot,
        "macro_context": macro_context,
        "analytics_digest": analytics_digest,
        "market_global": glob, "fear_greed": fng, "macro": _sanitize_macro_for_prompt(macro),
        "economic_calendar": calendar, "onchain_indicators": onchain,
        "polymarket": polymarket, "etf_flows": etf, "reddit": reddit_data,
        "telegram": telegram, "defi_tvl": defi, "kaito_narratives": narratives,
        "social_trending": social_trending, "token_unlocks": unlocks,
        "sector_rotation": rotation, "news_counts": news_counts,
        "news_24h_global": news_global_items[:12],
        "macro_news": macro_news_items[:12],
        "boursorama_calendar": boursorama_cal,
        "market_movers": market_movers,
        "youtube_corpus": youtube_corpus, "geopolitics": geopol,
        "btc_network": btc_network, "stablecoin_supply": stablecoin_supply,
        "whale_inflows": whale_inflows, "position_correlation": correlation,
        "active_sources": active_sources,
        "eligible_theses": eligible, "active_recommendations": active_recos,
        "reco_changes": reco_changes,
        "win_rate": win_rate,
        "all_positions_summary": _positions_summary(enriched, active_recos),
        "portfolio_heatmap": _portfolio_heatmap(enriched),
        "blind_spots": _blind_spots(
            onchain, polymarket, etf, telegram, defi,
            macro_flags=list(_macro_validation_flags),
            price_discrepancies=price_discrepancies,
            price_divergences=price_divergences,
        ),
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
        # Statut court (≤25 car) selon l'action active, pour le tableau dense.
        action = action_by_asset.get(s)
        status_short = _short_status(action, sig, bull, bear)
        out.append({
            "asset": s,
            "tier": tier,
            "change_24h": change,
            "change_7d": e.get("change_7d"),
            "change_30d": e.get("change_30d"),
            "price": e.get("price"),
            "volume_24h": e.get("volume_24h"),
            "comment": comment,
            "action_active": action,
            "status_short": status_short,
        })
    # Tri : actions actives d'abord, puis par tier, puis par |variation|.
    out.sort(key=lambda p: (
        p["action_active"] is None,
        p["tier"] if p["tier"] is not None else 9,
        -abs(p["change_24h"] or 0),
    ))
    return out


def _short_status(action: str | None, sig: int, bull: int, bear: int) -> str:
    """Construit un statut court (≤25 car) pour le tableau récap dense.

    RENFORCER → "🟢 …", ALLÉGER → "🔴 …", SURVEILLER → "🟡 …", RAS → "".
    """
    act = (action or "").upper()
    if act.startswith("RENFORC"):
        txt = f"🟢 {sig} signaux" if sig else "🟢 conviction"
    elif act.startswith(("ALLÉG", "ALLEG", "SORT", "VENT")):
        txt = "🔴 signaux baissiers" if bear > bull else "🔴 prise de profit"
    elif act.startswith("SURVEIL"):
        txt = f"🟡 {bull}/{sig} signaux" if sig else "🟡 en attente"
    else:
        return ""  # RAS : cellule vide, disparaît au scan
    return txt[:25]


def _portfolio_heatmap(enriched: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Construit une heatmap des positions : top 16 par poids dans le PTF.

    Triée par valeur décroissante (ordre STABLE d'un run à l'autre pour que le
    lecteur retrouve ses positions au même endroit). Le template affiche une
    grille 8×2 colorée selon la perf 24h. Données 100% factuelles.

    Returns:
        Dict ``{cells: [{symbol, value_usd, change_24h}] (max 16),
        total_count: int, remaining: int}``.
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
    total = len(cells)
    top = cells[:16]
    return {
        "cells": top,
        "total_count": total,
        "remaining": max(0, total - len(top)),
    }


def _position_value(info: dict[str, Any], market_entry: dict[str, Any] | None) -> float:
    """Valeur d'une position : quantité × prix live, fallback sur value_usd config.

    Permet au total du portefeuille de suivre le marché en temps réel à chaque
    run, plutôt que d'utiliser une valeur figée. Si le prix live est absent
    (API en échec), on retombe sur le ``value_usd`` du YAML (baseline = dernier
    snapshot connu).
    """
    qty = info.get("quantity")
    price = (market_entry or {}).get("price")
    if qty is not None and price:
        try:
            return round(float(qty) * float(price), 2)
        except (TypeError, ValueError):
            pass
    return float(info.get("value_usd") or 0)


def _portfolio_snapshot(
    portfolio: dict[str, Any], enriched: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Calcule la valeur totale et les variations 24h/7j du portefeuille.

    Inclut la performance vs BTC sur 7j (PTF outperform/underperform), le %
    de cash USDC et le drawdown moyen pondéré vs ATH.
    """
    crypto_total = 0.0
    delta_24h = 0.0
    delta_7d = 0.0
    usdc_value = 0.0
    drawdown_sum_weighted = 0.0
    counted = 0
    has_7d = False
    for sym, info in portfolio.items():
        if info.get("role") == "cash_reserve":
            usdc_value += float(info.get("value_usd") or 0)
            continue
        a = enriched.get(sym) or {}
        # Valeur dynamique (qté × prix live), déjà calculée dans enriched.
        v = a.get("value_usd")
        if v is None:
            v = _position_value(info, None)
        crypto_total += v
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
    total_wealth = crypto_total + usdc_value
    change_7d_pct = round((delta_7d / crypto_total) * 100, 2) if (crypto_total and has_7d) else None
    btc_7d = (enriched.get("BTC") or {}).get("change_7d")
    vs_btc_7d = round(change_7d_pct - btc_7d, 2) if (change_7d_pct is not None and btc_7d is not None) else None
    return {
        "value_usd": round(crypto_total, 2),
        "change_24h_pct": round((delta_24h / crypto_total) * 100, 2) if crypto_total else None,
        "change_7d_pct": change_7d_pct,
        "change_7d_usd": round(delta_7d, 2) if has_7d else None,
        "vs_btc_7d_pct": vs_btc_7d,
        "drawdown_ath_pct": round(drawdown_sum_weighted / counted, 1) if counted else None,
        "usdc_pct": round((usdc_value / total_wealth) * 100, 1) if total_wealth else None,
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


# A3 : plages plausibles par métrique macro. Hors de la plage = donnée
# suspecte (erreur de série, glitch FRED, hallucination) → on n'affiche PAS
# (None → "—" dans le template) et on logge. Note : "dxy" ici = indice dollar
# LARGE (FRED DTWEXBGS, base 100 en 2006), qui cote ~115-125 — c'est normal et
# différent du DXY ICE (~100-105). La plage reflète l'indice large.
_MACRO_RANGES: dict[str, tuple[float, float]] = {
    "dxy": (90.0, 145.0),
    "dxy_ice": (70.0, 130.0),
    "vix": (5.0, 100.0),
    "us_10y": (0.0, 12.0),
    "us_2y": (0.0, 12.0),
    "yield_curve": (-5.0, 5.0),
    "gold": (500.0, 6000.0),
    "sp500": (1000.0, 12000.0),
    "nasdaq": (3000.0, 45000.0),
    "brent": (10.0, 250.0),
    "wti": (10.0, 250.0),
    "eur_usd": (0.7, 1.6),
    "usd_jpy": (80.0, 260.0),
    "btc_price": (1000.0, 500000.0),
}
_macro_validation_flags: list[str] = []


def _vm(metric: str, value: Any) -> Any:
    """Valide une métrique macro contre sa plage plausible (A3).

    Renvoie la valeur si plausible, sinon None (non affichée) en loggant et en
    enregistrant un flag pour signalement éventuel dans les angles morts.
    """
    if value is None:
        return None
    rng = _MACRO_RANGES.get(metric)
    if rng is None:
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value  # non numérique : laissé tel quel (ex. libellé)
    lo, hi = rng
    if v < lo or v > hi:
        logger.warning("Macro %s=%s hors plage plausible [%s, %s] → masqué.", metric, v, lo, hi)
        if metric not in _macro_validation_flags:
            _macro_validation_flags.append(metric)
        return None
    return value


def _sanitize_macro_for_prompt(macro: dict[str, Any]) -> dict[str, Any]:
    """Valide les valeurs du dict FRED brut AVANT injection dans le prompt Gemini.

    macro_context est déjà validé via ``_vm``, mais le dict ``macro`` brut
    (structure ``{available, series: {name: {value, ...}}}``) est aussi
    sérialisé dans le prompt. Sans ce nettoyage, Gemini pourrait narrer sur une
    valeur aberrante présente dans le JSON brut. On nullifie ici les valeurs
    hors plage plausible (mêmes plages que ``_vm``). Les séries sans plage
    (cpi, m2, unemployment, fed_funds) sont laissées telles quelles.
    Renvoie une COPIE (n'altère pas le dict d'origine utilisé pour les pastilles).
    """
    if not isinstance(macro, dict):
        return macro
    series = macro.get("series")
    if not isinstance(series, dict):
        return macro
    cleaned: dict[str, Any] = {}
    for name, obs in series.items():
        if isinstance(obs, dict) and "value" in obs:
            new_obs = dict(obs)
            new_obs["value"] = _vm(name, obs.get("value"))
            cleaned[name] = new_obs
        else:
            cleaned[name] = obs
    out = dict(macro)
    out["series"] = cleaned
    return out


def _macro_context(
    market: dict[str, Any], fng: dict[str, Any], macro: dict[str, Any],
    polymarket: dict[str, Any], yahoo_quotes: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Agrège les chiffres macro pour l'en-tête et l'analyse.

    V6 : expose, en plus de BTC/F&G/DXY/Fed cut, les actifs macro hors-crypto
    (Gold, S&P 500, Nasdaq, Brent, WTI, EUR/USD, USD/JPY) et les taux/volatilité
    (VIX, US 10Y, US 2Y, yield curve 10Y-2Y).

    V8 : ``yahoo_quotes`` (prix temps réel Yahoo Finance) est prioritaire sur
    FRED pour les actifs cotés en continu (Gold, Brent, WTI, indices, FX, VIX,
    10Y) — FRED est en retard de plusieurs jours sur les matières premières et
    sa série or historique est gelée. FRED reste le fallback si Yahoo est
    indisponible. On expose aussi ``dxy_ice`` (le vrai DXY ICE ~99-105, distinct
    du ``dxy`` broad FRED ~115-125) pour lever toute ambiguïté de libellé.
    """
    _macro_validation_flags.clear()  # réinit par run (évite l'accumulation)
    yq = yahoo_quotes or {}
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

    # Yahoo prioritaire (live), FRED fallback. _pref(yahoo_key, fred_dict) :
    # renvoie la valeur Yahoo si présente, sinon la valeur FRED.
    def _pref(yk: str, fred_d: dict[str, Any]) -> Any:
        return yq[yk] if yk in yq else fred_d["value"]

    fed_cut = None
    if polymarket.get("available"):
        for m in polymarket.get("markets", []):
            q = (m.get("question") or "").lower()
            if "rate cut" in q or "fed" in q:
                fed_cut = m.get("probability_pct")
                break

    return {
        # Cœur (header principal, comme V5).
        "btc_price": _vm("btc_price", round(btc_price, 2) if btc_price else None),
        "fear_greed": fng_val,
        # dxy = indice dollar LARGE (FRED DTWEXBGS, ~115-125). Libellé corrigé
        # côté template en "USD (large)". dxy_ice = vrai DXY ICE (~99-105).
        "dxy": _vm("dxy", dxy["value"]),
        "dxy_delta": dxy["delta"],
        "dxy_ice": _vm("dxy_ice", yq.get("dxy_ice")),
        "polymarket_fed_cut_pct": fed_cut,
        # Taux & volatilité (Yahoo prioritaire sur VIX et 10Y).
        "vix": _vm("vix", _pref("vix", vix)),
        "us_10y": _vm("us_10y", _pref("us_10y", us_10y)),
        "us_2y": _vm("us_2y", us_2y["value"]),
        "yield_curve_10y_2y": _vm("yield_curve", yield_curve["value"]),
        # Actifs macro hors-crypto (Yahoo prioritaire — prix live).
        "gold_usd": _vm("gold", _pref("gold", gold)),
        "gold_delta": gold["delta"],
        "sp500": _vm("sp500", _pref("sp500", sp500)),
        "sp500_delta": sp500["delta"],
        "nasdaq": _vm("nasdaq", _pref("nasdaq", nasdaq)),
        "nasdaq_delta": nasdaq["delta"],
        "brent_usd": _vm("brent", _pref("brent", brent)),
        "wti_usd": _vm("wti", _pref("wti", wti)),
        "eur_usd": _vm("eur_usd", _pref("eur_usd", eur_usd)),
        "usd_jpy": _vm("usd_jpy", _pref("usd_jpy", usd_jpy)),
        # Provenance (pour transparence / debug ; non affiché par défaut).
        "_price_source": "yahoo+fred" if yq else "fred",
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
        "macro_news": "Yahoo Finance", "macro_calendar": "Calendrier macro",
        "crypto_rss": "RSS news (crypto + macro · 16 flux)",
        "onchain_adv": "On-chain avancé (Coin Metrics)",
        "options": "Options (Deribit)",
        "macro_corr": "Corrélations macro",
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


def _blind_spots(
    *sources: dict[str, Any],
    macro_flags: Optional[list[str]] = None,
    price_discrepancies: Optional[dict[str, Any]] = None,
    price_divergences: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Construit la phrase d'angles morts à partir des sources indisponibles.

    Ne liste QUE les sources réellement indisponibles ce jour. Signale en plus
    (A3) : les métriques macro masquées car hors plage plausible, et les écarts
    de prix détectés aux cross-checks (CoinGecko vs CoinMarketCap, et CoinGecko
    vs Yahoo Finance sur une couverture élargie).
    """
    labels = ["on-chain avancé", "Polymarket", "ETF flows", "Telegram", "DeFiLlama"]
    missing = [labels[i] for i, src in enumerate(sources)
               if not (src.get("available") if isinstance(src, dict) else src)]
    parts: list[str] = []
    if missing:
        parts.append("Sources indisponibles ce matin : " + ", ".join(missing) + ".")

    # A3 : chiffres macro écartés car aberrants (non affichés par prudence).
    if macro_flags:
        _names = {
            "dxy": "indice dollar", "dxy_ice": "DXY ICE", "vix": "VIX",
            "us_10y": "taux 10 ans US",
            "us_2y": "taux 2 ans US", "yield_curve": "courbe des taux",
            "gold": "or", "sp500": "S&P 500", "nasdaq": "Nasdaq",
            "brent": "Brent", "wti": "WTI", "eur_usd": "EUR/USD",
            "usd_jpy": "USD/JPY", "btc_price": "prix BTC",
        }
        flagged = ", ".join(_names.get(m, m) for m in macro_flags)
        parts.append(
            f"Donnée(s) macro écartée(s) car hors plage plausible (non affichée(s)) : {flagged}."
        )

    # A3 : écarts de prix au cross-check CoinMarketCap. Format {symbole: ecart_pct}.
    if price_discrepancies:
        diverging = [
            f"{sym} ({pct}%)"
            for sym, pct in price_discrepancies.items()
            if isinstance(pct, (int, float))
        ]
        if diverging:
            parts.append(
                "Écart de prix entre sources (CoinGecko vs CoinMarketCap), donnée à vérifier : "
                + ", ".join(diverging) + "."
            )

    # A3 bis : écarts au cross-check Yahoo (couverture élargie). Format liste de
    # dicts {symbol, coingecko, yahoo, gap_pct}.
    if price_divergences:
        diverging_y = [
            f"{d['symbol']} ({d['gap_pct']}%)"
            for d in price_divergences
            if isinstance(d, dict) and d.get("gap_pct") is not None
        ]
        if diverging_y:
            parts.append(
                "Écart de prix CoinGecko vs Yahoo (>6%), prix à vérifier avant toute décision : "
                + ", ".join(diverging_y) + "."
            )

    if not parts:
        return "Couverture des sources complète aujourd'hui · chiffres clés cross-checkés sur 2 sources."
    return " ".join(parts)


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
    header["total_sources_count"] = meta.get("total_sources_count")
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
    # Statuts de fiabilité (pastilles) : injectés tels quels pour le rendu.
    # crypto_price_status : {symbole: {status, gap_pct, sources, price}}.
    # macro_source_status : {clé_macro: "confirmed"|"single"}.
    if data.get("crypto_price_status"):
        payload["crypto_price_status"] = data["crypto_price_status"]
    if data.get("macro_source_status"):
        payload["macro_source_status"] = data["macro_source_status"]
    # M8 : heatmap portfolio (factuelle, calculée Python)
    if data.get("portfolio_heatmap"):
        payload["portfolio_heatmap"] = data["portfolio_heatmap"]
    # Partie 7 : top mouvements du marché (Crypto Bubbles)
    mm = data.get("market_movers")
    if isinstance(mm, dict) and mm.get("available"):
        payload["market_movers"] = mm

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

    # PARTIE 3 : trier les news par importance décroissante. Priorité par
    # catégorie (Catalyseur > Risque > Macro > Géopolitique > Info) pondérée
    # par la confiance annoncée. Tri stable (Gemini peut fournir 'importance').
    news = payload.get("news_24h")
    if isinstance(news, list) and news:
        cat_rank = {"catalyseur": 5, "risque": 4, "macro": 3,
                    "géopolitique": 2, "geopolitique": 2, "info": 1}
        conf_rank = {"haute": 3, "élevée": 3, "elevee": 3, "moyenne": 2, "faible": 1}
        def _news_score(n: dict) -> tuple:
            if not isinstance(n, dict):
                return (0, 0, 0)
            # Si Gemini fournit un score explicite, il prime.
            imp = n.get("importance")
            if isinstance(imp, (int, float)):
                imp_score = float(imp)
            else:
                cat = (n.get("category") or "info").lower().strip()
                imp_score = cat_rank.get(cat, 1)
            conf = n.get("confidence")
            if isinstance(conf, (int, float)):
                conf_score = float(conf) / 100.0
            else:
                conf_score = conf_rank.get((str(conf or "")).lower().strip(), 1) / 3.0
            return (imp_score, conf_score)
        payload["news_24h"] = sorted(news, key=_news_score, reverse=True)

    return payload


def _coerce_confidence(value: Any) -> Optional[float]:
    """Extrait une confiance numérique (0-100) depuis un nombre, '72', '72%' ou un label.

    Gemini renvoie tantôt un entier (``72``), tantôt une chaîne (``"72%"``),
    tantôt un libellé (``"élevée"``). On normalise pour pouvoir filtrer sur un
    seuil. Renvoie ``None`` si rien d'exploitable.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower().replace("%", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            pass
        labels = {
            "très élevée": 85, "tres elevee": 85, "very high": 85,
            "élevée": 75, "elevee": 75, "high": 75, "forte": 75,
            "moyenne": 60, "medium": 60, "modérée": 60, "moderee": 60,
            "faible": 40, "low": 40,
        }
        for key, val in labels.items():
            if key in s:
                return float(val)
    return None


def _persist_firm_recos(payload: dict[str, Any], data: dict[str, Any]) -> None:
    """Persiste les thèses FERMES du matin comme recommandations actives.

    Pourquoi : sans cet appel, ``state/active_recommendations.json`` et
    ``state/prediction_history.json`` restaient vides en permanence. Résultat :
    ``refresh_active`` tournait sur une liste vide et ``compute_win_rate``
    renvoyait toujours 0/0 (win rate jamais alimenté, calibration et regret
    toujours vides). On enregistre donc les thèses actionnables pour fermer la
    boucle de tracking.

    Règles :
    - on ne garde que les actions SCORABLES : RENFORCER / ALLÉGER (les
      SURVEILLER/MAINTENIR sont neutres et n'apportent rien au win rate) ;
    - confiance numérique requise et >= 55 (on ne track pas les paris faibles) ;
    - le prix d'entrée est le prix courant RÉEL calculé côté Python
      (``all_positions_summary``), jamais le champ ``entry`` free-form de Gemini
      qui est souvent une fourchette ou du texte, donc inexploitable pour le
      scoring déterministe ;
    - l'id ``{asset}-{date}-{action}`` est idempotent : ``add_recommendation``
      dédoublonne, donc relancer le matin n'empile pas de doublons.
    """
    theses = payload.get("thesis_of_the_day") or []
    if not isinstance(theses, list):
        return

    price_by_asset: dict[str, float] = {}
    for row in (data.get("all_positions_summary") or []):
        if not isinstance(row, dict):
            continue
        asset = row.get("asset")
        price = row.get("price")
        if asset and isinstance(price, (int, float)) and not isinstance(price, bool):
            price_by_asset[asset] = float(price)

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    created = mem.now_iso()
    persisted = 0
    for th in theses:
        if not isinstance(th, dict):
            continue
        asset = th.get("asset")
        action = (th.get("action") or "").upper()
        if not asset or not any(k in action for k in ("RENFORC", "ALLÉG", "ALLEG")):
            continue
        conf = _coerce_confidence(th.get("confidence"))
        if conf is None or conf < 55:
            continue
        price = price_by_asset.get(asset)
        if price is None or price <= 0:
            continue
        canonical = "RENFORCER" if "RENFORC" in action else "ALLEGER"
        reco = {
            "id": f"{asset}-{today}-{canonical}",
            "asset": asset,
            "action": canonical,
            "confidence": conf,
            "entry_price": price,
            "signal_price": price,
            "created_at": created,
            "status": "in_progress",
            "rationale": th.get("thesis") or th.get("summary") or "",
        }
        try:
            mem.add_recommendation(reco)
            persisted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Persist reco %s échouée : %s", reco.get("id"), exc)
    if persisted:
        logger.info("%d reco(s) ferme(s) persistée(s) pour le tracking.", persisted)


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
    # Boucle de tracking : on persiste les thèses fermes du jour pour que le win
    # rate, la calibration et le regret puissent réellement se calculer (gardé :
    # une erreur de persistance ne doit jamais bloquer l'envoi du rapport).
    try:
        _persist_firm_recos(payload, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persistance des recos fermes ignorée : %s", exc)
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
    # Enrichissement RSS (12h, fort impact, crypto + macro) en dédupliquant.
    rss_evening = crypto_rss.get_news(hours=12, high_impact_only=True, limit=15, category="all")
    if rss_evening.get("available"):
        seen_ev = {(n.get("title") or "").lower()[:60] for n in news_global}
        for rn in rss_evening.get("news", []):
            key = (rn.get("title") or "").lower()[:60]
            if key and key not in seen_ev:
                news_global.append({
                    "title": rn.get("title"),
                    "source": rn.get("source"),
                    "url": rn.get("url"),
                    "published_at": rn.get("published_iso"),
                })
                seen_ev.add(key)
    macro = fred.get_macro()
    polymarket = prediction_markets.get_fed_cut_probabilities()
    boursorama_cal = boursorama_calendar.get_boursorama_calendar()
    morning_state = mem.load_morning_report()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    active = tracker.refresh_active(price_lookup)

    # Delta de valeur du portfolio depuis le matin (basé sur le snapshot stocké).
    # Valeur dynamique crypto-only (qté × prix live), cohérente avec le snapshot.
    morning_snap = morning_state.get("portfolio_snapshot") or {}
    current_value = sum(_position_value(portfolio[s], market.get(s)) for s in symbols)
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
    ev_yahoo_quotes = market_prices.get_macro_quotes()
    ev_macro_ctx = _macro_context(market, fng, macro, polymarket, ev_yahoo_quotes)
    evening_macro = {
        "sp500": ev_macro_ctx.get("sp500"),
        "sp500_delta": ev_macro_ctx.get("sp500_delta"),
        "nasdaq": ev_macro_ctx.get("nasdaq"),
        "dxy": ev_macro_ctx.get("dxy"),
        "dxy_ice": ev_macro_ctx.get("dxy_ice"),
    }
    # Statut de fiabilité macro (pastilles ² du rendu soir) — calculé Python,
    # alimente _mss_e dans le template. Sans ça les pastilles seraient vides.
    ev_macro_source_status = market_prices.compute_macro_source_status(
        ev_macro_ctx, ev_yahoo_quotes, (macro or {}).get("series"), tolerance_pct=10.0
    )

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
    # Pastilles ² macro (vert = Yahoo+FRED concordent, orange = 1 source).
    if ev_macro_source_status:
        payload["macro_source_status"] = ev_macro_source_status
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
    current_value = sum(_position_value(portfolio[s], market.get(s)) for s in symbols)
    btc_price_now = (market.get("BTC") or {}).get("price")

    # Pastilles ² (point 6 weekly) : statut de fiabilité par crypto via cross-check
    # CoinGecko × CoinMarketCap (10%). Alimente _cps dans le template (top movers).
    _cmc_quotes_w: dict[str, Any] = {}
    try:
        _cmc_quotes_w = coinmarketcap.get_quotes(symbols) or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("CMC quotes weekly indisponibles : %s", exc)
    crypto_price_status_w = market_prices.compute_crypto_price_status(
        market, _cmc_quotes_w, None, tolerance_pct=10.0
    )

    # H7 : enregistrer le snapshot de la semaine, puis charger l'historique.
    # On stocke aussi le drawdown courant pour calculer sa variation WoW.
    _snap_now = (mem.load_morning_report() or {}).get("portfolio_snapshot") or {}
    _dd_now = _snap_now.get("drawdown_ath_pct")
    mem.record_weekly_snapshot(current_value, btc_price_now, drawdown_ath_pct=_dd_now)
    snapshots = mem.load_weekly_snapshots()
    ptf_evolution = [
        {"label": s.get("week_label"), "value": s.get("value_usd")}
        for s in snapshots if s.get("value_usd") is not None
    ]

    # drawdown_change_pts : variation du drawdown vs la semaine précédente
    # (factuel, calculé Python — JAMAIS fourni par Gemini). Positif = le
    # drawdown s'est réduit (amélioration), négatif = détérioration.
    _drawdown_change_pts = None
    if _dd_now is not None and len(snapshots) >= 2:
        prev_dd = None
        for s in reversed(snapshots[:-1]):  # le plus récent avant cette semaine
            if s.get("drawdown_ath_pct") is not None:
                prev_dd = s["drawdown_ath_pct"]
                break
        if prev_dd is not None:
            _drawdown_change_pts = round(_dd_now - prev_dd, 1)

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

    # ─────────────────────────────────────────────────────────────
    # Bug weekly #1 : portfolio_snapshot N'ÉTAIT PAS injecté côté Python.
    # Gemini était censé le remplir mais ne le faisait pas → tous les KPI
    # (Valeur, vs BTC 7j, P&L semaine, Cash USDC) tombaient sur "—".
    # On injecte ici les vraies valeurs calculées. Drawdown ATH supprimé
    # (point 9), remplacé par P&L semaine en % et $.
    # ─────────────────────────────────────────────────────────────
    snap_w: dict[str, Any] = {}
    snap_w["value_usd"] = round(current_value, 2)

    # P&L semaine : référence = snapshot de la semaine précédente (snapshots[-2]
    # car -1 est celui qu'on vient d'enregistrer à la ligne `record_weekly_snapshot`).
    if len(snapshots) >= 2:
        prev_val = snapshots[-2].get("value_usd")
        if prev_val and prev_val > 0:
            weekly_pnl_usd = current_value - prev_val
            snap_w["weekly_pnl_usd"] = round(weekly_pnl_usd, 2)
            snap_w["weekly_pnl_pct"] = round((weekly_pnl_usd / prev_val) * 100, 2)
            # change_7d_pct/usd : alias compat template (utilisé par l'ancien rendu).
            snap_w["change_7d_pct"] = snap_w["weekly_pnl_pct"]
            snap_w["change_7d_usd"] = snap_w["weekly_pnl_usd"]

    # vs BTC 7j : performance PTF vs performance BTC sur la même fenêtre.
    if len(snapshots) >= 2 and btc_price_now:
        prev_snap = snapshots[-2]
        prev_btc = prev_snap.get("btc_price")
        prev_val = prev_snap.get("value_usd")
        if prev_btc and prev_btc > 0 and prev_val and prev_val > 0:
            ptf_perf = ((current_value - prev_val) / prev_val) * 100
            btc_perf = ((btc_price_now - prev_btc) / prev_btc) * 100
            snap_w["vs_btc_7d_pct"] = round(ptf_perf - btc_perf, 2)

    # Cash USDC.
    cash_value = sum(
        (portfolio[s].get("value_usd") or 0)
        for s in portfolio
        if portfolio[s].get("role") == "cash_reserve"
    )
    if cash_value > 0:
        snap_w["usdc_usd"] = round(cash_value, 2)
        if current_value > 0:
            snap_w["usdc_pct"] = round((cash_value / current_value) * 100, 1)

    # Merge non destructif (préserve ce que Gemini aurait rempli en plus).
    existing_snap = payload.get("portfolio_snapshot") or {}
    for k, v in snap_w.items():
        existing_snap[k] = v  # nos valeurs Python prévalent
    payload["portfolio_snapshot"] = existing_snap

    if _drawdown_change_pts is not None:
        payload.setdefault("portfolio_snapshot", {})["drawdown_change_pts"] = _drawdown_change_pts
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
    # NOUVEAU #4 : calibration confiance vs réalisé (point 3 weekly : TOUJOURS
    # afficher le bloc, message neutre si pas d'historique — la transparence
    # sur l'absence de données est elle-même une info pour l'utilisateur).
    calibration = tracker.compute_calibration(30)
    if calibration.get("available"):
        payload["calibration"] = calibration
    else:
        payload["calibration"] = {
            "available": False,
            "empty_reason": "Pas encore d'historique · calibration disponible après 5 recos clôturées minimum.",
        }
    # NOUVEAU #5 : coût des erreurs (regret)
    regret = tracker.compute_regret(7)
    if regret.get("available"):
        payload["regret"] = regret
    else:
        payload["regret"] = {
            "available": False,
            "empty_reason": "Aucune erreur coûteuse cette semaine · discipline maintenue.",
        }
    # NOUVEAU #11 : bilan des angles morts récurrents (point 4 weekly : TOUJOURS
    # afficher, même message neutre si pas d'historique).
    blind_spots_weekly = mem.compute_blind_spots_weekly()
    if blind_spots_weekly.get("available"):
        payload["blind_spots_weekly"] = blind_spots_weekly
    else:
        payload["blind_spots_weekly"] = {
            "available": False,
            "empty_reason": "Pas encore assez d'historique · bilan disponible après 2 semaines de données.",
        }
    # Header : exposer total_sources_count pour la cohérence weekly (point 8 :
    # "13 / X sources actives cette semaine").
    payload.setdefault("header", {})["total_sources_count"] = len(_ALL_SOURCES_LIST)
    # Pastilles ² (point 6) : statut de fiabilité par crypto pour le rendu weekly.
    if crypto_price_status_w:
        payload["crypto_price_status"] = crypto_price_status_w
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("weekly")
    mem.save_weekly_report(payload)
    html = _render(payload, "weekly")
    ok = send_email(f"\U0001f4ca Bilan hebdo crypto \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Hebdo: %s", ok)
    return 0 if ok else 1


def _render(payload: dict[str, Any], kind: str, charts: dict[str, str] | None = None) -> str:
    """Rend le HTML du rapport selon son type."""
    from src.reporting import email_html
    return email_html.render(payload, kind, charts=charts)


def main() -> int:
    """Point d'entrée CLI."""
    modes = {"morning", "evening", "weekly"}
    if len(sys.argv) < 2 or sys.argv[1] not in modes:
        print("Usage : python -m src.main {morning|evening|weekly}")
        return 2
    mode = sys.argv[1]
    try:
        return {"morning": run_morning, "evening": run_evening,
                "weekly": run_weekly}[mode]()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec fatal mode %s : %s", mode, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
