"""Détection de patterns chartistes simples à partir d'OHLCV Binance.

Patterns détectés (heuristiques légères, non exhaustives) :
- breakout haussier / cassure baissière de range,
- volume spike,
- tendance courte (suite de clôtures haussières/baissières).

Ces signaux sont indicatifs et destinés à enrichir le contexte passé à Gemini,
pas à déclencher des décisions automatiques.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data_sources.binance import get_klines
from src.utils.logger import get_logger

logger = get_logger(__name__)


def detect_patterns(symbol: str) -> dict[str, Any]:
    """Analyse les bougies récentes d'un symbole et détecte des patterns.

    Args:
        symbol: ticker du portfolio.

    Returns:
        Dict ``{available, patterns: [str], volume_spike, trend}``.
    """
    klines = get_klines(symbol, interval="4h", limit=42)  # ~7 jours
    if not klines or len(klines) < 10:
        return {"available": False, "patterns": [], "volume_spike": False, "trend": None}

    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]

    patterns: list[str] = []

    # Range des N-1 dernières bougies (hors dernière).
    recent_high = max(highs[:-1])
    recent_low = min(lows[:-1])
    last_close = closes[-1]
    if last_close > recent_high:
        patterns.append("breakout_haussier")
    elif last_close < recent_low:
        patterns.append("cassure_baissiere")

    # Volume spike : dernière bougie > 2x la moyenne des précédentes.
    avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
    volume_spike = avg_vol > 0 and volumes[-1] > 2 * avg_vol
    if volume_spike:
        patterns.append("volume_spike")

    trend = _short_trend(closes[-6:])
    if trend:
        patterns.append(f"tendance_{trend}")

    return {
        "available": True,
        "patterns": patterns,
        "volume_spike": volume_spike,
        "trend": trend,
    }


def _short_trend(closes: list[float]) -> Optional[str]:
    """Détermine une tendance courte : 'haussiere', 'baissiere' ou None."""
    if len(closes) < 3:
        return None
    ups = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    downs = len(closes) - 1 - ups
    if ups >= len(closes) - 1:
        return "haussiere"
    if downs >= len(closes) - 1:
        return "baissiere"
    return None
