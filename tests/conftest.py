"""Configuration pytest — V31.

Deux garanties, toutes deux nécessaires :

  1. STUBS des SDK réseau absents en local, pour que la logique soit testable
     hors ligne. En CI, les vraies bibliothèques sont installées et priment.
  2. ISOLATION DE L'ÉTAT. Aucun test n'écrit dans le ``state/`` livré. La
     v30 laissait ses runs de test déposer des résumés « failed » dans l'arbre
     de livraison — un état de test embarqué dans un ZIP de production.
"""

from __future__ import annotations

import sys
import types

import pytest

_STUBS = [
    "google", "google.genai", "matplotlib", "bs4", "dateutil", "zoneinfo",
]

for _m in _STUBS:
    if _m not in sys.modules:
        try:
            __import__(_m)
        except ImportError:
            sys.modules[_m] = types.ModuleType(_m)

import importlib.util as _ilu  # noqa: E402

if "tenacity" not in sys.modules and _ilu.find_spec("tenacity") is None:
    _t = types.ModuleType("tenacity")
    _t.retry = lambda *a, **k: (lambda f: f)
    _t.retry_if_exception_type = lambda *a, **k: None
    _t.stop_after_attempt = lambda *a, **k: None
    _t.wait_exponential = lambda *a, **k: None
    _t.before_sleep_log = lambda *a, **k: None
    sys.modules["tenacity"] = _t

if "requests" not in sys.modules and _ilu.find_spec("requests") is None:
    _r = types.ModuleType("requests")
    _r.RequestException = Exception
    _r.get = lambda *a, **k: None
    sys.modules["requests"] = _r


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirige TOUS les répertoires d'état vers un dossier temporaire.

    Autouse : la protection ne dépend pas de la vigilance de chaque test. Un
    test qui veut inspecter l'état pointe explicitement sur ``tmp_path``.
    """
    from src.core import book, registry, runlog
    from src.state import bot_memory

    monkeypatch.setattr(book, "_STATE_DIR", tmp_path / "book", raising=False)
    monkeypatch.setattr(runlog, "_STATE_DIR", tmp_path / "state", raising=False)
    monkeypatch.setattr(registry, "_STATE_DIR", tmp_path / "state",
                        raising=False)
    monkeypatch.setattr(bot_memory, "_STATE_DIR", tmp_path / "state",
                        raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_params():
    """Aucun test n'hérite du cache de paramètres métier d'un autre."""
    from src.core import params
    params.reset_cache()
    yield
    params.reset_cache()
