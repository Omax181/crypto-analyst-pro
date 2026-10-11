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

# tenacity : fournir des décorateurs no-op si absent. v29 (audit) — probe de
# présence via find_spec (pyflakes-clean), sémantique identique à l'import.
import importlib.util as _ilu

if "tenacity" not in sys.modules and _ilu.find_spec("tenacity") is None:
    _t = types.ModuleType("tenacity")
    _t.retry = lambda *a, **k: (lambda f: f)
    _t.retry_if_exception_type = lambda *a, **k: None
    _t.stop_after_attempt = lambda *a, **k: None
    _t.wait_exponential = lambda *a, **k: None
    sys.modules["tenacity"] = _t

# requests : exceptions minimales si absent.
if "requests" not in sys.modules and _ilu.find_spec("requests") is None:
    _r = types.ModuleType("requests")
    _r.RequestException = Exception
    _r.get = lambda *a, **k: None
    sys.modules["requests"] = _r


# ── v32 (5.15) — DATES DE FIXTURE RELATIVES ───────────────────────────────
# Les fixtures datées en ABSOLU pourrissent : « 2026-07-15 » tombait hors de la
# fenêtre de 30 j du win rate dès le 15/08/2026, et
# ``test_extract_lesson_picks_most_costly`` échouait sans qu'une ligne de code
# ait bougé. Une suite qui verdit en juillet et rougit en août ne prouve rien,
# et fait sauter les portes de déploiement qui comptent les tests verts.
def il_y_a(jours: float) -> str:
    """Horodatage ISO-8601 UTC situé ``jours`` avant maintenant."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()


# ── v33 (audit 01/10) — AUCUN TEST N'ÉCRIT DANS LE VRAI ``state/`` ─────────
# Un rendu du matin passe par ``record_seen_news`` : la suite ajoutait ses
# titres de test au ``state/seen_news.json`` du dépôt. Lancée par le script de
# déploiement sur le clone de production, elle aurait mêlé ces titres à la
# déduplication réelle des news, puis les aurait commités avec le code.
import pytest


@pytest.fixture(autouse=True)
def _etat_isole(tmp_path_factory, monkeypatch):
    from src.state import report_memory as _mem
    monkeypatch.setattr(_mem, "_STATE_DIR", tmp_path_factory.mktemp("state"))
