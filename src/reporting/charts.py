"""Génération de mini-graphiques d'ANALYSE pour les mails (images CID PNG).

v20 (audit C1/M1) — PNG en octets bruts, attachés en images CID
(``<img src="cid:chart_XXX">``) : Gmail supprime les data-URI et les <svg> inline.

v23 — graphiques d'analyse ADAPTATIFS & LISIBLES. Le type d'analyse s'adapte au
MOTEUR de la thèse (le signal qui la porte), pour montrer LA bonne lecture
graphique, pas un gabarit figé :
  • support/résistance proche  → cours + niveaux S/R CHIFFRÉS + MM50 (tendance) ;
  • survente / momentum (RSI)  → cours + MM50 + RSI(14) mis en avant ;
  • tendance / croisement MM    → cours + MM50/100/200 (golden/death cross) ;
  • conviction / drawdown / valeur → cours long + retracements Fibonacci + MM200 ;
  • compression / volatilité    → cours + bandes de Bollinger(20,2).
Chaque graphique garde un sous-panneau RSI et reste ÉPURÉ (≤ 3 familles
d'overlays) pour la lisibilité : couleurs distinctes, labels chiffrés sur fond
blanc, jamais de ligne « ATH » hors-échelle qui écrase la courbe. Les indicateurs
sont calculés sur tout l'historique (365j) puis on AFFICHE une fenêtre récente.
Dégradation gracieuse : matplotlib/données absents → ``None``.
"""

from __future__ import annotations

import io
from typing import Any, Optional

from src.data_sources import coingecko
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Palette cohérente avec la charte des mails.
_C_PRICE = "#0f172a"
_C_MA50 = "#2563eb"
_C_MA100 = "#BA7517"
_C_MA200 = "#7c3aed"
_C_SUP = "#3B6D11"
_C_RES = "#A32D2D"
_C_FIB = "#94a3b8"
_C_RSI = "#7c3aed"
_C_BOLL = "#2563eb"
_C_PTF = "#534AB7"
_C_BTC = "#E8A33D"
_C_AXIS = "#e2e8f0"
_C_TICK = "#94a3b8"
_LBL_BBOX = dict(facecolor="white", edgecolor="none", alpha=0.78, pad=0.4)
_MA_STYLE = {50: (_C_MA50, "MM50"), 100: (_C_MA100, "MM100"), 200: (_C_MA200, "MM200")}


def _import_plt():
    """Importe matplotlib (Agg) ou None si indisponible. Tait le bruit de polices."""
    try:
        import logging as _logging
        _logging.getLogger("matplotlib.font_manager").setLevel(_logging.ERROR)
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        logger.warning("matplotlib indisponible, graphique ignoré.")
        return None


def _load_closes(symbol: str, *, days: int = 90) -> Optional[list[float]]:
    """Charge la série de clôtures CoinGecko (≥ 20 points) ou None."""
    series = coingecko.get_price_volume_series(symbol, days=days)
    if not series or len(series.get("closes", [])) < 20:
        return None
    return series["closes"]


def _load_series(symbol: str, *, days: int = 90) -> Optional[dict[str, list[float]]]:
    """v26 (C1) — clôtures ET volumes (le volume confirme cassures/rebonds)."""
    series = coingecko.get_price_volume_series(symbol, days=days)
    if not series or len(series.get("closes", [])) < 20:
        return None
    return {"closes": series["closes"], "volumes": series.get("volumes") or []}


def _draw_volume_underlay(ax, volumes: list[float], n_shown: int) -> None:
    """v26 (C1) — barres de volume DISCRÈTES sous le panneau prix.

    Axe secondaire borné à 4× le max → les barres occupent le quart bas du
    panneau sans écraser la courbe de prix. N'ajoute RIEN si la série est
    incomplète (jamais de volume inventé).
    """
    if not volumes or len(volumes) < n_shown:
        return
    vols = volumes[-n_shown:]
    if not any(v and v > 0 for v in vols):
        return
    try:
        axv = ax.twinx()
        axv.bar(range(len(vols)), [v or 0 for v in vols],
                color="#94a3b8", alpha=0.22, width=1.0, zorder=1)
        axv.set_ylim(0, max(v or 0 for v in vols) * 4)
        axv.set_yticks([])
        for spine in axv.spines.values():
            spine.set_visible(False)
    except Exception:  # noqa: BLE001 — le volume est un bonus, jamais bloquant
        pass


def _style_axis(ax) -> None:
    """Applique le style maison (ticks discrets, pas d'axe X, spines clairs)."""
    ax.tick_params(labelsize=6, colors=_C_TICK)
    ax.set_xticks([])
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(_C_AXIS)


def _new_fig(plt, title: str):
    """Figure/axe simple (compat ``price_bollinger_png``)."""
    fig, ax = plt.subplots(figsize=(5.2, 1.9), dpi=110)
    ax.set_title(title, fontsize=8, color="#334155")
    _style_axis(ax)
    return fig, ax


def _save(fig, plt) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _fmt_level(v: Optional[float]) -> str:
    """Formate un niveau de prix lisible (décimales selon l'ordre de grandeur)."""
    if not isinstance(v, (int, float)):
        return ""
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:,.2f}"
    if a >= 0.01:
        return f"{v:.4f}"
    return f"{v:.6f}"


def _sma(closes: list[float], period: int) -> list[Optional[float]]:
    """Moyenne mobile simple (alignée sur closes, None avant amorçage)."""
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n < period:
        return out
    window_sum = sum(closes[:period])
    out[period - 1] = window_sum / period
    for i in range(period, n):
        window_sum += closes[i] - closes[i - period]
        out[i] = window_sum / period
    return out


def _bollinger(closes: list[float], period: int = 20):
    """Bandes de Bollinger(period, 2) glissantes (listes alignées, None au début)."""
    upper, lower, mid = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None); lower.append(None); mid.append(None)
            continue
        window = closes[i - period + 1:i + 1]
        sma = sum(window) / period
        std = (sum((x - sma) ** 2 for x in window) / period) ** 0.5
        mid.append(sma); upper.append(sma + 2 * std); lower.append(sma - 2 * std)
    return upper, lower, mid


def _rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """RSI(period) façon Wilder. Liste alignée sur closes (None avant amorçage)."""
    n = len(closes)
    rsis: list[Optional[float]] = [None] * n
    if n < period + 1:
        return rsis
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    avg_gain = sum(max(d, 0) for d in deltas[:period]) / period
    avg_loss = sum(-min(d, 0) for d in deltas[:period]) / period
    for i in range(period, n):
        d = deltas[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + -min(d, 0)) / period
        if avg_loss == 0:
            rsis[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsis[i] = 100 - 100 / (1 + rs)
    return rsis


def _fib_levels(high: Optional[float], low: Optional[float]) -> dict[str, float]:
    """Retracements de Fibonacci entre un plus-bas et un plus-haut de swing."""
    if not (isinstance(high, (int, float)) and isinstance(low, (int, float))):
        return {}
    if high <= low:
        return {}
    diff = high - low
    return {"0.236": high - 0.236 * diff, "0.382": high - 0.382 * diff,
            "0.5": high - 0.5 * diff, "0.618": high - 0.618 * diff}


# --------------------------------------------------------------------------- #
# Sélection ADAPTATIVE de l'analyse (le « cerveau » graphique)
# --------------------------------------------------------------------------- #
# Chaque mode = un jeu d'overlays FOCALISÉ (lisible) sur le panneau prix ; le RSI
# est toujours en sous-panneau. On choisit selon le signal DOMINANT de la thèse.
_MODES: dict[str, dict[str, Any]] = {
    "trend": {"ma": (50, 100, 200), "sr": False, "fib": False, "boll": False,
              "suffix": "tendance · MM50/100/200", "days": 200},
    "support_resistance": {"ma": (50,), "sr": True, "fib": False, "boll": False,
                           "suffix": "support / résistance", "days": 150},
    "rsi": {"ma": (50,), "sr": False, "fib": False, "boll": False,
            "suffix": "momentum · RSI(14)", "days": 150},
    "fibonacci": {"ma": (200,), "sr": False, "fib": True, "boll": False,
                  "suffix": "retracements Fibonacci", "days": 300},
    "bollinger": {"ma": (), "sr": False, "fib": False, "boll": True,
                  "suffix": "compression · Bollinger(20,2)", "days": 150},
}


def _select_analysis(thesis: dict[str, Any]) -> str:
    """Choisit l'analyse graphique la plus PERTINENTE selon le moteur de la thèse.

    Lecture du signal dominant (labels de ``thesis_scoring.signals``) puis repli
    sur le type de thèse. Objectif : montrer la lecture qui PORTE la décision.
    """
    ts = thesis.get("thesis_scoring") or {}
    labels = " ".join(str(s.get("label", "")).lower()
                      for s in (ts.get("signals") or []))
    if "support" in labels or "résistance" in labels or "resistance" in labels:
        return "support_resistance"
    if "bollinger" in labels or "compression" in labels or "volatilit" in labels:
        return "bollinger"
    if any(k in labels for k in ("rsi", "survente", "surachat", "stochas",
                                 "momentum", "divergence")):
        return "rsi"
    if "fib" in labels or "retracement" in labels:
        return "fibonacci"
    if any(k in labels for k in ("drawdown", "ath", "sous-éval", "sous-eval",
                                 "capitulation", "realized", "mvrv")):
        return "fibonacci"
    if (ts.get("thesis_type") == "conviction"
            and "tendance" not in labels and "moyenne" not in labels):
        return "fibonacci"
    if any(k in labels for k in ("moyenne mobile", "mm50", "mm200", "cross",
                                 "golden", "death", "tendance")):
        return "trend"
    return "trend"  # défaut : vue tendance (MM50/100/200 + RSI), riche et lisible


# --------------------------------------------------------------------------- #
# Renderer d'analyse adaptatif (price + overlays choisis + RSI)
# --------------------------------------------------------------------------- #
def _render_analysis(plt, symbol: str, closes_full: list[float], mode: str,
                     levels: Optional[dict[str, Any]],
                     volumes_full: Optional[list[float]] = None) -> bytes:
    """Rend le graphique d'analyse pour le ``mode`` choisi (price + RSI + overlays)."""
    cfg = _MODES.get(mode, _MODES["trend"])
    display_days = cfg["days"]

    ma_series = {p: _sma(closes_full, p) for p in cfg["ma"]}
    rsis = _rsi(closes_full, 14)
    boll = _bollinger(closes_full, 20) if cfg["boll"] else None

    n = len(closes_full)
    start = max(0, n - display_days)
    cl = closes_full[start:]
    x = list(range(len(cl)))
    last_i = len(cl) - 1
    cur = cl[-1]
    rs = rsis[start:]

    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(6.6, 3.9), dpi=140, height_ratios=[3, 1],
        gridspec_kw={"hspace": 0.12},
    )

    # ── Panneau prix ──
    ax.plot(x, cl, color=_C_PRICE, linewidth=1.6, zorder=6, label="cours")
    # v26 (C1) — volume en sous-couche discrète (confirme cassures/rebonds).
    if volumes_full:
        _draw_volume_underlay(ax, volumes_full, len(cl))
    has_legend = False

    for p in cfg["ma"]:
        series = ma_series[p][start:]
        xs = [i for i, v in enumerate(series) if v is not None]
        if len(xs) >= 2:
            color, lab = _MA_STYLE[p]
            ax.plot(xs, [series[i] for i in xs], color=color, linewidth=1.0,
                    alpha=0.9, label=lab, zorder=4)
            has_legend = True

    if cfg["boll"] and boll is not None:
        up, lo, mid = (s[start:] for s in boll)
        xb = [i for i, v in enumerate(mid) if v is not None]
        if xb:
            ax.plot(xb, [mid[i] for i in xb], color=_C_BOLL, linewidth=0.8,
                    alpha=0.7, label="Bollinger(20,2)", zorder=3)
            ax.fill_between(xb, [lo[i] for i in xb], [up[i] for i in xb],
                            color=_C_BOLL, alpha=0.10, zorder=1)
            has_legend = True

    if cfg["fib"]:
        fib = _fib_levels(max(cl), min(cl))
        for lvl, val in fib.items():
            ax.axhline(val, color=_C_FIB, linewidth=0.6, linestyle=":", alpha=0.6, zorder=2)
            ax.text(0, val, f"fib {lvl}", fontsize=5.5, color=_C_FIB,
                    va="center", ha="left", bbox=_LBL_BBOX)

    if cfg["sr"]:
        sup = res = None
        if isinstance(levels, dict):
            sup = levels.get("support") or levels.get("nearest_support")
            res = levels.get("resistance") or levels.get("nearest_resistance")
        win = cl[-min(len(cl), 60):]
        if not isinstance(sup, (int, float)):
            sup = min(win)
        if not isinstance(res, (int, float)):
            res = max(win)
        ax.axhline(res, color=_C_RES, linewidth=1.1, linestyle="--", alpha=0.85, zorder=3)
        ax.axhline(sup, color=_C_SUP, linewidth=1.1, linestyle="--", alpha=0.85, zorder=3)
        ax.text(last_i, res, f"R {_fmt_level(res)}", fontsize=6.5, color=_C_RES,
                va="bottom", ha="right", bbox=_LBL_BBOX)
        ax.text(last_i, sup, f"S {_fmt_level(sup)}", fontsize=6.5, color=_C_SUP,
                va="top", ha="right", bbox=_LBL_BBOX)

    # Prix courant (toujours, repère net).
    ax.scatter([last_i], [cur], color=_C_PRICE, s=12, zorder=7)
    ax.text(last_i, cur, f" {_fmt_level(cur)}", fontsize=6.5, color=_C_PRICE,
            va="center", ha="left", fontweight="bold", zorder=8, bbox=_LBL_BBOX)

    ax.set_title(f"{symbol} · {len(cl)}j · {cfg['suffix']}", fontsize=8.5, color="#334155")
    if has_legend:
        # En mode Fibonacci, les labels « fib … » occupent le haut-GAUCHE → on place
        # la légende en haut-DROITE pour éviter tout chevauchement (lisibilité).
        _leg_loc = "upper right" if cfg["fib"] else "upper left"
        ax.legend(loc=_leg_loc, fontsize=5.5, ncol=4, frameon=True,
                  facecolor="white", framealpha=0.7, edgecolor="none",
                  handlelength=1.3, columnspacing=1.0, borderpad=0.3)
    _style_axis(ax)
    ax.margins(x=0.02)

    # ── Sous-panneau RSI ──
    xr = [i for i, v in enumerate(rs) if v is not None]
    axr.axhspan(70, 100, color=_C_RES, alpha=0.06)
    axr.axhspan(0, 30, color=_C_SUP, alpha=0.06)
    if xr:
        axr.plot(xr, [rs[i] for i in xr], color=_C_RSI, linewidth=1.1)
    axr.axhline(70, color=_C_RES, linewidth=0.6, linestyle=":", alpha=0.7)
    axr.axhline(30, color=_C_SUP, linewidth=0.6, linestyle=":", alpha=0.7)
    axr.set_ylim(0, 100)
    axr.set_yticks([30, 70])
    cur_rsi = next((rs[i] for i in range(len(rs) - 1, -1, -1)
                    if rs[i] is not None), None)
    if cur_rsi is not None:
        axr.text(last_i, cur_rsi, f"RSI {cur_rsi:.0f}", fontsize=6, color=_C_RSI,
                 va="center", ha="right", fontweight="bold", bbox=_LBL_BBOX)
    _style_axis(axr)
    axr.margins(x=0.02)

    return _save(fig, plt)


def _render_bollinger(plt, symbol: str, closes: list[float], days: int) -> bytes:
    """Cours + bandes de Bollinger(20,2) — conservé pour ``price_bollinger_png``."""
    upper, lower, mid = _bollinger(closes)
    x = list(range(len(closes)))
    fig, ax = _new_fig(plt, f"{symbol} · {days}j · Bollinger(20,2)")
    ax.plot(x, closes, color=_C_PRICE, linewidth=1.3)
    xb = [i for i in x if mid[i] is not None]
    if xb:
        ax.plot(xb, [mid[i] for i in xb], color=_C_BOLL, linewidth=0.8, alpha=0.7)
        ax.fill_between(xb, [lower[i] for i in xb], [upper[i] for i in xb],
                        color=_C_BOLL, alpha=0.10)
    return _save(fig, plt)


# --------------------------------------------------------------------------- #
# Évolution du portefeuille (hebdo) — valeur $ + performance vs BTC
# --------------------------------------------------------------------------- #
def _draw_value_panel(ax, vals: list[float], labels: list[str]) -> None:
    """Aire + ligne de la VALEUR du PTF en $, axe temporel en bas, $ à droite.

    v28 (3.B) — pixel-perfect : le 07/07, le label « $2,716 » (ha=right, sans
    marge) était rogné contre le bord droit et l'axe. On réserve désormais une
    MARGE DROITE explicite (xlim élargi) et on pose le label À DROITE du point
    (ha=left) dans cet espace, + une grille horizontale fine pour la lecture.
    """
    n = len(vals)
    x = list(range(n))
    up = vals[-1] >= vals[0]
    color = _C_SUP if up else _C_RES
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or (hi or 1.0)
    floor = lo - span * 0.12
    ceil = hi + span * 0.12
    # Grille horizontale fine SOUS la courbe (lecture des niveaux).
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=_C_AXIS, linewidth=0.5, alpha=0.55, zorder=0)
    ax.fill_between(x, floor, vals, color=color, alpha=0.13, zorder=2)
    ax.plot(x, vals, color=color, linewidth=2.0, solid_capstyle="round", zorder=4)
    ax.scatter([x[-1]], [vals[-1]], color=color, s=22, zorder=5,
               edgecolors="#ffffff", linewidths=0.8)
    # Marge droite = 16% de la fenêtre pour loger le point + son label sans rognure.
    ax.set_xlim(-0.02 * (n - 1), (n - 1) + 0.17 * (n - 1) + 0.4)
    ax.text(x[-1] + 0.03 * (n - 1) + 0.15, vals[-1], f"${vals[-1]:,.0f}",
            fontsize=7.5, color=color, va="center", ha="left",
            fontweight="bold", bbox=_LBL_BBOX, zorder=6, clip_on=False)
    ax.set_title("Valeur du portefeuille ($)", fontsize=9, color="#334155",
                 pad=6)
    ax.set_ylim(floor, ceil)
    # $ sur l'axe de droite, chiffres alignés (tabular via monospace-ish).
    ax.yaxis.tick_right()
    ax.tick_params(labelsize=6, colors=_C_TICK)
    try:
        from matplotlib.ticker import FuncFormatter, MaxNLocator
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune="both"))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    except Exception:  # noqa: BLE001
        pass
    for spine in ("top", "left", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_C_AXIS)
    _time_xticks(ax, labels, n)


def _draw_perf_panel(ax, perf_ptf: list[float], perf_btc: list[float],
                     labels: list[str]) -> None:
    """Performance cumulée (%) PTF vs BTC depuis le début de la fenêtre."""
    n = len(perf_ptf)
    x = list(range(n))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=_C_AXIS, linewidth=0.5, alpha=0.5, zorder=0)
    ax.axhline(0, color=_C_TICK, linewidth=0.8, linestyle=":", alpha=0.7, zorder=1)
    ax.fill_between(x, 0, perf_ptf, color=_C_PTF, alpha=0.10, zorder=2)
    ax.plot(x, perf_ptf, color=_C_PTF, linewidth=2.0, solid_capstyle="round",
            label="PTF", zorder=4)
    ax.plot(x, perf_btc, color=_C_BTC, linewidth=1.4, linestyle="--",
            label="BTC", zorder=3)
    ax.set_title("Performance · PTF vs BTC", fontsize=9, color="#334155", pad=6)
    # v28 (3.B) — label endpoint À DROITE du point, marge réservée (pas de rognure).
    ax.set_xlim(-0.02 * (n - 1), (n - 1) + 0.17 * (n - 1) + 0.4)
    ax.scatter([x[-1]], [perf_ptf[-1]], color=_C_PTF, s=18, zorder=5,
               edgecolors="#ffffff", linewidths=0.7)
    ax.text(x[-1] + 0.03 * (n - 1) + 0.15, perf_ptf[-1], f"{perf_ptf[-1]:+.0f}%",
            fontsize=7, color=_C_PTF, va="center", ha="left", fontweight="bold",
            bbox=_LBL_BBOX, zorder=6, clip_on=False)
    ax.legend(loc="upper left", fontsize=6.5, frameon=False, ncol=2)
    ax.yaxis.tick_right()
    ax.tick_params(labelsize=6, colors=_C_TICK)
    try:
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    except Exception:  # noqa: BLE001
        pass
    for spine in ("top", "left", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_C_AXIS)
    _time_xticks(ax, labels, n)


def _time_xticks(ax, labels: list[str], n: int) -> None:
    """Place 3 repères temporels (début / milieu / fin) si des labels existent."""
    clean = [(i, labels[i]) for i in range(min(len(labels), n))
             if labels[i]]
    if not clean:
        ax.set_xticks([])
        return
    idxs = sorted(set([clean[0][0], clean[len(clean) // 2][0], clean[-1][0]]))
    ax.set_xticks(idxs)
    ax.set_xticklabels([labels[i] for i in idxs], fontsize=5.5, color=_C_TICK)
    ax.tick_params(axis="x", length=0)


def portfolio_evolution_png(points: list[dict[str, Any]],
                            btc_points: Optional[list[float]] = None) -> Optional[bytes]:
    """Graphique d'évolution du PTF : valeur $ (gauche) + performance vs BTC (droite).

    Args:
        points: liste ``[{label, value}]`` (ancienne → récente), valeur du PTF en $.
        btc_points: prix BTC alignés (même longueur) → panneau de performance
            comparée (% depuis le début). Optionnel : sans lui, seul le panneau
            valeur $ est tracé.

    Returns:
        PNG (octets) ou None (matplotlib absent / < 3 points).
    """
    pts = [p for p in (points or []) if isinstance(p.get("value"), (int, float))]
    vals = [p["value"] for p in pts]
    if len(vals) < 3:
        return None
    plt = _import_plt()
    if plt is None:
        return None
    try:
        labels = [str(p.get("label", "")) for p in pts]
        perf_ptf = perf_btc = None
        if (btc_points and len(btc_points) == len(vals)
                and vals[0] and btc_points[0]
                and all(isinstance(b, (int, float)) and b for b in btc_points)):
            perf_ptf = [(v / vals[0] - 1) * 100 for v in vals]
            perf_btc = [(b / btc_points[0] - 1) * 100 for b in btc_points]

        if perf_ptf and perf_btc:
            fig, (axv, axp) = plt.subplots(
                1, 2, figsize=(9.0, 2.8), dpi=140, gridspec_kw={"wspace": 0.32})
            _draw_value_panel(axv, vals, labels)
            _draw_perf_panel(axp, perf_ptf, perf_btc, labels)
        else:
            fig, axv = plt.subplots(figsize=(6.8, 2.8), dpi=140)
            _draw_value_panel(axv, vals, labels)
        return _save(fig, plt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Graphique évolution PTF échoué : %s", exc)
        return None


# --------------------------------------------------------------------------- #
# API publique
# --------------------------------------------------------------------------- #
def chart_for_thesis(thesis: dict[str, Any]) -> Optional[bytes]:
    """Génère le graphique d'analyse ADAPTÉ à une thèse (octets PNG) ou None.

    Le type d'analyse est choisi par ``_select_analysis`` selon le signal qui
    porte la thèse ; rendu riche mais épuré (cf. module docstring).
    """
    sym = thesis.get("asset")
    if not sym:
        return None
    plt = _import_plt()
    if plt is None:
        return None
    series = _load_series(sym, days=365)
    if not series:
        return None
    # v28 (M-A17) — le DERNIER point du graphe = prix SPOT de la fiche. Le
    # 07/07, l'annotation du chart BTC disait 63 080 (clôture J-1 de la série
    # daily) sous une fiche titrée 63 214 $ : deux « prix actuels » différents
    # dans le même bloc. On substitue le spot au dernier close avant rendu.
    _spot_raw = thesis.get("current_price") or thesis.get("price")
    try:
        _spot = float(str(_spot_raw).replace(",", "").replace("$", "")
                      .replace(" ", "").replace(" ", ""))
    except (TypeError, ValueError):
        _spot = None
    closes = list(series["closes"])
    if _spot and _spot > 0 and closes:
        closes[-1] = _spot
    try:
        mode = _select_analysis(thesis)
        return _render_analysis(plt, sym, closes, mode,
                                thesis.get("support_resistance"),
                                volumes_full=series.get("volumes"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Graphique %s échoué : %s", sym, exc)
        return None


# --------------------------------------------------------------------------- #
# v26 (B8/A20) — graphiques de SUIVI pour les jours sans nouvelle reco
# --------------------------------------------------------------------------- #
def _render_tracking(plt, symbol: str, closes_full: list[float],
                     reco: dict[str, Any],
                     volumes_full: Optional[list[float]] = None) -> bytes:
    """Cours + MM50 + niveaux du PLAN (entrée/cible/stop) + RSI.

    La lecture qui compte pour une reco EN COURS : où est le prix par rapport
    au plan de trade. Chaque niveau est tracé et CHIFFRÉ.
    """
    display_days = 120
    ma50 = _sma(closes_full, 50)
    rsis = _rsi(closes_full, 14)
    n = len(closes_full)
    start = max(0, n - display_days)
    cl = closes_full[start:]
    x = list(range(len(cl)))
    last_i = len(cl) - 1
    cur = cl[-1]
    rs = rsis[start:]

    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(6.6, 3.9), dpi=140, height_ratios=[3, 1],
        gridspec_kw={"hspace": 0.12},
    )
    ax.plot(x, cl, color=_C_PRICE, linewidth=1.6, zorder=6, label="cours")
    if volumes_full:
        _draw_volume_underlay(ax, volumes_full, len(cl))
    series50 = ma50[start:]
    xs = [i for i, v in enumerate(series50) if v is not None]
    if len(xs) >= 2:
        ax.plot(xs, [series50[i] for i in xs], color=_C_MA50, linewidth=1.0,
                alpha=0.9, label="MM50", zorder=4)

    def _level(value: Any, color: str, label: str, style: str = "--") -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if v <= 0:
            return
        ax.axhline(v, color=color, linewidth=1.1, linestyle=style, alpha=0.85,
                   zorder=3)
        ax.text(0, v, f"{label} {_fmt_level(v)}", fontsize=6, color=color,
                va="bottom", ha="left", bbox=_LBL_BBOX, zorder=8)

    _level(reco.get("entry_price"), "#2563eb", "entrée", style=":")
    _level(reco.get("ct_target"), _C_SUP, "cible")
    _level(reco.get("stop_loss"), _C_RES, "stop")

    ax.scatter([last_i], [cur], color=_C_PRICE, s=12, zorder=7)
    ax.text(last_i, cur, f" {_fmt_level(cur)}", fontsize=6.5, color=_C_PRICE,
            va="center", ha="left", fontweight="bold", zorder=8, bbox=_LBL_BBOX)
    action = str(reco.get("action") or "").upper() or "SUIVI"
    ax.set_title(f"{symbol} · {len(cl)}j · suivi reco {action} — plan vs prix",
                 fontsize=8.5, color="#334155")
    ax.legend(loc="upper left", fontsize=5.5, ncol=2, frameon=True,
              facecolor="white", framealpha=0.7, edgecolor="none",
              handlelength=1.3, columnspacing=1.0, borderpad=0.3)
    _style_axis(ax)
    ax.margins(x=0.02)

    xr = [i for i, v in enumerate(rs) if v is not None]
    axr.axhspan(70, 100, color=_C_RES, alpha=0.06)
    axr.axhspan(0, 30, color=_C_SUP, alpha=0.06)
    if xr:
        axr.plot(xr, [rs[i] for i in xr], color=_C_RSI, linewidth=1.1)
    axr.axhline(70, color=_C_RES, linewidth=0.6, linestyle=":", alpha=0.7)
    axr.axhline(30, color=_C_SUP, linewidth=0.6, linestyle=":", alpha=0.7)
    axr.set_ylim(0, 100)
    axr.set_yticks([30, 70])
    cur_rsi = next((rs[i] for i in range(len(rs) - 1, -1, -1)
                    if rs[i] is not None), None)
    if cur_rsi is not None:
        axr.text(last_i, cur_rsi, f"RSI {cur_rsi:.0f}", fontsize=6, color=_C_RSI,
                 va="center", ha="right", fontweight="bold", bbox=_LBL_BBOX)
    _style_axis(axr)
    axr.margins(x=0.02)
    return _save(fig, plt)


def _tracking_chart_is_useful(reco: dict[str, Any]) -> bool:
    """B8 — un graphique de suivi n'est généré QUE s'il apporte une lecture :
    position proche du stop ou de la cible, ou mouvement notable depuis
    l'entrée. Pas de graphique décoratif."""
    if not isinstance(reco, dict) or not reco.get("asset"):
        return False
    has_plan = bool(reco.get("entry_price")) and (
        bool(reco.get("ct_target")) or bool(reco.get("stop_loss")))
    if not has_plan:
        return False
    path = reco.get("target_path_pct")
    prog = reco.get("progress_pct")
    health = str(reco.get("health_status") or "")
    if "Stop approché" in health or "Cible atteinte" in health:
        return True
    if isinstance(path, (int, float)) and (path >= 35 or path <= -30):
        return True
    if isinstance(prog, (int, float)) and abs(prog) >= 4:
        return True
    return False


def charts_for_tracked_recos(
    recos: list[dict[str, Any]], *, limit: int = 2
) -> dict[str, bytes]:
    """v26 (B8) — graphiques « plan vs prix » pour les recos actives UTILES.

    Sélection « quand utile » : proche du stop/de la cible ou mouvement
    notable. FILET : si aucune reco ne passe le filtre mais qu'au moins une
    porte un plan complet, on charte la plus avancée — un jour sans nouvelle
    reco ne doit plus être un mail sans AUCUN graphique (défaut v25/A20),
    et un graphe « plan vs prix » a une valeur intrinsèque pour une reco
    en cours.

    Returns:
        Dict ``{symbol: png_bytes}`` (peut être vide). Clés CID côté mail :
        ``chart_track_<SYMBOL>``.
    """
    plt = _import_plt()
    if plt is None:
        return {}
    out: dict[str, bytes] = {}
    candidates = [r for r in (recos or []) if _tracking_chart_is_useful(r)]
    if not candidates:
        with_plan = [
            r for r in (recos or [])
            if isinstance(r, dict) and r.get("asset") and r.get("entry_price")
            and (r.get("ct_target") or r.get("stop_loss"))
        ]
        with_plan.sort(key=lambda r: abs(r.get("progress_pct") or 0), reverse=True)
        candidates = with_plan[:1]
    candidates.sort(
        key=lambda r: abs(r.get("progress_pct") or 0), reverse=True)
    for reco in candidates[:limit]:
        sym = reco.get("asset")
        series = _load_series(sym, days=180)
        if not series:
            continue
        try:
            out[sym] = _render_tracking(plt, sym, series["closes"], reco,
                                        volumes_full=series.get("volumes"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graphique suivi %s échoué : %s", sym, exc)
    return out


def price_bollinger_png(symbol: str, *, days: int = 90) -> Optional[bytes]:
    """Graphique cours + Bollinger (octets PNG) — API conservée (compat v20)."""
    plt = _import_plt()
    if plt is None:
        return None
    closes = _load_closes(symbol, days=days)
    if not closes:
        return None
    try:
        return _render_bollinger(plt, symbol, closes, days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Graphique %s échoué : %s", symbol, exc)
        return None


def charts_for_theses(theses: list[dict[str, Any]], *, limit: int = 4) -> dict[str, bytes]:
    """Génère les graphiques d'analyse adaptés pour les thèses (limité taille mail).

    Returns:
        Dict ``{symbol: png_bytes}`` (peut être vide), à attacher en images CID.
    """
    out: dict[str, bytes] = {}
    for th in theses[:limit]:
        if not isinstance(th, dict):
            continue
        sym = th.get("asset")
        if not sym:
            continue
        png = chart_for_thesis(th)
        if png:
            out[sym] = png
    return out
