"""Tests v19.1 — refonte de l'analyse du bot conversationnel (3 leviers).

Couvre :
  • Levier 1 — recherche web ACTIVE PAR DÉFAUT (opt-out pédagogie/PTF), au lieu
    d'une liste blanche de mots-clés qui ne se déclenchait jamais.
  • Levier 2 — modèle gemini-2.5-pro en primaire avec REPLI flash automatique
    (gratuit, zéro panne) : routage de GeminiClient._with_fallback.
  • Levier 3 — prompt repensé : raisonnement par mécanisme, pas de salutation
    rituelle, gabarit libre (tout en conservant les ancres de l'audit Partie 6).
Aucun appel réseau réel.
"""

from __future__ import annotations

from src.telegram_bot import assistant


# --------------------------------------------------------------------------- #
# Levier 1 — recherche par défaut (opt-out)
# --------------------------------------------------------------------------- #
def test_search_on_by_default_for_real_market_questions() -> None:
    """Les vraies questions d'Omar (actualité/marché vivant) déclenchent une
    recherche — c'est exactement ce qui manquait dans les captures."""
    assert assistant._needs_research(
        "Que penses-tu de la semaine prochaine sur les cryptos ?")
    assert assistant._needs_research("Pourquoi le marché est baissier ?")
    assert assistant._needs_research(
        "MSTR est en baisse, ça peut contraindre Saylor à vendre du BTC ?")
    assert assistant._needs_research(
        "Y a-t-il eu des accords de paix entre USA et Iran ?")


def test_search_optout_for_pedagogy_and_pure_portfolio() -> None:
    """Pédagogie pure et math du PTF n'ont pas besoin de recherche (non-régression
    des assertions de l'audit v18.1)."""
    assert not assistant._needs_research("explique-moi le MVRV")
    assert not assistant._needs_research("combien vaut mon portefeuille ?")
    assert not assistant._needs_research("c'est quoi le funding rate ?")


# --------------------------------------------------------------------------- #
# Levier 2 — modèle pro primaire + repli flash
# --------------------------------------------------------------------------- #
def _bare_client(model: str, fallback: str | None):
    """Instancie GeminiClient SANS __init__ (pas de clé API / réseau) pour tester
    la pure logique de routage _with_fallback."""
    from src.ai_brain.gemini_client import GeminiClient
    client = object.__new__(GeminiClient)
    client.model_name = model
    client.fallback_model = fallback
    return client


def test_fallback_switches_to_flash_on_transient() -> None:
    from src.ai_brain.gemini_client import _GeminiTransientError
    client = _bare_client("gemini-2.5-pro", "gemini-2.5-flash")
    seen: list[str] = []

    def call(model: str) -> str:
        seen.append(model)
        if model == "gemini-2.5-pro":
            raise _GeminiTransientError("503 high demand")
        return f"ok:{model}"

    assert client._with_fallback(call) == "ok:gemini-2.5-flash"
    assert seen == ["gemini-2.5-pro", "gemini-2.5-flash"]


def test_fallback_switches_to_flash_on_quota() -> None:
    """Une saturation du palier gratuit pro (quota) doit basculer sur flash —
    et un quota n'est jamais facturé, donc coût nul."""
    from src.ai_brain.gemini_client import GeminiQuotaError
    client = _bare_client("gemini-2.5-pro", "gemini-2.5-flash")

    def call(model: str) -> str:
        if model == "gemini-2.5-pro":
            raise GeminiQuotaError("quota épuisé")
        return "ok-flash"

    assert client._with_fallback(call) == "ok-flash"


def test_no_fallback_when_identical_or_absent() -> None:
    client = _bare_client("gemini-2.5-flash", None)
    assert client._with_fallback(lambda m: f"ok:{m}") == "ok:gemini-2.5-flash"
    # Repli identique au primaire → pas de double tentative (l'erreur remonte).
    same = _bare_client("gemini-2.5-flash", "gemini-2.5-flash")
    raised = {"n": 0}

    def boom(model: str) -> str:
        raised["n"] += 1
        raise RuntimeError("down")

    try:
        same._with_fallback(boom)
    except RuntimeError:
        pass
    assert raised["n"] == 1  # une seule tentative, pas de repli inutile


def test_answer_targets_pro_with_flash_fallback(monkeypatch) -> None:
    """answer() construit le client en VISANT pro avec repli flash."""
    captured: dict = {}

    class _Fake:
        def generate(self, prompt, *, temperature=0.6):
            return "ok"

        def generate_with_search(self, prompt):
            return ("ok", [])

    def _factory(*args, **kwargs):
        captured.update(kwargs)
        return _Fake()

    import src.ai_brain.gemini_client as gc
    monkeypatch.setattr(gc, "GeminiClient", _factory)
    # Question pédagogique → chemin plain (peu importe, on inspecte la construction).
    assistant.answer("explique-moi le MVRV", {}, [])
    assert captured.get("model") == "gemini-2.5-pro"
    assert captured.get("fallback_model") == "gemini-2.5-flash"


# --------------------------------------------------------------------------- #
# Levier 3 — prompt repensé
# --------------------------------------------------------------------------- #
def test_prompt_demands_mechanism_and_bans_ritual_greeting() -> None:
    p = assistant._SYSTEM_PROMPT
    low = p.lower()
    # Raisonnement par mécanisme + steelman (le cœur de la valeur ajoutée).
    assert "mécanisme" in low
    assert "steelman" in low
    # Interdiction explicite de la salutation rituelle (« Bonjour Omar »).
    assert "salutation" in low
    # Croisement multi-marchés explicitement demandé.
    assert "croise les marchés" in low or "croise les marches" in low


def test_prompt_keeps_audit_anchors() -> None:
    """Non-régression : les ancres testées ailleurs restent présentes."""
    p = assistant._SYSTEM_PROMPT
    assert "analyste crypto personnel" in p
    assert "NON-INVENTION" in p
    assert "INDÉPENDANCE ANALYTIQUE" in p
    assert "ne valide PAS automatiquement" in p
