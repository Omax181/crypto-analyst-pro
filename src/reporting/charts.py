"""Graphiques — SPEC V31 §11 (I27, I59, I60).

Un seul graphique existe : LE CONTRAT. Il montre la série de clôtures, l'entrée,
la cible, l'invalidation, et la bande de bruit de l'horizon. Rien d'autre.

Les six variantes de la v30 (analyse adaptative, Bollinger, suivi, évolution du
portefeuille, comparaison BTC…) sont supprimées : elles portaient chacune leur
propre formatage — ``_fmt_level`` était une seconde autorité numérique, hors du
contrôle de la locale — et aucune n'était rattachée à une décision.

Toutes les annotations passent par ``core.formatter``. Un graphique est une
SORTIE : il est balayé par le contrôle de format I60 au même titre que le HTML.
"""

from __future__ import annotations

import io
from typing import Any, Optional

from src.core import formatter as fmt
from src.utils.logger import get_logger

logger = get_logger(__name__)

C_PRICE = "#0f172a"
C_ENTRY = "#334155"
C_TARGET = "#3B6D11"
C_STOP = "#A32D2D"
C_BAND = "#cbd5e1"
C_AXIS = "#e2e8f0"
C_TICK = "#94a3b8"

_LABEL_BOX = dict(facecolor="white", edgecolor="none", alpha=0.8, pad=0.4)


def _import_plt():
    """Import paresseux : matplotlib est optionnel, son absence n'est pas fatale."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception as exc:                                    # noqa: BLE001
        logger.info("matplotlib indisponible — aucun graphique : %s", exc)
        return None


def _save(fig, plt) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def contract_png(
    *, asset: str, closes: list[float], entry: float, target: float,
    stop: float, sigma_h_pct: Optional[float] = None,
) -> Optional[bytes]:
    """Graphique d'un contrat. ``None`` si le rendu est impossible.

    Les trois niveaux tracés sont ceux du CONTRAT SCORÉ, jamais ceux d'un plan
    opérationnel révisé : le graphique montre ce qui est évalué (I15).
    """
    series = [float(c) for c in (closes or [])
              if isinstance(c, (int, float)) and c > 0]
    if len(series) < 20 or not (entry and target and stop):
        return None
    plt = _import_plt()
    if plt is None:
        return None
    try:
        window = series[-90:]
        x = list(range(len(window)))
        fig, ax = plt.subplots(figsize=(7.2, 3.4))

        # Bande de bruit de l'horizon, autour de l'entrée.
        if sigma_h_pct and sigma_h_pct > 0:
            half = entry * float(sigma_h_pct) / 100.0
            ax.fill_between(x, entry - half, entry + half, color=C_BAND,
                            alpha=0.35, linewidth=0,
                            label=f"bruit ±{fmt.pct(sigma_h_pct, sign=False)}")

        ax.plot(x, window, color=C_PRICE, linewidth=1.6)
        for value, color, label in (
                (entry, C_ENTRY, f"entrée {fmt.price(entry)}"),
                (target, C_TARGET, f"cible {fmt.price(target)}"),
                (stop, C_STOP, f"invalidation {fmt.price(stop)}")):
            ax.axhline(value, color=color, linewidth=1.1,
                       linestyle="--" if color != C_ENTRY else "-")
            ax.annotate(label, xy=(len(x) - 1, value), xytext=(-4, 3),
                        textcoords="offset points", ha="right", fontsize=8,
                        color=color, bbox=_LABEL_BOX)

        ax.set_title(f"{asset} — contrat en cours", fontsize=10,
                     color=C_PRICE, loc="left")
        ax.set_xticks([])
        ax.tick_params(axis="y", colors=C_TICK, labelsize=8)
        for side in ("top", "right", "bottom"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color(C_AXIS)
        ax.grid(axis="y", color=C_AXIS, linewidth=0.6)
        ax.set_axisbelow(True)
        # Les étiquettes d'axe passent AUSSI par le formateur : un axe
        # matplotlib produit « 1,234.5 » par défaut, hors locale. Le formateur
        # est branché sur le LOCATOR (et non via set_yticklabels, qui fige les
        # positions et désynchronise étiquettes et graduations).
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt.price(v)))
        return _save(fig, plt)
    except Exception as exc:                                    # noqa: BLE001
        logger.info("Graphique %s non produit : %s", asset, exc)
        return None


def charts_for_contracts(
    emitted: list[dict[str, Any]], closes_by_asset: dict[str, list[float]],
    *, limit: int = 3,
) -> dict[str, bytes]:
    """Graphiques des contrats émis, indexés par identifiant CID."""
    out: dict[str, bytes] = {}
    for item in emitted[:limit]:
        cand = item.get("candidate")
        if cand is None or not cand.emittable:
            continue
        png = contract_png(
            asset=cand.asset, closes=closes_by_asset.get(cand.asset) or [],
            entry=cand.entry, target=cand.target, stop=cand.stop,
            sigma_h_pct=cand.sigma_h_pct)
        if png:
            out[f"contract_{cand.asset.lower()}"] = png
    return out
