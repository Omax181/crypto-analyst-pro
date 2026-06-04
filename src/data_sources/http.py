"""Helpers HTTP partagés par les data sources.

Centralise les requêtes ``requests`` avec :
- retry exponentiel (tenacity) sur erreurs réseau / 429 / 5xx ;
- timeout par défaut ;
- logging.

Chaque data source attrape ses propres exceptions et renvoie une valeur
dégradée (``None`` / dict avec ``available=False``) plutôt que de planter le
rapport entier — principe de robustesse du cahier des charges.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 15  # secondes

# En-têtes par défaut appliqués à TOUTES les requêtes du helper.
# Raison : sans User-Agent, ``requests`` envoie ``python-requests/x.y``, qui est
# explicitement REJETÉ (403) par plusieurs APIs utilisées ici :
#   - l'API GitHub exige un User-Agent (sinon 403 "administrative rules") ;
#   - CoinGecko public, blockchain.info, Yahoo Finance et de nombreux flux RSS
#     bloquent l'UA python-requests / l'absence d'UA.
# Un UA de type navigateur supprime la grande majorité de ces 403. Les en-têtes
# fournis par l'appelant (ex. clé d'API) sont prioritaires et ne sont jamais
# écrasés (merge "défaut puis appelant").
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) crypto-analyst-pro/8.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _merge_headers(headers: Optional[dict[str, str]]) -> dict[str, str]:
    """Fusionne les en-têtes par défaut avec ceux de l'appelant (appelant prioritaire)."""
    merged = dict(_DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    return merged

# Délai minimum (en secondes) entre 2 appels au même domaine — anti rate-limit.
# CoinGecko free demo = 30 req/min = 1 toutes les 2s, on prend 2.5s par sécurité.
_DOMAIN_THROTTLE = {
    "api.coingecko.com": 2.5,
    "pro-api.coingecko.com": 2.5,
    "api.llama.fi": 1.0,
    "lunarcrush.com": 6.0,  # free tier très limité
}
_last_call: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(url: str) -> None:
    """Bloque si un appel récent a été fait au même domaine."""
    host = urlparse(url).netloc
    delay = _DOMAIN_THROTTLE.get(host)
    if not delay:
        return
    with _throttle_lock:
        last = _last_call.get(host, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        _last_call[host] = time.monotonic()


class TransientHTTPError(Exception):
    """Erreur HTTP considérée comme temporaire (à retenter)."""


@retry(
    retry=retry_if_exception_type((requests.RequestException, TransientHTTPError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _request(
    method: str,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    json_body: Optional[dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    """Effectue une requête avec retry. Lève sur échec définitif."""
    _throttle(url)
    resp = requests.request(
        method,
        url,
        params=params,
        headers=_merge_headers(headers),
        json=json_body,
        timeout=timeout,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientHTTPError(f"{resp.status_code} sur {url}")
    resp.raise_for_status()
    return resp


def get_json(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Any]:
    """GET renvoyant le JSON, ou ``None`` en cas d'échec.

    Ne lève jamais : log l'erreur et renvoie ``None`` pour permettre la
    dégradation gracieuse côté appelant.
    """
    try:
        resp = _request("GET", url, params=params, headers=headers, timeout=timeout)
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - on veut tout capturer ici
        logger.warning("GET échoué : %s (%s)", url, exc)
        return None


def post_json(
    url: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Any]:
    """POST renvoyant le JSON, ou ``None`` en cas d'échec."""
    try:
        resp = _request(
            "POST", url, headers=headers, json_body=json_body, timeout=timeout
        )
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("POST échoué : %s (%s)", url, exc)
        return None


def get_text(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """GET renvoyant le corps texte brut (ex. flux RSS/XML), ou ``None``.

    Ne lève jamais : log et renvoie ``None`` pour la dégradation gracieuse.
    """
    try:
        resp = _request("GET", url, params=params, headers=headers, timeout=timeout)
        return resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("GET texte échoué : %s (%s)", url, exc)
        return None
