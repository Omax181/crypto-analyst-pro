"""Rendu HTML des emails (4 templates Jinja2 + dispatcher).

Contraintes clients mail : inline CSS, pas de JS, pas de fonts externes, icônes
Unicode, couleurs sémantiques. Les sections sans données sont masquées par les
conditions Jinja dans les templates.

Point d'entrée : ``render(payload, kind)`` où kind ∈
{morning, evening, weekly, panic}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from src.ai_brain.prompts.analyst_persona import DISCLAIMER
from src.utils.logger import get_logger

logger = get_logger(__name__)

_COLORS = {
    "bg": "#fafaf6",
    "card": "#ffffff",
    "text": "#1a1d24",
    "muted": "#7a786f",
    "border": "#e5e4dc",
    "success": "#3B6D11",
    "warning": "#BA7517",
    "danger": "#A32D2D",
    "info": "#2563eb",
    "accent": "#0f172a",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
    undefined=ChainableUndefined,  # tolérant aux clés absentes
)

_TEMPLATE_BY_KIND = {
    "morning": "report_morning.html.j2",
    "evening": "report_evening.html.j2",
    "weekly": "report_weekly.html.j2",
    "panic": "report_panic.html.j2",
}


def render(payload: dict[str, Any], kind: str, charts: dict[str, str] | None = None) -> str:
    """Rend le HTML d'un rapport selon son type.

    Args:
        payload: dict produit par Gemini (déjà validé par coherence_checker).
        kind: type de rapport (``morning``/``evening``/``weekly``/``panic``).
        charts: dict ``{symbol: base64_png}`` pour les graphiques de thèses.

    Returns:
        HTML complet prêt à l'envoi.
    """
    template_name = _TEMPLATE_BY_KIND.get(kind)
    if template_name is None:
        logger.error("Type de rapport inconnu : %s — fallback morning.", kind)
        template_name = _TEMPLATE_BY_KIND["morning"]

    template = _env.get_template(template_name)
    context: dict[str, Any] = dict(payload)
    context["c"] = _COLORS
    context["disclaimer"] = DISCLAIMER
    context["charts"] = charts or {}
    # Pré-initialise les dicts top-level pour éviter UndefinedError sur les
    # comparaisons (ChainableUndefined gère les attributs en chaîne mais pas
    # les opérateurs de comparaison `>= 0`, `is not none`).
    for key in (
        "header", "footer", "portfolio_snapshot", "macro_context",
        "story_of_the_day", "onchain_indicators", "macro_impact",
        "tomorrow_setup", "exit_plan", "predictions_scoring", "sources_review",
        "btc_hold_comparison",
        "btc_network", "stablecoin_supply", "whale_inflows", "position_correlation",
        "daily_pnl", "evening_macro", "weekly_movers",
        "calibration", "regret", "blind_spots_weekly",
    ):
        context.setdefault(key, {})
    for key in (
        "active_recommendations_tracking", "thesis_of_the_day", "news_24h",
        "all_positions_summary", "sector_rotation", "delta_highlights",
        "reco_evolution", "market_changes", "overnight_events",
        "sector_exposure", "upcoming_calendar", "scenarios",
        "long_term_positioning", "portfolio_heatmap", "ptf_evolution",
        "intraday_news", "tomorrow_macro_events", "reco_changes",
    ):
        context.setdefault(key, [])

    if kind == "panic":
        severity = payload.get("severity", "warning")
        context["sev_color"] = {
            "info": _COLORS["info"],
            "warning": _COLORS["warning"],
            "danger": _COLORS["danger"],
        }.get(severity, _COLORS["warning"])
        context.setdefault("timestamp", "")

    try:
        return template.render(**context)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec rendu template %s : %s", kind, exc)
        return _fallback_html(payload, kind)


def _fallback_html(payload: dict[str, Any], kind: str) -> str:
    """HTML minimal de secours si le rendu Jinja échoue."""
    title = payload.get("title") or payload.get("header", {}).get("title", "Veille crypto")
    return (
        f"<html><body style='font-family:sans-serif;padding:16px;'>"
        f"<h2>{title}</h2>"
        f"<p>Rapport {kind} — rendu simplifié (le rendu détaillé a échoué).</p>"
        f"<p style='color:#6b7280;font-size:12px;'>{DISCLAIMER}</p>"
        f"</body></html>"
    )
