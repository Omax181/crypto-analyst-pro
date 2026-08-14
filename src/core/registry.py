"""Registre des sources — SPEC V31 §1.2, §6 (I32, I34).

La CRITICITÉ n'est pas arbitrée : elle est DÉRIVÉE d'une question binaire —
« cette source alimente-t-elle un gate de viabilité ? ». Après V31-A, seules
trois entrées de marché le font ; toutes les autres sont ``optional`` par
construction, et leur péremption produit un marqueur, jamais un blocage.

La FRAÎCHEUR se mesure en PUBLICATIONS MANQUÉES, déduites de la série
elle-même — jamais en jours calendaires. Cela supprime le piège du week-end
(un lundi lisant une clôture de vendredi a 3 jours et n'est pas périmé), le
besoin d'un calendrier de jours fériés et toute dépendance au fuseau.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.core import params
from src.core.source_result import SourceResult, SourceStatus
from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATE_DIR = Path(__file__).resolve().parents[2] / "state"
_DEAD_FILE = "dead_sources.json"

# Table CLOSE des sources alimentant un gate de viabilité (SPEC §1.2).
# Toute extension exige une modification du moteur de viabilité.
BLOCKING_SOURCES = frozenset({"market.closes", "market.ohlc", "market.spot"})


@dataclass(frozen=True)
class SourceSpec:
    id: str
    label: str
    publication_cadence_days: Optional[float] = None   # None = flux continu
    usage_right: str = "context"
    fallback_chain: tuple[str, ...] = ()
    dead_reprobe_days: int = 7

    @property
    def criticality(self) -> str:
        """DÉRIVÉE, jamais saisie."""
        return "blocking" if self.id in BLOCKING_SOURCES else "optional"

    @property
    def is_continuous(self) -> bool:
        return not self.publication_cadence_days


@dataclass
class SourceHealth:
    spec: SourceSpec
    status: SourceStatus
    as_of: Optional[datetime]
    tier: str
    missed_publications: Optional[int] = None
    note: Optional[str] = None

    @property
    def stale(self) -> bool:
        """Périmé <=> au moins une publication manquée (SPEC §6)."""
        return bool(self.missed_publications and self.missed_publications >= 1)

    @property
    def degraded(self) -> bool:
        return (self.status in (SourceStatus.DEGRADED, SourceStatus.UNAVAILABLE,
                                SourceStatus.DEAD, SourceStatus.EMPTY)
                or self.stale)

    def describe(self) -> str:
        if self.status is SourceStatus.DEAD:
            return f"{self.spec.label} définitivement indisponible"
        if self.status is SourceStatus.UNAVAILABLE:
            return f"{self.spec.label} indisponible"
        if self.status is SourceStatus.EMPTY:
            return f"{self.spec.label} sans donnée"
        if self.stale:
            n = self.missed_publications
            return (f"{self.spec.label} : {n} publication"
                    f"{'s' if n and n > 1 else ''} manquée"
                    f"{'s' if n and n > 1 else ''}")
        if self.status is SourceStatus.DEGRADED:
            return f"{self.spec.label} en repli ({self.note or 'source secondaire'})"
        return f"{self.spec.label} nominale"


def missed_publications(
    *, as_of: Optional[datetime], cadence_days: Optional[float],
    latency_days: Optional[int], now: Optional[datetime] = None,
) -> Optional[int]:
    """Nombre de publications manquées, déduit de la cadence de la source.

    ``None`` si la cadence ou la latence est inconnue : dans ce cas la source
    est traitée comme DEGRADED (SPEC §9.2), jamais comme fraîche.
    """
    if as_of is None or not cadence_days:
        return None
    if latency_days is None:
        return None
    ref = now or datetime.now(timezone.utc)
    age_days = (ref - as_of).total_seconds() / 86400.0
    effective = age_days - float(latency_days)
    if effective <= 0:
        return 0
    return int(effective // float(cadence_days))


def assess(spec: SourceSpec, result: SourceResult,
           now: Optional[datetime] = None) -> SourceHealth:
    """Croise statut de transport et fraîcheur pour produire l'état d'une source."""
    latency = params.publication_latency_days(spec.id)
    missed = None
    note = result.note
    if spec.is_continuous:
        missed = 0
    else:
        missed = missed_publications(
            as_of=result.as_of, cadence_days=spec.publication_cadence_days,
            latency_days=latency, now=now)
        if missed is None and result.usable:
            note = note or "latence de publication non déclarée"
    status = result.status
    if status is SourceStatus.OK and (missed is None or missed >= 1):
        status = SourceStatus.DEGRADED
    return SourceHealth(spec=spec, status=status, as_of=result.as_of,
                        tier=result.provenance.tier.value, note=note,
                        missed_publications=missed)


# ── persistance du statut DEAD (I32) ──────────────────────────────────────

def _dead_path(state_dir: Optional[Path] = None) -> Path:
    return (Path(state_dir) if state_dir else _STATE_DIR) / _DEAD_FILE


def load_dead(state_dir: Optional[Path] = None) -> dict[str, str]:
    p = _dead_path(state_dir)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def mark_dead(source_id: str, spec: SourceSpec,
              state_dir: Optional[Path] = None) -> None:
    p = _dead_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_dead(state_dir)
    reprobe = datetime.now(timezone.utc) + timedelta(days=spec.dead_reprobe_days)
    data[source_id] = reprobe.isoformat()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def should_skip(source_id: str, state_dir: Optional[Path] = None,
                now: Optional[datetime] = None) -> bool:
    """Une source DEAD n'est pas rappelée avant sa date de re-sondage (I32)."""
    data = load_dead(state_dir)
    ts = data.get(source_id)
    if not ts:
        return False
    try:
        reprobe = datetime.fromisoformat(ts)
    except ValueError:
        return False
    if reprobe.tzinfo is None:
        reprobe = reprobe.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) < reprobe


# ── catalogue ─────────────────────────────────────────────────────────────

CATALOG: dict[str, SourceSpec] = {
    s.id: s for s in (
        SourceSpec("market.closes", "Clôtures journalières", 1.0, "fact"),
        SourceSpec("market.ohlc", "OHLC journalier", 1.0, "fact"),
        SourceSpec("market.spot", "Prix spot", None, "fact"),
        SourceSpec("fear_greed", "Fear & Greed", 1.0),
        SourceSpec("fred", "FRED", 1.0),
        SourceSpec("onchain", "On-chain", 1.0),
        SourceSpec("etf_flows", "Flux ETF", 1.0),
        SourceSpec("polymarket", "Polymarket", None),
        SourceSpec("news", "Actualités", None),
        SourceSpec("macro_calendar", "Calendrier macro", 7.0),
        SourceSpec("derivatives", "Dérivés", 0.34),
        SourceSpec("equities", "Actions", 1.0),
    )
}


def matrix(results: dict[str, SourceResult],
           now: Optional[datetime] = None) -> list[SourceHealth]:
    """Matrice d'états — remplace le compteur « X/25 sources » (SPEC §11)."""
    out: list[SourceHealth] = []
    for sid, res in sorted(results.items()):
        spec = CATALOG.get(sid) or SourceSpec(sid, sid)
        out.append(assess(spec, res, now=now))
    return out
