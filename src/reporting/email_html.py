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

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.ai_brain.prompts.analyst_persona import DISCLAIMER
from src.utils.logger import get_logger

logger = get_logger(__name__)

_COLORS = {
    "bg": "#f5f6f8",
    "card": "#ffffff",
    "text": "#1a1d24",
    "muted": "#6b7280",
    "border": "#e5e7eb",
    "success": "#16a34a",
    "warning": "#d97706",
    "danger": "#dc2626",
    "info": "#2563eb",
    "accent": "#0f172a",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
)

_TEMPLATE_BY_KIND = {
    "morning": "report_morning.html.j2",
    "evening": "report_evening.html.j2",
    "weekly": "report_weekly.html.j2",
    "panic": "report_panic.html.j2",
}


def render(payload: dict[str, Any], kind: str) -> str:
    """Rend le HTML d'un rapport selon son type.

    Args:
        payload: dict produit par Gemini (déjà validé par coherence_checker).
        kind: type de rapport (``morning``/``evening``/``weekly``/``panic``).

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
    context.setdefault("header", {})
    context.setdefault("footer", {})

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
