"""Client Gemini V2 : utilise google.genai (nouvelle API, remplace google.generativeai)."""
from __future__ import annotations
import json, os
from typing import Any, Optional
from src.utils.logger import get_logger
logger = get_logger(__name__)
_DEFAULT_MODEL = "gemini-2.5-flash"

class GeminiQuotaError(RuntimeError):
    pass

class GeminiClient:
    def __init__(self, model: Optional[str] = None) -> None:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY manquante.")
        self.model_name = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        from google import genai
        self._client = genai.Client(api_key=api_key)
        logger.info("GeminiClient V2 initialisé (modèle %s).", self.model_name)

    def generate(self, prompt: str, *, temperature: float = 0.6) -> str:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature)
            )
            return resp.text or ""
        except Exception as exc:
            self._raise_if_quota(exc); raise

    def generate_json(self, prompt: str, *, temperature: float = 0.4) -> dict[str, Any]:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json"
                )
            )
            return self._parse_json(resp.text or "{}")
        except Exception as exc:
            self._raise_if_quota(exc)
            logger.warning("generate_json fallback texte: %s", exc)
            return self._parse_json(self.generate(prompt, temperature=temperature))

    def generate_with_search(self, prompt: str) -> tuple[str, list[str]]:
        from google.genai import types
        try:
            resp = self._client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            return resp.text or "", []
        except Exception as exc:
            self._raise_if_quota(exc)
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
                try: return json.loads(cleaned[s:e+1])
                except: pass
        logger.error("Impossible de parser le JSON Gemini."); return {}

    @staticmethod
    def _raise_if_quota(exc: Exception) -> None:
        msg = str(exc).lower()
        if any(k in msg for k in ["quota", "429", "resource_exhausted"]):
            raise GeminiQuotaError("Quota Gemini épuisé.") from exc
