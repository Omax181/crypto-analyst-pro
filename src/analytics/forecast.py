"""Prévisions DISTRIBUTIONNELLES à 30 jours et 12 mois — source unique.

Pourquoi un module dédié (audit zero-trust, 01/10/2026)
=======================================================
Les cibles affichées à côté d'une décision du moteur venaient de l'ancien plan
V30 : une « Cible 30j » sur la résistance la plus proche, une « Cible 6-12m »
en reconquête d'ATH (Fibonacci 0,618 → ATH), et des scénarios bull/base/bear
pondérés par une probabilité heuristique bornée à 0,30–0,70. Le moteur
déclare pourtant ne rien prévoir à 30 jours et rejette tout ancrage sur l'ATH.
Le mail affichait donc, pour la MÊME reco, une méthode et son contraire.

Omar préfère les prévisions distributionnelles à une fausse précision, à
condition qu'elles restent lisibles. Ce module publie donc :

* une **fourchette 80 %** (P10–P90) du cours à l'horizon, sous l'hypothèse
  DÉCLARÉE d'une dérive nulle (aucun alpha n'est estimable : ±15,8 points à
  30 jours) et de rendements log-normaux à la volatilité mesurée ;
* pour une décision du moteur, la **réévaluation implicite** : le cours que
  donnerait le retour de la valorisation à son ancre mesurée — un CALCUL
  conditionné à la prémisse du moteur, jamais une probabilité.

Aucune probabilité n'est fabriquée : la fourchette est une propriété de la
volatilité mesurée, pas un pronostic directionnel.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# Quantile 90 % de la loi normale centrée réduite : P10–P90 couvre 80 % de la
# distribution. Convention de présentation, pas un paramètre de décision.
Z_P90 = 1.2815515655446004

HYPOTHESE = ("dérive nulle (aucun alpha estimable), rendements log-normaux à "
             "la volatilité quotidienne mesurée")


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and math.isfinite(f) else None


def distribution(price: Any, daily_vol_pct: Any,
                 horizon_days: float) -> Optional[dict[str, Any]]:
    """Fourchette P10–P90 du cours à ``horizon_days`` jours.

    ``P_q = P · exp(z_q · σ_j · √h)`` avec σ_j la volatilité quotidienne
    mesurée. Médiane = cours actuel (dérive nulle).

    Returns:
        ``{horizon_days, p10, p50, p90, p10_pct, p90_pct, sigma_h_pct,
        hypothese}`` ou ``None`` si le cours ou la volatilité manquent.
    """
    p, vol = _num(price), _num(daily_vol_pct)
    h = _num(horizon_days)
    if p is None or p <= 0 or vol is None or vol <= 0 or not h or h <= 0:
        return None
    sigma_h = vol / 100.0 * math.sqrt(h)
    lo = p * math.exp(-Z_P90 * sigma_h)
    hi = p * math.exp(Z_P90 * sigma_h)
    return {
        "horizon_days": int(h),
        "p10": lo, "p50": p, "p90": hi,
        "p10_pct": round((lo / p - 1.0) * 100.0, 1),
        "p90_pct": round((hi / p - 1.0) * 100.0, 1),
        "sigma_h_pct": round(sigma_h * 100.0, 1),
        "hypothese": HYPOTHESE,
    }


def implied_revaluation(price: Any, potential_pct: Any) -> Optional[float]:
    """Cours impliqué par le retour de la valorisation à son ancre mesurée."""
    p, pot = _num(price), _num(potential_pct)
    if p is None or p <= 0 or pot is None:
        return None
    return p * (1.0 + pot / 100.0)


def engine_view(decision: dict[str, Any],
                candidate: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Tous les chiffres d'une décision du moteur, prêts pour le rendu.

    Un seul objet, construit depuis la décision et le candidat — jamais depuis
    une sortie du modèle de langage. C'est ce que lisent le tableau, la fiche,
    Telegram et la persistance.
    """
    c = candidate or {}
    pot = decision.get("potential") or {}
    req = decision.get("required") or {}
    comp = req.get("components") or {}
    size = decision.get("size") or {}
    price = _num(c.get("price"))
    vol = _num(c.get("daily_vol_pct"))
    p_eff = _num(decision.get("potential_effective_pct"))
    if p_eff is None:
        p_eff = _num(pot.get("potential_pct"))
    preuve_req = req.get("preuve") or {}
    view = {
        "asset": decision.get("asset"),
        "price": price,
        "potential_pct": p_eff,
        "potential_conservative_pct": _num(
            decision.get("potential_conservative_effective_pct")
            if decision.get("potential_conservative_effective_pct") is not None
            else pot.get("potential_conservative_pct")),
        "required_pct": _num(req.get("required_pct")),
        "required_class": preuve_req.get("classe"),
        "costs_pct": _num(comp.get("costs_pct")),
        "benchmark_pct": _num(comp.get("benchmark_pct")),
        "risk_premium_pct": _num(comp.get("risk_premium_pct")),
        "multiplier": _num(req.get("multiplier")),
        "anchor": pot.get("anchor"),
        "basis": pot.get("basis"),
        "evidence_class": (pot.get("preuve") or {}).get("classe"),
        "size_pct": _num(size.get("pct")),
        "size_band": size.get("band"),
        "size_band_label": size.get("band_label"),
        "size_tier": size.get("kind"),
        "size_usd": _num(size.get("usd")),
        "size_usd_band": size.get("usd_band"),
        "size_basis": size.get("basis"),
        "coverage_pct": (round(_num(c.get("coverage")) * 100)
                         if _num(c.get("coverage")) is not None else None),
        "weight_pct": _num((decision.get("size_bands") or {}).get("weight_pct")),
        "effective_weight_pct": _num(
            (decision.get("size_bands") or {}).get("effective_weight_pct")),
        "beta_portfolio": _num((decision.get("size_bands") or {}).get("beta_portfolio")),
        "implied_price": implied_revaluation(price, p_eff),
        "required_price": implied_revaluation(price, req.get("required_pct")),
        "forecast_30d": distribution(price, vol, 30),
        "forecast_365d": distribution(price, vol, 365),
        "horizon_days": _num(req.get("horizon_days")),
        "funding_case": decision.get("funding_case"),
        "sell_asset": decision.get("sell_asset"),
        "reason": decision.get("reason"),
    }
    view["display"] = display(view)
    return view


# ── chaînes d'affichage (format français unique : src.utils.numfmt) ───────

def _pct(v: Any, *, signe: bool = False, nd: int = 1) -> str:
    f = _num(v)
    if f is None:
        return "—"
    r = round(f, nd)
    txt = f"{abs(r):.{nd}f}".replace(".", ",")
    if txt.endswith(",0"):
        txt = txt[:-2]
    pre = ("+" if r >= 0 else "−") if signe else ("−" if r < 0 else "")
    return f"{pre}{txt} %"


def _fourchette(fc: Optional[dict[str, Any]]) -> str:
    from src.utils.numfmt import fr_usd, fr_num
    if not isinstance(fc, dict):
        return "non publiée (volatilité non mesurée)"
    return f"{fr_num(fc.get('p10'))}–{fr_usd(fc.get('p90'))}"


def _dollars(v: Any) -> str:
    """Montant en dollars ENTIERS, milliers en espace fine : « 1 597 $ »."""
    f = _num(v)
    if f is None:
        return "—"
    return f"{int(round(f)):,}".replace(",", " ") + " $"


def display(view: dict[str, Any]) -> dict[str, str]:
    """Chaînes prêtes à rendre pour une décision du moteur (tableau + fiche)."""
    from src.utils.numfmt import fr_usd
    band = view.get("size_band") or []
    taille = (f"{_pct(band[0])[:-2]}–{_pct(band[1])}" if len(band) == 2
              else _pct(view.get("size_pct")))
    pot, req = view.get("potential_pct"), view.get("required_pct")
    return {
        "band_30d": _fourchette(view.get("forecast_30d")),
        "band_365d": _fourchette(view.get("forecast_365d")),
        "implied": (f"{fr_usd(view.get('implied_price'))} "
                    f"({_pct(pot, signe=True)})"
                    if view.get("implied_price") is not None else "—"),
        "required_price": (fr_usd(view.get("required_price"))
                           if view.get("required_price") is not None else "—"),
        "pot_req": f"{_pct(pot, signe=True)} / {_pct(req)}",
        "potential": _pct(pot, signe=True),
        "potential_conservative": _pct(view.get("potential_conservative_pct"),
                                       signe=True),
        "required": _pct(req),
        "costs": _pct(view.get("costs_pct")),
        "benchmark": _pct(view.get("benchmark_pct")),
        "premium": _pct(view.get("risk_premium_pct")),
        "size": taille,
        "size_usd": (f"{_dollars((view.get('size_usd_band') or [None])[0])[:-2]}–"
                     f"{_dollars((view.get('size_usd_band') or [None, None])[1])}"
                     if view.get("size_usd_band") else "—"),
        "weight": _pct(view.get("weight_pct")),
        "effective_weight": _pct(view.get("effective_weight_pct")),
        "row_note": (f"⚙ moteur · potentiel {_pct(pot, signe=True)} ≥ requis "
                     f"{_pct(req)} · {view.get('basis') or ''}").strip(" ·"),
    }


def forecast_display(view: dict[str, Any]) -> dict[str, str]:
    """Chaînes de fourchettes pour une ligne sans décision du moteur."""
    return {"band_30d": _fourchette(view.get("forecast_30d")),
            "band_365d": _fourchette(view.get("forecast_365d"))}
