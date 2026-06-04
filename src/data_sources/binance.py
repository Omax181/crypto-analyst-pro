"""Source Binance API publique : OHLCV (klines) haute résolution.

Aucune authentification requise. Sert à la détection de patterns et au calcul
des spikes intra-day.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BINANCE_SYMBOLS: dict[str, str] = _SOURCES["binance_symbols"]
_BASE = _SOURCES["endpoints"]["binance"]


def get_klines(
    symbol: str, interval: str = "1h", limit: int = 24
) -> Optional[list[dict[str, float]]]:
    """Récupère les bougies OHLCV pour un symbole.

    Args:
        symbol: ticker du portfolio (ex. ``"BTC"``).
        interval: intervalle Binance (``"1m"``, ``"15m"``, ``"1h"``, ``"4h"``...).
        limit: nombre de bougies.

    Returns:
        Liste de dicts ``{open_time, open, high, low, close, volume}`` ou
        ``None`` si indisponible.
    """
    exch_symbol = _BINANCE_SYMBOLS.get(symbol)
    if not exch_symbol:
        return None

    def _fetch() -> Optional[list[Any]]:
        return get_json(
            f"{_BASE}/klines",
            params={"symbol": exch_symbol, "interval": interval, "limit": limit},
        )

    raw = CACHE.get_or_compute(f"binance:{exch_symbol}:{interval}:{limit}", 300, _fetch)
    if not isinstance(raw, list):
        return None
    return [
        {
            "open_time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]


def short_window_change(symbol: str, window_hours: int = 4) -> Optional[float]:
    """Variation de prix (%) sur une fenêtre courte, pour alertes intra-day.

    Args:
        symbol: ticker.
        window_hours: taille de la fenêtre.

    Returns:
        Variation en pourcentage entre le début et la fin de fenêtre, ou
        ``None`` si données indisponibles.
    """
    klines = get_klines(symbol, interval="1h", limit=window_hours + 1)
    if not klines or len(klines) < 2:
        return None
    start = klines[0]["open"]
    end = klines[-1]["close"]
    if start == 0:
        return None
    return (end - start) / start * 100
