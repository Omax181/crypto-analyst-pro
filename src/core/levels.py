"""Niveaux — SPEC V31 §4.2 (Phase 2).

Deux changements structurels par rapport à ``analytics/key_levels`` :

  1. La FENÊTRE est un paramètre d'horizon, jamais une constante. La v30 codait
     en dur ``series[-120:]``, ``_fib_levels(lookback=90)`` et les périodes
     50/100/200 : un horizon ne pouvait pas choisir son échelle.
  2. Le FILTRAGE par sigma_H précède la TRONCATURE. La v30 ne retenait que les
     3 niveaux les PLUS PROCHES, avant tout filtre : en régime de range, les
     trois résistances étaient toutes à moins de 3 % et la vraie cible, plus
     loin, n'était jamais candidate. C'est la cause de fond du cas
     « ETH 2 000 -> 2 015 » — le repli ``ress[-1]`` n'en était que le symptôme.

Le repli sans plancher est SUPPRIMÉ : si aucun niveau ne satisfait la
contrainte de bruit, il n'y a pas de cible valide, donc pas de contrat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from src.core.horizon import HorizonSpec

# Priorité des bases lors du clustering : un pivot testé bat une MM, qui bat un
# retracement Fibo, qui bat une bande de Bollinger, qui bat un seuil rond.
# Les moyennes mobiles sont classées DYNAMIQUEMENT (voir ``_rank``) : leur
# période dépend de l'horizon, donc les énumérer en dur ferait retomber toute
# MM non listée — MM200 pour POSITION — au rang par défaut, derrière un simple
# seuil rond.
_BASIS_RANK = {"pivot": 0, "Fibo": 3, "Bollinger": 4, "seuil": 5}
_MA_RANK_LONG, _MA_RANK_SHORT = 1, 2
_DEFAULT_RANK = 9
_CLUSTER_TOL_PCT = 1.2


def _rank(basis: str) -> int:
    """Rang de priorité d'une base. Une MM LONGUE prime sur une MM courte."""
    head = basis.split()[0]
    if head.startswith("MM"):
        try:
            period = int(head[2:])
        except ValueError:
            return _DEFAULT_RANK
        return _MA_RANK_LONG if period >= 50 else _MA_RANK_SHORT
    return _BASIS_RANK.get(head, _DEFAULT_RANK)


@dataclass(frozen=True)
class Level:
    price: float
    basis: str
    dist_pct: float          # signé : négatif sous le prix, positif au-dessus

    @property
    def abs_dist_pct(self) -> float:
        return abs(self.dist_pct)


@dataclass(frozen=True)
class LevelSet:
    supports: list[Level]        # du plus proche au plus loin, sous le prix
    resistances: list[Level]     # du plus proche au plus loin, au-dessus
    price: float
    bars_used: int


def _sma(closes: Sequence[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _pivots(closes: Sequence[float], span: int = 3) -> list[float]:
    out: list[float] = []
    n = len(closes)
    for i in range(span, n - span):
        window = closes[i - span:i + span + 1]
        if closes[i] == max(window) or closes[i] == min(window):
            out.append(closes[i])
    return out


def _bollinger(closes: Sequence[float], period: int = 20
               ) -> Optional[tuple[float, float]]:
    if len(closes) < period:
        return None
    sample = closes[-period:]
    mean = sum(sample) / period
    var = sum((c - mean) ** 2 for c in sample) / period
    sd = math.sqrt(var)
    return mean - 2 * sd, mean + 2 * sd


def _fib(closes: Sequence[float], lookback: int) -> list[tuple[float, str]]:
    window = closes[-lookback:] if len(closes) > lookback else list(closes)
    if len(window) < 5:
        return []
    hi, lo = max(window), min(window)
    if hi <= lo:
        return []
    span = hi - lo
    return [(hi - span * 0.382, "Fibo 38,2%"),
            (hi - span * 0.5, "Fibo 50%"),
            (hi - span * 0.618, "Fibo 61,8%")]


def _round_step(price: float) -> float:
    if price <= 0:
        return 1.0
    return 10.0 ** (math.floor(math.log10(price)) - 1)


def _cluster(cands: list[tuple[float, str]], price: float) -> list[tuple[float, str]]:
    out: list[list] = []
    for lvl, basis in sorted(cands, key=lambda c: c[0]):
        rank = _rank(basis)
        if out and abs(lvl - out[-1][0]) / price * 100.0 <= _CLUSTER_TOL_PCT:
            if rank < out[-1][2]:
                out[-1][0], out[-1][1], out[-1][2] = lvl, basis, rank
        else:
            out.append([lvl, basis, rank])
    return [(o[0], o[1]) for o in out]


def compute(closes: Sequence[float], price: float, spec: HorizonSpec) -> LevelSet:
    """Jeu de niveaux COMPLET (non tronqué), calculé sur la fenêtre de l'horizon."""
    series = [float(c) for c in closes if isinstance(c, (int, float)) and c > 0]
    window = series[-spec.level_window_bars:] if \
        len(series) > spec.level_window_bars else series
    cands: list[tuple[float, str]] = []
    for p in _pivots(window):
        cands.append((p, "pivot"))
    for period, name in ((20, "MM20"), (spec.longest_ma_period,
                                        f"MM{spec.longest_ma_period}")):
        ma = _sma(series, period)
        if ma:
            cands.append((ma, name))
    boll = _bollinger(window)
    if boll:
        cands.append((boll[0], "Bollinger basse"))
        cands.append((boll[1], "Bollinger haute"))
    cands.extend(_fib(series, spec.fib_window_bars))
    step = _round_step(price)
    cands.append((math.floor(price / step) * step, "seuil rond"))
    cands.append((math.ceil(price / step) * step, "seuil rond"))

    merged = _cluster([c for c in cands if c[0] > 0], price)
    supports = [Level(l, b, (l - price) / price * 100.0)
                for l, b in merged if l < price * 0.998]
    resistances = [Level(l, b, (l - price) / price * 100.0)
                   for l, b in merged if l > price * 1.002]
    supports.sort(key=lambda x: -x.price)      # du plus proche au plus loin
    resistances.sort(key=lambda x: x.price)
    return LevelSet(supports=supports, resistances=resistances,
                    price=price, bars_used=len(window))


def select_target(levels: LevelSet, *, sigma_h_pct: float, k2: float,
                  direction_up: bool) -> Optional[Level]:
    """Cible : niveau le plus PROCHE satisfaisant ``dist >= k2 * sigma_H``.

    Filtrage AVANT sélection. Aucun repli : ``None`` si rien ne satisfait.
    """
    pool = levels.resistances if direction_up else levels.supports
    floor = k2 * sigma_h_pct
    eligible = [lv for lv in pool if lv.abs_dist_pct >= floor]
    return eligible[0] if eligible else None


def select_stop(levels: LevelSet, *, sigma_h_pct: float, k2p: float,
                direction_up: bool) -> Optional[Level]:
    """Invalidation : niveau le plus PROCHE au-delà de ``k2' * sigma_H``.

    Un stop plus serré serait déclenché par le bruit de l'horizon ; un stop
    beaucoup plus lointain dégraderait inutilement le rapport gain/risque.
    """
    pool = levels.supports if direction_up else levels.resistances
    floor = k2p * sigma_h_pct
    eligible = [lv for lv in pool if lv.abs_dist_pct >= floor]
    return eligible[0] if eligible else None
