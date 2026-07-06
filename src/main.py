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
from src.analytics.confidence_calibration import (
    apply_multiplier as _apply_conf_mult,
    compute_confidence_multiplier as _compute_conf_mult,
)
from src.analytics.exit_radar import compute_exit_signals
from src.analytics.fundamentals import compute_ath_distance, fundamental_score_from_signals
from src.analytics.narratives import detect_hot_narratives, sector_rotation
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
    news_relevance,
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
    # v26 (B5) — les flux ETF ont désormais 3 couches (Farside → CoinGlass →
    # canal Telegram ETF_Flows parsé en Python) : le libellé ne nomme plus
    # uniquement Farside (source déclarée down alors que les chiffres Telegram
    # étaient cités — contradiction A3 de l'audit v25).
    "ETF flows", "Telegram", "DeFiLlama", "Kaito", "Social trending",
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
    # v26 (B5/A3) — flux ETF STRUCTURÉS même quand Farside/CoinGlass sont KO
    # (cas GitHub Actions) : parsing déterministe du canal Telegram ETF_Flows,
    # repli aperçu web t.me. Fin de la contradiction « chiffres ETF cités dans
    # l'EN BREF mais source déclarée indisponible » (audit v25 A3).
    etf = etf_flows.merge_with_telegram(etf, telegram)
    defi = defillama.get_defi_tvl()
    narratives = kaito.get_trending_narratives()
    social_trending = lunarcrush.get_trending_coins()
    # OB6 — narratifs qui chauffent/refroidissent (catégories CoinGecko, gratuit,
    # sans clé) : remplace Kaito, MORT en prod faute de secret KAITO_API_KEY.
    # Filtré du bruit micro-cap / écosystèmes de chaînes / artefacts de composition.
    hot_narratives = detect_hot_narratives(coingecko.get_categories())
    # v26 (C2) — les événements CoinMarketCal (déjà en main) servent de repli
    # aux unlocks depuis que DefiLlama /emissions est payant.
    unlocks = token_unlocks.get_upcoming_unlocks(days_ahead=30,
                                                 crypto_events=crypto_events)
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
    # v26 (B1) — adresses actives BTC FRAÎCHES (blockchain.info / bitcoin-data,
    # gratuit sans clé) : remplace l'absolu daté du miroir CoinMetrics dans la
    # tuile on-chain (audit A2 : « légère hausse » qualifiait un snapshot gelé).
    try:
        from src.data_sources import bitcoin_data as _btc_data
        btc_active_fresh = _btc_data.get_btc_active_addresses()
    except Exception as _baexc:  # noqa: BLE001
        logger.info("Adresses actives BTC fraîches ignorées : %s", _baexc)
        btc_active_fresh = {"available": False}
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
            # v27 (TH1/TH2/RE2/RE3/ES1/ES2/ES3) — PLAN DÉTERMINISTE par actif
            # éligible : invalidation chiffrée, cible 30j en fourchette, cible
            # cycle, R:R, EV indicatif, bull/base/bear, zone d'accu + DCA.
            # SOURCE DE VÉRITÉ du plan d'action de la thèse (le LLM commente,
            # _merge_python_facts ré-écrase en post-génération).
            try:
                from src.analytics import asset_plan as _aplan
                _ath_dist_e = asset.get("ath_distance_pct")
                _plan_e = _aplan.compute_asset_plan(
                    sym, closes_for_hist,
                    price=asset["price"],
                    ath=asset.get("ath"),
                    ath_suspect=(isinstance(_ath_dist_e, (int, float))
                                 and _ath_dist_e <= -99.5),
                    funding_annualized_pct=(asset.get("derivatives") or {}
                                            ).get("funding_annualized_pct"),
                )
                if _plan_e.get("available"):
                    entry["asset_plan"] = _plan_e
            except Exception as _apexc:  # noqa: BLE001
                logger.info("asset_plan %s ignoré : %s", sym, _apexc)
            # v27 (TH5) — CATALYSEURS DATÉS de l'actif (unlocks token +
            # événements CoinMarketCal) : la thèse intègre le calendrier de SON
            # actif. Structures réelles : unlocks[].{symbol,date,pct_supply} et
            # crypto_events["events"][].{title,date,coins[]}.
            _cats_e: list[str] = []
            _symU = sym.upper()
            for _u in (unlocks.get("unlocks") or []):
                if str(_u.get("symbol") or "").upper() == _symU:
                    _pct_u = _u.get("pct_supply")
                    _cats_e.append(
                        f"unlock {_u.get('date')}"
                        + (f" ({_pct_u}% supply)" if _pct_u else ""))
            _ev_list = (crypto_events.get("events")
                        if isinstance(crypto_events, dict) else None) or []
            for _ce in _ev_list:
                if not isinstance(_ce, dict):
                    continue
                _coins = [str(c).upper() for c in (_ce.get("coins") or [])]
                if _symU in _coins and _ce.get("title"):
                    _cats_e.append(f"{_ce['title']} ({_ce.get('date')})")
            if _cats_e:
                entry["catalysts"] = _cats_e[:3]
            # v27 (TH7) — les FONDAMENTAUX (dilution FDV/MC, P/F, P/S, MC/TVL)
            # sont DÉJÀ calculés par valuation.py et exposés dans entry
            # ["valuation"] ci-dessus ; le prompt v27 les exploite désormais
            # explicitement dans la thèse (au lieu de les ignorer). Le NVT/MVRV
            # frais arrivent via onchain_advanced ci-dessous.
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
        # v26 (A9/B20) — le drapeau « Calendrier macro » reflète le calendrier
        # CONSOLIDÉ (ForexFactory + FRED + banques centrales), pas seulement
        # Boursorama (JS-only, quasi toujours down) : le mail v25 déclarait le
        # calendrier « indisponible » alors que ForexFactory alimentait bien
        # les événements — provenance mensongère corrigée.
        macro_news=bool(macro_news_items), macro_calendar=upcoming_calendar,
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

    # OB1 — RADAR DE SORTIE/ALLÈGEMENT (déterministe) : paliers de prise de profit
    # +80/×2/×3, offload des satellites sur pump, sur-concentration. Positions
    # valorisées live (prix vs PRU). Import LOCAL (call-time) → zéro circularité.
    # Best-effort : indisponible → liste vide, jamais bloquant.
    try:
        from src.telegram_bot.live_data import get_live_portfolio_snapshot
        _exit_positions = (get_live_portfolio_snapshot() or {}).get("positions") or []
    except Exception as _exit_exc:  # noqa: BLE001
        logger.info("Radar de sortie : snapshot live indisponible (%s).", _exit_exc)
        _exit_positions = []
    exit_signals = compute_exit_signals(_exit_positions)

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
        "narratives": digests.narratives_line(hot_narratives),  # OB6
        "exit_radar": exit_signals.get("summary", ""),          # OB1
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

    # v27 (ME1/TH4) — régime de marché + delta de conviction (best-effort).
    _morning_market_regime, _morning_thesis_deltas = (
        _compute_morning_regime_and_deltas(
            eligible, datetime.now(TZ).date().isoformat()))

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
        "hot_narratives": hot_narratives,        # OB6 — narratifs qui chauffent
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
        # v26 (B1/B7) — tuiles on-chain déterministes : adresses actives BTC
        # fraîches + dérivés BTC (funding/long-short déjà collectés par actif).
        "btc_active_addresses": btc_active_fresh,
        "btc_derivatives": (
            (enriched.get("BTC") or {}).get("derivatives")
            if isinstance((enriched.get("BTC") or {}).get("derivatives"), dict)
            else None
        ),
        "portfolio_risk": portfolio_risk,        # v22 (P1) — risque PTF consolidé
        "exit_signals": exit_signals,            # OB1 — radar de sortie/allègement
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
        # v26 (A14/B17) — les sources down ne sont PLUS répétées ici : elles
        # vivent au SEUL endroit « ⚠ Indisponibles » du footer (down_sources).
        # Le bloc « Angles morts » ne garde que le contenu analytique réel
        # (chiffres macro écartés, divergences de prix) — masqué si vide.
        "blind_spots": _blind_spots(
            macro_flags=list(_macro_validation_flags),
            price_discrepancies=price_discrepancies,
            price_divergences=price_divergences,
        ),
        # v19/V18-M10/M-A20 — sources INDISPONIBLES nommées (pour le footer), au
        # lieu d'un simple « 21/25 » que l'utilisateur doit déchiffrer ailleurs.
        "down_sources": [s for s in _ALL_SOURCES_LIST
                         if s not in set(active_sources)],
        # v27 (ME1) — RÉGIME DE MARCHÉ + (TH1) invalidations FRANCHIES/menacées
        # + (TH4) delta de conviction. Calculés ici (accès tracker/price_lookup)
        # et attachés au payload par _merge_python_facts.
        "market_regime": _morning_market_regime,
        "invalidations_deterministic": tracker.check_invalidations(price_lookup),
        "thesis_score_deltas": _morning_thesis_deltas,
    }


def _compute_morning_regime_and_deltas(
    eligible: list[dict[str, Any]], today_iso: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """v27 (ME1/TH4) — régime de marché BTC + delta de conviction par actif.

    Isolé pour rester best-effort : toute erreur → dicts vides, jamais de
    blocage du run. Le régime a besoin d'une série BTC longue (MM200) ; le
    delta relit l'historique des scores après avoir enregistré celui du jour.
    """
    regime: dict[str, Any] = {"available": False}
    try:
        from src.analytics import market_regime as _mr
        _closes_reg = coingecko.get_dated_closes("BTC", 300)
        if _closes_reg:
            _series = [_closes_reg[d] for d in sorted(_closes_reg)]
            regime = _mr.with_persistence(_mr.classify_regime(_series), today_iso)
    except Exception as _rexc:  # noqa: BLE001
        logger.info("Régime de marché indisponible : %s", _rexc)

    deltas: dict[str, Any] = {}
    try:
        scores_by_asset: dict[str, Any] = {}
        for e in (eligible or []):
            sc = e.get("thesis_scoring") or {}
            if sc.get("score") is None:
                continue
            by_cat: dict[str, float] = {}
            for s in (sc.get("signals") or []):
                cat = str(s.get("category") or "autre")
                try:
                    by_cat[cat] = by_cat.get(cat, 0.0) + float(s.get("weight") or 0)
                except (TypeError, ValueError):
                    continue
            scores_by_asset[str(e.get("asset")).upper()] = {
                "score": sc.get("score"), "by_category": by_cat,
            }
        if scores_by_asset:
            mem.record_thesis_scores(scores_by_asset)
            deltas = mem.load_thesis_score_deltas(7)
    except Exception as _dexc:  # noqa: BLE001
        logger.info("Delta de conviction indisponible : %s", _dexc)
    return regime, deltas


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
        # v24 — libellé AUTONOME (ne plus concaténer le biais LLM brut, qui laissait
        # pendre « … (garde-fou macro). défavorable » — redondant avec risk-off).
        readout["crypto_bias"] = "⚠ Prudence forcée (garde-fou macro)"


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
        # v26 (E-B10) — prix ETH : baseline du delta matin→soir du rapport du
        # soir (le state matin ne portait que le BTC → delta ETH impossible).
        "eth_price": (market.get("ETH") or {}).get("price"),
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
        "social": "Social trending", "unlocks": "Token Unlocks", "news": "News",
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
        labels = ["on-chain avancé", "Polymarket", "ETF flows", "Telegram", "DeFiLlama"]
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
        # v26 (A14/B17) — plus de phrase de remplissage : sans contenu
        # analytique réel, la section « Angles morts » est simplement masquée
        # (les sources down vivent déjà dans le footer « ⚠ Indisponibles »).
        return ""
    return " ".join(parts)


def _fr_ddmm(iso_date: Any) -> Optional[str]:
    """« 2026-05-23 » → « 23/05 » (None si non parsable)."""
    s = str(iso_date or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}"
    return None


def _int_fr(value: Any) -> Optional[str]:
    """Entier au format FR lisible (« 858 340 », espace fine insécable U+202F —
    même convention que ``_fmt_num_human``). None si non numérique."""
    v = _parse_num(value)
    if v is None:
        return None
    return f"{int(v):,}".replace(",", " ")


def _build_onchain_tiles(data: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """v26 (A1/A2/B19/B22/B7) — grille on-chain DÉTERMINISTE (fini Gemini).

    L'audit v25 a montré que les tuiles générées par Gemini étaient instables
    (« Whale Inflows ETH 0 · pas de signal vendeur » fabriqué depuis un vide,
    absolus non datés sur données du 23/05, format différent d'un run à l'autre).
    Les tuiles sont désormais construites en Python depuis les sources réelles,
    au GABARIT UNIQUE « valeur · Δ · date si différée » (B19). Gemini ne produit
    plus que le verdict et la lecture combinée.

    Returns:
        ``(tiles, freshness_note)`` — tiles peut être vide si tout est down ;
        freshness_note = phrase unique de fraîcheur différée (ou None).
    """
    tiles: list[dict[str, Any]] = []
    stale_notes: list[str] = []
    _GREEN, _RED, _AMBER, _INK, _GRAY = ("#3B6D11", "#A32D2D", "#BA7517",
                                         "#1a1a18", "#8a8880")

    def _is_dated(iso: Any, tolerance_days: int = 2) -> bool:
        """True si la donnée a plus de ``tolerance_days`` jours (différée)."""
        s = str(iso or "")[:10]
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return False
        return (datetime.now(TZ).date() - d).days > tolerance_days

    # ── MVRV BTC / ETH (CoinMetrics + surcouches fraîcheur) ──
    cm_assets = ((data.get("onchain_advanced") or {}).get("assets")
                 if isinstance(data.get("onchain_advanced"), dict) else None) or {}
    for sym in ("BTC", "ETH"):
        entry = cm_assets.get(sym) or {}
        mvrv = _parse_num(entry.get("mvrv"))
        if mvrv is None:
            continue
        zone = entry.get("mvrv_zone") or ""
        color = (_GREEN if mvrv < 1.0 else _INK if mvrv < 2.0
                 else _AMBER if mvrv < 3.5 else _RED)
        short = zone or "—"
        dated = _is_dated(entry.get("as_of"))
        if entry.get("mvrv_live_estimate"):
            # Estimation prix live / prix de revient daté : libellée, jamais
            # présentée comme une mesure on-chain du jour.
            short = f"{zone} · estim. prix live"
            if dated and _fr_ddmm(entry.get("as_of")):
                stale_notes.append(
                    f"MVRV {sym} estimé : prix live ÷ prix de revient au "
                    f"{_fr_ddmm(entry.get('as_of'))}")
        elif dated and _fr_ddmm(entry.get("as_of")):
            short = f"{zone} · au {_fr_ddmm(entry.get('as_of'))}"
            color = _GRAY
            stale_notes.append(f"MVRV {sym} au {_fr_ddmm(entry.get('as_of'))}")
        tiles.append({"label": f"MVRV {sym}", "value": f"{mvrv:.2f}",
                      "color": color, "short": short})

    # ── Adresses actives BTC (fraîches — blockchain.info/bitcoin-data) ──
    fresh_btc = data.get("btc_active_addresses") or {}
    if fresh_btc.get("available") and _int_fr(fresh_btc.get("value")):
        trend = _parse_num(fresh_btc.get("trend_7d_pct"))
        if trend is not None:
            short = f"{trend:+.1f}% / 7j"
            color = _GREEN if trend > 1 else _RED if trend < -1 else _INK
        else:
            short, color = "tendance 7j n/d", _INK
        tiles.append({"label": "Adresses actives BTC",
                      "value": _int_fr(fresh_btc.get("value")),
                      "color": color, "short": short})
    else:
        # Repli miroir (daté) — affiché HONNÊTEMENT : delta + date, grisé.
        btc_cm = cm_assets.get("BTC") or {}
        if _int_fr(btc_cm.get("active_addresses")):
            trend = _parse_num(btc_cm.get("active_addresses_trend_pct"))
            dd = _fr_ddmm(btc_cm.get("as_of"))
            short = (f"{trend:+.1f}% / 7j" if trend is not None else "Δ n/d")
            if dd and _is_dated(btc_cm.get("as_of")):
                short += f" · au {dd}"
            tiles.append({"label": "Adresses actives BTC",
                          "value": _int_fr(btc_cm.get("active_addresses")),
                          "color": _GRAY, "short": short})

    # ── Adresses actives ETH (miroir CoinMetrics — daté, grisé si différé) ──
    eth_cm = cm_assets.get("ETH") or {}
    if _int_fr(eth_cm.get("active_addresses")):
        trend = _parse_num(eth_cm.get("active_addresses_trend_pct"))
        dd = _fr_ddmm(eth_cm.get("as_of"))
        dated = _is_dated(eth_cm.get("as_of"))
        short = (f"{trend:+.1f}% / 7j" if trend is not None else "Δ n/d")
        if dd and dated:
            short += f" · au {dd}"
            stale_notes.append(f"adresses actives ETH au {dd}")
        tiles.append({"label": "Adresses actives ETH",
                      "value": _int_fr(eth_cm.get("active_addresses")),
                      "color": _GRAY if dated else _INK, "short": short})

    # ── Dérivés Deribit : put/call, max pain, DVOL ──
    opt_assets = ((data.get("options_deribit") or {}).get("assets")
                  if isinstance(data.get("options_deribit"), dict) else None) or {}
    btc_opt = opt_assets.get("BTC") or {}
    pcr = _parse_num(btc_opt.get("put_call_ratio"))
    if pcr is not None:
        short, color = ("appétit calls (haussier)", _GREEN) if pcr < 0.7 else \
            (("hedging puts (prudence)", _AMBER) if pcr > 1.3 else ("équilibré", _INK))
        tiles.append({"label": "Put/Call BTC", "value": f"{pcr:.2f}",
                      "color": color, "short": short})
    mp = _parse_num(btc_opt.get("max_pain"))
    if mp is not None:
        gap = _parse_num(btc_opt.get("max_pain_gap_pct"))
        if gap is not None and gap >= 1:
            short, color = f"+{gap:.1f}% vs spot · aimant haussier CT", _GREEN
        elif gap is not None and gap <= -1:
            short, color = f"{gap:.1f}% vs spot · aimant baissier CT", _RED
        else:
            short, color = "proche du spot · effet neutre", _INK
        tiles.append({"label": "Max Pain BTC", "value": f"${mp:,.0f}",
                      "color": color, "short": short})

    # ── Supply stablecoins : absolu ET Δ7j (B22 — les deux, plus jamais l'un
    # sans l'autre) ──
    st = data.get("stablecoin_supply") or {}
    st_total = _parse_num(st.get("total_mcap_usd"))
    if st.get("available") and st_total:
        chg = _parse_num(st.get("total_change_7d_pct"))
        if chg is not None:
            word = "stable" if abs(chg) < 0.5 else (
                "dry powder entrant" if chg > 0 else "capital sortant")
            short = f"{chg:+.2f}% / 7j · {word}"
            color = _GREEN if chg >= 0.5 else _RED if chg <= -0.5 else _INK
        else:
            short, color = "Δ 7j n/d", _INK
        tiles.append({"label": "Supply Stablecoins",
                      "value": f"{st_total / 1e9:,.1f} Mds$",
                      "color": color, "short": short})

    # ── Dépôts whales ETH (A1 : plus JAMAIS un « 0 » nu fabriqué d'un vide ;
    # source indisponible → tuile omise) ──
    wh = data.get("whale_inflows") or {}
    if wh.get("available"):
        n = wh.get("large_inflows_count")
        thr = int(_parse_num(wh.get("threshold_eth")) or 200)
        if isinstance(n, int) and n > 0:
            total_in = _parse_num(wh.get("total_eth_in")) or 0
            tiles.append({"label": "Dépôts whales ETH · 24h",
                          "value": f"{total_in:,.0f} ETH".replace(",", " "),
                          "color": _AMBER,
                          "short": f"{n} dépôt{'s' if n > 1 else ''} ≥{thr} ETH · pression vendeuse possible"})
        elif n == 0:
            tiles.append({"label": "Dépôts whales ETH · 24h",
                          "value": f"aucun ≥{thr} ETH",
                          "color": _INK, "short": "pas de signal vendeur détecté"})

    # ── Flux ETF BTC / ETH (B7/B22 — chiffres STRUCTURÉS et datés, cohérents
    # avec la liste des sources actives : fin de l'audit A3) ──
    etf = data.get("etf_flows") or {}
    if etf.get("available"):
        for sym in ("btc", "eth"):
            row = etf.get(sym) or {}
            flow = _parse_num(row.get("total_flow_musd"))
            if flow is None:
                continue
            sign = "+" if flow >= 0 else "−"
            value = f"{sign}${abs(flow):,.1f}M"
            dd = _fr_ddmm(row.get("date"))
            avg = _parse_num(row.get("avg_7d_musd"))
            bits = []
            if dd:
                bits.append(f"au {dd}")
            if avg is not None:
                bits.append(f"7j moy {'+' if avg >= 0 else '−'}${abs(avg):,.0f}M")
            bits.append("entrées nettes" if flow >= 0 else "sorties nettes")
            tiles.append({"label": f"Flux ETF {sym.upper()}", "value": value,
                          "color": _GREEN if flow >= 0 else _RED,
                          "short": " · ".join(bits[:2]) if len(bits) > 2 else " · ".join(bits)})

    # ── Funding BTC (B7 — dérivés discrets : funding + long/short) ──
    der = data.get("btc_derivatives") or {}
    fr_pct = _parse_num(der.get("funding_rate_pct"))
    if fr_pct is not None:
        if fr_pct >= 0.03:
            short, color = "longs surchauffés (risque flush)", _AMBER
        elif fr_pct <= -0.01:
            short, color = "shorts dominants (squeeze possible)", _GREEN
        else:
            short, color = "équilibré", _INK
        ls = _parse_num(der.get("long_short_ratio"))
        if ls is not None:
            short += f" · L/S {ls:.2f}"
        tiles.append({"label": "Funding BTC", "value": f"{fr_pct:+.3f}%",
                      "color": color, "short": short})

    # ── DVOL (volatilité implicite) ──
    bd = _parse_num(btc_opt.get("dvol"))
    ed = _parse_num((opt_assets.get("ETH") or {}).get("dvol"))
    dv = " · ".join(p for p in (
        (f"BTC {bd:.0f}" if bd is not None else None),
        (f"ETH {ed:.0f}" if ed is not None else None)) if p)
    if dv:
        tiles.append({"label": "Volatilité implicite (DVOL)", "value": dv,
                      "color": "#5a5852", "short": "vol. options annualisée"})

    # Note de fraîcheur UNIQUE (M-A22 : une seule mention, sous la grille).
    note = None
    if stale_notes:
        note = ("Données différées — " + " · ".join(dict.fromkeys(stale_notes))
                + ". Le reste de la grille est à jour (J ou J-1).")
    return tiles[:12], note


def _apply_asset_plans_to_theses(
    payload: dict[str, Any], data: dict[str, Any]
) -> None:
    """v27 (TH1/TH2/RE1/RE2/RE3/ES1/ES2/ES3) — plan déterministe → thèses.

    Écrase l'``action_plan`` et les ``targets`` de chaque thèse ferme avec les
    valeurs Python de ``asset_plan`` (invalidation chiffrée + basis, R:R, cible
    30j en fourchette, cible cycle), et attache ``asset_plan`` complet
    (scénarios bull/base/bear, EV, zone d'accu + DCA, plan_line) + catalyseurs
    + delta de conviction, pour le rendu. Le sizing (RE1) N'utilise JAMAIS le
    cash comme contrainte. Le contenu ÉDITORIAL du LLM (observation,
    raisonnement, contre-thèse) est conservé.
    """
    plans_by_asset = {
        str(e.get("asset")).upper(): e.get("asset_plan")
        for e in (data.get("eligible_theses") or [])
        if isinstance(e.get("asset_plan"), dict) and e["asset_plan"].get("available")
    }
    cats_by_asset = {
        str(e.get("asset")).upper(): e.get("catalysts")
        for e in (data.get("eligible_theses") or []) if e.get("catalysts")
    }
    core_by_asset = {
        str(e.get("asset")).upper(): bool(e.get("conviction"))
        for e in (data.get("eligible_theses") or [])
    }
    val_by_asset = {
        str(e.get("asset")).upper(): (e.get("value_usd") or 0.0)
        for e in (data.get("eligible_theses") or [])
    }
    deltas = data.get("thesis_score_deltas") or {}
    ptf_val = (data.get("portfolio_snapshot") or {}).get("value_usd")

    from src.analytics.asset_plan import suggest_sizing

    for t in (payload.get("thesis_of_the_day") or []):
        if not isinstance(t, dict):
            continue
        asset = str(t.get("asset") or "").upper()
        plan = plans_by_asset.get(asset)
        # Delta de conviction (TH4) : toujours attaché si dispo.
        d = deltas.get(asset)
        if isinstance(d, dict) and d.get("delta") is not None:
            t["conviction_delta"] = d
        if cats_by_asset.get(asset):
            t["catalysts"] = cats_by_asset[asset]
        if not plan:
            continue
        # Attache le plan complet (rendu du nouveau bloc + Telegram /pourquoi).
        t["asset_plan"] = plan
        t["plan_line"] = plan.get("plan_line")
        _act = (t.get("action") or "").upper()
        _firm = any(k in _act for k in ("RENFORC", "ALLÉG", "ALLEG"))
        if not _firm:
            continue
        inv = plan.get("invalidation") or {}
        tgt = plan.get("target_30d") or {}
        ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
        # Invalidation + R:R = source de vérité Python.
        ap["stop_loss"] = inv.get("level")
        ap["stop_loss_basis"] = inv.get("basis")
        ap["rr"] = plan.get("rr_30d")
        ap["invalidation_conditions"] = (
            f"sous {inv.get('level_label')} ({inv.get('basis')})")
        # Entrée : zone d'accumulation (RE3) si le LLM n'en a pas fourni.
        if not ap.get("entry"):
            _az = plan.get("accumulation_zone") or {}
            ap["entry"] = _az.get("high") or plan.get("price")
        # Sizing (RE1) — % PTF + $, cash jamais une contrainte.
        _w = (val_by_asset.get(asset, 0.0) / ptf_val * 100.0
              if ptf_val else None)
        _sz = suggest_sizing(
            action_type=("bearish" if "ALLÉG" in _act or "ALLEG" in _act
                         else "bullish"),
            weight_pct=_w, ptf_value_usd=ptf_val,
            is_core=core_by_asset.get(asset, False),
            position_value_usd=val_by_asset.get(asset))
        if _sz:
            if _sz.get("add_pct_ptf") is not None:
                ap["position_size_pct"] = _sz["add_pct_ptf"]
                ap["position_size_usd"] = _sz.get("add_usd")
            elif _sz.get("trim_pct_position") is not None:
                ap["position_size_pct"] = -_sz["trim_pct_position"]
                ap["position_size_usd"] = _sz.get("trim_usd")
            ap["sizing_note"] = _sz.get("note")
        t["action_plan"] = ap
        # Cibles (ES1/ES2) : 30j en fourchette + cycle. On respecte le contenu
        # LLM s'il existe, sinon on remplit avec les valeurs Python.
        _tg = t.get("targets") if isinstance(t.get("targets"), dict) else {}
        _tg.setdefault("short_term_30d", tgt.get("level"))
        _tg.setdefault("short_term_label", "Cible 30j (technique)")
        if not _tg.get("short_term_note") and tgt.get("low_label"):
            _tg["short_term_note"] = (f"fourchette {tgt.get('low_label')}"
                                      f"–{tgt.get('high_label')}")
        _cyc = plan.get("target_cycle")
        if _cyc:
            _tg.setdefault("long_term_6_12m_low", _cyc.get("low"))
            _tg.setdefault("long_term_6_12m_high", _cyc.get("high"))
            if not _tg.get("long_term_note"):
                _tg["long_term_note"] = (
                    "reconquête ATH (cycle)" if _cyc.get("kind") == "cycle"
                    else "objectif 6-12 mois")
        t["targets"] = _tg


def _compute_top_action(payload: dict[str, Any]) -> None:
    """v27 (RE4) — « Si tu ne fais qu'UNE chose » : meilleure thèse ferme.

    Classe les thèses fermes par (R:R × EV positif), et expose la n°1 en une
    ligne actionnable. Déterministe : survit à une panne IA. Rien si aucune
    thèse ferme exploitable.
    """
    best = None
    best_score = 0.0
    for t in (payload.get("thesis_of_the_day") or []):
        if not isinstance(t, dict):
            continue
        if not any(k in (t.get("action") or "").upper()
                   for k in ("RENFORC", "ALLÉG", "ALLEG")):
            continue
        plan = t.get("asset_plan") or {}
        rr = plan.get("rr_30d")
        ev = plan.get("ev_30d_pct")
        if not isinstance(rr, (int, float)) or not isinstance(ev, (int, float)):
            continue
        score = rr * max(ev, 0.1)
        if score > best_score:
            best_score = score
            ap = t.get("action_plan") or {}
            _line = f"{t.get('action')} {t.get('asset')}"
            if ap.get("sizing_note"):
                _line += f" · {ap['sizing_note']}"
            elif ap.get("entry"):
                _line += f" ~{ap['entry']}"
            if rr:
                _line += f" · R:R {rr}"
            best = {
                "asset": t.get("asset"), "action": t.get("action"),
                "line": _line, "rr": rr, "ev_pct": ev,
                "invalidation": (plan.get("invalidation") or {}).get("level_label"),
            }
    if best:
        payload["top_action"] = best


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
    # v26 (A17) — libellés adaptés aux runs HORS-CYCLE : un morning relancé à
    # 12h35 affichait « P&L nuit » et « synthèse de la nuit ». Fenêtre matin
    # nominale = 5h-11h (Casablanca) ; au-delà, libellés neutres et honnêtes.
    _run_hour = datetime.now(TZ).hour
    if not (5 <= _run_hour <= 11):
        payload.setdefault("portfolio_snapshot", {})
        if isinstance(payload["portfolio_snapshot"], dict):
            payload["portfolio_snapshot"]["overnight_label"] = "P&L depuis dernier rapport"
        header["subtitle_context"] = "run hors-cycle · point intrajournalier"

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

    # v26 (B18/B21) — AGENDA MACRO déterministe (72h) : l'endroit UNIQUE où
    # l'événement du jour vit avec son heure, sa prévision et son précédent
    # (ForexFactory). Fini le NFP répété 4 fois sans chiffre de consensus, et
    # fini le « NFP +172k » présenté comme un fait du jour alors que c'est le
    # PRÉCÉDENT (audits A11/A13). Zéro Gemini : rendu direct.
    _cal = data.get("upcoming_calendar") or {}
    _agenda_items: list[dict[str, Any]] = []
    if isinstance(_cal, dict) and _cal.get("available"):
        for _ev in (_cal.get("events") or []):
            if not isinstance(_ev, dict):
                continue
            _da = _ev.get("days_ahead")
            if not isinstance(_da, (int, float)) or not (0 <= _da <= 3):
                continue
            _agenda_items.append({
                "label": _ev.get("label"),
                "when": _ev.get("when"),
                "date_label": _ev.get("date_label"),
                "time": _ev.get("time"),
                "importance": _ev.get("importance"),
                "forecast": _ev.get("forecast"),
                "previous": _ev.get("previous"),
                "estimated": bool(_ev.get("estimated")),
                "_sort": (_da, 0 if _ev.get("importance") == "high" else 1),
            })
        # Ordre CHRONOLOGIQUE (High avant Medium le même jour). Cap 6 lignes :
        # si trop d'événements, les Medium les plus lointains sautent d'abord
        # (jamais un High), puis on retrie chronologiquement.
        _agenda_items.sort(key=lambda x: x["_sort"])
        if len(_agenda_items) > 6:
            _keep = [x for x in _agenda_items if x["importance"] == "high"][:6]
            for x in _agenda_items:
                if len(_keep) >= 6:
                    break
                if x not in _keep:
                    _keep.append(x)
            _keep.sort(key=lambda x: x["_sort"])
            _agenda_items = _keep
        for x in _agenda_items:
            x.pop("_sort", None)
    if _agenda_items:
        # NB : clé « events » (PAS « items ») — en Jinja, ``macro_agenda.items``
        # résoudrait la MÉTHODE dict.items et casserait le rendu.
        payload["macro_agenda"] = {"available": True, "events": _agenda_items}

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

    # Blind spots : utiliser la phrase Python (factuelle). v26 (A14/B17) —
    # override TOTAL : si Python n'a rien d'analytique à signaler, la section
    # disparaît (Gemini ne peut plus y recopier la liste des sources down, qui
    # ferait doublon avec le footer « ⚠ Indisponibles »).
    if "blind_spots" in data:
        if data.get("blind_spots"):
            payload["blind_spots"] = data["blind_spots"]
        else:
            payload.pop("blind_spots", None)

    # On-chain : injecter les chiffres réels
    onc = data.get("onchain_indicators") or {}
    if onc.get("available"):
        payload.setdefault("onchain_indicators", {}).update({
            k: v for k, v in onc.items() if k != "available" and v is not None
        })
    # v26 (A1/A2/B19) — la GRILLE on-chain devient DÉTERMINISTE : tuiles
    # construites en Python depuis les sources réelles (valeur · Δ · date si
    # différée), Gemini ne garde que le verdict + la lecture combinée. Si le
    # builder ne produit rien (toutes sources down), on conserve le repli
    # Gemini existant plutôt qu'une grille vide.
    try:
        _det_tiles, _fresh_note = _build_onchain_tiles(data)
    except Exception as _otexc:  # noqa: BLE001
        logger.info("Tuiles on-chain déterministes ignorées : %s", _otexc)
        _det_tiles, _fresh_note = [], None
    if _det_tiles:
        _oi_det = payload.setdefault("onchain_indicators", {})
        _oi_det["metrics"] = _det_tiles
        if _fresh_note:
            _oi_det["freshness_note"] = _fresh_note
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
                    f"en dessous des {THESIS_CONFIDENCE_FLOOR}% requis pour une reco "
                    f"ferme. Il manque une convergence plus nette (cassure de niveau "
                    f"confirmée, signal on-chain franc ou catalyseur daté). "
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

        # v26 (A20/B4) — JOUR SANS NOUVELLE RECO : bannière déterministe qui
        # explique le contexte (les recos actives restent en vigueur) au lieu
        # du seul pavé « aucune thèse ». Cas typique : 2e run du même jour,
        # l'anti-répétition a raison de ne rien ré-émettre.
        _tracked_n = len(data.get("active_recommendations_display")
                         or data.get("active_recommendations") or [])
        if _firm_n == 0 and _tracked_n > 0:
            payload["thesis_context_note"] = (
                f"Aucune nouvelle reco ce matin — les {_tracked_n} recos "
                f"actives restent en vigueur (détail dans le Tracking "
                f"ci-dessous). Une absence de nouveau signal est une "
                f"information, pas un oubli."
            )
        # v26 (A4/B2) — le repli « zéro thèse » ne doit JAMAIS être un pavé
        # markdown brut (« * BTC (Confiance plafonnée à 80%)… » affiché tel
        # quel dans le mail v25). Deux filets :
        #   1. si Gemini a fourni le champ STRUCTURÉ no_thesis_assets (schéma
        #      v26), on le garde tel quel pour le rendu en lignes propres ;
        #   2. sinon, on DÉCOUPE thesis_empty_reason sur ses puces « * » pour
        #      produire des bullets rendues proprement.
        _nta = payload.get("no_thesis_assets")
        if not (isinstance(_nta, list) and _nta):
            payload.pop("no_thesis_assets", None)
            _reason = payload.get("thesis_empty_reason")
            if isinstance(_reason, str) and " * " in f" {_reason}":
                import re as _re_te
                _parts = _re_te.split(r"\s\*\s+", " " + _reason.strip())
                _intro = _parts[0].strip(" *")
                _bullets = [p.strip() for p in _parts[1:] if p.strip()]
                if _bullets:
                    payload["thesis_empty_reason"] = _intro
                    payload["thesis_empty_bullets"] = _bullets[:8]

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

    # ── v27 — RÉGIME (ME1) + PLANS par actif (TH1/TH2/RE*/ES*) + TOP ACTION
    # (RE4) + INVALIDATIONS déterministes franchies/menacées (TH1). ──
    _reg = data.get("market_regime")
    if isinstance(_reg, dict) and _reg.get("available"):
        payload["market_regime"] = _reg
    # OB17/OB1 — expose le RADAR DE SORTIE déterministe au template (section
    # « À alléger aujourd'hui »), en plus du canal LLM (analytics_digest).
    _exs = data.get("exit_signals")
    if isinstance(_exs, dict) and _exs.get("available"):
        payload["exit_signals"] = _exs
    try:
        _apply_asset_plans_to_theses(payload, data)
        _compute_top_action(payload)
    except Exception as _apexc:  # noqa: BLE001 — jamais bloquant
        logger.info("Application des plans v27 ignorée : %s", _apexc)

    # OB24 — CALIBRATION DE CONFIANCE « HUMBLE » : si l'agent a été historiquement
    # SUR-confiant (track record), on RÉDUIT la confiance affichée des thèses
    # (borné [0.70,1.00], JAMAIS un boost ; kill-switch LEARNING_ENABLED ; inerte
    # tant que < 10 prédictions clôturées). Transparent : payload.confidence_
    # calibration porte le multiplicateur + la raison. Jamais bloquant.
    try:
        _cal = _compute_conf_mult(PredictionTracker())
        payload["confidence_calibration"] = _cal
        _cmult = _cal.get("multiplier", 1.0)
        if _cal.get("available") and isinstance(_cmult, (int, float)) and _cmult < 1.0:
            for _th in (payload.get("thesis_of_the_day") or []):
                if isinstance(_th, dict) and _th.get("confidence") is not None:
                    _th["confidence"] = _apply_conf_mult(_th.get("confidence"), _cmult)
    except Exception as _calexc:  # noqa: BLE001 — jamais bloquant
        logger.info("Calibration de confiance ignorée : %s", _calexc)
    # Fusion des invalidations DÉTERMINISTES (prix réel vs stop des recos
    # actives) avec le bloc invalidation_watch : les FRANCHIES/menacées Python
    # priment et passent en tête (le LLM ne peut pas les inventer ni les rater).
    _inv_det = data.get("invalidations_deterministic") or []
    if _inv_det:
        _existing = payload.get("invalidation_watch")
        _existing = _existing if isinstance(_existing, list) else []
        _det_rows = [{"condition": r.get("condition"),
                      "implication": r.get("implication"),
                      "status": r.get("status")} for r in _inv_det
                     if isinstance(r, dict) and r.get("condition")]
        payload["invalidation_watch"] = (_det_rows + _existing)[:6] or None

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
        # v26 (A12/B6) — persister la CIBLE 30j et le STOP de la thèse : sans
        # eux, le Tracking du lendemain ne pouvait ni afficher « cible/stop »
        # ni mesurer la progression VERS l'objectif (badge « Sur objectif »
        # attribué dès +3% arbitraires — audit A12).
        _tgt = _parse_num((th.get("targets") or {}).get("short_term_30d")
                          if isinstance(th.get("targets"), dict) else None)
        _ap_th = th.get("action_plan") if isinstance(th.get("action_plan"), dict) else {}
        _sl_th = _parse_num(_ap_th.get("stop_loss"))
        if _tgt is not None and _tgt > 0:
            reco["ct_target"] = _tgt
        if _sl_th is not None and _sl_th > 0:
            reco["stop_loss"] = _sl_th
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
    # Graphiques adaptés pour les thèses DÉTAILLÉES (top-3 dépliées en
    # prose, audit A1). limit=3 pour coller au détail rendu : générer un 4e
    # graphique l'attacherait en CID sans qu'il soit jamais référencé (orphelin).
    # v26 — seules les thèses FERMES sont chartées (le template ne rend que
    # leurs cartes ; une SURVEILLER chartée créait un CID orphelin latent).
    from src.reporting import charts
    _firm_for_charts = [
        t for t in (payload.get("thesis_of_the_day") or [])
        if isinstance(t, dict)
        and any(k in (t.get("action") or "").upper()
                for k in ("RENFORC", "ALLÉG", "ALLEG"))
    ]
    chart_imgs = charts.charts_for_theses(_firm_for_charts, limit=3)
    # v26 (B8/A20) — JOUR SANS NOUVELLE RECO : le mail ne perd plus tous ses
    # graphiques. On charte les recos ACTIVES où la lecture a de la valeur
    # (proche stop/cible, mouvement notable) : cours + plan (entrée/cible/
    # stop) + MM50 + RSI. Clés « track_<SYM> » → CID « chart_track_<SYM> ».
    if not chart_imgs:
        try:
            _track_imgs = charts.charts_for_tracked_recos(
                payload.get("active_recommendations_tracking") or [], limit=2)
            chart_imgs.update({f"track_{s}": p for s, p in _track_imgs.items()})
        except Exception as _tcexc:  # noqa: BLE001
            logger.info("Graphiques de suivi ignorés : %s", _tcexc)
    # v27 (AF6) — jauge visuelle Fear & Greed (sentiment en un regard).
    try:
        _fg_val = (payload.get("macro_context") or {}).get("fear_greed")
        if _fg_val is not None:
            _fg_png = charts.gauge_png(
                _fg_val, vmin=0, vmax=100, label="Fear & Greed",
                value_label=str(int(_fg_val)),
                zones=[(0, 25, "#A32D2D"), (25, 45, "#BA7517"),
                       (45, 55, "#8a8880"), (55, 75, "#3B6D11"),
                       (75, 100, "#0E6B5E")])
            if _fg_png:
                chart_imgs["fng_gauge"] = _fg_png
    except Exception as _gexc:  # noqa: BLE001
        logger.info("Jauge F&G ignorée : %s", _gexc)
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


def _split_evening_calendar(
    consolidated: dict[str, Any], now_local: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scinde le calendrier consolidé pour la checklist du soir (v16 + v26).

    v16 : ``tomorrow_macro_events`` = événements ≤ 2 jours calendaires (le
    weekly couvre la semaine), ``upcoming_macro_events`` = fenêtre 7 j.
    v26 (E-A6/E-B6) : un événement du JOUR (J0) DÉJÀ TOMBÉ est exclu de la
    checklist « demain matin » — l'audit v25 listait « aujourd'hui NFP » à
    15h03 alors que le NFP était sorti à 13h30. Un événement J0 encore à venir
    est gardé mais relabellisé (« ce soir » après 17h, sinon « encore
    aujourd'hui ») avec son heure.
    """
    tomorrow: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    if not isinstance(consolidated, dict) or not consolidated.get("available"):
        return tomorrow, upcoming
    for e in consolidated.get("events", []):
        if not isinstance(e, dict):
            continue
        _entry = {
            "label": e.get("label"),
            "date": e.get("date"),
            "when": e.get("when"),
            "days_ahead": e.get("days_ahead"),
            "source": e.get("source"),
            "importance": e.get("importance"),
            # v18 (E-A15) : libellé jour/date propre déjà calculé à la source
            # (« mardi 16 juin ») → évite la triple redondance dans le rendu.
            "weekday_label": e.get("weekday_label"),
            "date_label": e.get("date_label"),
            "time": e.get("time"),
        }
        upcoming.append(_entry)
        _da = e.get("days_ahead")
        if isinstance(_da, int) and _da <= 2:
            if _da == 0:
                _t = str(e.get("time") or "")
                _passed = False
                if ":" in _t:
                    try:
                        _hh, _mm = _t.split(":")[:2]
                        _passed = ((now_local.hour, now_local.minute)
                                   >= (int(_hh), int(_mm)))
                    except (ValueError, TypeError):
                        _passed = False
                if _passed:
                    continue  # déjà tombé → rien à surveiller demain matin
                _entry = dict(_entry)
                _entry["when"] = ("ce soir" if now_local.hour >= 17
                                  else "encore aujourd'hui")
            tomorrow.append(_entry)
    return tomorrow, upcoming


def _mark_published_events(
    consolidated: dict[str, Any], now_local: datetime
) -> dict[str, Any]:
    """v26 (W-A2/W-B2) — marque les événements J0 DÉJÀ TOMBÉS du calendrier.

    Le weekly v25, parti à 20h42, listait le NFP de 13h30 comme « aujourd'hui »
    À VENIR — et tout le fil rouge / les scénarios / le plan d'action étaient
    construits autour d'un catalyseur déjà connu. Chaque événement du jour dont
    l'heure est passée reçoit ``already_published=True`` et un ``when`` honnête
    (« déjà publié aujourd'hui (13h30) ») : le rendu le badge en gris et le
    prompt doit le traiter comme un FAIT à analyser, plus comme une attente.

    Returns:
        Copie du calendrier consolidé, événements enrichis. Entrée non-dict ou
        indisponible → retournée telle quelle.
    """
    if not isinstance(consolidated, dict) or not consolidated.get("available"):
        return consolidated
    out_events: list[dict[str, Any]] = []
    for e in consolidated.get("events", []):
        if not isinstance(e, dict):
            continue
        e = dict(e)
        e.setdefault("already_published", False)
        if e.get("days_ahead") == 0:
            _t = str(e.get("time") or "")
            if ":" in _t:
                try:
                    _hh, _mm = _t.split(":")[:2]
                    if (now_local.hour, now_local.minute) >= (int(_hh), int(_mm)):
                        e["already_published"] = True
                        _t_fr = f"{int(_hh)}h{_mm}"
                        e["when"] = f"déjà publié aujourd'hui ({_t_fr})"
                except (ValueError, TypeError):
                    pass
        out_events.append(e)
    return {**consolidated, "events": out_events}


def _fmt_usd_short(v: Any) -> Optional[str]:
    """« 61 949 $ » (milliers U+202F) / « 3.42 $ » — formatage compact FR."""
    x = _parse_num(v)
    if x is None:
        return None
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", " ") + " $"
    if abs(x) >= 100:
        return f"{x:.0f} $"
    return f"{x:.2f} $"


def _pct_fr_signed(v: float, nd: int = 1) -> str:
    """« +1,2% » / « −0,3% » — signe typographique + virgule décimale."""
    return f"{'+' if v >= 0 else '−'}{abs(round(v, nd))}%".replace(".", ",")


def _build_since_morning_facts(
    morning_state: dict[str, Any], morning_is_today: bool,
    evening_macro: dict[str, Any], polymarket: dict[str, Any],
    morning_time_label: Optional[str],
) -> Optional[dict[str, Any]]:
    """v26 (E-B10) — deltas matin→soir 100 % Python (survivent à l'IA).

    BTC, ETH, F&G, DXY, proba Fed dominante : chaque delta n'est émis que si
    les DEUX bornes existent (baseline matin réelle, pas de faux delta).
    """
    if not morning_is_today:
        return None
    m_macro = (morning_state or {}).get("macro_context") or {}
    if not isinstance(m_macro, dict):
        return None
    parts: list[str] = []

    def _px_delta(label: str, m_v: Any, e_v: Any) -> None:
        m_x, e_x = _parse_num(m_v), _parse_num(e_v)
        if m_x and e_x and m_x > 0:
            parts.append(
                f"{label} {_fmt_usd_short(m_x)} → {_fmt_usd_short(e_x)}"
                f" ({_pct_fr_signed((e_x / m_x - 1) * 100)})")

    _px_delta("BTC", m_macro.get("btc_price"), evening_macro.get("btc_price"))
    _px_delta("ETH", m_macro.get("eth_price"), evening_macro.get("eth_price"))
    m_fng = _parse_num(m_macro.get("fear_greed"))
    e_fng = _parse_num(evening_macro.get("fear_greed"))
    if m_fng is not None and e_fng is not None:
        _d = int(e_fng - m_fng)
        _d_txt = ("stable" if _d == 0
                  else ("+" if _d > 0 else "−") + str(abs(_d)) + " pts")
        parts.append(f"F&G {int(m_fng)} → {int(e_fng)} ({_d_txt})")
    m_dxy = _parse_num(m_macro.get("dxy"))
    e_dxy = _parse_num(evening_macro.get("dxy"))
    if m_dxy and e_dxy:
        parts.append(f"DXY {m_dxy:.2f} → {e_dxy:.2f}"
                     f" ({_pct_fr_signed((e_dxy / m_dxy - 1) * 100)})")
    m_fed = (m_macro.get("polymarket_fed_bars") or {})
    e_fed = ((polymarket or {}).get("fed_bars") or {})
    m_dom, e_dom = m_fed.get("dominant"), e_fed.get("dominant")
    m_pct = _parse_num(m_fed.get("dominant_pct"))
    e_pct = _parse_num(e_fed.get("dominant_pct"))
    if m_dom and e_dom and m_dom == e_dom and m_pct is not None and e_pct is not None:
        _dp = round(e_pct - m_pct, 1)
        parts.append(
            f"Fed {e_dom} {str(m_pct).replace('.', ',')}% → "
            f"{str(e_pct).replace('.', ',')}%"
            f"{'' if _dp == 0 else ' (' + _pct_fr_signed(_dp).replace('%', ' pts') + ')'}")
    if not parts:
        return None
    return {
        "available": True,
        "window_label": (f"depuis le matin ({morning_time_label})"
                         if morning_time_label else "depuis le matin"),
        "line": " · ".join(parts),
    }


def _build_evening_derivatives_line(
    btc_deriv: Optional[dict[str, Any]], etf: Optional[dict[str, Any]]
) -> Optional[str]:
    """v26 (E-B9/E-A14) — ligne « Dérivés & flux » compacte (1 ligne, discrète).

    Funding BTC + ratio L/S (Binance) et flux ETF BTC/ETH DATÉS (Farside ou
    canal Telegram) : des faits collectés mais jamais affichés le soir en v25.
    """
    parts: list[str] = []
    d = btc_deriv or {}
    if d.get("available"):
        f = _parse_num(d.get("funding_rate_pct"))
        if f is not None:
            parts.append(f"Funding BTC {_pct_fr_signed(f, 3)}")
        ls = _parse_num(d.get("long_short_ratio"))
        if ls is not None:
            parts.append(f"L/S {str(round(ls, 2)).replace('.', ',')}")
    e = etf or {}
    if e.get("available"):
        for sym in ("btc", "eth"):
            entry = e.get(sym) or {}
            flow = _parse_num(entry.get("total_flow_musd"))
            if flow is None:
                continue
            _dt = _fr_ddmm(entry.get("date"))
            parts.append(
                f"ETF {sym.upper()} {'+' if flow >= 0 else '−'}"
                f"${abs(flow):,.1f}M".replace(",", " ")
                + (f" ({_dt})" if _dt else ""))
    return " · ".join(parts) if parts else None


def _apply_evening_degraded_fallbacks(
    payload: dict[str, Any], *,
    daily_pnl: dict[str, Any],
    evening_macro: dict[str, Any],
    polymarket: dict[str, Any],
    computed_levels: dict[str, dict[str, Any]],
    since_morning: Optional[dict[str, Any]],
) -> None:
    """v26 (E-A1/E-B2) — un 503 Gemini ne vide PLUS le mail du soir.

    Reconstruit en Python les sections vitales que l'IA aurait produites :
    « À retenir » (faits chiffrés), « Niveaux à surveiller » (calculés, cf.
    key_levels) et la checklist scénario/invalidation (range ATR + supports).
    L'audit v25 : 6 sections disparues d'un coup, mail coquille. Plus jamais.
    """
    from src.analytics import key_levels as _kl

    # ── À retenir : puces factuelles (l'avertissement IA reste en tête) ──
    bullets: list[dict[str, str]] = list(payload.get("delta_summary") or [])
    fng = _parse_num(evening_macro.get("fear_greed"))
    fng_label = evening_macro.get("fear_greed_label")
    if fng is not None:
        _warn = fng < 25 or fng > 75
        bullets.append({
            "icon": "⚠" if _warn else "✓",
            "text": (f"Fear & Greed à {int(fng)} ({fng_label or 'n/d'}) — "
                     + ("aversion au risque persistante, prudence sur toute "
                        "prise de risque overnight." if fng < 25 else
                        "euphorie de marché, attention aux excès." if fng > 75
                        else "sentiment sans excès notable.")),
        })
    _pnl = _parse_num(daily_pnl.get("day_change_pct"))
    movers = daily_pnl.get("top_movers") or []
    if _pnl is not None:
        _m0 = movers[0] if movers else None
        _mover_txt = (f" — porté par {_m0['symbol']} "
                      f"{_pct_fr_signed(_parse_num(_m0.get('change')) or 0)}"
                      if _m0 else "")
        bullets.append({
            "icon": "✓" if _pnl >= 0 else "⚠",
            "text": (f"P&L du portefeuille {_pct_fr_signed(_pnl, 2)} depuis le "
                     f"matin{_mover_txt}."),
        })
    fed = (polymarket or {}).get("fed_bars") or {}
    if fed.get("dominant") and _parse_num(fed.get("dominant_pct")) is not None:
        bullets.append({
            "icon": "⚠",
            "text": (f"Polymarket price un {fed['dominant']} des taux Fed à "
                     f"{str(fed['dominant_pct']).replace('.', ',')}% — cadre de "
                     "liquidité inchangé pour la nuit."),
        })
    _vix = _parse_num(evening_macro.get("vix"))
    _dxy = _parse_num(evening_macro.get("dxy"))
    if _vix is not None or _dxy is not None:
        _vix_txt = (f"VIX {str(round(_vix, 1)).replace('.', ',')} "
                    f"({'stress' if _vix >= 25 else 'calme'})" if _vix is not None else "")
        _dxy_txt = f"DXY {_dxy:.2f}" if _dxy is not None else ""
        _sep = " · " if _vix_txt and _dxy_txt else ""
        bullets.append({
            "icon": "✗" if (_vix is not None and _vix >= 25) else "✓",
            "text": (f"{_dxy_txt}{_sep}{_vix_txt} — "
                     + ("stress actions élevé, risk-off possible sur la crypto."
                        if _vix is not None and _vix >= 25 else
                        "pas de signal de stress côté actions.")),
        })
    if bullets:
        payload["delta_summary"] = bullets[:5]

    # ── Niveaux à surveiller : calculés (pivots/MM/Fibo), plus jamais absents ──
    rows: list[dict[str, Any]] = []
    readouts: dict[str, str] = {}
    for sym in ("BTC", "ETH", *[s for s in computed_levels if s not in ("BTC", "ETH")]):
        comp = computed_levels.get(sym)
        if not comp:
            continue
        rows.extend(_kl.levels_tonight_rows(comp))
        if comp.get("readout_line"):
            readouts[sym] = comp["readout_line"]
    if rows and not payload.get("levels_tonight"):
        payload["levels_tonight"] = rows
    if readouts:
        _existing_ro = payload.get("levels_readout")
        payload["levels_readout"] = {**readouts, **(_existing_ro or {})}

    # ── Checklist : scénario + invalidation templatés depuis l'ATR/les supports ──
    _tc = payload.get("tomorrow_checklist")
    if not isinstance(_tc, dict):
        _tc = {}
    btc = computed_levels.get("BTC") or {}
    btc_rng = btc.get("expected_range") or {}
    btc_sups = btc.get("supports") or []
    if not _tc.get("scenario") and btc_rng.get("low_label"):
        _hold = (f" tant que {btc_sups[0]['level_label']} tient"
                 if btc_sups else "")
        _tint = (" · biais prudent (peur extrême)"
                 if fng is not None and fng < 25 else "")
        _tc["scenario"] = (f"Nuit attendue dans le range "
                           f"{btc_rng['low_label']}–{btc_rng['high_label']} "
                           f"(±ATR){_hold}{_tint}.")
    if not _tc.get("invalidation") and btc_sups:
        _nxt = (f" → test de {btc_sups[1]['level_label']} probable"
                if len(btc_sups) > 1 else "")
        _tc["invalidation"] = (f"Clôture sous {btc_sups[0]['level_label']} "
                               f"({btc_sups[0]['basis']}){_nxt}.")
    if not _tc.get("checks"):
        _chk = []
        if btc_sups:
            _chk.append(f"BTC tient {btc_sups[0]['level_label']} ?")
        _m0 = (daily_pnl.get("top_movers") or [None])[0]
        if _m0:
            _chk.append(f"{_m0['symbol']} conserve son "
                        f"{_pct_fr_signed(_parse_num(_m0.get('change')) or 0)} overnight ?")
        if _chk:
            _tc["checks"] = " · ".join(_chk)
    if _tc:
        payload["tomorrow_checklist"] = _tc

    # ── Ligne « depuis le matin » : déjà déterministe, on s'assure qu'elle vit ──
    if since_morning and not payload.get("since_morning_facts"):
        payload["since_morning_facts"] = since_morning


def run_evening() -> int:
    """Génère et envoie le rapport du soir (différentiel)."""
    from src.ai_brain.decision_engine import DecisionEngine
    logger.info("=== RAPPORT SOIR ===")
    portfolio_data = load_portfolio()
    portfolio = portfolio_data["portfolio"]
    symbols = [s for s, i in portfolio.items() if i.get("role") != "cash_reserve"]
    market = coingecko.get_market_data(symbols)
    fng = fear_greed.get_fear_greed()
    # v26 (E-A14) — flux ETF complétés par le canal Telegram (aperçu web t.me)
    # quand Farside/CoinGlass sont KO — même logique que le matin. Le soir n'a
    # pas de session Telethon : merge_with_telegram(None) retombe sur l'aperçu.
    etf = etf_flows.merge_with_telegram(etf_flows.get_etf_flows(), None)
    # v26 (E-B9) — funding + ratio long/short BTC (Binance) : collectés pour la
    # ligne « Dérivés & flux » du soir (facts jamais affichés en v25).
    btc_deriv = binance_futures.get_derivatives("BTC")
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
    # v26 (E-A3/E-A5/E-B3) — pertinence crypto directe OU indirecte (macro qui
    # meut la crypto) + titres nettoyés (« $$0.07308 »). Le repli v25 affichait
    # 3 news tradfi sur 5 (distributions d'ETF obligataires, PR FedRAMP).
    news_global = [
        {**n, "title": news_relevance.sanitize_title(n.get("title"))}
        for n in news_global
        if news_relevance.is_crypto_relevant(n.get("title"))
    ]
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
    ev_upcoming = _mc.get_consolidated_calendar(horizon_days=7)
    tomorrow_macro_events, upcoming_macro_events = _split_evening_calendar(
        ev_upcoming, now_local)

    # v26 (E-B5) — niveaux S/R CALCULÉS (pivots/MM/Fibo/Bollinger/ronds) + readout
    # technique complet pour BTC, ETH et les gros movers du jour. Injectés au
    # prompt comme SOURCE DE VÉRITÉ (l'IA choisit, ne fabrique plus de ronds
    # arbitraires) et rendus tels quels si l'IA tombe (mode dégradé).
    from src.analytics import key_levels as _key_levels
    from src.reporting import charts as _charts
    computed_levels: dict[str, dict[str, Any]] = {}
    _lvl_syms: list[str] = ["BTC", "ETH"]
    for _bm in big_movers_day[:2]:
        if _bm.get("symbol") and _bm["symbol"] not in _lvl_syms:
            _lvl_syms.append(_bm["symbol"])
    for _ls in _lvl_syms[:4]:
        try:
            _ser = _charts._load_series(_ls, days=180)
            if not _ser:
                continue
            _px_live = (price_lookup.get(_ls)
                        or (evening_macro.get("btc_price") if _ls == "BTC"
                            else evening_macro.get("eth_price") if _ls == "ETH"
                            else None))
            _comp = _key_levels.compute_key_levels(
                _ls, _ser.get("closes") or [], _ser.get("volumes"),
                price=_px_live)
            if _comp.get("available"):
                computed_levels[_ls] = _comp
        except Exception as _exc_lvl:  # noqa: BLE001
            logger.info("Niveaux calculés indisponibles pour %s : %s", _ls, _exc_lvl)

    # v26 (E-B10) — deltas matin→soir 100 % Python (BTC/ETH/F&G/DXY/Fed) :
    # l'essence d'un « complément du matin », et il survit à une panne IA.
    since_morning = _build_since_morning_facts(
        morning_state, morning_is_today, evening_macro, polymarket,
        morning_time_label)

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
        # v26 (E-B5) — niveaux calculés : SOURCE DE VÉRITÉ des levels_tonight.
        "computed_levels": {
            s: {"price": c.get("price_label"),
                "supports": c.get("supports"),
                "resistances": c.get("resistances"),
                "readout": c.get("readout_line"),
                "expected_range": c.get("expected_range")}
            for s, c in computed_levels.items()
        },
        # v26 (E-B10) — deltas matin→soir factuels (l'IA les cite, ne recalcule pas).
        "since_morning_facts": since_morning,
        # v26 (E-B9) — funding/L-S BTC pour le contexte dérivés du soir.
        "btc_derivatives": btc_deriv if btc_deriv.get("available") else None,
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
        # v26 (E-A12/E-B14) — run hors-cycle SIGNALÉ : quand le soir suit le
        # matin de < 4h, les sections « depuis ce matin » couvrent une fenêtre
        # courte — on le dit dans le sous-titre au lieu de laisser croire à une
        # journée pleine (audit v25 : soir à 15h03, matin 12h37, rien signalé).
        if _degenerate_window:
            header["timing_line"] += " · fenêtre courte"
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
    # v26 (E-B10) — ligne « Depuis le matin » déterministe (bloc Marchés).
    if since_morning:
        payload["since_morning_facts"] = since_morning
    # v26 (E-B9/E-A14) — ligne « Dérivés & flux » compacte (funding, L/S, ETF datés).
    _dfl = _build_evening_derivatives_line(btc_deriv, etf)
    if _dfl:
        payload["derivatives_flows_line"] = _dfl
    # v26 (E-B5) — readout technique par actif, AUSSI en mode nominal : la ligne
    # grise sous chaque actif du bloc « Niveaux » (RSI/MACD/Bollinger/ATR).
    _ro_nominal = {s: c["readout_line"] for s, c in computed_levels.items()
                   if c.get("readout_line")}
    if _ro_nominal:
        payload["levels_readout"] = {
            **_ro_nominal, **(payload.get("levels_readout") or {})}
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
    # S6 : si Gemini n'a pas produit de résumé news intraday, on fournit les
    # titres (déjà filtrés crypto + nettoyés). v26 (E-A4/E-B4) : horodatage
    # « 14h58 » heure locale — plus jamais l'ISO brut « 2026-07-02T13:58:09+00:00 ».
    if not payload.get("intraday_news") and news_global:
        payload["intraday_news"] = [
            {"title": n.get("title"), "source": n.get("source"),
             "timestamp": news_relevance.fmt_time_local(n.get("published_at"), TZ)}
            for n in news_global[:5]
        ]

    # v26 (E-A1/E-B2) — MODE DÉGRADÉ DIGNE : si l'IA est tombée (503/quota),
    # reconstruire en Python « À retenir », les niveaux calculés et la checklist
    # scénario/invalidation. L'audit v25 : 6 sections perdues d'un coup.
    if payload.get("_degraded"):
        _apply_evening_degraded_fallbacks(
            payload, daily_pnl=daily_pnl, evening_macro=evening_macro,
            polymarket=polymarket, computed_levels=computed_levels,
            since_morning=since_morning)

    # v27 (ME1) — RÉGIME DE MARCHÉ aussi au soir (cohérence cross-mail : le
    # lecteur voit le même « temps qu'il fait » matin, soir et weekly).
    try:
        from src.analytics import market_regime as _mre
        _reg_closes_e = coingecko.get_dated_closes("BTC", 300)
        if _reg_closes_e:
            _reg_series_e = [_reg_closes_e[d] for d in sorted(_reg_closes_e)]
            _reg_e = _mre.with_persistence(
                _mre.classify_regime(_reg_series_e),
                datetime.now(TZ).date().isoformat())
            if _reg_e.get("available"):
                payload["market_regime"] = _reg_e
    except Exception as _regexc:  # noqa: BLE001
        logger.info("Régime soir indisponible : %s", _regexc)

    payload.setdefault("footer", {})["next_report_at"] = _next_report_label("evening")
    mem.save_evening_report(payload)
    html = _render(payload, "evening")
    ok = send_email(f"\U0001f319 Veille crypto \u00b7 soir \u00b7 {datetime.now(TZ):%d/%m}", html)
    logger.info("Soir: %s", ok)
    _push_telegram_notification(payload, "evening")
    return 0 if ok else 1


def _is_core_asset(sym: str, info: dict[str, Any] | None) -> bool:
    """★ cœur = conviction RÉELLE (BTC/ETH/TAO/LINK), PAS le tier d'analyse.

    Override par actif via ``core: true|false`` dans portfolio.yaml (prioritaire),
    sinon le set CORE_ASSETS du profil. SOURCE DE VÉRITÉ UNIQUE du label
    cœur/satellite — utilisée à la fois par le tableau Positions du hebdo ET par
    l'exit plan des poussières (avant : tier 1/2 des deux côtés → poussières comme
    RSR/JASMY étiquetées « cœur »).
    """
    from src.ai_brain.prompts.investor_profile import CORE_ASSETS
    c = (info or {}).get("core")
    if isinstance(c, bool):
        return c
    return str(sym).upper() in CORE_ASSETS


def _build_calls_review(
    prev_calls: dict[str, Any], btc_price_now: Any,
    fear_greed_now: Any, regime_now: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """v27 (ME2/ME3) — verdict des appels du hebdo PRÉCÉDENT.

    Compare ce qui avait été annoncé (scénario dominant, prix BTC, F&G,
    régime) à ce qui s'est réellement passé, en une ligne honnête. None si
    pas d'historique (premier hebdo).
    """
    if not isinstance(prev_calls, dict) or not prev_calls.get("dominant_scenario"):
        return None
    bits: list[str] = []
    _prev_btc = _parse_num(prev_calls.get("btc_price"))
    _now_btc = _parse_num(btc_price_now)
    if _prev_btc and _now_btc and _prev_btc > 0:
        _chg = (_now_btc / _prev_btc - 1) * 100
        bits.append(f"BTC {_fmt_usd_short(_prev_btc)} → {_fmt_usd_short(_now_btc)} "
                    f"({_pct_fr_signed(_chg)})")
    _prev_reg = prev_calls.get("regime")
    _now_reg = regime_now.get("regime")
    if _prev_reg and _now_reg:
        bits.append(f"régime {'inchangé' if _prev_reg == _now_reg else _prev_reg + ' → ' + _now_reg}")
    _prev_fg = _parse_num(prev_calls.get("fear_greed"))
    _now_fg = _parse_num(fear_greed_now)
    if _prev_fg is not None and _now_fg is not None:
        bits.append(f"F&G {int(_prev_fg)} → {int(_now_fg)}")
    _scn = prev_calls.get("dominant_scenario")
    _pct = prev_calls.get("dominant_pct")
    # Verdict qualitatif : le scénario dominant annoncé s'est-il matérialisé ?
    verdict = None
    if _prev_btc and _now_btc and _scn:
        _mv = (_now_btc / _prev_btc - 1) * 100
        _sl = str(_scn).lower()
        if any(k in _sl for k in ("baiss", "bear", "sous support")):
            verdict = "conforme" if _mv < -2 else ("partiel" if _mv <= 2 else "démenti")
        elif any(k in _sl for k in ("hauss", "bull", "rebond")):
            verdict = "conforme" if _mv > 2 else ("partiel" if _mv >= -2 else "démenti")
        else:  # range/neutre
            verdict = "conforme" if abs(_mv) <= 5 else "démenti"
    header_line = (f"Scénario dominant annoncé : {_scn}"
                   + (f" ({_pct}%)" if _pct else "")
                   + (f" — {verdict}" if verdict else ""))
    return {
        "available": True,
        "prev_week_label": prev_calls.get("week_label"),
        "header_line": header_line,
        "verdict": verdict,
        "summary_line": (header_line + (" · " + " · ".join(bits) if bits else "")),
    }


def _build_positions_review(
    long_term: Any, scoring_detail: Any,
    portfolio: dict[str, Any], market: dict[str, Any],
    ath_facts: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """v23.x — FUSION (1 ligne/actif) des 2 anciens tableaux du weekly.

    Joint le positionnement LONG TERME (LLM : analyse, cible, phase de cycle,
    action) avec la performance de la reco à 30j (Python : reco, Δ, statut) et
    enrichit DÉTERMINISTIQUEMENT prix actuel, % vs PRU et conviction (cœur —
    cf. _is_core_asset : set CORE_ASSETS du profil + override portfolio).
    Union par actif : positions LT d'abord (ordre LLM), puis recos sans thèse LT.
    """
    price_by = {s.upper(): _parse_num((market.get(s) or {}).get("price")) for s in portfolio}
    pru_by = {s.upper(): _parse_num((portfolio.get(s) or {}).get("pru")) for s in portfolio}
    core_by = {s.upper(): _is_core_asset(s, portfolio.get(s) or {}) for s in portfolio}

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

    from src.analytics import weekly_guards as _wg_pr

    out: list[dict[str, Any]] = []
    for a in order:
        lt = lt_by.get(a) or {}
        d = h30_by.get(a)
        price = price_by.get(a)
        pru = pru_by.get(a)
        pru_pct = (round((price - pru) / pru * 100, 1)
                   if price and pru and pru > 0 else None)
        h30 = None
        if isinstance(d, dict):
            h30 = {"reco": d.get("reco"),
                   "delta_pct": d.get("delta_pct"),
                   "status": d.get("status")}

        # ── v26 (W-A4/B6) — FALLBACKS Python : un actif sous reco active sans
        # thèse LT (RSR au v25) n'affiche plus « — / cible à définir / — ».
        _fact = (ath_facts or {}).get(a) or {}
        _from_ath = _parse_num(_fact.get("from_ath_pct"))
        _ath_real = _parse_num(_fact.get("ath"))
        _ath_suspect = _wg_pr.ath_is_suspect(_from_ath)
        lt_status = lt.get("status")
        if not lt_status and _from_ath is not None and not _ath_suspect:
            # Phase de cycle déterministe depuis le drawdown ATH réel (mêmes
            # seuils que la règle 7 du prompt).
            _dd = abs(_from_ath)
            lt_status = ("capitulation" if _dd > 75
                         else "accumulation" if _dd >= 50 else "expansion")

        # ── v26 (W-A7/B6) — COHÉRENCE action ↔ reco 30j ACTIVE : la reco
        # d'achat en cours PRIME (fini le « RENFORCER +1.8% » à côté d'une
        # action « Garder » sur la même ligne).
        action = lt.get("action")
        _reco30 = str((d or {}).get("reco") or "").upper()
        _status30 = str((d or {}).get("status") or "")
        _reco_active = _status30 in ("in_progress", "validated")
        if _reco_active and _reco30.startswith(("RENFORCER", "BUY", "ACCUMULER")):
            if str(action or "").lower() not in ("renforcer",):
                action = "renforcer"
        elif _reco_active and _reco30.startswith(("ALLÉGER", "ALLEGER", "SELL", "SORTIR")):
            if str(action or "").lower() in ("renforcer", "", "none"):
                action = "alléger"
        elif not action:
            action = "garder" if (h30 or lt) else None

        # ── v26 (W-A16/B5) — CIBLE LT crédible : jamais > ATH réel, et une
        # cible ≥ +250% est étiquetée « cycle » (reconquête ATH), pas « 6-12m ».
        target = _parse_num(lt.get("target_price"))
        if _ath_suspect:
            target = None
        elif target and _ath_real and target > _ath_real * 1.05:
            target = _ath_real
        target_pct = (round((target - price) / price * 100)
                      if target and price and price > 0 else None)
        target_kind = None
        if target_pct is not None:
            target_kind = "cycle" if target_pct >= 250 else "6-12m"

        out.append({
            "asset": a,
            "conviction": core_by.get(a, _is_core_asset(a, None)),
            "current_price": price,
            "pru_pct": pru_pct,
            "h30": h30,
            "lt_status": lt_status,
            "lt_target": target,
            "lt_target_pct": target_pct,
            "lt_target_kind": target_kind,
            "analysis": (lt.get("analysis") or lt.get("thesis_short")
                         or lt.get("thesis")),
            "action": action,
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
    # v26 (W-B9) : horizon élargi 8 → 10 jours (le v25 s'arrêtait à J+1).
    calendar = _mc_w.get_consolidated_calendar(horizon_days=10)
    _now_wk = datetime.now(TZ)
    # v26 (W-A2/W-B2) — les événements J0 déjà tombés sont MARQUÉS (« déjà
    # publié aujourd'hui (13h30) ») : le prompt les traite en faits, le rendu
    # les badge en gris. Fini le « aujourd'hui NFP » à 20h42.
    calendar = _mark_published_events(calendar, _now_wk)
    polymarket = prediction_markets.get_key_markets()
    # v26 (W-A14) — même repli Telegram (aperçu t.me) que matin/soir : le hebdo
    # n'était PAS câblé sur merge_with_telegram et déclarait « ETF indispo »
    # alors qu'un aperçu existait.
    etf = etf_flows.merge_with_telegram(etf_flows.get_etf_flows(), None)
    # v26 (W-B3) — F&G direct (valeur + historique 8j → évolution WoW),
    # structure de marché (dominance BTC/ETH, mcap total) et % 7j RÉELS des
    # indices actions/or/DXY (W-A9 : fini les « −16.13 points » ambigus).
    fng_w = fear_greed.get_fear_greed()
    _global_w = coingecko.get_global()
    _macro_week_pct = market_prices.get_macro_week_pct()
    _yq_w = market_prices.get_macro_quotes()
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
                # v24 — conviction = vrai set cœur (cf. _is_core_asset), PAS le tier
                # d'analyse : sinon une poussière tier 1/2 (RSR…) serait « protégée »
                # de l'exit plan à tort. Source de vérité unique cœur/satellite.
                "conviction": _is_core_asset(s, portfolio.get(s) or {}),
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

    # v26 (W-B3/W-A6) — NIVEAUX CALCULÉS (source de vérité, comme le soir) :
    # S/R pivots/MM/Fibo/Bollinger/ronds + readout RSI/MACD/ATR pour BTC, ETH
    # et les 2 plus gros movers 7j. Injectés au prompt (interdit d'inventer un
    # niveau hors liste), ancrage du scaffold scénarios ET du graphique BTC.
    from src.analytics import key_levels as _key_levels_w
    from src.reporting import charts as _charts_w
    computed_levels_w: dict[str, dict[str, Any]] = {}
    _lvl_syms_w: list[str] = ["BTC", "ETH"]
    for _s7 in sorted(
            significant,
            key=lambda s: abs(market.get(s, {}).get("change_7d") or 0),
            reverse=True):
        if _s7 not in _lvl_syms_w:
            _lvl_syms_w.append(_s7)
        if len(_lvl_syms_w) >= 4:
            break
    _btc_closes_w: list[float] = []
    for _ls in _lvl_syms_w[:4]:
        try:
            _ser_w = _charts_w._load_series(_ls, days=180)
            if not _ser_w:
                continue
            _comp_w = _key_levels_w.compute_key_levels(
                _ls, _ser_w.get("closes") or [], _ser_w.get("volumes"),
                price=price_lookup.get(_ls))
            if _comp_w.get("available"):
                computed_levels_w[_ls] = _comp_w
                if _ls == "BTC":
                    _btc_closes_w = _ser_w.get("closes") or []
        except Exception as _exc_lvl_w:  # noqa: BLE001
            logger.info("Niveaux calculés weekly indisponibles pour %s : %s",
                        _ls, _exc_lvl_w)

    # v23.x — échafaudage déterministe des SCÉNARIOS de la semaine (rempli dans le
    # try ci-dessous quand les signaux sont prêts ; {} si indispo → repli LLM seul).
    _scenario_scaffold: dict[str, Any] = {}

    # v18 (Chantier E) — signaux d'analyse transverses pour le weekly (l'analyse
    # la plus profonde) : liquidité M2, cycle DXY, spreads HY, saisonnalité,
    # régime de vol réalisée, structure de marché, biais de confirmation, MVRV.
    weekly_cross_signals = {"signals": {}, "readings": []}
    # v26 — capturés hors du try (consommés par les faits déterministes plus
    # bas, même si cross_signals échoue) : dérivés par actif, fraîcheur
    # on-chain (W-A8), indice dollar élargi FRED (W-A11).
    _derivs_w: dict[str, Any] = {}
    _onchain_as_of_w: Optional[str] = None
    _onchain_assets_w: dict[str, Any] = {}
    _dxy_broad_w: Optional[float] = None
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
        # v26 (W-A8) — fraîcheur on-chain AU POINT D'USAGE : le v25 citait
        # « MVRV 1.14 » comme un fait courant alors que le miroir datait du
        # 23/05 (40 jours). L'as_of est injecté au prompt + affiché.
        _onchain_as_of_w = (
            (((_onchain_w.get("assets") or {}).get("BTC") or {}).get("as_of")
             if isinstance(_onchain_w, dict) else None)
        )
        _onchain_assets_w = (
            (_onchain_w.get("assets") or {}) if isinstance(_onchain_w, dict) else {}
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
        # v26 (W-A11) — indice dollar ÉLARGI (FRED, ~115-125), distinct du DXY
        # ICE (~99-105) : les deux sont affichés côte à côte dans les repères.
        _dxy_broad_w = _cur_macro_w.get("dxy")
        # #2 DVOL (move implicite) + #14 dérivés (funding) pour le weekly.
        _options_w = deribit.get_options_metrics()
        _btc_dvol_w = (
            ((_options_w.get("assets") or {}).get("BTC") or {}).get("dvol")
            if isinstance(_options_w, dict) else None
        )
        # (_derivs_w déclaré hors du try — v26, cf. plus haut.)
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
        # v26 (W-A6) — les S/R du scaffold viennent en PRIORITÉ des niveaux
        # CALCULÉS (plus proches, horizon cohérent) ; TradingView n'est plus
        # que le repli (son pivot long-horizon a produit le « 82 416 » recyclé
        # en borne de range 7j au v25).
        _btc_comp_w = computed_levels_w.get("BTC") or {}
        _sup_comp_w = ((_btc_comp_w.get("supports") or [{}])[0]).get("level")
        _res_comp_w = ((_btc_comp_w.get("resistances") or [{}])[0]).get("level")
        _scenario_scaffold = compute_scenario_scaffold(
            btc_price=_btc_mk.get("price"),
            implied_move_7d_pct=_imp_w.get("move_7d_pct"),
            polymarket=polymarket,
            vix=_cur_macro_w.get("vix"),
            dxy_trend=_dxy_trend_w,
            fear_greed=_cur_macro_w.get("fear_greed"),
            btc_funding_pct=(_derivs_w.get("BTC") or {}).get("funding_annualized_pct"),
            btc_support=_sup_comp_w if _sup_comp_w else _btc_sr_w.get("support"),
            btc_resistance=_res_comp_w if _res_comp_w else _btc_sr_w.get("resistance"),
            btc_trend_pct=_btc_ma_w.get("price_vs_sma50_pct"),
            btc_rsi=_btc_rsi_w,
            btc_change_7d=_btc_mk.get("change_7d"),
            calendar_events=(calendar.get("events") if isinstance(calendar, dict) else None),
        )
    except Exception as _xexc_w:  # noqa: BLE001
        logger.info("cross_signals weekly ignoré : %s", _xexc_w)

    # v26 (W-B7) — DIGEST NEWS DE LA SEMAINE : flux RSS crypto 7 jours, filtrés
    # crypto-related (news_relevance, mêmes règles que le soir), titres nettoyés
    # + horodatés. Best-effort : sans news, la section est simplement absente.
    weekly_news_digest: list[dict[str, Any]] = []
    try:
        _rss_w = crypto_rss.get_news(hours=7 * 24, high_impact_only=True, limit=25)
        for _n in (_rss_w.get("news") or []):
            _title = str(_n.get("title") or "")
            if not _title or not news_relevance.is_crypto_relevant(_title):
                continue
            _pub = str(_n.get("published") or "")
            _d_lbl = None
            try:
                _pdt = datetime.fromisoformat(_pub.replace("Z", "+00:00")).astimezone(TZ)
                _d_lbl = f"{_JOURS_FR[_pdt.weekday()][:3]} {_pdt.day:02d}/{_pdt.month:02d}"
            except (ValueError, TypeError):
                pass
            weekly_news_digest.append({
                "title": news_relevance.sanitize_title(_title),
                "source": _n.get("source"),
                "date_label": _d_lbl,
            })
            if len(weekly_news_digest) >= 5:
                break
    except Exception as _exc_news_w:  # noqa: BLE001
        logger.info("Digest news hebdo indisponible : %s", _exc_news_w)

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
    # ── v26 — profondeur d'analyse (B3) + sources de vérité chiffrées ──
    # F&G : valeur unique + évolution 7j (W-A18 prompt / W-B3).
    if fng_w.get("available"):
        data["fear_greed"] = {
            "value": fng_w.get("value"),
            "classification": fng_w.get("classification"),
            "value_7d_ago": fng_w.get("value_7d_ago"),
            "delta_7d": fng_w.get("delta_7d"),
        }
    # Structure de marché : dominance BTC/ETH, mcap total, ratio ETH/BTC 7j.
    if _global_w.get("available"):
        _eth7_ms = (market.get("ETH") or {}).get("change_7d")
        _btc7_ms = (market.get("BTC") or {}).get("change_7d")
        _ethbtc_7d = None
        if isinstance(_eth7_ms, (int, float)) and isinstance(_btc7_ms, (int, float)) \
                and (1 + _btc7_ms / 100) != 0:
            _ethbtc_7d = round(((1 + _eth7_ms / 100) / (1 + _btc7_ms / 100) - 1) * 100, 1)
        _eth_px_ms = (market.get("ETH") or {}).get("price")
        _btc_px_ms = (market.get("BTC") or {}).get("price")
        data["market_structure"] = {
            "btc_dominance_pct": _global_w.get("btc_dominance_pct"),
            "eth_dominance_pct": _global_w.get("eth_dominance_pct"),
            "total_market_cap_usd": _global_w.get("total_market_cap_usd"),
            "market_cap_change_24h_pct": _global_w.get("market_cap_change_24h_pct"),
            "eth_btc_ratio": (round(_eth_px_ms / _btc_px_ms, 5)
                              if _eth_px_ms and _btc_px_ms else None),
            "eth_btc_7d_pct": _ethbtc_7d,
        }
    # W-A9 : % 7j RÉELS des indices (S&P/Nasdaq/Stoxx/DAX/Nikkei/or/DXY ICE) —
    # les indices se citent en % 7j, JAMAIS en points de séance.
    if _macro_week_pct:
        data["markets_week_pct"] = _macro_week_pct
    # W-A11 : le DXY cité par le hebdo est le DXY ICE — valeur affichée pour
    # que les niveaux (« casse 101.5 ») soient vérifiables par le lecteur.
    if _yq_w.get("dxy_ice") is not None:
        data["dxy_ice"] = _yq_w.get("dxy_ice")
    if _dxy_broad_w is not None:
        data["dxy_broad"] = _dxy_broad_w
    # W-A6/W-B5 : niveaux CALCULÉS = source de vérité des scénarios.
    if computed_levels_w:
        data["computed_levels"] = computed_levels_w
    # W-A8 : fraîcheur on-chain au point d'usage.
    if _onchain_as_of_w:
        data["onchain_as_of"] = _onchain_as_of_w
    # W-B3 : dérivés par actif (funding annualisé, L/S, OI) déjà récupérés.
    if _derivs_w:
        data["derivatives"] = {
            s: {"funding_annualized_pct": d.get("funding_annualized_pct"),
                "long_short_ratio": d.get("long_short_ratio"),
                "open_interest": d.get("open_interest")}
            for s, d in _derivs_w.items()
        }
    # W-B7 : digest news 7j (le prompt explique les movers avec de VRAIES news).
    if weekly_news_digest:
        data["weekly_news"] = weekly_news_digest
    # W-A5 : flag ATH suspect (listing illiquide) — le prompt ne cite pas de
    # drawdown pour ces actifs (l'audit a vu « JASMY −99.9% sous ATH »).
    from src.analytics import weekly_guards as _wg
    for _s_ath, _f_ath in (data.get("ath_by_asset") or {}).items():
        if isinstance(_f_ath, dict):
            _f_ath["suspect"] = _wg.ath_is_suspect(_f_ath.get("from_ath_pct"))
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
    # v26 (W-A15) — RUN HORS-CYCLE signalé : le v25 est parti un JEUDI 20h42
    # en construisant une « semaine à venir » comme s'il était dimanche midi,
    # sans le dire. Même honnêteté que le « · fenêtre courte » du soir.
    if _now_h.weekday() != 6:  # weekday(): lundi=0 .. dimanche=6
        header["offcycle_note"] = (
            f"Run hors-cycle ({_JOURS_FR[_now_h.weekday()]}) — l'hebdo est "
            f"planifié le dimanche 12:00 ; fenêtres 7 j glissantes."
        )
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
        payload.get("long_term_positioning"), scoring_detail, portfolio, market,
        ath_facts=data.get("ath_by_asset"),
    )

    # ─────────────────────────────────────────────────────────────
    # v26 — GARDES DÉTERMINISTES POST-GÉNÉRATION (weekly_guards).
    # Le prompt seul n'a pas suffi au v25 : perf hebdo réinventée (+2.32% vs
    # +3.8%), vs BTC ÷10, SORTIE sur un actif RENFORCÉ, drawdown ATH halluciné,
    # « ETH 2.0 en cours », MVRV BTC recopié sur ETH, indices en « points ».
    # Chaque garde corrige en Python et logge ce qu'elle a touché.
    # ─────────────────────────────────────────────────────────────
    _wg_fixes: list[str] = []
    _fg_val_w = (fng_w.get("value") if fng_w.get("available") else
                 (((mem.load_morning_report() or {}).get("macro_context") or {})
                  .get("fear_greed")))
    payload["weekly_summary"], _fx = _wg.enforce_summary_figures(
        payload.get("weekly_summary"), payload.get("portfolio_snapshot"),
        fear_greed_value=_fg_val_w)
    _wg_fixes += _fx
    payload["weekly_summary"], _fx = _wg.fix_equity_points_in_bullets(
        payload.get("weekly_summary"), _macro_week_pct)
    _wg_fixes += _fx
    _wg_fixes += _wg.reconcile_recos(payload, scoring_detail)
    _wg_fixes += _wg.scrub_stale_narratives(payload)
    _wg_fixes += _wg.sanitize_ath_claims(
        payload.get("positions_review"), data.get("ath_by_asset"))
    _wg_fixes += _wg.sanitize_cross_asset_mvrv(
        payload.get("positions_review"), _onchain_assets_w)
    _held_w = {s.upper() for s in symbols}
    for _k_held in ("losses_vs_recos", "my_errors"):
        payload[_k_held], _fx = _wg.fix_held_opportunity_wording(
            payload.get(_k_held), _held_w)
        _wg_fixes += _fx
    if _wg_fixes:
        logger.info("Gardes weekly v26 : %d correction(s) — %s",
                    len(_wg_fixes), " | ".join(_wg_fixes[:8]))
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
            # v26 (W-A2) — un J0 déjà tombé garde son libellé honnête
            # (« déjà publié aujourd'hui (13h30) ») posé par
            # _mark_published_events, au lieu d'un « aujourd'hui » à venir.
            if e.get("already_published"):
                _when = e.get("when") or "déjà publié aujourd'hui"
            else:
                _when = ("aujourd'hui" if da == 0 else "demain" if da == 1
                         else f"dans {da}j")
            _wk_cal_events.append({
                "label": e.get("label"), "date": e.get("date"),
                "when": _when,
                "days_ahead": da,
                "already_published": bool(e.get("already_published")),
                "time": e.get("time"),
            })
        payload["upcoming_calendar_facts"] = {"available": True, "events": _wk_cal_events}
    # v26 (W-A13) — polymarket_facts est posé UNE fois, plus bas, dans un
    # format complet {available, markets, fed_bars, extra_markets} : l'ancien
    # double-set (dict complet ici, puis ÉCRASÉ par un dict sans `available`
    # ni `markets`) rendait le bandeau déterministe invisible au template.
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
            # v26 (W-A2) — badge « déjà publié » au rendu.
            "already_published": bool(e.get("already_published")),
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
    if polymarket.get("available") or _fed_bars_w:
        # v26 (W-A13) — format ALIGNÉ sur le template : `available` + `markets`
        # (le bandeau du v25 était invisible car ces clés manquaient).
        payload["polymarket_facts"] = {
            "available": True,
            "markets": (polymarket.get("markets") or [])[:3],
            "fed_bars": _fed_bars_w or None,
            "extra_markets": polymarket.get("extra_markets") or [],
        }

    # ─────────────────────────────────────────────────────────────
    # v26 (W-A11/A13/B3/B9) — REPÈRES DÉTERMINISTES : lignes factuelles 100%
    # Python (probas Fed, dollar ICE + élargi, flux ETF, F&G WoW, structure de
    # marché, dérivés). Le lecteur peut VÉRIFIER les chiffres que la prose
    # cite ; une panne d'une source fait juste sauter sa ligne.
    # ─────────────────────────────────────────────────────────────
    _facts_lines: list[str] = []
    if _fed_bars_w:
        _fl = (f"📊 Probas taux Fed (Polymarket) · {_fed_bars_w.get('dominant')} "
               f"{_fed_bars_w.get('dominant_pct')}%")
        if _fed_bars_w.get("cut_pct") is not None:
            _fl += (f" (baisse {_fed_bars_w.get('cut_pct')}% · maintien "
                    f"{_fed_bars_w.get('hold_pct')}% · hausse "
                    f"{_fed_bars_w.get('hike_pct')}%)")
        _facts_lines.append(_fl)
    _dxy_ice_w = _yq_w.get("dxy_ice")
    if _dxy_ice_w is not None or _dxy_broad_w is not None:
        _parts_dxy = []
        if _dxy_ice_w is not None:
            _d7 = _macro_week_pct.get("dxy_ice") if _macro_week_pct else None
            _parts_dxy.append(
                f"DXY (ICE) {_dxy_ice_w:.2f}"
                + (f" ({_pct_fr_signed(_d7)} 7j)" if isinstance(_d7, (int, float)) else ""))
        if _dxy_broad_w is not None:
            _parts_dxy.append(f"indice élargi (Fed) {_dxy_broad_w:.2f}")
        _facts_lines.append("💵 Dollar · " + " · ".join(_parts_dxy))
    if etf.get("available"):
        _etf_parts = []
        _etf_btc = etf.get("btc") or {}
        _etf_eth = etf.get("eth") or {}
        if _etf_btc.get("total_flow_musd") is not None:
            _v = _etf_btc["total_flow_musd"]
            _etf_parts.append(
                f"BTC {'+' if _v >= 0 else '−'}${abs(_v):,.1f}M"
                + (f" ({_etf_btc.get('date')})" if _etf_btc.get("date") else ""))
        if _etf_eth.get("total_flow_musd") is not None:
            _v = _etf_eth["total_flow_musd"]
            _etf_parts.append(f"ETH {'+' if _v >= 0 else '−'}${abs(_v):,.1f}M")
        if _etf_parts:
            _facts_lines.append("🏦 Flux ETF · " + " · ".join(_etf_parts))
    if fng_w.get("available"):
        _fl_fg = f"😨 Fear & Greed · {fng_w.get('value')}"
        if fng_w.get("classification"):
            _fl_fg += f" ({fng_w.get('classification')})"
        if fng_w.get("value_7d_ago") is not None:
            _d7fg = fng_w.get("delta_7d") or 0
            _fl_fg += (f" · il y a 7 j : {fng_w.get('value_7d_ago')} "
                       f"({'+' if _d7fg >= 0 else '−'}{abs(_d7fg)} pts)")
        _facts_lines.append(_fl_fg)
    if _global_w.get("available"):
        _ms = data.get("market_structure") or {}
        _ms_parts = []
        if _ms.get("btc_dominance_pct") is not None:
            _ms_parts.append(f"dominance BTC {_ms['btc_dominance_pct']:.1f}%")
        if _ms.get("eth_dominance_pct") is not None:
            _ms_parts.append(f"ETH {_ms['eth_dominance_pct']:.1f}%")
        if _ms.get("eth_btc_ratio") is not None:
            _r = f"ETH/BTC {_ms['eth_btc_ratio']:.5f}"
            if _ms.get("eth_btc_7d_pct") is not None:
                _r += f" ({_pct_fr_signed(_ms['eth_btc_7d_pct'])} 7j)"
            _ms_parts.append(_r)
        if _ms.get("total_market_cap_usd"):
            _t = _ms["total_market_cap_usd"] / 1e12
            _mc = f"MCap ${_t:.2f}T"
            if _ms.get("market_cap_change_24h_pct") is not None:
                _mc += f" (24h {_pct_fr_signed(_ms['market_cap_change_24h_pct'])})"
            _ms_parts.append(_mc)
        if _ms_parts:
            _facts_lines.append("🌐 Structure · " + " · ".join(_ms_parts))
    if _derivs_w:
        _dv_parts = []
        _dv_btc = _derivs_w.get("BTC") or {}
        if _dv_btc.get("funding_annualized_pct") is not None:
            _dv_parts.append(
                f"funding BTC {_pct_fr_signed(_dv_btc['funding_annualized_pct'], 2)}/an")
        if _dv_btc.get("long_short_ratio") is not None:
            _dv_parts.append(f"L/S {_dv_btc['long_short_ratio']}")
        # Fundings EXTRÊMES des autres positions (|annualisé| ≥ 15%/an) :
        # signal de positionnement (shorts/longs en excès).
        for _s_dv, _d_dv in _derivs_w.items():
            _f_dv = _d_dv.get("funding_annualized_pct")
            if _s_dv != "BTC" and isinstance(_f_dv, (int, float)) and abs(_f_dv) >= 15:
                _dv_parts.append(f"{_s_dv} {_pct_fr_signed(_f_dv, 1)}/an")
        if _dv_parts:
            _facts_lines.append("⚙️ Dérivés · " + " · ".join(_dv_parts))
    if _facts_lines:
        payload["weekly_facts_lines"] = _facts_lines
    # v26 (W-B7) — digest news 7j (rendu + déjà injecté au prompt).
    if weekly_news_digest:
        payload["weekly_news_digest"] = weekly_news_digest
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
            def _is_agg(_s: dict[str, Any]) -> bool:
                return bool(_s.get("is_aggregate")) or str(
                    _s.get("sector", "")).startswith("Autres secteurs")

            _sorted_secs = sorted(
                _wk_secs, key=lambda s: s.get("ptf_pct") or 0, reverse=True
            )
            # v24 — le bucket agrégé « Autres secteurs » DOIT rester en dernier :
            # trié par poids, il s'intercalait avant des secteurs nommés plus petits
            # (audit : « Autres (4) » 7.0% affiché avant Oracle/Infra 4.0%).
            _sorted_secs = ([s for s in _sorted_secs if not _is_agg(s)]
                            + [s for s in _sorted_secs if _is_agg(s)])
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
        # v26 (W-A19) — « % » (comme le KPI vs BTC 7j du header), plus « pts ».
        _q_axes.append({"label": "Momentum vs BTC", "score": round(_mom, 1),
                        "detail": (f"{_vsbtc:+.1f}% vs BTC 7j"
                                   if _vsbtc is not None
                                   else "aligné sur BTC (donnée 7j manquante)")})
        _ddq = snap_w.get("drawdown_ath_pct")
        _ddq_used = _ddq if _ddq is not None else -50
        _sol = max(0.0, min(10.0, 10.0 + _ddq_used / 9.5))
        # v26 (W-A19) — 1 décimale (le bilan disait « −67.8% », l'axe « −68% »).
        _q_axes.append({"label": "Solidité (vs ATH)", "score": round(_sol, 1),
                        "detail": (f"drawdown pondéré {_ddq:.1f}% vs ATH"
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

    # ─────────────────────────────────────────────────────────────
    # v26 (W-B9) — « DEPUIS LE HEBDO PRÉCÉDENT » : diff déterministe vs le
    # snapshot de la semaine N-1 (valeur PTF, santé, F&G, DXY). Le bilan
    # raconte une ÉVOLUTION, pas un instantané. Absent la 1re semaine.
    # ─────────────────────────────────────────────────────────────
    try:
        _prev_wk = snapshots[-2] if len(snapshots) >= 2 else None
        _wow_lines: list[str] = []
        if _prev_wk:
            _pv_w = _prev_wk.get("value_usd")
            if isinstance(_pv_w, (int, float)) and _pv_w > 0:
                _wow_lines.append(
                    f"Valeur PTF {_fmt_usd_short(_pv_w)} → "
                    f"{_fmt_usd_short(current_value)} "
                    f"({_pct_fr_signed((current_value / _pv_w - 1) * 100)})")
            _pq_w = _prev_wk.get("quality_score")
            _q_now = (payload.get("ptf_quality_score") or {}).get("score")
            if isinstance(_pq_w, (int, float)) and isinstance(_q_now, (int, float)):
                _wow_lines.append(
                    f"Santé {str(_pq_w).replace('.', ',')} → "
                    f"{str(_q_now).replace('.', ',')}/10")
            _pfg_w = _prev_wk.get("fear_greed")
            _fg_now_w = (fng_w.get("value") if fng_w.get("available") else None)
            if isinstance(_pfg_w, (int, float)) and isinstance(_fg_now_w, (int, float)):
                _dfg_w = int(_fg_now_w) - int(_pfg_w)
                _wow_lines.append(
                    f"F&G {int(_pfg_w)} → {int(_fg_now_w)} "
                    f"({'+' if _dfg_w >= 0 else '−'}{abs(_dfg_w)} pts)")
            _pdxy_w = _prev_wk.get("dxy")
            if (isinstance(_pdxy_w, (int, float)) and isinstance(_dxy_broad_w, (int, float))
                    and _pdxy_w > 0):
                _wow_lines.append(
                    f"Indice dollar élargi {_pdxy_w:.1f} → {_dxy_broad_w:.1f} "
                    f"({_pct_fr_signed((_dxy_broad_w / _pdxy_w - 1) * 100)})")
        if _wow_lines:
            payload["week_over_week"] = {"available": True, "lines": _wow_lines}
    except Exception as _exc_wow:  # noqa: BLE001
        logger.info("Bloc WoW indisponible : %s", _exc_wow)

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
    # v26 (W-A12) — format ABSOLU homogène avec la ligne hebdo (le v25 mêlait
    # « demain 08h30 » relatif et « dimanche 5 juillet 2026, 12:00 » absolu).
    _nm_day = _now_w if (_now_w.hour + _now_w.minute / 60.0) < 8.5 else _now_w + _td(days=1)
    payload["footer"]["next_morning"] = (
        f"{_JOURS_FR[_nm_day.weekday()]} {_nm_day.day} "
        f"{_MOIS_FR[_nm_day.month - 1]}, 08:30"
    )
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # v27 \u2014 ANALYSE PROFONDE HEBDO : r\u00e9gime (ME1), Brier (ES4), auto-backtest
    # (ES5), verdict des appels de la semaine pass\u00e9e (ME2/ME3), indice de
    # confiance du mail (ME4), on-chain frais (SO4), zones de liquidation
    # (SO2), positionnement options (SO3). Tout best-effort, jamais bloquant.
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    _btc_px_w = (market.get("BTC") or {}).get("price")
    # ME1 \u2014 r\u00e9gime (s\u00e9rie BTC 300j pour la MM200).
    try:
        from src.analytics import market_regime as _mrw
        _reg_closes = coingecko.get_dated_closes("BTC", 300)
        if _reg_closes:
            _reg_series = [_reg_closes[d] for d in sorted(_reg_closes)]
            _reg_w = _mrw.with_persistence(
                _mrw.classify_regime(_reg_series),
                datetime.now(TZ).date().isoformat())
            if _reg_w.get("available"):
                payload["market_regime"] = _reg_w
    except Exception as _rexc:  # noqa: BLE001
        logger.info("R\u00e9gime hebdo indisponible : %s", _rexc)
    # ES4 \u2014 Brier score.
    _brier = tracker.compute_brier_score(90)
    if _brier.get("available"):
        payload["brier_score"] = _brier
    # ES5 \u2014 auto-backtest \u00ab achat sous MM50 \u00bb (s\u00e9rie BTC 180j d\u00e9j\u00e0 charg\u00e9e).
    try:
        from src.analytics import strategy_backtest as _sbt
        if _btc_closes_w:
            _bt = _sbt.compute_dip_buy_stats(_btc_closes_w)
            if _bt.get("available"):
                payload["strategy_backtest"] = _bt
    except Exception as _btexc:  # noqa: BLE001
        logger.info("Auto-backtest indisponible : %s", _btexc)
    # SO4 \u2014 on-chain BTC FRAIS (SOPR/NUPL/NVT), gratuit et sans cl\u00e9.
    try:
        from src.data_sources import bitcoin_data as _bd_w
        _extras = _bd_w.get_btc_onchain_extras()
        if _extras.get("available") and _extras.get("readings"):
            payload["onchain_extras"] = _extras
    except Exception as _oexc:  # noqa: BLE001
        logger.info("On-chain extras indisponibles : %s", _oexc)
    # SO2 \u2014 zones de liquidation ESTIM\u00c9ES (aimants de prix).
    try:
        from src.analytics import liquidation_zones as _lz
        _lzw = _lz.compute_liquidation_zones(
            _btc_px_w,
            funding_annualized_pct=(_derivs_w.get("BTC") or {}).get("funding_annualized_pct"),
            long_short_ratio=(_derivs_w.get("BTC") or {}).get("long_short_ratio"))
        if _lzw.get("available"):
            payload["liquidation_zones"] = _lzw
    except Exception as _lzexc:  # noqa: BLE001
        logger.info("Zones de liquidation indisponibles : %s", _lzexc)
    # SO3 \u2014 positionnement options (max pain / put-call) \u2192 rep\u00e8re chiffr\u00e9.
    try:
        _opt_w = deribit.get_options_metrics()
        _optb = (_opt_w.get("assets") or {}).get("BTC") if _opt_w.get("available") else None
        if _optb:
            _op_parts = []
            if _optb.get("max_pain"):
                _op_parts.append(
                    f"max pain {_fmt_usd_short(_optb['max_pain'])}"
                    + (f" ({_pct_fr_signed(_optb['max_pain_gap_pct'])})"
                       if _optb.get("max_pain_gap_pct") is not None else ""))
            if _optb.get("put_call_ratio") is not None:
                _op_parts.append(f"put/call {_optb['put_call_ratio']}")
            if _optb.get("dvol") is not None:
                _op_parts.append(f"DVOL {_optb['dvol']}%")
            if _op_parts:
                payload.setdefault("weekly_facts_lines", []).append(
                    "\ud83c\udfb2 Options BTC \u00b7 " + " \u00b7 ".join(_op_parts))
    except Exception as _opexc:  # noqa: BLE001
        logger.info("Options positioning indisponible : %s", _opexc)
    # on-chain extras \u2192 aussi en rep\u00e8re chiffr\u00e9 (le lecteur voit la lecture).
    if payload.get("onchain_extras", {}).get("readings"):
        payload.setdefault("weekly_facts_lines", []).append(
            "\u26d3 On-chain BTC \u00b7 " + " \u00b7 ".join(payload["onchain_extras"]["readings"]))
    # ME2/ME3 \u2014 VERDICT des appels de la semaine pass\u00e9e + sauvegarde des appels
    # de CETTE semaine (\u00e9valu\u00e9s au prochain hebdo).
    try:
        _prev_calls = mem.load_weekly_calls()
        _dom_scn = max(
            [s for s in (payload.get("scenarios") or []) if isinstance(s, dict)],
            key=lambda s: _parse_num(s.get("probability_pct")) or 0, default=None)
        _cur_calls = {
            "week_label": header.get("period_covered"),
            "dominant_scenario": (_dom_scn or {}).get("label"),
            "dominant_pct": (_dom_scn or {}).get("probability_pct"),
            "btc_price": _btc_px_w,
            "regime": (payload.get("market_regime") or {}).get("regime"),
            "fear_greed": (fng_w.get("value") if fng_w.get("available") else None),
        }
        _cr = _build_calls_review(_prev_calls, _btc_px_w,
                                  (fng_w.get("value") if fng_w.get("available") else None),
                                  (payload.get("market_regime") or {}))
        if _cr:
            payload["calls_review"] = _cr
        mem.save_weekly_calls(_cur_calls)
    except Exception as _crexc:  # noqa: BLE001
        logger.info("Revue des appels indisponible : %s", _crexc)
    # ME4 \u2014 INDICE DE CONFIANCE du mail (sources actives + fra\u00eecheur on-chain).
    try:
        _avg_src = ((payload.get("header") or {}).get("active_sources_count")
                    or _wk_active_pre)
        _tot_src = len(_ALL_SOURCES_LIST)
        _conf_pct = round(min(100, (_avg_src / _tot_src * 100))) if _tot_src else None
        if _conf_pct is not None:
            _grade = ("\u00e9lev\u00e9e" if _conf_pct >= 70 else
                      "correcte" if _conf_pct >= 50 else "partielle")
            _onc_fresh = (payload.get("onchain_extras", {}).get("as_of")
                          or data.get("onchain_as_of"))
            payload["mail_confidence"] = {
                "pct": _conf_pct, "grade": _grade,
                "sources": f"{_avg_src}/{_tot_src}",
                "onchain_as_of": _onc_fresh,
            }
    except Exception as _mcexc:  # noqa: BLE001
        logger.info("Indice de confiance indisponible : %s", _mcexc)

    mem.save_weekly_report(payload)
    # v23 \u2014 courbe d'\u00c9VOLUTION PTF (aire + ligne, base 100 vs BTC si s\u00e9rie align\u00e9e)
    # en image CID, \u00e0 la place des barres grises. D\u00e9gradation gracieuse : si le PNG
    # n'est pas g\u00e9n\u00e9r\u00e9 (matplotlib absent / < 3 points), le template retombe sur les
    # barres HTML.
    from src.reporting import charts as _charts
    _evo_png = _charts.portfolio_evolution_png(ptf_evolution, btc_points=_evo_btc)
    _wk_charts = {"ptf_evolution": _evo_png} if _evo_png else {}
    # v26 (W-B4) — graphiques hebdo supplémentaires, tous best-effort (une
    # panne matplotlib/donnée fait juste sauter l'image, jamais l'envoi) :
    # donut d'allocation sectorielle, classement barres perf 7j, sparkline
    # F&G 8j, BTC annoté des niveaux CALCULÉS (ancrage visuel des scénarios).
    try:
        _donut_png = _charts.sector_donut_png(payload.get("sector_exposure_cells") or [])
        if _donut_png:
            _wk_charts["sector_donut"] = _donut_png
        _bars_png = _charts.weekly_perf_bars_png(
            (payload.get("portfolio_heatmap_7d") or {}).get("cells") or [])
        if _bars_png:
            _wk_charts["perf_bars_7d"] = _bars_png
        if fng_w.get("available"):
            _fng_png = _charts.fng_sparkline_png(fng_w.get("history") or [])
            if _fng_png:
                _wk_charts["fng_sparkline"] = _fng_png
        _btc_lv = computed_levels_w.get("BTC") or {}
        if _btc_closes_w and _btc_lv:
            _btc_png = _charts.btc_levels_png(
                _btc_closes_w,
                supports=[lv.get("level") for lv in (_btc_lv.get("supports") or [])],
                resistances=[lv.get("level") for lv in (_btc_lv.get("resistances") or [])],
                price=_btc_lv.get("price"))
            if _btc_png:
                _wk_charts["btc_levels"] = _btc_png
    except Exception as _exc_wcharts:  # noqa: BLE001
        logger.info("Graphiques hebdo v26 partiellement indisponibles : %s", _exc_wcharts)
    # v27 (GR1/GR2/AF6) — heatmap de corrélation des positions, courbe de
    # funding BTC (~14 j) + OI, jauge de santé PTF. Tous best-effort.
    try:
        _corr_png = _charts.correlation_heatmap_png(weekly_price_series, weekly_positions)
        if _corr_png:
            _wk_charts["corr_heatmap"] = _corr_png
        _fh = binance_futures.get_funding_history("BTC", days=14)
        if _fh.get("available"):
            _oih = binance_futures.get_oi_history("BTC")
            _oi_pts = ([p.get("oi") for p in _oih.get("points", [])]
                       if _oih.get("available") else None)
            _fund_png = _charts.funding_history_png(
                _fh.get("annualized_series") or [], symbol="BTC", oi_points=_oi_pts)
            if _fund_png:
                _wk_charts["funding_hist"] = _fund_png
        _q_sc = (payload.get("ptf_quality_score") or {}).get("score")
        if _q_sc is not None:
            _gauge_png = _charts.gauge_png(
                _q_sc, vmin=0, vmax=10, label="Santé du portefeuille",
                value_label=f"{_q_sc}/10")
            if _gauge_png:
                _wk_charts["health_gauge"] = _gauge_png
    except Exception as _exc_v27ch:  # noqa: BLE001
        logger.info("Graphiques hebdo v27 partiellement indisponibles : %s", _exc_v27ch)
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
