"""Source de données CoinGecko (source primaire prix & marché global).

Free tier : ~30 calls/min avec clé. La clé est optionnelle ; sans clé,
l'endpoint public fonctionne mais avec une limite plus basse.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_CACHE_TTL = load_config("thresholds").get("cache_ttl", {})
_CG_IDS: dict[str, str] = _SOURCES["coingecko_ids"]


def _base_and_headers() -> tuple[str, dict[str, str]]:
    """Retourne (base_url, headers) selon présence d'une clé pro/demo."""
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    endpoints = _SOURCES["endpoints"]
    if key:
        # Les clés "demo" gratuites passent par l'API publique avec header dédié.
        return endpoints["coingecko"], {"x-cg-demo-api-key": key}
    return endpoints["coingecko"], {}


def get_market_data(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Récupère prix, MC, volume, variations 24h pour une liste de symboles.

    Args:
        symbols: tickers du portfolio (ex. ``["BTC", "ETH"]``).

    Returns:
        Dict ``{symbol: {price, market_cap, volume_24h, change_24h, ath,
        atl, change_from_ath_pct}}``. Symboles non résolus omis.
    """
    ids = [_CG_IDS[s] for s in symbols if s in _CG_IDS]
    if not ids:
        return {}
    id_to_symbol = {v: k for k, v in _CG_IDS.items()}

    base, headers = _base_and_headers()
    cache_key = "cg:markets:" + ",".join(sorted(ids))

    def _fetch() -> Optional[list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        # /coins/markets paginé par lots de 250 (large marge ici).
        for i in range(0, len(ids), 250):
            chunk = ids[i : i + 250]
            data = get_json(
                f"{base}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(chunk),
                    "price_change_percentage": "24h,7d,30d",
                },
                headers=headers,
            )
            if isinstance(data, list):
                out.extend(data)
        return out

    raw = CACHE.get_or_compute(cache_key, _CACHE_TTL.get("prices", 300), _fetch)
    result: dict[str, dict[str, Any]] = {}
    for coin in raw or []:
        sym = id_to_symbol.get(coin.get("id", ""))
        if not sym:
            continue
        ath = coin.get("ath") or 0
        price = coin.get("current_price") or 0
        change_from_ath = ((price - ath) / ath * 100) if ath else None
        result[sym] = {
            "price": price,
            "market_cap": coin.get("market_cap"),
            "volume_24h": coin.get("total_volume"),
            "change_24h": coin.get("price_change_percentage_24h_in_currency") or coin.get("price_change_percentage_24h"),
            "change_7d": coin.get("price_change_percentage_7d_in_currency"),
            "change_30d": coin.get("price_change_percentage_30d_in_currency"),
            "ath": ath,
            "atl": coin.get("atl"),
            "change_from_ath_pct": change_from_ath,
            # v22 (#4/#5) — VALORISATION & DILUTION : déjà présents dans la réponse
            # /coins/markets, jusque-là non extraits. Permettent FDV/MC, % en
            # circulation et dilution restante (pression d'émission structurelle).
            "fully_diluted_valuation": coin.get("fully_diluted_valuation"),
            "circulating_supply": coin.get("circulating_supply"),
            "total_supply": coin.get("total_supply"),
            "max_supply": coin.get("max_supply"),
        }
    logger.info("CoinGecko : %d/%d symboles résolus.", len(result), len(symbols))
    return result


def get_global() -> dict[str, Any]:
    """Récupère les métriques globales : BTC dominance, total MC, total vol.

    Returns:
        Dict ``{available, total_market_cap_usd, total_volume_usd,
        btc_dominance_pct, market_cap_change_24h_pct}``.
    """
    base, headers = _base_and_headers()

    def _fetch() -> Optional[dict[str, Any]]:
        return get_json(f"{base}/global", headers=headers)

    raw = CACHE.get_or_compute("cg:global", 300, _fetch)
    if not raw or "data" not in raw:
        return {"available": False}
    d = raw["data"]
    return {
        "available": True,
        "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
        "total_volume_usd": d.get("total_volume", {}).get("usd"),
        "btc_dominance_pct": d.get("market_cap_percentage", {}).get("btc"),
        "eth_dominance_pct": d.get("market_cap_percentage", {}).get("eth"),
        "market_cap_change_24h_pct": d.get("market_cap_change_percentage_24h_usd"),
    }


def get_categories() -> dict[str, Any]:
    """OB6 — catégories/narratifs CoinGecko (gratuit, sans clé requise).

    Sert de source de DÉTECTION DE NARRATIFS (remplace Kaito, mort en prod faute
    de clé). Renvoie le brut minimal ; le filtrage/classement (bruit micro-cap,
    exclusion des écosystèmes de chaînes) est fait dans ``analytics/narratives``.

    Returns:
        Dict ``{available, categories: [{name, market_cap, change_24h,
        volume_24h, top_coins}]}``.
    """
    base, headers = _base_and_headers()

    def _fetch() -> Optional[list[Any]]:
        return get_json(f"{base}/coins/categories", headers=headers)

    raw = CACHE.get_or_compute("cg:categories", 1800, _fetch)
    if not isinstance(raw, list) or not raw:
        return {"available": False, "categories": []}
    cats: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        try:
            name = c.get("name")
            mcap = c.get("market_cap")
            chg = c.get("market_cap_change_24h")
            if not name or mcap is None or chg is None:
                continue
            cats.append({
                "name": str(name),
                "market_cap": float(mcap),
                "change_24h": round(float(chg), 2),
                "volume_24h": float(c.get("volume_24h") or 0.0),
                "top_coins": [str(x) for x in (c.get("top_3_coins_id") or []) if x][:3],
            })
        except (TypeError, ValueError):
            continue
    if not cats:
        return {"available": False, "categories": []}
    return {"available": True, "categories": cats}


def get_ohlc(symbol: str, days: int = 90) -> Optional[list[dict[str, float]]]:
    """Récupère les bougies OHLC via CoinGecko (remplace Binance, non géo-bloqué).

    Args:
        symbol: ticker du portfolio (ex. ``"BTC"``).
        days: profondeur d'historique (1/7/14/30/90/180/365).

    Returns:
        Liste de dicts ``{open, high, low, close}`` (granularité ~4j pour
        days>=31, ~4h pour 3-30j) ou ``None`` si indisponible.
    """
    cg_id = _CG_IDS.get(symbol)
    if not cg_id:
        return None
    base, headers = _base_and_headers()

    def _fetch() -> Optional[list[Any]]:
        return get_json(
            f"{base}/coins/{cg_id}/ohlc",
            params={"vs_currency": "usd", "days": days},
            headers=headers,
        )

    raw = CACHE.get_or_compute(f"cg:ohlc:{cg_id}:{days}", 1800, _fetch)
    if not isinstance(raw, list) or not raw:
        return None
    return [
        {"open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])}
        for c in raw
        if len(c) >= 5
    ]


def get_price_volume_series(
    symbol: str, days: int = 30, interval: Optional[str] = "daily"
) -> Optional[dict[str, list[float]]]:
    """Récupère les séries prix et volume (pour les anomalies de volume).

    Args:
        symbol: ticker.
        days: nombre de jours.
        interval: granularité CoinGecko (``"daily"`` par défaut). Si ``None``,
            le paramètre est OMIS de la requête : CoinGecko choisit alors
            automatiquement le pas le plus fin disponible.

    Returns:
        Dict ``{closes: [...], volumes: [...], prices: [...]}`` ou ``None``.
        ``prices`` est un ALIAS de ``closes`` (bug v14 : main.py lisait
        ``series["prices"]`` qui n'existait pas -> corrélation des positions
        jamais calculée ; l'alias rend les deux clés équivalentes).
    """
    cg_id = _CG_IDS.get(symbol)
    if not cg_id:
        return None
    base, headers = _base_and_headers()

    params: dict[str, Any] = {"vs_currency": "usd", "days": days}
    if interval is not None:
        params["interval"] = interval

    def _fetch() -> Optional[dict[str, Any]]:
        return get_json(
            f"{base}/coins/{cg_id}/market_chart",
            params=params,
            headers=headers,
        )

    cache_key = f"cg:chart:{cg_id}:{days}:{interval or 'auto'}"
    raw = CACHE.get_or_compute(cache_key, 1800, _fetch)
    if not isinstance(raw, dict):
        return None
    prices = [p[1] for p in raw.get("prices", []) if len(p) >= 2]
    volumes = [v[1] for v in raw.get("total_volumes", []) if len(v) >= 2]
    if not prices:
        return None
    return {"closes": prices, "volumes": volumes, "prices": prices}


def get_dated_closes(symbol: str, days: int = 35) -> dict[str, float]:
    """Récupère les clôtures quotidiennes datées (pour corrélations macro).

    Contrairement à ``get_price_volume_series`` (liste sans dates), renvoie un
    mapping ``{YYYY-MM-DD (UTC): close}`` permettant l'alignement par date avec
    des séries macro (FRED, jours ouvrés uniquement).

    Args:
        symbol: ticker du portfolio.
        days: profondeur en jours.

    Returns:
        Dict ``{date_str: close}`` (vide si indisponible).
    """
    from datetime import datetime, timezone

    cg_id = _CG_IDS.get(symbol)
    if not cg_id:
        return {}
    base, headers = _base_and_headers()

    def _fetch() -> Optional[dict[str, Any]]:
        return get_json(
            f"{base}/coins/{cg_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
            headers=headers,
        )

    raw = CACHE.get_or_compute(f"cg:dated:{cg_id}:{days}", 1800, _fetch)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for point in raw.get("prices", []):
        if len(point) < 2:
            continue
        ts_ms, price = point[0], point[1]
        try:
            day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
            out[day] = float(price)
        except (TypeError, ValueError, OSError):
            continue
    return out
