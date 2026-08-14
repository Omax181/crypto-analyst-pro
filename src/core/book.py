"""RecommendationBook — AUTORITÉ UNIQUE de l'état des recommandations.

SPEC V31 §1.4, §3, §2 (I1, I2, I3, I4, I5, I7, I9, I15, I45, I46, I47, I48, I53).

Aucun module hors de cette façade ne lit ni n'écrit l'état des recommandations.
La v30 laissait cinq lecteurs maintenir chacun sa définition d'« invalidation » :
asset_plan (2e support), check_invalidations (prix <= stop, affichage seul),
apply_stop_slide_gate (payload seul), evaluate_recommendation (timeout 30 j,
SEUL écrivain), et les compteurs (status == invalidated). Une seule définition
subsiste ici.

Règles structurantes :
  - ``scored_contract`` est écrit UNE FOIS et n'est jamais modifié (I15).
    Les révisions vivent dans ``operational_plan[]`` et ne sont JAMAIS scorées.
  - Seul le run du matin peut écrire (I45/I46) : la façade est ouverte en
    lecture seule pour le soir et l'hebdo, et toute tentative d'écriture lève.
  - Un contrat n'existe que s'il a été communiqué : la persistance intervient
    APRÈS un envoi réussi (I53), jamais avant.
  - Le journal d'événements est SEGMENTÉ PAR MOIS (I61/I62) : un run ne
    réécrit que le segment courant.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from src.core import params
from src.core.horizon import Horizon, spec_for
from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATE_DIR = Path(__file__).resolve().parents[2] / "state" / "book"
_CONTRACTS = "contracts.json"
_ARCHIVE_DIR = "archive"

SCHEMA_VERSION = 1
SCORING_REGIME = "v31"


class Direction(str, Enum):
    LONG_INCREASE = "LONG_INCREASE"
    LONG_REDUCE = "LONG_REDUCE"


class State(str, Enum):
    ACTIVE = "ACTIVE"
    TARGET_HIT = "TARGET_HIT"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = frozenset({
    State.TARGET_HIT, State.INVALIDATED, State.EXPIRED,
    State.SUPERSEDED, State.CANCELLED,
})

# Table CLOSE du scoring des terminaux (SPEC §3). Aucune autre lecture permise.
SCORE_BY_STATE: dict[State, Optional[int]] = {
    State.TARGET_HIT: 1,
    State.INVALIDATED: -1,
    State.EXPIRED: 0,
    State.SUPERSEDED: None,   # exclu
    State.CANCELLED: None,    # exclu
}
# Seuls ces états alimentent le win rate et la mesure R9 (I42).
BINARY_OUTCOME_STATES = frozenset({State.TARGET_HIT, State.INVALIDATED})


class BookWriteError(RuntimeError):
    """Tentative d'écriture depuis un run non habilité, ou transition illégale."""


class ContractValidityError(ValueError):
    """Le contrat viole une règle de validité — il ne peut pas exister (I4/I48)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── structures ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoredContract:
    """IMMUABLE. Seul objet évalué par la machine à états et par le scoring."""

    entry_price: float
    target: float
    stop: float
    expires_at: str
    horizon: str
    sizing: dict[str, Any]
    viability: dict[str, Any]
    p_null: Optional[float] = None
    p_breakeven: Optional[float] = None
    delta_required: Optional[float] = None
    issued_at: str = field(default_factory=lambda: _iso(_now()))


@dataclass
class OperationalPlan:
    """Versionné, mutable, JAMAIS scoré (I7/I15)."""
    version: int
    issued_at: str
    price_at_issue: float
    levels: dict[str, Any] = field(default_factory=dict)
    add_zone: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    at: str
    type: str
    from_state: Optional[str]
    to_state: Optional[str]
    cause: str
    actor: str                      # engine | gate | user | expiry
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Recommendation:
    id: str
    asset: str
    direction: str
    horizon: str
    created_at: str
    source_run: str
    scored_contract: dict[str, Any]
    operational_plan: list[dict[str, Any]] = field(default_factory=list)
    tracking: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, Any] = field(default_factory=lambda: {"reissues": 0})
    schema_version: int = SCHEMA_VERSION
    scoring_regime: str = SCORING_REGIME

    # ── accès typés ───────────────────────────────────────────────────────
    @property
    def state_value(self) -> State:
        return State(self.state.get("value", State.ACTIVE.value))

    @property
    def is_active(self) -> bool:
        return self.state_value is State.ACTIVE

    @property
    def dir_enum(self) -> Direction:
        return Direction(self.direction)

    @property
    def entry(self) -> float:
        return float(self.scored_contract["entry_price"])

    @property
    def target(self) -> float:
        return float(self.scored_contract["target"])

    @property
    def stop(self) -> float:
        return float(self.scored_contract["stop"])

    @property
    def expires_at(self) -> Optional[datetime]:
        return _parse(self.scored_contract.get("expires_at"))

    @property
    def notional(self) -> Optional[float]:
        v = (self.scored_contract.get("sizing") or {}).get("notional_usd")
        return float(v) if isinstance(v, (int, float)) else None

    def upside_pct(self) -> float:
        """Distance POSITIVE vers la cible, direction-agnostique (SPEC §1.4)."""
        e, t = self.entry, self.target
        return ((t - e) if self.dir_enum is Direction.LONG_INCREASE
                else (e - t)) / e * 100.0

    def downside_pct(self) -> float:
        """Distance POSITIVE vers le stop, direction-agnostique."""
        e, s = self.entry, self.stop
        return ((e - s) if self.dir_enum is Direction.LONG_INCREASE
                else (s - e)) / e * 100.0


# ── règles de validité — appliquées à l'ÉCRITURE (I4, I48) ────────────────

def validate_contract(asset: str, direction: Direction, entry: float,
                      target: float, stop: float) -> None:
    """Lève ``ContractValidityError`` si le contrat ne peut pas exister.

    Un contrat invalide n'est pas corrigé au rendu : il n'est pas écrit.
    """
    if not (entry and target and stop) or entry <= 0 or target <= 0 or stop <= 0:
        raise ContractValidityError(
            f"{asset} : prix non strictement positifs (entry/target/stop)")
    if direction is Direction.LONG_INCREASE:
        if not (stop < entry < target):
            raise ContractValidityError(
                f"{asset} : LONG_INCREASE exige stop < entry < target "
                f"({stop} / {entry} / {target})")
        risk, reward = entry - stop, target - entry
    else:
        if not (target < entry < stop):
            raise ContractValidityError(
                f"{asset} : LONG_REDUCE exige target < entry < stop "
                f"({target} / {entry} / {stop})")
        risk, reward = stop - entry, entry - target
    if risk <= 0:
        raise ContractValidityError(f"{asset} : risque nul ou négatif")
    if reward / risk > 8.0:
        raise ContractValidityError(
            f"{asset} : R:R {reward / risk:.1f} > 8 — stop irréaliste")


# ── le carnet ──────────────────────────────────────────────────────────────

class RecommendationBook:
    """Façade unique. ``writable=False`` pour le soir et l'hebdo (I46)."""

    def __init__(self, *, run_kind: str, run_id: str,
                 state_dir: Optional[Path] = None) -> None:
        self.run_kind = run_kind
        self.run_id = run_id
        self.writable = run_kind == "morning"
        self._dir = Path(state_dir) if state_dir else _STATE_DIR
        self._contracts: list[Recommendation] = []
        self._pending_events: list[tuple[str, Event]] = []
        self._dirty = False
        self._load()

    # ── E/S ───────────────────────────────────────────────────────────────
    def _path(self, name: str) -> Path:
        return self._dir / name

    def _load(self) -> None:
        p = self._path(_CONTRACTS)
        if not p.exists():
            self._contracts = []
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Carnet illisible (%s) — carnet VIDE pour ce run.", exc)
            self._contracts = []
            return
        if not isinstance(raw, list):
            logger.error("Carnet de type inattendu — carnet VIDE pour ce run.")
            self._contracts = []
            return
        out: list[Recommendation] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                out.append(Recommendation(**item))
            except TypeError as exc:
                logger.warning("Contrat illisible ignoré : %s", exc)
        self._contracts = out

    def _write_atomic(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def commit(self) -> None:
        """Persiste. À n'appeler qu'APRÈS un envoi réussi (I53)."""
        if not self.writable:
            raise BookWriteError(
                f"run {self.run_kind} : le carnet est en lecture seule (I46)")
        if not self._dirty and not self._pending_events:
            return
        self._write_atomic(self._path(_CONTRACTS),
                           [asdict(c) for c in self._contracts])
        # Journal segmenté par mois : un run ne réécrit que le segment courant.
        by_segment: dict[str, list[dict[str, Any]]] = {}
        for segment, ev in self._pending_events:
            by_segment.setdefault(segment, []).append(asdict(ev))
        for segment, events in by_segment.items():
            path = self._path(f"events-{segment}.json")
            existing: list[Any] = []
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        existing = loaded
                except (json.JSONDecodeError, OSError):
                    existing = []
            self._write_atomic(path, existing + events)
        self._pending_events.clear()
        self._dirty = False

    # ── lecture ───────────────────────────────────────────────────────────
    def all(self) -> list[Recommendation]:
        return list(self._contracts)

    def active(self) -> list[Recommendation]:
        return [c for c in self._contracts if c.is_active]

    def active_by_key(self) -> dict[tuple[str, str], Recommendation]:
        return {(c.asset, c.direction): c for c in self.active()}

    def terminal_since(self, since: datetime) -> list[Recommendation]:
        out = []
        for c in self._contracts:
            if c.state_value in TERMINAL_STATES:
                at = _parse(c.state.get("since"))
                if at and at >= since:
                    out.append(c)
        return out

    def view(self, as_of: Optional[datetime] = None) -> dict[str, Any]:
        """Vue unique consommée par les TROIS mails (I13)."""
        ref = as_of or _now()
        return {
            "as_of": _iso(ref),
            "active": [asdict(c) for c in self.active()],
            "terminal_recent": [asdict(c) for c in
                                self.terminal_since(ref - timedelta(days=30))],
            "counts": {
                "active": len(self.active()),
                "terminal_30d": len(self.terminal_since(ref - timedelta(days=30))),
            },
        }

    # ── journalisation ────────────────────────────────────────────────────
    def _record(self, rec: Optional[Recommendation], *, type_: str,
                from_state: Optional[str], to_state: Optional[str],
                cause: str, actor: str, payload: Optional[dict] = None) -> None:
        at = _now()
        ev = Event(at=_iso(at), type=type_, from_state=from_state,
                   to_state=to_state, cause=cause, actor=actor,
                   payload={"run_id": self.run_id,
                            "contract_id": rec.id if rec else None,
                            **(payload or {})})
        self._pending_events.append((at.strftime("%Y-%m"), ev))

    def _guard_write(self) -> None:
        if not self.writable:
            raise BookWriteError(
                f"run {self.run_kind} : écriture interdite dans le carnet (I45/I46)")

    # ── transitions ───────────────────────────────────────────────────────
    def evaluate_transitions(
        self, *, daily_closes: dict[str, float], now: Optional[datetime] = None
    ) -> list[dict[str, Any]]:
        """Évalue les transitions sur la DERNIÈRE CLÔTURE JOURNALIÈRE (SPEC §2.4).

        Priorité si plusieurs gardes sont vraies : stop > cible > échéance
        (hypothèse la plus défavorable). Idempotente : le soir et l'hebdo
        peuvent l'appeler sans effet, mais ne peuvent pas la persister.
        """
        ref = now or _now()
        fired: list[dict[str, Any]] = []
        for rec in self._contracts:
            if not rec.is_active:
                continue
            close = daily_closes.get(rec.asset)
            new_state: Optional[State] = None
            cause = ""
            if isinstance(close, (int, float)) and close > 0:
                if rec.dir_enum is Direction.LONG_INCREASE:
                    breached = close <= rec.stop
                    reached = close >= rec.target
                else:
                    breached = close >= rec.stop
                    reached = close <= rec.target
                if breached:
                    new_state, cause = State.INVALIDATED, "stop_breached"
                elif reached:
                    new_state, cause = State.TARGET_HIT, "target_reached"
            if new_state is None:
                exp = rec.expires_at
                if exp and ref >= exp:
                    new_state, cause = State.EXPIRED, "horizon_elapsed"
            if new_state is None:
                continue
            fired.append({"asset": rec.asset, "id": rec.id,
                          "to": new_state.value, "cause": cause,
                          "close": close})
            if self.writable:
                self._transition(rec, new_state, cause=cause,
                                 actor="expiry" if cause == "horizon_elapsed"
                                 else "engine", exit_price=close)
        return fired

    def _transition(self, rec: Recommendation, to: State, *, cause: str,
                    actor: str, exit_price: Optional[float] = None) -> None:
        self._guard_write()
        if not rec.is_active:
            raise BookWriteError(
                f"{rec.asset} : transition {cause} depuis un état terminal "
                f"({rec.state_value.value}) — illégale")
        frm = rec.state_value.value
        at = _iso(_now())
        rec.state = {"value": to.value, "since": at, "reason": cause}
        if to in TERMINAL_STATES:
            px = exit_price if isinstance(exit_price, (int, float)) else None
            realized_pct = None
            realized_usd = None
            if px and rec.entry:
                sign = 1.0 if rec.dir_enum is Direction.LONG_INCREASE else -1.0
                realized_pct = sign * (px - rec.entry) / rec.entry * 100.0
                cost = ((rec.scored_contract.get("viability") or {})
                        .get("round_trip_cost_pct"))
                if isinstance(cost, (int, float)):
                    realized_pct -= float(cost)
                notional = rec.notional
                if notional:
                    realized_usd = realized_pct / 100.0 * notional
            rec.outcome = {
                "exit_price": px, "exit_at": at,
                "realized_pnl_pct": (round(realized_pct, 4)
                                     if realized_pct is not None else None),
                "realized_pnl_usd_net": (round(realized_usd, 4)
                                         if realized_usd is not None else None),
                "closed_by": actor,
            }
        self._dirty = True
        self._record(rec, type_="transition", from_state=frm,
                     to_state=to.value, cause=cause, actor=actor,
                     payload={"exit_price": exit_price})

    def cancel(self, asset: str, direction: Optional[str] = None) -> int:
        """Transition CANCELLED — seule écriture autorisée hors run matin.

        Déclenchée par une action utilisateur explicite (``/dismiss``).
        """
        n = 0
        for rec in self._contracts:
            if not rec.is_active or rec.asset != asset:
                continue
            if direction and rec.direction != direction:
                continue
            was_writable = self.writable
            self.writable = True
            try:
                self._transition(rec, State.CANCELLED, cause="dismiss",
                                 actor="user")
            finally:
                self.writable = was_writable
            n += 1
        return n

    # ── émission ──────────────────────────────────────────────────────────
    def reemission_blocked(self, asset: str, direction: Direction,
                           new_stop: float, new_horizon: Horizon,
                           sigma_h_pct: float) -> Optional[str]:
        """Contrainte post-INVALIDATED (SPEC §3). ``None`` = émission permise.

        Au moins une des trois : (a) stop >= 1 sigma_H au-delà du stop invalidé,
        (b) cooldown écoulé, (c) horizon différent.
        """
        last = None
        for rec in self._contracts:
            if rec.asset != asset or rec.direction != direction.value:
                continue
            if rec.state_value is not State.INVALIDATED:
                continue
            at = _parse(rec.state.get("since"))
            if at and (last is None or at > last[0]):
                last = (at, rec)
        if last is None:
            return None
        invalidated_at, rec = last

        if rec.horizon != new_horizon.value:
            return None  # (c)

        cooldown = params.cooldown_days()
        if cooldown is not None and \
                (_now() - invalidated_at).days >= cooldown:
            return None  # (b)

        old_stop = rec.stop
        margin_pct = abs(new_stop - old_stop) / old_stop * 100.0
        if direction is Direction.LONG_INCREASE:
            deeper = new_stop < old_stop
        else:
            deeper = new_stop > old_stop
        if deeper and margin_pct >= sigma_h_pct:
            return None  # (a)

        return (f"{asset} : contrat précédent invalidé le "
                f"{invalidated_at:%d/%m} — nouveau stop à "
                f"{margin_pct:.1f}% de l'ancien (< 1 sigma_H), cooldown non "
                f"écoulé, horizon identique")

    def emit(
        self, *, asset: str, direction: Direction, horizon: Horizon,
        entry: float, target: float, stop: float, sizing: dict[str, Any],
        viability: dict[str, Any], p_null: Optional[float],
        p_breakeven: Optional[float], delta_required: Optional[float],
        levels: Optional[dict[str, Any]] = None,
        add_zone: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[Recommendation], str]:
        """Émet, révise ou remplace. Retourne (contrat, action).

        action ∈ {"created", "revised", "superseded_and_created", "blocked"}.
        """
        self._guard_write()
        validate_contract(asset, direction, entry, target, stop)

        existing = None
        opposite = None
        for rec in self.active():
            if rec.asset != asset:
                continue
            if rec.direction == direction.value:
                existing = rec
            else:
                opposite = rec

        # Même actif + direction + horizon : RÉVISION, contrat figé.
        if existing is not None and existing.horizon == horizon.value:
            version = len(existing.operational_plan) + 1
            existing.operational_plan.append(asdict(OperationalPlan(
                version=version, issued_at=_iso(_now()), price_at_issue=entry,
                levels=dict(levels or {}), add_zone=dict(add_zone or {}))))
            existing.counters["reissues"] = \
                int(existing.counters.get("reissues") or 0) + 1
            self._dirty = True
            self._record(existing, type_="revision", from_state=State.ACTIVE.value,
                         to_state=State.ACTIVE.value,
                         cause="operational_plan_updated", actor="engine",
                         payload={"version": version})
            return existing, "revised"

        # Direction opposée, ou horizon différent : SUPERSEDED puis création.
        superseded = False
        for rec in (r for r in (existing, opposite) if r is not None):
            self._transition(rec, State.SUPERSEDED,
                             cause=("direction_reversal"
                                    if rec is opposite else "horizon_change"),
                             actor="engine")
            superseded = True

        expires = _now() + timedelta(days=spec_for(horizon).days)
        contract = ScoredContract(
            entry_price=float(entry), target=float(target), stop=float(stop),
            expires_at=_iso(expires), horizon=horizon.value,
            sizing=dict(sizing), viability=dict(viability),
            p_null=p_null, p_breakeven=p_breakeven,
            delta_required=delta_required)
        created_at = _iso(_now())
        rec = Recommendation(
            id=f"{asset}-{_now():%Y%m%d}-{direction.value}",
            asset=asset, direction=direction.value, horizon=horizon.value,
            created_at=created_at, source_run=self.run_id,
            scored_contract=asdict(contract),
            operational_plan=[asdict(OperationalPlan(
                version=1, issued_at=created_at, price_at_issue=float(entry),
                levels=dict(levels or {}), add_zone=dict(add_zone or {})))],
            tracking={"current_price": float(entry),
                      "low_since_entry": float(entry),
                      "high_since_entry": float(entry),
                      "days_elapsed": 0},
            state={"value": State.ACTIVE.value, "since": created_at,
                   "reason": "emit"},
        )
        self._contracts.append(rec)
        self._dirty = True
        self._record(rec, type_="emission", from_state=None,
                     to_state=State.ACTIVE.value, cause="emit", actor="engine",
                     payload={"viability": viability})
        return rec, ("superseded_and_created" if superseded else "created")

    # ── suivi ─────────────────────────────────────────────────────────────
    def refresh_tracking(self, *, prices: dict[str, float],
                         daily_lows: Optional[dict[str, float]] = None,
                         daily_highs: Optional[dict[str, float]] = None,
                         now: Optional[datetime] = None) -> None:
        """Met à jour le suivi. N'écrit jamais d'état, ne transitionne jamais."""
        ref = now or _now()
        for rec in self._contracts:
            if not rec.is_active:
                continue
            px = prices.get(rec.asset)
            if isinstance(px, (int, float)) and px > 0:
                rec.tracking["current_price"] = float(px)
            lo = (daily_lows or {}).get(rec.asset)
            hi = (daily_highs or {}).get(rec.asset)
            cur_lo = rec.tracking.get("low_since_entry")
            cur_hi = rec.tracking.get("high_since_entry")
            cand_lo = lo if isinstance(lo, (int, float)) else px
            cand_hi = hi if isinstance(hi, (int, float)) else px
            if isinstance(cand_lo, (int, float)):
                rec.tracking["low_since_entry"] = (
                    min(cur_lo, cand_lo) if isinstance(cur_lo, (int, float))
                    else cand_lo)
            if isinstance(cand_hi, (int, float)):
                rec.tracking["high_since_entry"] = (
                    max(cur_hi, cand_hi) if isinstance(cur_hi, (int, float))
                    else cand_hi)
            rec.tracking["extremes_are_daily"] = bool(daily_lows and daily_highs)
            created = _parse(rec.created_at)
            if created:
                rec.tracking["days_elapsed"] = max(0, (ref - created).days)
            if rec.target != rec.entry:
                cur = rec.tracking.get("current_price") or rec.entry
                rec.tracking["path_to_target_pct"] = round(
                    (cur - rec.entry) / (rec.target - rec.entry) * 100.0, 1)
            self._dirty = self._dirty or self.writable

    def consecutive_expired(self, asset: str, direction: str) -> int:
        """Contrats successifs terminés en EXPIRED (SPEC §7, DF10)."""
        seq = [c for c in self._contracts
               if c.asset == asset and c.direction == direction
               and c.state_value in TERMINAL_STATES]
        seq.sort(key=lambda c: _parse(c.state.get("since")) or _parse(c.created_at)
                 or _now())
        n = 0
        for rec in reversed(seq):
            if rec.state_value is State.EXPIRED:
                n += 1
            else:
                break
        return n

    def budget_consumed(self, *, month: Optional[str] = None) -> float:
        """Somme des notionals RECOMMANDÉS sur la période (I18, §4.3).

        Le budget est un FLUX MENSUEL d'apport, pas une exposition instantanée :
        un contrat clôturé par le marché (cible, invalidation, échéance) a bien
        consommé sa part — il ne la restitue pas.

        DEUX ÉTATS SONT EXCLUS, et ce sont exactement ceux que la table close
        ``SCORE_BY_STATE`` met déjà hors comptabilité (score ``None``) :

          SUPERSEDED  le contrat a été REMPLACÉ par un autre. Ce n'est pas un
                      apport supplémentaire, c'est le même argent redéployé ;
                      le compter deux fois ferait dépasser le plafond.
          CANCELLED   l'utilisateur a écarté la proposition. Lui facturer le
                      budget d'un geste qu'il a refusé serait le punir de
                      l'avoir refusé.

        L'exclusion n'introduit aucune règle nouvelle : elle applique au budget
        la même frontière que le scoring.
        """
        key = month or _now().strftime("%Y-%m")
        total = 0.0
        for rec in self._contracts:
            created = _parse(rec.created_at)
            if not created or created.strftime("%Y-%m") != key:
                continue
            state = rec.state_value
            if state in TERMINAL_STATES and SCORE_BY_STATE.get(state) is None:
                continue
            if rec.notional:
                total += rec.notional
        return round(total, 2)

    def committed_notional(self, asset: str, direction: Direction) -> float:
        """Notional DÉJÀ engagé sur un couple actif/direction encore ACTIF.

        Sert à ne pas facturer deux fois une RÉVISION : mettre à jour le plan
        opérationnel d'un contrat existant n'engage aucun capital nouveau — le
        notional est déjà compté dans ``budget_consumed``. Sans cette
        déduction, le carnet se fige dès le premier contrat du mois : plus
        aucune révision n'est possible, et le motif affiché (« geste de 0 $
        sous le ticket minimum ») désigne une cause fausse.
        """
        for rec in self.active():
            if rec.asset == asset and rec.direction == direction.value:
                return float(rec.notional or 0.0)
        return 0.0
