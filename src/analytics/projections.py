"""Échafaudage de PROJECTION déterministe pour les thèses de recommandation.

Objectif (deepthink Omar, 2026-06-29) : les cibles affichées dans les 3 cases des
thèses du jour — TACTIQUE COURT TERME (30j), POSITIONNEMENT LT (6-12 mois) et le
PLAN D'ACTION (entrée/stop/R:R) — ne doivent PLUS être inventées par le LLM mais
ANCRÉES sur des niveaux réels et bornées par la volatilité réelle de l'actif.

Ce module est PUR (aucun réseau, aucune dépendance lourde) et dégrade proprement :
chaque donnée manquante est simplement ignorée. Il produit, par actif éligible :

  * ``volatility``        : mouvement 30j attendu (ATR×√30) + plafond réaliste,
  * ``levels_above/below``: niveaux chiffrés ordonnés, fusionnés en CONFLUENCES,
  * ``short_term_30d``    : cible 30j haussière ANCRÉE (résistance/Fibo/confluence)
                            et plafonnée par la volatilité (anti-cible irréaliste),
  * ``short_term_30d_bear``: cible 30j baissière (support) pour les thèses ALLÉGER,
  * ``long_term_6_12m``   : fourchette 6-12 mois (retracement vers l'ATH ou
                            extension Fibonacci au-delà de l'ATH),
  * ``stop_suggestion``   : niveau de stop ancré (support / bande basse).

Le LLM reçoit ce bloc comme ANCRAGE (il cite la base, ajuste avec le narratif) et
un garde-fou Python (main._merge_python_facts) ramène toute cible 30j aberrante.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# Tolérance de regroupement de deux niveaux en une CONFLUENCE (% du niveau).
_CONFLUENCE_TOL_PCT = 0.8
# Multiplicateur « stretch mais plausible » appliqué au mouvement 30j attendu pour
# obtenir le plafond réaliste d'une cible 30j (≈ 1,5 σ).
_REALISTIC_MULT = 1.5
# Jours calendaires de l'horizon court terme (mise à l'échelle √temps de l'ATR).
_ST_HORIZON_DAYS = 30


def _num(x: Any) -> Optional[float]:
    """Coercition douce en float positif exploitable (sinon None)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)) and math.isfinite(x) and x > 0:
        return float(x)
    return None


def _collect_levels(
    *,
    support_resistance: Optional[dict[str, Any]],
    fibonacci: Optional[dict[str, Any]],
    bollinger: Optional[dict[str, Any]],
    moving_averages: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rassemble les niveaux techniques chiffrés avec un libellé lisible."""
    out: list[dict[str, Any]] = []

    def _add(level: Any, basis: str) -> None:
        v = _num(level)
        if v is not None:
            out.append({"level": v, "basis": basis})

    sr = support_resistance or {}
    if sr.get("available", True):
        _add(sr.get("resistance"), "résistance")
        _add(sr.get("support"), "support")
    bo = bollinger or {}
    if bo.get("available", True):
        _add(bo.get("upper"), "bande haute Bollinger")
        _add(bo.get("lower"), "bande basse Bollinger")
        _add(bo.get("middle"), "médiane Bollinger")
    ma = moving_averages or {}
    _add(ma.get("sma50"), "MM50")
    _add(ma.get("sma100"), "MM100")
    _add(ma.get("sma200"), "MM200")
    fib = fibonacci or {}
    for key, label in (
        ("level_236", "Fibonacci 0.236"), ("level_382", "Fibonacci 0.382"),
        ("level_500", "Fibonacci 0.5"), ("level_618", "Fibonacci 0.618"),
        ("level_786", "Fibonacci 0.786"),
    ):
        _add(fib.get(key), label)
    return out


def _cluster_levels(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fusionne les niveaux proches (< _CONFLUENCE_TOL_PCT) en CONFLUENCES.

    Une confluence (≥ 2 indicateurs au même prix) est un niveau bien plus fiable
    qu'un indicateur isolé : on la signale (``confluence=True``) pour que la cible
    s'y ancre en priorité.
    """
    if not levels:
        return []
    ordered = sorted(levels, key=lambda d: d["level"])
    clusters: list[dict[str, Any]] = []
    cur = {"levels": [ordered[0]["level"]], "bases": [ordered[0]["basis"]]}
    for item in ordered[1:]:
        ref = cur["levels"][0]
        if abs(item["level"] - ref) / ref * 100 <= _CONFLUENCE_TOL_PCT:
            cur["levels"].append(item["level"])
            cur["bases"].append(item["basis"])
        else:
            clusters.append(cur)
            cur = {"levels": [item["level"]], "bases": [item["basis"]]}
    clusters.append(cur)
    out: list[dict[str, Any]] = []
    for c in clusters:
        lv = sum(c["levels"]) / len(c["levels"])
        bases = list(dict.fromkeys(c["bases"]))  # dédup en gardant l'ordre
        out.append({
            "level": round(lv, 6),
            "basis": " + ".join(bases),
            "confluence": len(bases) >= 2,
        })
    return out


def _pct(target: float, price: float) -> float:
    return round((target / price - 1.0) * 100.0, 1)


def _expected_move_30d_pct(
    atr_pct: Optional[float], change_30d: Optional[float]
) -> Optional[float]:
    """Mouvement 30j attendu (magnitude, %). ATR×√30 prioritaire ; repli sur la
    magnitude du mouvement 30j réalisé ; None si aucune info de volatilité."""
    a = atr_pct if isinstance(atr_pct, (int, float)) and atr_pct > 0 else None
    if a is not None:
        return round(a * math.sqrt(_ST_HORIZON_DAYS), 1)
    if isinstance(change_30d, (int, float)) and change_30d != 0:
        return round(abs(float(change_30d)), 1)
    return None


def _pick_short_term(
    clusters_above: list[dict[str, Any]],
    price: float,
    cap_price: Optional[float],
    expected_move_pct: Optional[float],
) -> Optional[dict[str, Any]]:
    """Cible 30j HAUSSIÈRE ancrée : confluence la plus proche sous le plafond, à
    défaut résistance la plus proche, à défaut projection ATR pure."""
    within = [c for c in clusters_above if cap_price is None or c["level"] <= cap_price]
    confluent = [c for c in within if c["confluence"]]
    pick = None
    if confluent:
        pick = confluent[0]
    elif within:
        pick = within[0]
    elif clusters_above and cap_price is None:
        pick = clusters_above[0]
    if pick is not None:
        return {
            "target": round(pick["level"], 6),
            "basis": pick["basis"],
            "move_pct": _pct(pick["level"], price),
            "confluence": pick["confluence"],
        }
    # Aucune résistance exploitable sous le plafond → projection ATR pure.
    if expected_move_pct is not None:
        tgt = price * (1.0 + expected_move_pct / 100.0)
        return {
            "target": round(tgt, 6),
            "basis": "projection volatilité 30j (pas de résistance proche)",
            "move_pct": round(expected_move_pct, 1),
            "confluence": False,
        }
    return None


def _pick_short_term_bear(
    clusters_below: list[dict[str, Any]],
    price: float,
    floor_price: Optional[float],
) -> Optional[dict[str, Any]]:
    """Cible 30j BAISSIÈRE (pour ALLÉGER) : support le plus proche au-dessus du
    plancher de volatilité."""
    within = [c for c in clusters_below if floor_price is None or c["level"] >= floor_price]
    pick = (within or clusters_below)
    if not pick:
        return None
    c = pick[0]
    return {
        "target": round(c["level"], 6),
        "basis": c["basis"],
        "move_pct": _pct(c["level"], price),
        "confluence": c["confluence"],
    }


def _long_term_range(
    price: float, ath: Optional[float], expected_move_pct: Optional[float]
) -> Optional[dict[str, Any]]:
    """Fourchette 6-12 mois ancrée sur l'ATH (retracement du repli) ou, près de
    l'ATH, sur une extension Fibonacci. Repli volatilité si pas d'ATH."""
    a = _num(ath)
    if a is not None and price < a * 0.97:
        # Sous l'ATH : bas = retracement 0.382 du repli (reprise prudente),
        # haut = retour ATH (si le narratif se confirme).
        low = price + 0.382 * (a - price)
        return {
            "low": round(low, 6), "high": round(a, 6),
            "basis": "bas = retracement 0.382 vers l'ATH · haut = retour ATH",
            "low_pct": _pct(low, price), "high_pct": _pct(a, price),
        }
    if a is not None:  # au contact / au-dessus de l'ATH → extension
        low = a
        high = a * 1.414  # extension Fibonacci 1.414 (price discovery)
        return {
            "low": round(low, 6), "high": round(high, 6),
            "basis": "bas = ancien ATH (support) · haut = extension Fibonacci 1.414",
            "low_pct": _pct(low, price), "high_pct": _pct(high, price),
        }
    if expected_move_pct is not None:  # pas d'ATH connu → projection volatilité
        low = price * (1.0 + expected_move_pct / 100.0)
        high = price * (1.0 + 2.5 * expected_move_pct / 100.0)
        return {
            "low": round(low, 6), "high": round(high, 6),
            "basis": "projection volatilité 6-12m (ATH inconnu)",
            "low_pct": _pct(low, price), "high_pct": _pct(high, price),
        }
    return None


def compute_price_projection(
    price: Any,
    *,
    support_resistance: Optional[dict[str, Any]] = None,
    fibonacci: Optional[dict[str, Any]] = None,
    bollinger: Optional[dict[str, Any]] = None,
    moving_averages: Optional[dict[str, Any]] = None,
    ath: Any = None,
    ath_distance_pct: Any = None,
    atr_pct: Any = None,
    change_30d: Any = None,
) -> dict[str, Any]:
    """Construit l'échafaudage de projection d'un actif (voir docstring module)."""
    p = _num(price)
    if p is None:
        return {"available": False}

    clusters = _cluster_levels(_collect_levels(
        support_resistance=support_resistance, fibonacci=fibonacci,
        bollinger=bollinger, moving_averages=moving_averages,
    ))
    # Niveaux les plus PROCHES d'abord, plafonnés à 6 de chaque côté (les niveaux
    # lointains n'éclairent ni l'entrée, ni le stop, ni la cible 30j — payload lean).
    above = [c for c in clusters if c["level"] > p * 1.002][:6]
    below = sorted(
        [c for c in clusters if c["level"] < p * 0.998],
        key=lambda c: c["level"], reverse=True,
    )[:6]

    expected = _expected_move_30d_pct(
        atr_pct if isinstance(atr_pct, (int, float)) else None, change_30d
    )
    realistic_high = round(expected * _REALISTIC_MULT, 1) if expected is not None else None
    cap_price = p * (1.0 + realistic_high / 100.0) if realistic_high is not None else None
    floor_price = p * (1.0 - realistic_high / 100.0) if realistic_high is not None else None

    short_term = _pick_short_term(above, p, cap_price, expected)
    if short_term is not None and realistic_high is not None:
        short_term["within_realistic_band"] = short_term["move_pct"] <= realistic_high * 1.05
    short_term_bear = _pick_short_term_bear(below, p, floor_price)
    long_term = _long_term_range(p, ath, expected)

    stop = below[0] if below else None
    stop_suggestion = None
    if stop is not None:
        stop_suggestion = {
            "level": round(stop["level"] * 0.99, 6),  # 1% sous le support = swing low
            "basis": stop["basis"],
        }

    available = any(x is not None for x in (short_term, short_term_bear, long_term))
    return {
        "available": available,
        "price": round(p, 6),
        "volatility": {
            "atr_pct_daily": round(float(atr_pct), 2) if isinstance(atr_pct, (int, float)) else None,
            "expected_move_30d_pct": expected,
            "realistic_30d_high_pct": realistic_high,
        },
        "levels_above": above,
        "levels_below": below,
        "short_term_30d": short_term,
        "short_term_30d_bear": short_term_bear,
        "long_term_6_12m": long_term,
        "stop_suggestion": stop_suggestion,
    }
