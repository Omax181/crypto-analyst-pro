"""État du bot et derniers rapports — SPEC V31 §1.4, §8.

Ce module NE CONTIENT AUCUN état de recommandation. L'autorité unique du carnet
est ``core.book`` ; ce qui reste ici est strictement conversationnel :

  - décalage et historique Telegram ;
  - mémoire durable (notes et décisions saisies par l'utilisateur) ;
  - instantané du dernier rapport envoyé, pour que le bot puisse s'y référer.

La v30 mélangeait les deux dans ``report_memory`` (917 lignes) : c'est ce
mélange qui permettait à ``add_recommendation`` d'écraser un stop en conservant
l'entrée, produisant un contrat au stop flottant et à l'entrée figée.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_STATE_DIR = Path(__file__).resolve().parents[2] / "state"

TELEGRAM_OFFSET_FILE = "telegram_offset.json"
TELEGRAM_HISTORY_FILE = "telegram_history.json"
BOT_MEMORY_FILE = "bot_memory.json"
LAST_REPORTS_FILE = "last_reports.json"

MAX_HISTORY_TURNS = 40
MAX_MEMORY_ENTRIES = 200


def _path(name: str, state_dir: Optional[Path] = None) -> Path:
    return (Path(state_dir) if state_dir else _STATE_DIR) / name


def _read(name: str, default: Any, state_dir: Optional[Path] = None) -> Any:
    p = _path(name, state_dir)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("État %s illisible (%s) — valeur par défaut.", name, exc)
        return default


def _write(name: str, data: Any, state_dir: Optional[Path] = None) -> None:
    p = _path(name, state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.",
                              suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Telegram ──────────────────────────────────────────────────────────────

def load_telegram_offset(state_dir: Optional[Path] = None) -> int:
    data = _read(TELEGRAM_OFFSET_FILE, {}, state_dir)
    val = data.get("offset") if isinstance(data, dict) else None
    return int(val) if isinstance(val, int) else 0


def save_telegram_offset(offset: int, state_dir: Optional[Path] = None) -> None:
    _write(TELEGRAM_OFFSET_FILE, {"offset": int(offset), "at": now_iso()},
           state_dir)


def load_telegram_history(limit: int = 12, state_dir: Optional[Path] = None
                          ) -> list[dict[str, Any]]:
    data = _read(TELEGRAM_HISTORY_FILE, [], state_dir)
    turns = data if isinstance(data, list) else []
    return turns[-limit:] if limit else turns


def append_telegram_turn(role: str, content: str, *,
                         max_keep: int = MAX_HISTORY_TURNS,
                         state_dir: Optional[Path] = None) -> None:
    data = _read(TELEGRAM_HISTORY_FILE, [], state_dir)
    turns = data if isinstance(data, list) else []
    turns.append({"role": role, "content": str(content)[:4000],
                  "at": now_iso()})
    _write(TELEGRAM_HISTORY_FILE, turns[-max_keep:], state_dir)


# ── mémoire durable ───────────────────────────────────────────────────────

def load_bot_memory(limit: int = 0, state_dir: Optional[Path] = None
                    ) -> list[dict[str, Any]]:
    data = _read(BOT_MEMORY_FILE, [], state_dir)
    items = data if isinstance(data, list) else []
    return items[-limit:] if limit else items


def append_bot_memory(kind: str, text: str, *,
                      max_keep: int = MAX_MEMORY_ENTRIES,
                      state_dir: Optional[Path] = None) -> None:
    items = load_bot_memory(state_dir=state_dir)
    items.append({"kind": kind, "text": str(text)[:600], "at": now_iso()})
    _write(BOT_MEMORY_FILE, items[-max_keep:], state_dir)


def remove_bot_memory(index: int, state_dir: Optional[Path] = None) -> bool:
    items = load_bot_memory(state_dir=state_dir)
    if not 0 <= index < len(items):
        return False
    items.pop(index)
    _write(BOT_MEMORY_FILE, items, state_dir)
    return True


# ── instantané des rapports envoyés ───────────────────────────────────────

# Champs conservés : de quoi répondre à « qu'as-tu dit ce matin ? » sans
# dupliquer une once de logique décisionnelle.
_SNAPSHOT_KEYS = ("title", "date_label", "banner", "top_action",
                  "nothing_to_do", "emissions", "transitions", "intraday")


def save_report_snapshot(kind: str, payload: dict[str, Any],
                         state_dir: Optional[Path] = None) -> None:
    """Enregistre un extrait du mail ENVOYÉ. Jamais avant l'envoi (I53)."""
    data = _read(LAST_REPORTS_FILE, {}, state_dir)
    store = data if isinstance(data, dict) else {}
    store[kind] = {"at": now_iso(),
                   **{k: payload.get(k) for k in _SNAPSHOT_KEYS}}
    _write(LAST_REPORTS_FILE, store, state_dir)


def load_report_snapshot(kind: str, state_dir: Optional[Path] = None
                         ) -> dict[str, Any]:
    data = _read(LAST_REPORTS_FILE, {}, state_dir)
    entry = data.get(kind) if isinstance(data, dict) else None
    return entry if isinstance(entry, dict) else {}


def load_latest_snapshot(state_dir: Optional[Path] = None
                         ) -> tuple[Optional[str], dict[str, Any]]:
    """Rapport le plus récent, quel que soit son type."""
    data = _read(LAST_REPORTS_FILE, {}, state_dir)
    if not isinstance(data, dict) or not data:
        return None, {}
    kind = max(data, key=lambda k: str((data.get(k) or {}).get("at") or ""))
    return kind, data.get(kind) or {}
