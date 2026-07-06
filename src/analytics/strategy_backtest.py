"""Auto-backtest léger de la stratégie maison (v27 · ES5).

Teste sur l'historique récent la règle cœur du profil d'Omar — « accumuler
sur repli sous la MM50 » — et publie le hit-rate + retour médian à 7 et 30
jours. HONNÊTE sur ses limites : petit échantillon, pas de frais, pas un
backtest institutionnel — un simple thermomètre : « cette règle a-t-elle
payé récemment sur cet actif ? ».
"""

from __future__ import annotations

from statistics import median
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _sma(closes: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def compute_dip_buy_stats(
    closes: list[float], *, ma_period: int = 50,
    horizons: tuple[int, ...] = (7, 30),
) -> dict[str, Any]:
    """Stats de la règle « acheter le passage SOUS la MM{ma_period} ».

    Un ÉVÉNEMENT = le jour où la clôture passe sous la MM (croisement, pas
    chaque jour en dessous — sinon un long bear market compte 100 fois).
    Pour chaque horizon, le retour est mesuré depuis la clôture d'événement.

    Returns:
        ``{available, ma_period, events_count, horizons: {"7": {hit_rate_pct,
        median_ret_pct, n}, ...}, note}`` — ``available=False`` si < 3
        événements mesurables (on ne publie pas une stat sur 1-2 cas).
    """
    series = [float(c) for c in (closes or []) if isinstance(c, (int, float))]
    if len(series) < ma_period + max(horizons) + 5:
        return {"available": False, "reason": "série trop courte"}
    ma = _sma(series, ma_period)

    events: list[int] = []
    for i in range(1, len(series)):
        if (ma[i] and ma[i - 1]
                and series[i] < ma[i] and series[i - 1] >= ma[i - 1]):
            events.append(i)

    out_h: dict[str, dict[str, Any]] = {}
    max_n = 0
    for h in horizons:
        rets = [
            (series[i + h] - series[i]) / series[i] * 100
            for i in events if i + h < len(series) and series[i] > 0
        ]
        if len(rets) >= 3:
            wins = sum(1 for r in rets if r > 0)
            out_h[str(h)] = {
                "hit_rate_pct": round(wins / len(rets) * 100),
                "median_ret_pct": round(median(rets), 1),
                "n": len(rets),
            }
            max_n = max(max_n, len(rets))
    if not out_h:
        return {"available": False,
                "reason": f"pas assez d'événements mesurables "
                          f"({len(events)} croisement(s) sous MM{ma_period})"}
    return {
        "available": True,
        "ma_period": ma_period,
        "events_count": len(events),
        "horizons": out_h,
        "note": (f"échantillon de {max_n} événement(s) sur l'historique "
                 "récent, hors frais — thermomètre, pas une garantie"),
    }
