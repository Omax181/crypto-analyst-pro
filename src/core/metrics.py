"""Métriques — SPEC V31 §7 (I49 à I52).

Jeu CLOS de six métriques. Une métrique existe si et seulement si elle répond
à une question nommée. Rien d'autre n'est publié.

Trois règles sans exception :
  1. toute statistique publiée déclare SA FENÊTRE et SON n, à côté du chiffre ;
  2. sous le plancher d'échantillon : RIEN — ni chiffre, ni substitut, ni
     mention « en calibration » ;
  3. aucune métrique de recommandation n'est présentée comme une performance
     de portefeuille, ni réciproquement.

La v30 publiait un Brier « 0.175 (bien calibré, n=9) » à côté de « 0 reco
clôturée » : deux fenêtres non déclarées, et une calibration portant sur un
champ qui ne décidait de rien.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from src.core import params
from src.core.book import (BINARY_OUTCOME_STATES, RecommendationBook, State)

# Quantile normal unilatéral à 95 % — borne supérieure de Wilson.
_Z_95_ONE_SIDED = 1.645


@dataclass(frozen=True)
class Metric:
    """Une métrique publiable. ``published`` False => rien n'est rendu."""

    key: str
    question: str
    window_label: str
    n: int
    published: bool
    value: Optional[float] = None
    upper_bound: Optional[float] = None
    detail: dict[str, Any] = field(default_factory=dict)
    reason_unpublished: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "question": self.question,
                "window": self.window_label, "n": self.n,
                "published": self.published, "value": self.value,
                "upper_bound": self.upper_bound, "detail": dict(self.detail),
                "reason": self.reason_unpublished}


def _floor() -> Optional[int]:
    return params.n_min()


def _unpublished(key: str, question: str, window: str, n: int,
                 reason: str) -> Metric:
    return Metric(key=key, question=question, window_label=window, n=n,
                  published=False, reason_unpublished=reason)


def _wilson_upper(successes: int, n: int) -> Optional[float]:
    """Borne supérieure unilatérale de Wilson sur un taux binomial."""
    if n <= 0:
        return None
    z = _Z_95_ONE_SIDED
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, center + half)


# ── 1. « Mes recommandations gagnent-elles ? » ─────────────────────────────

def win_rate(book: RecommendationBook, last_n: int = 50) -> Metric:
    closed = [c for c in book.all()
              if c.state_value in BINARY_OUTCOME_STATES]
    closed.sort(key=lambda c: c.state.get("since") or c.created_at)
    sample = closed[-last_n:]
    n = len(sample)
    floor = _floor()
    window = f"{last_n} derniers contrats clôturés"
    if floor is None:
        return _unpublished("win_rate", "Mes recommandations gagnent-elles ?",
                            window, n, "plancher d'échantillon non défini")
    if n < floor:
        return _unpublished("win_rate", "Mes recommandations gagnent-elles ?",
                            window, n, f"{n} < {floor} clôtures binaires")
    wins = sum(1 for c in sample if c.state_value is State.TARGET_HIT)
    return Metric("win_rate", "Mes recommandations gagnent-elles ?", window,
                  n, True, value=round(wins / n * 100.0, 1),
                  detail={"wins": wins, "losses": n - wins})


# ── 2. « Mes horizons sont-ils calibrés ? » ────────────────────────────────

def horizon_calibration(book: RecommendationBook, last_n: int = 50) -> Metric:
    terminal = [c for c in book.all()
                if c.state_value in (State.TARGET_HIT, State.INVALIDATED,
                                     State.EXPIRED)]
    terminal.sort(key=lambda c: c.state.get("since") or c.created_at)
    sample = terminal[-last_n:]
    n = len(sample)
    floor = _floor()
    window = f"{last_n} derniers contrats terminés"
    runs: dict[str, int] = {}
    for c in book.all():
        k = f"{c.asset}/{c.direction}"
        if k not in runs:
            runs[k] = book.consecutive_expired(c.asset, c.direction)
    repeated = {k: v for k, v in runs.items() if v >= 3}
    if floor is None:
        return _unpublished("horizon_calibration",
                            "Mes horizons sont-ils calibrés ?", window, n,
                            "plancher d'échantillon non défini")
    if n < floor:
        return _unpublished("horizon_calibration",
                            "Mes horizons sont-ils calibrés ?", window, n,
                            f"{n} < {floor} contrats terminés")
    expired = sum(1 for c in sample if c.state_value is State.EXPIRED)
    return Metric("horizon_calibration", "Mes horizons sont-ils calibrés ?",
                  window, n, True, value=round(expired / n * 100.0, 1),
                  detail={"expired": expired,
                          "repeated_expired": repeated})


# ── 3. « Mon avantage est-il réel ? » (R9) ─────────────────────────────────

def realized_edge(book: RecommendationBook, last_n: int = 50) -> Metric:
    """Avantage réalisé sur le null : taux de réussite − moyenne(p0).

    N'est PAS un asservissement : ``delta_claimable`` n'est jamais modifié par
    le code (I44). Cette métrique est une INSTRUMENTATION.
    """
    closed = [c for c in book.all()
              if c.state_value in BINARY_OUTCOME_STATES
              and isinstance(c.scored_contract.get("p_null"), (int, float))]
    closed.sort(key=lambda c: c.state.get("since") or c.created_at)
    sample = closed[-last_n:]
    n = len(sample)
    floor = _floor()
    window = f"{last_n} derniers contrats clôturés sur issue binaire"
    if floor is None:
        return _unpublished("realized_edge", "Mon avantage est-il réel ?",
                            window, n, "plancher d'échantillon non défini")
    if n < floor:
        return _unpublished("realized_edge", "Mon avantage est-il réel ?",
                            window, n, f"{n} < {floor} clôtures binaires")
    wins = sum(1 for c in sample if c.state_value is State.TARGET_HIT)
    mean_p0 = sum(float(c.scored_contract["p_null"]) for c in sample) / n
    hit = wins / n
    return Metric("realized_edge", "Mon avantage est-il réel ?", window, n,
                  True, value=round((hit - mean_p0) * 100.0, 2),
                  upper_bound=round((_wilson_upper(wins, n) - mean_p0) * 100.0, 2),
                  detail={"hit_rate_pct": round(hit * 100, 1),
                          "mean_p_null_pct": round(mean_p0 * 100, 1)})


# ── 4. « Le moteur émet-il ? » ─────────────────────────────────────────────

def emission(candidates_total: int, emitted: int, non_viable: int,
             non_evaluable: int, reasons: dict[str, int]) -> Metric:
    """Toujours publiée : pas de plancher, c'est un compte du run."""
    return Metric("emission", "Le moteur émet-il ?", "run courant",
                  candidates_total, True,
                  value=float(emitted),
                  detail={"emitted": emitted, "non_viable": non_viable,
                          "non_evaluable": non_evaluable,
                          "reasons": dict(reasons)})


# ── 5. « Le contrat de contenu tient-il ? » ────────────────────────────────

def content_contract(authored_fields: int, rejections: int) -> Metric:
    return Metric("content_contract", "Le contrat de contenu tient-il ?",
                  "run courant", authored_fields, True,
                  value=round(rejections / authored_fields * 100.0, 1)
                  if authored_fields else 0.0,
                  detail={"rejections": rejections})


# ── 6. « Les sources tiennent-elles ? » ────────────────────────────────────

def sources(health_matrix: list[Any]) -> Metric:
    degraded = [h for h in health_matrix if getattr(h, "degraded", False)]
    return Metric("sources", "Les sources tiennent-elles ?", "instantané",
                  len(health_matrix), True, value=float(len(degraded)),
                  detail={"degraded": [h.describe() for h in degraded]})


def all_metrics(book: RecommendationBook, *, health_matrix: list[Any],
                candidates_total: int, emitted: int, non_viable: int,
                non_evaluable: int, reasons: dict[str, int],
                authored_fields: int, rejections: int) -> list[Metric]:
    """Les six, dans l'ordre de la SPEC. Aucune autre n'est publiable."""
    return [
        win_rate(book),
        horizon_calibration(book),
        realized_edge(book),
        emission(candidates_total, emitted, non_viable, non_evaluable, reasons),
        content_contract(authored_fields, rejections),
        sources(health_matrix),
    ]
