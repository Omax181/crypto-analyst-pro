"""Indicateurs techniques avancés : Fibonacci, Bollinger, supports/résistances.

Source OHLC : CoinGecko (Binance est géo-bloqué depuis GitHub Actions, erreur 451).
"""
from __future__ import annotations
from typing import Any
from src.data_sources import coingecko
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)


def _compute_fibonacci(high: float, low: float) -> dict[str, float]:
    diff = high - low
    return {
        "level_0": round(high, 6), "level_236": round(high - 0.236 * diff, 6),
        "level_382": round(high - 0.382 * diff, 6), "level_500": round(high - 0.5 * diff, 6),
        "level_618": round(high - 0.618 * diff, 6), "level_786": round(high - 0.786 * diff, 6),
        "level_100": round(low, 6),
    }


def _compute_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0) -> dict[str, Any]:
    if len(closes) < period:
        return {"available": False}
    recent = closes[-period:]
    sma = sum(recent) / period
    std = (sum((x - sma) ** 2 for x in recent) / period) ** 0.5
    upper, lower = sma + std_mult * std, sma - std_mult * std
    last = closes[-1]
    pos = "upper" if last > upper * 0.99 else "lower" if last < lower * 1.01 else "middle"
    return {
        "available": True, "upper": round(upper, 6), "middle": round(sma, 6),
        "lower": round(lower, 6), "width_pct": round((upper - lower) / sma * 100, 2) if sma else None,
        "position": pos,
    }


def _support_resistance(highs: list[float], lows: list[float], current: float) -> dict[str, Any]:
    if len(highs) < 10:
        return {"available": False}
    resistance = round(sum(sorted(highs[-30:], reverse=True)[:3]) / 3, 6)
    support = round(sum(sorted(lows[-30:])[:3]) / 3, 6)
    return {
        "available": True, "resistance": resistance, "support": support,
        "dist_to_resistance_pct": round((resistance - current) / current * 100, 2) if current else None,
        "dist_to_support_pct": round((current - support) / current * 100, 2) if current else None,
    }


def get_technical_advanced(symbol: str) -> dict[str, Any]:
    """Fibonacci + Bollinger + supports/résistances sur 90j (CoinGecko OHLC)."""
    def _fetch() -> dict[str, Any]:
        ohlc = coingecko.get_ohlc(symbol, days=90)
        if not ohlc or len(ohlc) < 10:
            return {"available": False}
        highs = [c["high"] for c in ohlc]
        lows = [c["low"] for c in ohlc]
        closes = [c["close"] for c in ohlc]
        current = closes[-1]
        return {
            "available": True,
            "fibonacci": _compute_fibonacci(max(highs), min(lows)),
            "bollinger": _compute_bollinger(closes),
            "support_resistance": _support_resistance(highs, lows, current),
            "current_price": current,
            "high_90d": round(max(highs), 6), "low_90d": round(min(lows), 6),
        }
    return CACHE.get_or_compute(f"tech_adv:{symbol}", 1800, _fetch)
