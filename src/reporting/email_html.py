"""Template HTML de l'email (Jinja2) + fonction de rendu.

Contraintes clients mail : inline CSS, pas de JS, pas de fonts externes, mise
en page tolérante. Couleurs sémantiques (success/warning/danger/info), cartes
à bordure latérale colorée, tableaux compacts, icônes Unicode.

Le template est volontairement sobre (compatibilité > esthétique) et s'adapte
au style (calm/normal/active) en masquant les sections vides.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, select_autoescape

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Palette sémantique.
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

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ header.title }}</title>
</head>
<body style="margin:0;padding:0;background:{{ c.bg }};
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
 color:{{ c.text }};line-height:1.5;">
<div style="max-width:640px;margin:0 auto;padding:16px;">

  <!-- En-tête -->
  <div style="padding:8px 4px 16px;border-bottom:2px solid {{ c.accent }};">
    <h1 style="margin:0;font-size:22px;color:{{ c.accent }};">{{ header.title }}</h1>
    <div style="font-size:13px;color:{{ c.muted }};margin-top:4px;">{{ header.subtitle }}</div>
  </div>

  {% if essentiel %}
  <!-- L'essentiel -->
  <div style="background:{{ c.card }};border-left:3px solid {{ c.info }};
   border-radius:6px;padding:14px 16px;margin-top:16px;
   box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <h2 style="margin:0 0 8px;font-size:15px;text-transform:uppercase;
     letter-spacing:.5px;color:{{ c.accent }};">⚡ L'essentiel en 30 secondes</h2>
    <ul style="margin:0;padding-left:18px;font-size:14px;">
      {% for item in essentiel %}<li style="margin:4px 0;">{{ item }}</li>{% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if marche_global %}
  <!-- Marché global -->
  <div style="background:{{ c.card }};border-radius:6px;padding:14px 16px;margin-top:14px;
   box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <h2 style="margin:0 0 8px;font-size:15px;color:{{ c.accent }};">🌐 Marché crypto global</h2>
    {% if marche_global.commentaire %}
    <p style="margin:0 0 10px;font-size:14px;">{{ marche_global.commentaire }}</p>
    {% endif %}
    {% if marche_global.indicateurs %}
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin:6px 0;">
      <tr>
        {% for label, val in marche_global.indicateurs.items() %}
        <td style="padding:6px;border:1px solid {{ c.border }};text-align:center;">
          <div style="color:{{ c.muted }};font-size:11px;text-transform:uppercase;">{{ label }}</div>
          <div style="font-weight:600;">{{ val }}</div>
        </td>
        {% endfor %}
      </tr>
    </table>
    {% endif %}
    {% if marche_global.narratives %}
    <p style="margin:8px 0 0;font-size:13px;color:{{ c.muted }};">{{ marche_global.narratives }}</p>
    {% endif %}
  </div>
  {% endif %}

  {% if macro %}
  <!-- Macro -->
  <div style="background:{{ c.card }};border-radius:6px;padding:14px 16px;margin-top:14px;
   box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <h2 style="margin:0 0 8px;font-size:15px;color:{{ c.accent }};">📊 Macro · ce qui compte</h2>
    {% if macro.indicateurs %}<p style="margin:0 0 8px;font-size:14px;">{{ macro.indicateurs }}</p>{% endif %}
    {% if macro.calendrier %}
    <ul style="margin:0 0 8px;padding-left:18px;font-size:13px;">
      {% for ev in macro.calendrier %}<li style="margin:3px 0;">{{ ev }}</li>{% endfor %}
    </ul>
    {% endif %}
    {% if macro.geopolitique %}
    <p style="margin:6px 0 0;font-size:13px;color:{{ c.muted }};border-top:1px dashed {{ c.border }};padding-top:8px;">
      🌍 {{ macro.geopolitique }}</p>
    {% endif %}
  </div>
  {% endif %}

  {% if positions %}
  <!-- Positions -->
  <h2 style="margin:18px 4px 8px;font-size:15px;color:{{ c.accent }};">💼 Tes positions · ce qui mérite attention</h2>
  {% for p in positions %}
  <div style="background:{{ c.card }};border-left:3px solid
   {% if p.avis %}{{ c.warning }}{% else %}{{ c.info }}{% endif %};
   border-radius:6px;padding:12px 14px;margin-bottom:10px;
   box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <div style="font-weight:700;font-size:15px;">{{ p.symbol }}</div>
    {% if p.pourquoi %}<div style="font-size:12px;color:{{ c.muted }};margin:2px 0 6px;">{{ p.pourquoi }}</div>{% endif %}
    {% if p.lecture %}<p style="margin:0 0 6px;font-size:14px;">{{ p.lecture }}</p>{% endif %}
    {% if p.avis %}
    <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:4px;
     padding:8px 10px;font-size:13px;margin:6px 0;">
      <strong style="color:{{ c.warning }};">Avis :</strong> {{ p.avis }}
      {% if p.invalidation %}<div style="margin-top:4px;color:{{ c.muted }};">
        Invalidation : {{ p.invalidation }}</div>{% endif %}
    </div>
    {% endif %}
    {% if p.sources %}<div style="font-size:11px;color:{{ c.muted }};">
      Sources : {{ p.sources | join(', ') }}</div>{% endif %}
  </div>
  {% endfor %}
  {% endif %}

  {% if spikes %}
  <!-- Spikes -->
  <div style="background:{{ c.card }};border-radius:6px;padding:12px 14px;margin-top:6px;
   box-shadow:0 1px 2px rgba(0,0,0,0.04);">
    <h2 style="margin:0 0 8px;font-size:14px;color:{{ c.accent }};">📈 Autres mouvements significatifs</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      {% for s in spikes %}
      <tr>
        <td style="padding:5px 6px;border-bottom:1px solid {{ c.border }};font-weight:600;">{{ s.symbol }}</td>
        <td style="padding:5px 6px;border-bottom:1px solid {{ c.border }};">{{ s.change_24h }}</td>
        <td style="padding:5px 6px;border-bottom:1px solid {{ c.border }};color:{{ c.muted }};">{{ s.note }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}

  {% if sante_projets %}
  <!-- Santé projets -->
  <div style="margin-top:14px;">
    {% if sante_projets.global_ok and not sante_projets.alertes %}
    <div style="display:inline-block;background:#dcfce7;color:{{ c.success }};
     border-radius:14px;padding:6px 14px;font-size:13px;font-weight:600;">
      ✅ Santé des projets : aucun signal d'alerte</div>
    {% else %}
    <div style="background:{{ c.card }};border-left:3px solid {{ c.danger }};
     border-radius:6px;padding:12px 14px;box-shadow:0 1px 2px rgba(0,0,0,0.04);">
      <h2 style="margin:0 0 8px;font-size:14px;color:{{ c.accent }};">🩺 Santé des projets</h2>
      {% for a in sante_projets.alertes %}
      <div style="font-size:13px;margin:4px 0;">
        <strong>{{ a.symbol }}</strong>
        <span style="color:{% if a.verdict=='exit' %}{{ c.danger }}{% else %}{{ c.warning }}{% endif %};">
          [{{ a.verdict }}]</span> — {{ a.detail }}
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  {% endif %}

  <!-- Footer -->
  <div style="margin-top:20px;padding-top:12px;border-top:1px solid {{ c.border }};
   font-size:11px;color:{{ c.muted }};text-align:center;">
    {{ footer }}
  </div>

</div>
</body>
</html>
"""

_ALERT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:16px;background:{{ c.bg }};
 font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:{{ c.text }};">
<div style="max-width:560px;margin:0 auto;background:{{ c.card }};
 border-left:4px solid {{ sev_color }};border-radius:6px;padding:16px 18px;
 box-shadow:0 1px 3px rgba(0,0,0,0.06);">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;
   color:{{ sev_color }};font-weight:700;">⚠️ Alerte intra-day</div>
  <h1 style="margin:6px 0 8px;font-size:18px;color:{{ c.accent }};">{{ title }}</h1>
  <p style="margin:0;font-size:14px;line-height:1.55;">{{ body }}</p>
  <div style="margin-top:14px;font-size:11px;color:{{ c.muted }};">{{ timestamp }}</div>
</div></body></html>
"""

_env = Environment(autoescape=select_autoescape(["html", "xml"]))


def render_report(payload: dict[str, Any]) -> str:
    """Rend le rapport HTML à partir du payload JSON de Gemini.

    Args:
        payload: dict conforme au schéma de sortie (voir analyst_persona).

    Returns:
        HTML complet prêt à l'envoi.
    """
    template = _env.from_string(_TEMPLATE)
    return template.render(
        c=_COLORS,
        header=payload.get("header", {"title": "Veille crypto", "subtitle": ""}),
        essentiel=payload.get("essentiel") or [],
        marche_global=payload.get("marche_global"),
        macro=payload.get("macro"),
        positions=payload.get("positions") or [],
        spikes=payload.get("spikes") or [],
        sante_projets=payload.get("sante_projets"),
        footer=payload.get("footer", ""),
    )


def render_alert(payload: dict[str, Any], timestamp: str) -> str:
    """Rend l'email d'alerte intra-day.

    Args:
        payload: dict ``{title, body, severity}``.
        timestamp: horodatage Casablanca.

    Returns:
        HTML de l'alerte.
    """
    severity = payload.get("severity", "warning")
    sev_color = {
        "info": _COLORS["info"],
        "warning": _COLORS["warning"],
        "danger": _COLORS["danger"],
    }.get(severity, _COLORS["warning"])
    template = _env.from_string(_ALERT_TEMPLATE)
    return template.render(
        c=_COLORS,
        sev_color=sev_color,
        title=payload.get("title", "Alerte crypto"),
        body=payload.get("body", ""),
        timestamp=timestamp,
    )
