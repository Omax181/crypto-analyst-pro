"""Source dérivés : funding rate + open interest via l'API publique Binance Futures.

REMPLACE Coinglass (mort sur le free tier). L'API Binance Futures (fapi) est
publique, gratuite et sans clé. On récupère :
- le funding rate courant (premiumIndex) — indicateur clé de surchauffe perp ;
- l'historique récent de funding (moyenne sur 3 jours pour le contexte) ;
- l'open interest courant.

Lecture analytique fournie : un funding élevé et positif (> +0.05% par période
de 8h) signale un excès de longs (risque de purge / retournement baissier).
Un funding négatif signale un excès de shorts (potentiel short squeeze).

Dégradation gracieuse totale : si Binance Futures est inaccessible (geo-block,
réseau, symbole sans perp), renvoie ``{available: False}`` sans planter.
"""

from __future__ import annotations

from datetime import datetime, timezone

import os
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"].get("binance_futures", "https://fapi.binance.com/fapi/v1")
_BINANCE_SYMBOLS: dict[str, str] = _SOURCES.get("binance_symbols", {})

# Repli OKX : l'API publique Binance Futures (fapi) est géo-bloquée (451) depuis
# les runners GitHub hébergés aux US. L'API publique OKX (/api/v5/public) n'est
# pas géo-restreinte sur les endpoints marché et fournit funding + OI + mark.
# Convention instrument perp OKX : <BASE>-USDT-SWAP (ex. BTC-USDT-SWAP).
_OKX_BASE = "https://www.okx.com/api/v5/public"

# Funding facturé toutes les 8h sur Binance -> 3 paiements/jour.
_FUNDINGS_PER_DAY = 3
# Seuils d'interprétation (par période de 8h).
_HOT_LONG = 0.0005   # +0.05% : excès de longs marqué
_HOT_SHORT = -0.0005  # -0.05% : excès de shorts marqué


def _perp_symbol(symbol: str) -> Optional[str]:
    """Renvoie le symbole perp USDT-margined Binance pour un ticker (ou None)."""
    base = _BINANCE_SYMBOLS.get(symbol)
    if base:
        return base
    # Fallback : convention <TICKER>USDT (la plupart des perps Binance).
    if symbol and symbol.isalnum():
        return f"{symbol}USDT"
    return None


def _okx_inst(symbol: str) -> Optional[str]:
    """Renvoie l'instrument perp OKX pour un ticker (ex. BTC -> BTC-USDT-SWAP)."""
    if symbol and symbol.isalnum():
        return f"{symbol}-USDT-SWAP"
    return None


def _fetch_okx(symbol: str, inst: str) -> dict[str, Any]:
    """Repli OKX (funding + OI + mark) si Binance est géo-bloqué.

    Renvoie le même schéma que le chemin Binance, avec ``source='OKX'``.
    Dégradation totale : ``{available: False}`` si OKX ne répond pas non plus.
    """
    fr = get_json(f"{_OKX_BASE}/funding-rate", params={"instId": inst})
    rows = (fr or {}).get("data") if isinstance(fr, dict) else None
    if not rows:
        return {"available": False, "reason": "OKX funding indisponible"}
    try:
        funding_rate = float(rows[0].get("fundingRate"))
    except (TypeError, ValueError, KeyError, IndexError):
        return {"available": False, "reason": "OKX funding non parsable"}

    # Mark price (optionnel).
    mark_price = None
    mk = get_json(f"{_OKX_BASE}/mark-price", params={"instType": "SWAP", "instId": inst})
    mk_rows = (mk or {}).get("data") if isinstance(mk, dict) else None
    if mk_rows:
        try:
            mark_price = float(mk_rows[0].get("markPx")) or None
        except (TypeError, ValueError, KeyError, IndexError):
            mark_price = None

    # Open interest (optionnel) — OKX renvoie l'OI en nombre de contrats (oi)
    # et en devise (oiCcy). On prend oiCcy (unités de l'actif) pour rester
    # cohérent avec Binance (qui renvoie l'OI en unités de l'actif).
    open_interest = None
    oi = get_json(f"{_OKX_BASE}/open-interest", params={"instType": "SWAP", "instId": inst})
    oi_rows = (oi or {}).get("data") if isinstance(oi, dict) else None
    if oi_rows:
        try:
            open_interest = float(oi_rows[0].get("oiCcy") or oi_rows[0].get("oi"))
        except (TypeError, ValueError, KeyError, IndexError):
            open_interest = None

    # Moyenne funding 3 jours (9 paiements de 8h) via l'historique OKX —
    # contexte de tendance pour la famille « dérivés » du scoring des thèses.
    funding_3d_avg = None
    hist = get_json(
        f"{_OKX_BASE}/funding-rate-history",
        params={"instId": inst, "limit": _FUNDINGS_PER_DAY * 3},
    )
    hist_rows = (hist or {}).get("data") if isinstance(hist, dict) else None
    if hist_rows:
        try:
            rates = [float(r.get("fundingRate")) for r in hist_rows
                     if r.get("fundingRate") not in (None, "")]
            if rates:
                funding_3d_avg = sum(rates) / len(rates)
        except (TypeError, ValueError):
            funding_3d_avg = None

    # Long/short account ratio (v22 #31) — positionnement de la foule. > 1 =
    # plus de comptes longs (foule haussière, contrarian baissier si extrême).
    long_short_ratio = None
    ls = get_json(
        "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
        params={"ccy": symbol, "period": "1D"},
    )
    ls_rows = (ls or {}).get("data") if isinstance(ls, dict) else None
    if ls_rows:
        try:
            long_short_ratio = round(float(ls_rows[0][1]), 2)
        except (TypeError, ValueError, IndexError):
            long_short_ratio = None

    annualized = funding_rate * _FUNDINGS_PER_DAY * 365 * 100
    return {
        "available": True,
        "symbol": symbol,
        "perp_symbol": inst,
        "source": "OKX",
        "funding_rate": funding_rate,
        "funding_rate_pct": round(funding_rate * 100, 4),
        "funding_3d_avg_pct": round(funding_3d_avg * 100, 4)
        if funding_3d_avg is not None else None,
        "funding_annualized_pct": round(annualized, 2),
        # v30 (#70) — horodatage : le funding affiché doit être datable
        # (le +10,9%/an identique sur 4 rapports/2 jours était indétectable).
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "open_interest": open_interest,
        "long_short_ratio": long_short_ratio,
        "mark_price": mark_price,
        "interpretation": _interpret(funding_rate),
    }


def _interpret(funding_rate: float) -> str:
    """Lecture analytique d'un funding rate (période 8h)."""
    if funding_rate >= _HOT_LONG:
        return "excès de longs · surchauffe perp, risque de purge baissière"
    if funding_rate <= _HOT_SHORT:
        return "excès de shorts · potentiel short squeeze haussier"
    if funding_rate > 0:
        return "légèrement positif · longs majoritaires, sain"
    if funding_rate < 0:
        return "légèrement négatif · shorts majoritaires"
    return "neutre"


def get_derivatives(symbol: str) -> dict[str, Any]:
    """Récupère funding rate + OI d'un actif via Binance Futures.

    Args:
        symbol: ticker du portefeuille (ex. ``"BTC"``, ``"INJ"``).

    Returns:
        Dict ``{available, symbol, funding_rate, funding_rate_pct,
        funding_3d_avg, funding_annualized_pct, open_interest, mark_price,
        interpretation, reason}``. ``available=False`` si indisponible.
    """
    perp = _perp_symbol(symbol)
    if not perp:
        return {"available": False, "reason": f"pas de perp Binance pour {symbol}"}

    cache_key = f"binance_fut:{perp}"

    def _fetch() -> dict[str, Any]:
        # v21 (Logs#3) — OKX EN PREMIER. L'API Binance Futures (fapi) renvoie
        # systématiquement 451 (geo-block) depuis les runners GitHub Actions
        # hébergés aux US : appeler Binance d'abord ne produisait QUE du bruit
        # 451 dans les logs (9+ warnings/run) sans jamais réussir. OKX
        # (/api/v5/public) n'est pas géo-restreint sur les endpoints marché et
        # fournit funding + OI + mark. On tente donc OKX d'abord ; Binance ne
        # sert plus que de repli hors-Actions (dev local / IP résidentielle),
        # et on l'évite carrément sous GITHUB_ACTIONS pour zéro 451.
        inst = _okx_inst(symbol)
        if inst:
            okx = _fetch_okx(symbol, inst)
            if okx.get("available"):
                return okx

        # Repli Binance — uniquement hors GitHub Actions (sinon 451 garanti).
        if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
            return {"available": False, "reason": "OKX indisponible (Binance évité sur Actions: 451)"}

        # 1) Funding + mark price courant (Binance).
        premium = get_json(f"{_BASE}/premiumIndex", params={"symbol": perp})
        if not isinstance(premium, dict) or "lastFundingRate" not in premium:
            return {"available": False, "reason": "premiumIndex indisponible (Binance + OKX)"}
        try:
            funding_rate = float(premium.get("lastFundingRate"))
            mark_price = float(premium.get("markPrice") or 0) or None
        except (TypeError, ValueError):
            return {"available": False, "reason": "funding non parsable"}

        # 2) Historique funding (3 jours = 9 points) pour la moyenne.
        hist = get_json(
            f"{_BASE}/fundingRate",
            params={"symbol": perp, "limit": _FUNDINGS_PER_DAY * 3},
        )
        funding_3d_avg = None
        if isinstance(hist, list) and hist:
            try:
                rates = [float(h["fundingRate"]) for h in hist if "fundingRate" in h]
                if rates:
                    funding_3d_avg = sum(rates) / len(rates)
            except (TypeError, ValueError, KeyError):
                funding_3d_avg = None

        # 3) Open interest courant.
        oi_data = get_json(f"{_BASE}/openInterest", params={"symbol": perp})
        open_interest = None
        if isinstance(oi_data, dict) and oi_data.get("openInterest"):
            try:
                open_interest = float(oi_data["openInterest"])
            except (TypeError, ValueError):
                open_interest = None

        annualized = funding_rate * _FUNDINGS_PER_DAY * 365 * 100
        return {
            "available": True,
            "symbol": symbol,
            "perp_symbol": perp,
            "source": "Binance",
            "funding_rate": funding_rate,
            "funding_rate_pct": round(funding_rate * 100, 4),
            "funding_3d_avg_pct": round(funding_3d_avg * 100, 4)
            if funding_3d_avg is not None
            else None,
            "funding_annualized_pct": round(annualized, 2),
            "open_interest": open_interest,
            "mark_price": mark_price,
            "interpretation": _interpret(funding_rate),
        }

    try:
        return CACHE.get_or_compute(cache_key, 900, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Binance Futures échoué pour %s : %s", symbol, exc)
        return {"available": False, "reason": str(exc)}


