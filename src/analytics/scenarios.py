"""Échafaudage DÉTERMINISTE des scénarios hebdomadaires (deepthink Omar, 2026-06-30).

Les 3 scénarios de la semaine (baissier / neutre / haussier) et leurs probabilités
ne doivent plus être « estimés au doigt mouillé » : ce module agrège, de façon
transparente et traçable, TOUS les signaux objectifs qui pilotent la distribution —
volatilité implicite (DVOL), Polymarket, régime macro, technique BTC, sentiment
(Fear & Greed), dérivés (funding), momentum, et le calendrier macro à 7j — puis en
DÉRIVE un PRIOR de probabilités. Le LLM s'y ANCRE pour rédiger l'analyse profonde et
finaliser les % (qu'il peut ajuster avec justification), au lieu d'inventer.

Méthode (100% Python, pur, dégradation gracieuse) :
  1. Chaque dimension produit un TILT directionnel dans [-1, +1] (négatif = baissier).
  2. ``net_tilt`` = moyenne pondérée des tilts disponibles → biais directionnel.
  3. ``dispersion`` ∈ [0, 1] = largeur des queues, fonction du move implicite (DVOL)
     ET du risque événementiel (catalyseurs datés ≤ 7j, incertitude Polymarket).
  4. ``prior`` : neutre = 0.55 − 0.35·dispersion (catalyseur/vol ↑ → neutre ↓) ;
     le reste réparti bull/bear selon ``net_tilt``. Somme = 100.
Tout est exposé (tilts + notes chiffrées + prior + niveaux + drivers) pour que le
LLM CITE chaque moteur et reste cohérent.
"""

from __future__ import annotations

from typing import Any, Optional

# Poids relatifs des dimensions dans le biais directionnel net.
_DIM_WEIGHTS = {
    "Macro": 0.30,
    "Technique": 0.25,
    "Sentiment": 0.15,
    "Dérivés": 0.15,
    "Momentum": 0.15,
}
# Événements macro à fort impact directionnel sur le risque (élargissent les queues).
_HIGH_IMPACT = ("fomc", "fed", "taux", "cpi", "inflation", "pce", "nfp", "emploi",
                "chômage", "chomage", "payroll", "jackson hole", "boj", "bce", "ecb")


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    return float(x) if isinstance(x, (int, float)) else None


def _macro_tilt(vix: Optional[float], dxy_trend: Optional[str]) -> Optional[dict[str, Any]]:
    """Régime macro : VIX élevé = risk-off (−) ; DXY en hausse = vent contraire crypto (−)."""
    parts: list[str] = []
    tilt = 0.0
    contributed = False
    v = _num(vix)
    if v is not None:
        contributed = True
        if v >= 25:
            tilt -= 0.5
            parts.append(f"VIX {v:.0f} (risk-off)")
        elif v >= 20:
            tilt -= 0.25
            parts.append(f"VIX {v:.0f} (prudence)")
        elif v <= 15:
            tilt += 0.3
            parts.append(f"VIX {v:.0f} (risk-on)")
        else:
            parts.append(f"VIX {v:.0f} (neutre)")
    if dxy_trend in ("up", "down"):
        contributed = True
        if dxy_trend == "up":
            tilt -= 0.3
            parts.append("DXY en hausse (liquidité ↓)")
        else:
            tilt += 0.3
            parts.append("DXY en baisse (liquidité ↑)")
    if not contributed:
        return None
    return {"dimension": "Macro", "tilt": _clamp(tilt), "note": ", ".join(parts)}


def _technical_tilt(
    price: Optional[float], support: Optional[float], resistance: Optional[float],
    trend_pct: Optional[float], rsi: Optional[float],
) -> Optional[dict[str, Any]]:
    """Technique BTC : tendance (prix vs MM), proximité support/résistance, RSI."""
    p = _num(price)
    parts: list[str] = []
    tilt = 0.0
    contributed = False
    t = _num(trend_pct)
    if t is not None:
        contributed = True
        if t > 0:
            tilt += 0.4
            parts.append(f"BTC au-dessus MM50 (+{t:.0f}%)")
        else:
            tilt -= 0.4
            parts.append(f"BTC sous MM50 ({t:.0f}%)")
    if p is not None:
        r = _num(resistance)
        s = _num(support)
        if r is not None and r > 0 and abs(r - p) / p <= 0.03:
            tilt -= 0.2
            parts.append(f"sous résistance {r:.0f}")
            contributed = True
        if s is not None and s > 0 and abs(p - s) / p <= 0.03:
            tilt += 0.2
            parts.append(f"sur support {s:.0f} (rebond possible)")
            contributed = True
    rv = _num(rsi)
    if rv is not None:
        contributed = True
        if rv <= 30:
            tilt += 0.2
            parts.append(f"RSI {rv:.0f} (survente)")
        elif rv >= 70:
            tilt -= 0.2
            parts.append(f"RSI {rv:.0f} (surachat)")
        else:
            parts.append(f"RSI {rv:.0f}")
    if not contributed:
        return None
    return {"dimension": "Technique", "tilt": _clamp(tilt), "note": ", ".join(parts)}


def _sentiment_tilt(fear_greed: Optional[float]) -> Optional[dict[str, Any]]:
    """Fear & Greed : CONTRARIAN — peur extrême = potentiel de rebond (+), avidité (−)."""
    fg = _num(fear_greed)
    if fg is None:
        return None
    if fg <= 20:
        tilt, lbl = 0.4, "peur extrême (contrarian haussier)"
    elif fg <= 35:
        tilt, lbl = 0.2, "peur (léger contrarian +)"
    elif fg >= 80:
        tilt, lbl = -0.4, "avidité extrême (risque de repli)"
    elif fg >= 65:
        tilt, lbl = -0.2, "avidité"
    else:
        tilt, lbl = 0.0, "neutre"
    return {"dimension": "Sentiment", "tilt": tilt, "note": f"F&G {fg:.0f} — {lbl}"}


def _derivatives_tilt(funding_annualized_pct: Optional[float]) -> Optional[dict[str, Any]]:
    """Funding BTC : excès de longs (funding ↑↑) = risque de purge (−) ; excès de
    shorts (funding ↓↓) = short squeeze potentiel (+)."""
    f = _num(funding_annualized_pct)
    if f is None:
        return None
    if f >= 30:
        tilt, lbl = -0.3, f"funding +{f:.0f}% ann. (longs en excès)"
    elif f <= -10:
        tilt, lbl = 0.3, f"funding {f:.0f}% ann. (shorts en excès → squeeze ?)"
    else:
        tilt, lbl = 0.0, f"funding {f:.0f}% ann. (sain)"
    return {"dimension": "Dérivés", "tilt": tilt, "note": lbl}


def _momentum_tilt(change_7d: Optional[float]) -> Optional[dict[str, Any]]:
    """Momentum BTC 7j (poids modéré : la tendance court terme peut se prolonger)."""
    c = _num(change_7d)
    if c is None:
        return None
    if c >= 5:
        tilt = 0.2
    elif c <= -5:
        tilt = -0.2
    else:
        tilt = 0.0
    return {"dimension": "Momentum", "tilt": tilt, "note": f"BTC {c:+.1f}% sur 7j"}


def _event_risk(
    calendar_events: Optional[list[dict[str, Any]]],
    polymarket: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Risque événementiel ≤ 7j : catalyseurs macro datés + incertitude Polymarket.

    Plus il y a de catalyseurs imminents (et plus l'issue Fed est incertaine), plus
    les queues (bull/bear) s'élargissent au détriment du scénario neutre. NB : la
    volatilité implicite (DVOL) PRICE DÉJÀ le calendrier connu — on distingue donc
    le risque de SURPRISE (NFP/CPI, ou Fed à l'issue incertaine) qui s'ajoute
    au-delà de ce que la vol implicite capture.
    """
    _fed_kw = ("fomc", "fed", "taux")
    events: list[dict[str, Any]] = []
    fed_present = False
    for e in (calendar_events or []):
        if not isinstance(e, dict):
            continue
        da = e.get("days_ahead")
        label = str(e.get("label") or e.get("title") or "")
        if isinstance(da, (int, float)) and 0 <= da <= 7 and label:
            low = label.lower()
            if any(k in low for k in _HIGH_IMPACT):
                events.append({"label": label, "days_ahead": int(da)})
                if any(k in low for k in _fed_kw):
                    fed_present = True
    events.sort(key=lambda x: x["days_ahead"])
    fed = (polymarket or {}).get("fed_bars") or {}
    dom_pct = _num(fed.get("dominant_pct"))
    fed_uncertain = bool(dom_pct is not None and dom_pct < 80)
    # Surprises = catalyseurs NON déjà neutralisés par le marché : on retire UN
    # événement Fed si son issue est quasi certaine (Polymarket dominant ≥ 80%).
    fed_priced = fed_present and dom_pct is not None and dom_pct >= 80
    surprise_count = max(0, len(events) - (1 if fed_priced else 0))
    return {"events": events[:4], "count": len(events),
            "surprise_count": surprise_count, "fed_present": fed_present,
            "fed_uncertain": fed_uncertain}


def _polymarket_summary(polymarket: Optional[dict[str, Any]]) -> dict[str, Any]:
    pm = polymarket or {}
    fed = pm.get("fed_bars") or {}
    extra = []
    for m in (pm.get("extra_markets") or [])[:3]:
        if isinstance(m, dict) and m.get("question") is not None:
            extra.append({"question": m.get("question"),
                          "probability_pct": m.get("probability_pct")})
    out: dict[str, Any] = {"extra": extra}
    if fed.get("dominant"):
        out["fed_dominant"] = fed.get("dominant")
        out["fed_dominant_pct"] = fed.get("dominant_pct")
        out["meeting_hint"] = fed.get("meeting_hint")
    return out


def compute_scenario_scaffold(
    *,
    btc_price: Any = None,
    implied_move_7d_pct: Any = None,
    polymarket: Optional[dict[str, Any]] = None,
    vix: Any = None,
    dxy_trend: Optional[str] = None,
    fear_greed: Any = None,
    btc_funding_pct: Any = None,
    btc_support: Any = None,
    btc_resistance: Any = None,
    btc_trend_pct: Any = None,
    btc_rsi: Any = None,
    btc_change_7d: Any = None,
    calendar_events: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Construit l'échafaudage déterministe des scénarios (voir docstring module)."""
    tilts = [t for t in (
        _macro_tilt(vix, dxy_trend),
        _technical_tilt(btc_price, btc_support, btc_resistance, btc_trend_pct, btc_rsi),
        _sentiment_tilt(fear_greed),
        _derivatives_tilt(btc_funding_pct),
        _momentum_tilt(btc_change_7d),
    ) if t is not None]

    move = _num(implied_move_7d_pct)
    # Il faut un minimum de matière objective pour proposer un prior honnête.
    if len(tilts) < 2 and move is None:
        return {"available": False}

    # Biais directionnel net (moyenne pondérée des tilts disponibles).
    if tilts:
        _num_w = sum(_DIM_WEIGHTS.get(t["dimension"], 0.1) * t["tilt"] for t in tilts)
        _den_w = sum(_DIM_WEIGHTS.get(t["dimension"], 0.1) for t in tilts)
        net_tilt = _clamp(_num_w / _den_w) if _den_w else 0.0
    else:
        net_tilt = 0.0

    evr = _event_risk(calendar_events, polymarket)
    # Dispersion (largeur des queues). La vol implicite (DVOL) est PRIMAIRE car elle
    # price déjà le calendrier connu ; le risque événementiel n'ajoute qu'un petit
    # surplus pour les surprises NON pricées (NFP/CPI, ou Fed à l'issue incertaine).
    if move is not None:
        vol_d = _clamp((move - 3.0) / 12.0, 0.0, 0.6)
        event_topup = min(0.05 * evr["surprise_count"], 0.15)
        if evr["fed_present"] and evr["fed_uncertain"]:
            event_topup = min(event_topup + 0.10, 0.20)
        dispersion = round(min(vol_d + event_topup, 0.65), 3)
    else:
        # Pas de DVOL : le calendrier sert de PROXY (plus fort, faute de mieux).
        proxy = 0.13 * evr["count"] + (0.12 if evr["fed_uncertain"] else 0.0)
        dispersion = round(min(proxy, 0.5), 3)

    # Prior : neutre comprimé par la dispersion ; reste réparti selon le biais.
    neutral = max(0.25, min(0.60, 0.55 - 0.35 * dispersion))
    remaining = 1.0 - neutral
    bull_share = _clamp(0.5 + net_tilt * 0.5, -1, 1)
    bull_share = max(0.10, min(0.90, bull_share))
    bullish = remaining * bull_share
    bearish = remaining * (1.0 - bull_share)
    # Arrondi entier somme 100 (le résidu va au neutre).
    _bear = int(round(bearish * 100))
    _bull = int(round(bullish * 100))
    _neu = 100 - _bear - _bull
    prior = {"bearish": _bear, "neutral": _neu, "bullish": _bull}

    pm = _polymarket_summary(polymarket)

    # v26 (W-A6) — RANGE ATTENDU 7j DÉTERMINISTE : prix × (1 ± move implicite).
    # C'est LUI le range du scénario neutre. L'audit v25 a vu la résistance
    # long-horizon ($82,416, +34%) présentée comme borne d'un range hebdo
    # « compatible ±5.6% » : les deux notions sont désormais séparées et
    # étiquetées à la source.
    _px = _num(btc_price)
    expected_range_7d = None
    if _px and move is not None:
        expected_range_7d = {
            "low": round(_px * (1 - move / 100), 0),
            "high": round(_px * (1 + move / 100), 0),
            "label": f"±{move:.1f}% (DVOL) sur 7j",
        }

    # v26 (W-A6) — HORIZON des niveaux techniques : un support/résistance à
    # plus de max(1.5×move, 8%) du prix ne peut PAS borner un range 7j — il
    # est étiqueté « long terme » pour que le LLM (et le lecteur) ne le
    # confonde plus avec le range hebdo.
    _far_pct = max((move or 0) * 1.5, 8.0)

    def _lvl(value: Any) -> Optional[dict[str, Any]]:
        v = _num(value)
        if not v or not _px:
            return {"value": round(v, 0)} if v else None
        dist = (v - _px) / _px * 100
        return {
            "value": round(v, 0),
            "distance_pct": round(dist, 1),
            "horizon": "long terme" if abs(dist) > _far_pct else "hebdo",
        }

    _sup_lvl = _lvl(btc_support)
    _res_lvl = _lvl(btc_resistance)

    # Drivers suggérés par scénario (le LLM les enrichit + cite la source).
    _neg = [t["note"] for t in tilts if t["tilt"] < -0.05]
    _pos = [t["note"] for t in tilts if t["tilt"] > 0.05]
    _evt = [f"{e['label']} (J+{e['days_ahead']})" for e in evr["events"]]

    def _lvl_note(lvl: Optional[dict[str, Any]], kind: str) -> list[str]:
        if not lvl:
            return []
        _h = f" ({lvl['horizon']}, {lvl['distance_pct']:+.1f}%)" if lvl.get("horizon") else ""
        return [f"{kind} {lvl['value']:.0f}{_h}"]

    drivers = {
        "bearish": _neg + ([f"catalyseurs : {', '.join(_evt)}"] if _evt else [])
        + _lvl_note(_sup_lvl, "cassure support"),
        "neutral": ([f"Polymarket {pm.get('fed_dominant')} {pm.get('fed_dominant_pct')}%"]
                    if pm.get("fed_dominant") else [])
        + ([f"move implicite 7j ±{move:.1f}% (DVOL)"] if move is not None else [])
        # v26 — le range NEUTRE est le range ATTENDU (DVOL), plus jamais
        # l'écart support↔résistance technique (qui peut être ±30%).
        + ([f"range attendu 7j {expected_range_7d['low']:.0f}–{expected_range_7d['high']:.0f} $ "
            f"({expected_range_7d['label']})"] if expected_range_7d else []),
        "bullish": _pos
        + _lvl_note(_res_lvl, "franchissement résistance"),
    }

    return {
        "available": True,
        "implied_move_7d_pct": round(move, 1) if move is not None else None,
        # v26 (W-A6) — LE range du scénario neutre (déterministe, DVOL).
        "expected_range_7d": expected_range_7d,
        "net_tilt": round(net_tilt, 2),
        "dispersion": dispersion,
        "factor_tilts": [
            {"dimension": t["dimension"], "tilt": round(t["tilt"], 2), "note": t["note"]}
            for t in tilts
        ],
        "event_risk": evr,
        "polymarket": pm,
        "key_levels": {
            "support": round(_num(btc_support), 0) if _num(btc_support) else None,
            "resistance": round(_num(btc_resistance), 0) if _num(btc_resistance) else None,
            # v26 — distance au prix + horizon (« hebdo » / « long terme ») pour
            # que ces niveaux ne soient plus recyclés en bornes de range 7j.
            "support_detail": _sup_lvl,
            "resistance_detail": _res_lvl,
        },
        "prior": prior,
        "drivers": drivers,
    }
