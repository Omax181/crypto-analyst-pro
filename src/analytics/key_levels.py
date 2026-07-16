"""Niveaux S/R CALCULÉS + readout technique complet (v26 E-B5).

L'audit evening v24/v25 a montré que les « Niveaux à surveiller cette nuit »
étaient des nombres RONDS inventés par l'IA (59 000 / 61 000…) — risque
d'hallucination structurel. Ce module calcule les niveaux depuis la série de
clôtures réelle (pivots de swing, MM50/100/200, retracements Fibonacci, bandes
de Bollinger, seuils psychologiques) et produit un readout technique exhaustif
(RSI, MACD, Bollinger, ATR, tendance, volume) :

  - en mode NOMINAL, ces niveaux sont injectés au prompt comme SOURCE DE VÉRITÉ
    (l'IA choisit et commente, elle n'invente plus) ;
  - en mode DÉGRADÉ (IA indisponible), ils sont rendus tels quels — le bloc le
    plus actionnable du mail survit à une panne Gemini.

Calcul pur (série in → dict out), zéro réseau, zéro dépendance externe.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from src.analytics.technical_local import (
    _pivot_highs,
    _pivot_lows,
    _rsi_series,
    compute_atr_pct,
    compute_bollinger,
    compute_macd,
)

# Espace fine insécable (convention projet pour les milliers, cf. main._int_fr).
_NNBSP = " "

# Priorité des bases lors du clustering : un pivot testé bat une MM, qui bat un
# retracement Fibo, qui bat une bande de Bollinger, qui bat un rond.
_BASIS_RANK = {"pivot": 0, "MM200": 1, "MM100": 2, "MM50": 3,
               "Fibo": 4, "Bollinger": 5, "seuil rond": 6}


def _fmt_usd(v: float) -> str:
    """Prix formaté façon mail : « 61 200 $ » (milliers U+202F) / « 3.42 $ »."""
    if v >= 1000:
        return f"{v:,.0f}".replace(",", _NNBSP) + f"{_NNBSP}$"
    if v >= 100:
        return f"{v:.0f}{_NNBSP}$"
    if v >= 1:
        return f"{v:.2f}".replace(".", ",") + f"{_NNBSP}$"
    return f"{v:.4f}{_NNBSP}$"


def _sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _round_step(price: float) -> float:
    """Pas psychologique : 61 949 → 1 000 ; 1 615 → 100 ; 211 → 10 ; 0.52 → 0.01."""
    if price <= 0:
        return 1.0
    return 10.0 ** (math.floor(math.log10(price)) - 1)


def _fib_levels(closes: list[float], lookback: int = 90) -> list[tuple[float, str]]:
    """Retracements Fibonacci du swing majeur de la fenêtre (hi/lo réels)."""
    window = closes[-lookback:] if len(closes) > lookback else closes
    hi, lo = max(window), min(window)
    if hi <= lo:
        return []
    span = hi - lo
    return [
        (hi - span * 0.382, "Fibo 38,2%"),
        (hi - span * 0.5, "Fibo 50%"),
        (hi - span * 0.618, "Fibo 61,8%"),
    ]


def _cluster(cands: list[tuple[float, str]], price: float,
             tol_pct: float = 1.2) -> list[dict[str, Any]]:
    """Fusionne les niveaux proches (< tol_pct) ; ancre sur la base la plus forte."""
    out: list[dict[str, Any]] = []
    for level, basis in sorted(cands, key=lambda c: c[0]):
        rank = _BASIS_RANK.get(basis.split()[0], 9)
        if out and abs(level - out[-1]["level"]) / price * 100 <= tol_pct:
            prev = out[-1]
            bases = set(prev["basis"].split(" + ")) | {basis}
            prev["basis"] = " + ".join(
                sorted(bases, key=lambda b: _BASIS_RANK.get(b.split()[0], 9)))
            if rank < prev["_rank"]:  # niveau ancré sur la base la plus forte
                prev["level"] = level
                prev["_rank"] = rank
        else:
            out.append({"level": level, "basis": basis, "_rank": rank})
    for it in out:
        it.pop("_rank", None)
    return out


def compute_key_levels(
    symbol: str,
    closes: list[float],
    volumes: Optional[list[float]] = None,
    *,
    price: Optional[float] = None,
) -> dict[str, Any]:
    """Niveaux S/R + readout technique depuis une série daily chronologique.

    Args:
        symbol: ticker (BTC…) — recopié dans la sortie.
        closes: clôtures chronologiques (≥ 30 pour un résultat utile).
        volumes: volumes alignés (optionnel — tendance de volume 7j/30j).
        price: prix spot live (défaut : dernière clôture).

    Returns:
        ``{available, symbol, price, supports, resistances, readout,
        readout_line, expected_range}`` — supports/resistances triés du plus
        proche au plus loin, 3 max chacun, chacun ``{level, level_label, basis,
        dist_pct}``. ``available=False`` si la série est trop courte.
    """
    series = [float(c) for c in (closes or []) if isinstance(c, (int, float))]
    if len(series) < 30:
        return {"available": False, "symbol": symbol,
                "reason": "série trop courte (<30 clôtures)"}
    px = float(price) if isinstance(price, (int, float)) and price > 0 else series[-1]

    # ── candidats de niveaux ──
    cands: list[tuple[float, str]] = []
    window = series[-120:]
    for i in _pivot_highs(window, span=3):
        cands.append((window[i], "pivot"))
    for i in _pivot_lows(window, span=3):
        cands.append((window[i], "pivot"))
    for period, name in ((50, "MM50"), (100, "MM100"), (200, "MM200")):
        ma = _sma(series, period)
        if ma:
            cands.append((ma, name))
    boll = compute_bollinger(series)
    if boll:
        cands.append((boll["lower"], "Bollinger"))
        cands.append((boll["upper"], "Bollinger"))
    cands.extend(_fib_levels(series))
    step = _round_step(px)
    cands.append((math.floor(px / step) * step, "seuil rond"))
    cands.append((math.ceil(px / step) * step, "seuil rond"))

    merged = _cluster([c for c in cands if c[0] > 0], px)
    sup = [c for c in merged if c["level"] < px * 0.998]
    res = [c for c in merged if c["level"] > px * 1.002]
    sup.sort(key=lambda c: -c["level"])   # du plus proche au plus loin
    res.sort(key=lambda c: c["level"])

    def _decorate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "level": round(it["level"], 6),
            "level_label": _fmt_usd(it["level"]),
            # Lisibilité : 2 bases max à l'affichage (les plus fortes d'abord).
            "basis": " + ".join(it["basis"].split(" + ")[:2]),
            "dist_pct": round((it["level"] - px) / px * 100, 1),
        } for it in items[:3]]

    supports = _decorate(sup)
    resistances = _decorate(res)

    # ── readout technique exhaustif ──
    rsi_ser = _rsi_series(series, 14)
    rsi = rsi_ser[-1] if rsi_ser else None
    rsi = round(rsi, 0) if isinstance(rsi, float) else rsi
    rsi_zone = None
    if isinstance(rsi, (int, float)):
        rsi_zone = ("survente" if rsi < 30 else
                    "surchauffe" if rsi > 70 else "neutre")
    macd_hist = compute_macd(series)
    macd_state = None
    if isinstance(macd_hist, (int, float)):
        macd_state = "haussier" if macd_hist > 0 else "baissier"
    atr_pct = compute_atr_pct(series)
    atr_abs = round(atr_pct / 100 * px, 2) if atr_pct else None
    ma50 = _sma(series, 50)
    ma200 = _sma(series, 200)
    trend_7d = (round((series[-1] / series[-8] - 1) * 100, 1)
                if len(series) >= 8 and series[-8] else None)
    vol_trend = None
    vols = [float(v) for v in (volumes or []) if isinstance(v, (int, float))]
    if len(vols) >= 30:
        v7 = sum(vols[-7:]) / 7
        v30 = sum(vols[-30:]) / 30
        if v30 > 0:
            vol_trend = round((v7 / v30 - 1) * 100, 0)

    readout = {
        "rsi": rsi, "rsi_zone": rsi_zone,
        "macd_state": macd_state,
        "boll_position": (boll or {}).get("position"),
        "boll_width_pct": (boll or {}).get("width_pct"),
        "atr_pct": atr_pct, "atr_abs": atr_abs,
        "ma50_rel_pct": (round((px / ma50 - 1) * 100, 1) if ma50 else None),
        "ma200_rel_pct": (round((px / ma200 - 1) * 100, 1) if ma200 else None),
        "trend_7d_pct": trend_7d,
        "volume_trend_pct": vol_trend,
    }

    # ── ligne readout FR compacte (rendu mail + prompt) ──
    parts: list[str] = []
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f} ({rsi_zone})")
    if macd_state:
        parts.append(f"MACD {macd_state}")
    bp = readout["boll_position"]
    if bp:
        bp_fr = {"lower": "bande basse", "mid": "médiane",
                 "upper": "bande haute"}.get(bp, bp)
        parts.append(f"Bollinger {bp_fr}")
    if atr_pct is not None and atr_abs is not None:
        parts.append(f"ATR {str(round(atr_pct, 1)).replace('.', ',')}%"
                     f" (≈{_fmt_usd(atr_abs)}/j)")
    if readout["ma50_rel_pct"] is not None:
        _m = readout["ma50_rel_pct"]
        parts.append(f"{'+' if _m >= 0 else '−'}"
                     f"{str(abs(_m)).replace('.', ',')}% vs MM50")
    if vol_trend is not None:
        parts.append(f"volume 7j {'+' if vol_trend >= 0 else '−'}"
                     f"{abs(vol_trend):.0f}% vs 30j")
    readout_line = " · ".join(parts)

    expected_range = None
    if atr_abs:
        expected_range = {
            "low": round(px - atr_abs, 2), "high": round(px + atr_abs, 2),
            "low_label": _fmt_usd(px - atr_abs),
            "high_label": _fmt_usd(px + atr_abs),
        }

    return {
        "available": True, "symbol": symbol, "price": round(px, 6),
        "price_label": _fmt_usd(px),
        "supports": supports, "resistances": resistances,
        "readout": readout, "readout_line": readout_line,
        "expected_range": expected_range,
    }


def _pct_fr(v: float) -> str:
    """« −1,7% » / « +2,7% » — signe typographique + virgule décimale."""
    return f"{'+' if v >= 0 else '−'}{str(abs(v)).replace('.', ',')}%"


def levels_tonight_rows(computed: dict[str, Any]) -> list[dict[str, Any]]:
    """Convertit ``compute_key_levels`` en lignes prêtes pour ``levels_tonight``.

    2 supports + 2 résistances max par actif, avec un trigger FR déterministe
    qui chaîne vers le niveau suivant (« Sous X → prochain appui Y »).
    """
    if not computed or not computed.get("available"):
        return []
    sym = computed["symbol"]
    rows: list[dict[str, Any]] = []
    sups = computed.get("supports") or []
    ress = computed.get("resistances") or []
    for i, s in enumerate(sups[:2]):
        nxt = sups[i + 1] if i + 1 < len(sups) else None
        trig = (f"Sous {s['level_label']} ({s['basis']}) → prochain appui "
                f"{nxt['level_label']} ({_pct_fr(nxt['dist_pct'])})." if nxt else
                f"Sous {s['level_label']} ({s['basis']}) → plus de filet dans "
                f"la fenêtre 120j, prudence.")
        rows.append({"asset": sym, "level": s["level_label"],
                     "type": "support", "trigger": trig})
    for i, r in enumerate(ress[:2]):
        nxt = ress[i + 1] if i + 1 < len(ress) else None
        trig = (f"Au-dessus de {r['level_label']} ({r['basis']}) → ouverture "
                f"vers {nxt['level_label']} ({_pct_fr(nxt['dist_pct'])})." if nxt else
                f"Au-dessus de {r['level_label']} ({r['basis']}) → plus haut "
                f"de la fenêtre 120j, terrain dégagé.")
        rows.append({"asset": sym, "level": r["level_label"],
                     "type": "resistance", "trigger": trig})
    return rows
