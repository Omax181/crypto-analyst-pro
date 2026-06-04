"""On-chain institutionnel : Coin Metrics Community API (gratuit, SANS clé).

Fournit les métriques on-chain « de pro » absentes des sources gratuites
basiques (blockchain.info / Etherscan) :
  - MVRV  (CapMVRVCur)   : ratio cap. marché / cap. réalisée → sur/sous-évaluation
  - NVT   (NVTAdj)       : valorisation réseau / volume on-chain (P/E du réseau)
  - Realized Price       : CapRealUSD / SplyCur (prix de revient moyen du marché)
  - Active addresses     : AdrActCnt (adoption réseau)

Endpoint communautaire : ``https://api.coinmetrics.io/v4/timeseries/asset-metrics``.
Aucune authentification requise (cf. doc « Coin Metrics Community Data »).
Rate limit communautaire : 10 req / 6 s par IP — on fait 1 seule requête (batch
BTC+ETH) par run, donc large marge.

Dégradation gracieuse totale : toute erreur réseau / métrique absente → la clé
correspondante est simplement omise, jamais d'exception propagée.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://api.coinmetrics.io/v4/timeseries/asset-metrics"

# Métriques community (IDs validés, stables pour BTC/ETH).
_METRICS = ["PriceUSD", "CapMVRVCur", "NVTAdj", "CapRealUSD", "SplyCur", "AdrActCnt"]

# Mapping ticker PTF -> id Coin Metrics (minuscule).
_CM_IDS = {"BTC": "btc", "ETH": "eth"}


def _mvrv_zone(mvrv: Optional[float]) -> Optional[str]:
    """Traduit le MVRV en zone de marché lisible."""
    if mvrv is None:
        return None
    if mvrv < 1.0:
        return "sous-évalué (capitulation)"
    if mvrv < 2.0:
        return "neutre"
    if mvrv < 3.5:
        return "élevé"
    return "surchauffe"


def _to_float(value: Any) -> Optional[float]:
    """Convertit en float tolérant (Coin Metrics renvoie des strings)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_onchain_metrics() -> dict[str, Any]:
    """Récupère les métriques on-chain avancées BTC + ETH (Coin Metrics community).

    Returns:
        Dict ``{available, source, assets: {SYM: {price, mvrv, mvrv_zone, nvt,
        realized_price, realized_price_ratio, active_addresses,
        active_addresses_trend_pct}}}``. Clés omises si la métrique manque.
    """

    def _fetch() -> dict[str, Any]:
        start = (datetime.now(timezone.utc) - timedelta(days=12)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        raw = get_json(
            _BASE,
            params={
                "assets": ",".join(_CM_IDS.values()),
                "metrics": ",".join(_METRICS),
                "frequency": "1d",
                "start_time": start,
                "page_size": 1000,
                "pretty": "false",
            },
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
            return {"available": False, "source": "coinmetrics"}

        # Regroupe les lignes (1 ligne = 1 asset à 1 date) par asset, triées.
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in raw["data"]:
            if not isinstance(row, dict):
                continue
            a = row.get("asset")
            if a:
                by_asset.setdefault(a, []).append(row)

        id_to_sym = {v: k for k, v in _CM_IDS.items()}
        out_assets: dict[str, Any] = {}
        for cm_id, rows in by_asset.items():
            sym = id_to_sym.get(cm_id)
            if not sym or not rows:
                continue
            rows.sort(key=lambda r: str(r.get("time", "")))
            last = rows[-1]
            prev = rows[-8] if len(rows) >= 8 else rows[0]

            price = _to_float(last.get("PriceUSD"))
            mvrv = _to_float(last.get("CapMVRVCur"))
            nvt = _to_float(last.get("NVTAdj"))
            cap_real = _to_float(last.get("CapRealUSD"))
            supply = _to_float(last.get("SplyCur"))
            adr = _to_float(last.get("AdrActCnt"))
            adr_prev = _to_float(prev.get("AdrActCnt"))

            entry: dict[str, Any] = {}
            if price is not None:
                entry["price"] = round(price, 2)
            if mvrv is not None:
                entry["mvrv"] = round(mvrv, 2)
                entry["mvrv_zone"] = _mvrv_zone(mvrv)
            if nvt is not None:
                entry["nvt"] = round(nvt, 1)
            # Realized price = cap réalisée / supply (prix de revient marché).
            if cap_real is not None and supply:
                rp = cap_real / supply
                entry["realized_price"] = round(rp, 2)
                if price is not None and rp:
                    # > 1 : marché en profit latent ; < 1 : en perte latente.
                    entry["realized_price_ratio"] = round(price / rp, 2)
            if adr is not None:
                entry["active_addresses"] = int(adr)
                if adr_prev:
                    entry["active_addresses_trend_pct"] = round(
                        (adr - adr_prev) / adr_prev * 100, 1
                    )
            if entry:
                out_assets[sym] = entry

        if not out_assets:
            return {"available": False, "source": "coinmetrics"}
        return {"available": True, "source": "coinmetrics", "assets": out_assets}

    try:
        return CACHE.get_or_compute("coinmetrics:onchain", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Coin Metrics indisponible : %s", exc)
        return {"available": False, "source": "coinmetrics"}
