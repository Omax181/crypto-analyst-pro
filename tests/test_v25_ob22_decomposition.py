# -*- coding: utf-8 -*-
"""OB22 — décomposition des prompts / ANTI-PANNE.

Le matin est déjà décomposé en 2 passes (régime best-effort + analyse), et le
travail v25 (exit_radar, narratives, skew, calibration…) déplace la logique HORS
du prompt (forme la plus saine de « décomposition »). Ces tests VERROUILLENT
l'invariant clé exigé par Omar : « jamais 1 découpe sur 2 » — chaque générateur
renvoie TOUJOURS un dict valide (réel ou dégradé), jamais une sortie partielle,
jamais d'exception propagée.
"""

from __future__ import annotations

import pytest

from src.ai_brain.decision_engine import DecisionEngine
from src.ai_brain.gemini_client import GeminiQuotaError


class _SeqClient:
    """Client factice : renvoie ou lève les éléments de ``seq`` à chaque appel."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def generate_json(self, prompt, *, temperature=0.4, model=None):
        self.calls += 1
        item = self.seq.pop(0) if self.seq else {}
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_long_pause(monkeypatch):
    # Sécurité : neutralise l'ultime tentative différée (sinon pause 10 min).
    monkeypatch.setenv("GEMINI_LAST_CHANCE_PAUSE_S", "0")


_KW = dict(
    timestamp="05/07 08:00",
    data={"macro_context": {}, "analytics_digest": {}, "fear_greed": {},
          "all_positions_summary": [], "win_rate": {}},
    portfolio_data={"portfolio": {}}, evening_state={},
)


def test_morning_pass1_fails_pass2_runs_never_partial():
    """Passe 1 en panne → la passe 2 tourne quand même (jamais « 1 sur 2 »)."""
    c = _SeqClient([RuntimeError("pass1 down"), {"executive_summary": "ok"}])
    out = DecisionEngine(client=c).generate_morning(**_KW)
    assert isinstance(out, dict)
    assert out.get("executive_summary") == "ok"
    assert not out.get("_degraded")


def test_morning_cascading_failure_degrades_never_raises():
    """Passe 1 vide + passe 2 quota → dégradé PROPRE (le mail part quand même)."""
    c = _SeqClient([{}, GeminiQuotaError("quota")])
    out = DecisionEngine(client=c).generate_morning(**_KW)
    assert isinstance(out, dict) and out.get("_degraded") is True


def test_evening_always_returns_dict_on_quota():
    c = _SeqClient([GeminiQuotaError("quota")])
    out = DecisionEngine(client=c).generate_evening(
        timestamp="t", data={}, morning_state={})
    assert isinstance(out, dict) and out.get("_degraded") is True


def test_weekly_always_returns_dict_on_quota():
    c = _SeqClient([GeminiQuotaError("quota")])
    out = DecisionEngine(client=c).generate_weekly(
        timestamp="t", data={}, week_state={})
    assert isinstance(out, dict) and out.get("_degraded") is True


def test_no_client_degrades_without_any_call():
    eng = DecisionEngine(client=object())  # client bidon…
    eng.client = None                       # …puis simulate init échoué
    eng._init_error = "clé absente"
    out = eng.generate_morning(**_KW)
    assert isinstance(out, dict) and out.get("_degraded") is True
