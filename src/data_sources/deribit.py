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

import math
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


def _f(value: Any) -> Optional[float]:
    """Cast float sûr (None si non convertible)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_cdf(x: float) -> float:
    """Fonction de répartition de la loi normale centrée réduite (via erf)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(opt: str, spot: float, strike: float, iv: float, t_years: float,
              rate: float = 0.0) -> Optional[float]:
    """Delta Black-Scholes (call ∈ ]0,1[, put ∈ ]-1,0[). ``iv`` en DÉCIMAL (0.48)."""
    if spot <= 0 or strike <= 0 or iv <= 0 or t_years <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (
        iv * math.sqrt(t_years))
    call_delta = _norm_cdf(d1)
    return call_delta if opt == "C" else call_delta - 1.0


# OB26 — SKEW des options (risk reversal 25Δ). Signal directionnel du
# positionnement options : un put 25Δ plus cher (en vol implicite) que le call
# 25Δ (RR < 0) = le marché paie la protection à la baisse (prudence/couverture) ;
# l'inverse (RR > 0) = appétit haussier (calls). Calculé sur l'échéance la plus
# proche de ~30 jours (tenor de référence), 100 % depuis le book summary déjà
# récupéré (AUCUN appel réseau supplémentaire). Dégradation gracieuse : {} si
# données insuffisantes (jamais d'exception, jamais de champ inventé).
_SKEW_MIN_DAYS = 5.0
_SKEW_TARGET_DAYS = 30.0
_SKEW_MIN_QUOTES = 6
_SKEW_THRESHOLD = 1.5  # points de vol pour qualifier haussier/baissier


def _compute_skew(
    opts: list[tuple[datetime, float, str, Optional[float], Optional[float]]],
    now: datetime,
) -> dict[str, Any]:
    """Risk reversal 25Δ + ATM IV sur l'échéance ≈30 j. ``{}`` si insuffisant.

    ``opts`` : liste ``(expiry, strike, opt, mark_iv_pts, underlying)``.
    """
    by_exp: dict[datetime, list[tuple[float, str, float, float]]] = {}
    for expiry, strike, opt, iv, u in opts:
        if iv is None or iv <= 0 or u is None or u <= 0 or expiry <= now:
            continue
        by_exp.setdefault(expiry, []).append((strike, opt, iv, u))
    best = None  # (dist_to_target, days, quotes)
    for expiry, quotes in by_exp.items():
        days = (expiry - now).total_seconds() / 86400.0
        if days < _SKEW_MIN_DAYS or len(quotes) < _SKEW_MIN_QUOTES:
            continue
        dist = abs(days - _SKEW_TARGET_DAYS)
        if best is None or dist < best[0]:
            best = (dist, days, quotes)
    if best is None:
        return {}
    _, days, quotes = best
    t_years = days / 365.0
    spot = sorted(u for _s, _o, _iv, u in quotes)[len(quotes) // 2]  # médiane
    best_call = best_put = best_atm = None
    for strike, opt, iv, _u in quotes:
        delta = _bs_delta(opt, spot, strike, iv / 100.0, t_years)
        if delta is None:
            continue
        if opt == "C":
            dc = abs(delta - 0.25)
            if best_call is None or dc < best_call[0]:
                best_call = (dc, iv)
            da = abs(delta - 0.5)
            if best_atm is None or da < best_atm[0]:
                best_atm = (da, iv)
        else:
            dp = abs(delta + 0.25)
            if best_put is None or dp < best_put[0]:
                best_put = (dp, iv)
            da = abs(delta + 0.5)
            if best_atm is None or da < best_atm[0]:
                best_atm = (da, iv)
    if best_call is None or best_put is None:
        return {}
    call_iv, put_iv = best_call[1], best_put[1]
    rr = call_iv - put_iv
    out: dict[str, Any] = {
        "iv_skew_25d": round(rr, 1),
        "call_iv_25d": round(call_iv, 1),
        "put_iv_25d": round(put_iv, 1),
        "skew_tenor_days": int(round(days)),
    }
    if best_atm is not None:
        out["atm_iv"] = round(best_atm[1], 1)
    if rr <= -_SKEW_THRESHOLD:
        out["skew_reading"] = (
            f"Skew baissier ({f'{rr:+.1f}'.replace('.', ',')} pts de vol) — le marché paie cher la "
            "protection à la baisse (couverture/prudence)")
    elif rr >= _SKEW_THRESHOLD:
        out["skew_reading"] = (
            f"Skew haussier ({f'{rr:+.1f}'.replace('.', ',')} pts de vol) — appétit pour les calls "
            "(optimisme/spéculation à la hausse)")
    else:
        out["skew_reading"] = (
            f"Skew neutre ({f'{rr:+.1f}'.replace('.', ',')} pts de vol) — positionnement options "
            "équilibré")
    return out


def _fetch_options_summary(currency: str) -> dict[str, Any]:
    """Put/call ratio (OI global) + max pain + skew 25Δ (OB26)."""
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
    iv_opts: list[tuple[datetime, float, str, Optional[float], Optional[float]]] = []
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
            iv_opts.append((expiry, strike, opt,
                            _f(inst.get("mark_iv")), _f(inst.get("underlying_price"))))

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

    # OB26 — skew 25Δ sur l'échéance ≈30 j (best-effort, réutilise le même fetch).
    skew = _compute_skew(iv_opts, now)
    if skew:
        out.update(skew)
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
