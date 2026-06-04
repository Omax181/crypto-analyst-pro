"""Client Gemini V2 : utilise google.genai (nouvelle API).

Retry policy (Q5 — anti-dégradation sur 503 transitoire) :
  - Les erreurs Gemini sont classées en deux familles :
      * QUOTA       (quota épuisé, 429) → ``GeminiQuotaError`` immédiate, non
        retryable (problème permanent jusqu'au reset).
      * TRANSITOIRE (5xx, "overload", "high demand", "deadline", "unavailable",
        timeout, internal error) → retentée jusqu'à 4 fois avec backoff
        exponentiel (2s, 4s, 8s, 16s plafonné). Sur 503 isolé, Gemini répond
        au 2e ou 3e essai au lieu de déclencher le mode dégradé.
  - Toute autre erreur est levée telle quelle (pas de boucle infinie sur un
    bug réel).
"""
from __future__ import annotations
import json, os
from typing import Any, Optional

from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type,
    before_sleep_log,
)

from src.utils.logger import get_logger
logger = get_logger(__name__)
_DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiQuotaError(RuntimeError):
    """Quota épuisé — pas de retry (problème permanent jusqu'au reset)."""


class _GeminiTransientError(RuntimeError):
    """Erreur passagère (5xx, overload, timeout) — retryable."""


_TRANSIENT_MARKERS = (
    "503", "500", "502", "504", "deadline", "unavailable",
    "overload", "high demand", "timeout", "internal error",
)
_QUOTA_MARKERS = ("quota", "429", "resource_exhausted")


def _classify(exc: Exception) -> Exception:
    """Reclassifie une exception Gemini en quota / transient / autre.

    Renvoie l'exception à lever (GeminiQuotaError / _GeminiTransientError /
    exception originale). Le caller doit faire ``raise _classify(exc) from exc``.
    """
    msg = str(exc).lower()
    if any(k in msg for k in _QUOTA_MARKERS):
        return GeminiQuotaError("Quota Gemini épuisé.")
    if any(k in msg for k in _TRANSIENT_MARKERS):
        return _GeminiTransientError(f"Gemini transient: {exc}")
    return exc


_RETRY_KWARGS = dict(
    retry=retry_if_exception_type(_GeminiTransientError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    before_sleep=before_sleep_log(logger, 30),  # log avant chaque retry (WARNING)
    reraise=True,
)


class GeminiClient:
    def __init__(self, model: Optional[str] = None) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY manquante.")
        self.model_name = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        from google import genai
        self._client = genai.Client(api_key=api_key)
        logger.info("GeminiClient V2 initialisé (modèle %s) — retry 4x sur transitoire.", self.model_name)

    @retry(**_RETRY_KWARGS)
    def _call_text(self, prompt: str, temperature: float) -> str:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature))
            return resp.text or ""
        except Exception as exc:
            raise _classify(exc) from exc

    @retry(**_RETRY_KWARGS)
    def _call_json(self, prompt: str, temperature: float) -> str:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature, response_mime_type="application/json"))
            return resp.text or "{}"
        except Exception as exc:
            raise _classify(exc) from exc

    def generate(self, prompt: str, *, temperature: float = 0.6) -> str:
        return self._call_text(prompt, temperature)

    def generate_json(self, prompt: str, *, temperature: float = 0.4) -> dict[str, Any]:
        try:
            return self._parse_json(self._call_json(prompt, temperature))
        except GeminiQuotaError:
            raise
        except Exception as exc:
            logger.warning("generate_json fallback texte : %s", exc)
            return self._parse_json(self.generate(prompt, temperature=temperature))

    def generate_with_search(self, prompt: str) -> tuple[str, list[str]]:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]))
            return resp.text or "", []
        except Exception as exc:
            classified = _classify(exc)
            if isinstance(classified, GeminiQuotaError):
                raise classified from exc
            logger.warning("generate_with_search indisponible : %s", exc)
            return self.generate(prompt), []

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        cleaned = text.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            s, e = cleaned.find("{"), cleaned.rfind("}")
            if s != -1 and e > s:
                try:
                    return json.loads(cleaned[s:e + 1])
                except json.JSONDecodeError:
                    pass
        logger.error("Impossible de parser le JSON Gemini.")
        return {}

    @staticmethod
    def _raise_if_quota(exc: Exception) -> None:
        """Compat backward (utilisé ailleurs dans le code). Reclassifie."""
        classified = _classify(exc)
        if isinstance(classified, GeminiQuotaError):
            raise classified from exc
