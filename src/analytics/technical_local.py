"""Indicateurs techniques calculés LOCALEMENT depuis une série de clôtures.

Filet de sécurité (P0 #57) : quand TradingView est indisponible (IP datacenter,
lib absente, paire manquante), la dimension technique ne doit pas s'effondrer.
Ces fonctions recalculent RSI, MACD et Bollinger à partir de l'OHLC CoinGecko
DÉJÀ chargé (aucun appel réseau supplémentaire), et détectent une VRAIE
divergence prix/RSI — le signal poids 2 ``bullish_divergence`` de thesis_scoring
était jusque-là MORT faute de producteur (P0 #56).

Tout dégrade proprement : si la série est trop courte, la métrique vaut ``None``.
Aucune dépendance externe (pas de numpy) — calcul Python pur et déterministe.
"""

from __future__ import annotations

from typing import Any, Optional


def _ema(values: list[float], period: int) -> list[float]:
    """EMA simple, amorcée sur la première valeur (suffisant pour un repli)."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi_series(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """Série RSI (Wilder) alignée sur ``closes`` (None tant que pas assez de données)."""
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n < period + 1:
        return out

    def _rsi_val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_val(avg_gain, avg_loss)
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
        out[i] = _rsi_val(avg_gain, avg_loss)
    return out


def compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Dernière valeur RSI (Wilder) ou None si série trop courte."""
    ser = _rsi_series(closes, period)
    return round(ser[-1], 1) if ser and ser[-1] is not None else None


def compute_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Optional[float]:
    """Histogramme MACD (macd - signal) sur la dernière barre, ou None."""
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema(macd_line, signal)
    return round(macd_line[-1] - signal_line[-1], 6)


def compute_bollinger(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> Optional[dict[str, Any]]:
    """Bandes de Bollinger : position (lower/upper/mid) + largeur relative."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    if sma <= 0:
        return None
    var = sum((c - sma) ** 2 for c in window) / period
    sd = var ** 0.5
    upper = sma + mult * sd
    lower = sma - mult * sd
    price = closes[-1]
    if price <= lower:
        position = "lower"
    elif price >= upper:
        position = "upper"
    else:
        position = "mid"
    return {
        "position": position,
        "width_pct": round((upper - lower) / sma * 100, 2),
        "sma": round(sma, 6),
        "upper": round(upper, 6),
        "lower": round(lower, 6),
    }


def compute_stochastic(closes: list[float], period: int = 14) -> Optional[float]:
    """%K stochastique (basé clôtures) : position du prix dans son range récent."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    lo, hi = min(window), max(window)
    if hi == lo:
        return None
    return round((closes[-1] - lo) / (hi - lo) * 100, 1)


def compute_atr_pct(closes: list[float], period: int = 14) -> Optional[float]:
    """ATR approché (clôture-à-clôture) en % du prix : volatilité pour le sizing
    du stop. ATR vrai (high/low) vient de TradingView quand dispo ; ceci est le
    repli local sur la seule série de clôtures."""
    if len(closes) < period + 1:
        return None
    trs = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes))]
    atr = sum(trs) / len(trs)
    last = closes[-1]
    return round(atr / last * 100, 2) if last else None


def _pivot_lows(closes: list[float], span: int = 2) -> list[int]:
    return [
        i for i in range(span, len(closes) - span)
        if closes[i] == min(closes[i - span:i + span + 1])
    ]


def _pivot_highs(closes: list[float], span: int = 2) -> list[int]:
    return [
        i for i in range(span, len(closes) - span)
        if closes[i] == max(closes[i - span:i + span + 1])
    ]


def detect_divergence(
    closes: list[float], rsi_ser: list[Optional[float]], lookback: int = 30
) -> tuple[bool, bool]:
    """Vraie divergence prix/RSI sur les 2 derniers pivots de la fenêtre.

    Haussière : le prix fait un PLUS BAS plus bas mais le RSI un plus bas PLUS HAUT.
    Baissière : le prix fait un PLUS HAUT plus haut mais le RSI un plus haut PLUS BAS.
    """
    n = len(closes)
    start = max(0, n - lookback)
    lows = [i for i in _pivot_lows(closes) if i >= start and rsi_ser[i] is not None]
    highs = [i for i in _pivot_highs(closes) if i >= start and rsi_ser[i] is not None]
    bullish = False
    bearish = False
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if closes[b] < closes[a] and rsi_ser[b] > rsi_ser[a]:  # type: ignore[operator]
            bullish = True
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if closes[b] > closes[a] and rsi_ser[b] < rsi_ser[a]:  # type: ignore[operator]
            bearish = True
    return bullish, bearish


def compute_local_technical(closes: list[float]) -> dict[str, Any]:
    """Bloc technique local complet depuis une série de clôtures chronologiques.

    Args:
        closes: clôtures dans l'ordre chronologique (CoinGecko, déjà chargées).

    Returns:
        Dict ``{available, source, rsi, macd_hist, bollinger, bullish_divergence,
        bearish_divergence}``. ``available=False`` si la série est trop courte.
    """
    series = [float(c) for c in (closes or []) if isinstance(c, (int, float))]
    if len(series) < 15:
        return {"available": False, "reason": "série de clôtures trop courte (<15)"}
    rsi_ser = _rsi_series(series, 14)
    rsi = rsi_ser[-1]
    bullish_div, bearish_div = detect_divergence(series, rsi_ser)
    return {
        "available": True,
        "source": "local (OHLC CoinGecko)",
        "rsi": round(rsi, 1) if rsi is not None else None,
        "macd_hist": compute_macd(series),
        "bollinger": compute_bollinger(series),
        "stochastic_k": compute_stochastic(series),
        "atr_pct": compute_atr_pct(series),
        "bullish_divergence": bullish_div,
        "bearish_divergence": bearish_div,
    }


def local_tech_score(local: dict[str, Any]) -> Optional[float]:
    """Score technique 0-100 (même convention que TradingView : haut = haussier).

    Sert de REPLI au ``technical_multi_tf`` du score composite quand TradingView
    est indisponible. Combine momentum RSI, signe du MACD et position Bollinger.
    """
    if not local or not local.get("available"):
        return None
    score = 50.0
    contributed = False
    rsi = local.get("rsi")
    if isinstance(rsi, (int, float)):
        score += (rsi - 50.0) * 0.5
        contributed = True
    mh = local.get("macd_hist")
    if isinstance(mh, (int, float)):
        score += 8.0 if mh > 0 else -8.0
        contributed = True
    position = (local.get("bollinger") or {}).get("position")
    if position == "lower":
        score += 5.0
        contributed = True
    elif position == "upper":
        score -= 5.0
        contributed = True
    if not contributed:
        return None
    return max(0.0, min(100.0, round(score, 1)))
