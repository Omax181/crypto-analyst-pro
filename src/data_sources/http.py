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

from typing import Any, Optional

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
    resp = requests.request(
        method,
        url,
        params=params,
        headers=headers,
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
