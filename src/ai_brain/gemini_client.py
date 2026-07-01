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
    def __init__(
        self,
        model: Optional[str] = None,
        *,
        fallback_model: Optional[str] = None,
    ) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY manquante.")
        self.model_name = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        # Repli optionnel : si le modèle primaire est indisponible/saturé (ex. le
        # bot vise gemini-2.5-pro mais son palier gratuit est momentanément plein),
        # on bascule AUTOMATIQUEMENT sur ce modèle. Une erreur de quota n'étant
        # jamais facturée, ce repli garantit « meilleure qualité quand dispo,
        # jamais de panne » sans coût. None = pas de repli (comportement v18).
        self.fallback_model = fallback_model
        from google import genai
        self._client = genai.Client(api_key=api_key)
        logger.info(
            "GeminiClient V2 initialisé (modèle %s%s) — retry 4x sur transitoire.",
            self.model_name,
            f", repli {self.fallback_model}" if self.fallback_model else "")

    def _with_fallback(self, call: Any) -> Any:
        """Exécute ``call(model)`` sur le modèle primaire ; en cas d'échec (quota,
        transitoire épuisé, modèle indisponible…), réessaie UNE fois sur le modèle
        de repli s'il est défini et différent. ``call`` reçoit le nom du modèle et
        peut lever GeminiQuotaError / _GeminiTransientError / toute autre erreur."""
        try:
            return call(self.model_name)
        except Exception as exc:  # noqa: BLE001
            if self.fallback_model and self.fallback_model != self.model_name:
                logger.warning(
                    "Modèle %s indisponible (%s) → repli sur %s.",
                    self.model_name, type(exc).__name__, self.fallback_model)
                return call(self.fallback_model)
            raise

    @retry(**_RETRY_KWARGS)
    def _call_text(self, prompt: str, temperature: float, model: str) -> str:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature))
            return resp.text or ""
        except Exception as exc:
            raise _classify(exc) from exc

    @retry(**_RETRY_KWARGS)
    def _call_json(self, prompt: str, temperature: float, model: str) -> str:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature, response_mime_type="application/json"))
            return resp.text or "{}"
        except Exception as exc:
            raise _classify(exc) from exc

    def generate(self, prompt: str, *, temperature: float = 0.6) -> str:
        return self._with_fallback(
            lambda m: self._call_text(prompt, temperature, m))

    def generate_json(self, prompt: str, *, temperature: float = 0.4) -> dict[str, Any]:
        try:
            return self._parse_json(self._with_fallback(
                lambda m: self._call_json(prompt, temperature, m)))
        except GeminiQuotaError:
            raise
        except Exception as exc:
            logger.warning("generate_json fallback texte : %s", exc)
            return self._parse_json(self.generate(prompt, temperature=temperature))

    def generate_with_search(self, prompt: str) -> tuple[str, list[str]]:
        from google.genai import types

        def _call(model: str) -> str:
            try:
                resp = self._client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())]))
                return resp.text or ""
            except Exception as exc:
                raise _classify(exc) from exc

        try:
            return self._with_fallback(_call), []
        except GeminiQuotaError:
            raise
        except Exception as exc:  # noqa: BLE001
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
