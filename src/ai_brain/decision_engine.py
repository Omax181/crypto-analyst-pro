"""Moteur de décision : transforme les données collectées en rapport structuré.

Fait le pont entre la collecte de données (orchestrée par main.py) et Gemini :
construit le prompt approprié, appelle Gemini en mode JSON, valide la sortie.
"""

from __future__ import annotations

from typing import Any

from src.ai_brain.gemini_client import GeminiClient, GeminiQuotaError
from src.ai_brain.prompts.report_prompts import (
    build_evening_prompt,
    build_intraday_prompt,
    build_morning_prompt,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DecisionEngine:
    """Encapsule les appels Gemini pour les différents types de rapport."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    def morning_report(
        self, *, timestamp: str, data: dict[str, Any], portfolio_yaml: str
    ) -> dict[str, Any]:
        """Produit le rapport du matin (dict conforme au schéma de sortie)."""
        prompt = build_morning_prompt(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml
        )
        return self._safe_json(prompt, fallback_style=data.get("report_style", "calm"))

    def evening_report(
        self,
        *,
        timestamp: str,
        data: dict[str, Any],
        portfolio_yaml: str,
        morning_digest: str = "",
    ) -> dict[str, Any]:
        """Produit le rapport du soir."""
        prompt = build_evening_prompt(
            timestamp=timestamp,
            data=data,
            portfolio_yaml=portfolio_yaml,
            morning_digest=morning_digest,
        )
        return self._safe_json(prompt, fallback_style=data.get("report_style", "calm"))

    def intraday_alert(
        self, *, timestamp: str, triggers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Produit une alerte intra-day concise."""
        prompt = build_intraday_prompt(timestamp=timestamp, triggers=triggers)
        result = self._safe_json(prompt, fallback_style="active")
        # Garantir les clés attendues par l'email d'alerte.
        result.setdefault("title", "Alerte crypto")
        result.setdefault("body", "Mouvement notable détecté sur le portefeuille.")
        result.setdefault("severity", "warning")
        return result

    def _safe_json(self, prompt: str, *, fallback_style: str) -> dict[str, Any]:
        """Appelle Gemini en mode JSON avec gestion d'erreurs."""
        try:
            result = self.client.generate_json(prompt)
            if not result:
                raise ValueError("réponse JSON vide")
            return result
        except GeminiQuotaError:
            logger.error("Quota Gemini épuisé : rapport dégradé.")
            return self._degraded_payload(fallback_style, "Quota IA épuisé.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Échec génération Gemini : %s", exc)
            return self._degraded_payload(fallback_style, "Génération IA indisponible.")

    @staticmethod
    def _degraded_payload(style: str, reason: str) -> dict[str, Any]:
        """Payload minimal quand l'IA est indisponible (le rapport part quand même)."""
        return {
            "report_style": style,
            "header": {"title": "Veille crypto", "subtitle": "rapport dégradé"},
            "essentiel": [f"⚠️ {reason} Données brutes seules disponibles."],
            "positions": [],
            "footer": "Rapport partiel. Réessaie au prochain créneau.",
            "_degraded": True,
        }
