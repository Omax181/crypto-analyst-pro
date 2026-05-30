"""Source whale tracking : flux ETH vers les exchanges via Etherscan V2.

REMPLACE Arkham (offline) par un traçage smart-money léger et gratuit. On
surveille les adresses de dépôt connues des grands exchanges (Binance, Coinbase,
Kraken, OKX) et on détecte les gros transferts ENTRANTS récents.

Logique analytique :
- Gros flux ENTRANT vers un exchange = potentielle pression VENDEUSE
  (les whales déposent avant de vendre).
- Absence de gros flux = pas de signal vendeur particulier.

Limites assumées (transparence) :
- Ne couvre que l'ETH natif (pas les ERC-20 ni les autres chaînes).
- Liste d'adresses exchange non exhaustive (hot wallets principaux).
- Un dépôt n'est pas toujours suivi d'une vente (peut être OTC, custody...).

Etherscan V2 free tier : 5 calls/sec, 100k/jour. Dégradation gracieuse totale.
"""

from __future__ import annotations

import os
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://api.etherscan.io/v2/api"
_CHAIN_ID = "1"

# Hot wallets de dépôt connus (adresses publiques largement documentées).
_EXCHANGE_WALLETS = {
    "0x28C6c06298d514Db089934071355E5743bf21d60": "Binance 14",
    "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549": "Binance 15",
    "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d": "Binance 16",
    "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3": "Coinbase 1",
    "0x503828976D22510aad0201ac7EC88293211D23Da": "Coinbase 2",
    "0xddFAbCdc4D8FfC6d5beaf154f18B778f892A0740": "Coinbase 3",
    "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2": "Kraken 1",
    "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b": "OKX 1",
}

# Seuil "gros transfert" en ETH (~ $1M à $2.5k/ETH ≈ 400 ETH ; on prend 200).
_WHALE_THRESHOLD_ETH = 200.0
_LOOKBACK_HOURS = 24


def _to_eth(wei_str: str) -> float:
    """Convertit une valeur Wei (string) en ETH."""
    try:
        return int(wei_str) / 1e18
    except (TypeError, ValueError):
        return 0.0


def get_exchange_inflows() -> dict[str, Any]:
    """Détecte les gros flux ETH entrants vers les exchanges sur 24h.

    Returns:
        Dict ``{available, large_inflows_count, total_eth_in, top_transfers,
        interpretation, reason}``. ``available=False`` si Etherscan indisponible.
    """
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not key:
        return {"available": False, "reason": "pas de clé Etherscan"}

    def _fetch() -> dict[str, Any]:
        import time

        cutoff_ts = time.time() - _LOOKBACK_HOURS * 3600
        large_transfers: list[dict[str, Any]] = []
        total_in = 0.0

        # On limite à 3 wallets par run pour rester sous le quota (les plus gros).
        for addr, label in list(_EXCHANGE_WALLETS.items())[:3]:
            data = get_json(
                _BASE,
                params={
                    "chainid": _CHAIN_ID,
                    "module": "account",
                    "action": "txlist",
                    "address": addr,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 100,
                    "sort": "desc",
                    "apikey": key,
                },
            )
            if not isinstance(data, dict) or data.get("status") != "1":
                continue
            for tx in data.get("result", []):
                try:
                    ts = float(tx.get("timeStamp", 0))
                except (TypeError, ValueError):
                    continue
                if ts < cutoff_ts:
                    break  # trié desc : tout le reste est plus ancien
                # Transfert ENTRANT = 'to' == adresse exchange.
                if (tx.get("to") or "").lower() != addr.lower():
                    continue
                eth = _to_eth(tx.get("value", "0"))
                if eth >= _WHALE_THRESHOLD_ETH:
                    total_in += eth
                    large_transfers.append(
                        {
                            "exchange": label,
                            "eth": round(eth, 1),
                            "from": (tx.get("from") or "")[:10] + "…",
                            "hash": (tx.get("hash") or "")[:12] + "…",
                        }
                    )

        large_transfers.sort(key=lambda t: t["eth"], reverse=True)
        return {
            "available": True,
            "large_inflows_count": len(large_transfers),
            "total_eth_in": round(total_in, 1),
            "top_transfers": large_transfers[:5],
            "threshold_eth": _WHALE_THRESHOLD_ETH,
            "lookback_hours": _LOOKBACK_HOURS,
        }

    try:
        result = CACHE.get_or_compute("whale:eth_inflows", 1800, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Whale tracking échoué : %s", exc)
        return {"available": False, "reason": str(exc)}

    if result.get("available"):
        n = result.get("large_inflows_count", 0)
        if n == 0:
            result["interpretation"] = (
                "aucun gros dépôt ETH détecté vers les exchanges surveillés sur "
                "24h · pas de signal vendeur whale particulier"
            )
        else:
            result["interpretation"] = (
                f"{n} gros dépôt(s) ETH (≥{int(_WHALE_THRESHOLD_ETH)} ETH) vers "
                f"exchanges sur 24h, total {result['total_eth_in']:.0f} ETH · "
                "pression vendeuse potentielle à surveiller (non confirmée)"
            )
    return result
