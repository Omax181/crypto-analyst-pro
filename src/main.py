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
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.analytics.coherence_checker import check_report
from src.analytics.composite_score import composite_score
from src.analytics.fundamentals import compute_ath_distance, fundamental_score_from_signals
from src.analytics.narratives import sector_rotation
from src.analytics.projections import compute_price_projection
from src.analytics.scenarios import compute_scenario_scaffold
from src.analytics.technical import evaluate_technical
from src.analytics.technical_local import compute_local_technical, local_tech_score
from src.analytics.valuation import compute_tradability, compute_valuation
from src.analytics import portfolio_risk as _prisk
from src.analytics.tier_resolver import min_signals_for_firm_reco, resolve_tier
from src.analytics.thesis_scoring import (
    compute_completeness as _thesis_completeness,
    confidence_bounds as _thesis_confidence_bounds,
)
from src.data_sources import (
    coingecko,
    coinmarketcap,
    coinmarketcal,
    binance_futures,
    boursorama_calendar,
    coinglass,
    coinmetrics,
    cryptobubbles,
    crypto_rss,
    deribit,
    newsapi,
    defillama,
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
    EQUITY_CRYPTO_MAP,
    compute_correlation_analysis,
    compute_equity_crypto_links,
    compute_macro_crypto_correlation,
    compute_per_asset_macro_beta,
)
from src.analytics.historical_patterns import compute_setup_stats, relevant_patterns
from src.reporting.email_sender import send_email
from src.state import report_memory as mem
from src.tracking.prediction_scoring import PredictionTracker
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_portfolio

logger = get_logger(__name__)

TZ = ZoneInfo("Africa/Casablanca")

# v23.x — SEUIL DE CONFIANCE des thèses du jour (Omar, 2026-06-29) : toute thèse
# sous ce seuil n'apparaît PAS (filtre anti-bruit) ; ≥ seuil = thèse recommandée.
# Relevé de 60% à 75%. Source de vérité unique du gating d'affichage des thèses ;
# les bornes de confiance par type (thesis_scoring.confidence_bounds) sont alignées
# pour qu'une thèse FORTE de l'un ou l'autre type puisse atteindre ce seuil.
THESIS_CONFIDENCE_FLOOR = 75

# v15 — version UNIQUE du produit : la constante APP_VERSION vit dans
# email_html.py et est injectée dans les 3 footers via le contexte de rendu.
# v20 — l'import « from … import APP_VERSION » dans main.py était MORT (aucun
# consommateur ne fait « from src.main import APP_VERSION ») : retiré pour un
# lint 100% propre. La centralisation de la version reste dans email_html.py.

# Sources interrogées chaque run (catalogue de référence pour l'angle "X / N
# sources actives ce matin" et le bilan hebdo des angles morts). Conserver
# en sync avec ce qui est réellement tenté côté collecte.
_ALL_SOURCES_LIST = [
    "CoinGecko", "Fear&Greed", "FRED", "On-chain", "Polymarket",
    "ETF flows (Farside)", "Telegram", "DeFiLlama", "Kaito", "LunarCrush",
    "Token Unlocks", "News", "YouTube", "Géopolitique", "BTC Network",
    "Stablecoins", "Whale Tracking", "Yahoo Finance", "Calendrier macro",
    "RSS news (crypto + macro · 16 flux)",
    "On-chain avancé (Coin Metrics)", "Options (Deribit)", "Corrélations macro",
    # v14.1 — international + transmission actions → crypto.
    "Marchés internationaux (BCE · BoJ · Nikkei · Stoxx)",
    "Actions ↔ crypto (NVDA · COIN · MSTR…)",
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


def _fr_when(ts: Any) -> Optional[str]:
    """v15 (audit P2) — timestamp ISO brut → libellé FR court et lisible.

    v18 (M-A9) : on N'AFFICHE PLUS « ce matin »/« aujourd'hui » pour une news du
    jour. L'audit a vu « ce matin 02:01 » affiché à 18:33 (news vieille de ~16h)
    → l'étiquette relative faisait croire à du frais. v19/M-A9 : jour même =
    « HH:MM » seul (heure Casablanca) ; hors jour même = date ABSOLUE « DD/MM
    HH:MM » (plus AUCUN « hier »/« avant-hier »). Le lecteur juge la fraîcheur sur
    l'heure réelle, pas un libellé trompeur. Renvoie None si non parsable.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(TZ)
    now = datetime.now(TZ)
    delta_days = (now.date() - local.date()).days
    hhmm = local.strftime("%H:%M")
    if delta_days <= 0:
        return hhmm           # jour même : heure seule, pas de « ce matin »
    # v19/M-A9 : hors jour même → date ABSOLUE « DD/MM HH:MM ». Plus aucun libellé
    # relatif (« hier »/« avant-hier ») qui laissait croire à de la fraîcheur.
    return f"{local.strftime('%d/%m')} {hhmm}"


def _next_report_label(mode: str) -> str:
    """Libellé du prochain rapport (heure Casablanca), sensible à l'heure réelle.

    B9 : un run matinal lancé à 01h ne doit pas annoncer « ce soir 20h » comme
    s'il était 08h. On déduit le prochain créneau réel à partir de l'heure
    courante : créneaux quotidiens à 08h30 (matin) et 20h00 (soir).
    """
    now = datetime.now(TZ)
    h = now.hour + now.minute / 60.0
    morning_slot = "demain 08h30" if h >= 8.5 else "aujourd'hui 08h30"
    evening_slot = "demain 20h00" if h >= 20.0 else "aujourd'hui 20h00"
    if mode == "morning":
        # Après l'envoi du matin, le prochain rapport est celui du soir.
        return evening_slot
    if mode == "evening":
        return morning_slot
    if mode == "weekly":
        # Le hebdo part le dimanche : prochain rapport = matin suivant.
        return morning_slot
    return "prochain créneau"


# B3 — libellés de tier explicites (corrige les « TIER 1/2 » erronés). Tier 0 =
# BTC/ETH (cœur) ; 1 = large caps ; 2 = mid ; 3 = small ; 4 = micro/poussières.
_TIER_LABELS = {
    0: "Tier 0 · cœur (BTC/ETH)",
    1: "Tier 1 · large cap",
    2: "Tier 2 · mid cap",
    3: "Tier 3 · small cap",
    4: "Tier 4 · micro cap",
}


def _onchain_flows_score(onchain: dict[str, Any] | None) -> float | None:
    """Score on-chain RÉEL 0-100 (P0 #54 — remplace la constante 55.0 factice).

    L'ancien ``onchain_flows`` valait ``55.0`` codé en dur pour BTC/ETH (une
    FAUSSE donnée légèrement haussière qui pesait 15% du score composite) et
    ``None`` ailleurs. On dérive ici un score à partir de données on-chain RÉELLES
    (CoinMetrics) : tendance des adresses actives (proxy d'usage/flux) et zone de
    cycle MVRV. ``None`` si aucune donnée réelle (alts non couverts) — honnête,
    plutôt qu'une valeur inventée.
    """
    if not isinstance(onchain, dict) or not onchain:
        return None
    score = 50.0
    used = False
    aa = onchain.get("active_addresses_trend_pct")
    if isinstance(aa, (int, float)):
        score += max(-20.0, min(20.0, float(aa)))
        used = True
    mvrv = onchain.get("mvrv")
    if isinstance(mvrv, (int, float)) and mvrv > 0:
        if mvrv < 1.0:
            score += 10.0
        elif mvrv > 3.5:
            score -= 10.0
        used = True
    if not used:
        return None
    return max(0.0, min(100.0, round(score, 1)))


def _round_num_display(x: Any) -> Any:
    """Arrondit un nombre à une précision LISIBLE selon sa magnitude (v23 C3).

    Empêche les niveaux techniques bruts sur-précis de fuiter dans les mails
    (« 1545.096667 $ », « 0.140035 », « 1715.52836 »). Non-numériques inchangés.
    """
    import math as _m
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return x
    a = abs(x)
    if a == 0 or not _m.isfinite(a):
        return x
    if a >= 1000:
        return round(x)
    if a >= 1:
        return round(x, 2)
    if a >= 0.01:
        return round(x, 4)
    exp = _m.floor(_m.log10(a))
    return round(x, -exp + 3)  # ~4 chiffres significatifs


def _round_levels(obj: Any) -> Any:
    """Applique récursivement ``_round_num_display`` (dicts/listes de niveaux)."""
    if isinstance(obj, dict):
        return {k: _round_levels(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_levels(v) for v in obj]
    return _round_num_display(obj)


def _dedup_theses_by_asset(theses: list) -> list:
    """Garde UNE thèse par actif (v23 — anti-doublon d'affichage).

    Gemini peut émettre deux thèses pour le même actif (constaté sur RENDER
    ré-émis le 29/06 → l'actif apparaissait 2× dans le tableau des thèses du
    matin). La liste reçue est supposée DÉJÀ triée « meilleure en tête » (action
    avant watch, puis confiance décroissante) : on conserve donc la PREMIÈRE
    occurrence de chaque actif et on jette les suivantes. Les éléments non-dict
    ou sans ``asset`` sont laissés tels quels (jamais filtrés à tort).
    """
    seen: set[str] = set()
    out: list = []
    for t in theses:
        asset = ((t.get("asset") or "").strip().upper()
                 if isinstance(t, dict) else "")
        if asset and asset in seen:
            continue
        if asset:
            seen.add(asset)
        out.append(t)
    return out


def _build_asset_signals(
    symbol: str, tier: int, market: dict[str, Any], reddit_sentiment: float,
    news_24h_count: int, sector_change: float | None, derivatives: dict[str, Any],
    onchain: dict[str, Any] | None = None, macro_alignment: float | None = None,
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

    # P0 #57 — FILET TECHNIQUE LOCAL. Recalcule RSI/MACD/Bollinger + divergence
    # depuis l'OHLC déjà chargé (zéro appel réseau). Deux rôles : (a) REPLI du
    # score technique si TradingView est indisponible (IP datacenter/lib/paire) —
    # la dimension technique ne s'effondre plus ; (b) PRODUCTEUR de la divergence
    # prix/RSI consommée par thesis_scoring (signal poids 2 jusque-là mort, faute
    # de source qui le calcule).
    tech_local: dict[str, Any] = {"available": False}
    if price_series_30d:
        tech_local = compute_local_technical(price_series_30d)
        if tech_score is None:
            _local_score = local_tech_score(tech_local)
            if _local_score is not None:
                tech_score = _local_score

    # Fondamental : dev GitHub + tendance TVL DeFiLlama.
    dev = github_dev.get_dev_activity(symbol)
    tvl = defillama.get_protocol_tvl(symbol)
    # v22 (P1 #2-5) — frais/revenus DeFiLlama + valorisation fondamentale
    # (FDV/MC, dilution restante, P/F, P/S, MC/TVL). get_protocol_fees rend
    # {available:False} sans réseau pour les non-DeFi → coût nul pour les alts
    # non couverts.
    fees = defillama.get_protocol_fees(symbol)
    valuation = compute_valuation(market, tvl, fees)
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
        "onchain_flows": _onchain_flows_score(onchain),
        "derivatives": deriv_score,
        "sector_rotation": sector_score,
        "news_24h": news_score if news_24h_count else None,
        "social_sentiment": social if social_active else None,
        "fundamental": fundamental,
        # P0 #55 — alignement macro RÉEL (market-wide, plafonné 40-60 = contexte
        # pas déclencheur) ; était toujours None (5% du composite gaspillés).
        "macro_alignment": macro_alignment,
    }
    score = composite_score(signals)
    return {
        "signals": signals, "score": score, "technical": tech, "dev": dev,
        "tech_advanced": tech_adv, "tech_local": tech_local, "tvl": tvl,
        "fees": fees, "valuation": valuation,
        "social": social_data,
        # CORRECTIF v18.1 : on stocke le compte de news reçu pour que le signal
        # « news récente » de thesis_scoring (asset.get("news_24h_count")) puisse
        # réellement s'allumer — sinon clé absente → signal poids 1 MORT.
        "news_24h_count": news_24h_count,
        "tv_daily": (tv_raw.get("signals", {}) or {}).get("1d", {}),
        "derivatives": derivatives, "price": market.get("price"),
        "change_24h": market.get("change_24h"),
        "change_7d": market.get("change_7d"),
        "change_30d": market.get("change_30d"),
        "volume_24h": market.get("volume_24h"),
        "market_cap": market.get("market_cap"),
        "ath": market.get("ath"),
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
    onchain = onchain_advanced.get_onchain_indicators()
    # v15 — Polymarket ÉTENDU (audit : barres baisse/maintien/hausse, scénario
    # dominant en tête + autres événements majeurs : crypto, récession, géopo).
    polymarket = prediction_markets.get_key_markets()
    # V10 — sources analytiques avancées (gratuites, sans clé, dégradation
    # gracieuse totale : un échec n'affecte aucune autre source ni le pipeline).
    onchain_cm = coinmetrics.get_onchain_metrics()      # MVRV / NVT / realized price
    # v23 — dégèle le MVRV PÉRIMÉ (miroir CSV daté) via le prix live déjà en main
    # (CoinGecko) : MVRV ≈ prix live / realized price. Estimation libellée, aucune
    # source ajoutée. Seul moyen GRATUIT de rafraîchir le MVRV ETH (aucune API ETH
    # on-chain fraîche, keyless, joignable depuis un datacenter n'existe).
    onchain_cm = coinmetrics.apply_live_price_mvrv(
        onchain_cm,
        {s: v.get("price") for s, v in market.items() if isinstance(v, dict)},
    )
    options_deribit = deribit.get_options_metrics()     # put/call · max pain · DVOL
    macro_series = fred.get_macro_series(35)            # séries datées (corrélations)
    calendar_prints = fred.get_calendar_prints()        # derniers chiffres macro publiés
    # v15 — calendrier CONSOLIDÉ (FRED + Boursorama + banques centrales) : le
    # « Aucun événement macro » de l'audit ne peut plus se produire — il y a
    # toujours au moins les décisions FOMC/BoJ officielles + récurrences.
    from src.data_sources import macro_calendar
    upcoming_calendar = macro_calendar.get_consolidated_calendar(horizon_days=10)
    # v22 (#38) — catalyseurs crypto datés (mainnet/listings/upgrades/votes) via
    # CoinMarketCal. Gracieux sans clé (optionnelle) : {available: False}.
    crypto_events = coinmarketcal.get_events(max_events=15)
    etf = etf_flows.get_etf_flows()
    reddit_data = reddit.get_reddit_sentiment()
    reddit_sent = reddit_data.get("sentiment_score", 0.0)
    rotation = sector_rotation(market)

    # P0 #55 — ALIGNEMENT MACRO market-wide (déterministe), calculé UNE fois avant
    # la boucle d'actifs : nourrit le signal composite macro_alignment de chaque
    # actif (jusque-là TOUJOURS None → 5% du poids gaspillés). Plafonné 40-60
    # (« la macro est un contexte, pas un déclencheur ») → ne franchit jamais le
    # seuil de convergence, donc aucune incidence sur l'éligibilité.
    macro_alignment_now: float | None = None
    try:
        from src.analytics.cross_signals import macro_alignment_score as _macro_align
        try:
            _vix_now = (_fred_value(macro, "vix") or {}).get("value")
        except Exception:  # noqa: BLE001
            _vix_now = None
        macro_alignment_now = _macro_align(macro_series, _vix_now)
    except Exception as _maexc:  # noqa: BLE001
        logger.info("macro_alignment ignoré : %s", _maexc)

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
    # CORRECTIF v18.1 — symboles avec un unlock IMMINENT (≤ 7j) : alimente le
    # signal catalyseur ``token_unlock_soon`` de thesis_scoring. Cette clé n'était
    # JAMAIS posée sur l'actif → signal catalyseur poids 2 MORT (même classe que
    # les bugs d'audit). On dérive l'imminence des dates des unlocks réels.
    _imminent_unlock_syms: set[str] = set()
    if isinstance(unlocks, dict) and unlocks.get("available"):
        _today = datetime.now(TZ).date()
        for _u in (unlocks.get("unlocks") or []):
            try:
                _ud = datetime.strptime(str(_u.get("date") or ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if 0 <= (_ud - _today).days <= 7:
                _imminent_unlock_syms.add(str(_u.get("symbol") or "").upper())
    youtube_corpus = youtube.get_youtube_corpus()
    geopol = geopolitics.get_geopolitics()
    # V6 : santé réseau BTC (hashrate/difficulty), flux stablecoins, whale tracking.
    btc_network = onchain_btc.get_btc_onchain()
    stablecoin_supply = stablecoins.get_stablecoin_supply()
    whale_inflows = whale_tracker.get_exchange_inflows()
    # v18 (Chantier E #5) — mouvements des wallets stratégiques connus (best-effort).
    try:
        _strategic_wallets = whale_tracker.get_strategic_wallet_activity()
    except Exception as _swexc:  # noqa: BLE001
        logger.info("Wallets stratégiques ignorés : %s", _swexc)
        _strategic_wallets = {"available": False}
    boursorama_cal = boursorama_calendar.get_boursorama_calendar()
    # Crypto Bubbles : top mouvements du marché + focus PTF (source complémentaire).
    market_movers = cryptobubbles.get_market_movers(symbols, top_n=3)  # v16 : 3+3, MarketCap>50M

    enriched: dict[str, dict[str, Any]] = {}
    eligible: list[dict[str, Any]] = []
    # Clôtures datées par actif (éligibles) : réutilisées pour l'analyse
    # historique (A11) ET la corrélation/bêta macro par actif (A5/C8).
    asset_dated_closes: dict[str, dict[str, float]] = {}
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
        # P0 #54 — on-chain RÉEL de l'actif (CoinMetrics : MVRV, tendance adresses
        # actives), passé pour calculer un vrai score onchain_flows au lieu de la
        # constante 55.0 factice. Vide pour les alts non couverts → None honnête.
        _onchain_sym = (((onchain_cm.get("assets") or {}).get(sym) or {})
                        if isinstance(onchain_cm, dict) else {})
        asset = _build_asset_signals(
            sym, tier, market.get(sym, {}), reddit_sent, news_counts.get(sym, 0),
            sector_change, derivatives, onchain=_onchain_sym,
            macro_alignment=macro_alignment_now,
        )
        asset["tier"] = tier
        asset["value_usd"] = _position_value(info, market.get(sym))
        enriched[sym] = asset
        needed = min_signals_for_firm_reco(tier)
        sig_count = asset["score"]["signals_count"]
        change = abs(asset.get("change_24h") or 0)
        # Éligibilité standard : signaux >= seuil du tier.
        is_eligible = needed < 999 and sig_count >= needed
        # B2 — Les positions cœur (Tier 0 : BTC/ETH) sont TOUJOURS candidates dès
        # 2 signaux convergents, même sans gros mouvement 24h : l'agent ne doit
        # jamais ignorer ses actifs principaux (libre à l'analyse de conclure
        # RENFORCER / ALLÉGER / SURVEILLER). Tier 1 : seuil abaissé si 2 signaux
        # + mouvement notable, ou 3 signaux.
        if not is_eligible and tier == 0 and sig_count >= 2:
            is_eligible = True
        if not is_eligible and tier == 1 and (
            (sig_count >= 2 and change >= 3.0) or sig_count >= 3
        ):
            is_eligible = True
        # v18 (Chantier F — SCORING PONDÉRÉ) : en plus du comptage ci-dessus, on
        # évalue un score PONDÉRÉ multi-dimensions. Un seul signal fondamental LT
        # fort (MVRV < 1 + sous PRU) peut suffire à rendre éligible — c'est ainsi
        # qu'on capte les meilleures entrées d'accumulation, qui arrivent dans le
        # CALME (pas de news, pas de mouvement). On enrichit aussi l'actif avec le
        # type (tactique/conviction) et les signaux pour le prompt.
        try:
            from src.analytics import thesis_scoring as _tsc
            # On-chain CoinMetrics de l'actif (MVRV, tendance adresses actives).
            # _build_asset_signals ne pose PAS de clé "onchain" — on l'attache ici
            # depuis onchain_cm pour que la divergence prix/fondamentaux et le MVRV
            # soient réellement évalués (sinon signaux morts).
            _cm_sym = (((onchain_cm.get("assets") or {}).get(sym) or {})
                       if isinstance(onchain_cm, dict) else {})
            if _cm_sym and not asset.get("onchain"):
                asset["onchain"] = _cm_sym
            # Put/call ratio : il vient des options Deribit (BTC/ETH), pas de
            # l'on-chain. _build_asset_signals ne le pose pas non plus.
            _opt_sym = (((options_deribit.get("assets") or {}).get(sym) or {})
                        if isinstance(options_deribit, dict) else {})
            _put_call = _opt_sym.get("put_call_ratio")
            # v18.1 — unlock imminent (≤7j) sur cet actif → catalyseur (poids 2).
            asset["token_unlock_soon"] = sym.upper() in _imminent_unlock_syms
            _info = portfolio[sym]
            _pru = _info.get("pru")
            _pru_gap = None
            _price_now = (market.get(sym) or {}).get("price")
            if (isinstance(_pru, (int, float)) and isinstance(_price_now, (int, float))
                    and _pru):
                _pru_gap = (_price_now - _pru) / _pru * 100
            _mvrv_sym = _cm_sym.get("mvrv")
            _cat_days = None
            for _ev in (upcoming_calendar.get("events") or []):
                _da = _ev.get("days_ahead")
                if isinstance(_da, (int, float)) and 0 <= _da <= 7:
                    _cat_days = _da
                    break
            _sec7 = None
            for _sd in sectors.values():
                if sym in _sd.get("members", []):
                    _sec7 = _sd.get("avg_change_7d")
                    break
            # Structure de marché D1 calculée depuis l'historique de prix de
            # l'actif (le signal était inerte avant : _struct toujours None car
            # cross_signals n'est calculé qu'après cette boucle).
            _struct = None
            _closes_struct = asset.get("price_series_30d")
            if isinstance(_closes_struct, list) and len(_closes_struct) >= 7:
                try:
                    from src.analytics.cross_signals import _swing_structure
                    _struct = _swing_structure(_closes_struct)
                except Exception:  # noqa: BLE001
                    _struct = None
            _deriv = asset.get("derivatives") or {}
            _fng_val = fng.get("value") if isinstance(fng, dict) and fng.get("available") else None
            _thesis_eval = _tsc.evaluate_thesis_eligibility(
                asset, tier=tier,
                mvrv=_mvrv_sym, pru_gap_pct=_pru_gap,
                drawdown_from_ath_pct=asset.get("ath_distance_pct"),
                upcoming_catalyst_days=_cat_days,
                token_unlock_soon=bool(asset.get("token_unlock_soon")),
                sector_change_7d=_sec7,
                funding_annualized_pct=_deriv.get("funding_annualized_pct"),
                fear_greed=_fng_val,
                put_call_ratio=_put_call,
                market_structure=_struct,
            )
            asset["thesis_eval"] = _thesis_eval
            if _thesis_eval["eligible"]:
                is_eligible = True
        except Exception as _tscexc:  # noqa: BLE001 — ne jamais bloquer le scan
            logger.info("thesis_scoring %s ignoré : %s", sym, _tscexc)
        # v21 (#73) — GATE DE CONVERGENCE : quelle que soit la voie qui a rendu
        # l'actif éligible (comptage legacy OU score pondéré), une thèse exige la
        # convergence d'au moins 2 familles de signaux (ou un cluster fondamental
        # LT fort). Supprime les recos d'alts déclenchées sur une dimension unique.
        # Le cœur Tier 0 (BTC/ETH) reste candidat d'office (B2) : l'analyse ne doit
        # jamais ignorer les actifs principaux ; l'action y est laissée au jugement.
        _te = asset.get("thesis_eval")
        if (is_eligible and tier != 0 and _te is not None
                and not _te.get("convergent", True)):
            is_eligible = False
        if is_eligible:
            ta = asset.get("tech_advanced") or {}
            # B1 — DÉTAIL TECHNIQUE POUR TOUTE THÈSE ÉLIGIBLE. Si l'actif n'a pas
            # reçu d'OHLC profond au scan (Tier 2-3 sans gros mouvement), on le
            # récupère MAINTENANT : une thèse recommandée doit toujours porter
            # des niveaux chiffrés (RSI/MACD/Bollinger/SR/Fib), jamais « non
            # disponible ». Coût maîtrisé : seules les ~3-5 thèses éligibles.
            closes_for_hist: list[float] = asset.get("price_series_30d") or []
            if not ta.get("available"):
                ta = technical_advanced.get_technical_advanced(sym)
                asset["tech_advanced"] = ta
            if len(closes_for_hist) < 35:
                # Historique plus long (90j) pour des stats chartistes fiables
                # ET la corrélation/bêta macro par actif (réutilisé, caché).
                dated = coingecko.get_dated_closes(sym, 95)
                if dated:
                    asset_dated_closes[sym] = dated
                    closes_for_hist = [dated[d] for d in sorted(dated)]
            elif asset.get("price_series_30d"):
                # série 30j présente : on tente quand même les clôtures datées
                # pour la corrélation macro (alignée par date).
                dated = coingecko.get_dated_closes(sym, 95)
                if dated:
                    asset_dated_closes[sym] = dated
                    if len(dated) > len(closes_for_hist):
                        closes_for_hist = [dated[d] for d in sorted(dated)]
            # V10 — détail technique compact (valeurs brutes : RSI/MACD/Stoch/
            # ADX/SMA cross/Bollinger/SR + signaux qui flashent).
            technical_detail = digests.build_asset_technical(
                asset.get("tv_daily") or {}, ta
            )
            # A11 — analyse historique chartiste RÉELLE (stats calculées sur OHLC).
            historical_stats = compute_setup_stats(
                closes_for_hist, asset.get("change_24h"), forward_days=7
            )
            # P0 #53 — complétude de l'analyse (calculée une fois).
            _completeness = (
                _thesis_completeness(asset) if asset.get("thesis_eval") else None
            )
            entry = {
                "asset": sym, "tier": tier,
                "tier_label": _TIER_LABELS.get(tier, f"Tier {tier}"),
                "signals_count": asset["score"]["signals_count"],
                "bullish_count": asset["score"]["bullish_count"],
                "bearish_count": asset["score"]["bearish_count"],
                "composite": asset["score"]["total"],
                "price": asset["price"],
                # v23 (M5) — valeur de position LIVE (qté × prix live), pour que la
                # thèse ne reprenne plus le fallback périmé de portfolio.yaml.
                "value_usd": asset.get("value_usd"),
                "change_24h": asset["change_24h"],
                "ath_distance_pct": asset["ath_distance_pct"],
                "market_cap": asset.get("market_cap"),
                "ath": asset.get("ath"),
                "technical_signal": asset["technical"].get("dominant_signal"),
                # v23 (C3) — niveaux arrondis à une précision lisible (anti
                # « 1545.096667 $ » recopié tel quel par le LLM).
                "technical_detail": _round_levels(technical_detail),
                "historical_stats": historical_stats,
                "signals_detail": asset["score"]["components"],
                "fibonacci": _round_levels(ta.get("fibonacci")) if ta.get("available") else None,
                "bollinger": _round_levels(ta.get("bollinger")) if ta.get("available") else None,
                "support_resistance": _round_levels(ta.get("support_resistance")) if ta.get("available") else None,
                "moving_averages": _round_levels(ta.get("moving_averages")) if ta.get("available") else None,
                "tvl": asset.get("tvl") if asset.get("tvl", {}).get("available") else None,
                # v22 (P1) — valorisation fondamentale (FDV/MC, dilution, P/F, P/S, MC/TVL).
                "valuation": asset.get("valuation") if asset.get("valuation", {}).get("available") else None,
                # v22 (P3 #48) — tradabilité (liquidité) = garde-fou de TAILLE.
                "tradability": compute_tradability(asset.get("volume_24h"), asset.get("value_usd")),
                "social": asset.get("social") if asset.get("social", {}).get("available") else None,
                "dev_activity": asset.get("dev") if asset.get("dev", {}).get("available") else None,
                # v22 (audit signal mort) — le prompt référence
                # data.eligible_theses[].derivatives (funding + OKX long_short_ratio) :
                # ce champ n'était JAMAIS posé sur l'entrée → référence morte. On
                # l'expose (funding/OI/long-short réels par actif pour le raisonnement).
                "derivatives": asset.get("derivatives") if asset.get("derivatives", {}).get("available") else None,
                # v18 (Chantier F) — scoring pondéré multi-dimensions : type de
                # thèse suggéré (tactique/conviction), score pondéré, signaux
                # détaillés par catégorie, et bornes de confiance honnêtes.
                # P0 #53 — complétude de l'analyse (% de dimensions réelles
                # disponibles) → plafonne la confiance (verrou anti-reco à trous).
                "thesis_scoring": (
                    {
                        **asset["thesis_eval"],
                        "completeness": _completeness,
                        "confidence_bounds": _thesis_confidence_bounds(
                            asset["thesis_eval"]["thesis_type"],
                            asset["thesis_eval"]["dimensions_count"],
                            _completeness["pct"] if _completeness else None,
                        ),
                    }
                    if asset.get("thesis_eval") else None
                ),
            }
            # v23.x (deepthink projections) — ÉCHAFAUDAGE DE PROJECTION ancré sur
            # des niveaux réels + volatilité réelle (ATR). Le LLM s'y ANCRE pour
            # remplir targets.short_term_30d / long_term_* (au lieu d'inventer) ;
            # un garde-fou (_merge_python_facts) ramène toute cible 30j aberrante.
            entry["projection"] = _round_levels(compute_price_projection(
                asset["price"],
                support_resistance=ta.get("support_resistance") if ta.get("available") else None,
                fibonacci=ta.get("fibonacci") if ta.get("available") else None,
                bollinger=ta.get("bollinger") if ta.get("available") else None,
                moving_averages=ta.get("moving_averages") if ta.get("available") else None,
                ath=asset.get("ath"),
                ath_distance_pct=asset.get("ath_distance_pct"),
                atr_pct=(asset.get("tech_local") or {}).get("atr_pct"),
                change_30d=asset.get("change_30d"),
            ))
            # On-chain avancé + options pour les actifs couverts (BTC/ETH).
            cm_asset = (onchain_cm.get("assets") or {}).get(sym)
            if cm_asset:
                entry["onchain_advanced"] = cm_asset
            opt_asset = (options_deribit.get("assets") or {}).get(sym)
            if opt_asset:
                entry["options"] = opt_asset
            # P0 #59 — FRAÎCHEUR NORMALISÉE par bloc (anti-mélange live/différé) :
            # l'on-chain peut être différé (miroir daté) alors que prix/technique/
            # dérivés sont live. On expose un as_of explicite pour que l'analyse ne
            # fonde pas un « signal du jour » sur une métrique vieille de 3 semaines.
            _oc_entry = asset.get("onchain") or {}
            entry["data_freshness"] = {
                "onchain_as_of": _oc_entry.get("as_of") or _oc_entry.get("time"),
                "onchain_source": _oc_entry.get("mvrv_source") or _oc_entry.get("source"),
                "technical": (
                    "live" if ((asset.get("technical") or {}).get("score") is not None
                               or (asset.get("tech_local") or {}).get("available"))
                    else None
                ),
                "derivatives": (
                    "live" if (asset.get("derivatives") or {}).get("available") else None
                ),
                "price": "live" if asset.get("price") is not None else None,
            }
            eligible.append(entry)
    eligible.sort(key=lambda e: (e["tier"], -e["signals_count"]))

    # v19/M-A18 — la liste ▲/▼ « Tes positions en hausse/baisse » doit être
    # COHÉRENTE avec la heatmap (même source CoinGecko, enriched) pour que les
    # positions EN BAISSE apparaissent TOUJOURS. cryptobubbles ne couvre pas tous
    # les tokens → le bloc ▼ pouvait rester vide à tort. On reconstruit donc
    # portfolio_movers depuis les positions réellement enrichies.
    if isinstance(market_movers, dict):
        _pm_full = [
            {"symbol": s, "change_24h": e.get("change_24h"),
             "value_usd": e.get("value_usd")}
            for s, e in enriched.items()
            if isinstance(e.get("change_24h"), (int, float))
            and (e.get("value_usd") or 0) > 0
        ]
        _pm_full.sort(key=lambda c: abs(c["change_24h"]), reverse=True)
        if _pm_full:
            market_movers["portfolio_movers"] = _pm_full[:12]

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
    # (DXY/S&P/VIX/10Y). 1 appel CoinGecko (caché 30min) pour aligner par date.
    # 95j (v22) : sert AUSSI la force relative 90j et la VaR du portefeuille ;
    # macro_correlations reste borné par macro_series (35j) à l'alignement.
    btc_dated = coingecko.get_dated_closes("BTC", 95)
    macro_correlations = compute_macro_crypto_correlation(btc_dated, macro_series, window=30)
    # A5/C8 — bêtas PAR ACTIF vs facteurs macro (DXY/S&P/VIX). Réutilise les
    # clôtures datées déjà récupérées pour les thèses éligibles (aucun appel
    # supplémentaire) + BTC. Remplit le champ « β » des positions exposées.
    _beta_inputs = dict(asset_dated_closes)
    if btc_dated:
        _beta_inputs.setdefault("BTC", btc_dated)
    per_asset_beta = compute_per_asset_macro_beta(
        _beta_inputs, macro_series, window=30, factors=("dxy", "sp500", "vix")
    )

    # v22 (P1 #20/#42/#44/#45/#46) — RISQUE PORTEFEUILLE consolidé (bêta-BTC par
    # position, concentration HHI / nombre effectif de paris, stress-test
    # « BTC −20% », VaR 95%) + FORCE RELATIVE vs BTC par actif éligible. Réutilise
    # les clôtures datées déjà chargées (aucun appel réseau supplémentaire).
    portfolio_risk = _prisk.compute_portfolio_risk(
        _beta_inputs, btc_dated, position_values
    )
    if btc_dated:
        for _e in eligible:
            _ad = _beta_inputs.get(_e.get("asset"))
            if _ad and _e.get("asset") != "BTC":
                _rs = _prisk.relative_strength_vs_btc(_ad, btc_dated)
                if _rs.get("available"):
                    _e["relative_strength"] = _rs

    # A6 — exposition sectorielle du portefeuille (poids PTF par secteur, et
    # perf 24h moyenne du secteur côté marché). Données factuelles pour le hebdo.
    sector_exposure = _compute_sector_exposure(enriched, rotation)



    # v14.1 — INTERNATIONAL + ACTIONS ↔ CRYPTO -------------------------------
    # Prix macro temps réel Yahoo (valeurs ET deltas vs clôture précédente,
    # même fetch caché) — prioritaires sur FRED pour les actifs cotés en
    # continu. Récupérés ICI (avant _active_sources) pour alimenter le flag
    # « Marchés internationaux ».
    yahoo_quotes = market_prices.get_macro_quotes()
    yahoo_deltas = market_prices.get_macro_deltas()
    # Actions liées crypto (NVDA, COIN, MSTR, AMD, TSM, MARA) : quotes live +
    # corrélations 30j avec les positions du PTF concernées (bloc IA/GPU +
    # proxys BTC). Les clôtures datées crypto réutilisent celles déjà chargées
    # pour les thèses (asset_dated_closes/BTC) ; seules les paires manquantes
    # déclenchent un appel CoinGecko (caché 30 min, ~2-4 appels max).
    equity_quotes = market_prices.get_equity_quotes()
    equity_dated = market_prices.get_equity_dated_closes(days=95)
    equity_crypto_links: dict[str, Any] = {"available": False}
    if equity_dated:
        link_crypto_dated: dict[str, dict[str, float]] = {}
        for _eq, _cr, _ in EQUITY_CRYPTO_MAP:
            if _cr in link_crypto_dated:
                continue
            dated = _beta_inputs.get(_cr) or coingecko.get_dated_closes(_cr, 35)
            if dated:
                link_crypto_dated[_cr] = dated
        equity_crypto_links = compute_equity_crypto_links(
            equity_dated, link_crypto_dated, window=30
        )

    tracker = PredictionTracker()
    price_lookup = {s: enriched[s].get("price") for s in enriched}
    active_recos = tracker.refresh_active(price_lookup)
    # v17 (T-DEDUP / M-A2) : version dédupliquée + enrichie pour le rendu matin
    # (1 ligne/actif, entry/Δ/cible) — le brut active_recos garde les doublons
    # legacy et sert aux calculs internes (_positions_summary, etc.).
    active_recos_display = tracker.active_for_display(price_lookup)
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
        intl_markets=bool(
            yahoo_quotes.get("nikkei") or yahoo_quotes.get("stoxx50")
            or yahoo_quotes.get("dax")
        ),
        equity_links=bool(equity_quotes) and equity_crypto_links.get("available", False),
    )

    # Enregistre la santé des sources (alimente le bilan hebdo des angles morts).
    _ALL_SOURCES = _ALL_SOURCES_LIST  # alias local pour compat héritée
    try:
        mem.record_source_health(_ALL_SOURCES, active_sources)
    except Exception as exc:  # noqa: BLE001
        logger.info("record_source_health ignoré : %s", exc)

    # Portfolio snapshot calculé côté Python (Gemini n'a pas à l'inventer).
    snapshot = _portfolio_snapshot(portfolio, enriched)
    # v17 (T-7J / M-A7) : aligner la perf 7j du matin sur la MÊME méthode que le
    # weekly (variation de la valeur PTF vs snapshot d'il y a ~7j), quand un
    # snapshot hebdo est disponible. Évite les 3 valeurs 7j divergentes entre
    # matin et weekly. Repli sur la méthode somme-des-deltas si pas de snapshot.
    try:
        _snaps = mem.load_weekly_snapshots()
        _cur_val = snapshot.get("value_usd")
        if _snaps and isinstance(_cur_val, (int, float)) and _cur_val > 0:
            _prev = _snaps[-1]
            _prev_val = _prev.get("value_usd")
            _prev_btc = _prev.get("btc_price")
            if isinstance(_prev_val, (int, float)) and _prev_val > 0:
                _snap_7d_pct = round((_cur_val - _prev_val) / _prev_val * 100, 2)
                snapshot["change_7d_pct"] = _snap_7d_pct
                snapshot["change_7d_usd"] = round(_cur_val - _prev_val, 2)
                _btc_now = (enriched.get("BTC") or {}).get("price")
                if isinstance(_prev_btc, (int, float)) and _prev_btc > 0 and isinstance(_btc_now, (int, float)):
                    _btc_7d = (_btc_now - _prev_btc) / _prev_btc * 100
                    snapshot["vs_btc_7d_pct"] = round(_snap_7d_pct - _btc_7d, 2)
    except Exception as _exc:  # noqa: BLE001
        logger.info("Alignement 7j matin/weekly ignoré : %s", _exc)
    # A2 — P&L NUIT FIABILISÉ. La baseline est la valeur du DERNIER rapport du
    # soir, mais uniquement s'il est RÉCENT (< 18h) ET que le mouvement implicite
    # est PLAUSIBLE (|Δ| <= 25% : un PTF crypto ne bouge pas de 40% en une nuit ;
    # un tel écart trahit une baseline périmée, pas une vraie perf). Sinon n/d —
    # jamais de faux « P&L nuit ». (Corrige le −40.9% dû à un soir périmé.)
    _ev_report = mem.load_evening_report() or {}
    _ev_prev = _ev_report.get("portfolio_snapshot") or {}
    _ev_prev_val = _ev_prev.get("value_usd")
    _ev_fresh = _evening_report_is_fresh(_ev_report, max_age_hours=18)
    snapshot["overnight_pnl_usd"] = None
    snapshot["overnight_pnl_pct"] = None
    if (
        _ev_fresh
        and isinstance(_ev_prev_val, (int, float)) and _ev_prev_val
        and snapshot.get("value_usd")
    ):
        _overnight = snapshot["value_usd"] - _ev_prev_val
        _pct = _overnight / _ev_prev_val * 100
        # v23 (M1) — COHÉRENCE avec le 24h. La nuit (~12-14h) est un SOUS-ensemble
        # du 24h : un « P&L nuit » de signe OPPOSÉ au 24h et d'amplitude notable
        # trahit une baseline soir périmée (ex. +5.0% nuit alors que 24h = −0.6% au
        # 1er run après déploiement). Dans ce cas on masque plutôt que d'afficher
        # un chiffre faux et trompeur (en vert proéminent).
        _ch24 = snapshot.get("change_24h_pct")
        _incoherent = (
            isinstance(_ch24, (int, float))
            and (_pct * _ch24 < 0)      # signes opposés nuit vs 24h
            and abs(_pct) > 2.0          # amplitude nuit notable
        )
        if abs(_pct) <= 25.0 and not _incoherent:  # plausibilité + cohérence 24h
            snapshot["overnight_pnl_usd"] = round(_overnight, 2)
            snapshot["overnight_pnl_pct"] = round(_pct, 2)
    # Macro context : valeurs chiffrées injectées directement (yahoo_quotes /
    # yahoo_deltas déjà récupérés plus haut — même cache, zéro appel en plus).
    macro_context = _macro_context(
        market, fng, macro, polymarket, yahoo_quotes, yahoo_deltas
    )
    # v18 (Chantier E / Partie 4) — SIGNAUX D'ANALYSE TRANSVERSES déterministes,
    # injectés dans data pour nourrir l'analyse de Gemini (allocation vs cibles,
    # liquidité M2, cycle DXY, spreads HY, saisonnalité, régime de vol réalisée,
    # structure de marché HH/HL, biais de confirmation, MVRV, divergence
    # prix/fondamentaux, mémoire des contextes). Pur Python, dégrade proprement.
    try:
        from src.analytics import cross_signals as _xsig
        _btc_mvrv = (
            ((onchain_cm.get("assets") or {}).get("BTC") or {}).get("mvrv")
            if isinstance(onchain_cm, dict) else None
        )
        _recent_theses = (mem.load_recent_theses(limit=12)
                          if hasattr(mem, "load_recent_theses") else None)
        _focus = [s for s, a in enriched.items()
                  if (a.get("value_usd") or 0) > 0]
        # #2 — DVOL BTC (move implicite des options) ; #14 — dérivés par actif
        # (funding) depuis les positions enrichies.
        _btc_dvol = (
            ((options_deribit.get("assets") or {}).get("BTC") or {}).get("dvol")
            if isinstance(options_deribit, dict) else None
        )
        _derivs_by_asset = {
            s: (a.get("derivatives") or {})
            for s, a in enriched.items()
            if isinstance(a.get("derivatives"), dict)
            and a["derivatives"].get("available")
        }
        cross_signals_block = _xsig.compute_all(
            macro_series,
            price_series,
            weights=position_values,
            focus_assets=_focus,
            recent_theses=_recent_theses,
            mvrv_value=_btc_mvrv,
            portfolio=portfolio_data,
            enriched=enriched,
            onchain_assets=(onchain_cm.get("assets") if isinstance(onchain_cm, dict) else None),
            market=market,
            current_macro=macro_context,
            snapshots=mem.load_weekly_snapshots(),
            dvol=_btc_dvol,
            upcoming_events=upcoming_calendar.get("events") if isinstance(upcoming_calendar, dict) else None,
            derivatives_by_asset=_derivs_by_asset,
            sector_rotation=rotation,
            holdings_sectors=[
                _s.get("sector")
                for _s in (sector_exposure.get("sectors") or [])
                if isinstance(_s, dict) and _s.get("sector")
            ] if isinstance(sector_exposure, dict) else None,
            strategic_wallets=_strategic_wallets,
            global_market=glob,            # v22 #22 — contexte dominance/altseason
        )
    except Exception as _xexc:  # noqa: BLE001 — l'analyse ne doit jamais bloquer
        logger.info("cross_signals ignoré : %s", _xexc)
        cross_signals_block = {"signals": {}, "readings": []}
    # Statut de fiabilité par métrique macro (pastilles : vert si Yahoo+FRED
    # concordent, orange si une seule source).
    macro_source_status = market_prices.compute_macro_source_status(
        macro_context, yahoo_quotes, (macro or {}).get("series"), tolerance_pct=10.0
    )
    # C1 — contradictions de données détectées (avec tolérance). Renseigne une
    # note de bas de page discrète (²) seulement en cas d'écart marqué.
    data_contradictions = _detect_data_contradictions(macro_context)

    # V10 — DIGEST ANALYTIQUE COMPACT : lignes condensées (économie de tokens)
    # que l'IA exploite pour un raisonnement CROISÉ. Chaque ligne est vide si la
    # source correspondante est indisponible (jamais d'invention).
    analytics_digest = {
        "macro_correlations": digests.macro_correlation_line(macro_correlations),
        "macro_calendar": digests.calendar_line(
            calendar_prints, polymarket, upcoming_calendar
        ),
        "per_asset_beta": digests.per_asset_beta_line(per_asset_beta),
        "onchain_advanced": digests.onchain_line(onchain_cm),
        "options": digests.options_line(options_deribit),
        "feedback": digests.feedback_line(per_asset_perf),
        # v14.1 — transmission actions → crypto (NVDA↔RENDER…), 1 ligne dense.
        "equity_crypto": digests.equity_crypto_line(equity_crypto_links, equity_quotes),
    }

    # B5 — score de risque PTF synthétique (conservé pour le bot /risque).
    risk_score = _compute_portfolio_risk_score(
        snapshot, sector_exposure, macro_context, enriched, portfolio
    )
    # v23 — note de SANTÉ PTF (affichée dans les mails à la place du risque).
    health_score = _compute_portfolio_health(snapshot, sector_exposure)

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
        # V11 — données analytiques structurées (au-delà des digests texte).
        "per_asset_beta": per_asset_beta,        # A5/C8 — bêtas par actif vs macro
        "macro_correlations": macro_correlations,  # B7 — corrélations BTC↔macro
        "onchain_advanced": onchain_cm,          # B7/A4 — MVRV/NVT (CoinMetrics)
        "options_deribit": options_deribit,      # B7/A3 — put/call · max pain · DVOL
        "sector_exposure": sector_exposure,      # A6 — exposition sectorielle PTF
        # v18 (Chantier E) — signaux d'analyse transverses (Partie 4 de l'audit).
        "cross_signals": cross_signals_block,
        "risk_score": risk_score,                # B5 — score de risque PTF (0-10, bot)
        "health_score": health_score,            # v23 — note SANTÉ PTF (mails)
        "upcoming_calendar": upcoming_calendar,  # A10/C6 — prochaines publications
        "crypto_events": crypto_events,          # v22 #38 — catalyseurs crypto datés
        "data_contradictions": data_contradictions,  # C1 — incohérences détectées
        "historical_context": relevant_patterns({
            "fear_greed": macro_context.get("fear_greed"),
            "dxy_up": (macro_context.get("dxy_delta") or 0) > 0,
        }),                                      # A11 — patterns macro indicatifs
        "reco_evolution_30d": tracker.compute_per_asset_performance(30),  # C3
        "market_global": glob, "fear_greed": fng, "macro": _sanitize_macro_for_prompt(macro),
        "economic_calendar": upcoming_calendar, "onchain_indicators": onchain,
        "polymarket": polymarket, "etf_flows": etf, "reddit": reddit_data,
        "telegram": telegram, "defi_tvl": defi, "kaito_narratives": narratives,
        "social_trending": social_trending, "token_unlocks": unlocks,
        "sector_rotation": rotation, "news_counts": news_counts,
        "news_24h_global": news_global_items[:12],
        "macro_news": macro_news_items[:12],
        "boursorama_calendar": boursorama_cal,
        "market_movers": market_movers,
        # v14.1 — actions liées crypto : quotes live + liens chiffrés 30j.
        "equity_quotes": equity_quotes,
        "equity_crypto_links": equity_crypto_links,
        "youtube_corpus": youtube_corpus, "geopolitics": geopol,
        "btc_network": btc_network, "stablecoin_supply": stablecoin_supply,
        "whale_inflows": whale_inflows, "position_correlation": correlation,
        "portfolio_risk": portfolio_risk,        # v22 (P1) — risque PTF consolidé
        "active_sources": active_sources,
        "eligible_theses": eligible, "active_recommendations": active_recos,
        "active_recommendations_display": active_recos_display,
        "reco_changes": reco_changes,
        "win_rate": win_rate,
        # v19/W-B12 — espérance mathématique des recos AUSSI dans le matin (et le
        # soir), pas seulement le weekly. Vide tant que < 5 recos clôturées.
        "expectancy": tracker.compute_expectancy(30),
        "all_positions_summary": _positions_summary(enriched, active_recos),
        "portfolio_heatmap": _portfolio_heatmap(enriched),
        # v15 (audit P1-6) — positions du PTF à mouvement 24h > ±10% (NOT
        # +10,8% passait sous silence). Injecté au prompt avec une RÈGLE :
        # chaque entrée DOIT être commentée (thèse ou ligne dédiée), quel que
        # soit le tier. Dust < 10 $ exclu.
        "ptf_big_movers_24h": sorted(
            (
                {"symbol": s, "change_24h": round(e.get("change_24h") or 0, 1),
                 "tier": e.get("tier"), "value_usd": round(e.get("value_usd") or 0, 2)}
                for s, e in enriched.items()
                if isinstance(e.get("change_24h"), (int, float))
                and abs(e["change_24h"]) >= 10
                and (e.get("value_usd") or 0) >= 10
            ),
            key=lambda m: abs(m["change_24h"]), reverse=True,
        ),
        "blind_spots": _blind_spots(
            onchain, polymarket, etf, telegram, defi,
            macro_flags=list(_macro_validation_flags),
            price_discrepancies=price_discrepancies,
            price_divergences=price_divergences,
            degraded_sources=[s for s in _ALL_SOURCES_LIST
                              if s not in set(active_sources)],
        ),
        # v19/V18-M10/M-A20 — sources INDISPONIBLES nommées (pour le footer), au
        # lieu d'un simple « 21/25 » que l'utilisateur doit déchiffrer ailleurs.
        "down_sources": [s for s in _ALL_SOURCES_LIST
                         if s not in set(active_sources)],
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


def _portfolio_heatmap(
    enriched: dict[str, dict[str, Any]], change_key: str = "change_24h"
) -> dict[str, Any]:
    """Construit une heatmap des positions, triée par AMPLEUR DE MOUVEMENT.

    v23.x — ``change_key`` choisit la fenêtre de perf : ``change_24h`` (morning,
    défaut) ou ``change_7d`` (weekly). La valeur retenue est stockée sous le champ
    ``change_24h`` de chaque case (le « slot perf » lu par le template) — le LIBELLÉ
    de fenêtre (24h vs 7j) est porté par le template, pas par la donnée.

    v18 (M-B17) — l'ordre n'est PLUS le poids dans le PTF mais le % d'évolution
    24h (|change| décroissant), comme la rotation sectorielle : on met en avant
    ce qui BOUGE le plus (hausse ou baisse), pas ce qui pèse le plus.

    v23.x — grille de 5 COLONNES × 4 LIGNES = 20 cases (Omar : 4 lignes au lieu
    de 3). Au-delà de 20 positions : 19 plus gros mouvements + 1 case « +N autres »
    (positions calmes, moyenne pondérée) = 20 cases pile. ≤ 20 positions : tout
    afficher. Chaque case : symbole, perf 24h, poids PTF.

    Returns:
        Dict ``{cells: [{symbol, value_usd, change_24h, ptf_pct}] (max 20),
        total_count, remaining, extra}``.
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
                "change_24h": e.get(change_key),  # slot perf (24h morning / 7j weekly)
            }
        )
    total = len(cells)
    _total_val = sum(c["value_usd"] for c in cells) or 1.0
    # v16 — % PTF par position (poids), pour la 3e info de chaque case.
    for c in cells:
        c["ptf_pct"] = round(c["value_usd"] / _total_val * 100, 1)
    # v18 (M-B17) : tri par |variation 24h| DÉCROISSANTE (les plus gros mouvements
    # d'abord). Une position sans change connu est traitée comme mouvement nul
    # (donc reléguée vers l'agrégat). À |variation| égale, le poids départage
    # (ordre stable).
    def _mv(c: dict[str, Any]) -> float:
        ch = c.get("change_24h")
        return abs(ch) if isinstance(ch, (int, float)) else 0.0
    cells.sort(key=lambda c: (_mv(c), c["value_usd"]), reverse=True)
    # v23.x : grille 5 colonnes × 4 lignes = 20 cases. Le rendu mail (template)
    # affiche 5 cases par ligne et ajoute lui-même la case « +N autres » quand
    # ``extra`` est présent. ≤ 20 positions → tout afficher (pas d'agrégat) ;
    # > 20 → 19 plus gros mouvements + 1 case agrégée = 20 cases.
    if total <= 20:
        top = cells[:20]
        rest: list[dict[str, Any]] = []
    else:
        top = cells[:19]
        rest = cells[19:]
    extra = None
    if rest:
        _w = sum(c["value_usd"] for c in rest)
        _wsum = sum((c["value_usd"] * c["change_24h"])
                    for c in rest if isinstance(c.get("change_24h"), (int, float)))
        extra = {
            "count": len(rest),
            "value_usd": round(_w, 2),
            "ptf_pct": round(_w / _total_val * 100, 1),
            "avg_change_24h": round(_wsum / _w, 1) if _w > 0 else None,
        }
    return {
        "cells": top,
        "total_count": total,
        "remaining": max(0, total - len(top)),
        "extra": extra,
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
    val_7d_ago = 0.0
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
        if ch7d is not None and v and (1 + ch7d / 100.0) > 0:
            # v23 (W2/W5) — valeur 7j passée RÉELLE (v / (1+r)), pour un rendement
            # standard identique au calcul du hebdo (source unique cross-mail).
            val_7d_ago += v / (1 + ch7d / 100.0)
            has_7d = True
        elif v:
            val_7d_ago += v  # pas de variation 7j connue → neutre
        ath_d = a.get("ath_distance_pct")
        if ath_d is not None and v:
            drawdown_sum_weighted += v * ath_d
            counted += v
    total_wealth = crypto_total + usdc_value
    # v23 (W2/W5) — rendement 7j STANDARD : (actuel − valeur 7j passée) / valeur 7j
    # passée, identique au P&L du hebdo. AVANT : delta/total (non standard) →
    # divergence matin (−2.6%) vs hebdo (−7.3%) pour la MÊME fenêtre 7j, et un
    # vs-BTC faux (+3.3% au lieu de −1.7%). Une seule formule désormais, partout.
    change_7d_usd = round(crypto_total - val_7d_ago, 2) if has_7d else None
    change_7d_pct = (
        round(((crypto_total - val_7d_ago) / val_7d_ago) * 100, 2)
        if (has_7d and val_7d_ago) else None
    )
    btc_7d = (enriched.get("BTC") or {}).get("change_7d")
    vs_btc_7d = round(change_7d_pct - btc_7d, 2) if (change_7d_pct is not None and btc_7d is not None) else None
    return {
        "value_usd": round(crypto_total, 2),
        "change_24h_pct": round((delta_24h / crypto_total) * 100, 2) if crypto_total else None,
        "change_7d_pct": change_7d_pct,
        "change_7d_usd": change_7d_usd,
        "vs_btc_7d_pct": vs_btc_7d,
        "drawdown_ath_pct": round(drawdown_sum_weighted / counted, 1) if counted else None,
        "usdc_pct": round((usdc_value / total_wealth) * 100, 1) if total_wealth else None,
        "usdc_usd": round(usdc_value, 2),
        # v19/WS2 (W-B2 — SOURCE DE VÉRITÉ UNIQUE) : date de calcul du snapshot,
        # pour que le hebdo du MÊME jour réutilise la perf 7j / vs BTC 7j du matin
        # (un seul nombre, partout) au lieu de la recalculer différemment.
        "computed_date": datetime.now(TZ).date().isoformat(),
    }


def _evening_report_is_fresh(report: dict[str, Any], max_age_hours: float = 18.0) -> bool:
    """Vrai si le rapport du soir stocké date de moins de ``max_age_hours``.

    A2 — garantit que la baseline du « P&L nuit » est bien celle de la veille au
    soir, et non un rapport périmé (qui produirait un faux delta géant).
    Tolérant : si aucun horodatage exploitable, on considère NON frais (n/d).
    """
    if not report:
        return False
    ts = (
        report.get("_saved_at")
        or report.get("generated_at")
        or report.get("timestamp")
        or (report.get("header") or {}).get("generated_at")
        or (report.get("meta") or {}).get("generated_at")
    )
    if not ts:
        return False
    from datetime import datetime as _dt, timezone as _tz
    try:
        parsed = _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_tz.utc)
    now = _dt.now(_tz.utc)
    age_h = (now - parsed).total_seconds() / 3600.0
    return 0 <= age_h <= max_age_hours


def _compute_sector_exposure(
    enriched: dict[str, dict[str, Any]], rotation: dict[str, Any]
) -> dict[str, Any]:
    """A6 — exposition sectorielle du PTF : poids (%) par secteur + perf marché.

    Pour chaque secteur de la rotation, somme la valeur des positions détenues
    et la rapporte au total PTF. Joint la perf 24h moyenne du secteur (côté
    marché) pour comparer « mon poids » vs « le mouvement du secteur ».

    Returns:
        Dict ``{available, total_usd, sectors: [{sector, ptf_pct, value_usd,
        market_change_24h, holdings}]}`` trié par poids décroissant.
    """
    sectors = (rotation or {}).get("sectors", {})
    total = sum((e.get("value_usd") or 0) for e in enriched.values()) or 0.0
    if not sectors or total <= 0:
        return {"available": False, "sectors": [], "total_usd": round(total, 2)}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, sec in sectors.items():
        members = sec.get("members", []) or []
        held = [m for m in members if (enriched.get(m, {}).get("value_usd") or 0) > 0]
        if not held:
            continue
        val = sum((enriched.get(m, {}).get("value_usd") or 0) for m in held)
        seen.update(held)
        rows.append(
            {
                "sector": name,
                "ptf_pct": round(val / total * 100, 1),
                "value_usd": round(val, 2),
                "market_change_24h": sec.get("avg_change_24h"),
                "market_change_7d": sec.get("avg_change_7d"),
                "market_change_30d": sec.get("avg_change_30d"),
                "holdings": sorted(held),
            }
        )
    # Positions hors secteurs identifiés (regroupées sous « Autre »).
    other_val = sum(
        (e.get("value_usd") or 0) for s, e in enriched.items()
        if s not in seen and (e.get("value_usd") or 0) > 0
    )
    if other_val > 0:
        rows.append(
            {
                "sector": "Autre / non classé",
                "ptf_pct": round(other_val / total * 100, 1),
                "value_usd": round(other_val, 2),
                "market_change_24h": None,
                "holdings": [],
            }
        )
    rows.sort(key=lambda r: r["ptf_pct"], reverse=True)
    # v15 (audit weekly P2) — anti-bruit : fusion « Indexing/Infra » → « Infra »
    # (deux micro-secteurs redondants), puis filtrage des poids < 1% du PTF
    # (regroupés dans une ligne « Divers <1% » pour que le total reste lisible).
    _by_name = {r["sector"]: r for r in rows}
    if "Indexing/Infra" in _by_name and "Infra" in _by_name:
        infra, idx = _by_name["Infra"], _by_name["Indexing/Infra"]
        infra["value_usd"] = round(infra["value_usd"] + idx["value_usd"], 2)
        infra["ptf_pct"] = round(infra["value_usd"] / total * 100, 1)
        infra["holdings"] = sorted(set(infra["holdings"]) | set(idx["holdings"]))
        rows = [r for r in rows if r["sector"] != "Indexing/Infra"]
    elif "Indexing/Infra" in _by_name:
        _by_name["Indexing/Infra"]["sector"] = "Infra"
    major = [r for r in rows if r["ptf_pct"] >= 1.0]
    minor = [r for r in rows if r["ptf_pct"] < 1.0]
    if minor:
        _mv = sum(r["value_usd"] for r in minor)
        if _mv > 0:
            major.append({
                "sector": f"Divers (<1% · {len(minor)} secteurs)",
                "ptf_pct": round(_mv / total * 100, 1),
                "value_usd": round(_mv, 2),
                "market_change_24h": None,
                "holdings": [],
            })
    rows = sorted(major, key=lambda r: r["ptf_pct"], reverse=True)
    # v23.x : 6 cases = 5 secteurs individuels (les plus gros) + 1 ligne
    # « Autres secteurs » (agrège le reste ; perfs de marché = moyennes pondérées
    # par la valeur, sur 24h/7j/30j). Avant : 4 + Autres = 5 cases.
    if len(rows) > 6:
        head = rows[:5]
        tail = rows[5:]
        tail_val = sum(r["value_usd"] for r in tail)

        def _tail_weighted(key: str) -> Optional[float]:
            """Moyenne pondérée par la valeur des perfs connues (ignore les None)."""
            num = sum(
                (r["value_usd"] * r[key]) for r in tail
                if isinstance(r.get(key), (int, float))
            )
            den = sum(
                r["value_usd"] for r in tail
                if isinstance(r.get(key), (int, float))
            )
            return round(num / den, 1) if den > 0 else None

        _tail_holdings = sorted(
            {h for r in tail for h in (r.get("holdings") or [])}
        )
        head.append({
            # v23 (W7) — « Autres secteurs (N) » (petits secteurs IDENTIFIÉS
            # agrégés), distinct de « Autre / non classé » (positions sans secteur
            # connu). Évite deux libellés « Autre » prêtant à confusion dans le hebdo.
            "sector": f"Autres secteurs ({len(tail)})",
            "ptf_pct": round(tail_val / total * 100, 1),
            "value_usd": round(tail_val, 2),
            "market_change_24h": _tail_weighted("market_change_24h"),
            "market_change_7d": _tail_weighted("market_change_7d"),
            "market_change_30d": _tail_weighted("market_change_30d"),
            "holdings": _tail_holdings,
            "is_aggregate": True,
        })
        rows = head
    return {"available": bool(rows), "total_usd": round(total, 2), "sectors": rows}


def _detect_data_contradictions(macro_context: dict[str, Any]) -> dict[str, Any]:
    """C1 — détecte les incohérences de données notables (avec tolérance).

    Aujourd'hui : vérifie que le DXY réel (``dxy``) et l'indice dollar large
    (``dxy_broad``) ne sont pas présentés comme une même grandeur — ils diffèrent
    structurellement (échelles distinctes), donc on note simplement que « DXY »
    désigne l'indice ICE, pas l'indice large, SEULEMENT si les deux sont présents
    et que la confusion serait possible. Tolérance : on ne signale rien si une
    seule valeur existe. Conçu pour rester DISCRET (note ² occasionnelle), pas
    pour polluer chaque rapport.

    Returns:
        Dict ``{has_any, notes: [str]}``. ``has_any=False`` → aucune note.
    """
    notes: list[str] = []
    dxy = macro_context.get("dxy")
    dxy_broad = macro_context.get("dxy_broad")
    if (
        isinstance(dxy, (int, float)) and isinstance(dxy_broad, (int, float))
        and abs(dxy - dxy_broad) >= 8.0
    ):
        notes.append(
            "« DXY » désigne l'indice dollar ICE (~"
            f"{dxy:.0f}). L'indice dollar large de la Fed ("
            f"~{dxy_broad:.0f}) est une mesure distincte, à ne pas confondre."
        )
    if macro_context.get("dxy_is_broad_fallback"):
        notes.append(
            "DXY indisponible en direct : valeur de repli sur l'indice dollar "
            "large de la Fed (échelle différente, à interpréter avec prudence)."
        )
    return {"has_any": bool(notes), "notes": notes}


def _compute_macro_guardrail(macro_context: dict[str, Any]) -> dict[str, Any]:
    """Garde-fou macro déterministe (v18 M-B12/M-B14).

    Indépendant de l'analyse de Gemini : certains signaux de marché imposent la
    prudence quel que soit le récit. Renvoie ``{active, triggers, message}``.
      • VIX ≥ 25      → stress sur les actions, risk-off potentiel
      • F&G ≤ 20      → peur extrême, marché émotionnellement fragile
      • DXY ≥ 105     → dollar fort, pression sur les actifs risqués
    """
    triggers: list[str] = []
    vix = macro_context.get("vix")
    if isinstance(vix, (int, float)) and vix >= 25:
        triggers.append(f"VIX à {vix:.0f} (≥ 25, stress sur les actions)")
    fg = macro_context.get("fear_greed")
    if isinstance(fg, (int, float)) and fg <= 20:
        triggers.append(f"Fear & Greed à {fg:.0f} (peur extrême)")
    dxy = macro_context.get("dxy")
    if isinstance(dxy, (int, float)) and dxy >= 105:
        triggers.append(f"DXY à {dxy:.1f} (dollar fort, ≥ 105)")
    if not triggers:
        return {"active": False}
    return {
        "active": True,
        "triggers": triggers,
        "message": (
            "Signaux macro de prudence détectés : " + " · ".join(triggers)
            + ". Quelle que soit la lecture du jour, privilégier la préservation "
            "du capital, éviter de sur-dimensionner les renforcements et resserrer "
            "la discipline sur les invalidations."
        ),
    }


def _apply_macro_guardrail_override(payload: dict[str, Any]) -> None:
    """v19/M-B14 — FORÇAGE déterministe de la prudence, indépendant de Gemini.

    Le garde-fou macro (_compute_macro_guardrail) était jusqu'ici purement
    ADVISORY (une instruction au prompt). Or la consigne exige qu'un signal de
    stress (VIX ≥ 25, peur extrême, dollar fort) FORCE un biais prudent quel que
    soit l'optimisme de Gemini. Ce post-traitement garantit ce forçage :
      • pose ``macro_regime_readout.forced_caution = True`` ;
      • injecte une clause de prudence en tête de ``crypto_bias`` si absente.
    Idempotent et sans effet si le garde-fou est inactif. Testé : VIX=28 →
    forced_caution True, biais prudent (cf. tests/test_v19.py).
    """
    guard = payload.get("macro_guardrail") or {}
    if not guard.get("active"):
        return
    readout = payload.get("macro_regime_readout")
    if not isinstance(readout, dict):
        readout = {}
        payload["macro_regime_readout"] = readout
    readout["forced_caution"] = True
    triggers = guard.get("triggers") or []
    readout["forced_caution_note"] = (
        "Prudence forcée par garde-fou macro déterministe"
        + (" (" + " · ".join(triggers) + ")" if triggers else "")
        + " : biais défensif quel que soit le régime affiché."
    )
    bias = (readout.get("crypto_bias") or "").strip()
    if "pruden" not in bias.lower():
        readout["crypto_bias"] = ("⚠ Prudence forcée (garde-fou macro). " + bias).strip()


def _compute_portfolio_risk_score(
    snapshot: dict[str, Any],
    sector_exposure: dict[str, Any],
    macro_context: dict[str, Any],
    enriched: dict[str, dict[str, Any]],
    portfolio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """B5 — score de risque PTF synthétique (0-10) + facteurs explicites.

    Combine, de façon déterministe et transparente, les vrais signaux de risque
    du portefeuille : drawdown 7j, concentration sectorielle, volatilité 24h
    moyenne des positions, et régime macro (peur extrême / biais risk-off).
    v19/M-B8 : le cash N'EST PLUS une composante (un PTF 100% investi par choix
    ne doit pas être pénalisé). Chaque composante est plafonnée ; le total est
    ramené sur 10. Objectif : un chiffre scannable en haut de rapport, pas un modèle.

    Returns:
        Dict ``{score, level, level_color, factors: [str]}``.
    """
    score = 0.0
    factors: list[str] = []
    # v15 (audit B-2) — décomposition structurée du score pour les mini-barres
    # du rendu : chaque axe = {label, pts, max}. Alimenté en parallèle de
    # ``factors`` (compat). v18 (M-B8) : 4 axes = Drawdown 7j / Concentration /
    # Volatilité 24h / Sentiment. Le Cash a été RETIRÉ (ne pas pénaliser un PTF
    # 100% investi par choix). Score max = 3 + 2.5 + 2 + 1.5 = 9.0.
    components: list[dict[str, Any]] = []

    # 1) Drawdown 7j (perf hebdo négative) — jusqu'à 3 pts.
    ch7 = snapshot.get("change_7d_pct")
    _dd_pts = 0.0
    if isinstance(ch7, (int, float)) and ch7 < 0:
        _dd_pts = min(3.0, abs(ch7) / 5.0)  # -15%/7j → 3 pts
        score += _dd_pts
        if _dd_pts >= 1:
            factors.append(f"drawdown 7j {ch7:+.1f}%")
    components.append({"label": "Drawdown 7j", "pts": round(_dd_pts, 1), "max": 3.0})

    # 2) Concentration sectorielle (poids du 1er secteur) — jusqu'à 2.5 pts.
    sectors = (sector_exposure or {}).get("sectors") or []
    _cc_pts = 0.0
    if sectors:
        top = max(sectors, key=lambda s: s.get("ptf_pct") or 0)
        top_pct = top.get("ptf_pct") or 0
        if top_pct >= 30:
            _cc_pts = min(2.5, (top_pct - 20) / 20)  # 70% → 2.5 pts
            score += _cc_pts
            factors.append(f"concentration {top.get('sector')} {top_pct:.0f}%")
    components.append({"label": "Concentration", "pts": round(_cc_pts, 1), "max": 2.5})

    # 3) Volatilité 24h moyenne des positions significatives — jusqu'à 2 pts.
    vols = [abs(e.get("change_24h")) for e in enriched.values()
            if isinstance(e.get("change_24h"), (int, float)) and (e.get("value_usd") or 0) >= 10]
    _vol_pts = 0.0
    if vols:
        avg_vol = sum(vols) / len(vols)
        _vol_pts = min(2.0, avg_vol / 6.0)  # 12%/24h moyen → 2 pts
        score += _vol_pts
        if _vol_pts >= 1:
            factors.append(f"volatilité 24h {avg_vol:.1f}% en moyenne")
    # v17 (T-RISK / M-A5) : la volatilité était comptée dans le total mais ABSENTE
    # des barres → barres (3.7) ≠ total (4.4). On l'ajoute comme 5e composante.
    components.append({"label": "Volatilité 24h", "pts": round(_vol_pts, 1), "max": 2.0})

    # v18 (M-B8 / E-A16) : le CASH n'est PLUS une composante du score de risque.
    # Demande d'Omar : juger le risque des CRYPTOS détenues, pas pénaliser
    # l'absence de cash (qui tirait mécaniquement le score à la hausse). Le PTF
    # est 100% investi par choix — ce n'est pas un facteur de risque ici.

    # 5) Régime macro défavorable (peur extrême / VIX tendu) — jusqu'à 1.5 pt.
    _sent_pts = 0.0
    fng = macro_context.get("fear_greed")
    if isinstance(fng, (int, float)) and fng <= 25:
        _sent_pts += 1.0
        score += 1.0
        factors.append(f"sentiment Peur Extrême (F&G {int(fng)})")
    vix = macro_context.get("vix")
    if isinstance(vix, (int, float)) and vix >= 25:
        _sent_pts += 0.5
        score += 0.5
        factors.append(f"VIX tendu ({vix:.0f})")
    components.append({"label": "Sentiment", "pts": round(_sent_pts, 1), "max": 1.5})

    score = round(min(10.0, score), 1)
    if score >= 7:
        level, color = "élevé", "#A32D2D"
    elif score >= 4:
        level, color = "modéré", "#BA7517"
    else:
        level, color = "maîtrisé", "#3B6D11"
    # v17 (M-A18) : axe(s) DOMINANT(s) = plus haut ratio pts/max (pas la valeur
    # absolue). Évite « tiré par la concentration » quand Cash=1.5/1.5 (100%)
    # pèse plus que Concentration=1.2/2.5 (48%). Le readout s'appuie dessus.
    _ranked = sorted(
        (c for c in components if c["max"] and c["pts"] > 0),
        key=lambda c: c["pts"] / c["max"], reverse=True,
    )
    dominant_axes = [
        {"label": c["label"], "pts": c["pts"], "max": c["max"],
         "ratio_pct": round(c["pts"] / c["max"] * 100)}
        for c in _ranked[:2]
    ]
    return {"score": score, "level": level, "level_color": color,
            "factors": factors, "components": components,
            "dominant_axes": dominant_axes,
            # v18 (E-B4) : liste des actifs détenus à l'instant du calcul, pour
            # détecter le soir si la composition du PTF a changé depuis le matin.
            "holdings_snapshot": sorted(
                k for k, v in (portfolio or {}).items()
                if isinstance(v, dict) and (v.get("value_usd") or 0) > 0
            ) if portfolio else []}


# Vocabulaire d'action par axe de risque (pour le readout déterministe M-B8).
_RISK_AXIS_ACTION = {
    "Concentration": "diversifier hors du secteur dominant pour réduire la concentration",
    "Drawdown 7j": "surveiller les supports clés des positions en perte récente",
    "Volatilité 24h": "alléger tactiquement les positions les plus volatiles si le risque global gêne",
    "Sentiment": "rester prudent tant que le sentiment de marché reste dégradé",
}


def _build_risk_readout(risk: dict[str, Any]) -> dict[str, str]:
    """v18 (M-B8 / E-A2) — readout du risque calculé en PYTHON (déterministe).

    Avant, le readout venait de Gemini (matin) et était copié tel quel le soir,
    créant des incohérences (« concentration 2.0 » écrit alors que le bloc
    affichait 0.0). Ici on construit driver / reco / caveat directement depuis
    les composantes réelles, donc matin et soir sont TOUJOURS cohérents avec les
    barres affichées.

    Returns:
        ``{driver, reco, caveat}``.
    """
    dominant = risk.get("dominant_axes") or []
    if not dominant:
        # Aucun axe positif → risque minimal.
        return {
            "driver": "Aucun facteur de risque significatif sur les axes suivis.",
            "reco": "Maintenir le suivi habituel ; pas d'action de réduction nécessaire.",
            "caveat": "Note indicative et déterministe (concentration, drawdown, "
                      "volatilité, sentiment) : elle ne capte pas le risque "
                      "spécifique projet ni les chocs exogènes.",
        }
    # Driver : les 1-2 axes au plus haut ratio pts/max, avec chiffres réels.
    parts = [f"{a['label']} ({a['pts']}/{a['max']})" for a in dominant]
    driver = "Score tiré par " + " et ".join(parts) + "."
    # Reco : action liée à l'axe dominant n°1.
    top_label = dominant[0]["label"]
    reco_action = _RISK_AXIS_ACTION.get(top_label, "réduire l'exposition sur l'axe le plus chargé")
    reco = reco_action[0].upper() + reco_action[1:] + "."
    caveat = ("Note indicative et déterministe (concentration, drawdown, "
              "volatilité, sentiment) : elle ne capte ni le risque spécifique "
              "projet ni les corrélations cachées ou chocs exogènes.")
    return {"driver": driver, "reco": reco, "caveat": caveat}


# v23 — levier d'amélioration par axe de SANTÉ (readout déterministe, actionnable).
_HEALTH_AXIS_IMPROVE = {
    "Diversification": "alléger le secteur dominant sur rebond et étaler sur d'autres narratifs",
    "Momentum vs BTC": "recentrer sur le cœur (BTC/ETH) et alléger les satellites qui sous-performent",
    "Solidité (vs ATH)": "accumuler le cœur de conviction sur repli et offloader les poussières mortes",
}


def _compute_portfolio_health(
    snapshot: dict[str, Any],
    sector_exposure: dict[str, Any],
) -> dict[str, Any]:
    """v23 — note de SANTÉ du portefeuille /10 (plus haut = plus sain).

    Remplace l'ancienne « note de risque » des mails par une lecture RÉFLECTIVE
    et ACTIONNABLE, UNIFIÉE avec la « Santé » de l'hebdo (mêmes axes). Trois axes
    transparents (formules pures, aucun avis IA) :
      • Diversification : poids du 1er secteur (≤25% → 10 ; 75% → 0).
      • Momentum vs BTC : perf 7j vs BTC (+10 pts → 10 ; −10 pts → 0).
      • Solidité (vs ATH) : drawdown pondéré (0% → 10 ; −80% → ~1.6).
    Note = moyenne des axes disponibles. On expose un ``driver`` (ce qui tire la
    note) et un ``improve`` (le levier le PLUS efficace = l'axe le plus faible),
    tous deux très courts. Dégradation gracieuse : entrée manquante → axe omis.

    Returns:
        ``{score, level, level_color, axes:[{label,score,max,detail}],
        driver, improve}`` ou ``{}`` si aucun axe exploitable.
    """
    axes: list[dict[str, Any]] = []

    sectors = (sector_exposure or {}).get("sectors") or []
    top_pct = max((s.get("ptf_pct") or 0) for s in sectors) if sectors else None
    if top_pct is not None:
        _div = max(0.0, 10.0 - max(0.0, top_pct - 25) / 5.0)
        axes.append({"label": "Diversification", "score": round(_div, 1),
                     "max": 10.0, "detail": f"top secteur {top_pct:.0f}% du PTF"})

    vsbtc = snapshot.get("vs_btc_7d_pct")
    if isinstance(vsbtc, (int, float)):
        _mom = max(0.0, min(10.0, 5.0 + vsbtc / 2.0))
        axes.append({"label": "Momentum vs BTC", "score": round(_mom, 1),
                     "max": 10.0, "detail": f"{vsbtc:+.1f} pts vs BTC 7j"})

    dd = snapshot.get("drawdown_ath_pct")
    if isinstance(dd, (int, float)):
        _sol = max(0.0, min(10.0, 10.0 + dd / 9.5))
        axes.append({"label": "Solidité (vs ATH)", "score": round(_sol, 1),
                     "max": 10.0, "detail": f"drawdown {dd:.0f}% vs ATH"})

    if not axes:
        return {}
    score = round(sum(a["score"] for a in axes) / len(axes), 1)
    if score >= 6.5:
        level, color = "robuste", "#3B6D11"
    elif score >= 5.0:
        level, color = "correct", "#6B8E23"
    elif score >= 3.5:
        level, color = "fragile", "#BA7517"
    else:
        level, color = "à risque", "#A32D2D"
    strongest = max(axes, key=lambda a: a["score"])
    weakest = min(axes, key=lambda a: a["score"])
    driver = (f"Portée par {strongest['label']} ({strongest['score']}/10), "
              f"pénalisée par {weakest['label']} ({weakest['score']}/10).")
    _imp = _HEALTH_AXIS_IMPROVE.get(weakest["label"], "renforcer l'axe le plus faible")
    return {"score": score, "level": level, "level_color": color,
            "axes": axes, "driver": driver,
            "improve": _imp[0].upper() + _imp[1:] + "."}


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
    # v17 (M-A4) : plancher relevé à 0.5%. Un 10Y/2Y US sous 0.5% est aberrant
    # dans le régime actuel (souvent un ÷10 ou un parsing décimal FRED erroné) ;
    # avec le plancher 0.0 précédent, « 0.4487% » passait au lieu d'être écarté.
    "us_10y": (0.5, 12.0),
    "us_2y": (0.5, 12.0),
    "yield_curve": (-5.0, 5.0),
    "gold": (500.0, 6000.0),
    "sp500": (1000.0, 12000.0),
    "nasdaq": (3000.0, 45000.0),
    "brent": (10.0, 250.0),
    "wti": (10.0, 250.0),
    "eur_usd": (0.7, 1.6),
    "usd_jpy": (80.0, 260.0),
    "btc_price": (1000.0, 500000.0),
    # v14.1 — international.
    "nikkei": (10000.0, 80000.0),
    "stoxx50": (2000.0, 9000.0),
    "dax": (8000.0, 35000.0),
    "ecb_deposit_rate": (-1.0, 8.0),
    "boj_rate": (-1.0, 5.0),
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


def _fng_label_fr(value: Any) -> Optional[str]:
    """Traduit l'index Fear & Greed (0-100) en libellé français lisible.

    Bug v14 : le template du soir lisait ``fear_greed_label`` mais aucune
    fonction ne produisait cette clé (alternative.me renvoie ``classification``
    en anglais) -> le libellé n'apparaissait jamais. Paliers standard du
    Crypto Fear & Greed Index.
    """
    if not isinstance(value, (int, float)):
        return None
    if value <= 25:
        return "Peur extrême"
    if value <= 45:
        return "Peur"
    if value <= 55:
        return "Neutre"
    if value <= 75:
        return "Avidité"
    return "Avidité extrême"


def _macro_context(
    market: dict[str, Any], fng: dict[str, Any], macro: dict[str, Any],
    polymarket: dict[str, Any], yahoo_quotes: Optional[dict[str, float]] = None,
    yahoo_deltas: Optional[dict[str, float]] = None,
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

    v14.1 : (1) ``yahoo_deltas`` (variation vs clôture précédente, MÊME unité
    que la valeur) est prioritaire sur le delta FRED pour les flèches 24h —
    avant, une valeur Yahoo live pouvait porter la flèche d'un delta FRED
    périmé (valeur et tendance contradictoires). (2) INTERNATIONAL : Nikkei 225,
    Euro Stoxx 50, DAX (Yahoo, FRED en recoupement pour le Nikkei), taux de
    dépôt BCE et taux BoJ (FRED). Le marché crypto ne vit pas qu'aux USA.
    """
    _macro_validation_flags.clear()  # réinit par run (évite l'accumulation)
    yq = yahoo_quotes or {}
    yd = yahoo_deltas or {}
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
    # v14.1 — international (FRED ; Yahoo prioritaire pour le niveau Nikkei).
    nikkei = _fred_value(macro, "nikkei")
    ecb_rate = _fred_value(macro, "ecb_deposit_rate")
    boj_rate = _fred_value(macro, "boj_call_rate")

    # Yahoo prioritaire (live), FRED fallback. _pref(yahoo_key, fred_dict) :
    # renvoie la valeur Yahoo si présente, sinon la valeur FRED.
    def _pref(yk: str, fred_d: dict[str, Any]) -> Any:
        return yq[yk] if yk in yq else fred_d["value"]

    # Delta : Yahoo live (vs clôture précédente) prioritaire, FRED fallback —
    # la flèche 24h reste ainsi cohérente avec la valeur affichée.
    def _delta_pref(yk: str, fred_d: dict[str, Any]) -> Any:
        return yd[yk] if yk in yd else fred_d["delta"]

    # v15 — barres Fed Polymarket : le DOMINANT s'affiche (audit : « maintien
    # 99,8% », jamais le minoritaire « baisse 0,2% »). fed_cut conservé pour
    # compat interne ; fed_bars alimente les 3 barres du template.
    fed_cut = None
    fed_bars = None
    if polymarket.get("available"):
        fed_bars = polymarket.get("fed_bars") or None
        if fed_bars:
            fed_cut = fed_bars.get("cut_pct")
        if fed_cut is None:
            for m in (polymarket.get("fed_markets")
                      or polymarket.get("markets") or []):
                q = (m.get("question") or "").lower()
                if "rate cut" in q or "fed" in q:
                    fed_cut = m.get("probability_pct")
                    break

    return {
        # Cœur (header principal, comme V5).
        "btc_price": _vm("btc_price", round(btc_price, 2) if btc_price else None),
        "fear_greed": fng_val,
        "fear_greed_label": _fng_label_fr(fng_val),
        "fear_greed_delta": fng.get("delta") if fng.get("available") else None,
        # A1 — DXY DÉSAMBIGUÏSÉ. ``dxy`` = le VRAI DXY ICE (~98-105, source
        # Yahoo DX-Y.NYB), c'est celui que tout le monde appelle « le DXY » et
        # que l'IA doit citer. ``dxy_broad`` = l'indice dollar large pondéré du
        # commerce de la Fed (DTWEXBGS, ~115-125), échelle différente, fourni
        # en complément seulement. Si Yahoo est indisponible, ``dxy`` retombe
        # sur l'indice large (clairement noté) pour ne pas laisser de trou.
        "dxy": _vm("dxy_ice", yq.get("dxy_ice")) if yq.get("dxy_ice") is not None
                else _vm("dxy", dxy["value"]),
        "dxy_is_broad_fallback": yq.get("dxy_ice") is None and dxy["value"] is not None,
        "dxy_broad": _vm("dxy", dxy["value"]),
        "dxy_delta": yd.get("dxy_ice") if yd.get("dxy_ice") is not None else dxy["delta"],
        # Conservé pour compat interne (égal à dxy quand Yahoo dispo).
        "dxy_ice": _vm("dxy_ice", yq.get("dxy_ice")),
        "polymarket_fed_cut_pct": fed_cut,
        "polymarket_fed_bars": fed_bars,
        # v16.1 — autres marchés Polymarket (récession, géopo, crypto) pour la
        # ligne « autres marchés » et l'intégration au fil des news.
        "polymarket_extra_markets": (polymarket.get("extra_markets") or [])[:4],
        # Taux & volatilité (Yahoo prioritaire sur VIX et 10Y).
        "vix": _vm("vix", _pref("vix", vix)),
        "vix_delta": _delta_pref("vix", vix),
        "us_10y": _vm("us_10y", _pref("us_10y", us_10y)),
        "us_10y_delta": _delta_pref("us_10y", us_10y),
        "us_2y": _vm("us_2y", us_2y["value"]),
        "yield_curve_10y_2y": _vm("yield_curve", yield_curve["value"]),
        # Actifs macro hors-crypto (Yahoo prioritaire — prix live).
        "gold_usd": _vm("gold", _pref("gold", gold)),
        "gold_delta": _delta_pref("gold", gold),
        "sp500": _vm("sp500", _pref("sp500", sp500)),
        "sp500_delta": _delta_pref("sp500", sp500),
        "nasdaq": _vm("nasdaq", _pref("nasdaq", nasdaq)),
        "nasdaq_delta": _delta_pref("nasdaq", nasdaq),
        "brent_usd": _vm("brent", _pref("brent", brent)),
        "brent_delta": _delta_pref("brent", brent),
        "wti_usd": _vm("wti", _pref("wti", wti)),
        "eur_usd": _vm("eur_usd", _pref("eur_usd", eur_usd)),
        "eur_usd_delta": _delta_pref("eur_usd", eur_usd),
        "usd_jpy": _vm("usd_jpy", _pref("usd_jpy", usd_jpy)),
        "usd_jpy_delta": _delta_pref("usd_jpy", usd_jpy),
        # v14.1 — INTERNATIONAL (zone euro · Japon). Indices via Yahoo live
        # (FRED recoupe le Nikkei) ; taux directeurs via FRED. Une valeur
        # absente reste None → cellule masquée, jamais inventée.
        "nikkei": _vm("nikkei", _pref("nikkei", nikkei)),
        "nikkei_delta": _delta_pref("nikkei", nikkei),
        "stoxx50": _vm("stoxx50", yq.get("stoxx50")),
        "stoxx50_delta": yd.get("stoxx50"),
        "dax": _vm("dax", yq.get("dax")),
        "dax_delta": yd.get("dax"),
        "ecb_deposit_rate": _vm("ecb_deposit_rate", ecb_rate["value"]),
        "ecb_deposit_rate_delta": ecb_rate["delta"],
        "ecb_deposit_rate_date": ecb_rate["date"],
        "boj_rate": _vm("boj_rate", boj_rate["value"]),
        "boj_rate_delta": boj_rate["delta"],
        "boj_rate_date": boj_rate["date"],
        # Variation 24h du BTC (%) pour la flèche de tendance (v14 point 16).
        "btc_change_24h": (market.get("BTC") or {}).get("change_24h"),
        # Provenance (pour transparence / debug ; non affiché par défaut).
        "_price_source": "yahoo+fred" if yq else "fred",
    }


def _active_sources(**flags: Any) -> list[str]:
    """Liste lisible des sources réellement actives (anti-fabrication)."""
    out: list[str] = []
    mapping = {
        "market": "CoinGecko", "fng": "Fear&Greed", "macro": "FRED",
        "onchain": "On-chain", "polymarket": "Polymarket", "etf": "ETF flows (Farside)",
        "telegram": "Telegram", "defi": "DeFiLlama", "narratives": "Kaito",
        "social": "LunarCrush", "unlocks": "Token Unlocks", "news": "News",
        "youtube": "YouTube", "geopolitics": "Géopolitique",
        "btc_network": "BTC Network", "stablecoins": "Stablecoins", "whales": "Whale Tracking",
        "macro_news": "Yahoo Finance", "macro_calendar": "Calendrier macro",
        "crypto_rss": "RSS news (crypto + macro · 16 flux)",
        "onchain_adv": "On-chain avancé (Coin Metrics)",
        "options": "Options (Deribit)",
        "macro_corr": "Corrélations macro",
        # v14.1 — international + transmission actions → crypto.
        "intl_markets": "Marchés internationaux (BCE · BoJ · Nikkei · Stoxx)",
        "equity_links": "Actions ↔ crypto (NVDA · COIN · MSTR…)",
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
        # v17 (W-A1) : un dict « symbole → données » (ex. CoinGecko
        # {BTC:{...}, ETH:{...}}) n'a pas de clé 'available' mais EST actif s'il
        # est non vide. Avant, .get('available') → None → CoinGecko toujours
        # marqué indispo (« indispo 7j/7 ») malgré 28/28 symboles résolus.
        if "available" not in val:
            return len(val) > 0
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
    degraded_sources: Optional[list[str]] = None,
) -> str:
    """Construit la phrase d'angles morts à partir des sources indisponibles.

    v17 (M-A3) : au lieu d'auditer 5 sources en dur positionnellement, on reçoit
    ``degraded_sources`` = l'ensemble RÉEL des sources dégradées (toutes celles
    de _ALL_SOURCES_LIST qui n'ont pas renvoyé de contenu). Les ``*sources``
    legacy restent acceptés en repli si degraded_sources n'est pas fourni.
    """
    parts: list[str] = []
    if degraded_sources:
        # Set complet (M-A3) : on liste toutes les sources réellement dégradées.
        missing = list(dict.fromkeys(degraded_sources))  # dédup, ordre stable
    else:
        labels = ["on-chain avancé", "Polymarket", "ETF flows (Farside)", "Telegram", "DeFiLlama"]
        missing = [labels[i] for i, src in enumerate(sources)
                   if not (src.get("available") if isinstance(src, dict) else src)]
    if missing:
        # Cap d'affichage : au-delà de 8, on résume pour ne pas noyer le mail.
        # v18 (M-A15) : on remplace le slash ambigu « indisponibles / dégradées »
        # (le lecteur ne sait pas si c'est ET ou OU) par une formulation claire :
        # « indisponibles ou en données partielles ».
        if len(missing) > 8:
            shown = ", ".join(missing[:8])
            parts.append(
                f"Sources indisponibles ou en données partielles ce matin "
                f"({len(missing)}) : {shown}, +{len(missing) - 8} autres."
            )
        else:
            parts.append("Sources indisponibles ou en données partielles ce matin : "
                         + ", ".join(missing) + ".")

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

    # v18 (M-A6) : Gemini préfixe parfois le driver par le nom de l'actif, qui
    # est DÉJÀ affiché dans la colonne actif → « ETH | ETH Dépôts whales ETH ».
    # On retire un nom d'actif redondant en TÊTE du driver (la colonne actif le
    # porte déjà). On ne touche pas aux occurrences en milieu/fin de phrase.
    _mi = payload.get("macro_impact")
    if isinstance(_mi, dict) and isinstance(_mi.get("exposed_positions"), list):
        # v18 (M-B3) : limiter à 4 actifs principaux (au-delà, ça dilue le signal).
        _mi["exposed_positions"] = _mi["exposed_positions"][:4]
        for _ep in _mi["exposed_positions"]:
            if not isinstance(_ep, dict):
                continue
            _asset = (_ep.get("asset") or "").strip()
            _drv = (_ep.get("driver") or "").strip()
            if _asset and _drv:
                # Retire « ETH » / « ETH: » / « ETH - » en tête du driver.
                import re as _re
                _drv = _re.sub(rf"^{_re.escape(_asset)}\b\s*[:\-–]?\s*", "", _drv, count=1).strip()
                _ep["driver"] = _drv
    macro_ctx = data.get("macro_context") or {}
    if any(v is not None for v in macro_ctx.values()):
        existing = payload.get("macro_context") or {}
        existing.update({k: v for k, v in macro_ctx.items() if v is not None})
        payload["macro_context"] = existing

    # v18 (M-B12/M-B14) — GARDE-FOU MACRO DÉTERMINISTE. Si déjà calculé en amont
    # (run_morning l'injecte dans data AVANT Gemini pour aligner le récit), on le
    # reprend ; sinon on le calcule ici. Indépendant de l'optimisme de Gemini.
    _precomputed_guard = data.get("macro_guardrail")
    if _precomputed_guard is not None:
        if _precomputed_guard.get("active"):
            payload["macro_guardrail"] = _precomputed_guard
    else:
        _g = _compute_macro_guardrail(payload.get("macro_context") or {})
        if _g.get("active"):
            payload["macro_guardrail"] = _g
    # v19/M-B14 — FORÇAGE déterministe : si le garde-fou est actif, la lecture de
    # régime reflète la prudence quoi que dise Gemini (indépendance vis-à-vis du
    # récit IA). Sans effet si inactif.
    _apply_macro_guardrail_override(payload)

    # v19/W-B12 — espérance mathématique des recos (calculée Python en amont),
    # transmise telle quelle au rendu du matin (présente dans les 3 mails).
    if data.get("expectancy") is not None:
        payload["expectancy"] = data["expectancy"]

    # v19/V18-M10 — sources indisponibles nommées dans le footer (déterministe).
    if data.get("down_sources"):
        payload.setdefault("footer", {})["down_sources"] = data["down_sources"]

    # v19/Partie 5 (§4.2) — EXHIBER LE SCORE PONDÉRÉ : on attache à chaque thèse le
    # détail du scoring (score total + seuil + signaux par catégorie avec leur
    # poids) depuis data.eligible_theses (match par actif). Le mail affiche ainsi
    # « Score 6 (seuil 4) · MVRV<1 +3 · drawdown −63% +3 » — le soupçon de l'audit
    # était que ce scoring restait invisible ; il devient déterministe et visible.
    _elig_scoring = {}
    for _e in (data.get("eligible_theses") or []):
        if isinstance(_e, dict) and _e.get("asset") and _e.get("thesis_scoring"):
            _elig_scoring[_e["asset"]] = _e["thesis_scoring"]
    for _th in (payload.get("thesis_of_the_day") or []):
        if (isinstance(_th, dict) and _th.get("asset") in _elig_scoring
                and not _th.get("thesis_scoring")):
            _th["thesis_scoring"] = _elig_scoring[_th["asset"]]

    # v14.1 — liens actions ↔ crypto : chiffres Python (corr/β 30j) injectés
    # tels quels pour le rendu (l'IA ne peut pas les altérer). Quotes actions
    # passées aussi (cellule NVDA du bloc Actions US).
    links = data.get("equity_crypto_links") or {}
    if links.get("available"):
        payload["equity_crypto_links"] = links
    eqq = data.get("equity_quotes") or {}
    if eqq:
        payload["equity_quotes"] = eqq

    # Rotation sectorielle : on injecte directement les chiffres réels
    sec = (data.get("sector_rotation") or {}).get("sectors", {})
    if sec:
        rot_list = []
        for name, sd in sec.items():
            rot_list.append({
                "sector": name,
                "change_24h": round(sd.get("avg_change_24h") or 0, 2),
                "change_7d": sd.get("avg_change_7d"),
                "change_30d": sd.get("avg_change_30d"),
                "your_holdings": sd.get("members", []),
                "_weight": len(sd.get("members", []) or []) or 1,
            })
        # v18 (M-A23) : tri par AMPLEUR ABSOLUE de variation (|change|) décroissante
        # — un secteur à −20% passe avant un secteur à +15%, car son mouvement est
        # plus significatif. On affiche au MAX 5 cases : les 4 plus gros mouvements
        # + une 5e case « Autres secteurs » = moyenne PONDÉRÉE (par nb de membres,
        # proxy de la taille) des secteurs restants. Vue synthétique sur 1 ligne.
        rot_list.sort(key=lambda r: abs(r["change_24h"]), reverse=True)
        _MAX_INDIVIDUAL = 4
        if len(rot_list) > _MAX_INDIVIDUAL + 1:
            top = rot_list[:_MAX_INDIVIDUAL]
            rest = rot_list[_MAX_INDIVIDUAL:]
            _tot_w = sum(r["_weight"] for r in rest) or 1
            _avg = sum(r["change_24h"] * r["_weight"] for r in rest) / _tot_w
            # 7j/30j pondérés sur les secteurs restants qui les ont.
            _r7 = [r for r in rest if isinstance(r.get("change_7d"), (int, float))]
            _r30 = [r for r in rest if isinstance(r.get("change_30d"), (int, float))]
            _avg7 = (sum(r["change_7d"] * r["_weight"] for r in _r7)
                     / (sum(r["_weight"] for r in _r7) or 1)) if _r7 else None
            _avg30 = (sum(r["change_30d"] * r["_weight"] for r in _r30)
                      / (sum(r["_weight"] for r in _r30) or 1)) if _r30 else None
            top.append({
                "sector": f"Autres secteurs ({len(rest)})",
                "change_24h": round(_avg, 2),
                "change_7d": round(_avg7, 2) if _avg7 is not None else None,
                "change_30d": round(_avg30, 2) if _avg30 is not None else None,
                "your_holdings": [],
                "is_aggregate": True,
            })
            rot_list = top
        for r in rot_list:
            r.pop("_weight", None)
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
    # v17 (T-DEDUP / M-A2) : version dédupliquée + enrichie (1 ligne/actif).
    if data.get("active_recommendations_display") is not None:
        payload["active_recommendations_tracking"] = data["active_recommendations_display"]
    elif data.get("active_recommendations") is not None:
        payload["active_recommendations_tracking"] = data["active_recommendations"]
    # v18 (M-A8/M-B1) : le header doit dire « 0 nouvelle reco · 9 en suivi »
    # plutôt que « 0 reco ferme · 0 sous surveillance », qui laissait croire que
    # le PTF n'est sous AUCUN signal alors que 9 recos sont actives. On expose le
    # nombre de recos réellement en suivi (set dédupliqué).
    _tracked = payload.get("active_recommendations_tracking") or []
    payload.setdefault("header", {})["active_recos_count"] = len(_tracked)
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
    # v23.x (Omar — anti-doublon « Contexte global ») : le bloc « Données
    # quantitatives · référence » est SUPPRIMÉ (il dupliquait les tuiles on-chain).
    # Sa seule donnée unique = la volatilité implicite DVOL → ajoutée ici comme
    # TUILE déterministe (BTC+ETH) dans la grille on-chain, formatée proprement.
    _oi = payload.get("onchain_indicators")
    if isinstance(_oi, dict) and isinstance(_oi.get("metrics"), list):
        _opt = (data.get("options_deribit") or {}).get("assets") or {}
        _bd = _parse_num((_opt.get("BTC") or {}).get("dvol"))
        _ed = _parse_num((_opt.get("ETH") or {}).get("dvol"))
        _dv = " · ".join(p for p in (
            (f"BTC {_bd:.0f}" if _bd is not None else None),
            (f"ETH {_ed:.0f}" if _ed is not None else None),
        ) if p)
        _has_dvol = any("dvol" in str(m.get("label", "")).lower()
                        for m in _oi["metrics"] if isinstance(m, dict))
        if _dv and not _has_dvol:
            _oi["metrics"].append({
                "label": "Volatilité implicite (DVOL)", "value": _dv,
                "color": "#5a5852", "short": "vol. options annualisée",
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
    if data.get("portfolio_risk", {}).get("available"):
        payload["portfolio_risk"] = data["portfolio_risk"]
    if data.get("crypto_events", {}).get("available"):
        payload["crypto_events"] = data["crypto_events"]

    # M5 : hiérarchiser les thèses. action_type bullish/bearish = "action"
    # (décision à prendre), neutral = "watch" (surveillance). On trie pour
    # afficher les thèses actionnables en premier, puis par confiance décroissante.
    theses = payload.get("thesis_of_the_day") or []
    if isinstance(theses, list):
        # v14 (point 1C) / v23.x — filet de sécurité : on n'affiche QUE les thèses
        # de confiance >= THESIS_CONFIDENCE_FLOOR (75% — relevé de 60% par Omar pour
        # filtrer le bruit : seules les convictions FORTES et bien analysées passent).
        # Le prompt le demande déjà à Gemini ; ici on GARANTIT le seuil même si le
        # modèle le rate. Seuil unique = source de vérité.
        def _conf_ok(t: Any) -> bool:
            if not isinstance(t, dict):
                return False
            c = _coerce_confidence(t.get("confidence"))
            return c is not None and c >= THESIS_CONFIDENCE_FLOOR
        filtered = [t for t in theses if _conf_ok(t)]
        # Si Gemini avait produit des thèses mais TOUTES sont sous le seuil, on le
        # respecte STRICTEMENT : aucune thèse affichée (ne pas présenter une thèse
        # tiède comme « fondée »). On renseigne thesis_empty_reason pour que le
        # rendu affiche un message honnête plutôt qu'un vide brut.
        if not filtered and theses:
            if not payload.get("thesis_empty_reason"):
                # v18 (M-B11) : message explicite — on indique le niveau de
                # conviction atteint et ce qui manque, plutôt qu'un vide brut.
                _best = max((_coerce_confidence(t.get("confidence")) or 0)
                            for t in theses)
                _assets = ", ".join(
                    str(t.get("asset")) for t in theses[:3] if t.get("asset")
                )
                payload["thesis_empty_reason"] = (
                    f"Aucune thèse à conviction suffisante ce matin : les pistes "
                    f"étudiées ({_assets}) plafonnent à {_best:.0f}% de confiance, "
                    f"sous le seuil de {THESIS_CONFIDENCE_FLOOR}% requis pour une "
                    f"reco affichée. Il manque une convergence plus nette (cassure "
                    f"de niveau confirmée, signal on-chain franc ou catalyseur daté). "
                    f"On surveille, on n'agit pas dans le bruit."
                )
        theses = filtered
        # ── v15 (audit P0-4 / P1-8) — FILETS PYTHON sur les plans d'action.
        # 1. R:R aberrant : un ratio > 8:1 vient toujours d'un SL collé à
        #    l'entrée (-0,6% = trigeable par le bruit). RÈGLE 6 violée →
        #    bascule SURVEILLER automatique, plan retiré, raison affichée.
        # 2. SL incohérent : RENFORCER avec SL >= entrée (ou ALLÉGER avec
        #    SL <= entrée), ou SL à moins de 1,5% de l'entrée → même bascule.
        # 3. Action ferme SANS stop loss exploitable → bascule SURVEILLER
        #    (règle métier : « plan d'action complet, SL sous swing low réel »).
        _gated: list[dict[str, Any]] = []
        for t in theses:
            if not isinstance(t, dict):
                continue
            action_up = (t.get("action") or "").upper()
            is_firm = any(k in action_up for k in ("RENFORC", "ALLÉG", "ALLEG"))
            if not is_firm:
                _gated.append(t)
                continue
            ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
            entry = _parse_num(ap.get("entry"))
            sl = _parse_num(ap.get("stop_loss"))
            bearish = "ALLÉG" in action_up or "ALLEG" in action_up
            demote_reason = None
            if sl is None:
                demote_reason = ("plan sans stop loss exploitable — règle "
                                 "métier : SL ancré sous un swing low réel")
            elif entry:
                sl_dist_pct = abs(entry - sl) / entry * 100
                wrong_side = (not bearish and sl >= entry) or (bearish and sl <= entry)
                if wrong_side:
                    demote_reason = ("stop loss du mauvais côté de l'entrée "
                                     "(incohérence support/résistance)")
                elif sl_dist_pct < 1.5:
                    demote_reason = (f"SL à {sl_dist_pct:.1f}% de l'entrée — "
                                     "trigeable par le bruit, pas un vrai swing low")
            # R:R explicite fourni par Gemini ou calculable depuis TP/SL.
            rr_val = None
            rr_raw = ap.get("rr")
            if rr_raw:
                import re as _re_rr
                m_rr = _re_rr.search(r"(\d+(?:[.,]\d+)?)\s*:\s*1", str(rr_raw))
                if m_rr:
                    try:
                        rr_val = float(m_rr.group(1).replace(",", "."))
                    except ValueError:
                        rr_val = None
            if rr_val is None and entry and sl:
                tp_block = ap.get("take_profit")
                tp1 = None
                if isinstance(tp_block, dict):
                    for v in tp_block.values():
                        tp1 = _parse_num(v)
                        if tp1:
                            break
                else:
                    tp1 = _parse_num(tp_block)
                risk = abs(entry - sl)
                if tp1 and risk > 0:
                    rr_val = abs(tp1 - entry) / risk
            if demote_reason is None and rr_val is not None and rr_val > 8:
                demote_reason = (f"R:R calculé {rr_val:.1f}:1 > 8:1 — SL "
                                 "irréaliste, recalibrage requis")
            if demote_reason:
                t["action"] = "SURVEILLER"
                t["action_type"] = "neutral"
                t.pop("action_plan", None)
                t["demoted_by_python"] = True
                t["demotion_reason"] = demote_reason
                _wt = t.get("watch_trigger")
                t["watch_trigger"] = (_wt or
                    "Reco dégradée en SURVEILLER par le garde-fou Python : "
                    + demote_reason)
            elif rr_val is not None:
                # Sync badge (audit P2-12) : favorable seulement si 1.5–8.
                t["rr_value"] = round(rr_val, 1)
                t["rr_favorable"] = bool(1.5 <= rr_val <= 8)
            _gated.append(t)
        theses = _gated
        for t in theses:
            if not isinstance(t, dict):
                continue
            at = (t.get("action_type") or "").lower()
            t["priority"] = "action" if at in ("bullish", "bearish") else "watch"
        def _thesis_rank(t: dict) -> tuple:
            if not isinstance(t, dict):
                return (2, 0)
            prio = 0 if t.get("priority") == "action" else 1
            # Gemini renvoie parfois la confiance en string ("72%") :
            # sans coercition, -conf lève TypeError et fait planter le tri.
            conf = _coerce_confidence(t.get("confidence")) or 0
            return (prio, -conf)
        payload["thesis_of_the_day"] = _dedup_theses_by_asset(
            sorted(theses, key=_thesis_rank)
        )
        # v20 (audit A1) — le Morning détaillait ~14 thèses en prose (16 pages).
        # On n'EXPANSE désormais en prose complète que les 3 plus fortes
        # convictions (déjà triées en tête) ; les autres restent ENTIÈREMENT
        # résumées dans le tableau récap en tête de section (Actif · Action ·
        # Score · Entrée · Stop · R:R · Confiance). Aucune perte d'information
        # décisionnelle, mail ~3× plus court et scannable.
        # v23.x — le mail morning n'affiche QUE les recos FERMES ; on n'expanse en
        # prose complète que les 3 plus fortes convictions FERMES. Les SURVEILLER
        # ne sont jamais dépliées ni étoilées (mais restent dans thesis_of_the_day
        # pour le bot / l'état interne — suivi silencieux, comme demandé).
        _firm_seen = 0
        for _t in payload["thesis_of_the_day"]:
            if not isinstance(_t, dict):
                continue
            _is_firm = any(k in (_t.get("action") or "").upper()
                           for k in ("RENFORC", "ALLÉG", "ALLEG"))
            _t["_expand"] = bool(_is_firm and _firm_seen < 3)
            if _is_firm:
                _firm_seen += 1
        # v17 (T-FMT / M-A8) : filet — coercer en NOMBRE tous les champs prix des
        # thèses, au cas où Gemini renvoie une string pré-formatée (« 302,17 $ »).
        # Le rendu (fmt_money) reçoit ainsi toujours un float et formate de façon
        # homogène (un seul séparateur, un seul placement du $).
        # v23.x (deepthink projections) — bases déterministes : valeur totale du
        # PTF (pour la taille en $) + échafaudage de projection par actif (pour
        # ancrer/borner les cibles).
        _snap = data.get("portfolio_snapshot") or {}
        _ptf_total = (_snap.get("value_usd") or 0) + (_snap.get("usdc_usd") or 0)
        _proj_by_asset = {
            (e.get("asset") or "").upper(): e.get("projection")
            for e in (data.get("eligible_theses") or [])
            if isinstance(e, dict) and e.get("asset") and isinstance(e.get("projection"), dict)
        }
        # v23.x — prix actuel (à l'heure d'envoi) par actif, pour la colonne
        # « Actuel » du tableau des thèses. Source : prix live des eligible_theses
        # (repli : prix de l'échafaudage de projection).
        _price_by_asset = {
            (e.get("asset") or "").upper(): _parse_num(e.get("price"))
            for e in (data.get("eligible_theses") or [])
            if isinstance(e, dict) and e.get("asset")
        }
        for _t in payload["thesis_of_the_day"]:
            if not isinstance(_t, dict):
                continue
            _ca = (_t.get("asset") or "").upper()
            _cur = _price_by_asset.get(_ca)
            if _cur is None:
                _cur = _parse_num((_proj_by_asset.get(_ca) or {}).get("price"))
            if _cur is not None:
                _t["current_price"] = _cur
            _tg = _t.get("targets") if isinstance(_t.get("targets"), dict) else None
            if _tg:
                for _k in ("short_term_30d", "long_term_6_12m_low", "long_term_6_12m_high"):
                    if _tg.get(_k) is not None:
                        _n = _parse_num(_tg.get(_k))
                        if _n is not None:
                            _tg[_k] = _n
                # GARDE-FOU ANTI-CIBLE IRRÉALISTE : une cible 30j HAUSSIÈRE dont le
                # mouvement implicite dépasse largement (>1.8×) le plafond réaliste
                # (ATR×√30) est ramenée à la cible ANCRÉE (résistance/Fibo), avec
                # une note honnête. N'agit que si une cible ancrée existe.
                _proj = _proj_by_asset.get((_t.get("asset") or "").upper())
                if _proj and _proj.get("available"):
                    _rh = (_proj.get("volatility") or {}).get("realistic_30d_high_pct")
                    _stp = _proj.get("short_term_30d") or {}
                    _pp = _proj.get("price")
                    _tgt = _parse_num(_tg.get("short_term_30d"))
                    _bear = (_t.get("action_type") or "").lower() == "bearish"
                    if (_tgt and _pp and _rh and not _bear and _stp.get("target")):
                        _impl = (_tgt / _pp - 1.0) * 100.0
                        if _impl > _rh * 1.8:
                            _tg["short_term_30d"] = _stp["target"]
                            _tg["short_term_30d_capped"] = True
                            _tg["short_term_note"] = (
                                f"{_stp.get('move_pct', 0):+.1f}% · {_stp.get('basis')} "
                                "(cible ramenée au niveau réaliste · ATR 30j)"
                            )
            _ap = _t.get("action_plan") if isinstance(_t.get("action_plan"), dict) else None
            if _ap:
                for _k in ("entry", "stop_loss"):
                    if _ap.get(_k) is not None:
                        _n = _parse_num(_ap.get(_k))
                        if _n is not None:
                            _ap[_k] = _n
                _tp = _ap.get("take_profit") if isinstance(_ap.get("take_profit"), dict) else None
                if _tp:
                    for _k in list(_tp.keys()):
                        if _tp.get(_k) is not None:
                            _n = _parse_num(_tp.get(_k))
                            if _n is not None:
                                _tp[_k] = _n
                # v23.x — TAILLE EN $ (déterministe = % × valeur PTF), pour que le
                # plan d'action soit exécutable directement (Omar voit le montant).
                _psp = _parse_num(_ap.get("position_size_pct"))
                if _psp is not None and _ptf_total > 0:
                    _ap["position_size_usd"] = round(abs(_psp) / 100.0 * _ptf_total)
        # v15 (audit P1-5) — le header disait « 4 thèses fondées » alors que
        # tout était en SURVEILLER. Le compteur reflète désormais les actions
        # FERMES (RENFORCER/ALLÉGER) post-filtres ; le template affiche
        # « X recos fermes · Y sous surveillance ».
        _firm_n = sum(
            1 for t in theses if isinstance(t, dict)
            and any(k in (t.get("action") or "").upper()
                    for k in ("RENFORC", "ALLÉG", "ALLEG"))
        )
        hdr = payload.setdefault("header", {})
        hdr["firm_theses_count"] = _firm_n
        hdr["watch_theses_count"] = max(0, len(theses) - _firm_n)

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
        # v18 (M-A10) : déduplication MULTI-RUNS. L'audit a vu la même news
        # (« interdiction Claude Fable 5 ») affichée sur 2 runs successifs. On
        # retire les news dont la signature a déjà été montrée dans les 48h, SAUF
        # si Gemini la marque comme complément (champ `is_update`/`complement`).
        try:
            _seen = mem.load_seen_news(hours=48)
        except Exception:  # noqa: BLE001
            _seen = set()
        if _seen:
            _filtered = []
            for _n in payload["news_24h"]:
                if not isinstance(_n, dict):
                    continue
                _is_update = bool(_n.get("is_update") or _n.get("complement"))
                if (not _is_update) and mem.is_news_seen(_n.get("title") or "", _seen):
                    continue  # déjà vue récemment, pas un complément → on saute
                _filtered.append(_n)
            # Filet : si TOUT a été filtré (cas rare), on garde le tri d'origine
            # plutôt que d'afficher une section vide.
            if _filtered:
                payload["news_24h"] = _filtered
        # v15 (audit P2-4) — cap STRICT à 6 news (9 au même niveau = bruit) ;
        # le tri ci-dessus garantit que ce sont les plus importantes.
        payload["news_24h"] = payload["news_24h"][:6]
        # v18 (M-A10) : on enregistre les signatures des news RÉELLEMENT affichées
        # pour que les prochains runs (soir, lendemain) ne les répètent pas.
        try:
            mem.record_seen_news([
                _n.get("title") for _n in payload["news_24h"]
                if isinstance(_n, dict) and _n.get("title")
            ])
        except Exception:  # noqa: BLE001
            pass
        # v15 (audit P2-5) — dates ISO brutes → libellé FR (« hier 15:41 »).
        for n in payload["news_24h"]:
            if isinstance(n, dict) and n.get("timestamp"):
                _lbl = _fr_when(n["timestamp"])
                if _lbl:
                    n["timestamp_iso"] = n["timestamp"]
                    n["timestamp"] = _lbl
                else:
                    # v18 (M-A9) : si Gemini a écrit un libellé non-ISO contenant
                    # un mot relatif trompeur (« ce matin 02:01 »), on retire le
                    # mot relatif et on garde l'heure HH:MM seule si présente.
                    import re as _re
                    _raw = str(n["timestamp"])
                    _m = _re.search(r"(\d{1,2}[:hH]\d{2})", _raw)
                    if _m and _re.search(r"ce matin|aujourd'hui|ce soir|cette nuit", _raw, _re.I):
                        n["timestamp"] = _m.group(1).replace("h", ":").replace("H", ":")

    # ── V11/V12 — injections factuelles supplémentaires (priorité au calcul Python) ──
    # A3 (v12) : on n'injecte PLUS de grille de bêtas (macro_impact.exposed_positions)
    # — elle était illisible et souvent aberrante. Les bêtas restent calculés et
    # passés à l'IA via le digest (analytics_digest.per_asset_beta) pour nourrir
    # son ANALYSE, mais ne sont plus déversés tels quels dans le mail.
    # B6 — readout du régime macro (passe 1) : injecté si Gemini ne l'a pas rempli.
    regime = payload.get("macro_regime_pass1") or {}
    if regime and not payload.get("macro_regime_readout"):
        payload["macro_regime_readout"] = {
            "regime": regime.get("regime"),
            "confidence_pct": regime.get("confidence_pct"),
            "drivers": (
                " · ".join(regime["drivers"]) if isinstance(regime.get("drivers"), list)
                else regime.get("drivers")
            ),
            "crypto_bias": regime.get("crypto_bias"),
        }
    # B7 — bloc quantitatif compact (MVRV/options/corrélations) GARANTI à
    # l'affichage : les lignes du digest sont injectées pour rendu direct, en plus
    # de l'usage que Gemini en fait dans sa prose. (Bêtas exclus du rendu — A3.)
    dg = data.get("analytics_digest") or {}
    # v15 (audit P2-2) — corrélations quasi nulles (+0.23, +0.01) avec lecture
    # « lien ténu » = bruit. On n'affiche la ligne corrélations QUE si au
    # moins une |corr| >= 0.4 ; sinon elle est omise du bloc quantitatif.
    _corr_line = dg.get("macro_correlations")
    if _corr_line:
        import re as _re_c
        _vals = [abs(float(v.replace(",", ".")))
                 for v in _re_c.findall(r"[+\u2212-]?(\d+[.,]\d+)", str(_corr_line))]
        if not _vals or max(_vals) < 0.4:
            _corr_line = None
    quant = {k: v for k, v in {
        "onchain": dg.get("onchain_advanced"),
        "options": dg.get("options"),
        "correlations": _corr_line,
    }.items() if v}
    if quant:
        payload["quant_reference"] = quant
    # C1 — note(s) de contradiction de données (référence ² discrète).
    contra = data.get("data_contradictions") or {}
    if contra.get("has_any"):
        payload["data_contradictions"] = contra
    # B5 — score de risque PTF (conservé pour le bot /risque ; PLUS affiché dans
    # le mail, remplacé par la note de SANTÉ ci-dessous).
    if data.get("risk_score"):
        payload["risk_score"] = data["risk_score"]
        payload["risk_score_readout"] = _build_risk_readout(data["risk_score"])
    # v23 — note de SANTÉ PTF affichée dans le mail (réflective + comment améliorer).
    if data.get("health_score"):
        payload["health_score"] = data["health_score"]
    # B2 — libellé de tier garanti dans chaque thèse (depuis les données éligibles),
    # au cas où l'IA ne l'aurait pas recopié.
    _tier_by_asset = {
        e.get("asset"): e.get("tier_label")
        for e in (data.get("eligible_theses") or []) if e.get("tier_label")
    }
    for _t in (payload.get("thesis_of_the_day") or []):
        if isinstance(_t, dict) and not _t.get("tier_label"):
            tl = _tier_by_asset.get(_t.get("asset"))
            if tl:
                _t["tier_label"] = tl

    # ── v15 — NORMALISATION des champs au nouveau format (le template gère un
    # format unique ; si Gemini renvoie l'ancien, on convertit ici).
    _ex = payload.get("executive_summary")
    if isinstance(_ex, str) and _ex.strip():
        payload["executive_summary"] = {
            "bullets": [{"icon": "⚠", "text": s.strip()}
                        for s in _ex.replace("\n", " ").split(". ") if s.strip()][:5]
        }
    elif isinstance(_ex, dict):
        _bl = []
        for b in (_ex.get("bullets") or [])[:5]:
            if isinstance(b, dict) and b.get("text"):
                _ic = str(b.get("icon") or "⚠")
                _bl.append({"icon": _ic if _ic in ("✓", "⚠", "✗") else "⚠",
                            "text": str(b["text"])})
            elif isinstance(b, str) and b.strip():
                _bl.append({"icon": "⚠", "text": b.strip()})
        payload["executive_summary"] = {"bullets": _bl} if _bl else None
    _sc_g = payload.get("self_critique_global")
    if isinstance(_sc_g, str) and _sc_g.strip():
        payload["self_critique_global"] = {"bullets": [_sc_g.strip()]}
    elif isinstance(_sc_g, dict):
        _scb = [str(x).strip() for x in (_sc_g.get("bullets") or [])
                if str(x).strip()][:4]
        payload["self_critique_global"] = {"bullets": _scb} if _scb else None
    _inv = payload.get("invalidation_watch")
    if isinstance(_inv, str) and _inv.strip():
        payload["invalidation_watch"] = [{"condition": _inv.strip(),
                                          "implication": ""}]
    elif isinstance(_inv, list):
        _invn = []
        for it in _inv[:4]:
            if isinstance(it, dict) and it.get("condition"):
                _invn.append({"condition": str(it["condition"]),
                              "implication": str(it.get("implication") or "")})
            elif isinstance(it, str) and it.strip():
                _invn.append({"condition": it.strip(), "implication": ""})
        payload["invalidation_watch"] = _invn or None
    # Confiance news : Gemini renvoie parfois /5 (« Confiance 4% » de l'audit).
    # v16 — plafond à 80 par défaut : Gemini est chroniquement sur-confiant
    # (95% sur des interprétations). On ne laisse passer > 80 que si la news
    # est explicitement un fait certain (tag/catégorie « Macro » sur une
    # publication officielle reste interprétable, donc on cape tout > 85 à 80).
    for _n in (payload.get("news_24h") or []):
        if isinstance(_n, dict):
            _cv = _n.get("confidence")
            if isinstance(_cv, (int, float)) and 0 < _cv <= 5:
                _cv = int(_cv * 20)
                _n["confidence"] = _cv
            if isinstance(_cv, (int, float)) and _cv > 85:
                _n["confidence"] = 80

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
        # v19 (Partie 6 — COHÉRENCE) : ne pas ré-émettre une reco qu'Omar vient
        # d'écarter via /dismiss (anti ré-émission 48h). Évite le « je l'écarte,
        # elle revient le lendemain » signalé comme incohérence.
        if mem.is_recently_dismissed(asset, canonical):
            logger.info("Reco %s %s ignorée : écartée récemment via /dismiss.",
                        asset, canonical)
            continue
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


def _confidence_caps_from_data(data: dict[str, Any]) -> dict[str, int]:
    """P0 #60 — plafonds de confiance par actif issus de la complétude d'analyse.

    Extrait, pour chaque thèse éligible, le plafond ``confidence_bounds.cap`` (qui
    intègre déjà la complétude) afin que le coherence_checker borne, côté Python,
    toute confiance excessive émise par Gemini sur une analyse incomplète.
    """
    caps: dict[str, int] = {}
    for e in (data.get("eligible_theses") or []):
        ts = e.get("thesis_scoring") or {}
        cap = (ts.get("confidence_bounds") or {}).get("cap")
        asset = (e.get("asset") or "").upper()
        if asset and isinstance(cap, (int, float)):
            caps[asset] = int(cap)
    return caps


def run_morning() -> int:
    """Génère et envoie le rapport du matin."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT MATIN ===")
    portfolio_data = load_portfolio()
    data = _collect_morning_data(portfolio_data)
    # v18 (M-B12/M-B14) : garde-fou macro calculé AVANT Gemini → injecté dans data
    # pour que le récit de Gemini s'y conforme, ET réutilisé après-coup pour la
    # bannière déterministe (même source).
    data["macro_guardrail"] = _compute_macro_guardrail(data.get("macro_context") or {})
    evening_state = mem.load_evening_report()
    engine = DecisionEngine()
    payload = engine.generate_morning(
        timestamp=_now_str(), data=data, portfolio_data=portfolio_data,
        evening_state=evening_state,
    )
    # FUSION : on écrase les champs factuels avec les valeurs Python.
    payload = _merge_python_facts(payload, data, _now_str())
    checked = check_report(payload, _confidence_caps_from_data(data))
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
    # v17 (T-TAO / M-A1) : résumé des postures FERMES du matin (1/actif), pour que
    # le soir et le weekly se réconcilient au lieu de se contredire (matin
    # RENFORCER TAO / soir ALLÉGER / weekly SORTIE). Direction nette par actif.
    _firm_postures: dict[str, dict[str, Any]] = {}
    for _th in (payload.get("thesis_of_the_day") or []):
        if not isinstance(_th, dict):
            continue
        _a = _th.get("asset")
        _act = (_th.get("action") or "").upper()
        if not _a or _a in _firm_postures:
            continue
        if any(k in _act for k in ("RENFORC", "ALLÉG", "ALLEG")):
            _ap = _th.get("action_plan") if isinstance(_th.get("action_plan"), dict) else {}
            _firm_postures[_a] = {
                "action": "RENFORCER" if "RENFORC" in _act else "ALLÉGER",
                "entry": _ap.get("entry"),
                "stop_loss": _ap.get("stop_loss"),
                "target": _first_take_profit(_ap),  # v23.x : prix cible (TP1) pour le bilan soir
                "confidence": _coerce_confidence(_th.get("confidence")),
            }
    payload["firm_postures"] = _firm_postures
    # v18.1 — persiste les 17 signaux croisés déterministes dans le payload, pour
    # qu'ils soient disponibles au bot Telegram (contexte complet) et à la
    # relecture. Sans nuisance pour le rendu mail (clé ignorée par le template).
    _xs = data.get("cross_signals")
    if isinstance(_xs, dict) and (_xs.get("signals") or _xs.get("readings")):
        payload["cross_signals"] = _xs
    mem.save_morning_report(payload)
    # Graphiques prix+Bollinger pour les thèses DÉTAILLÉES (top-3 dépliées en
    # prose, audit A1). limit=3 pour coller au détail rendu : générer un 4e
    # graphique l'attacherait en CID sans qu'il soit jamais référencé (orphelin).
    from src.reporting import charts
    chart_imgs = charts.charts_for_theses(payload.get("thesis_of_the_day") or [], limit=3)
    html = _render(payload, "morning", charts=chart_imgs)
    # v20 (audit C1) \u2014 images attach\u00e9es en CID (cl\u00e9 \u00ab chart_<ASSET> \u00bb), Gmail-safe.
    inline = {f"chart_{sym}": png for sym, png in chart_imgs.items() if png}
    ok = send_email(
        f"\u2600\ufe0f Veille crypto \u00b7 matin \u00b7 {datetime.now(TZ):%d/%m}",
        html,
        inline_images=inline,
    )
    logger.info("Matin: %s (cohérence: %d corr · %d graphiques)",
                ok, len(checked["warnings"]), len(chart_imgs))
    _push_telegram_notification(payload, "morning")
    return 0 if ok else 1


def _parse_num(value: Any) -> Optional[float]:
    """Parse un nombre depuis un float ou une string ('63180', '1,679.33',
    '63 180 $', '69.637,63 $', '0,0014'). Renvoie None si non parsable/non fini.

    v14.1 — FIX format français. L'ancien code supprimait toutes les virgules :
    « 69.637,63 » (le propre format ``fmt_money`` du projet !) devenait 69.637
    au lieu de 69637,63 — bilan recos faussé si l'IA recopie un prix formaté.
    Désormais : si '.' ET ',' coexistent, le séparateur le PLUS À DROITE est la
    décimale (l'autre = milliers) ; une ',' seule suivie de ≤2 chiffres en fin
    de chaîne (ou d'une longue mantisse type '0,0014') = décimale française.
    """
    import math as _m
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if _m.isfinite(float(value)) else None
    if isinstance(value, str):
        cleaned = (
            value.replace("$", "").replace("€", "").replace("\u202f", "")
            .replace("\xa0", "").replace(" ", "").strip()
        )
        if not cleaned:
            return None
        has_dot, has_comma = "." in cleaned, "," in cleaned
        if has_dot and has_comma:
            # Le séparateur le plus à droite est la décimale.
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")   # 69.637,63
            else:
                cleaned = cleaned.replace(",", "")                     # 1,679.33
        elif has_comma:
            head, _, tail = cleaned.rpartition(",")
            if tail.isdigit() and len(tail) == 3 and head and "," not in head:
                # « 63,180 » : très probablement milliers US (3 chiffres pile).
                cleaned = cleaned.replace(",", "")
            else:
                # « 69637,63 » / « 0,0014 » / « 1,5 » : décimale française.
                cleaned = cleaned.replace(",", ".")
        try:
            v = float(cleaned)
            return v if _m.isfinite(v) else None
        except (ValueError, TypeError):
            return None
    return None


def _first_take_profit(action_plan: Any) -> Optional[float]:
    """Premier niveau de take-profit exploitable d'un ``action_plan`` (TP1).

    ``take_profit`` est tantôt un dict de niveaux (``{"tp1": …, "tp2": …}``),
    tantôt un scalaire. On renvoie le premier niveau numérique, sinon ``None``.
    """
    tp = action_plan.get("take_profit") if isinstance(action_plan, dict) else None
    if isinstance(tp, dict):
        for v in tp.values():
            n = _parse_num(v)
            if n:
                return n
        return None
    return _parse_num(tp)


def _reco_bilan_status(
    action: str, entry: Optional[float], cur: Optional[float], sl: Optional[float]
) -> tuple[str, str]:
    """Statut déterministe + raison courte ET utile du bilan soir (v23.x).

    Renvoie ``(status, reason)`` où ``status`` ∈ on_track / under_pressure /
    invalidated / pending et ``reason`` explique en quelques mots POURQUOI, en
    citant le NIVEAU d'invalidation (ex. « repli sous l'entrée · invalidé sous
    $60,500 »). ``cur`` = cours à l'heure d'envoi.
    """
    from src.reporting.email_html import _fmt_money

    bearish = "ALLÉG" in action or "ALLEG" in action
    if not (entry and cur and entry > 0):
        return "pending", "prix d'entrée ou cours indisponible"
    sl_txt = _fmt_money(sl) if sl else None
    inval = (f"invalidé {'au-dessus de' if bearish else 'sous'} {sl_txt}"
             if sl_txt else "pas de niveau d'invalidation défini")
    # Stop franchi : la thèse est cassée.
    if sl and ((not bearish and cur < sl) or (bearish and cur > sl)):
        return "invalidated", (f"stop {sl_txt} franchi" if sl_txt else "stop franchi")
    delta = (cur - entry) / entry * 100
    on_track = (not bearish and delta >= 0) or (bearish and delta <= 0)
    if on_track:
        good = "baisse conforme à la thèse" if bearish else "au-dessus de l'entrée"
        return "on_track", f"{good} · {inval}"
    bad = "rebond contre la thèse" if bearish else "repli sous l'entrée"
    return "under_pressure", f"{bad} · {inval}"


def _build_evening_reco_bilan(
    morning_state: dict[str, Any], market: dict[str, Any]
) -> list[dict[str, Any]]:
    """BLOC 6 — bilan des recos FERMES du dernier matin, 1 ligne/actif.

    100% Python (Gemini ne touche pas). v23.x : ne liste QUE les recos
    actionnables (RENFORCER / ALLÉGER). Les actifs simplement surveillés ne sont
    plus affichés (suivi interne silencieux) — si un signal flashe, ils
    deviennent une vraie reco ferme et réapparaissent ici. Chaque ligne porte :
    prix d'entrée, prix cible (TP1), prix actuel (à l'heure d'envoi), variation
    vs entrée, confiance du matin et un statut explicite (raison + niveau
    d'invalidation). Dédup par actif ; firm_postures (source de vérité) prime.
    """
    firm_postures = (morning_state or {}).get("firm_postures") or {}
    theses = (morning_state or {}).get("thesis_of_the_day") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _emit(asset: str, action_raw: str, entry: Any, sl: Any,
              target: Any, confidence: Any) -> None:
        action = "RENFORCER" if "RENFORC" in action_raw else "ALLÉGER"
        entry = _parse_num(entry)
        sl = _parse_num(sl)
        cur = _parse_num((market.get(asset) or {}).get("price"))
        delta_pct = (round((cur - entry) / entry * 100, 2)
                     if entry and cur and entry > 0 else None)
        status, reason = _reco_bilan_status(action, entry, cur, sl)
        out.append({
            "asset": asset, "action": action,
            "confidence": _coerce_confidence(confidence),
            "entry": entry, "target": _parse_num(target), "current": cur,
            "delta_pct": delta_pct, "status": status, "reason": reason,
        })

    # 1) Postures fermes persistées (BTC/TAO/… RENFORCER/ALLÉGER), source de vérité.
    for asset, fp in firm_postures.items():
        if not isinstance(fp, dict) or asset in seen:
            continue
        seen.add(asset)
        _emit(asset, (fp.get("action") or "").upper(), fp.get("entry"),
              fp.get("stop_loss"), fp.get("target"), fp.get("confidence"))
    # 2) Filet : thèses fermes non persistées. Les SURVEILLER/MAINTENIR (sans
    #    direction actionnable) sont volontairement IGNORÉES — plus de bruit.
    for t in theses:
        if not isinstance(t, dict):
            continue
        asset = t.get("asset")
        action_raw = (t.get("action") or "").upper()
        if (not asset or asset in seen
                or not any(k in action_raw for k in ("RENFORC", "ALLÉG", "ALLEG"))):
            continue
        seen.add(asset)
        ap = t.get("action_plan") or {}
        _emit(asset, action_raw, ap.get("entry"), ap.get("stop_loss"),
              _first_take_profit(ap), t.get("confidence"))
    return out


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
    # v15 — Polymarket étendu (barres Fed + événements majeurs), comme le matin.
    polymarket = prediction_markets.get_key_markets()
    # v14.1 — scrape Boursorama RETIRÉ ici : le résultat n'était jamais lu
    # (B8 = calendrier FRED exclusivement depuis v14). Appel réseau économisé.
    morning_state = mem.load_morning_report()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    active = tracker.refresh_active(price_lookup)

    # Delta de valeur du portfolio depuis le matin (basé sur le snapshot stocké).
    # Valeur dynamique crypto-only (qté × prix live), cohérente avec le snapshot.
    morning_snap = morning_state.get("portfolio_snapshot") or {}
    current_value = sum(_position_value(portfolio[s], market.get(s)) for s in symbols)
    # v17 (T-STATE / E-A4) : ne calcule un delta « depuis matin » QUE si le state
    # matin est d'aujourd'hui ET récent. Sinon la baseline est périmée → delta
    # factice. _morning_baseline = None ⇒ le rendu affiche « depuis le matin »
    # neutre, pas un faux +2,1%.
    _morning_baseline_ok = bool(morning_snap.get("value_usd"))
    # (raffiné plus bas une fois morning_is_today calculé)

    # BLOC 1 (v14) : écart horaire RÉEL depuis l'heure d'envoi du matin
    # (morning_state._saved_at, ISO UTC), et non un 08h30 théorique. Si le matin
    # n'a pas tourné aujourd'hui, on retombe proprement sur l'écart vs 08h30.
    now_local = datetime.now(TZ)
    morning_saved_at = morning_state.get("_saved_at") if isinstance(morning_state, dict) else None
    # v17 (T-STATE / E-A4/E-A5/E-A8) : le state matin est-il VRAIMENT d'aujourd'hui ?
    # Si le matin n'a pas tourné aujourd'hui (state périmé d'un run précédent), les
    # « depuis matin », le label timing et le risk_score repris sont FACTICES — on
    # les neutralise plutôt que d'afficher de faux deltas (« +2,1% depuis matin »
    # sur une valeur identique au matin, « matin 14h17 · Δ12h » aberrant).
    morning_is_today = False
    if morning_saved_at:
        try:
            _ms_check = datetime.fromisoformat(str(morning_saved_at).replace("Z", "+00:00"))
            if _ms_check.tzinfo is None:
                _ms_check = _ms_check.replace(tzinfo=timezone.utc)
            morning_is_today = _ms_check.astimezone(TZ).date() == now_local.date()
        except (ValueError, TypeError):
            morning_is_today = False
    # v17 (T-STATE / E-A4) : delta « depuis matin » uniquement si baseline fraîche.
    if _morning_baseline_ok and morning_is_today:
        delta_morning = current_value - morning_snap["value_usd"]
    else:
        delta_morning = 0.0  # baseline périmée/absente → pas de faux delta intraday
    morning_time_label = None  # ex. "08h32" — heure réelle du matin (Casablanca)
    since_morning_label = None  # ex. "il y a 11h" / "il y a 23min"
    hours_since_morning = max(1, round(now_local.hour + now_local.minute / 60 - 8.5))
    if morning_saved_at:
        try:
            _ms = datetime.fromisoformat(str(morning_saved_at).replace("Z", "+00:00"))
            if _ms.tzinfo is None:
                _ms = _ms.replace(tzinfo=timezone.utc)
            _ms_local = _ms.astimezone(TZ)
            morning_time_label = _ms_local.strftime("%Hh%M")
            _delta_min = max(0, int((now_local - _ms_local).total_seconds() // 60))
            if _delta_min < 60:
                since_morning_label = f"il y a {_delta_min}min"
                hours_since_morning = max(1, round(_delta_min / 60))
            else:
                _h = round(_delta_min / 60)
                since_morning_label = f"il y a {_h}h"
                hours_since_morning = _h
        except (ValueError, TypeError):
            pass

    # v19/WS3 — FENÊTRE DÉGÉNÉRÉE (run hors-cycle / rattrapage). Si l'Evening
    # tourne très peu après le Morning (< 4h), les sections « depuis ce matin »
    # (évolution marché, news intraday, P&L jour, F&G) n'ont pas de sens : on
    # calcule le délai réel et un flag pour adapter le wording (mode rejeu).
    _minutes_since_morning = None
    if morning_saved_at and morning_is_today:
        try:
            _ms3 = datetime.fromisoformat(str(morning_saved_at).replace("Z", "+00:00"))
            if _ms3.tzinfo is None:
                _ms3 = _ms3.replace(tzinfo=timezone.utc)
            _minutes_since_morning = max(
                0, int((now_local - _ms3.astimezone(TZ)).total_seconds() // 60))
        except (ValueError, TypeError):
            _minutes_since_morning = None
    _degenerate_window = (
        _minutes_since_morning is not None and _minutes_since_morning < 240
    )
    # E-B3 — « ce qui est tombé depuis ce matin » : on ne garde que les news
    # RÉELLEMENT postérieures à l'envoi du matin (les antérieures appartiennent au
    # Morning). Les items sans horodatage exploitable sont conservés (prudence).
    if morning_is_today and morning_saved_at:
        try:
            _morning_dt = datetime.fromisoformat(
                str(morning_saved_at).replace("Z", "+00:00"))
            if _morning_dt.tzinfo is None:
                _morning_dt = _morning_dt.replace(tzinfo=timezone.utc)

            def _after_morning(_n: dict[str, Any]) -> bool:
                _ts = _n.get("published_at") or _n.get("published_iso")
                if not _ts:
                    return True  # pas d'horodatage → on ne peut pas l'exclure
                try:
                    _d = datetime.fromisoformat(str(_ts).replace("Z", "+00:00"))
                    if _d.tzinfo is None:
                        _d = _d.replace(tzinfo=timezone.utc)
                    return _d >= _morning_dt
                except (ValueError, TypeError):
                    return True

            news_global = [n for n in news_global if _after_morning(n)]
        except (ValueError, TypeError):
            pass

    # S5 : bilan P&L du jour + top movers (sur la base des variations 24h).
    # A7 — P&L PAR POSITION EN $ (24h). Le % seul masque l'impact réel : −15%
    # sur une ligne à $9 ≠ −15% sur une ligne à $64. On calcule donc le P&L 24h
    # en dollars par position (déterministe, jamais halluciné). Base cohérente
    # avec le snapshot matin : valeur_position × (variation_24h / 100).
    movers = []
    for s in symbols:
        ch = market.get(s, {}).get("change_24h")
        if ch is None:
            continue
        pos_val = _position_value(portfolio[s], market.get(s))
        # P2-A5 — on ignore les poussières (< 10 $) : pas d'analyse ni d'affichage,
        # leur P&L en $ est négligeable et pollue la lecture.
        if pos_val < 10:
            continue
        movers.append(
            {
                "symbol": s,
                "change": round(ch, 1),
                "pnl_usd": round(pos_val * (ch / 100.0), 2),
                "value_usd": round(pos_val, 2),
            }
        )
    movers.sort(key=lambda m: abs(m["change"]), reverse=True)
    movers = movers[:5]
    _day_pct = (round(delta_morning / morning_snap["value_usd"] * 100, 2)
                if morning_snap.get("value_usd") else None)
    daily_pnl = {
        "value_usd": round(current_value, 2),
        "day_change_usd": round(delta_morning, 2),
        "day_change_pct": _day_pct,
        # v15 (audit P0) : « +0.0% avec +$1 » mangeait le signal. 2 décimales
        # au rendu + label explicite quand le mouvement est réellement neutre.
        "day_change_label": ("neutre" if _day_pct is not None
                             and abs(_day_pct) < 0.05 else None),
        "top_movers": movers,
    }
    # v15 (audit evening P1) : positions ayant bougé > ±8% sur 24h — injectées
    # au prompt pour que levels_tonight propose résistance/TP dessus (« si IMX
    # fait +12%, le soir doit proposer un niveau »). Calcul Python, dust exclu.
    big_movers_day = [
        {"symbol": m["symbol"], "change_24h": m["change"],
         "price": (market.get(m["symbol"]) or {}).get("price")}
        for m in movers if abs(m["change"]) >= 8
    ]

    # S3 : macro de clôture US (S&P, Nasdaq, DXY) — dispo en soirée Casablanca.
    ev_yahoo_quotes = market_prices.get_macro_quotes()
    ev_yahoo_deltas = market_prices.get_macro_deltas()
    ev_macro_ctx = _macro_context(
        market, fng, macro, polymarket, ev_yahoo_quotes, ev_yahoo_deltas
    )
    # v17 (E-A6) : F&G du matin (pour un delta intraday réel, pas hebdo).
    _mr_macro = (morning_state or {}).get("macro_context") or {}
    _mr_fng = _mr_macro.get("fear_greed")
    if not isinstance(_mr_fng, (int, float)):
        # repli : certains états stockent le F&G ailleurs.
        _mr_fng = ((morning_state or {}).get("evening_macro") or {}).get("fear_greed")
    evening_macro = {
        # Crypto & sentiment (BLOC 3 ligne 1).
        "btc_price": ev_macro_ctx.get("btc_price"),
        "btc_change_24h": ev_macro_ctx.get("btc_change_24h"),
        # v19/V18-E9/E-A14 — prix ETH : référence pour les niveaux ETH du soir
        # (l'Evening proposait des niveaux 1800/1750/1730 sans prix actuel).
        "eth_price": (market.get("ETH") or {}).get("price"),
        "eth_change_24h": (market.get("ETH") or {}).get("change_24h"),
        "fear_greed": ev_macro_ctx.get("fear_greed"),
        "fear_greed_label": ev_macro_ctx.get("fear_greed_label"),
        # v17 (E-A6) : delta F&G DEPUIS LE MATIN (pas le delta hebdo). Si le matin
        # affichait déjà 18 et qu'on est à 18, le delta intraday = 0 → pas de
        # fausse flèche « ▲ +5 ». On ne reprend le delta journalier que si le
        # matin n'est pas d'aujourd'hui (sinon on calcule l'écart réel).
        "fear_greed_delta": (
            (ev_macro_ctx.get("fear_greed") - _mr_fng)
            if (morning_is_today and isinstance(ev_macro_ctx.get("fear_greed"), (int, float))
                and isinstance(_mr_fng, (int, float)))
            else ev_macro_ctx.get("fear_greed_delta")
        ),
        "gold_usd": ev_macro_ctx.get("gold_usd"),
        "gold_delta": ev_macro_ctx.get("gold_delta"),
        # Actions & taux (BLOC 3 ligne 2).
        "sp500": ev_macro_ctx.get("sp500"),
        "sp500_delta": ev_macro_ctx.get("sp500_delta"),
        "nasdaq": ev_macro_ctx.get("nasdaq"),
        "nasdaq_delta": ev_macro_ctx.get("nasdaq_delta"),
        "vix": ev_macro_ctx.get("vix"),
        "vix_delta": ev_macro_ctx.get("vix_delta"),
        "brent_usd": ev_macro_ctx.get("brent_usd"),
        "brent_delta": ev_macro_ctx.get("brent_delta"),
        "dxy": ev_macro_ctx.get("dxy"),
        "dxy_broad": ev_macro_ctx.get("dxy_broad"),
        "dxy_ice": ev_macro_ctx.get("dxy_ice"),
        "dxy_delta": ev_macro_ctx.get("dxy_delta"),
        # v14.1 — international (contexte IA UNIQUEMENT : la structure 8 blocs
        # du mail soir est figée, B3 reste 2×4 cellules — ne rien y rajouter).
        "nikkei": ev_macro_ctx.get("nikkei"),
        "nikkei_delta": ev_macro_ctx.get("nikkei_delta"),
        "stoxx50": ev_macro_ctx.get("stoxx50"),
        "stoxx50_delta": ev_macro_ctx.get("stoxx50_delta"),
        "eur_usd": ev_macro_ctx.get("eur_usd"),
        "usd_jpy": ev_macro_ctx.get("usd_jpy"),
        "ecb_deposit_rate": ev_macro_ctx.get("ecb_deposit_rate"),
        "boj_rate": ev_macro_ctx.get("boj_rate"),
    }
    # Actions liées crypto en séance US (mi-séance à 20h Casa) — contexte IA.
    ev_equity_quotes = market_prices.get_equity_quotes()
    # Statut de fiabilité macro (pastilles ² du rendu soir) — calculé Python,
    # alimente _mss_e dans le template. Sans ça les pastilles seraient vides.
    ev_macro_source_status = market_prices.compute_macro_source_status(
        ev_macro_ctx, ev_yahoo_quotes, (macro or {}).get("series"), tolerance_pct=10.0
    )

    # v15 — ÉVÉNEMENTS MACRO À VENIR : calendrier CONSOLIDÉ (FRED dates réelles
    # + Boursorama + décisions FOMC/BoJ officielles + récurrences « estimé »).
    # v16 — CORRECTION BUG TEMPORALITÉ : la checklist « demain matin » ne doit
    # contenir QUE les événements des 2 PROCHAINS JOURS CALENDAIRES (le weekly
    # couvre la semaine). On scinde : tomorrow_macro_events (≤ 2 j, pour la
    # checklist du soir) et upcoming_macro_events (fenêtre 7 j, contexte). Chaque
    # entrée garde son « when » réel (« demain », « dans 4j ») — plus jamais un
    # événement à J+4 présenté comme « demain ».
    from src.data_sources import macro_calendar as _mc
    tomorrow_macro_events: list[dict[str, Any]] = []
    upcoming_macro_events: list[dict[str, Any]] = []
    ev_upcoming = _mc.get_consolidated_calendar(horizon_days=7)
    if ev_upcoming.get("available"):
        for e in ev_upcoming.get("events", []):
            _entry = {
                "label": e.get("label"),
                "date": e.get("date"),
                "when": e.get("when"),
                "days_ahead": e.get("days_ahead"),
                "source": e.get("source"),
                "importance": e.get("importance"),
                # v18 (E-A15) : libellé jour/date propre déjà calculé à la source
                # (« mardi 16 juin ») → permet d'éviter la triple redondance
                # « Demain / 48h : dans 2j … (2026-06-16) » dans le rendu.
                "weekday_label": e.get("weekday_label"),
                "date_label": e.get("date_label"),
            }
            upcoming_macro_events.append(_entry)
            _da = e.get("days_ahead")
            if isinstance(_da, int) and _da <= 2:
                tomorrow_macro_events.append(_entry)

    data = {
        "prices_now": price_lookup,
        "changes_24h": {s: market.get(s, {}).get("change_24h") for s in symbols},
        "fear_greed": fng, "etf_flows": etf, "news_12h": news_global[:8],
        "active_recommendations": active,
        "daily_pnl": daily_pnl, "evening_macro": evening_macro,
        "equity_quotes": ev_equity_quotes,
        "tomorrow_macro_events": tomorrow_macro_events,
        "upcoming_macro_events": upcoming_macro_events,
        "hours_since_morning": hours_since_morning,
        # v19/WS3 — fenêtre du run (rejeu si l'Evening suit le Morning de près).
        "run_window": {
            "degenerate": _degenerate_window,
            "minutes_since_morning": _minutes_since_morning,
            "morning_time_label": morning_time_label,
        },
        # v15 — movers > ±8% du jour (pour levels_tonight) + Polymarket étendu.
        "big_movers_day": big_movers_day,
        "polymarket": {
            "fed_bars": polymarket.get("fed_bars"),
            "extra_markets": polymarket.get("extra_markets"),
        } if polymarket.get("available") else {},
    }
    engine = DecisionEngine()
    payload = engine.generate_evening(
        timestamp=_now_str(), data=data, morning_state=morning_state,
    )
    checked = check_report(payload, _confidence_caps_from_data(data))
    payload = checked["sanitized_payload"]
    # v19/W-B12 — espérance mathématique des recos AUSSI dans le soir (présente
    # dans les 3 mails). Calculée Python, vide tant que < 5 recos clôturées.
    payload["expectancy"] = tracker.compute_expectancy(30)
    # v16.1 — filet : chaque puce delta_summary DOIT être un objet {icon, text}
    # avec un icon parmi ✓/⚠/✗. Si Gemini renvoie une string brute ou un autre
    # symbole (ex. '→'), on normalise en ⚠ pour éviter le rendu en flèche.
    _ds = payload.get("delta_summary")
    if isinstance(_ds, list):
        _valid_icons = {"✓", "⚠", "✗"}
        _ds_norm = []
        for d in _ds:
            if isinstance(d, dict):
                _ic = (d.get("icon") or "").strip()
                _txt = d.get("text") or d.get("label") or ""
                if _ic not in _valid_icons:
                    _ic = "⚠"
                if str(_txt).strip():
                    _ds_norm.append({"icon": _ic, "text": _txt})
            elif isinstance(d, str) and d.strip():
                _ds_norm.append({"icon": "⚠", "text": d.strip()})
        payload["delta_summary"] = _ds_norm or None
    header = payload.setdefault("header", {})
    header["date"] = _now_str()
    header["time_casablanca"] = _now_str()
    header["hours_since_morning"] = hours_since_morning
    header["morning_time_label"] = morning_time_label
    header["since_morning_label"] = since_morning_label
    # v23 — winrate 30j en TÊTE du soir (présent une fois dans chaque mail).
    try:
        _wr30_ev = tracker.compute_win_rate(30)
        header["win_rate_30d"] = _wr30_ev.get("win_rate_pct")
        header["win_rate_total"] = (
            f"{_wr30_ev.get('validated', 0)}/{_wr30_ev.get('total', 0)}")
    except Exception:  # noqa: BLE001
        pass
    # v15 (audit B4) : sous-titre FACTUEL « matin 10h14 · soir 19h32 · Δ9h »
    # — heure réelle de LANCEMENT du rapport, pas l'heure cron théorique.
    _ev_time = now_local.strftime("%Hh%M")
    header["evening_time_label"] = _ev_time
    # v17 (T-STATE / E-A5) : n'affiche le label « matin … · Δ… » QUE si le matin
    # a tourné aujourd'hui. Sinon (state périmé) l'écart est aberrant (« Δ12h »
    # avec un matin 14h17) → on retombe sur l'heure du soir seule.
    if morning_time_label and morning_is_today:
        header["timing_line"] = (
            f"matin {morning_time_label} · soir {_ev_time} · Δ{hours_since_morning}h"
            if hours_since_morning >= 1 and since_morning_label
            and "min" not in (since_morning_label or "")
            else f"matin {morning_time_label} · soir {_ev_time} · "
                 f"{since_morning_label or ''}".rstrip(" · ")
        )
    else:
        header["timing_line"] = f"rapport lancé à {_ev_time}"
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
    # BLOC 6 (v14) : bilan recos du dernier matin, calculé Python (1 ligne/actif,
    # zéro doublon). Remplace l'ancienne section reco_evolution (buguée/dupliquée).
    payload["reco_bilan"] = _build_evening_reco_bilan(morning_state, market)
    # v16 — la mini-heatmap soir est SUPPRIMÉE (doublon avec « Top mouvements »).
    # v15 — Polymarket factuel pour le rendu soir (barres Fed si dispo).
    # v16.1 — déclenche aussi si seuls des extra_markets existent (cap 4).
    if polymarket.get("available") and (polymarket.get("fed_bars")
                                        or polymarket.get("extra_markets")):
        payload["polymarket_facts"] = {
            "fed_bars": polymarket.get("fed_bars"),
            "extra_markets": (polymarket.get("extra_markets") or [])[:4],
        }
    # BLOC 2 — score de risque. v18 (E-A1/E-A2/E-A6/E-B4) : le soir RECALCULE le
    # score sur données live, MAIS avec le sector_exposure recalculé en direct
    # (bug E-A1 : morning_state ne contient PAS 'sector_exposure' — clé jamais
    # persistée — donc concentration tombait à 0.0/2.5 le soir). On recompute
    # aussi le readout pour qu'il reflète les VRAIES composantes du soir (bug
    # E-A2 : readout copié du matin citait « concentration 2.0 » alors que le
    # bloc affichait 0.0). Repli sur le matin si recalcul impossible.
    _risk_recomputed = None
    _ev_sector_live = None
    try:
        _ev_enriched = {
            s: {
                "change_24h": (market.get(s) or {}).get("change_24h"),
                "value_usd": _position_value(portfolio[s], market.get(s)),
                "price": (market.get(s) or {}).get("price"),
            }
            for s in symbols
        }
        # E-A1 fix : recalculer l'exposition sectorielle en live (même fonction
        # que le matin), au lieu de lire une clé inexistante du state matin.
        _ev_sector_live = _compute_sector_exposure(_ev_enriched, sector_rotation(market))
        _risk_recomputed = _compute_portfolio_risk_score(
            morning_snap if morning_is_today else {"change_7d_pct": (morning_snap or {}).get("change_7d_pct")},
            _ev_sector_live, ev_macro_ctx, _ev_enriched, portfolio,
        )
    except Exception as _exc:  # noqa: BLE001
        logger.info("Recalcul risk_score soir ignoré : %s", _exc)
    if _risk_recomputed:
        payload["risk_score"] = _risk_recomputed
        # E-A2 fix : readout recalculé sur les composantes RÉELLES du soir
        # (driver = axes dominants live), pas copié du matin.
        payload["risk_score_readout"] = _build_risk_readout(_risk_recomputed)
        # v18 (E-B4) : si la COMPOSITION du PTF n'a pas changé depuis le matin
        # ET que le score de risque est quasi identique (±0.3), on le signale.
        # Le rendu peut alors présenter le score en SCALAIRE compact plutôt que
        # de re-décomposer 4 axes comme si c'était une analyse neuve.
        try:
            _m_risk = (morning_state or {}).get("risk_score") or {}
            _m_score = _m_risk.get("score")
            _e_score = _risk_recomputed.get("score")
            if isinstance(_m_score, (int, float)) and isinstance(_e_score, (int, float)):
                # Composition stable : mêmes actifs détenus qu'au matin.
                _m_assets = set((_m_risk.get("holdings_snapshot") or []))
                _e_assets = set(symbols)
                _same_comp = (not _m_assets) or (_m_assets == _e_assets)
                if _same_comp and abs(_e_score - _m_score) <= 0.3:
                    payload["risk_unchanged_since_morning"] = {
                        "active": True,
                        "morning_score": _m_score,
                        "note": (
                            f"Risque stable depuis ce matin ({_m_score}/10 → "
                            f"{_e_score}/10) — composition du portefeuille inchangée."
                        ),
                    }
        except Exception:  # noqa: BLE001
            pass
    else:
        _mr_risk = (morning_state or {}).get("risk_score")
        if _mr_risk:
            payload["risk_score"] = _mr_risk
        _mr_risk_readout = (morning_state or {}).get("risk_score_readout")
        if _mr_risk_readout:
            payload["risk_score_readout"] = _mr_risk_readout
    # v23 — note de SANTÉ PTF du soir (recalculée live, miroir du risque ci-dessus).
    _health_recomputed = None
    try:
        _health_recomputed = _compute_portfolio_health(
            morning_snap if morning_is_today
            else {"vs_btc_7d_pct": (morning_snap or {}).get("vs_btc_7d_pct"),
                  "drawdown_ath_pct": (morning_snap or {}).get("drawdown_ath_pct")},
            _ev_sector_live if _ev_sector_live is not None else {},
        )
    except Exception as _exc_h:  # noqa: BLE001
        logger.info("Recalcul health_score soir ignoré : %s", _exc_h)
    if _health_recomputed:
        payload["health_score"] = _health_recomputed
        _mh = (morning_state or {}).get("health_score") or {}
        _mh_score, _eh_score = _mh.get("score"), _health_recomputed.get("score")
        if (isinstance(_mh_score, (int, float)) and isinstance(_eh_score, (int, float))
                and abs(_eh_score - _mh_score) <= 0.3):
            payload["health_unchanged_since_morning"] = {
                "active": True,
                "note": (f"Santé stable depuis ce matin ({_mh_score}/10 → "
                         f"{_eh_score}/10) — composition du portefeuille inchangée."),
            }
    elif (morning_state or {}).get("health_score"):
        payload["health_score"] = morning_state["health_score"]
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
    _push_telegram_notification(payload, "evening")
    return 0 if ok else 1


def _build_positions_review(
    long_term: Any, scoring_detail: Any,
    portfolio: dict[str, Any], market: dict[str, Any]
) -> list[dict[str, Any]]:
    """v23.x — FUSION (1 ligne/actif) des 2 anciens tableaux du weekly.

    Joint le positionnement LONG TERME (LLM : analyse, cible, phase de cycle,
    action) avec la performance de la reco à 30j (Python : reco, Δ, statut) et
    enrichit DÉTERMINISTIQUEMENT prix actuel, % vs PRU et conviction (tier).
    Union par actif : positions LT d'abord (ordre LLM), puis recos sans thèse LT.
    """
    price_by = {s.upper(): _parse_num((market.get(s) or {}).get("price")) for s in portfolio}
    pru_by = {s.upper(): _parse_num((portfolio.get(s) or {}).get("pru")) for s in portfolio}
    tier_by = {s.upper(): (portfolio.get(s) or {}).get("tier", 3) for s in portfolio}

    lt_by: dict[str, dict] = {}
    order: list[str] = []
    for e in (long_term or []):
        if isinstance(e, dict) and e.get("asset"):
            a = str(e["asset"]).upper()
            if a not in lt_by:
                lt_by[a] = e
                order.append(a)
    h30_by: dict[str, dict] = {}
    for d in (scoring_detail or []):
        if isinstance(d, dict) and d.get("asset"):
            a = str(d["asset"]).upper()
            if a not in h30_by:
                h30_by[a] = d
    for a in h30_by:               # recos fermes sans thèse LT → ajoutées en fin
        if a not in lt_by:
            order.append(a)

    out: list[dict[str, Any]] = []
    for a in order:
        lt = lt_by.get(a) or {}
        d = h30_by.get(a)
        price = price_by.get(a)
        pru = pru_by.get(a)
        pru_pct = (round((price - pru) / pru * 100, 1)
                   if price and pru and pru > 0 else None)
        target = _parse_num(lt.get("target_price"))
        target_pct = (round((target - price) / price * 100)
                      if target and price and price > 0 else None)
        h30 = None
        if isinstance(d, dict):
            h30 = {"reco": d.get("reco"),
                   "delta_pct": d.get("delta_pct"),
                   "status": d.get("status")}
        out.append({
            "asset": a,
            "conviction": tier_by.get(a, 3) in (1, 2),
            "current_price": price,
            "pru_pct": pru_pct,
            "h30": h30,
            "lt_status": lt.get("status"),
            "lt_target": target,
            "lt_target_pct": target_pct,
            "analysis": (lt.get("analysis") or lt.get("thesis_short")
                         or lt.get("thesis")),
            "action": lt.get("action"),
        })
    return out


def run_weekly() -> int:
    """Génère et envoie le rapport hebdomadaire."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT HEBDO ===")
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]
    market = coingecko.get_market_data(symbols)
    # P3-A5 — CALENDRIER : l'ancien econ_calendar (Trading Economics) renvoie 410.
    # On passe sur FRED /release/dates (dates RÉELLES, fiable) avec un horizon
    # hebdo (8 jours). + Polymarket (signaux taux Fed) + ETF flows : ces trois
    # sources sont désormais TOUJOURS récupérées et surfacées dans le hebdo.
    # v15 — calendrier CONSOLIDÉ (FRED + Boursorama + FOMC/BoJ officiels) :
    # « Aucun événement macro majeur » ne peut plus se produire, et chaque
    # événement porte sa source. + Polymarket ÉTENDU (barres Fed + marchés
    # majeurs) pour croiser calendrier × probabilités (demande forte Omar).
    from src.data_sources import macro_calendar as _mc_w
    calendar = _mc_w.get_consolidated_calendar(horizon_days=8)
    polymarket = prediction_markets.get_key_markets()
    etf = etf_flows.get_etf_flows()
    tracker = PredictionTracker()
    price_lookup = {s: market.get(s, {}).get("price") for s in symbols}
    tracker.refresh_active(price_lookup)
    win_rate = tracker.compute_win_rate(7)
    lesson = tracker.extract_lesson(7)
    # v15 (audit weekly P0) — tableau de scoring 100% Python, dédupliqué,
    # une ligne par (actif, action), la plus récente prime. Gemini ne génère
    # plus jamais ce détail (cause des « 11 recos clôturées en 1 jour »).
    scoring_detail = tracker.build_scoring_detail(price_lookup, period_days=7)
    # v15 — seuil poussières UNIFIÉ à 10 $ (audit : ACH ~25 $ passait, FIL
    # 0,007 $ analysé ; Omar : « je n'aime pas les poussières »).
    # v16 — on joint le TIER de chaque poussière : un actif tier 1-2 (conviction)
    # qui se retrouve sous 10 $ est sous-pondéré, PAS une vraie poussière à
    # liquider. Le flag `conviction` permet au prompt d'exclure ces actifs de
    # l'exit plan (corrige le conflit « AR à liquider » alors qu'il a une thèse LT).
    # v16.1 — actifs ayant une reco validée/en cours cette semaine : ils ne
    # doivent PAS apparaître dans l'exit plan poussières (anti-contradiction).
    _recoed_assets = {
        str(r.get("asset")).upper()
        for r in (scoring_detail or [])
        if r.get("score") in (1, -1) or r.get("status") in ("validated", "in_progress")
    }
    dust = []
    for s in symbols:
        _pv = _position_value(portfolio[s], market.get(s))
        if _pv < 10:
            # v17 (W-A5) : un actif sous reco active (validée/en cours) est EXCLU
            # du set poussières, pas seulement signalé. Avant, le flag dépendait du
            # prompt pour l'écarter → l'audit a vu « AR à liquider » alors qu'AR
            # avait une reco active. On l'exclut directement à la source.
            if s.upper() in _recoed_assets:
                continue
            _tier_cfg = (portfolio.get(s) or {}).get("tier", 3)
            dust.append({
                "asset": s,
                "value_usd": round(_pv, 2),
                "tier": _tier_cfg,
                "conviction": _tier_cfg in (1, 2),
                "active_reco": False,
            })

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
    # v23.x — échafaudage déterministe des SCÉNARIOS de la semaine (rempli dans le
    # try ci-dessous quand les signaux sont prêts ; {} si indispo → repli LLM seul).
    _scenario_scaffold: dict[str, Any] = {}

    # v18 (Chantier E) — signaux d'analyse transverses pour le weekly (l'analyse
    # la plus profonde) : liquidité M2, cycle DXY, spreads HY, saisonnalité,
    # régime de vol réalisée, structure de marché, biais de confirmation, MVRV.
    weekly_cross_signals = {"signals": {}, "readings": []}
    try:
        from src.analytics import cross_signals as _xsig_w
        _macro_series_w = fred.get_macro_series(40)
        _onchain_w = coinmetrics.get_onchain_metrics()
        # v23 — même dégel MVRV via prix live (cohérent avec le matin).
        _onchain_w = coinmetrics.apply_live_price_mvrv(
            _onchain_w,
            {s: v.get("price") for s, v in market.items() if isinstance(v, dict)},
        )
        _btc_mvrv_w = (
            ((_onchain_w.get("assets") or {}).get("BTC") or {}).get("mvrv")
            if isinstance(_onchain_w, dict) else None
        )
        _recent_theses_w = (mem.load_recent_theses(limit=12)
                            if hasattr(mem, "load_recent_theses") else None)
        # enriched-like (valeurs live) pour l'allocation vs cibles.
        _wk_enriched_xs = {
            s: {"value_usd": _position_value(portfolio[s], market.get(s)),
                "change_24h": (market.get(s) or {}).get("change_24h")}
            for s in significant
        }
        # macro courant (VIX/F&G/DXY) depuis séries + dernier matin, pour #8.
        def _last_v(_name: str) -> float | None:
            _vals = [v for _, v in sorted((_macro_series_w.get(_name) or {}).items())
                     if isinstance(v, (int, float))]
            return _vals[-1] if _vals else None
        _cur_macro_w = {
            "vix": _last_v("vix"), "dxy": _last_v("dxy"),
            "fear_greed": (((mem.load_morning_report() or {}).get("macro_context") or {})
                           .get("fear_greed")),
        }
        # #2 DVOL (move implicite) + #14 dérivés (funding) pour le weekly.
        _options_w = deribit.get_options_metrics()
        _btc_dvol_w = (
            ((_options_w.get("assets") or {}).get("BTC") or {}).get("dvol")
            if isinstance(_options_w, dict) else None
        )
        _derivs_w: dict[str, Any] = {}
        for _s in significant[:6]:  # limite les appels (positions principales)
            try:
                _d = binance_futures.get_derivatives(_s)
                if _d.get("available"):
                    _derivs_w[_s] = _d
            except Exception:  # noqa: BLE001
                pass
        weekly_cross_signals = _xsig_w.compute_all(
            _macro_series_w,
            weekly_price_series,
            weights=weekly_positions,
            focus_assets=significant,
            recent_theses=_recent_theses_w,
            mvrv_value=_btc_mvrv_w,
            portfolio=portfolio_data,
            enriched=_wk_enriched_xs,
            onchain_assets=(_onchain_w.get("assets") if isinstance(_onchain_w, dict) else None),
            market=market,
            current_macro=_cur_macro_w,
            snapshots=mem.load_weekly_snapshots(),
            dvol=_btc_dvol_w,
            derivatives_by_asset=_derivs_w,
            sector_rotation=sector_rotation(market),
        )
        # v23.x — ÉCHAFAUDAGE SCÉNARIOS : agrège les signaux objectifs (DVOL,
        # Polymarket, macro, technique BTC, sentiment, dérivés, momentum, calendrier)
        # en un prior de probabilités que le LLM ancre. Tout en repli gracieux.
        _btc_mk = market.get("BTC") or {}
        _btc_ta_w = technical_advanced.get_technical_advanced("BTC")
        _btc_sr_w = (_btc_ta_w.get("support_resistance") or {}) if _btc_ta_w.get("available") else {}
        _btc_ma_w = (_btc_ta_w.get("moving_averages") or {}) if _btc_ta_w.get("available") else {}
        _btc_rsi_w = compute_local_technical(weekly_price_series.get("BTC") or []).get("rsi")
        _dxy_hist = [v for _, v in sorted((_macro_series_w.get("dxy") or {}).items())
                     if isinstance(v, (int, float))]
        _dxy_trend_w = None
        if len(_dxy_hist) >= 2:
            _dxy_trend_w = "up" if _dxy_hist[-1] > _dxy_hist[-2] else (
                "down" if _dxy_hist[-1] < _dxy_hist[-2] else None)
        _imp_w = ((weekly_cross_signals.get("signals") or {}).get("implied_move") or {})
        _scenario_scaffold = compute_scenario_scaffold(
            btc_price=_btc_mk.get("price"),
            implied_move_7d_pct=_imp_w.get("move_7d_pct"),
            polymarket=polymarket,
            vix=_cur_macro_w.get("vix"),
            dxy_trend=_dxy_trend_w,
            fear_greed=_cur_macro_w.get("fear_greed"),
            btc_funding_pct=(_derivs_w.get("BTC") or {}).get("funding_annualized_pct"),
            btc_support=_btc_sr_w.get("support"),
            btc_resistance=_btc_sr_w.get("resistance"),
            btc_trend_pct=_btc_ma_w.get("price_vs_sma50_pct"),
            btc_rsi=_btc_rsi_w,
            btc_change_7d=_btc_mk.get("change_7d"),
            calendar_events=(calendar.get("events") if isinstance(calendar, dict) else None),
        )
    except Exception as _xexc_w:  # noqa: BLE001
        logger.info("cross_signals weekly ignoré : %s", _xexc_w)

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
    # v19/WS2 (W-B2/X1 — SOURCE DE VÉRITÉ UNIQUE) : si le snapshot du matin est
    # daté d'AUJOURD'HUI, le hebdo réutilise sa perf 7j / vs BTC 7j (un seul
    # nombre, partout) plutôt que de recalculer une valeur légèrement divergente.
    _morning_is_today = (
        _snap_now.get("computed_date") == datetime.now(TZ).date().isoformat()
    )
    mem.record_weekly_snapshot(current_value, btc_price_now, drawdown_ath_pct=_dd_now)
    snapshots = mem.load_weekly_snapshots()
    ptf_evolution = [
        {"label": s.get("week_label"), "value": s.get("value_usd")}
        for s in snapshots if s.get("value_usd") is not None
    ]
    # v23 — série BTC alignée (prix BTC de chaque snapshot) pour la comparaison
    # base 100 « PTF vs BTC hold » dans la courbe d'évolution. La fonction de
    # rendu ne l'utilise que si la longueur correspond (sinon courbe PTF seule).
    _evo_btc = [s.get("btc_price") for s in snapshots if s.get("value_usd") is not None]
    # v19/W-B16 — si l'historique de snapshots hebdo est insuffisant pour tracer
    # une COURBE (< 3 points, typiquement sur un state récent), on reconstruit une
    # vraie évolution ~30j de la VALEUR du PTF à partir des séries de prix par
    # actif déjà récupérées (weekly_price_series, aucun appel réseau en plus),
    # échantillonnée à ~10 points. La sparkline s'affiche ainsi DÈS le 1er hebdo,
    # sans attendre 3 semaines de snapshots.
    if len([p for p in ptf_evolution if p.get("value") is not None]) < 3 and weekly_price_series:
        _lens = [len(p) for p in weekly_price_series.values() if p]
        _L = min(_lens) if _lens else 0
        if _L >= 4:
            _recon: list[float] = []
            for _i in range(_L):
                _day_val = 0.0
                for _s, _prices in weekly_price_series.items():
                    if not _prices or len(_prices) < _L:
                        continue
                    _qty = (portfolio.get(_s) or {}).get("quantity")
                    _px = _prices[len(_prices) - _L + _i]
                    if isinstance(_qty, (int, float)) and isinstance(_px, (int, float)):
                        _day_val += _qty * _px
                if _day_val > 0:
                    _recon.append(round(_day_val, 2))
            if len(_recon) >= 4:
                _step = max(1, len(_recon) // 10)
                _sampled = _recon[::_step]
                if _sampled and _sampled[-1] != _recon[-1]:
                    _sampled.append(_recon[-1])
                ptf_evolution = [{"label": "", "value": _v} for _v in _sampled]
                if ptf_evolution:
                    ptf_evolution[0]["label"] = "≈30j"
                    ptf_evolution[-1]["label"] = "auj."

    # v23 (W4) — le DERNIER point d'évolution DOIT être la valeur live actuelle.
    # Un snapshot hebdo déjà enregistré plus tôt dans la même semaine (valeur plus
    # ancienne) restait en dernier point → « auj. $2,221 » alors que le PTF vaut
    # $2,481 partout ailleurs. On force l'endpoint sur la valeur courante.
    if ptf_evolution:
        ptf_evolution[-1] = {"label": "auj.", "value": round(current_value, 2)}
        # Aligne le dernier point BTC sur le prix BTC live (si série alignée).
        if (_evo_btc and len(_evo_btc) == len(ptf_evolution)
                and isinstance(btc_price_now, (int, float))):
            _evo_btc[-1] = btc_price_now

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

    # v15 (audit weekly P0) — BTC HOLD sur la MÊME FENÊTRE 7j que le P&L
    # semaine. v14 comparait au snapshot le plus ancien (~N semaines) pendant
    # que « vs BTC 7j » utilisait 7 jours → -4.5% vs -1.7% contradictoires
    # dans le même mail. Désormais : base = valeur PTF il y a 7j (reconstruite
    # depuis les change_7d par position, comme le P&L), hold = base × perf BTC
    # 7j. Une seule fenêtre, un seul verdict. Calculé plus bas, une fois
    # val_7d_ago connu (voir « BTC HOLD v15 »). Fallback snapshots conservé.
    btc_hold_comparison = None

    # H8 : top gagnants / perdants de la semaine (sur la base du change 7j).
    # v15 (audit P2) : élargi à 5+5 (était 3+3 — « 5 actifs seulement »).
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
            "upcoming_calendar": calendar, "polymarket": polymarket,
            "etf_flows": etf, "dust_positions": dust, "prices_now": price_lookup,
            "active_sources_count": None, "total_sources_count": len(_ALL_SOURCES_LIST),
            "sector_exposure_computed": None,
            # v18 (Chantier E) — signaux d'analyse transverses (Partie 4).
            "cross_signals": weekly_cross_signals,
            # v23.x — échafaudage déterministe des scénarios (prior de probabilités
            # + tilts par dimension + niveaux + drivers) que le LLM ANCRE.
            "scenario_scaffold": _scenario_scaffold,
            # v18 (W-B11) : liste des actifs de CONVICTION (tier 0/1) détenus, pour
            # que le positionnement long terme les couvre TOUS (l'audit a vu des
            # tier-1 comme TAO/RENDER/JASMY absents du tableau LT). Le prompt s'en
            # sert comme checklist de couverture minimale.
            "conviction_assets": sorted(
                s for s in symbols
                if resolve_tier(s, portfolio[s].get("value_usd")) <= 1
            ),
            # v15 — détail de scoring Python (Gemini le COMMENTE, ne le génère plus)
            "scoring_detail": scoring_detail,
            # v15 — ATH RÉELS par actif (CoinGecko) : les cibles LT s'ancrent
            # dessus (audit : « retest ATH >73k » alors que l'ATH BTC ≈ 108k).
            "ath_by_asset": {
                s: {"ath": (market.get(s) or {}).get("ath"),
                    "from_ath_pct": round((market.get(s) or {}).get("change_from_ath_pct") or 0, 1)}
                for s in symbols if (market.get(s) or {}).get("ath")
            },
            # v16.1 — top movers 7j (pour expliquer les fortes variations ±20%).
            "weekly_movers": weekly_movers,
            # v17 (T-TAO / W-A12) : postures fermes du dernier matin, pour que la
            # watchlist/SORTIE du weekly ne contredise pas le matin sans raison.
            "firm_postures": (mem.load_morning_report() or {}).get("firm_postures") or {}}
    # A4/A6 — pré-calculs injectés dans le prompt (le rendu utilise aussi les
    # versions persistées dans le payload après génération).
    _wk_enriched_pre = {
        s: {
            "value_usd": _position_value(portfolio[s], market.get(s)),
            "change_24h": market.get(s, {}).get("change_24h"),
        }
        for s in symbols
    }
    _wk_sector_pre = _compute_sector_exposure(_wk_enriched_pre, sector_rotation(market))
    if _wk_sector_pre.get("available"):
        data["sector_exposure_computed"] = _wk_sector_pre
    _wk_active_pre = sum([
        bool(market), bool(_cmc_quotes_w), bool(polymarket.get("available")),
        bool(calendar.get("available")), bool(correlation.get("available")),
        bool(etf.get("available")), len(snapshots) >= 2,
    ])
    data["active_sources_count"] = _wk_active_pre
    engine = DecisionEngine()
    payload = engine.generate_weekly(timestamp=_now_str(), data=data, week_state=week_state)
    checked = check_report(payload, _confidence_caps_from_data(data))
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
    # v16 — alimente snap_w avec les métriques du snapshot du matin pour que le
    # SCORE QUALITÉ PTF affiche un détail réel (et non « n/d ») sur ses axes :
    #   - drawdown ATH pondéré (_dd_now, déjà calculé plus haut)
    #   - réserve cash USDC et momentum 7j vs BTC (depuis le snapshot du matin).
    if _dd_now is not None:
        snap_w["drawdown_ath_pct"] = _dd_now
    if _snap_now.get("usdc_pct") is not None:
        snap_w["usdc_pct"] = _snap_now.get("usdc_pct")
    # v19/WS2 : on ne pré-charge vs_btc_7d_pct depuis le matin QUE s'il est du jour
    # (source de vérité unique). Sinon le hebdo recalcule sa propre valeur fraîche.
    if _morning_is_today and _snap_now.get("vs_btc_7d_pct") is not None:
        snap_w["vs_btc_7d_pct"] = _snap_now.get("vs_btc_7d_pct")

    # P3-A2 — P&L SEMAINE FIABILISÉ. L'ancienne méthode dépendait d'un snapshot
    # de la semaine précédente (snapshots[-2]) qui n'existe pas tant que le hebdo
    # n'a pas tourné plusieurs dimanches → « — » en permanence. On calcule donc
    # le P&L 7j DIRECTEMENT depuis les variations 7j par position (CoinGecko),
    # exactement comme le snapshot du matin : value_7j = Σ valeur_i / (1+chg7j_i),
    # ce qui donne une perf réelle dès le 1er run. Le snapshot reste un repli.
    val_7d_ago = 0.0
    has_7d = False
    for s in symbols:
        v = _position_value(portfolio[s], market.get(s))
        ch7 = market.get(s, {}).get("change_7d")
        if v > 0 and isinstance(ch7, (int, float)) and (1 + ch7 / 100) > 0:
            val_7d_ago += v / (1 + ch7 / 100)
            has_7d = True
        else:
            val_7d_ago += v  # neutre si pas de variation 7j connue
    if has_7d and val_7d_ago > 0:
        weekly_pnl_usd = current_value - val_7d_ago
        snap_w["weekly_pnl_usd"] = round(weekly_pnl_usd, 2)
        snap_w["weekly_pnl_pct"] = round(weekly_pnl_usd / val_7d_ago * 100, 2)
        snap_w["change_7d_pct"] = snap_w["weekly_pnl_pct"]
        snap_w["change_7d_usd"] = snap_w["weekly_pnl_usd"]
        # v15 — début/fin de fenêtre EXPLICITES (audit : « montrer début de
        # semaine vs fin de semaine pour une variation 7j claire »).
        snap_w["week_start_value"] = round(val_7d_ago, 2)
        snap_w["week_end_value"] = round(current_value, 2)
        # ── BTC HOLD v15 : MÊME fenêtre 7j que le P&L (cf. note plus haut).
        _btc7 = (market.get("BTC") or {}).get("change_7d")
        if isinstance(_btc7, (int, float)):
            _hold_val = val_7d_ago * (1 + _btc7 / 100)
            _outperf = current_value >= _hold_val
            _diff = ((current_value - _hold_val) / _hold_val * 100) if _hold_val else 0
            btc_hold_comparison = {
                "btc_hold_value": round(_hold_val, 2),
                "actual_value": round(current_value, 2),
                "outperforms": _outperf,
                "window_label": "7 jours",
                "verdict": (
                    f"Ta gestion active {'surperforme' if _outperf else 'sous-performe'} "
                    f"un simple BTC hold de {abs(_diff):.1f}% sur 7 jours "
                    f"(même fenêtre que le P&L semaine)."
                ),
            }
    elif len(snapshots) >= 2:  # repli : snapshot semaine précédente
        prev_val = snapshots[-2].get("value_usd")
        if prev_val and prev_val > 0:
            weekly_pnl_usd = current_value - prev_val
            snap_w["weekly_pnl_usd"] = round(weekly_pnl_usd, 2)
            snap_w["weekly_pnl_pct"] = round((weekly_pnl_usd / prev_val) * 100, 2)
            snap_w["change_7d_pct"] = snap_w["weekly_pnl_pct"]
            snap_w["change_7d_usd"] = snap_w["weekly_pnl_usd"]
            snap_w["week_start_value"] = round(prev_val, 2)
            snap_w["week_end_value"] = round(current_value, 2)
            # BTC HOLD (repli) : même base snapshot → fenêtre identique au P&L.
            _prev_btc = snapshots[-2].get("btc_price")
            if _prev_btc and _prev_btc > 0 and btc_price_now:
                _hold_val = prev_val * (btc_price_now / _prev_btc)
                _outperf = current_value >= _hold_val
                _diff = ((current_value - _hold_val) / _hold_val * 100) if _hold_val else 0
                btc_hold_comparison = {
                    "btc_hold_value": round(_hold_val, 2),
                    "actual_value": round(current_value, 2),
                    "outperforms": _outperf,
                    "window_label": "depuis le dernier hebdo",
                    "verdict": (
                        f"Ta gestion active {'surperforme' if _outperf else 'sous-performe'} "
                        f"un simple BTC hold de {abs(_diff):.1f}% depuis le dernier hebdo."
                    ),
                }

    # vs BTC 7j : perf PTF 7j − perf BTC 7j (même fenêtre). BTC change_7d direct.
    # v19/WS2 : on ne recalcule QUE si le matin du jour ne l'a pas déjà fourni
    # (cf. _morning_is_today) — garantit « un seul nombre, partout » (X1/W-B2).
    _btc_7d = (market.get("BTC") or {}).get("change_7d")
    if (snap_w.get("vs_btc_7d_pct") is None
            and snap_w.get("weekly_pnl_pct") is not None
            and isinstance(_btc_7d, (int, float))):
        snap_w["vs_btc_7d_pct"] = round(snap_w["weekly_pnl_pct"] - _btc_7d, 2)
    # B2 — benchmark supplémentaire vs ETH (perf 7j). Donne une vue au-delà du BTC.
    _eth_7d = (market.get("ETH") or {}).get("change_7d")
    if snap_w.get("weekly_pnl_pct") is not None and isinstance(_eth_7d, (int, float)):
        snap_w["vs_eth_7d_pct"] = round(snap_w["weekly_pnl_pct"] - _eth_7d, 2)
        snap_w["ptf_7d_pct"] = snap_w["weekly_pnl_pct"]
        snap_w["btc_7d_pct"] = round(_btc_7d, 2) if isinstance(_btc_7d, (int, float)) else None
        snap_w["eth_7d_pct"] = round(_eth_7d, 2)
        # v23 — COHÉRENCE INTERNE : le « vs BTC 7j » du header doit dériver des
        # MÊMES composantes que la ligne benchmark (PTF 7j − BTC 7j), exactement
        # comme vs_eth. Sinon le header (repris du matin, calculé sur un PTF 7j
        # légèrement différent) affiche −1.5% à côté de « PTF −8.7% · BTC −6.8% »
        # (= −1.9%). On l'aligne donc sur les chiffres réellement montrés.
        if isinstance(_btc_7d, (int, float)):
            snap_w["vs_btc_7d_pct"] = round(snap_w["weekly_pnl_pct"] - _btc_7d, 2)
    elif len(snapshots) >= 2 and btc_price_now:  # repli snapshot
        prev_snap = snapshots[-2]
        prev_btc = prev_snap.get("btc_price")
        prev_val = prev_snap.get("value_usd")
        if (prev_btc and prev_btc > 0 and prev_val and prev_val > 0
                and snap_w.get("vs_btc_7d_pct") is None):
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
    _now_h = datetime.now(TZ)
    # Bug v14 : le template lit header.week_number / header.year /
    # header.time_casablanca, mais Python ne remplissait que header["week"]
    # (jamais lu) -> numéro de semaine et date absents de l'en-tête hebdo.
    header["week"] = f"Semaine {_now_h.strftime('%V')} · {_fr_date(_now_h, with_time=False)}"
    header["week_number"] = int(_now_h.strftime("%V"))
    header["year"] = _now_h.year
    # v23 — winrate 30j en TÊTE de l'hebdo (présent une fois dans chaque mail).
    header["win_rate_30d"] = win_rate_30d.get("win_rate_pct")
    header["win_rate_total"] = (
        f"{win_rate_30d.get('validated', 0)}/{win_rate_30d.get('total', 0)}")
    # v15 (audit P2) — PÉRIODE COUVERTE explicite : fenêtre 7j glissante du
    # bilan (même fenêtre que P&L/scoring), au lieu d'un « semaine 24/2026 » nu.
    from datetime import timedelta as _td_h
    _wk_start = _now_h - _td_h(days=7)
    header["period_covered"] = (
        f"du {_wk_start.day} {_MOIS_FR[_wk_start.month - 1]} "
        f"au {_now_h.day} {_MOIS_FR[_now_h.month - 1]}"
    )
    # v16 — CORRECTION BUG LABEL « semaine à venir 8-14 juin » : la fenêtre
    # FUTURE est now → now+7j (et non now-4j). Calculée en Python pour ne pas
    # dépendre de Gemini (qui se trompait de fenêtre).
    _wk_fwd_end = _now_h + _td_h(days=7)
    header["upcoming_week"] = (
        f"{_now_h.day} {_MOIS_FR[_now_h.month - 1]} – "
        f"{_wk_fwd_end.day} {_MOIS_FR[_wk_fwd_end.month - 1]}"
    )
    header.setdefault("time_casablanca", _fr_date(_now_h))
    header.setdefault("date", _fr_date(_now_h, with_time=False))
    if win_rate.get("total", 0) == 0 and not scoring_detail:
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
    # v15 (audit weekly P0) — DÉTAIL 100% PYTHON. Le tableau « DÉTAIL »
    # généré par Gemini (11 lignes, doublons, scores fantaisistes) est
    # remplacé par scoring_detail (dédupliqué, dates réelles, holding_days).
    # Le header et le détail partagent désormais LA MÊME source : les
    # compteurs sont recomposés depuis le détail → plus de « — » en header
    # avec 11 recos en détail.
    _sc = payload.setdefault("predictions_scoring", {})
    _sc["detail"] = scoring_detail
    _closed = [r for r in scoring_detail if r.get("score") in (1, -1)]
    _open = [r for r in scoring_detail if r.get("score") == 0]
    _sc["issued"] = len(scoring_detail)
    _sc["validated"] = sum(1 for r in _closed if r["score"] == 1)
    _sc["invalidated"] = sum(1 for r in _closed if r["score"] == -1)
    _sc["open_count"] = len(_open)
    # Win rate affiché dès >= 5 recos CLÔTURÉES (seuil explicite au rendu).
    _sc["closed_count"] = len(_closed)
    _sc["min_closed_for_winrate"] = 5
    # v16.1 — win_rate_30d explicitement posé (était absent du scoring hebdo →
    # le template affichait « Win rate 30j · % » avec une valeur vide). None si
    # pas d'historique 30j suffisant.
    _sc["win_rate_30d"] = wr_month
    if len(_closed) >= 5:
        _sc["win_rate_pct"] = round(_sc["validated"] / len(_closed) * 100)
        _sc["no_history"] = False
    else:
        _sc["win_rate_pct"] = None
        _sc["no_history"] = len(scoring_detail) == 0
        _sc["winrate_gate_label"] = (
            f"Recos clôturées : {len(_closed)}/5 minimum pour calibration"
        )
    # v23.x — TABLEAU UNIFIÉ « Positions · 30j & long terme » : fusion du
    # positionnement LT (LLM) + perf reco 30j (scoring_detail) + prix/PRU/
    # conviction (déterministe). Remplace les 2 anciens tableaux par-actif.
    payload["positions_review"] = _build_positions_review(
        payload.get("long_term_positioning"), scoring_detail, portfolio, market
    )
    # Drawdown depuis le dernier snapshot
    if last_snap.get("drawdown_ath_pct") is not None:
        payload.setdefault("portfolio_overview", {})["drawdown_pct"] = last_snap["drawdown_ath_pct"]
    # V6 : matrice de corrélation chiffrée des positions (clusters de risque)
    if correlation.get("available"):
        payload["position_correlation"] = correlation
    # P3-A5 — CALENDRIER / POLYMARKET / ETF FLOWS surfacés comme FAITS Python
    # (jamais hallucinés). Le rendu et l'analyse s'appuient dessus. Le calendrier
    # passe en structure {events:[{label,date,when,days_ahead}]} lisible.
    if calendar.get("available"):
        _wk_cal_events = []
        for e in calendar.get("events", []):
            da = e.get("days_ahead")
            _wk_cal_events.append({
                "label": e.get("label"), "date": e.get("date"),
                "when": ("aujourd'hui" if da == 0 else "demain" if da == 1 else f"dans {da}j"),
                "days_ahead": da,
            })
        payload["upcoming_calendar_facts"] = {"available": True, "events": _wk_cal_events}
    if polymarket.get("available"):
        payload["polymarket_facts"] = polymarket
    if etf.get("available"):
        payload["etf_flows_facts"] = etf
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
    # v18 (W-B12) : espérance mathématique des recos (gain moyen × winrate −
    # perte moyenne × échec). Affichée dès 5 recos clôturées avec niveaux.
    payload["expectancy"] = tracker.compute_expectancy(30)
    # v18 (W-B14) : mémoire des thèses invalidées (boucle d'apprentissage).
    payload["invalidation_lessons"] = tracker.compute_invalidation_lessons(60)
    # v18 (Chantier E #15) : calibration des cibles de prix (auto-apprentissage).
    payload["target_calibration"] = tracker.compute_target_calibration(90)
    # NOUVEAU #5 : coût des erreurs (regret)
    # v18 (W-A5) : le message du coût des erreurs doit être COHÉRENT avec le
    # tableau de scoring juste au-dessus. L'audit a vu « Pas encore de reco
    # clôturée » alors que 3 recos ✓ validée figuraient dans la table. Cause :
    # compute_regret lit prediction_history (recos migrées) tandis que le scoring
    # voit aussi les actives validées pas encore migrées. On réconcilie : le
    # nombre de recos CLÔTURÉES de référence est celui du scoring (_closed), et
    # « clôturée » = validée OU invalidée (score ±1), pas seulement invalidée.
    _closed_count_sc = len(_closed)
    _invalidated_sc = _sc.get("invalidated", 0)
    regret = tracker.compute_regret(7)
    if regret.get("available") and regret.get("entries"):
        # Il y a au moins une reco invalidée chiffrée → on l'affiche.
        payload["regret"] = regret
    elif _closed_count_sc > 0:
        # Des recos sont clôturées (validées et/ou invalidées) mais aucune perte
        # chiffrable : message HONNÊTE et cohérent avec la table (pas « aucune
        # clôture »). On distingue « que des validées » d'un cas mixte.
        if _invalidated_sc == 0:
            _regret_msg = (
                f"{_closed_count_sc} reco(s) clôturée(s) cette semaine, toutes "
                f"validées : aucune perte sur les recos. Le coût des erreurs ne "
                f"s'applique qu'aux recos invalidées (0 cette semaine)."
            )
        else:
            _regret_msg = (
                f"{_invalidated_sc} reco(s) invalidée(s) sur {_closed_count_sc} "
                f"clôturée(s) : coût détaillé indisponible (mouvement de prix non "
                f"enregistré), mais l'invalidation est tracée."
            )
        payload["regret"] = {"available": False, "empty_reason": _regret_msg}
    else:
        # v15 (audit P2) : « Aucune erreur coûteuse · discipline maintenue »
        # était une HALLUCINATION quand des pertes figuraient juste au-dessus.
        # Sans AUCUNE clôture, on dit la vérité : la mesure n'existe pas encore.
        payload["regret"] = {
            "available": False,
            "empty_reason": "Pas encore de reco clôturée sur la fenêtre · "
                            "mesure du coût des erreurs disponible dès la "
                            "première clôture.",
        }
    # v15 — SEMAINE À VENIR croisée Polymarket (demande forte Omar : « si
    # Polymarket table à 90% sur baisse des taux, ça donne la couleur »).
    # Pour chaque événement du calendrier, on attache la probabilité de
    # marché correspondante quand elle existe (FOMC ↔ barres Fed). Factuel.
    # v18 (W-A1/W-A13/W-B1) : weekday_label (« mardi ») et date_label (« mardi
    # 16 juin ») sont DÉJÀ calculés en Python À LA SOURCE (macro_calendar) → le
    # rendu ET Gemini partagent le même libellé exact. On les propage simplement.
    _week_ahead: list[dict[str, Any]] = []
    _fed_bars_w = (polymarket.get("fed_bars") or {}) if polymarket.get("available") else {}
    for e in (calendar.get("events") or []):
        item = {
            "label": e.get("label"), "date": e.get("date"),
            "when": e.get("when"), "days_ahead": e.get("days_ahead"),
            "importance": e.get("importance"), "source": e.get("source"),
            "weekday_label": e.get("weekday_label"),
            "date_label": e.get("date_label"),
        }
        if _fed_bars_w and "fomc" in (e.get("label") or "").lower():
            item["polymarket_note"] = (
                f"Polymarket : {_fed_bars_w.get('dominant')} "
                f"{_fed_bars_w.get('dominant_pct')}%"
            )
        _week_ahead.append(item)
    if _week_ahead:
        payload["week_ahead"] = _week_ahead
    if data.get("ath_by_asset"):
        payload["ath_facts"] = data["ath_by_asset"]
    if _fed_bars_w:
        payload["polymarket_facts"] = {
            "fed_bars": _fed_bars_w,
            "extra_markets": polymarket.get("extra_markets") or [],
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
    # Sources réellement interrogées par CE run dominical (repli + liste info).
    _weekly_active = []
    if market:
        _weekly_active.append("CoinGecko")
    if _cmc_quotes_w:
        _weekly_active.append("CoinMarketCap")
    if polymarket.get("available"):
        _weekly_active.append("Polymarket")
    if calendar.get("available"):
        _weekly_active.append("Calendrier macro")
    if correlation.get("available"):
        _weekly_active.append("Corrélations")
    if len(snapshots) >= 2:
        _weekly_active.append("Historique PTF")
    # v15 (audit weekly P0) — le compteur « 4/23 » reflétait le seul run
    # dominical (6 sources interrogées max) alors que le matin même en avait
    # 16/23. On affiche désormais la MOYENNE quotidienne réelle de la semaine
    # (source_health, alimenté par chaque run matin), avec repli sur l'ancien
    # compteur du run si l'historique manque.
    _wk_src_stats = mem.compute_weekly_source_stats(len(_ALL_SOURCES_LIST))
    if _wk_src_stats.get("available"):
        payload["header"]["active_sources_count"] = _wk_src_stats["avg_active"]
        _days_obs = _wk_src_stats["days_observed"]
        if _days_obs <= 1:
            # v16 — 1 seul jour observé n'est PAS une « moyenne hebdo » : le dire
            # honnêtement plutôt que d'afficher un chiffre trompeur.
            payload["header"]["sources_week_label"] = (
                f"{_wk_src_stats['avg_active']}/{_wk_src_stats['total']} sources "
                f"actives aujourd'hui (1ʳᵉ semaine de mesure · "
                f"moyenne hebdo à partir de J+7)"
            )
        else:
            # v16.1 — le « pic » n'a de sens que s'il dépasse la moyenne ; sinon
            # (pic == moyenne) c'est redondant et trompeur, on l'omet.
            _avg = _wk_src_stats["avg_active"]
            _best = _wk_src_stats["best_active"]
            if _best > _avg:
                _src_detail = f"pic {_best}, {_days_obs} jours observés"
            else:
                _src_detail = f"{_days_obs} jours observés"
            payload["header"]["sources_week_label"] = (
                f"{_avg}/{_wk_src_stats['total']} sources "
                f"actives en moyenne cette semaine ({_src_detail})"
            )
        payload["weekly_source_stats"] = _wk_src_stats
    else:
        payload["header"]["active_sources_count"] = len(_weekly_active)
    payload["header"]["active_sources"] = _weekly_active
    # A4 (déplacé en v15) : _weekly_active = sources du RUN dominical, gardé
    # uniquement comme repli + liste informative ; le compteur affiché vient
    # désormais de compute_weekly_source_stats (réalité de la semaine).
    # → défini plus haut, avant son usage.
    # A6 — EXPOSITION SECTORIELLE calculée côté Python (poids PTF par secteur +
    # perf marché). Remplace les « n/d% » : Gemini ne génère plus cette grille.
    _weekly_enriched = {
        s: {
            "value_usd": _position_value(portfolio[s], market.get(s)),
            "change_24h": market.get(s, {}).get("change_24h"),
            "change_7d": market.get(s, {}).get("change_7d"),
        }
        for s in symbols
    }
    # v23.x — HEATMAP hebdo des positions (identique au matin mais sur 7j) :
    # même tri par |perf| décroissante, même code couleur, 4 lignes × 5 cases +
    # « +N autres ». Placée sous l'exposition sectorielle dans le template.
    payload["portfolio_heatmap_7d"] = _portfolio_heatmap(
        _weekly_enriched, change_key="change_7d"
    )
    _weekly_sector_exposure = _compute_sector_exposure(
        _weekly_enriched, sector_rotation(market)
    )
    # v23.x — faits de concentration SECTORIELLE collectés ici, fusionnés plus bas
    # avec la concentration MONO-ACTIF en UNE seule alerte concise (un seul bloc).
    _conc_sector_facts: list[str] = []
    _conc_top_sector: Optional[str] = None
    _conc_top_pct: float = 0.0
    if _weekly_sector_exposure.get("available"):
        payload["sector_exposure_computed"] = _weekly_sector_exposure
        # v18 (W-B15) : exposition sectorielle en CASES (comme la heatmap du
        # matin) plutôt qu'en barres. Top 4 secteurs par poids PTF + une case
        # « Autres secteurs (N) » agrégeant le reste (poids cumulé + variation
        # moyenne pondérée par le poids). Vue compacte et homogène avec le matin.
        _wk_secs = _weekly_sector_exposure.get("sectors") or []
        if _wk_secs:
            _sorted_secs = sorted(
                _wk_secs, key=lambda s: s.get("ptf_pct") or 0, reverse=True
            )
            # v23.x — 6 cases : 5 secteurs individuels (les plus gros) + 1 case
            # « Autres secteurs » agrégeant le reste. (_compute_sector_exposure
            # plafonne déjà à 6 ; ce repli reste cohérent si jamais davantage de
            # secteurs arrivent, en conservant holdings + perfs 24h/7j/30j.)
            _MAXC = 5
            if len(_sorted_secs) > _MAXC + 1:
                _top = _sorted_secs[:_MAXC]
                _rest = _sorted_secs[_MAXC:]
                _rest_pct = sum(s.get("ptf_pct") or 0 for s in _rest)

                def _rest_weighted(_key):
                    _n = sum(
                        (s.get(_key) or 0) * (s.get("value_usd") or 0)
                        for s in _rest if isinstance(s.get(_key), (int, float))
                    )
                    _d = sum(
                        (s.get("value_usd") or 0)
                        for s in _rest if isinstance(s.get(_key), (int, float))
                    )
                    return round(_n / _d, 1) if _d > 0 else None

                _rest_holds = sorted(
                    {h for s in _rest for h in (s.get("holdings") or [])}
                )
                _cells = list(_top) + [{
                    "sector": f"Autres secteurs ({len(_rest)})",
                    "ptf_pct": round(_rest_pct, 1),
                    "market_change_24h": _rest_weighted("market_change_24h"),
                    "market_change_7d": _rest_weighted("market_change_7d"),
                    "market_change_30d": _rest_weighted("market_change_30d"),
                    "holdings": _rest_holds,
                    "is_aggregate": True,
                }]
            else:
                _cells = _sorted_secs
            payload["sector_exposure_cells"] = _cells
        # v18 (W-B13) — ALERTE DE RÉÉQUILIBRAGE déterministe. Si un secteur pèse
        # trop lourd (> 50% du PTF) ou si le top 2 dépasse 75%, on émet une alerte
        # factuelle (le portefeuille est vulnérable à une rotation défavorable).
        _top_sec = max(_wk_secs, key=lambda s: s.get("ptf_pct") or 0, default=None)
        if _top_sec:
            _top_pct = _top_sec.get("ptf_pct") or 0
            _sorted_for_alert = sorted(
                _wk_secs, key=lambda s: s.get("ptf_pct") or 0, reverse=True
            )
            _top2_pct = sum((s.get("ptf_pct") or 0) for s in _sorted_for_alert[:2])
            if _top_pct >= 50 or _top2_pct >= 75:
                if _top_pct >= 50:
                    _conc_sector_facts.append(
                        f"{_top_sec.get('sector')} {_top_pct:.0f}%")
                if _top2_pct >= 75 and len(_sorted_for_alert) >= 2:
                    _conc_sector_facts.append(f"top 2 secteurs {_top2_pct:.0f}%")
                _conc_top_sector = _top_sec.get("sector")
                _conc_top_pct = round(_top_pct, 1)
    # v19/W-B13 + v23.x — concentration MONO-ACTIF (position ≥ 12% du PTF) FUSIONNÉE
    # avec la concentration sectorielle en UNE SEULE alerte concise (faits chiffrés
    # + une recommandation d'action), au lieu de deux blocs redondants. Cette alerte
    # SERT aussi de « lecture concentration » dans le template (un seul bloc).
    _asset_weights: list[tuple[str, float]] = []
    for _s in symbols:
        _v = _position_value(portfolio[_s], market.get(_s))
        if _v > 0 and current_value > 0:
            _asset_weights.append((_s, _v / current_value * 100))
    _asset_weights.sort(key=lambda kv: kv[1], reverse=True)
    _heavy = [(s, p) for s, p in _asset_weights if p >= 12.0]
    if _conc_sector_facts or _heavy:
        _conc_parts: list[str] = []
        if _conc_sector_facts:
            _conc_parts.append("secteurs " + ", ".join(_conc_sector_facts))
        if _heavy:
            _conc_parts.append(
                "mono-actifs " + " · ".join(f"{s} {p:.0f}%" for s, p in _heavy))
        _alert: dict[str, Any] = {
            "active": True,
            "message": (
                "Portefeuille très concentré (" + " ; ".join(_conc_parts)
                + " du PTF) : vulnérable à une rotation défavorable — alléger / "
                "diversifier progressivement vers d'autres narratifs."
            ),
        }
        if _conc_top_sector:
            _alert["top_sector"] = _conc_top_sector
            _alert["top_pct"] = _conc_top_pct
        if _heavy:
            _alert["heavy_assets"] = [{"asset": s, "pct": round(p, 1)} for s, p in _heavy]
            _alert["top_asset"] = _heavy[0][0]
            _alert["top_asset_pct"] = round(_heavy[0][1], 1)
        payload["rebalance_alert"] = _alert

    # Pastilles ² (point 6) : statut de fiabilité par crypto pour le rendu weekly.
    if crypto_price_status_w:
        payload["crypto_price_status"] = crypto_price_status_w
    # ── v15 (audit B) — SCORE QUALITÉ PTF, 3 axes 0-10, 100% Python (simple
    # et honnête : chaque axe est une formule transparente, pas un avis IA).
    #   Diversification : poids du 1er secteur (25% → 10 ; 75% → 0).
    #   Momentum        : perf 7j vs BTC (−10 pts → 0 ; +10 pts → 10).
    #   Solidité        : drawdown ATH pondéré (0% → 10 ; −80% → ~1.6).
    # v19/M-B8 : l'axe « Réserve cash » a été RETIRÉ (cohérence avec le retrait
    # du cash de la note de risque matin/soir — un PTF 100% investi par choix ne
    # doit pas être pénalisé). Le score = moyenne des 3 axes restants.
    # L'évolution WoW est lue depuis weekly_snapshots (quality_score stocké).
    try:
        _q_axes: list[dict[str, Any]] = []
        _secs = (_weekly_sector_exposure.get("sectors") or []) \
            if _weekly_sector_exposure.get("available") else []
        _top_pct = max((s.get("ptf_pct") or 0) for s in _secs) if _secs else None
        _div = max(0.0, 10.0 - max(0.0, (_top_pct or 50) - 25) / 5.0)
        # v19/W-A10 : comparaison de la diversification vs semaine N-1 (note de
        # tendance demandée par l'audit). Lue depuis le dernier snapshot antérieur.
        _div_prev = None
        for _s in reversed(snapshots[:-1] if snapshots else []):
            if _s.get("diversification_score") is not None:
                _div_prev = _s["diversification_score"]
                break
        _div_detail = (f"top secteur {_top_pct:.0f}% du PTF"
                       if _top_pct is not None else "n/d")
        if _div_prev is not None:
            _div_delta = round(_div - _div_prev, 1)
            _div_detail += f" · {'+' if _div_delta >= 0 else ''}{_div_delta} vs N-1"
        _q_axes.append({"label": "Diversification", "score": round(_div, 1),
                        "detail": _div_detail})
        # v19/M-B8 : axe « Réserve cash » SUPPRIMÉ du score qualité (le cash
        # informatif reste affiché comme tuile, mais ne pénalise plus la note).
        _vsbtc = snap_w.get("vs_btc_7d_pct")
        _mom = max(0.0, min(10.0, 5.0 + (_vsbtc or 0) / 2.0))
        _q_axes.append({"label": "Momentum vs BTC", "score": round(_mom, 1),
                        "detail": (f"{_vsbtc:+.1f} pts vs BTC 7j"
                                   if _vsbtc is not None
                                   else "aligné sur BTC (donnée 7j manquante)")})
        _ddq = snap_w.get("drawdown_ath_pct")
        _ddq_used = _ddq if _ddq is not None else -50
        _sol = max(0.0, min(10.0, 10.0 + _ddq_used / 9.5))
        _q_axes.append({"label": "Solidité (vs ATH)", "score": round(_sol, 1),
                        "detail": (f"drawdown pondéré {_ddq:.0f}% vs ATH"
                                   if _ddq is not None
                                   else "drawdown vs ATH estimé (−50% par défaut)")})
        _q_score = round(sum(a["score"] for a in _q_axes) / len(_q_axes), 1)
        _q_prev = None
        for _s in reversed(snapshots[:-1] if snapshots else []):
            if _s.get("quality_score") is not None:
                _q_prev = _s["quality_score"]
                break
        # v23 — levier d'amélioration (axe le plus faible), cohérent avec la
        # note de SANTÉ matin/soir (même vocabulaire d'action).
        _q_weak = min(_q_axes, key=lambda a: a["score"]) if _q_axes else None
        _q_imp = _HEALTH_AXIS_IMPROVE.get(_q_weak["label"]) if _q_weak else None
        payload["ptf_quality_score"] = {
            "score": _q_score,
            "axes": _q_axes,
            "prev_score": _q_prev,
            "delta_wow": round(_q_score - _q_prev, 1) if _q_prev is not None else None,
            "improve": (_q_imp[0].upper() + _q_imp[1:] + ".") if _q_imp else None,
        }
        # Met à jour le snapshot de la semaine (écrase par iso_week → WoW futur).
        # v18 (Chantier E #8) : on stocke aussi VIX/F&G/DXY pour la mémoire des
        # contextes macro similaires. VIX/DXY depuis les séries FRED déjà
        # récupérées ; F&G depuis le dernier rapport du matin (déjà persisté).
        def _last_series_val(_name: str) -> float | None:
            try:
                _series = fred.get_macro_series(40).get(_name) or {}
                _vals = [v for _, v in sorted(_series.items())
                         if isinstance(v, (int, float))]
                return _vals[-1] if _vals else None
            except Exception:  # noqa: BLE001
                return None
        _snap_vix = _last_series_val("vix")
        _snap_dxy = _last_series_val("dxy")
        _snap_fg = (((mem.load_morning_report() or {}).get("macro_context") or {})
                    .get("fear_greed"))
        mem.record_weekly_snapshot(current_value, btc_price_now,
                                   drawdown_ath_pct=_dd_now,
                                   quality_score=_q_score,
                                   diversification_score=_div,
                                   vix=_snap_vix, fear_greed=_snap_fg,
                                   dxy=_snap_dxy)
    except Exception as _exc_q:  # noqa: BLE001 — le score ne doit jamais bloquer l'envoi
        logger.warning("Score qualité PTF indisponible : %s", _exc_q)
    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("weekly")
    # v14 — date du prochain hebdo calculée en Python (le cron weekly_report.yml
    # est dimanche 11h UTC = 12:00 Casablanca, UTC+1). Évite l'hallucination
    # Gemini (« lundi 15 juin ») et corrige l'ancienne mention erronée 15:00.
    _now_w = datetime.now(TZ)
    _days_to_sun = (6 - _now_w.weekday()) % 7  # weekday(): lundi=0..dimanche=6
    if _days_to_sun == 0:
        _days_to_sun = 7  # le prochain hebdo, pas celui d'aujourd'hui
    from datetime import timedelta as _td
    _next_sun = _now_w + _td(days=_days_to_sun)
    payload["footer"]["next_weekly"] = (
        f"{_JOURS_FR[_next_sun.weekday()]} {_next_sun.day} "
        f"{_MOIS_FR[_next_sun.month - 1]} {_next_sun.year}, 12:00 Casablanca"
    )
    # v17 (E-A11) : heure du prochain matin HARMONISÉE (08h30 partout). Avant, le
    # weekly affichait « lundi 08:00 » (texte IA) ≠ « 08h30 » du soir. On force la
    # valeur Python canonique pour que les 3 mails soient cohérents.
    payload["footer"]["next_morning"] = _next_report_label("weekly")
    mem.save_weekly_report(payload)
    # v23 \u2014 courbe d'\u00c9VOLUTION PTF (aire + ligne, base 100 vs BTC si s\u00e9rie align\u00e9e)
    # en image CID, \u00e0 la place des barres grises. D\u00e9gradation gracieuse : si le PNG
    # n'est pas g\u00e9n\u00e9r\u00e9 (matplotlib absent / < 3 points), le template retombe sur les
    # barres HTML.
    from src.reporting import charts as _charts
    _evo_png = _charts.portfolio_evolution_png(ptf_evolution, btc_points=_evo_btc)
    _wk_charts = {"ptf_evolution": _evo_png} if _evo_png else {}
    html = _render(payload, "weekly", charts=_wk_charts)
    _wk_inline = {f"chart_{k}": v for k, v in _wk_charts.items() if v}
    ok = send_email(f"\U0001f4ca Bilan hebdo crypto \u00b7 {datetime.now(TZ):%d/%m}",
                    html, inline_images=_wk_inline)
    logger.info("Hebdo: %s", ok)
    _push_telegram_notification(payload, "weekly")
    return 0 if ok else 1


def _push_telegram_notification(payload: dict[str, Any], kind: str) -> None:
    """Pousse une notification Telegram après un rapport (best-effort).

    N'échoue JAMAIS : le mail reste la livraison principale. Si le bot Telegram
    n'est pas configuré (secrets absents), l'appel est simplement ignoré.
    """
    try:
        from src.telegram_bot.notify import push_report_notification
        push_report_notification(payload, kind)
    except Exception as exc:  # noqa: BLE001
        logger.info("Notification Telegram ignorée (non bloquant) : %s", exc)


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
    # M-A1 (v18) : reset unique de l'historique de performance (recos, win rate,
    # scoring, snapshots) au tout premier run v18. Idempotent ensuite.
    try:
        mem.ensure_v18_reset()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reset v18 ignoré (non bloquant) : %s", exc)
    try:
        return {"morning": run_morning, "evening": run_evening,
                "weekly": run_weekly}[mode]()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec fatal mode %s : %s", mode, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
