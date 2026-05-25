"""Source YouTube : transcripts des vidéos récentes des chaînes de référence.

Pipeline :
1. YouTube Data API v3 -> résoudre les chaînes et lister les vidéos < 24h.
2. youtube-transcript-api (gratuit, sans quota) -> récupérer les transcripts.
3. Les transcripts bruts sont renvoyés pour être SYNTHÉTISÉS par Gemini.

IMPORTANT : on ne renvoie jamais le nom de la chaîne associé au contenu dans
le rapport final ; la synthèse Gemini est globale et anonymisée.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_YT_CONF = load_config("youtube_channels")
_BASE = _SOURCES["endpoints"]["youtube"]


def _api_key() -> str:
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def _all_channel_names() -> list[str]:
    """Aplati toutes les catégories de chaînes en une seule liste."""
    names: list[str] = []
    for group in (_YT_CONF.get("youtube_channels") or {}).values():
        names.extend(group)
    return names


def _resolve_channel_id(name: str, key: str) -> str | None:
    """Résout l'ID d'une chaîne par recherche, ou via le mapping explicite."""
    explicit = (_YT_CONF.get("channel_ids") or {}).get(name)
    if explicit:
        return explicit
    data = get_json(
        f"{_BASE}/search",
        params={
            "part": "snippet",
            "q": name,
            "type": "channel",
            "maxResults": 1,
            "key": key,
        },
    )
    items = (data or {}).get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]
    return None


def _recent_video_ids(channel_id: str, key: str, max_age_hours: int, n: int) -> list[str]:
    """Liste les IDs de vidéos d'une chaîne publiées dans la fenêtre récente."""
    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()
    data = get_json(
        f"{_BASE}/search",
        params={
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "type": "video",
            "publishedAfter": published_after,
            "maxResults": n,
            "key": key,
        },
    )
    items = (data or {}).get("items", [])
    return [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]


def _get_transcript(video_id: str, languages: list[str]) -> str | None:
    """Récupère le transcript d'une vidéo (texte concaténé) ou ``None``."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        return " ".join(s["text"] for s in segments)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Transcript indisponible pour %s : %s", video_id, exc)
        return None


def get_youtube_corpus() -> dict[str, Any]:
    """Construit un corpus de transcripts récents pour synthèse Gemini.

    Returns:
        Dict ``{available, transcripts: [str], video_count}``. Les transcripts
        sont tronqués pour rester dans des limites raisonnables de tokens.
    """
    key = _api_key()
    if not key:
        logger.info("YouTube : pas de clé, corpus ignoré.")
        return {"available": False, "transcripts": [], "video_count": 0}

    settings = _YT_CONF.get("settings", {})
    max_age = int(settings.get("max_age_hours", 24))
    per_channel = int(settings.get("max_videos_per_channel", 2))
    languages = settings.get("transcript_languages", ["fr", "en"])

    def _fetch() -> dict[str, Any]:
        transcripts: list[str] = []
        video_count = 0
        for name in _all_channel_names():
            channel_id = _resolve_channel_id(name, key)
            if not channel_id:
                continue
            for vid in _recent_video_ids(channel_id, key, max_age, per_channel):
                text = _get_transcript(vid, languages)
                if text:
                    # Tronquer chaque transcript à ~4000 caractères.
                    transcripts.append(text[:4000])
                    video_count += 1
        return {
            "available": bool(transcripts),
            "transcripts": transcripts,
            "video_count": video_count,
        }

    return CACHE.get_or_compute("youtube:corpus", 21600, _fetch)
