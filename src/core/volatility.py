"""Volatilité — SPEC V31 §4.2.

Estimateur NORMATIF de la volatilité journalière de diffusion :

  PRÉFÉRÉ (OK)        Parkinson sur bougies JOURNALIÈRES agrégées
                      sigma^2 = 1/(4 n ln2) * somme[ ln(H_i / L_i) ]^2
  REPLI (DEGRADED)    écart-type des rendements log journaliers sur clôtures
                      -> sous-estime sigma ; marqueur DEGRADED propagé au verdict

Deux pièges de la v30, tous deux corrigés ici :
  1. ``compute_atr_pct`` était un écart absolu moyen CLÔTURE-À-CLÔTURE, présenté
     comme un ATR. Il sous-estimait sigma d'un facteur ~1,6 à 2 (absence
     d'amplitude intra-journalière + MAD ~ 0,8 sigma).
  2. L'endpoint OHLC renvoie une granularité DÉPENDANT de la fenêtre demandée.
     Consommer ces bougies sans agrégation journalière surestime sigma d'un
     facteur ~2 dans l'autre sens. L'agrégation est donc OBLIGATOIRE et
     explicite (``aggregate_to_daily``).

Les deux erreurs produisent le même symptôme : un moteur correct qui n'émet
jamais rien, pour une raison invisible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Any, Optional, Sequence

_NORM = NormalDist()

# Nombre minimal de bougies journalières exigé par un estimateur de sigma.
# Dérivé : en dessous, l'erreur d'estimation de sigma domine la quantité mesurée.
SIGMA_MIN_BARS = 20


@dataclass(frozen=True)
class DailyBar:
    day: str          # AAAA-MM-JJ (UTC)
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SigmaEstimate:
    """sigma journalière + qualité de l'estimation.

    ``degraded`` True <=> repli clôture-à-clôture : le marqueur DOIT être
    propagé jusqu'au ViabilityVerdict (SPEC §4.2).
    """
    value: Optional[float]        # sigma journalière, en FRACTION (0.015 = 1,5 %)
    estimator: str                # "parkinson" | "logret" | "none"
    bars: int
    degraded: bool
    reason: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.value is not None and self.value > 0


def aggregate_to_daily(candles: Sequence[Sequence[Any]]) -> list[DailyBar]:
    """Agrège des bougies intra-journalières en bougies JOURNALIÈRES UTC.

    Args:
        candles: séquence ``[timestamp_ms, open, high, low, close]`` (format
            CoinGecko /ohlc). Toute bougie mal formée est ignorée.

    Returns:
        Bougies journalières triées chronologiquement. Une journée incomplète
        est conservée : elle sera écartée en amont si nécessaire (la dernière
        clôture complète est déterminée par le carnet, pas ici).
    """
    buckets: dict[str, list[list[float]]] = {}
    for c in candles or []:
        if not isinstance(c, (list, tuple)) or len(c) < 5:
            continue
        try:
            ts = float(c[0]) / 1000.0
            o, h, l_, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l_, cl)):
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append([ts, o, h, l_, cl])

    out: list[DailyBar] = []
    for day in sorted(buckets):
        rows = sorted(buckets[day], key=lambda r: r[0])
        out.append(DailyBar(
            day=day,
            open=rows[0][1],
            high=max(r[2] for r in rows),
            low=min(r[3] for r in rows),
            close=rows[-1][4],
        ))
    return out


def parkinson_sigma(bars: Sequence[DailyBar], window: int) -> Optional[float]:
    """Volatilité journalière de diffusion, estimateur de Parkinson.

    sigma^2 = 1 / (4 n ln2) * somme[ ln(H_i / L_i) ]^2
    """
    usable = [b for b in bars if b.high > 0 and b.low > 0 and b.high >= b.low]
    if len(usable) < SIGMA_MIN_BARS:
        return None
    sample = usable[-window:] if window and len(usable) > window else usable
    n = len(sample)
    if n < SIGMA_MIN_BARS:
        return None
    acc = 0.0
    for b in sample:
        acc += math.log(b.high / b.low) ** 2
    var = acc / (4.0 * n * math.log(2.0))
    if var <= 0 or not math.isfinite(var):
        return None
    return math.sqrt(var)


def logret_sigma(closes: Sequence[float], window: int) -> Optional[float]:
    """Repli : écart-type des rendements log journaliers. SOUS-ESTIME sigma."""
    vals = [float(c) for c in (closes or [])
            if isinstance(c, (int, float)) and c and c > 0]
    if len(vals) < SIGMA_MIN_BARS + 1:
        return None
    sample = vals[-(window + 1):] if window else vals
    rets = [math.log(sample[i] / sample[i - 1]) for i in range(1, len(sample))]
    if len(rets) < SIGMA_MIN_BARS:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0 or not math.isfinite(var):
        return None
    return math.sqrt(var)


def _usable_bars(bars: Sequence[DailyBar]) -> int:
    return sum(1 for b in bars if b.high > 0 and b.low > 0 and b.high >= b.low)


def daily_sigma(
    *,
    daily_bars: Optional[Sequence[DailyBar]],
    closes: Optional[Sequence[float]],
    window: int,
) -> SigmaEstimate:
    """sigma journalière selon l'ordre normatif : Parkinson, puis repli.

    FENÊTRE NON COUVERTE = DÉGRADÉ. ``parkinson_sigma`` n'exige que
    ``SIGMA_MIN_BARS`` barres, pas la fenêtre demandée : sans ce contrôle, une
    sigma estimée sur trente bougies serait appliquée à un horizon de six mois
    SANS QUE RIEN NE LE DISE. L'estimation reste utilisée — elle vaut mieux que
    rien — mais le marqueur remonte jusqu'au verdict et jusqu'au bandeau.

    Le critère est binaire et vérifiable : l'estimateur dispose-t-il de la
    fenêtre qu'exige l'horizon ? Aucun seuil intermédiaire n'est inventé.
    """
    if daily_bars:
        v = parkinson_sigma(daily_bars, window)
        if v is not None:
            n = min(_usable_bars(daily_bars), window)
            short = n < window
            return SigmaEstimate(
                v, "parkinson", len(daily_bars), short,
                (f"volatilité estimée sur {n} bougies pour une fenêtre de "
                 f"{window} — horizon extrapolé") if short else None)
    if closes:
        v = logret_sigma(closes, window)
        if v is not None:
            why = ("OHLC insuffisant" if daily_bars else "OHLC indisponible")
            return SigmaEstimate(
                v, "logret", len(closes), True,
                f"{why} — volatilité estimée sur clôtures (sous-estimation)")
    return SigmaEstimate(None, "none", 0, True,
                         "série insuffisante pour estimer la volatilité")


def sigma_h(sigma_daily: float, horizon_days: int) -> float:
    """sigma_H = sigma_jour * racine(H). Hypothèse i.i.d. déclarée.

    En présence de clustering de volatilité et de queues épaisses, cette mise à
    l'échelle SOUS-ESTIME la dispersion réelle à long horizon : le biais pousse
    vers des stops plus serrés, donc plus d'invalidations. Documenté ici, jamais
    silencieux.
    """
    return float(sigma_daily) * math.sqrt(max(1, int(horizon_days)))


def k_from_probability(p_max: float) -> Optional[float]:
    """Multiple de sigma_H tel que P(toucher la barrière) <= ``p_max``.

    P(toucher) ~= 2 * Phi(-x / sigma_H)  =>  k = -Phi^-1(p_max / 2)

    Repères : 0,50 -> 0,674 · 0,32 -> 0,994 · 0,20 -> 1,282 · 0,13 -> 1,514
              0,05 -> 1,960
    """
    try:
        p = float(p_max)
    except (TypeError, ValueError):
        return None
    if not (0.0 < p < 1.0):
        return None
    return -_NORM.inv_cdf(p / 2.0)


def touch_probability(distance_pct: float, sigma_h_pct: float) -> Optional[float]:
    """P(toucher une barrière à ``distance_pct`` sur l'horizon), marche sans dérive.

    Les deux arguments sont EN POINTS DE POURCENTAGE. Surveillance continue
    supposée ; l'évaluation réelle se faisant sur clôture journalière, la
    probabilité réelle est plus FAIBLE — biais conservateur, assumé.
    """
    try:
        x = abs(float(distance_pct))
        s = float(sigma_h_pct)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    return min(1.0, 2.0 * _NORM.cdf(-x / s))
