"""Digests compacts pour l'injection dans les prompts (économie de tokens).

Transforme les données analytiques riches (techniques détaillées, on-chain
avancé, options, corrélations macro, feedback) en lignes COURTES et lisibles.
Objectif : donner à Gemini la matière chiffrée pour un raisonnement croisé
SANS dumper du JSON verbeux (budget ~4000 tokens de données injectées).

Chaque fonction est tolérante aux données manquantes et renvoie ``""`` /
structure vide plutôt que de planter.
"""

from __future__ import annotations

from typing import Any, Optional


def _num(v: Any, nd: int = 2) -> Optional[str]:
    """Formate un nombre proprement, ou None si non numérique."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f"{f:.{nd}f}".rstrip("0").rstrip(".") if nd else str(int(f))


def build_asset_technical(tv_daily: dict[str, Any], tech_adv: dict[str, Any]) -> dict[str, Any]:
    """Construit le détail technique compact d'un actif (valeurs brutes).

    Args:
        tv_daily: signaux TradingView du timeframe daily (rsi, macd, stoch…).
        tech_adv: sortie ``technical_advanced`` (bollinger, SR, MA, flash).

    Returns:
        Dict compact ``{summary, rsi, macd_hist, stoch_k, adx, bollinger,
        cross, price_vs_sma200_pct, dist_support_pct, dist_resistance_pct,
        flash}``. Clés numériques omises si absentes.
    """
    tv_daily = tv_daily or {}
    tech_adv = tech_adv if tech_adv.get("available") else {}
    boll = tech_adv.get("bollinger") or {}
    sr = tech_adv.get("support_resistance") or {}
    mas = tech_adv.get("moving_averages") or {}

    out: dict[str, Any] = {}
    parts: list[str] = []

    rsi = _num(tv_daily.get("rsi"), 0)
    if rsi is not None:
        out["rsi"] = round(float(tv_daily.get("rsi")), 1)
        zone = " (survente)" if out["rsi"] < 30 else " (surachat)" if out["rsi"] > 70 else ""
        parts.append(f"RSI {rsi}{zone}")
    mh = tv_daily.get("macd_hist")
    if mh is not None:
        out["macd_hist"] = round(float(mh), 6)
        parts.append(f"MACD {'haussier' if mh > 0 else 'baissier'}")
    stoch = _num(tv_daily.get("stoch_k"), 0)
    if stoch is not None:
        out["stoch_k"] = round(float(tv_daily.get("stoch_k")), 1)
        parts.append(f"Stoch {stoch}")
    adx = _num(tv_daily.get("adx"), 0)
    if adx is not None:
        out["adx"] = round(float(tv_daily.get("adx")), 1)
        if out["adx"] >= 25:
            parts.append(f"ADX {adx} (tendance forte)")
    if boll.get("available") and boll.get("position"):
        out["bollinger"] = boll["position"]
        parts.append(f"Bollinger {boll['position']}")
    if mas.get("cross"):
        out["cross"] = mas["cross"]
        pv = mas.get("price_vs_sma200_pct")
        if pv is not None:
            out["price_vs_sma200_pct"] = pv
        parts.append(
            f"{'golden' if mas['cross'] == 'golden' else 'death'} cross"
            + (f", prix {'+' if (pv or 0) >= 0 else ''}{pv}% vs SMA200" if pv is not None else "")
        )
    if sr.get("available"):
        if sr.get("dist_to_support_pct") is not None:
            out["dist_support_pct"] = sr["dist_to_support_pct"]
        if sr.get("dist_to_resistance_pct") is not None:
            out["dist_resistance_pct"] = sr["dist_to_resistance_pct"]
    flash = tech_adv.get("flash_signals") or []
    if flash:
        out["flash"] = flash
    out["summary"] = " · ".join(parts) if parts else "données techniques limitées"
    return out


def onchain_line(cm: dict[str, Any]) -> str:
    """Ligne compacte on-chain avancé (Coin Metrics) BTC/ETH."""
    if not cm.get("available"):
        return ""
    bits: list[str] = []
    for sym, d in (cm.get("assets") or {}).items():
        seg = []
        if d.get("mvrv") is not None:
            seg.append(f"MVRV {d['mvrv']} ({d.get('mvrv_zone', '?')})")
        if d.get("nvt") is not None:
            seg.append(f"NVT {d['nvt']}")
        if d.get("realized_price_ratio") is not None:
            rr = d["realized_price_ratio"]
            seg.append(f"prix/realized {rr} ({'profit' if rr >= 1 else 'perte'} latent)")
        if d.get("active_addresses_trend_pct") is not None:
            seg.append(f"adresses actives {d['active_addresses_trend_pct']:+}% / 7j")
        if seg:
            bits.append(f"{sym}: " + ", ".join(seg))
    return " | ".join(bits)


def options_line(opt: dict[str, Any]) -> str:
    """Ligne compacte dérivés options (Deribit) BTC/ETH."""
    if not opt.get("available"):
        return ""
    bits: list[str] = []
    for sym, d in (opt.get("assets") or {}).items():
        seg = []
        if d.get("put_call_ratio") is not None:
            pcr = d["put_call_ratio"]
            bias = "couverture/prudence" if pcr > 1 else "appétit calls" if pcr < 0.7 else "neutre"
            seg.append(f"put/call {pcr} ({bias})")
        if d.get("max_pain") is not None:
            gap = d.get("max_pain_gap_pct")
            seg.append(
                f"max pain {d['max_pain']}"
                + (f" ({gap:+}% vs spot)" if gap is not None else "")
            )
        if d.get("dvol") is not None:
            seg.append(f"DVOL {d['dvol']}")
        if seg:
            bits.append(f"{sym}: " + ", ".join(seg))
    return " | ".join(bits)


def macro_correlation_line(corr: dict[str, Any]) -> str:
    """Ligne compacte corrélations 30j BTC ↔ macro + régime."""
    if not corr.get("available"):
        return ""
    items = ", ".join(
        f"{c['label']} {c['corr']:+}" for c in corr.get("correlations", [])
    )
    return f"Corrélations 30j BTC ↔ {items} · régime: {corr.get('regime_hint', '')}"


def calendar_line(
    prints: dict[str, Any],
    polymarket: dict[str, Any],
    upcoming: dict[str, Any] | None = None,
) -> str:
    """Ligne compacte calendrier : à venir + derniers chiffres + consensus.

    A10/C6 : les publications À VENIR (dates réelles FRED) passent en premier —
    c'est l'information la plus actionnable (« NFP demain → attendre »). Aucune
    invention possible : si ``upcoming`` est vide, rien n'est affiché.
    """
    parts: list[str] = []
    if upcoming and upcoming.get("available"):
        seg = []
        for e in upcoming.get("events", [])[:4]:
            da = e.get("days_ahead")
            when = (
                "aujourd'hui" if da == 0
                else "demain" if da == 1
                else f"dans {da}j"
            )
            seg.append(f"{e['label']} {when} ({e['date']})")
        if seg:
            parts.append("À venir: " + " · ".join(seg))
    if prints.get("available"):
        seg = []
        for p in prints.get("prints", []):
            delta = p.get("delta")
            dtxt = f" (Δ{delta:+})" if isinstance(delta, (int, float)) else ""
            seg.append(f"{p['label']} {p['value']}{dtxt}")
        if seg:
            parts.append("Derniers chiffres: " + " · ".join(seg))
    if polymarket.get("available"):
        mk = polymarket.get("markets", [])
        seg = [f"{m['question']} {m['probability_pct']}%" for m in mk[:3] if m.get("question")]
        if seg:
            parts.append("Consensus marché (Polymarket): " + " · ".join(seg))
    return " | ".join(parts)


_BETA_FACTOR_LABEL = {"dxy": "DXY", "sp500": "S&P500", "vix": "VIX", "gold": "Or"}


def per_asset_beta_line(beta_data: dict[str, Any]) -> str:
    """Ligne compacte bêtas par actif vs macro (DXY/S&P/VIX) — recommandation A9.

    Ex. ``TAO: β-DXY −0.42 (corr −0.55) · β-S&P500 +0.68`` — chiffre le lien
    macro → crypto position par position. Vide si non disponible.
    """
    if not beta_data.get("available"):
        return ""
    bits: list[str] = []
    for sym, factors in (beta_data.get("by_asset") or {}).items():
        seg = []
        for fac, d in factors.items():
            beta = d.get("beta")
            if beta is None:
                continue
            corr = d.get("corr")
            ctxt = f" (corr {corr:+})" if isinstance(corr, (int, float)) else ""
            seg.append(f"β-{_BETA_FACTOR_LABEL.get(fac, fac)} {beta:+}{ctxt}")
        if seg:
            bits.append(f"{sym}: " + " · ".join(seg))
    return " | ".join(bits)


def feedback_line(perf: dict[str, Any]) -> str:
    """Ligne compacte feedback : actifs à surveiller + erreurs récentes."""
    if not perf.get("available"):
        return ""
    parts: list[str] = []
    caution = perf.get("caution_assets") or []
    if caution:
        wr = perf.get("by_asset", {})
        seg = [f"{s} ({wr.get(s, {}).get('win_rate_pct', '?')}%)" for s in caution]
        parts.append("⚠️ Win rate faible (prudence accrue): " + ", ".join(seg))
    errs = perf.get("recent_errors") or []
    if errs:
        seg = [f"{e['asset']} {e['action']} (il y a {e['age_days']}j)" for e in errs[:3]]
        parts.append("Erreurs récentes invalidées: " + " · ".join(seg))
    return " | ".join(parts)
