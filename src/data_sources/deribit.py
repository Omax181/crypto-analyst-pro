"""Dérivés options : Deribit Public API (gratuit, SANS clé).

Deribit concentre l'essentiel du marché des options crypto (BTC/ETH). On en
tire trois signaux à fort contenu informationnel pour le court terme :
  - Put/Call ratio (open interest) : > 1 = couverture/baissier dominant,
    < 0.7 = appétit haussier (calls).
  - Max pain : strike où le maximum d'options expirent sans valeur (aimant de
    prix théorique à l'approche de l'expiration mensuelle).
  - DVOL : indice de volatilité implicite Deribit (≈ « VIX du crypto »).

Endpoints publics (aucune auth) :
  - ``/public/get_book_summary_by_currency`` (open interest par instrument)
  - ``/public/get_volatility_index_data``     (historique DVOL)

Dégradation gracieuse : toute erreur → ``available: False`` pour la devise,
jamais d'exception propagée. Polling rare (3x/jour) conforme aux bonnes
pratiques Deribit.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_BASE = "https://www.deribit.com/api/v2/public"
_CURRENCIES = ("BTC", "ETH")


def _parse_instrument(name: str) -> Optional[tuple[datetime, float, str]]:
    """Parse ``BTC-27JUN25-65000-C`` -> (expiry_dt, strike, 'C'|'P').

    Renvoie ``None`` si le format n'est pas une option standard.
    """
    parts = name.split("-")
    if len(parts) != 4:
        return None
    _, expiry_s, strike_s, opt = parts
    if opt not in ("C", "P"):
        return None
    try:
        expiry = datetime.strptime(expiry_s, "%d%b%y").replace(tzinfo=timezone.utc)
        strike = float(strike_s)
    except (ValueError, TypeError):
        return None
    return expiry, strike, opt


def _max_pain(options: list[tuple[float, str, float]]) -> Optional[float]:
    """Calcule le strike de max pain pour une liste (strike, type, oi).

    Max pain = strike S minimisant la valeur intrinsèque totale versée aux
    détenteurs à l'expiration : somme des calls ITM (S-K) + puts ITM (K-S),
    pondérée par l'open interest.
    """
    strikes = sorted({s for s, _, _ in options})
    if len(strikes) < 3:
        return None
    best_strike, best_pain = None, None
    for s in strikes:
        pain = 0.0
        for strike, opt, oi in options:
            if oi <= 0:
                continue
            if opt == "C" and s > strike:
                pain += (s - strike) * oi
            elif opt == "P" and s < strike:
                pain += (strike - s) * oi
        if best_pain is None or pain < best_pain:
            best_pain, best_strike = pain, s
    return best_strike


def _fetch_options_summary(currency: str) -> dict[str, Any]:
    """Put/call ratio (OI global) + max pain (expiration la plus proche)."""
    raw = get_json(
        f"{_BASE}/get_book_summary_by_currency",
        params={"currency": currency, "kind": "option"},
    )
    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, list) or not result:
        return {}

    now = datetime.now(timezone.utc)
    call_oi = put_oi = 0.0
    # OI par expiration pour isoler la prochaine échéance (max pain).
    by_expiry: dict[datetime, list[tuple[float, str, float]]] = {}
    underlying: Optional[float] = None

    for inst in result:
        if not isinstance(inst, dict):
            continue
        parsed = _parse_instrument(str(inst.get("instrument_name", "")))
        if not parsed:
            continue
        expiry, strike, opt = parsed
        oi = inst.get("open_interest")
        try:
            oi = float(oi) if oi is not None else 0.0
        except (TypeError, ValueError):
            oi = 0.0
        if opt == "C":
            call_oi += oi
        else:
            put_oi += oi
        if underlying is None and inst.get("underlying_price"):
            underlying = float(inst["underlying_price"])
        if expiry > now:
            by_expiry.setdefault(expiry, []).append((strike, opt, oi))

    out: dict[str, Any] = {}
    if call_oi > 0:
        out["put_call_ratio"] = round(put_oi / call_oi, 2)
    if underlying is not None:
        out["underlying_price"] = round(underlying, 2)

    # Max pain sur l'expiration future la plus proche (la plus « contraignante »).
    if by_expiry:
        nearest = min(by_expiry)
        mp = _max_pain(by_expiry[nearest])
        if mp is not None:
            out["max_pain"] = mp
            out["max_pain_expiry"] = nearest.strftime("%d %b %Y")
            if underlying:
                out["max_pain_gap_pct"] = round((mp - underlying) / underlying * 100, 1)
    return out


def _fetch_dvol(currency: str) -> Optional[float]:
    """Dernière valeur de l'indice de volatilité implicite DVOL."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 3 * 24 * 60 * 60 * 1000  # 3 jours
    raw = get_json(
        f"{_BASE}/get_volatility_index_data",
        params={
            "currency": currency,
            "start_timestamp": start_ms,
            "end_timestamp": now_ms,
            "resolution": 43200,  # 12h
        },
    )
    result = raw.get("result") if isinstance(raw, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or not data:
        return None
    last = data[-1]
    # Format candle : [timestamp, open, high, low, close].
    if isinstance(last, list) and len(last) >= 5:
        try:
            return round(float(last[4]), 1)
        except (TypeError, ValueError):
            return None
    return None


def get_options_metrics() -> dict[str, Any]:
    """Récupère les métriques options Deribit pour BTC et ETH.

    Returns:
        Dict ``{available, assets: {SYM: {put_call_ratio, max_pain,
        max_pain_expiry, max_pain_gap_pct, dvol, underlying_price}}}``.
        ``available=False`` si aucune devise n'a pu être récupérée.
    """

    def _fetch() -> dict[str, Any]:
        assets: dict[str, Any] = {}
        for cur in _CURRENCIES:
            entry = _fetch_options_summary(cur)
            dvol = _fetch_dvol(cur)
            if dvol is not None:
                entry["dvol"] = dvol
            if entry:
                assets[cur] = entry
        if not assets:
            return {"available": False, "assets": {}}
        return {"available": True, "assets": assets}

    try:
        return CACHE.get_or_compute("deribit:options", 1800, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deribit indisponible : %s", exc)
        return {"available": False, "assets": {}}
