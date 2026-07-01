"""Logging centralisé pour l'agent crypto.

Configure un logger qui écrit à la fois sur la console (stdout, capté par
GitHub Actions) et dans `logs/agent.log` (uploadé en artifact en cas d'échec).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger configuré.

    Args:
        name: nom du logger (typiquement ``__name__`` du module appelant).

    Returns:
        Une instance ``logging.Logger`` prête à l'emploi.
    """
    global _CONFIGURED
    if not _CONFIGURED:
        _configure_root()
        _CONFIGURED = True
    return logging.getLogger(name)


def _configure_root() -> None:
    """Configure le logger racine une seule fois."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # Système de fichiers en lecture seule : on se contente de la console.
        root.warning("Impossible d'écrire dans logs/ ; logs console uniquement.")
