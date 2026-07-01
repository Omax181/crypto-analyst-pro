"""Cache TTL simple en mémoire pour économiser les quotas API.

Au sein d'un même run (un rapport), plusieurs modules peuvent demander les
mêmes données (ex. prix BTC). Ce cache évite les appels redondants. Il est
volatile : il disparaît à la fin du process GitHub Actions, ce qui est le
comportement voulu (chaque run repart sur des données fraîches).
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TTLCache:
    """Cache clé/valeur avec expiration par entrée."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        """Récupère une valeur si présente et non expirée, sinon ``None``."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        """Stocke une valeur avec un TTL en secondes."""
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def get_or_compute(
        self, key: str, ttl: int, compute: Callable[[], Any]
    ) -> Any:
        """Retourne la valeur cachée ou la calcule via ``compute`` et la cache.

        Args:
            key: clé de cache.
            ttl: durée de vie en secondes.
            compute: fonction sans argument produisant la valeur.

        Returns:
            La valeur (cachée ou fraîchement calculée).
        """
        cached = self.get(key)
        if cached is not None:
            logger.debug("Cache HIT: %s", key)
            return cached
        logger.debug("Cache MISS: %s", key)
        value = compute()
        self.set(key, value, ttl)
        return value


# Instance partagée à l'échelle du process.
CACHE = TTLCache()
