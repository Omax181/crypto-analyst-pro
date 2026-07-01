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


# --------------------------------------------------------------------------- #
# v18 (Chantier E #5) — wallets stratégiques spécifiques
# --------------------------------------------------------------------------- #
# Adresses PUBLIQUES largement documentées (fondations / trésoreries). Leurs
# mouvements sont des signaux plus forts que les flux génériques. Liste volontai-
# rement courte et vérifiable (ETH natif uniquement, via Etherscan free tier).
# NB : un mouvement de fondation n'est pas toujours une vente (grants, staking,
# custody) — on signale le MOUVEMENT, l'interprétation reste prudente.
_STRATEGIC_WALLETS = {
    "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe": "Ethereum Foundation",
    "0x9bf4001d307dFd62B26A2F1307ee0C0307632d59": "Ethereum Foundation (dev)",
}
_STRATEGIC_THRESHOLD_ETH = 100.0


def get_strategic_wallet_activity() -> dict[str, Any]:
    """v18 (Chantier E #5) — mouvements récents de wallets stratégiques connus.

    Surveille une liste courte d'adresses publiques documentées (fondations,
    trésoreries) et détecte leurs gros transferts sur 24h. Renseignement
    actionnable : un mouvement de fondation est un signal fort (à interpréter
    prudemment). Dégradation gracieuse totale (free tier Etherscan).

    Returns:
        Dict ``{available, movements: [{wallet, label, eth, direction}],
        interpretation}``.
    """
    key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not key:
        return {"available": False, "reason": "ETHERSCAN_API_KEY absente"}

    def _fetch() -> dict[str, Any]:
        import time as _t

        movements: list[dict[str, Any]] = []
        now_s = int(_t.time())
        cutoff = now_s - _LOOKBACK_HOURS * 3600
        for addr, label in _STRATEGIC_WALLETS.items():
            raw = get_json(
                _BASE,
                params={
                    "chainid": _CHAIN_ID, "module": "account", "action": "txlist",
                    "address": addr, "startblock": 0, "endblock": 99999999,
                    "page": 1, "offset": 20, "sort": "desc", "apikey": key,
                },
            )
            rows = raw.get("result") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                continue
            for tx in rows:
                if not isinstance(tx, dict):
                    continue
                try:
                    ts = int(tx.get("timeStamp", 0))
                except (TypeError, ValueError):
                    continue
                if ts < cutoff:
                    break  # rows triées desc : au-delà du cutoff, stop
                eth = _to_eth(tx.get("value", "0"))
                if eth < _STRATEGIC_THRESHOLD_ETH:
                    continue
                direction = ("sortant"
                             if (tx.get("from", "").lower() == addr.lower())
                             else "entrant")
                movements.append({
                    "wallet": addr[:10] + "…", "label": label,
                    "eth": round(eth, 1), "direction": direction,
                })
        movements.sort(key=lambda m: m["eth"], reverse=True)
        if not movements:
            # Rien de notable : on ne surface pas un signal vide (cross_signals
            # n'ajoute que les signaux ``available``).
            return {"available": False, "reason": "aucun mouvement stratégique notable 24h"}
        # CORRECTIF v18.1 : on fournit la clé ``interpretation`` que cross_signals
        # (#5) lit comme ``reading``. Sans elle, le signal était ajouté sans aucune
        # lecture (jamais injecté dans les readings du prompt).
        top = movements[0]
        interpretation = (
            f"{len(movements)} mouvement(s) de wallets stratégiques sur "
            f"{_LOOKBACK_HOURS}h ; le plus important : {top['label']} "
            f"{top['eth']} ETH {top['direction']}. Renseignement à interpréter "
            "prudemment (un flux sortant de trésorerie peut précéder une "
            "distribution, un flux entrant une accumulation)."
        )
        return {"available": True, "movements": movements[:5],
                "interpretation": interpretation}

    try:
        result = CACHE.get_or_compute("whale:strategic", 1800, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wallets stratégiques échoué : %s", exc)
        return {"available": False, "reason": str(exc)}

    if result.get("available"):
        movs = result.get("movements", [])
        if not movs:
            result["interpretation"] = (
                "aucun mouvement notable des wallets stratégiques surveillés "
                "sur 24h"
            )
        else:
            _top = movs[0]
            result["interpretation"] = (
                f"{len(movs)} mouvement(s) de wallets stratégiques sur 24h, "
                f"dont {_top['label']} ({_top['eth']:.0f} ETH {_top['direction']}) "
                "· signal à interpréter prudemment (grant/staking/custody possible)"
            )
    return result
