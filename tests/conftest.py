"""Configuration pytest : stub des libs externes absentes en CI hors-ligne.

Permet d'exécuter les tests unitaires de logique sans installer les SDK réseau
(google-generativeai, tradingview-ta, telethon, etc.). Les tests réels avec
réseau s'exécutent dans l'environnement GitHub Actions avec les vraies libs.
"""

from __future__ import annotations

import os
import sys
import types

# v26 (E-B1c) — l'ultime tentative Gemini différée attend 10 min en prod avant
# de dégrader. En test, cette pause est désactivée par défaut (les tests qui la
# vérifient la réactivent explicitement via monkeypatch + sleep injecté).
os.environ.setdefault("GEMINI_LAST_CHANCE_PAUSE_S", "0")

_STUBS = [
    "google", "google.generativeai", "tradingview_ta", "fredapi",
    "youtube_transcript_api", "pandas", "bs4", "telethon", "telethon.sync",
    "telethon.sessions", "matplotlib", "dotenv", "dateutil", "cachetools",
]

for _m in _STUBS:
    if _m not in sys.modules:
        try:
            __import__(_m)
        except ImportError:
            sys.modules[_m] = types.ModuleType(_m)

# tenacity : fournir des décorateurs no-op si absent.
if "tenacity" not in sys.modules:
    try:
        import tenacity  # noqa: F401
    except ImportError:
        _t = types.ModuleType("tenacity")
        _t.retry = lambda *a, **k: (lambda f: f)
        _t.retry_if_exception_type = lambda *a, **k: None
        _t.stop_after_attempt = lambda *a, **k: None
        _t.wait_exponential = lambda *a, **k: None
        sys.modules["tenacity"] = _t

# requests : exceptions minimales si absent.
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ImportError:
        _r = types.ModuleType("requests")
        _r.RequestException = Exception
        _r.get = lambda *a, **k: None
        sys.modules["requests"] = _r
