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

# v32 (3.6) — en deçà de ce nombre d'observations, la statistique porte une
# réserve explicite. Elle reste publiée (arbitrage d'Omar) : c'est un
# thermomètre, pas un résultat.
_N_CONCLUANT = 10


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
    min_n = 0
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
            min_n = len(rets) if min_n == 0 else min(min_n, len(rets))
    if not out_h:
        return {"available": False,
                "reason": f"pas assez d'événements mesurables "
                          f"({len(events)} croisement(s) sous MM{ma_period})"}
    return {
        "available": True,
        "ma_period": ma_period,
        "events_count": len(events),
        "horizons": out_h,
        # v32 (3.6) — la note annonçait ``max_n``, le MAXIMUM des horizons,
        # pendant que chaque ligne portait le sien. Le 24/08 : « 7j (n=5) ·
        # 30j (n=3) » suivi de « échantillon de 5 événement(s) » — la note
        # contredisait la moitié de ce qu'elle résumait. On annonce la PLAGE
        # réellement couverte, ou le nombre unique s'il n'y en a qu'un.
        # v32 (3.6) — sous 10 observations, on le DIT. Arbitrage d'Omar
        # (25/08/2026) : la statistique garde sa valeur de thermomètre et
        # reste publiée, mais le 24/08 « 30j : 0 % de hausse, retour médian
        # −16,2 % (n=3) » se lisait comme un résultat. Trois observations ne
        # départagent rien. Même logique que la garde de sélectivité posée sur
        # les patterns historiques.
        "note": (
            (f"échantillon de {min_n} événement(s) sur l'historique récent"
             if min_n == max_n else
             f"échantillon de {min_n} à {max_n} événement(s) selon l'horizon")
            + ", hors frais — thermomètre, pas une garantie"
            + (f" · échantillon trop mince pour conclure (n < {_N_CONCLUANT})"
               if min_n < _N_CONCLUANT else "")),
    }
