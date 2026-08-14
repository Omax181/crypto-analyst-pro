"""Session LLM — SPEC V31 §10 (I36, I40, I54, I55, I56).

Trois protections, toutes absentes de la v30 :

I54  BUDGET DE RETRY BORNÉ PAR LE TEMPS RESTANT. La v30 pouvait enchaîner
     cinq tentatives par appel sans jamais regarder l'horloge du job : un run
     tué par le runner ne produisait ni mail ni trace. Ici, on ne LANCE un
     appel que s'il reste de quoi le terminer.
I55  REPLI VÉRIFIÉ AU DÉMARRAGE. Un modèle de repli identique au modèle
     primaire n'est pas un repli : c'est une illusion de résilience. On le
     détecte au démarrage et on le déclare comme tel.
I56  TRAÇABILITÉ DU MODÈLE. Le modèle réellement utilisé par passe est écrit
     dans le résumé de run. En v30, seule l'URL httpx le révélait, par accident.

Le LLM ne produit AUCUN fait : sa sortie est un dictionnaire de champs
éditoriaux, validé par ``core.content`` selon le principe du REJET.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.ai_brain.prompts import v31_prompts
from src.core.runlog import RunSummary
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Durée maximale d'un job de rapport (aligné sur le ``timeout-minutes`` des
# workflows). Sert de borne haute au budget de retry.
DEFAULT_JOB_BUDGET_S = 900.0
# Durée observée d'un appel + marge. En-dessous, on ne LANCE pas l'appel :
# lancer un appel qu'on ne peut pas terminer coûte le temps et perd le mail.
CALL_RESERVE_S = 90.0


class LLMUnavailable(RuntimeError):
    """Aucune rédaction possible. Le rapport se rend en mode déterministe."""


@dataclass
class ModelPlan:
    """Modèles retenus pour ce run, et honnêteté du repli (I55)."""
    primary: str
    fallback: Optional[str]
    fallback_is_real: bool
    note: Optional[str] = None


def resolve_models() -> ModelPlan:
    """Lit la configuration modèle et VÉRIFIE que le repli en est un."""
    primary = (os.environ.get("GEMINI_MODEL") or "").strip() or "gemini-2.5-flash"
    fallback = (os.environ.get("GEMINI_FALLBACK_MODEL") or "").strip() or None
    if fallback is None:
        return ModelPlan(primary, None, False,
                         "aucun modèle de repli configuré")
    if fallback == primary:
        return ModelPlan(primary, None, False,
                         "modèle de repli identique au modèle primaire — "
                         "ce n'est pas un repli")
    return ModelPlan(primary, fallback, True)


class LLMSession:
    """Une session par run. Porte le budget temps et la traçabilité."""

    def __init__(self, summary: RunSummary, *,
                 job_budget_s: float = DEFAULT_JOB_BUDGET_S,
                 client: Any = None) -> None:
        self.summary = summary
        self.deadline = time.monotonic() + float(job_budget_s)
        self.plan = resolve_models()
        if self.plan.note:
            logger.warning("Modèles : %s", self.plan.note)
            summary.add_degradation(f"modèle : {self.plan.note}")
        self._client = client
        self._client_failed = False

    # ── budget ────────────────────────────────────────────────────────────
    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def can_call(self) -> bool:
        """I54 — n'autorise un appel que s'il reste de quoi le terminer."""
        return self.remaining_s >= CALL_RESERVE_S

    # ── client ────────────────────────────────────────────────────────────
    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_failed:
            raise LLMUnavailable("client LLM indisponible")
        try:
            from src.ai_brain.gemini_client import GeminiClient
            self._client = GeminiClient(model=self.plan.primary,
                                        fallback_model=self.plan.fallback)
        except Exception as exc:                                # noqa: BLE001
            self._client_failed = True
            raise LLMUnavailable(f"client LLM indisponible : {exc}") from exc
        return self._client

    # ── rédaction ─────────────────────────────────────────────────────────
    def compose(self, kind: str, *, fact_context: list[dict[str, Any]],
                engine_summary: dict[str, Any],
                external_items: Optional[list[dict[str, Any]]] = None,
                ) -> dict[str, Any]:
        """Rédige les champs éditoriaux du run. Ne lève que ``LLMUnavailable``.

        La sortie n'est PAS validée ici : la validation appartient au contrat de
        contenu, qui rejette sans réparer.
        """
        if not self.can_call():
            self.summary.add_degradation(
                "rédaction éditoriale abandonnée : temps de job insuffisant")
            raise LLMUnavailable("budget temps insuffisant pour un appel LLM")

        prompt = v31_prompts.build(kind, fact_context=fact_context,
                                   engine_summary=engine_summary,
                                   external_items=external_items)
        client = self._get_client()
        with self.summary.phase("llm"):
            try:
                raw = client.generate_json(prompt, temperature=0.5)
            except Exception as exc:                            # noqa: BLE001
                self.summary.add_degradation(
                    "rédaction éditoriale indisponible")
                raise LLMUnavailable(str(exc)) from exc
        used = getattr(client, "last_used_model", None) or self.plan.primary
        self.summary.note_model(kind, used)                     # I56
        if self.plan.fallback and used == self.plan.fallback:
            self.summary.add_degradation(
                "rapport rédigé par le modèle de repli")
        if not isinstance(raw, dict):
            raise LLMUnavailable("réponse LLM non exploitable")
        allowed = set(v31_prompts.FIELDS_BY_KIND.get(kind, ()))
        # Le modèle ne peut pas élargir son propre périmètre : toute clé hors
        # table est écartée AVANT le contrat de contenu.
        return {k: v for k, v in raw.items() if k in allowed}
