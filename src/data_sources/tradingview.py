"""Source TradingView TA (tradingview-ta) : recos agrégées multi-timeframe.

Récupère pour chaque crypto la recommandation TradingView (STRONG_BUY..
STRONG_SELL) et quelques indicateurs (RSI, MACD) sur plusieurs timeframes.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_CACHE_TTL = load_config("thresholds").get("cache_ttl", {})
_BINANCE_SYMBOLS: dict[str, str] = _SOURCES["binance_symbols"]

# Map nom de timeframe -> Interval tradingview-ta (import paresseux).
_TF_MAP = {"1h": "1h", "4h": "4h", "1d": "1d", "1w": "1W"}


def _get_interval(tf_lib: str) -> Any:
    """Retourne l'enum Interval de tradingview-ta pour un timeframe donné."""
    from tradingview_ta import Interval

    mapping = {
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1d": Interval.INTERVAL_1_DAY,
        "1W": Interval.INTERVAL_1_WEEK,
    }
    return mapping[tf_lib]


def get_technical(symbol: str) -> dict[str, Any]:
    """Récupère les signaux techniques multi-TF pour un symbole.

    Args:
        symbol: ticker du portfolio (ex. ``"BTC"``).

    Returns:
        Dict ``{available, signals: {tf: {recommendation, rsi, macd_hist}}}``.
        ``available=False`` si le symbole n'a pas de paire Binance configurée
        ou si TradingView ne répond pas.
    """
    exch_symbol = _BINANCE_SYMBOLS.get(symbol)
    if not exch_symbol:
        return {"available": False, "reason": "pas de paire Binance configurée"}

    def _fetch() -> dict[str, Any]:
        try:
            from tradingview_ta import TA_Handler
        except ImportError as exc:
            logger.warning("tradingview_ta indisponible : %s", exc)
            return {"available": False, "reason": "tradingview_ta non installé"}

        signals: dict[str, Any] = {}
        for tf_name, tf_lib in _TF_MAP.items():
            try:
                handler = TA_Handler(
                    symbol=exch_symbol,
                    screener="crypto",
                    exchange="BINANCE",
                    interval=_get_interval(tf_lib),
                )
                analysis = handler.get_analysis()
                ind = analysis.indicators
                signals[tf_name] = {
                    "recommendation": analysis.summary.get("RECOMMENDATION"),
                    "buy": analysis.summary.get("BUY"),
                    "sell": analysis.summary.get("SELL"),
                    "neutral": analysis.summary.get("NEUTRAL"),
                    "rsi": ind.get("RSI"),
                    "macd_hist": ind.get("MACD.macd", 0) - ind.get("MACD.signal", 0)
                    if ind.get("MACD.macd") is not None
                    else None,
                    "close": ind.get("close"),
                }
            except Exception as exc:  # noqa: BLE001
                logger.debug("TV-TA %s %s indisponible : %s", exch_symbol, tf_name, exc)
        return {"available": bool(signals), "signals": signals}

    ttl = _CACHE_TTL.get("technical", 600)
    return CACHE.get_or_compute(f"tv:{exch_symbol}", ttl, _fetch)


def detect_rsi_divergence(technical: dict[str, Any]) -> Optional[str]:
    """Heuristique simple de divergence RSI sur le daily.

    Returns:
        ``"oversold"`` si RSI 1d < 30, ``"overbought"`` si > 70, sinon ``None``.
    """
    daily = technical.get("signals", {}).get("1d", {})
    rsi = daily.get("rsi")
    if rsi is None:
        return None
    if rsi < 30:
        return "oversold"
    if rsi > 70:
        return "overbought"
    return None
