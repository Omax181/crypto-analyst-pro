"""Génération de mini-graphiques pour les thèses (prix + bandes de Bollinger).

Rend un PNG compact encodé en base64, intégrable directement dans l'email via
``<img src="data:image/png;base64,...">`` (pas de pièce jointe, compatible
clients mail). Dégradation gracieuse : si matplotlib ou les données manquent,
renvoie ``None`` et l'email s'affiche sans graphique.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Optional

from src.data_sources import coingecko
from src.utils.logger import get_logger

logger = get_logger(__name__)


def price_bollinger_png(symbol: str, *, days: int = 90) -> Optional[str]:
    """Génère un graphique prix + Bollinger pour un actif, encodé base64.

    Args:
        symbol: ticker.
        days: profondeur d'historique.

    Returns:
        Chaîne base64 du PNG (sans préfixe data:), ou ``None`` si indisponible.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib indisponible, graphique ignoré.")
        return None

    series = coingecko.get_price_volume_series(symbol, days=days)
    if not series or len(series.get("closes", [])) < 20:
        return None
    closes = series["closes"]

    # Bollinger 20 périodes glissantes.
    period = 20
    upper, lower, mid = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None); lower.append(None); mid.append(None)
            continue
        window = closes[i - period + 1 : i + 1]
        sma = sum(window) / period
        std = (sum((x - sma) ** 2 for x in window) / period) ** 0.5
        mid.append(sma); upper.append(sma + 2 * std); lower.append(sma - 2 * std)

    x = list(range(len(closes)))
    try:
        fig, ax = plt.subplots(figsize=(5.2, 1.9), dpi=110)
        ax.plot(x, closes, color="#0f172a", linewidth=1.3, label="Prix")
        xb = [i for i in x if mid[i] is not None]
        if xb:
            ax.plot(xb, [mid[i] for i in xb], color="#2563eb", linewidth=0.8, alpha=0.7)
            ax.fill_between(
                xb, [lower[i] for i in xb], [upper[i] for i in xb],
                color="#2563eb", alpha=0.10,
            )
        ax.set_title(f"{symbol} · {days}j · Bollinger(20,2)", fontsize=8, color="#334155")
        ax.tick_params(labelsize=6, colors="#94a3b8")
        ax.set_xticks([])
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#e2e8f0")
        fig.tight_layout(pad=0.4)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Graphique %s échoué : %s", symbol, exc)
        return None


def charts_for_theses(theses: list[dict[str, Any]], *, limit: int = 4) -> dict[str, str]:
    """Génère les graphiques pour les thèses (limité pour la taille de l'email).

    Args:
        theses: liste de thèses (doit contenir ``asset``).
        limit: nombre max de graphiques.

    Returns:
        Dict ``{symbol: base64_png}`` (peut être vide).
    """
    out: dict[str, str] = {}
    for th in theses[:limit]:
        sym = th.get("asset")
        if not sym:
            continue
        png = price_bollinger_png(sym)
        if png:
            out[sym] = png
    return out
