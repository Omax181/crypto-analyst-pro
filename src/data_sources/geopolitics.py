"""Source géopolitique : synthèse via le grounding Google Search de Gemini.

Une requête unique demande à Gemini de résumer les événements géopolitiques
et macro majeurs des dernières 24h affectant les marchés crypto, avec sources.
"""

from __future__ import annotations

from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_QUERY = (
    "Quels sont les événements géopolitiques et macroéconomiques majeurs des "
    "dernières 24 heures susceptibles d'affecter les marchés crypto et risk-on "
    "(décisions de banques centrales, tensions géopolitiques, régulation crypto, "
    "mouvements sur le dollar/obligations) ? Réponds en 4-6 puces factuelles et "
    "datées, en français, avec une courte lecture d'impact crypto pour chacune."
)


def get_geopolitics() -> dict[str, Any]:
    """Récupère une synthèse géopolitique/macro via Gemini + Google Search.

    Returns:
        Dict ``{available, summary, sources}``. ``available=False`` si Gemini
        ou le grounding échoue.
    """
    # Import tardif pour éviter une dépendance circulaire au chargement.
    from src.ai_brain.gemini_client import GeminiClient

    def _fetch() -> dict[str, Any]:
        try:
            client = GeminiClient()
            text, sources = client.generate_with_search(_QUERY)
            return {
                "available": bool(text),
                "summary": text,
                "sources": sources,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Géopolitique (Gemini search) indisponible : %s", exc)
            return {"available": False, "summary": "", "sources": []}

    return CACHE.get_or_compute("geopolitics", 3600, _fetch)
