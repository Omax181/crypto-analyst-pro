# -*- coding: utf-8 -*-
"""Tests v27.1 — routage de modèle par tâche (pro pour l'analyse matin/hebdo,
flash pour le soir/régime), le repli restant garanti."""

from __future__ import annotations


_ALL_MODEL_ENVS = ("GEMINI_MODEL", "GEMINI_MODEL_DEEP", "GEMINI_MODEL_FAST",
                   "GEMINI_MODEL_MORNING", "GEMINI_MODEL_EVENING",
                   "GEMINI_MODEL_WEEKLY")


def _clear_all(monkeypatch):
    for k in _ALL_MODEL_ENVS:
        monkeypatch.delenv(k, raising=False)


# ── AUTO : un SEUL secret GEMINI_MODEL_DEEP suffit ────────────────────────
def test_auto_deep_used_for_strategic_tasks_only(monkeypatch):
    """L'objectif d'Omar : mettre le pro À DISPOSITION → auto sur matin+hebdo,
    flash ailleurs, avec UN seul secret (GEMINI_MODEL_DEEP)."""
    from src.ai_brain import decision_engine as de
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")   # défaut rapide
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "gemini-2.5-pro")  # pro à dispo
    # Tâches profondes → pro AUTOMATIQUEMENT (aucun secret par mail).
    assert de._model_for_kind("morning") == "gemini-2.5-pro"
    assert de._model_for_kind("weekly") == "gemini-2.5-pro"
    # Tâches rapides → flash.
    assert de._model_for_kind("evening") == "gemini-2.5-flash"
    assert de._model_for_kind("macro_regime") == "gemini-2.5-flash"


def test_no_deep_secret_everything_stays_base(monkeypatch):
    """Sans GEMINI_MODEL_DEEP : aucun changement, tout sur le modèle de base."""
    from src.ai_brain import decision_engine as de
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    for kind in ("morning", "weekly", "evening", "macro_regime"):
        assert de._model_for_kind(kind) == "gemini-2.5-flash"


def test_explicit_override_wins_over_auto(monkeypatch):
    """Échappatoire avancée : un override par mail prime sur l'auto."""
    from src.ai_brain import decision_engine as de
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "gemini-2.5-pro")
    monkeypatch.setenv("GEMINI_MODEL_EVENING", "gemini-2.5-pro")  # force soir=pro
    assert de._model_for_kind("evening") == "gemini-2.5-pro"
    # Le matin garde l'auto (deep → pro).
    assert de._model_for_kind("morning") == "gemini-2.5-pro"


def test_empty_values_ignored(monkeypatch):
    from src.ai_brain import decision_engine as de
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "   ")           # vide → ignoré
    monkeypatch.setenv("GEMINI_MODEL_MORNING", "  ")          # vide → ignoré
    assert de._model_for_kind("morning") == "gemini-2.5-flash"


def test_model_for_kind_free_defaults_when_nothing_set(monkeypatch):
    """Palier GRATUIT sans aucun secret : tâche profonde → gemini-3.5-flash (le
    meilleur flash gratuit), tâche rapide → gemini-2.5-flash (quota journalier
    large). Ne renvoie JAMAIS None : un mail a toujours un modèle valide."""
    from src.ai_brain import decision_engine as de
    _clear_all(monkeypatch)
    assert de._model_for_kind("morning") == "gemini-3.5-flash"
    assert de._model_for_kind("weekly") == "gemini-3.5-flash"
    assert de._model_for_kind("evening") == "gemini-2.5-flash"
    assert de._model_for_kind("macro_regime") == "gemini-2.5-flash"


def test_bot_not_routed_by_engine():
    """Le bot est indépendant : pas d'entrée 'bot' dans le routeur du moteur."""
    from src.ai_brain import decision_engine as de
    assert "bot" not in de._MODEL_ENV_BY_KIND


# ── le modèle par tâche est réellement transmis au client ─────────────────
class _SpyClient:
    def __init__(self):
        self.models_used = []

    def generate_json(self, prompt, *, temperature=0.4, model=None):
        self.models_used.append(model)
        return {"ok": True}


def test_safe_json_passes_task_model(monkeypatch):
    """AUTO : GEMINI_MODEL_DEEP suffit pour que l'hebdo tourne en pro."""
    from src.ai_brain.decision_engine import DecisionEngine
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "gemini-2.5-pro")
    spy = _SpyClient()
    eng = DecisionEngine(client=spy)
    out = eng.generate_weekly(timestamp="t", data={}, week_state={})
    assert out == {"ok": True}
    assert spy.models_used == ["gemini-2.5-pro"]


def test_generate_morning_routes_pro_and_regime_fast(monkeypatch):
    """AUTO : le matin (analyse) → pro, sa passe régime (rapide) → flash, avec
    le seul GEMINI_MODEL_DEEP."""
    from src.ai_brain.decision_engine import DecisionEngine
    _clear_all(monkeypatch)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_MODEL_DEEP", "gemini-2.5-pro")
    spy = _SpyClient()
    eng = DecisionEngine(client=spy)
    eng.generate_morning(timestamp="t", data={}, portfolio_data={},
                         evening_state={})
    # 1er appel = passe régime (flash, tâche rapide), 2e = analyse matin (pro).
    assert spy.models_used == ["gemini-2.5-flash", "gemini-2.5-pro"]


# ── GeminiClient : le model par appel prime, le repli reste le filet ──────
def test_client_with_fallback_honours_primary_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    # Évite l'import réseau google.genai en neutralisant __init__.
    from src.ai_brain import gemini_client as gc

    class _C(gc.GeminiClient):
        def __init__(self):
            self.model_name = "gemini-2.5-flash"
            self.fallback_model = "gemini-2.5-flash"

    c = _C()
    seen = {}

    def _call(model):
        seen["model"] = model
        return "ok"

    assert c._with_fallback(_call, primary="gemini-2.5-pro") == "ok"
    assert seen["model"] == "gemini-2.5-pro"


def test_client_fallback_triggers_on_primary_failure():
    from src.ai_brain import gemini_client as gc

    class _C(gc.GeminiClient):
        def __init__(self):
            self.model_name = "gemini-2.5-pro"
            self.fallback_model = "gemini-2.5-flash"

    c = _C()
    calls = []

    def _call(model):
        calls.append(model)
        if model == "gemini-2.5-pro":
            raise RuntimeError("boom")
        return "recovered"

    # primaire routé = pro → échoue → repli flash.
    assert c._with_fallback(_call, primary="gemini-2.5-pro") == "recovered"
    assert calls == ["gemini-2.5-pro", "gemini-2.5-flash"]
