"""RunSummary et bandeau de dégradation — SPEC V31 §1.9, §7, §8 (I57, I58).

Le bandeau REMPLACE l'indice de confiance supprimé (BB2). Ce n'est pas une
métrique : ni note, ni seuil, ni pondération, ni grade. C'est une ÉNUMÉRATION
des dégradations OBSERVÉES.

Règle d'honnêteté attachée : le bandeau n'affirme JAMAIS que le reste va bien.
Absence de bandeau = aucune dégradation DÉTECTÉE, pas « rapport fiable ».
C'est exactement l'erreur que faisait « 23/25 sources actives ».
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.core import params
from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATE_DIR = Path(__file__).resolve().parents[2] / "state"
_SUMMARY_FILE = "run_summary.json"
_HISTORY_FILE = "run_history.json"
_MAX_HISTORY = 60
# Aucun intervalle silencieux au-delà de ce seuil (I58) : la v30 laissait
# 2 min 11 s sans trace dans le run du soir.
SILENT_PHASE_WARN_S = 30.0


@dataclass
class RunSummary:
    run_id: str
    kind: str
    started_at: str
    ended_at: Optional[str] = None
    status: str = "running"          # running | success | failed
    phase_durations: dict[str, float] = field(default_factory=dict)
    source_matrix: list[dict[str, Any]] = field(default_factory=list)
    models_used: list[dict[str, str]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    degradations: list[str] = field(default_factory=list)
    disabled_features: list[str] = field(default_factory=list)

    def add_degradation(self, text: str) -> None:
        if text and text not in self.degradations:
            self.degradations.append(text)

    def note_model(self, pass_name: str, model: str) -> None:
        """Traçabilité modèle (I56) : la v30 n'écrivait cette information nulle
        part — seule l'URL httpx la révélait, par accident."""
        self.models_used.append({"pass": pass_name, "model": model})

    @contextmanager
    def phase(self, name: str):
        """Borne une phase du run. Aucun intervalle silencieux ne subsiste."""
        t0 = time.monotonic()
        logger.info("→ phase %s", name)
        try:
            yield
        finally:
            dt = time.monotonic() - t0
            self.phase_durations[name] = round(dt, 2)
            logger.info("← phase %s terminée en %.2fs", name, dt)

    def finish(self, status: str) -> None:
        self.status = status
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.disabled_features = params.disabled_features()


def new_run(kind: str) -> RunSummary:
    now = datetime.now(timezone.utc)
    return RunSummary(run_id=f"{kind}-{now:%Y%m%dT%H%M%SZ}", kind=kind,
                      started_at=now.isoformat())


def _write_atomic(path: Path, payload: Any) -> None:
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


def persist(summary: RunSummary, state_dir: Optional[Path] = None) -> None:
    """Écrit le résumé. À n'appeler qu'après un envoi réussi (I53)."""
    base = Path(state_dir) if state_dir else _STATE_DIR
    _write_atomic(base / _SUMMARY_FILE, asdict(summary))
    history: list[Any] = []
    hp = base / _HISTORY_FILE
    if hp.exists():
        try:
            loaded = json.loads(hp.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except (json.JSONDecodeError, OSError):
            history = []
    history.append({"run_id": summary.run_id, "kind": summary.kind,
                    "ended_at": summary.ended_at, "status": summary.status,
                    "counters": dict(summary.counters)})
    _write_atomic(hp, history[-_MAX_HISTORY:])


def load_last(state_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    base = Path(state_dir) if state_dir else _STATE_DIR
    p = base / _SUMMARY_FILE
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# ── bandeau de dégradation (BB2) ──────────────────────────────────────────

def degradation_banner(summary: RunSummary) -> Optional[str]:
    """Énumération conditionnelle. ``None`` = aucune dégradation détectée.

    Ne produit ni note, ni grade, ni pourcentage : il n'y a rien à saturer.
    """
    if not summary.degradations:
        return None
    return "Rapport partiel : " + " · ".join(summary.degradations)


def build_degradations(*, health_matrix: list[Any], non_evaluable: int,
                       missing_params: list[str], rejections: int,
                       sigma_degraded: Any = None,
                       failed_assets: Any = None) -> list[str]:
    """Construit l'énumération à partir des faits OBSERVÉS du run.

    ``sigma_degraded`` est une table ``{actif: motif}``. Le motif est REPRIS
    tel quel : « estimée sur clôtures » et « fenêtre non couverte » sont deux
    causes distinctes, et afficher l'une pour l'autre serait faux. Une liste
    d'actifs sans motif reste acceptée pour compatibilité.
    """
    out: list[str] = []
    for h in health_matrix:
        if getattr(h, "degraded", False):
            out.append(h.describe())
    # Un actif écarté sur erreur de traitement DOIT être dit : il ne figure ni
    # dans les candidats ni dans les rejets, et sans cette ligne il
    # disparaîtrait du rapport sans laisser de trace lisible.
    if failed_assets:
        noms = sorted(failed_assets) if isinstance(failed_assets, dict) \
            else sorted(failed_assets)
        out.append(f"{len(noms)} actif(s) écarté(s) sur erreur de "
                   f"traitement : {', '.join(noms)}")
    if non_evaluable:
        out.append(f"{non_evaluable} recommandation(s) non évaluable(s)")
    if missing_params:
        out.append("paramètres métier absents : " + ", ".join(missing_params))
    if rejections >= 3:
        out.append(f"{rejections} champs éditoriaux écartés")

    if sigma_degraded:
        if isinstance(sigma_degraded, dict):
            by_reason: dict[str, list[str]] = {}
            for asset, reason in sigma_degraded.items():
                key = reason or "volatilité estimée en mode dégradé"
                by_reason.setdefault(key, []).append(asset)
            for reason, assets in sorted(by_reason.items()):
                out.append(f"{reason} — {', '.join(sorted(assets))}")
        else:
            out.append("volatilité estimée en mode dégradé pour "
                       + ", ".join(sorted(sigma_degraded)))
    return out


# ── chien de garde (R10-1) ────────────────────────────────────────────────

def watchdog_verdict(state_dir: Optional[Path] = None,
                     now: Optional[datetime] = None) -> dict[str, Any]:
    """Le dernier run réussi est-il trop ancien ? Désactivé si non paramétré."""
    cfg = params.watchdog()
    if cfg is None:
        return {"enabled": False, "reason": "chien de garde non paramétré"}
    last = load_last(state_dir)
    if not last or not last.get("ended_at"):
        return {"enabled": True, "alert": True,
                "reason": "aucun run réussi enregistré"}
    try:
        ended = datetime.fromisoformat(str(last["ended_at"]))
    except ValueError:
        return {"enabled": True, "alert": True,
                "reason": "horodatage du dernier run illisible"}
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    silence_h = (ref - ended).total_seconds() / 3600.0
    limit = float(cfg["max_silence_hours"])
    return {"enabled": True, "alert": silence_h > limit,
            "silence_hours": round(silence_h, 1), "limit_hours": limit,
            "channel": cfg["channel"], "last_run": last.get("run_id")}
