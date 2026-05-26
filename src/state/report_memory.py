"""Mémoire inter-rapports persistée sur disque (commitée par les workflows).

Permet au rapport du soir de lire le matin, au matin de lire le soir précédent,
et à l'hebdo d'agréger la semaine. Tous les fichiers vivent dans ``state/`` à
la racine du repo et sont commités avec ``[skip ci]``.

Robustesse : toute lecture d'un fichier absent/corrompu renvoie un défaut sûr
plutôt que de lever, pour ne jamais bloquer un rapport.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Racine du repo = parent de src/. state/ est à la racine.
_STATE_DIR = Path(__file__).resolve().parents[2] / "state"

MORNING_FILE = "last_morning_report.json"
EVENING_FILE = "last_evening_report.json"
WEEKLY_FILE = "last_weekly_report.json"
ACTIVE_RECOS_FILE = "active_recommendations.json"
PREDICTION_HISTORY_FILE = "prediction_history.json"
PANIC_FILE = "last_panic_email.json"


def _path(name: str) -> Path:
    return _STATE_DIR / name


def _read(name: str, default: Any) -> Any:
    """Lit un JSON de state, renvoie ``default`` si absent ou illisible."""
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("State illisible %s : %s — défaut utilisé.", name, exc)
        return default


def _write(name: str, data: Any) -> None:
    """Écrit un JSON de state (crée le dossier au besoin)."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _path(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Échec écriture state %s : %s", name, exc)


def now_iso() -> str:
    """Timestamp ISO UTC courant."""
    return datetime.now(timezone.utc).isoformat()


# --------------------------- rapports --------------------------------------- #
def save_morning_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport du matin (lu par le soir)."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(MORNING_FILE, payload)


def load_morning_report() -> dict[str, Any]:
    """Charge le dernier rapport du matin (``{}`` si absent)."""
    return _read(MORNING_FILE, {})


def save_evening_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport du soir (lu par le matin suivant)."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(EVENING_FILE, payload)


def load_evening_report() -> dict[str, Any]:
    """Charge le dernier rapport du soir (``{}`` si absent)."""
    return _read(EVENING_FILE, {})


def save_weekly_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport hebdo."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(WEEKLY_FILE, payload)


def load_weekly_report() -> dict[str, Any]:
    """Charge le dernier rapport hebdo (``{}`` si absent)."""
    return _read(WEEKLY_FILE, {})


# --------------------------- recommandations -------------------------------- #
def load_active_recommendations() -> list[dict[str, Any]]:
    """Charge les recommandations actives (non clôturées)."""
    return _read(ACTIVE_RECOS_FILE, [])


def save_active_recommendations(recos: list[dict[str, Any]]) -> None:
    """Sauvegarde la liste des recommandations actives."""
    _write(ACTIVE_RECOS_FILE, recos)


def add_recommendation(reco: dict[str, Any]) -> None:
    """Ajoute une nouvelle reco à la liste active (dédupliquée par id)."""
    recos = load_active_recommendations()
    existing_ids = {r.get("id") for r in recos}
    if reco.get("id") in existing_ids:
        logger.info("Reco %s déjà active, ignorée.", reco.get("id"))
        return
    reco.setdefault("created_at", now_iso())
    reco.setdefault("status", "in_progress")
    recos.append(reco)
    save_active_recommendations(recos)


# --------------------------- historique prédictions ------------------------- #
def load_prediction_history() -> list[dict[str, Any]]:
    """Charge l'historique complet des prédictions (clôturées + en cours)."""
    return _read(PREDICTION_HISTORY_FILE, [])


def save_prediction_history(history: list[dict[str, Any]]) -> None:
    """Sauvegarde l'historique des prédictions."""
    _write(PREDICTION_HISTORY_FILE, history)


# --------------------------- panic anti-spam -------------------------------- #
def load_last_panic() -> dict[str, Any]:
    """Charge l'horodatage du dernier panic email."""
    return _read(PANIC_FILE, {})


def mark_panic_sent(triggers: list[str]) -> None:
    """Enregistre l'envoi d'un panic email (anti-spam)."""
    _write(PANIC_FILE, {"sent_at": now_iso(), "triggers": triggers})
