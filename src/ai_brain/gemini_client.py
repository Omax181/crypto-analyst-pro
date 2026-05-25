"""Client Gemini : wrapper robuste autour de l'API Google Generative AI.

Points clés :
- Modèle configurable via ``GEMINI_MODEL`` (défaut ``gemini-2.5-flash``, qui
  est le modèle free-tier fiable en 2026 ; ``gemini-2.5-pro`` est restreint au
  tier payant ou à un quota très faible selon la région).
- ``generate`` : texte simple.
- ``generate_json`` : force une sortie JSON et la parse.
- ``generate_with_search`` : active le grounding Google Search (géopolitique).
- Dégradation gracieuse : en cas d'épuisement de quota, lève une exception
  claire que l'orchestrateur peut attraper.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiQuotaError(RuntimeError):
    """Levée quand le quota Gemini est épuisé."""


class GeminiClient:
    """Wrapper minimal et robuste pour Gemini."""

    def __init__(self, model: Optional[str] = None) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY manquante.")
        self.model_name = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)

        import google.generativeai as genai

        genai.configure(api_key=api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(self.model_name)
        logger.info("GeminiClient initialisé (modèle %s).", self.model_name)

    def generate(self, prompt: str, *, temperature: float = 0.6) -> str:
        """Génère une réponse texte.

        Args:
            prompt: prompt complet.
            temperature: créativité (0-1).

        Returns:
            Le texte généré.

        Raises:
            GeminiQuotaError: si le quota est épuisé.
        """
        try:
            resp = self._model.generate_content(
                prompt,
                generation_config={"temperature": temperature},
            )
            return resp.text or ""
        except Exception as exc:  # noqa: BLE001
            self._raise_if_quota(exc)
            raise

    def generate_json(
        self, prompt: str, *, temperature: float = 0.4
    ) -> dict[str, Any]:
        """Génère et parse une réponse JSON.

        Force ``response_mime_type=application/json`` quand supporté, avec
        fallback sur un nettoyage manuel des backticks.

        Returns:
            Le dict parsé (``{}`` si parsing impossible).
        """
        try:
            resp = self._model.generate_content(
                prompt,
                generation_config={
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                },
            )
            text = resp.text or "{}"
        except Exception as exc:  # noqa: BLE001
            self._raise_if_quota(exc)
            # Certains modèles/versions ne supportent pas response_mime_type :
            # on retente en texte libre.
            logger.warning("generate_json : fallback texte libre (%s).", exc)
            text = self.generate(prompt, temperature=temperature)

        return self._parse_json(text)

    def generate_with_search(self, prompt: str) -> tuple[str, list[str]]:
        """Génère une réponse avec grounding Google Search.

        Returns:
            Tuple ``(texte, sources)``. ``sources`` est une liste d'URLs/titres
            extraits des métadonnées de grounding (best effort).
        """
        try:
            # L'outil de recherche Google s'active via la déclaration d'outil.
            model = self._genai.GenerativeModel(
                self.model_name,
                tools="google_search_retrieval",
            )
            resp = model.generate_content(prompt)
            text = resp.text or ""
            sources = self._extract_grounding_sources(resp)
            return text, sources
        except Exception as exc:  # noqa: BLE001
            self._raise_if_quota(exc)
            logger.warning("generate_with_search indisponible : %s", exc)
            # Fallback : génération sans grounding.
            return self.generate(prompt), []

    # ----------------------------- helpers ------------------------------- #
    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse du JSON en tolérant les backticks et préambules."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if "\n" in cleaned:
                first, rest = cleaned.split("\n", 1)
                cleaned = rest if first.lower().startswith("json") else cleaned
        # Tente d'isoler le premier objet JSON.
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
        logger.error("Impossible de parser le JSON Gemini.")
        return {}

    @staticmethod
    def _extract_grounding_sources(resp: Any) -> list[str]:
        """Extrait les sources de grounding (best effort, tolérant aux schémas)."""
        sources: list[str] = []
        try:
            for cand in getattr(resp, "candidates", []) or []:
                meta = getattr(cand, "grounding_metadata", None)
                if not meta:
                    continue
                for chunk in getattr(meta, "grounding_chunks", []) or []:
                    web = getattr(chunk, "web", None)
                    if web and getattr(web, "uri", None):
                        title = getattr(web, "title", "") or web.uri
                        sources.append(f"{title} — {web.uri}")
        except Exception:  # noqa: BLE001
            pass
        return sources[:8]

    @staticmethod
    def _raise_if_quota(exc: Exception) -> None:
        """Convertit une erreur de quota en ``GeminiQuotaError`` explicite."""
        msg = str(exc).lower()
        if "quota" in msg or "429" in msg or "resource_exhausted" in msg:
            raise GeminiQuotaError(
                "Quota Gemini épuisé. Réessaie plus tard ou passe au tier payant."
            ) from exc
