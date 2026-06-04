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


def _sma(closes: list[float], period: int) -> float | None:
    """Moyenne mobile simple sur ``period`` jours (None si historique court)."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 6)


def _moving_averages(closes: list[float], current: float) -> dict[str, Any]:
    """SMA 50/100/200 + détection golden/death cross + position du prix.

    Le croisement SMA50 vs SMA200 est le signal de tendance de fond le plus
    suivi : golden cross (50 repasse au-dessus de 200) = signal haussier
    majeur ; death cross = baissier. On renvoie aussi la distance du prix à
    chaque MA (prix > SMA200 = tendance long terme haussière).
    """
    sma50, sma100, sma200 = _sma(closes, 50), _sma(closes, 100), _sma(closes, 200)
    out: dict[str, Any] = {
        "sma50": sma50, "sma100": sma100, "sma200": sma200,
    }
    if sma50 and sma200:
        out["cross"] = "golden" if sma50 > sma200 else "death"
        out["sma50_vs_sma200_pct"] = round((sma50 - sma200) / sma200 * 100, 2)
    if current and sma200:
        out["price_vs_sma200_pct"] = round((current - sma200) / sma200 * 100, 2)
    if current and sma50:
        out["price_vs_sma50_pct"] = round((current - sma50) / sma50 * 100, 2)
    return out


def _flash_signals(
    bollinger: dict[str, Any], sr: dict[str, Any], mas: dict[str, Any]
) -> list[str]:
    """Liste compacte des signaux techniques « qui flashent » (bull/bear).

    Synthétise les conditions notables (extrêmes Bollinger, proximité
    support/résistance, tendance des moyennes) en puces courtes prêtes pour
    le prompt. Vide si rien de notable.
    """
    flags: list[str] = []
    pos = bollinger.get("position") if bollinger.get("available") else None
    if pos == "lower":
        flags.append("🟢 prix sur bande Bollinger basse (survente potentielle)")
    elif pos == "upper":
        flags.append("🔴 prix sur bande Bollinger haute (surchauffe potentielle)")
    if sr.get("available"):
        dr = sr.get("dist_to_resistance_pct")
        ds = sr.get("dist_to_support_pct")
        if dr is not None and dr <= 3:
            flags.append(f"🔴 proche résistance (+{dr}%)")
        if ds is not None and ds <= 3:
            flags.append(f"🟢 proche support (−{ds}%)")
    cross = mas.get("cross")
    pv200 = mas.get("price_vs_sma200_pct")
    if cross == "golden":
        flags.append("🟢 golden cross actif (SMA50 > SMA200)")
    elif cross == "death":
        flags.append("🔴 death cross actif (SMA50 < SMA200)")
    if pv200 is not None:
        if pv200 > 0:
            flags.append(f"🟢 au-dessus SMA200 (+{pv200}%, tendance LT haussière)")
        else:
            flags.append(f"🔴 sous SMA200 ({pv200}%, tendance LT baissière)")
    return flags


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
        bollinger = _compute_bollinger(closes)
        sr = _support_resistance(highs, lows, current)
        mas = _moving_averages(closes, current)
        return {
            "available": True,
            "fibonacci": _compute_fibonacci(max(highs), min(lows)),
            "bollinger": bollinger,
            "support_resistance": sr,
            "moving_averages": mas,
            "flash_signals": _flash_signals(bollinger, sr, mas),
            "current_price": current,
            "high_90d": round(max(highs), 6), "low_90d": round(min(lows), 6),
        }
    return CACHE.get_or_compute(f"tech_adv:{symbol}", 1800, _fetch)
