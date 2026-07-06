"""Zones de liquidation ESTIMÉES (v27 · SO2) — gratuites et honnêtes.

Les cartes de liquidation temps réel (Coinglass) sont payantes. On estime
donc les zones « magnétiques » par construction : un long ouvert AU PRIX
ACTUEL avec un levier standard L est liquidé ≈ prix × (1 − 1/L) (marge
isolée, maintenance ignorée) ; un short ≈ prix × (1 + 1/L). Les paquets de
positions étant ouverts en continu autour du prix courant, ces niveaux
concentrent statistiquement des liquidations — ce sont des AIMANTS de prix
connus (les mèches vont les chercher).

ÉTIQUETÉ « estimation » partout : ce n'est PAS un carnet de liquidations
réel. Le biais funding / long-short indique quel côté est le plus chargé.
"""

from __future__ import annotations

from typing import Any, Optional

_LEVERAGES = (10, 25, 50, 100)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ") + " $"
    if abs(v) >= 1:
        return f"{v:,.2f} $"
    return f"{v:.4f} $"


def compute_liquidation_zones(
    price: Any, *,
    funding_annualized_pct: Any = None,
    long_short_ratio: Any = None,
) -> dict[str, Any]:
    """Zones de liquidation estimées autour du prix courant.

    Returns:
        ``{available, price, long_zones: [{leverage, level, level_label,
        dist_pct}], short_zones: [...], bias, bias_note, method_note}``.
        ``bias`` ∈ {"long_heavy", "short_heavy", None} selon funding + L/S.
    """
    px = _num(price)
    if not px or px <= 0:
        return {"available": False, "reason": "prix indisponible"}

    def _zone(level: float, lev: int) -> dict[str, Any]:
        return {
            "leverage": lev,
            "level": round(level, 6),
            "level_label": _fmt(level),
            "dist_pct": round((level - px) / px * 100, 1),
        }

    long_zones = [_zone(px * (1 - 1.0 / lev), lev) for lev in _LEVERAGES]
    short_zones = [_zone(px * (1 + 1.0 / lev), lev) for lev in _LEVERAGES]

    fund = _num(funding_annualized_pct)
    ls = _num(long_short_ratio)
    bias = None
    bias_note = None
    if fund is not None and fund <= -10:
        bias = "short_heavy"
        # Bornes citées dans le SENS du mouvement (proche → lointaine), comme
        # pour la purge des longs ci-dessous.
        bias_note = (f"funding {fund:+.1f}%/an : shorts dominants — les zones "
                     "AU-DESSUS du prix sont les plus chargées (risque de "
                     "short squeeze vers "
                     f"{short_zones[1]['level_label']}–{short_zones[0]['level_label']})")
    elif (fund is not None and fund >= 15) or (ls is not None and ls >= 1.5):
        bias = "long_heavy"
        _f = f"funding {fund:+.1f}%/an" if fund is not None else f"L/S {ls:.2f}"
        bias_note = (f"{_f} : longs dominants — les zones SOUS le prix sont "
                     "les plus chargées (risque de purge vers "
                     f"{long_zones[1]['level_label']}–{long_zones[0]['level_label']})")

    return {
        "available": True,
        "price": round(px, 6),
        "long_zones": long_zones,
        "short_zones": short_zones,
        "bias": bias,
        "bias_note": bias_note,
        "method_note": ("zones ESTIMÉES par leviers standards (10/25/50/100×) "
                        "autour du prix courant — pas un carnet de "
                        "liquidations réel"),
    }
