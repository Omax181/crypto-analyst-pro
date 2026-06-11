"""Rendu HTML des emails (3 templates Jinja2 + dispatcher).

Contraintes clients mail : inline CSS, pas de JS, pas de fonts externes, icônes
Unicode, couleurs sémantiques. Les sections sans données sont masquées par les
conditions Jinja dans les templates.

Point d'entrée : ``render(payload, kind)`` où kind ∈
{morning, evening, weekly}.
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


def _md_inline(value: Any) -> Any:
    """Convertit le markdown inline (**gras**, *italique*) en HTML sûr.

    Le texte est d'abord échappé (anti-injection), puis **x** → <strong>x</strong>
    et *x* → <em>x</em>. Retourne un Markup pour que Jinja ne ré-échappe pas.
    Tolérant : si la valeur n'est pas une chaîne, la renvoie telle quelle.
    """
    import re as _re
    from markupsafe import Markup, escape
    from jinja2 import Undefined

    if value is None or isinstance(value, Undefined):
        return ""
    if not isinstance(value, str):
        return value
    escaped = str(escape(value))
    # **gras** (non-greedy, paire de doubles astérisques)
    escaped = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    # *italique* (simple astérisque, en évitant les ** déjà traités)
    escaped = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    return Markup(escaped)


_env.filters["md"] = _md_inline


def _fmt_price(value: Any) -> str:
    """Formate un prix selon la spec H30 (cohérente partout).

    Règles :
      - >= 1000      : 0 décimale, séparateur virgule  ($71,262)
      - >= 1, < 1000 : 2 décimales                     ($8.98)
      - >= 0.01, < 1 : 4 décimales                     ($0.0526)
      - < 0.01       : 6 chiffres significatifs, zéros de fin retirés,
                       jamais de notation scientifique  ($0.000168, $0.00000001)
    Toujours préfixé par ``$``. Valeur absente / <= 0 → ``—``.
    """
    import math
    from jinja2 import Undefined

    if value is None or isinstance(value, Undefined):
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "—"
    if v <= 0:
        return "—"
    if not math.isfinite(v):
        return "—"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    # v < 0.01 : 6 chiffres significatifs, sans notation scientifique.
    exp = math.floor(math.log10(v))          # ex. 0.000168 -> -4 ; 1e-8 -> -8
    decimals = min(-exp + 5, 18)             # 6 sig figs, borné pour la sûreté
    return f"${v:.{decimals}f}".rstrip("0").rstrip(".")


def _fmt_money(value: Any) -> str:
    """Formate un montant à la française avec suffixe ``$`` : ``69.637,63 $``.

    V14 (point 1B) : les prix dans les plans d'action étaient collés et sans
    devise (``69637,63``). On rend désormais ``69.637,63 $`` : séparateur de
    milliers ``.``, décimale ``,``, devise en suffixe. Décimales adaptatives :
      - >= 1000 : 2 décimales (ex. 69.637,63 $)
      - >= 1    : 2 décimales (ex. 7,94 $)
      - >= 0.01 : 4 décimales (ex. 0,0526 $)
      - < 0.01  : 6 chiffres significatifs, zéros de fin retirés
    Accepte un nombre OU une string déjà partiellement formatée (on tente de
    parser ; si échec, on renvoie la valeur telle quelle). Valeur absente → ``—``.
    """
    import math
    from jinja2 import Undefined

    if value is None or isinstance(value, Undefined):
        return "—"
    # Tolérance : string type "63180" / "63,180" / "63 180 $" / "0.0014" /
    # "69.637,63 $" (v14.1 : la SORTIE de ce filtre redevient parsable — avant,
    # re-filtrer un montant déjà formaté donnait 69.637 au lieu de 69637,63).
    if isinstance(value, str):
        cleaned = (
            value.replace("$", "").replace("€", "").replace("\u202f", "")
            .replace("\xa0", "").replace(" ", "").strip()
        )
        has_dot, has_comma = "." in cleaned, "," in cleaned
        if has_dot and has_comma:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif has_comma:
            head, _, tail = cleaned.rpartition(",")
            if tail.isdigit() and len(tail) == 3 and head and "," not in head:
                cleaned = cleaned.replace(",", "")   # « 63,180 » : milliers US
            else:
                cleaned = cleaned.replace(",", ".")  # « 69637,63 » : décimale FR
        try:
            v = float(cleaned)
        except (ValueError, TypeError):
            return value  # non parsable : on laisse tel quel (ex. "marché")
    else:
        try:
            v = float(value)
        except (ValueError, TypeError):
            return "—"
    if v == 0:
        return "0 $"
    if not math.isfinite(v):
        return "—"
    neg = v < 0
    v = abs(v)
    if v >= 1:
        s = f"{v:,.2f}"
    elif v >= 0.01:
        s = f"{v:,.4f}"
    else:
        exp = math.floor(math.log10(v))
        decimals = min(-exp + 5, 18)
        s = f"{v:,.{decimals}f}".rstrip("0").rstrip(".")
    # Conversion format US (','=milliers, '.'=décimale) -> FR ('.'=milliers, ','=déc.)
    s = s.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")
    return f"{'−' if neg else ''}{s} $"


def _fmt_vol(value: Any) -> str:
    """Formate un volume : $2.4B / $142M / $890K / $12K."""
    from jinja2 import Undefined

    if value is None or isinstance(value, Undefined):
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "—"
    if v <= 0:
        return "—"
    import math as _m
    if not _m.isfinite(v):
        return "—"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


_env.filters["fmt_price"] = _fmt_price
_env.filters["fmt_money"] = _fmt_money
_env.filters["fmt_vol"] = _fmt_vol


def _fmt_pct(value: Any) -> str:
    """Formate un pourcentage selon la spec H31 : 1 décimale, signe explicite.

    Exemples : ``+20.9%``, ``−3.5%`` (vrai signe moins U+2212), ``+0.0%``.
    Valeur absente → ``—``. Le caractère ``−`` (U+2212) est réservé aux nombres
    négatifs ; ``—`` (U+2014) reste réservé aux valeurs non disponibles (H32).
    """
    from jinja2 import Undefined
    import math

    if value is None or isinstance(value, Undefined):
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "—"
    if not math.isfinite(v):
        return "—"
    if round(v, 1) == 0:
        v = 0.0  # évite l'affichage "−0.0%" pour les micro-négatifs
    return f"{v:+.1f}%".replace("-", "\u2212")  # hyphen-minus -> vrai signe moins


def _pastille(status: Any) -> Any:
    """Rend une mini-pastille de fiabilité en exposant (spec : taille d'un ²).

    - ``"confirmed"`` (vert)  : valeur recoupée par >=2 sources concordantes.
    - ``"single"``    (orange): une seule source disponible (à vérifier).
    - autre / absent          : rien (pas de pastille).

    Marqueur volontairement discret (7px, exposant) pour ne pas alourdir le mail.
    """
    from markupsafe import Markup
    from jinja2 import Undefined

    if status is None or isinstance(status, Undefined):
        return ""
    if status == "confirmed":
        color = "#3B6D11"
    elif status == "single":
        color = "#BA7517"
    else:
        return ""
    return Markup(
        f'<sup style="font-size:7px;color:{color};vertical-align:super;'
        f'line-height:0;">\u25cf</sup>'
    )


_env.filters["fmt_pct"] = _fmt_pct
_env.filters["pastille"] = _pastille


# Mapping noms techniques → libellés humains (point 6 evening, 3 mails).
# Les clés Python comme "fear_greed", "prices_now", "morning_report",
# "evening_macro", "etf_flows" ne doivent jamais fuiter dans le rendu.
# Gemini les voit parfois dans son contexte et les recopie ; ce filtre
# nettoie systématiquement à l'affichage.
_SOURCE_NAME_MAP = {
    "fear_greed": "Fear & Greed Index",
    "fear&greed": "Fear & Greed Index",
    "fearandgreed": "Fear & Greed Index",
    "prices_now": "CoinGecko",
    "pricesnow": "CoinGecko",
    "morning_report": "Rapport matin",
    "morningreport": "Rapport matin",
    "evening_macro": "Yahoo Finance",
    "eveningmacro": "Yahoo Finance",
    "etf_flows": "Farside Investors",
    "etfflows": "Farside Investors",
    "btc_network": "Blockchain.com",
    "btcnetwork": "Blockchain.com",
    "stablecoin_supply": "DeFiLlama",
    "whale_inflows": "Whale Alert",
    "whaleinflows": "Whale Alert",
    "polymarket": "Polymarket",
    "fred": "FRED",
    "yahoo": "Yahoo Finance",
    "coingecko": "CoinGecko",
    "coinmarketcap": "CoinMarketCap",
    "cmc": "CoinMarketCap",
    "lunarcrush": "LunarCrush",
    "kaito": "Kaito",
    "tokenunlocks": "Token Unlocks",
    "token_unlocks": "Token Unlocks",
    "defillama": "DeFiLlama",
    "telegram": "Telegram",
    "youtube": "YouTube",
}


def _humanize_source(value: Any) -> str:
    """Convertit un identifiant technique de source en libellé humain.

    - Si la valeur est déjà un libellé connu (pas dans la map), elle est
      renvoyée telle quelle.
    - Si la valeur est None/vide, renvoie chaîne vide (pour rendu propre).
    - Insensible à la casse pour les clés techniques.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    key = s.lower().replace(" ", "")
    return _SOURCE_NAME_MAP.get(key, s)


_env.filters["humanize_source"] = _humanize_source


def _num(value: Any, default: Any = None) -> Any:
    """Caste une valeur en float de façon sûre, pour les comparaisons.

    Gemini renvoie souvent les nombres sous forme de chaîne ("12.5", "+3,2 %"),
    ou des None/Undefined. Toute comparaison numérique directe (>=, <=, >, <)
    sur ces valeurs plante (TypeError str vs int). Ce filtre normalise :
    renvoie un float si castable, sinon ``default`` (None par défaut). Gère
    virgule décimale, signes +/−, symboles %, $, espaces et tirets longs.

    Usage template : ``{% if (x|num(0)) >= 0 %}`` — toujours sûr, jamais de crash.
    """
    from jinja2 import Undefined

    if value is None or isinstance(value, Undefined):
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        import math as _m
        return float(value) if _m.isfinite(float(value)) else default
    if isinstance(value, str):
        import re as _re
        s = (value.strip().replace("%", "").replace("$", "")
             .replace("\u202f", "").replace(",", ".")
             .replace("\u2212", "-").replace(" ", ""))
        if not s or s in ("-", ".", "—", "n/a", "N/A", "null", "None"):
            return default
        # Extrait le premier nombre signé, même suivi d'une unité ("+2pts", "5x").
        m = _re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return default
        try:
            return float(m.group())
        except ValueError:
            return default
    return default


_env.filters["num"] = _num

_TEMPLATE_BY_KIND = {
    "morning": "report_morning.html.j2",
    "evening": "report_evening.html.j2",
    "weekly": "report_weekly.html.j2",
}


def render(payload: dict[str, Any], kind: str, charts: dict[str, str] | None = None) -> str:
    """Rend le HTML d'un rapport selon son type.

    Args:
        payload: dict produit par Gemini (déjà validé par coherence_checker).
        kind: type de rapport (``morning``/``evening``/``weekly``).
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
        "calibration", "regret", "blind_spots_weekly", "portfolio_heatmap",
        "market_movers", "tomorrow_checklist", "risk_score",
    ):
        context.setdefault(key, {})
    for key in (
        "active_recommendations_tracking", "thesis_of_the_day", "news_24h",
        "all_positions_summary", "sector_rotation", "delta_highlights",
        "reco_evolution", "market_changes", "overnight_events",
        "sector_exposure", "upcoming_calendar", "scenarios",
        "long_term_positioning", "ptf_evolution",
        "intraday_news", "tomorrow_macro_events", "reco_changes",
        "delta_summary", "news_today", "levels_tonight", "reco_bilan",
    ):
        context.setdefault(key, [])

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
