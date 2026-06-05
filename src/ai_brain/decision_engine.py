"""Moteur de décision V2 : ponts entre données collectées et Gemini.

Construit le prompt approprié (matin/soir/hebdo), appelle Gemini en mode
JSON, et renvoie un payload dégradé si l'IA est indisponible (le rapport part
quand même avec les données brutes).
"""

from __future__ import annotations

from typing import Any

import yaml

from src.ai_brain.gemini_client import GeminiClient, GeminiQuotaError
from src.ai_brain.prompts.evening_prompt import build_evening_prompt
from src.ai_brain.prompts.morning_prompt import build_morning_prompt
from src.ai_brain.prompts.weekly_prompt import build_weekly_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DecisionEngine:
    """Encapsule les appels Gemini pour les 4 types de rapport."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        # Init "gardé" : si GeminiClient() lève (ex. GEMINI_API_KEY absente ou
        # expirée), on NE propage PAS l'exception. Sinon elle remonterait hors
        # de run_morning/evening/weekly -> main() -> return 1 -> job ROUGE et
        # AUCUN mail envoyé. On mémorise l'erreur et on laisse _safe_json
        # basculer sur le payload dégradé (le rapport part quand même, en mode
        # secours). C'est le comportement attendu par le cahier des charges :
        # une IA indisponible dégrade le rapport, elle ne le bloque jamais.
        self._init_error: str | None = None
        if client is not None:
            self.client = client
        else:
            try:
                self.client = GeminiClient()
            except Exception as exc:  # noqa: BLE001
                self.client = None
                self._init_error = str(exc)
                logger.error(
                    "Init GeminiClient impossible (%s) : mode dégradé activé.", exc
                )

    def generate_morning(
        self, *, timestamp: str, data: dict[str, Any],
        portfolio_data: dict[str, Any], evening_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport du matin en 2 passes chaînées (V10).

        Passe 1 : régime macro (cadre). Passe 2 : analyse par actif + recos,
        contrainte par le régime de la passe 1. La passe 1 est best-effort :
        si elle échoue, la passe 2 s'exécute quand même (sans cadre macro).
        Le contrat de dégradation est préservé : client absent / quota épuisé /
        échecs répétés → payload dégradé (le rapport part toujours).
        """
        if self.client is None:
            logger.error("Client Gemini indisponible : payload dégradé (morning).")
            return self._degraded(
                "morning", data, self._init_error or "Client IA non initialisé."
            )

        # PASSE 1 — régime macro (optionnelle, jamais bloquante).
        macro_regime = self._macro_regime_pass(timestamp, data)
        if macro_regime:
            data = {**data, "macro_regime": macro_regime}

        # PASSE 2 — rapport complet, cadré par le régime macro.
        portfolio_yaml = yaml.safe_dump(
            portfolio_data, allow_unicode=True, sort_keys=False
        )
        prompt = build_morning_prompt(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml,
            evening_state=evening_state, macro_regime=macro_regime or None,
        )
        payload = self._safe_json(prompt, data, kind="morning")
        # B6 — expose le verdict de la PASSE 1 dans le payload pour que le rendu
        # puisse l'afficher même si la passe 2 ne l'a pas recopié fidèlement.
        if isinstance(payload, dict) and macro_regime:
            payload["macro_regime_pass1"] = macro_regime
        return payload

    def _macro_regime_pass(
        self, timestamp: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Passe 1 : évalue le régime macro. Best-effort, jamais d'exception.

        Renvoie le dict régime, ou ``{}`` si indisponible (la passe 2 continuera
        sans cadre macro plutôt que de bloquer). Le quota éventuel sera de toute
        façon re-géré (dégradation propre) par la passe 2.
        """
        try:
            from src.ai_brain.prompts.macro_regime_prompt import (
                build_macro_regime_prompt,
            )

            prompt = build_macro_regime_prompt(timestamp=timestamp, data=data)
            result = self.client.generate_json(prompt)
            if isinstance(result, dict) and result:
                logger.info(
                    "Passe 1 régime macro : %s (conf. %s%%)",
                    result.get("regime", "?"), result.get("confidence_pct", "?"),
                )
                return result
            logger.warning("Passe 1 régime macro : réponse vide, passe 2 sans cadre.")
        except Exception as exc:  # noqa: BLE001 — passe optionnelle, on n'échoue jamais ici
            logger.warning("Passe 1 régime macro indisponible (%s), on continue.", exc)
        return {}

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

    def _safe_json(
        self, prompt: str, data: dict[str, Any], *, kind: str, attempts: int = 2
    ) -> dict[str, Any]:
        """Appelle Gemini en JSON avec retry + payload dégradé.

        Conformément à l'exigence « ne pas dégrader à la première erreur » :
        on tente ``attempts`` fois (2 par défaut) avant de basculer en dégradé,
        EN PLUS du retry interne du client sur erreurs transitoires (503…). Une
        réponse vide ou un échec non-quota déclenche une nouvelle tentative ;
        seul le quota épuisé dégrade immédiatement (réessayer serait inutile).
        """
        if self.client is None:
            # Le constructeur a échoué (clé manquante/expirée). On dégrade
            # proprement au lieu de crasher : le rapport est envoyé en secours.
            logger.error("Client Gemini indisponible : payload dégradé (%s).", kind)
            return self._degraded(
                kind, data, self._init_error or "Client IA non initialisé."
            )
        last_reason = "Génération IA indisponible."
        for attempt in range(1, attempts + 1):
            try:
                result = self.client.generate_json(prompt)
                if result:
                    return result
                last_reason = "Réponse IA vide."
                logger.warning(
                    "Gemini (%s) tentative %d/%d : réponse vide — nouvelle tentative.",
                    kind, attempt, attempts,
                )
            except GeminiQuotaError:
                logger.error("Quota Gemini épuisé : payload dégradé (%s).", kind)
                return self._degraded(kind, data, "Quota IA épuisé.")
            except Exception as exc:  # noqa: BLE001
                last_reason = "Génération IA indisponible."
                logger.exception(
                    "Échec Gemini (%s) tentative %d/%d : %s", kind, attempt, attempts, exc
                )
        logger.error(
            "Gemini (%s) : %d tentatives épuisées → payload dégradé.", kind, attempts
        )
        return self._degraded(kind, data, last_reason)

    @staticmethod
    def _degraded(kind: str, data: dict[str, Any], reason: str) -> dict[str, Any]:
        """Payload minimal de secours, propre à chaque type de rapport."""
        base = {
            "header": {"title": "Veille crypto", "subtitle": "rapport dégradé"},
            "_degraded": True,
            "footer": {"note": f"{reason} Données brutes disponibles."},
        }
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
