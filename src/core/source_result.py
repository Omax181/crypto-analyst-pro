"""Enveloppe de transport — SPEC V31 §1.1 (I30, I31).

AUCUNE fonction du chemin marché ne renvoie de valeur nue. Le classement
d'erreur est NORMATIF : il ne se déduit pas, il se lit dans la table §1.1.

La v30 effondrait 403 / 410 / 429 / timeout / JSON malformé / corps vide sur un
unique ``None``. C'est la cause racine de R5 : la couche transport détruisait
l'information avant qu'aucun consommateur ne puisse en tenir compte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class SourceStatus(str, Enum):
    """États mutuellement exclusifs — table close de la SPEC §1.1."""

    OK = "OK"                    # charge exploitable, fraîche, source primaire
    DEGRADED = "DEGRADED"        # repli, ou périmée, ou parseur peu sûr
    EMPTY = "EMPTY"              # source jointe, rien à renvoyer
    UNAVAILABLE = "UNAVAILABLE"  # échec (transitoire ou non)
    DEAD = "DEAD"                # échec définitif : 410, ou 404 x3 runs


class Tier(str, Enum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class Provenance:
    source_id: str
    tier: Tier = Tier.PRIMARY
    endpoint: Optional[str] = None
    fallback_rank: int = 0       # 0 = primaire, 1..n = rang du repli

    def label(self) -> str:
        if self.tier is Tier.PRIMARY:
            return self.source_id
        return f"{self.source_id} (repli {self.fallback_rank})"


@dataclass(frozen=True)
class Failure:
    http_status: Optional[int] = None
    exception_class: Optional[str] = None
    retryable: bool = False

    def label(self) -> str:
        if self.http_status is not None:
            return f"HTTP {self.http_status}"
        return self.exception_class or "inconnu"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceResult:
    """Résultat d'une source. ``status`` fait foi, jamais la présence de value."""

    status: SourceStatus
    provenance: Provenance
    value: Any = None
    as_of: Optional[datetime] = None       # horodatage de la DONNÉE
    fetched_at: datetime = field(default_factory=_now)  # horodatage de l'APPEL
    depth: Optional[int] = None            # nb de points (séries uniquement)
    corroboration: int = 0                 # sources indépendantes concordantes
    failure: Optional[Failure] = None
    note: Optional[str] = None             # motif de DEGRADED, lisible

    # ── prédicats ─────────────────────────────────────────────────────────
    @property
    def usable(self) -> bool:
        """La charge est exploitable (OK ou DEGRADED). EMPTY ne l'est pas."""
        return self.status in (SourceStatus.OK, SourceStatus.DEGRADED)

    @property
    def degraded(self) -> bool:
        return self.status is SourceStatus.DEGRADED

    def require(self) -> Any:
        """Charge exploitable, ou ``None``. Ne lève jamais."""
        return self.value if self.usable else None

    def with_status(self, status: SourceStatus, note: Optional[str] = None
                    ) -> "SourceResult":
        return SourceResult(
            status=status, provenance=self.provenance, value=self.value,
            as_of=self.as_of, fetched_at=self.fetched_at, depth=self.depth,
            corroboration=self.corroboration, failure=self.failure,
            note=note or self.note)


# ── constructeurs ─────────────────────────────────────────────────────────

def ok(source_id: str, value: Any, *, as_of: Optional[datetime] = None,
       depth: Optional[int] = None, endpoint: Optional[str] = None,
       corroboration: int = 1) -> SourceResult:
    return SourceResult(
        status=SourceStatus.OK,
        provenance=Provenance(source_id, Tier.PRIMARY, endpoint),
        value=value, as_of=as_of, depth=depth, corroboration=corroboration)


def degraded(source_id: str, value: Any, note: str, *,
             as_of: Optional[datetime] = None, depth: Optional[int] = None,
             fallback_rank: int = 1, endpoint: Optional[str] = None,
             corroboration: int = 1) -> SourceResult:
    tier = Tier.FALLBACK if fallback_rank else Tier.PRIMARY
    return SourceResult(
        status=SourceStatus.DEGRADED,
        provenance=Provenance(source_id, tier, endpoint, fallback_rank),
        value=value, as_of=as_of, depth=depth, note=note,
        corroboration=corroboration)


def empty(source_id: str, *, endpoint: Optional[str] = None) -> SourceResult:
    return SourceResult(
        status=SourceStatus.EMPTY,
        provenance=Provenance(source_id, Tier.PRIMARY, endpoint))


def unavailable(source_id: str, failure: Failure, *,
                endpoint: Optional[str] = None) -> SourceResult:
    return SourceResult(
        status=SourceStatus.UNAVAILABLE,
        provenance=Provenance(source_id, Tier.PRIMARY, endpoint),
        failure=failure)


def dead(source_id: str, failure: Failure, *,
         endpoint: Optional[str] = None) -> SourceResult:
    return SourceResult(
        status=SourceStatus.DEAD,
        provenance=Provenance(source_id, Tier.PRIMARY, endpoint),
        failure=failure)


# ── classement d'erreur — NORMATIF (SPEC §1.1) ────────────────────────────

_DEAD_STATUSES = (410,)
_NON_RETRYABLE = (403,)


def classify(
    source_id: str,
    *,
    http_status: Optional[int] = None,
    exception: Optional[BaseException] = None,
    consecutive_404: int = 0,
    endpoint: Optional[str] = None,
) -> SourceResult:
    """Traduit une observation de transport en ``SourceResult`` d'échec.

    Table close, aucune interprétation possible :
      410, ou 404 sur 3 runs consécutifs  -> DEAD
      403                                 -> UNAVAILABLE (retryable=False)
      429, 5xx, timeout, JSON malformé    -> UNAVAILABLE (retryable=True)
    """
    if http_status in _DEAD_STATUSES or (http_status == 404
                                         and consecutive_404 >= 3):
        return dead(source_id,
                    Failure(http_status=http_status, retryable=False),
                    endpoint=endpoint)
    if http_status in _NON_RETRYABLE:
        return unavailable(source_id,
                           Failure(http_status=http_status, retryable=False),
                           endpoint=endpoint)
    retryable = True
    if http_status is not None and 400 <= http_status < 500 \
            and http_status != 429:
        retryable = False
    return unavailable(
        source_id,
        Failure(http_status=http_status,
                exception_class=type(exception).__name__ if exception else None,
                retryable=retryable),
        endpoint=endpoint)
