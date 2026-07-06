"""Plan de trade DÉTERMINISTE par actif (v27 · TH1/TH2/ES1/ES2/ES3/RE1/RE2/RE3).

Transforme les niveaux calculés (``key_levels``) en un PLAN falsifiable :
    • invalidation chiffrée (TH1) — le prix qui TUE la thèse, avec sa base ;
    • cible 30 j (ES1) en FOURCHETTE (ES2) — prochaine résistance ± ATR ;
    • cible cycle (ES1/ES2) — chemin vers l'ATH réel (fib 0.618 → ATH) ;
    • R:R (RE2) — (cible − prix) / (prix − invalidation) ;
    • EV prospectif 30 j (ES3) — p(hausse) × upside − p(baisse) × downside,
      p dérivée de signaux objectifs (RSI, tendance, funding, tilt marché),
      bornée [0.30, 0.70] : une ESTIMATION indicative, jamais une certitude ;
    • bull / base / bear par actif (TH2) avec probabilités sommant à 100 ;
    • zone d'accumulation + DCA 3 tranches (RE3) ;
    • sizing suggéré en % du PTF et $ (RE1) — plafonné par la concentration,
      SANS jamais considérer le cash comme une contrainte (Omar peut injecter).

Tout est Python : le LLM commente ces chiffres, il ne les invente plus.
Chaque champ est None-tolérant (dégradation gracieuse si une donnée manque).
"""

from __future__ import annotations

from typing import Any, Optional

from src.analytics.key_levels import compute_key_levels
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN out


def _fmt_usd(v: Optional[float]) -> Optional[str]:
    """« 61 949 $ » (espace fine insécable) / « 0.0850 $ » — compact FR."""
    if v is None:
        return None
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}".replace(",", " ") + " $"
    if a >= 1:
        return f"{v:,.2f} $"
    if a >= 0.01:
        return f"{v:.4f} $"
    return f"{v:.6f} $"


def _pct_fr(v: float, nd: int = 1) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(round(v, nd))}%".replace(".", ",")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── p(hausse) 30 j — signaux objectifs, bornée, indicative ────────────────

def _prob_up_30d(
    readout: dict[str, Any],
    funding_annualized_pct: Any = None,
    market_net_tilt: Any = None,
) -> float:
    """Probabilité indicative de hausse à 30 j depuis les signaux disponibles.

    Chaque signal contribue un tilt ∈ [−1, 1] ; la moyenne pondérée est
    convertie en probabilité bornée [0.30, 0.70] — on n'affirme jamais une
    quasi-certitude depuis 4 indicateurs techniques.
    """
    tilts: list[tuple[float, float]] = []  # (tilt, poids)
    rsi = _num((readout or {}).get("rsi"))
    if rsi is not None:
        # Contrarian doux : survendu → tilt positif, suracheté → négatif.
        tilts.append((_clamp((50.0 - rsi) / 30.0, -1, 1), 1.0))
    trend = _num((readout or {}).get("trend_7d_pct"))
    if trend is not None:
        # Momentum : la tendance 7j se prolonge plus souvent qu'elle ne s'inverse.
        tilts.append((_clamp(trend / 10.0, -1, 1), 0.8))
    ma200 = _num((readout or {}).get("ma200_rel_pct"))
    if ma200 is not None:
        tilts.append((_clamp(ma200 / 25.0, -1, 1), 0.6))
    fund = _num(funding_annualized_pct)
    if fund is not None:
        # Funding très négatif = shorts en excès → carburant contrarian haussier.
        tilts.append((_clamp(-fund / 25.0, -1, 1), 0.6))
    tilt_mkt = _num(market_net_tilt)
    if tilt_mkt is not None:
        tilts.append((_clamp(tilt_mkt, -1, 1), 0.8))
    if not tilts:
        return 0.5
    num = sum(t * w for t, w in tilts)
    den = sum(w for _, w in tilts)
    return round(_clamp(0.5 + 0.2 * (num / den), 0.30, 0.70), 2)


# ── sizing suggéré (RE1) — % PTF plafonné par la concentration ────────────

def suggest_sizing(
    *,
    action_type: str,
    weight_pct: Any = None,
    ptf_value_usd: Any = None,
    is_core: bool = False,
    position_value_usd: Any = None,
) -> Optional[dict[str, Any]]:
    """Geste chiffré suggéré : % du PTF + $ + poids avant→après.

    Le CASH N'EST JAMAIS une contrainte (Omar peut injecter des fonds
    externes) : le sizing s'exprime en % du PTF et en $, sans conditionner à
    une vente. Garde-fou concentration : pas de renfort proposé au-delà de
    20% du PTF sur un même actif (12% pour un satellite).
    """
    w = _num(weight_pct)
    ptf = _num(ptf_value_usd)
    act = (action_type or "").lower()
    if act in ("bullish", "renforcer", "buy", "accumuler"):
        cap = 20.0 if is_core else 12.0
        if w is not None and w >= cap:
            return {
                "add_pct_ptf": 0.0,
                "note": (f"déjà {w:.0f}% du PTF (plafond {cap:.0f}%) — "
                         "renfort non suggéré, concentration"),
            }
        add = (2.0 if is_core else 1.0) if (w is None or w < cap - 3) else 0.5
        out: dict[str, Any] = {"add_pct_ptf": add}
        if ptf:
            out["add_usd"] = round(ptf * add / 100.0, 0)
        if w is not None:
            out["weight_before_pct"] = round(w, 1)
            out["weight_after_pct"] = round(w + add, 1)
            _usd = f" (≈ {_fmt_usd(out.get('add_usd'))})" if out.get("add_usd") else ""
            out["note"] = (f"+{add:.1f}% du PTF{_usd} · porte "
                           f"{w:.0f}% → {w + add:.1f}% du PTF")
        return out
    if act in ("bearish", "alléger", "alleger", "sell", "sortir"):
        pv = _num(position_value_usd)
        trim_pct = 50.0 if not is_core else 25.0
        out = {"trim_pct_position": trim_pct}
        if pv:
            out["trim_usd"] = round(pv * trim_pct / 100.0, 0)
            out["note"] = (f"−{trim_pct:.0f}% de la position "
                           f"(≈ {_fmt_usd(out['trim_usd'])})")
        return out
    return None


# ── plan complet par actif ────────────────────────────────────────────────

def compute_asset_plan(
    symbol: str,
    closes: list[float],
    volumes: Optional[list[float]] = None,
    *,
    price: Optional[float] = None,
    ath: Any = None,
    ath_suspect: bool = False,
    funding_annualized_pct: Any = None,
    market_net_tilt: Any = None,
    key_levels_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Construit le plan déterministe d'un actif (voir docstring module).

    Args:
        symbol: ticker.
        closes: clôtures chronologiques (≥ 30).
        volumes: volumes alignés (optionnel).
        price: prix spot live (défaut : dernière clôture).
        ath: ATH réel CoinGecko (None → pas de cible cycle).
        ath_suspect: True = ATH de listing illiquide → cible cycle omise.
        funding_annualized_pct: funding annualisé Binance (contrarian).
        market_net_tilt: biais directionnel marché (scaffold), ∈ [−1, 1].
        key_levels_result: résultat ``compute_key_levels`` déjà calculé
            (évite un recalcul) ; sinon calculé ici.

    Returns:
        ``{available, symbol, price, invalidation, target_30d, target_cycle,
        rr_30d, prob_up_30d, ev_30d_pct, scenarios, accumulation_zone, dca,
        plan_line}`` — ``available=False`` si données insuffisantes.
    """
    kl = key_levels_result or compute_key_levels(
        symbol, closes, volumes, price=price)
    if not kl or not kl.get("available"):
        return {"available": False, "symbol": symbol,
                "reason": (kl or {}).get("reason") or "niveaux indisponibles"}
    px = _num(kl.get("price"))
    if not px or px <= 0:
        return {"available": False, "symbol": symbol, "reason": "prix invalide"}

    sups = kl.get("supports") or []
    ress = kl.get("resistances") or []
    readout = kl.get("readout") or {}
    atr = _num(readout.get("atr_abs")) or px * 0.03  # repli : 3% du prix

    # ── TH1 — INVALIDATION : le prix qui tue la thèse. 2e support (le 1er
    # peut être bruité par l'intraday) ; repli : 1er support − 1 ATR ; repli
    # ultime : −8% (jamais de plan sans invalidation).
    if len(sups) >= 2:
        inv_level = _num(sups[1].get("level"))
        inv_basis = sups[1].get("basis")
    elif sups:
        inv_level = (_num(sups[0].get("level")) or px * 0.95) - atr
        inv_basis = f"{sups[0].get('basis')} − 1 ATR"
    else:
        inv_level, inv_basis = px * 0.92, "repli −8% (aucun support détecté)"
    if inv_level is None or inv_level >= px or inv_level <= 0:
        # ``inv_level <= 0`` : micro-prix avec ATR > support (« s0 − 1 ATR »
        # négatif) — un plan n'a jamais d'invalidation à 0 ou négative.
        inv_level, inv_basis = px * 0.92, "repli −8% (support incohérent)"
    invalidation = {
        "level": round(inv_level, 6),
        "level_label": _fmt_usd(inv_level),
        "basis": inv_basis,
        "dist_pct": round((inv_level - px) / px * 100, 1),
    }

    # ── ES1/ES2 — CIBLE 30 j : première résistance à ≥ +3% (sinon la
    # suivante), en FOURCHETTE ± 1 ATR (honnête, pas de fausse précision).
    tgt = None
    for r in ress:
        lv = _num(r.get("level"))
        if lv and (lv - px) / px * 100 >= 3.0:
            tgt = (lv, r.get("basis"))
            break
    if tgt is None and ress:
        lv = _num(ress[-1].get("level"))
        if lv:
            tgt = (lv, ress[-1].get("basis"))
    if tgt is None:
        tgt = (px + 2 * atr, "extension +2 ATR (aucune résistance détectée)")
    target_30d = {
        "level": round(tgt[0], 6),
        "level_label": _fmt_usd(tgt[0]),
        "basis": tgt[1],
        "low": round(max(tgt[0] - atr, px), 6),
        "high": round(tgt[0] + atr, 6),
        "low_label": _fmt_usd(max(tgt[0] - atr, px)),
        "high_label": _fmt_usd(tgt[0] + atr),
        "upside_pct": round((tgt[0] - px) / px * 100, 1),
    }

    # ── ES1/ES2 — CIBLE CYCLE : chemin fib 0.618 → ATH réel (jamais
    # au-delà) ; omise si ATH suspect (listing illiquide) ou déjà proche.
    target_cycle = None
    ath_v = _num(ath)
    if ath_v and ath_v > px * 1.10 and not ath_suspect:
        low_c = px + (ath_v - px) * 0.618
        target_cycle = {
            "low": round(low_c, 6),
            "high": round(ath_v, 6),
            "low_label": _fmt_usd(low_c),
            "high_label": _fmt_usd(ath_v),
            "upside_pct": round((ath_v - px) / px * 100, 0),
            "kind": ("cycle" if (ath_v - px) / px * 100 >= 250 else "6-12m"),
            "basis": "fib 0.618 → ATH réel",
        }

    # ── RE2 — R:R sur le plan 30 j.
    risk = px - inv_level
    reward = target_30d["level"] - px
    rr_30d = round(reward / risk, 1) if risk > 0 and reward > 0 else None

    # ── ES3 — EV prospectif 30 j (indicatif).
    p_up = _prob_up_30d(readout, funding_annualized_pct, market_net_tilt)
    upside_pct = (target_30d["level"] - px) / px * 100
    downside_pct = (inv_level - px) / px * 100  # négatif
    ev = round(p_up * upside_pct + (1 - p_up) * downside_pct, 1)

    # ── TH2 — BULL / BASE / BEAR par actif, probabilités sommant à 100.
    # Base comprimée quand le tilt est net ; bull/bear répartis selon p_up.
    tilt_strength = abs(p_up - 0.5) * 2  # 0..0.4 (p_up borné [0.30, 0.70])
    p_base = int(round(55 - 20 * tilt_strength))
    p_bull = int(round((100 - p_base) * p_up))
    p_bear = 100 - p_base - p_bull
    _s1 = _num(sups[0].get("level")) if sups else None
    scenarios = {
        "bull": {
            "probability_pct": p_bull,
            "level": target_30d["high"],
            "level_label": _fmt_usd(target_30d["high"]),
            "condition": (f"cassure de {target_30d['level_label']} "
                          f"({target_30d['basis']}) en clôture"),
        },
        "base": {
            "probability_pct": p_base,
            "low": round(_s1, 6) if _s1 else invalidation["level"],
            "high": target_30d["level"],
            "range_label": (f"{_fmt_usd(_s1 if _s1 else invalidation['level'])}"
                            f" – {target_30d['level_label']}"),
            "condition": "consolidation entre support et résistance",
        },
        "bear": {
            "probability_pct": p_bear,
            "level": round(inv_level - atr, 6),
            "level_label": _fmt_usd(inv_level - atr),
            "condition": (f"cassure de {invalidation['level_label']} "
                          f"({invalidation['basis']}) en clôture"),
        },
    }

    # ── RE3 — ZONE D'ACCUMULATION + DCA 3 tranches (contrarian, profil Omar).
    s1 = _s1 if (_s1 and _s1 < px) else px - atr
    accumulation_zone = {
        "low": round(min(inv_level + 0.25 * atr, s1), 6),
        "high": round(min(px, s1 + 0.5 * atr), 6),
    }
    accumulation_zone["low_label"] = _fmt_usd(accumulation_zone["low"])
    accumulation_zone["high_label"] = _fmt_usd(accumulation_zone["high"])
    dca = [
        {"price": round(px, 6), "price_label": _fmt_usd(px),
         "weight_pct": 40, "basis": "prix actuel"},
        {"price": round(s1, 6), "price_label": _fmt_usd(s1),
         "weight_pct": 30, "basis": (sups[0].get("basis") if sups
                                     else "prix − 1 ATR")},
        {"price": round(max(inv_level + 0.25 * atr, inv_level), 6),
         "price_label": _fmt_usd(max(inv_level + 0.25 * atr, inv_level)),
         "weight_pct": 30, "basis": "au-dessus de l'invalidation"},
    ]

    # ── ligne FR compacte (rendu mail + Telegram).
    parts = [
        f"Invalidation {invalidation['level_label']} "
        f"({invalidation['basis']} · {_pct_fr(invalidation['dist_pct'])})",
        f"Cible 30j {target_30d['level_label']} "
        f"[{target_30d['low_label']}–{target_30d['high_label']}]",
    ]
    if rr_30d is not None:
        parts.append(f"R:R {str(rr_30d).replace('.', ',')}")
    parts.append(f"EV 30j {_pct_fr(ev)} (p↑ {int(p_up * 100)}%)")
    parts.append(f"Zone d'accu {accumulation_zone['low_label']}"
                 f"–{accumulation_zone['high_label']}")
    plan_line = " · ".join(parts)

    return {
        "available": True,
        "symbol": symbol,
        "price": round(px, 6),
        "price_label": _fmt_usd(px),
        "invalidation": invalidation,
        "target_30d": target_30d,
        "target_cycle": target_cycle,
        "rr_30d": rr_30d,
        "prob_up_30d": p_up,
        "ev_30d_pct": ev,
        "ev_note": "estimation indicative (signaux techniques + funding), pas une certitude",
        "scenarios": scenarios,
        "accumulation_zone": accumulation_zone,
        "dca": dca,
        "plan_line": plan_line,
    }
