"""FactStore — SPEC V31 §1.3 (I26, I27, I33, I34).

Un fait est la SEULE source d'un nombre destiné au lecteur. Il porte sa valeur,
son unité, sa fraîcheur, sa provenance et son UNIQUE représentation textuelle
(produite par ``core.formatter``).

Deux règles de propagation, normatives :
  - ``as_of`` d'un fait dérivé  = MINIMUM des as_of de ses entrées ;
  - ``staleness_status`` dérivé = OU LOGIQUE des staleness de ses entrées.
Sans elles, un prix frais combiné à une volatilité périmée paraîtrait frais.

Le FactStore est construit INTÉGRALEMENT avant l'appel LLM ; aucun fait n'est
créé après (le LLM consomme, il ne produit pas — I36).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Optional

from src.core import formatter as fmt
from src.core.source_result import Provenance, SourceResult, SourceStatus


class Unit(str, Enum):
    USD_PRICE = "usd_price"        # prix -> formatter.price
    USD_AMOUNT = "usd_amount"      # montant signé -> formatter.usd
    USD_COMPACT = "usd_compact"    # 2,3 Mds$ -> formatter.compact_usd
    PCT = "pct"                    # points de pourcentage -> formatter.pct
    RATIO = "ratio"                # sans dimension -> formatter.ratio
    COUNT = "count"                # entier -> formatter.integer
    SIGMA = "sigma"                # multiples d'écart-type
    DAYS = "days"
    DATE = "date"
    TEXT = "text"                  # déjà textuel, non numérique


class UsageRight(str, Enum):
    FACT = "fact"                  # référençable par un champ AUTHORED
    CONTEXT = "context"            # visible du LLM, non référençable
    DISPLAY_ONLY = "display_only"  # jamais transmis au LLM (I39)


class Staleness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True)
class Fact:
    id: str
    value: Any
    unit: Unit
    as_of: Optional[datetime] = None
    staleness: Staleness = Staleness.FRESH
    provenance: Optional[Provenance] = None
    usage_right: UsageRight = UsageRight.FACT
    derived_from: tuple[str, ...] = ()
    note: Optional[str] = None

    @property
    def formatted(self) -> str:
        """UNIQUE représentation textuelle (I27). Aucune autre n'est permise."""
        v, u = self.value, self.unit
        if u is Unit.USD_PRICE:
            return fmt.price(v)
        if u is Unit.USD_AMOUNT:
            return fmt.usd(v)
        if u is Unit.USD_COMPACT:
            return fmt.compact_usd(v)
        if u is Unit.PCT:
            return fmt.pct(v)
        if u is Unit.RATIO:
            return fmt.ratio(v)
        if u is Unit.COUNT:
            return fmt.integer(v)
        if u is Unit.SIGMA:
            return fmt.sigma(v)
        if u is Unit.DAYS:
            return fmt.days(v)
        if u is Unit.DATE:
            return fmt.date_fr(v)
        return fmt.ABSENT if v is None else str(v)

    @property
    def is_stale(self) -> bool:
        return self.staleness is Staleness.STALE

    def to_context(self) -> dict[str, Any]:
        """Forme transmise au LLM : valeur VISIBLE, id RÉFÉRENÇABLE."""
        return {"id": self.id, "value": self.formatted, "unit": self.unit.value,
                "stale": self.is_stale,
                "source": self.provenance.label() if self.provenance else None}


class FactStore:
    """Registre des faits d'un run. Immuable après la construction."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}
        self._sealed = False

    # ── construction ─────────────────────────────────────────────────────
    def register(
        self, fact_id: str, value: Any, unit: Unit, *,
        source: Optional[SourceResult] = None,
        usage_right: UsageRight = UsageRight.FACT,
        as_of: Optional[datetime] = None,
        stale: bool = False,
        note: Optional[str] = None,
    ) -> Fact:
        if self._sealed:
            raise RuntimeError(
                "FactStore scellé : aucun fait ne peut être créé après "
                "l'appel LLM (SPEC §2.1 étape 2)")
        prov = source.provenance if source else None
        eff_as_of = as_of or (source.as_of if source else None)
        eff_stale = stale or bool(
            source is not None and source.status in (SourceStatus.DEGRADED,))
        f = Fact(id=fact_id, value=value, unit=unit, as_of=eff_as_of,
                 staleness=Staleness.STALE if eff_stale else Staleness.FRESH,
                 provenance=prov, usage_right=usage_right, note=note)
        self._facts[fact_id] = f
        return f

    def derive(
        self, fact_id: str, value: Any, unit: Unit, *,
        inputs: Iterable[str],
        usage_right: UsageRight = UsageRight.FACT,
        note: Optional[str] = None,
    ) -> Fact:
        """Fait dérivé : as_of = MIN des entrées, staleness = OU des entrées (I33)."""
        if self._sealed:
            raise RuntimeError("FactStore scellé")
        ids = tuple(inputs)
        parents = [self._facts[i] for i in ids if i in self._facts]
        dates = [p.as_of for p in parents if p.as_of is not None]
        as_of = min(dates) if dates else None
        stale = any(p.is_stale for p in parents)
        prov = parents[0].provenance if parents else None
        f = Fact(id=fact_id, value=value, unit=unit, as_of=as_of,
                 staleness=Staleness.STALE if stale else Staleness.FRESH,
                 provenance=prov, usage_right=usage_right,
                 derived_from=ids, note=note)
        self._facts[fact_id] = f
        return f

    def seal(self) -> None:
        self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    # ── lecture ──────────────────────────────────────────────────────────
    def get(self, fact_id: str) -> Optional[Fact]:
        return self._facts.get(fact_id)

    def has(self, fact_id: str) -> bool:
        return fact_id in self._facts

    def formatted(self, fact_id: str) -> str:
        f = self._facts.get(fact_id)
        return f.formatted if f else fmt.ABSENT

    def ids(self) -> list[str]:
        return sorted(self._facts)

    def stale_ids(self) -> list[str]:
        return sorted(i for i, f in self._facts.items() if f.is_stale)

    def referenceable_ids(self) -> set[str]:
        """Faits qu'un champ AUTHORED peut citer.

        Un fait périmé n'est PAS référençable (SPEC §6 règle 2) : il reste
        affichable avec son marqueur, mais le LLM ne peut pas construire un
        récit dessus.
        """
        return {i for i, f in self._facts.items()
                if f.usage_right is UsageRight.FACT and not f.is_stale}

    def llm_context(self) -> list[dict[str, Any]]:
        """Contexte CURÉ transmis au prompt (I39) : jamais de display_only."""
        return [f.to_context() for f in
                sorted((x for x in self._facts.values()
                        if x.usage_right is not UsageRight.DISPLAY_ONLY),
                       key=lambda x: x.id)]

    def render_map(self) -> dict[str, str]:
        """Table id -> texte formaté, consommée par la substitution de jetons."""
        return {i: f.formatted for i, f in self._facts.items()}
