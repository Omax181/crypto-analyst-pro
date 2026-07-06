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


def get_btc_onchain_extras() -> dict[str, Any]:
    """v27 (SO4/TH7) — SOPR, NUPL, NVT BTC FRAIS (J-1), gratuits, sans clé.

    Probés live : ``/v1/sopr/last``, ``/v1/nupl/last``, ``/v1/nvt/last``
    répondent à J-1 (vs miroir CoinMetrics daté de plusieurs semaines).
    Lectures FR incluses pour le rendu direct :
        • SOPR < 1 = ventes à perte (capitulation) ; > 1 = prises de profit ;
        • NUPL < 0 = marché sous l'eau ; 0-0.25 = espoir ; > 0.5 = euphorie ;
        • NVT haut = valorisation chère vs volume transféré on-chain.

    Returns:
        ``{available, sopr, nupl, nvt, as_of, readings: [str], source}``.
    """

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "source": "bitcoin-data.com"}
        readings: list[str] = []
        as_of = None
        for ep, key in (("sopr", "sopr"), ("nupl", "nupl"), ("nvt", "nvt")):
            raw = get_json(f"{_BASE}/{ep}/last")
            if not isinstance(raw, dict):
                continue
            v = _num(raw.get(key))
            if v is None:
                continue
            out[key] = round(v, 4)
            as_of = as_of or (str(raw.get("d") or "")[:10] or None)
        if out.get("sopr") is not None:
            s = out["sopr"]
            readings.append(
                f"SOPR {s:.3f} — " + ("ventes à perte (capitulation en cours)"
                                      if s < 0.99 else
                                      "équilibre pertes/profits" if s <= 1.01
                                      else "prises de profit dominantes"))
        if out.get("nupl") is not None:
            n = out["nupl"]
            readings.append(
                f"NUPL {n:.2f} — " + ("marché sous l'eau (capitulation)"
                                      if n < 0 else
                                      "zone espoir/peur (bas de cycle historique)"
                                      if n < 0.25 else
                                      "optimisme" if n < 0.5 else "euphorie"))
        if out.get("nvt") is not None:
            readings.append(f"NVT {out['nvt']:.1f}")
        if readings:
            out["available"] = True
            out["readings"] = readings
            if as_of:
                out["as_of"] = as_of
        return out

    try:
        return CACHE.get_or_compute("bitcoin_data:btc_extras", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.info("bitcoin-data.com extras indisponibles : %s", exc)
        return {"available": False, "source": "bitcoin-data.com"}


# v26 (B1) — adresses actives BTC FRAÎCHES. Le miroir CoinMetrics accuse des
# semaines de retard : la tuile « Adresses actives BTC » du matin affichait un
# absolu daté sans le dire (audit A2). Deux couches gratuites, sans clé,
# joignables depuis les runners (probées) :
#   1. blockchain.info /charts/n-unique-addresses (série 10 j → valeur + Δ7j) ;
#   2. bitcoin-data.com /v1/active-addresses/last (valeur J-1, pas de tendance).
_BLOCKCHAIN_CHART = "https://api.blockchain.info/charts/n-unique-addresses"


def get_btc_active_addresses() -> dict[str, Any]:
    """Adresses actives BTC fraîches (valeur + tendance 7j si possible).

    Returns:
        Dict ``{available, value, trend_7d_pct?, as_of?, source}``. Les clés
        absentes sont omises (jamais inventées).
    """

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "source": "blockchain.info"}
        # Couche 1 — série blockchain.info : valeur du jour + Δ vs J-7.
        chart = get_json(_BLOCKCHAIN_CHART,
                         params={"timespan": "10days", "format": "json"})
        values = (chart or {}).get("values") if isinstance(chart, dict) else None
        if isinstance(values, list) and len(values) >= 8:
            try:
                last = values[-1]
                prev = values[-8]
                cur = _num(last.get("y"))
                old = _num(prev.get("y"))
                if cur and cur > 0:
                    out["available"] = True
                    out["value"] = int(cur)
                    if old and old > 0:
                        out["trend_7d_pct"] = round((cur - old) / old * 100, 1)
                    ts = _num(last.get("x"))
                    if ts:
                        from datetime import datetime, timezone
                        out["as_of"] = datetime.fromtimestamp(
                            ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except (KeyError, TypeError, ValueError):
                pass
        if out["available"]:
            return out
        # Couche 2 — bitcoin-data.com : valeur J-1 (sans tendance).
        raw = get_json(f"{_BASE}/active-addresses/last")
        if isinstance(raw, dict):
            val = _num(raw.get("activeAddresses"))
            if val and val > 0:
                out = {"available": True, "value": int(val),
                       "source": "bitcoin-data.com"}
                as_of = str(raw.get("d") or "")[:10]
                if as_of:
                    out["as_of"] = as_of
        return out

    try:
        return CACHE.get_or_compute("bitcoin_data:btc_active_addr", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.info("Adresses actives BTC fraîches indisponibles : %s", exc)
        return {"available": False, "source": "blockchain.info"}
