"""Moteur de décision V2 : ponts entre données collectées et Gemini.

Construit le prompt approprié (matin/soir/hebdo/panic), appelle Gemini en mode
JSON, et renvoie un payload dégradé si l'IA est indisponible (le rapport part
quand même avec les données brutes).
"""

from __future__ import annotations

from typing import Any

import yaml

from src.ai_brain.gemini_client import GeminiClient, GeminiQuotaError
from src.ai_brain.prompts.evening_prompt import build_evening_prompt
from src.ai_brain.prompts.morning_prompt import build_morning_prompt
from src.ai_brain.prompts.panic_prompt import build_panic_prompt
from src.ai_brain.prompts.weekly_prompt import build_weekly_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DecisionEngine:
    """Encapsule les appels Gemini pour les 4 types de rapport."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    def generate_morning(
        self, *, timestamp: str, data: dict[str, Any],
        portfolio_data: dict[str, Any], evening_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport du matin."""
        portfolio_yaml = yaml.safe_dump(
            portfolio_data, allow_unicode=True, sort_keys=False
        )
        prompt = build_morning_prompt(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml,
            evening_state=evening_state,
        )
        return self._safe_json(prompt, data, kind="morning")

    def generate_evening(
        self, *, timestamp: str, data: dict[str, Any], morning_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport du soir."""
        prompt = build_evening_prompt(
            timestamp=timestamp, data=data, morning_state=morning_state
        )
        return self._safe_json(prompt, data, kind="evening")

    def generate_weekly(
        self, *, timestamp: str, data: dict[str, Any], week_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport hebdomadaire."""
        prompt = build_weekly_prompt(
            timestamp=timestamp, data=data, week_state=week_state
        )
        return self._safe_json(prompt, data, kind="weekly")

    def generate_panic(
        self, *, timestamp: str, triggers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Génère une alerte panic."""
        prompt = build_panic_prompt(timestamp=timestamp, triggers=triggers)
        result = self._safe_json(prompt, {}, kind="panic")
        result.setdefault("title", "Mouvement majeur détecté")
        result.setdefault("body", "; ".join(t["detail"] for t in triggers))
        result.setdefault("severity", "danger")
        return result

    def _safe_json(
        self, prompt: str, data: dict[str, Any], *, kind: str
    ) -> dict[str, Any]:
        """Appelle Gemini en JSON avec gestion d'erreurs + payload dégradé."""
        try:
            result = self.client.generate_json(prompt)
            if not result:
                raise ValueError("réponse JSON vide")
            return result
        except GeminiQuotaError:
            logger.error("Quota Gemini épuisé : payload dégradé (%s).", kind)
            return self._degraded(kind, data, "Quota IA épuisé.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Échec Gemini (%s) : %s", kind, exc)
            return self._degraded(kind, data, "Génération IA indisponible.")

    @staticmethod
    def _degraded(kind: str, data: dict[str, Any], reason: str) -> dict[str, Any]:
        """Payload minimal de secours, propre à chaque type de rapport."""
        base = {
            "header": {"title": "Veille crypto", "subtitle": "rapport dégradé"},
            "_degraded": True,
            "footer": {"note": f"{reason} Données brutes disponibles."},
        }
        if kind == "panic":
            return {"title": "Alerte", "body": reason, "severity": "warning"}
        if kind == "morning":
            base["essentiel"] = [f"⚠️ {reason}"]
            base["all_positions_summary"] = data.get("all_positions_summary", [])
            base["win_rate"] = data.get("win_rate", {})
        elif kind == "evening":
            base["delta_of_the_day"] = [f"⚠️ {reason}"]
        elif kind == "weekly":
            base["weekly_narrative"] = reason
            base["weekly_predictions_scoring"] = data.get("win_rate", {})
        return base
