"""Indicateurs techniques avances : Fibonacci, Bollinger Bands, Supports/Resistances."""

from __future__ import annotations
from typing import Any, Optional
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def _get_klines(symbol: str, interval: str = "1d", limit: int = 90) -> list:
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
    data = get_json(BINANCE_KLINES_URL, params=params)
    if not data:
        return []
    return data


def _compute_fibonacci(high: float, low: float) -> dict[str, float]:
    diff = high - low
    return {
        "level_0": high,
        "level_236": high - 0.236 * diff,
        "level_382": high - 0.382 * diff,
        "level_500": high - 0.500 * diff,
        "level_618": high - 0.618 * diff,
        "level_786": high - 0.786 * diff,
        "level_100": low,
    }


def _compute_bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0) -> dict[str, Any]:
    if len(closes) < period:
        return {"available": False}
    recent = closes[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std = variance ** 0.5
    return {
        "available": True,
        "upper": round(sma + std_mult * std, 6),
        "middle": round(sma, 6),
        "lower": round(sma - std_mult * std, 6),
        "width": round((std_mult * std * 2) / sma * 100, 2),
        "position": "upper" if closes[-1] > sma + std_mult * std * 0.8 else "lower" if closes[-1] < sma - std_mult * std * 0.8 else "middle",
    }


def _find_support_resistance(highs: list[float], lows: list[float], closes: list[float]) -> dict[str, Any]:
    if len(closes) < 20:
        return {"available": False}
    recent_highs = sorted(highs[-30:], reverse=True)[:3]
    recent_lows = sorted(lows[-30:])[:3]
    resistance = round(sum(recent_highs) / len(recent_highs), 6)
    support = round(sum(recent_lows) / len(recent_lows), 6)
    current = closes[-1]
    return {
        "available": True,
        "resistance": resistance,
        "support": support,
        "distance_to_resistance_pct": round((resistance - current) / current * 100, 2),
        "distance_to_support_pct": round((current - support) / current * 100, 2),
    }


def get_technical_advanced(symbol: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            klines = _get_klines(symbol, "1d", 90)
            if not klines or len(klines) < 20:
                return {"available": False}
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            high_90d = max(highs)
            low_90d = min(lows)
            fib = _compute_fibonacci(high_90d, low_90d)
            boll = _compute_bollinger(closes)
            sr = _find_support_resistance(highs, lows, closes)
            return {
                "available": True,
                "fibonacci": fib,
                "bollinger": boll,
                "support_resistance": sr,
                "current_price": closes[-1],
                "high_90d": high_90d,
                "low_90d": low_90d,
            }
        except Exception as exc:
            logger.warning("Technical advanced %s indisponible : %s", symbol, exc)
            return {"available": False}
    return CACHE.get_or_compute(f"tech_adv_{symbol}", 1800, _fetch)
