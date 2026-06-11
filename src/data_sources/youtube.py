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
    """Résout l'ID d'une chaîne, avec cache pour économiser le quota.

    Ordre : (1) mapping explicite channel_ids, (2) résolution par handle
    (forHandle = 1 unité de quota), (3) recherche par nom (100 unités, dernier
    recours). Les IDs résolus sont mis en cache 7 jours.
    """
    explicit = (_YT_CONF.get("channel_ids") or {}).get(name)
    if explicit:
        return explicit

    def _do_resolve() -> str | None:
        # 2) Tentative par handle : "Crypto pour tous" -> "@cryptopourtous"
        #    forHandle ne coûte que 1 unité de quota (vs 100 pour search).
        #    Un handle YouTube ne contient que [a-z0-9._-] : on translittère les
        #    accents (HugoDécrypte -> hugodecrypte) et on retire le reste
        #    (Heu?reka -> heureka), sinon forHandle échoue systématiquement.
        import re
        import unicodedata

        normalized = unicodedata.normalize("NFD", name)
        normalized = "".join(c for c in normalized if not unicodedata.combining(c))
        handle = re.sub(r"[^a-z0-9._-]", "", normalized.lstrip("@").lower())
        data = get_json(
            f"{_BASE}/channels",
            params={"part": "id", "forHandle": f"@{handle}", "key": key},
        )
        items = (data or {}).get("items", [])
        if items:
            return items[0]["id"]
        # 3) Fallback : recherche par nom (coûteux mais robuste).
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

    # Cache 7 jours : les channel IDs ne changent jamais.
    return CACHE.get_or_compute(f"yt:chanid:{name}", 604800, _do_resolve)


def _recent_videos(
    channel_id: str, key: str, max_age_hours: int, n: int
) -> list[dict[str, str]]:
    """Liste les vidéos d'une chaîne publiées dans la fenêtre récente.

    Returns:
        Liste de dicts ``{id, title, description}``. Le titre et la description
        servent de corpus de SECOURS quand les transcripts sont bloqués (cas
        fréquent depuis les IP datacenter de GitHub Actions).

    v14.1 — ÉCONOMIE DE QUOTA ×100 : on interroge d'abord la playlist
    « uploads » de la chaîne via ``playlistItems.list`` (1 unité de quota) au
    lieu de ``search.list`` (100 unités). Avec 8 chaînes × 3 runs/jour, search
    consommait ~2 400 unités/jour sur les 10 000 du quota gratuit — première
    cause des erreurs « quotaExceeded » remontées dans les logs. L'ID de la
    playlist uploads se déduit du channel ID (préfixe UC → UU, convention
    stable de l'API v3). ``search.list`` reste en REPLI si la playlist échoue.

    Note format : l'API YouTube exige un timestamp RFC 3339 ; les microsecondes
    de ``isoformat()`` provoquent des 400 intermittents -> on les retire et on
    force le suffixe ``Z``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    published_after = (
        cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    # --- Chemin 1 : playlist uploads (1 unité) -------------------------------
    if isinstance(channel_id, str) and channel_id.startswith("UC"):
        uploads_id = "UU" + channel_id[2:]
        data = get_json(
            f"{_BASE}/playlistItems",
            params={
                "part": "snippet",
                "playlistId": uploads_id,
                "maxResults": max(n * 3, 5),  # marge : on filtre par date après
                "key": key,
            },
        )
        items = (data or {}).get("items", [])
        out: list[dict[str, str]] = []
        for it in items:
            snippet = it.get("snippet") or {}
            vid = ((snippet.get("resourceId") or {}).get("videoId"))
            pub = snippet.get("publishedAt") or ""
            if not vid or not pub:
                continue
            try:
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                continue
            if pub_dt < cutoff:
                continue
            out.append(
                {
                    "id": vid,
                    "title": (snippet.get("title") or "").strip(),
                    "description": (snippet.get("description") or "").strip(),
                }
            )
            if len(out) >= n:
                break
        if out or items:
            # Playlist répondue : items vides = vraiment aucune vidéo récente
            # (pas la peine de payer 100 unités de search pour confirmer).
            return out

    # --- Repli : search.list (100 unités) ------------------------------------
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
    out = []
    for it in items:
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        snippet = it.get("snippet") or {}
        out.append(
            {
                "id": vid,
                "title": (snippet.get("title") or "").strip(),
                "description": (snippet.get("description") or "").strip(),
            }
        )
    return out


def _get_transcript(video_id: str, languages: list[str]) -> str | None:
    """Récupère le transcript d'une vidéo (texte concaténé) ou ``None``.

    Compatibilité youtube-transcript-api :
      - <= 0.6.x : méthode de classe ``YouTubeTranscriptApi.get_transcript()``
        renvoyant une liste de dicts ``{"text": ...}`` ;
      - >= 1.0   : ``get_transcript`` SUPPRIMÉE -> instance ``.fetch()``
        renvoyant un ``FetchedTranscript`` itérable de snippets ``.text``.
    Sans cette compat, la 1.x lève AttributeError (avalé) -> corpus toujours
    vide -> YouTube jamais cité dans les rapports (bug v14 d'origine).
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            segments = YouTubeTranscriptApi.get_transcript(
                video_id, languages=languages
            )
            return " ".join(s.get("text", "") for s in segments if s.get("text"))
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
        parts = [getattr(s, "text", "") or "" for s in fetched]
        text = " ".join(p for p in parts if p)
        return text or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Transcript indisponible pour %s : %s", video_id, exc)
        return None


def get_youtube_corpus() -> dict[str, Any]:
    """Construit un corpus de contenus YouTube récents pour synthèse Gemini.

    Stratégie en 2 niveaux :
      1. transcripts complets (riches) quand ils sont accessibles ;
      2. REPLI titres + descriptions (YouTube Data API) quand les transcripts
         sont bloqués — YouTube bloque fréquemment l'endpoint transcript depuis
         les IP datacenter (GitHub Actions). Sans ce repli, la source était
         marquée indisponible et n'apparaissait JAMAIS dans les analyses.

    Returns:
        Dict ``{available, transcripts: [str], video_count, videos_seen,
        mode}``. ``video_count`` = vidéos avec transcript complet ;
        ``videos_seen`` = vidéos récentes détectées ; ``mode`` =
        ``"transcripts"`` / ``"titles"`` / ``"mixte"`` (transparence du repli).
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
        metadata_notes: list[str] = []
        video_count = 0
        videos_seen = 0
        for name in _all_channel_names():
            channel_id = _resolve_channel_id(name, key)
            if not channel_id:
                logger.info("YouTube : chaîne '%s' non résolue, ignorée.", name)
                continue
            for vid in _recent_videos(channel_id, key, max_age, per_channel):
                videos_seen += 1
                text = _get_transcript(vid["id"], languages)
                if text:
                    # Tronquer chaque transcript à ~4000 caractères.
                    transcripts.append(text[:4000])
                    video_count += 1
                elif vid.get("title"):
                    note = f"[Vidéo récente] {vid['title']}"
                    if vid.get("description"):
                        note += f" — {vid['description']}"
                    metadata_notes.append(note[:600])

        if transcripts and metadata_notes:
            # Mixte : transcripts riches + titres des vidéos sans transcript.
            transcripts.extend(metadata_notes[:6])
            mode = "mixte"
        elif transcripts:
            mode = "transcripts"
        elif metadata_notes:
            # Repli intégral : titres + descriptions (transcripts bloqués).
            transcripts = metadata_notes[:16]
            mode = "titles"
            logger.info(
                "YouTube : transcripts inaccessibles, repli titres/descriptions "
                "(%d vidéos).", len(transcripts),
            )
        else:
            mode = None

        return {
            "available": bool(transcripts),
            "transcripts": transcripts,
            "video_count": video_count,
            "videos_seen": videos_seen,
            "mode": mode,
        }

    return CACHE.get_or_compute("youtube:corpus", 21600, _fetch)
