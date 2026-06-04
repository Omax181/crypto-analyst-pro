"""Extension YouTube : focus chaîne prioritaire "Crypto Pour Tous".

Récupère le transcript de la dernière vidéo <24h de la chaîne prioritaire et
le fait résumer par Gemini en 1 paragraphe pour le bloc "News marché".
S'appuie sur le module ``youtube`` existant pour la résolution/transcripts.
"""

from __future__ import annotations

from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_CONF = load_config("youtube_channels")
_PRIORITY = _CONF.get("settings", {}).get("priority_channel", "Crypto pour tous")


def get_cpt_summary() -> dict[str, Any]:
    """Résume la dernière vidéo <24h de la chaîne prioritaire via Gemini.

    Returns:
        Dict ``{available, channel, summary}``. ``available=False`` si pas de
        vidéo récente ou si Gemini/YouTube indisponible.
    """

    def _fetch() -> dict[str, Any]:
        try:
            from src.data_sources import youtube

            corpus = youtube.get_youtube_corpus()
        except Exception as exc:  # noqa: BLE001
            logger.warning("YouTube CPT : corpus indisponible : %s", exc)
            return {"available": False, "channel": _PRIORITY, "summary": ""}

        transcripts = corpus.get("transcripts") or []
        if not transcripts:
            return {"available": False, "channel": _PRIORITY, "summary": ""}

        # Résumé via Gemini (1 paragraphe, anonymisé).
        try:
            from src.ai_brain.gemini_client import GeminiClient

            client = GeminiClient()
            joined = "\n\n".join(transcripts[:2])[:8000]
            prompt = (
                "Résume en 1 paragraphe factuel (français) les points crypto "
                "saillants de ces transcripts d'analyse récents, sans citer "
                "d'auteur ni de chaîne :\n\n" + joined
            )
            summary = client.generate(prompt, temperature=0.4)
            return {
                "available": bool(summary),
                "channel": _PRIORITY,
                "summary": summary.strip(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("YouTube CPT : résumé Gemini échoué : %s", exc)
            return {"available": False, "channel": _PRIORITY, "summary": ""}

    return CACHE.get_or_compute("youtube:cpt", 21600, _fetch)
