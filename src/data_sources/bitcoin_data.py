"""On-chain BTC FRAIS et gratuit : bitcoin-data.com (BGeometrics), sans clé.

v21 (Logs#1, audit M#15) — RAISON D'ÊTRE. Les deux sources on-chain existantes
sont défaillantes sur les runners GitHub Actions (IP datacenter US) :
  • CoinMetrics community API  → 403 (geo/IP-block datacenter) ;
  • miroir CSV GitHub CoinMetrics → joignable mais EN RETARD (~3-5 semaines :
    dernière ligne 2026-05-24 vérifiée), donc un MVRV affiché « au 23/05 ».

bitcoin-data.com expose des endpoints PUBLICS, SANS CLÉ, à J-1, qui répondent
depuis les IP datacenter (vérifié). On l'utilise comme surcouche de FRAÎCHEUR
pour le MVRV BTC (le signal de valorisation le plus regardé), avec dégradation
gracieuse totale : toute erreur → ``available=False`` et on retombe sur
CoinMetrics (API puis miroir) sans rien casser.

Limite assumée : ce fournisseur ne couvre que BTC. ETH garde la chaîne
CoinMetrics (API → miroir) avec étiquetage honnête de la fraîcheur.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://bitcoin-data.com/v1"


def _num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_btc_mvrv() -> dict[str, Any]:
    """MVRV BTC frais (J-1) via bitcoin-data.com. ``available=False`` si KO.

    Returns:
        Dict ``{available, mvrv, mvrv_zscore, as_of, source}``. Les clés
        absentes sont omises (jamais inventées).
    """

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "source": "bitcoin-data.com"}
        mvrv_raw = get_json(f"{_BASE}/mvrv/last")
        if isinstance(mvrv_raw, dict):
            mvrv = _num(mvrv_raw.get("mvrv"))
            as_of = str(mvrv_raw.get("d") or "")[:10] or None
            if mvrv is not None:
                out["available"] = True
                out["mvrv"] = round(mvrv, 4)
                if as_of:
                    out["as_of"] = as_of
        # Z-score (bonus contextuel : extrême si |z| élevé) — best-effort.
        z_raw = get_json(f"{_BASE}/mvrv-zscore/last")
        if isinstance(z_raw, dict):
            z = _num(z_raw.get("mvrvZscore"))
            if z is not None:
                out["mvrv_zscore"] = round(z, 3)
        return out

    try:
        return CACHE.get_or_compute("bitcoin_data:btc_mvrv", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.info("bitcoin-data.com indisponible : %s", exc)
        return {"available": False, "source": "bitcoin-data.com"}
