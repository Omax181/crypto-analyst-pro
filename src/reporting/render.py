"""Rendu des trois mails — SPEC V31 §5, §11 (I13, I26, I27, I59, I60).

Le rendu ne calcule RIEN. Il reçoit un ``RunContext`` déjà figé et le traduit en
HTML. Toute chaîne numérique provient de ``core.formatter``, directement ou via
la propriété ``Fact.formatted`` : les gabarits n'ont AUCUN filtre de formatage,
ce qui rend structurellement impossible la coexistence de deux conventions sur
une même page (défaut #67 de la v29).

Les champs éditoriaux passent par ``core.content.apply_contract`` : validés ou
remplacés par leur repli, jamais réparés.

La VUE DU CARNET est construite une seule fois et partagée par les trois mails
(I13) : le soir ne peut pas afficher un carnet différent de celui du matin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.core import content, formatter as fmt
from src.core.book import Direction, State
from src.core.horizon import Horizon
from src.utils.logger import get_logger

logger = get_logger(__name__)

# RESTE « v31 », délibérément. La révision du 15/08/2026 (câblage des
# actualités, plafond CoinGecko à 365 jours, chien de garde muet, rejeu des
# 4xx) aurait mérité « v31.1 » — mais cette chaîne finit dans le HTML rendu,
# où I60 interdit le décimal à l'anglaise, et le contrôle de locale ne peut
# pas distinguer une version d'un nombre. On n'affaiblit pas un invariant pour
# un confort d'affichage : la révision exacte est identifiée par l'empreinte
# du livrable et par le commit, qui sont des ancres plus sûres qu'une chaîne.
APP_VERSION = "v31"

DISCLAIMER = ("Analyse personnelle automatisée. Aucune de ces lignes n'est un "
              "conseil en investissement. Les décisions restent les tiennes.")

COLORS = {
    "bg": "#faf9f5", "card": "#ffffff", "text": "#1a1a18", "muted": "#6f6d66",
    "border": "#e5e4dc", "good": "#3B6D11", "warn": "#BA7517",
    "bad": "#A32D2D", "accent": "#0f172a",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
    # StrictUndefined : une clé absente fait ÉCHOUER le rendu au test plutôt que
    # de produire un trou silencieux en production (la v30 utilisait
    # ChainableUndefined, qui masquait les payloads incomplets).
    undefined=StrictUndefined,
    trim_blocks=True, lstrip_blocks=True,
)

_TEMPLATE_BY_KIND = {
    "morning": "v31_morning.html.j2",
    "evening": "v31_evening.html.j2",
    "weekly": "v31_weekly.html.j2",
}

_TITLE_BY_KIND = {
    "morning": "Rapport du matin",
    "evening": "Point du soir",
    "weekly": "Bilan de la semaine",
}

_STATE_LABEL = {
    State.ACTIVE: "en cours",
    State.TARGET_HIT: "cible atteinte",
    State.INVALIDATED: "invalidé",
    State.EXPIRED: "échu",
    State.SUPERSEDED: "remplacé",
    State.CANCELLED: "annulé",
}

_DIRECTION_LABEL = {
    Direction.LONG_INCREASE: "renforcer",
    Direction.LONG_REDUCE: "alléger",
}

_ACTION_LABEL = {
    "created": "nouveau contrat",
    "revised": "plan opérationnel révisé",
    "superseded_and_created": "contrat remplacé",
}

_CAUSE_LABEL = {
    "stop_breached": "invalidation franchie en clôture",
    "target_reached": "cible atteinte en clôture",
    "horizon_elapsed": "horizon écoulé",
    "direction_reversal": "renversement de direction",
    "horizon_change": "changement d'horizon",
    "dismiss": "écarté manuellement",
}


# ── vue du carnet, unique et partagée (I13) ───────────────────────────────

def _contract_row(rec: Any) -> dict[str, Any]:
    tracking = rec.tracking or {}
    current = tracking.get("current_price")
    delta = None
    if isinstance(current, (int, float)) and rec.entry:
        sign = 1.0 if rec.dir_enum is Direction.LONG_INCREASE else -1.0
        delta = sign * (current - rec.entry) / rec.entry * 100.0
    viability = rec.scored_contract.get("viability") or {}
    return {
        "asset": rec.asset,
        "direction": _DIRECTION_LABEL.get(rec.dir_enum, rec.direction),
        "horizon": rec.horizon,
        "state": _STATE_LABEL.get(rec.state_value, rec.state_value.value),
        "is_terminal": not rec.is_active,
        "entry": fmt.price(rec.entry),
        "target": fmt.price(rec.target),
        "stop": fmt.price(rec.stop),
        "current": fmt.price(current),
        "delta": fmt.pct(delta),
        "delta_positive": bool(delta is not None and delta >= 0),
        "days": fmt.days(tracking.get("days_elapsed")),
        "notional": fmt.usd(rec.notional),
        "upside": fmt.pct(rec.upside_pct()),
        "downside": fmt.pct(-rec.downside_pct()),
        "target_sigma": fmt.sigma(viability.get("target_in_sigma")),
        "stop_sigma": fmt.sigma(viability.get("stop_in_sigma")),
        "cost": fmt.pct(viability.get("round_trip_cost_pct")),
        "delta_required": fmt.fraction_as_pct(
            rec.scored_contract.get("delta_required")),
        "p_null": fmt.fraction_as_pct(rec.scored_contract.get("p_null")),
        "expires": fmt.date_fr(rec.scored_contract.get("expires_at")),
        "revisions": int((rec.counters or {}).get("reissues") or 0),
        "reason": _CAUSE_LABEL.get((rec.state or {}).get("reason"),
                                   (rec.state or {}).get("reason") or ""),
        "realized": fmt.pct((rec.outcome or {}).get("realized_pnl_pct")),
        "realized_usd": fmt.usd((rec.outcome or {}).get("realized_pnl_usd_net")),
    }


def book_view(ctx: Any) -> dict[str, Any]:
    """Vue unique du carnet — identique dans les trois mails."""
    active = [_contract_row(r) for r in ctx.book.active()]
    ref = datetime.now(timezone.utc)
    from datetime import timedelta
    recent = [_contract_row(r)
              for r in ctx.book.terminal_since(ref - timedelta(days=30))]
    return {
        "active": active,
        "closed_recent": recent,
        "active_count": fmt.integer(len(active)),
        "closed_count": fmt.integer(len(recent)),
        "has_any": bool(active or recent),
    }


# ── éléments dérivés du run ───────────────────────────────────────────────

def emissions_view(ctx: Any) -> list[dict[str, Any]]:
    out = []
    for e in ctx.emitted:
        c = e["candidate"]
        v = c.verdict
        out.append({
            "asset": c.asset,
            "cid": f"contract_{c.asset.lower()}",
            "action": _ACTION_LABEL.get(e["action"], e["action"]),
            "direction": _DIRECTION_LABEL.get(c.direction, c.direction.value),
            "horizon": c.horizon.value if isinstance(c.horizon, Horizon) else "",
            "entry": fmt.price(c.entry),
            "target": fmt.price(c.target),
            "stop": fmt.price(c.stop),
            "target_basis": c.target_basis or "",
            "stop_basis": c.stop_basis or "",
            "notional": fmt.usd(c.sizing.notional_usd if c.sizing else None),
            "weight_after": fmt.pct(c.sizing.weight_after_pct if c.sizing else None,
                                    sign=False),
            "binding": (c.sizing.binding_constraint if c.sizing else ""),
            "sigma_h": fmt.pct(c.sigma_h_pct, sign=False),
            "sigma_estimator": c.sigma_estimator or "",
            "sigma_degraded": bool(c.sigma_degraded),
            "target_sigma": fmt.sigma(v.target_in_sigma if v else None),
            "stop_sigma": fmt.sigma(v.stop_in_sigma if v else None),
            "cost": fmt.pct(v.round_trip_cost_pct if v else None),
            "p_null": fmt.fraction_as_pct(v.p_null if v else None),
            "p_breakeven": fmt.fraction_as_pct(v.p_breakeven if v else None),
            "delta_required": fmt.fraction_as_pct(v.delta_required if v else None),
            "net_pnl": fmt.usd(v.expected_pnl_usd_net if v else None, sign=True),
        })
    return out


def rejections_view(ctx: Any) -> list[dict[str, Any]]:
    """Chaque rejet est CHIFFRÉ. Un candidat écarté n'est jamais muet (§4.4)."""
    out = []
    for c in ctx.candidates:
        if c.emittable:
            continue
        out.append({
            "asset": c.asset,
            "horizon": c.horizon.value if isinstance(c.horizon, Horizon) else "—",
            "reason": c.rejection_summary(),
            "failed": ", ".join((c.verdict.failed_conditions if c.verdict else [])),
        })
    return out


def transitions_view(ctx: Any) -> list[dict[str, Any]]:
    return [{
        "asset": t["asset"],
        "to": _STATE_LABEL.get(State(t["to"]), t["to"]),
        "cause": _CAUSE_LABEL.get(t["cause"], t["cause"]),
        "close": fmt.price(t.get("close")),
    } for t in ctx.transitions]


def intraday_view(ctx: Any) -> list[dict[str, Any]]:
    return [{
        "asset": w["asset"],
        "kind": "invalidation" if w["kind"] == "stop" else "cible",
        "level": fmt.price(w["level"]),
        "spot": fmt.price(w["spot"]),
        "note": w["note"],
    } for w in ctx.intraday_warnings]


def portfolio_view(positions: list[Any], total: float) -> dict[str, Any]:
    rows = []
    for p in positions:
        rows.append({
            "asset": p.symbol,
            "tier": p.tier,
            "is_core": p.is_core,
            "price": fmt.price(p.price),
            "value": fmt.usd(p.value_usd),
            "weight": fmt.pct(p.weight_pct, sign=False),
            "pnl": fmt.pct(p.pnl_pct),
            "pnl_positive": bool(p.pnl_pct is not None and p.pnl_pct >= 0),
        })
    return {"total": fmt.usd(total), "rows": rows,
            "count": fmt.integer(len(rows))}


def metrics_view(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une métrique non publiée affiche SON MOTIF, jamais un substitut (I50)."""
    out = []
    for m in metrics:
        # Toutes les clés sont TOUJOURS présentes : le gabarit tourne en
        # StrictUndefined, une clé absente ferait échouer le rendu.
        entry = {"question": m["question"], "window": m["window"],
                 "n": fmt.integer(m["n"]), "published": m["published"],
                 "reason": m.get("reason") or "", "value": "", "bound": ""}
        if m["published"]:
            key = m["key"]
            if key in ("win_rate", "horizon_calibration"):
                entry["value"] = fmt.pct(m["value"], sign=False)
            elif key == "realized_edge":
                entry["value"] = fmt.pct(m["value"])
                entry["bound"] = fmt.pct(m.get("upper_bound"))
            elif key == "emission":
                d = m["detail"]
                entry["value"] = (
                    f"{fmt.integer(d['emitted'])} émis · "
                    f"{fmt.integer(d['non_viable'])} non viables · "
                    f"{fmt.integer(d['non_evaluable'])} non évaluables")
            elif key == "content_contract":
                entry["value"] = (
                    f"{fmt.integer(m['detail']['rejections'])} champ(s) écarté(s)")
            else:
                entry["value"] = (
                    f"{fmt.integer(m['value'])} source(s) dégradée(s)")
        out.append(entry)
    return out


def news_view(items: Any) -> list[dict[str, Any]]:
    """Actualités : EXTERNAL. Titre et source cités, jamais promus en fait.

    Une vue ne doit JAMAIS tuer le run. Au premier run réel de la v31.0, cette
    fonction a reçu le dictionnaire complet de ``get_news`` au lieu de sa clé
    ``news`` ; le découpage a levé « unhashable type: slice » et les trois
    mails sont morts pour une liste d'actualités décorative. Le câblage est
    corrigé en amont ; ici on refuse de faire tomber un rapport pour ça — et
    on le DIT dans les logs plutôt que d'absorber la faute en silence.
    """
    if isinstance(items, dict):
        logger.warning("news_view a reçu un dictionnaire : la charge de "
                       "get_news doit être lue à la clé « news ».")
        items = items.get("news")
    if items is not None and not isinstance(items, (list, tuple)):
        logger.warning("news_view : forme inattendue (%s), actualités "
                       "ignorées.", type(items).__name__)
        return []
    out = []
    for it in (items or [])[:10]:
        if not isinstance(it, dict):
            continue
        out.append({"title": str(it.get("title") or "").strip(),
                    "source": str(it.get("source") or "").strip(),
                    "when": str(it.get("published_label")
                                or it.get("when") or "").strip()})
    return [i for i in out if i["title"]]


# ── contrat de contenu ────────────────────────────────────────────────────

def deterministic_fallbacks(ctx: Any) -> dict[str, str]:
    """Replis DÉTERMINISTES (§5.2 niveau 2). Ils n'imitent jamais le champ écarté.

    Ce sont des ÉNUMÉRATIONS de ce que le moteur a observé, sans jugement.
    """
    out: dict[str, str] = {}
    signals = []
    for c in ctx.candidates[:5]:
        signals.append(c.asset)
    if signals:
        out["observation"] = (
            "Actifs évalués ce matin : " + ", ".join(signals) + ".")
        out["reasoning_signals"] = (
            "Les motifs de retenue ou d'écartement figurent en clair dans le "
            "tableau des candidats.")
    out["combined_reading"] = (
        "Synthèse éditoriale indisponible ; les éléments chiffrés du rapport "
        "restent complets.")
    return out


def apply_content(ctx: Any, authored: dict[str, Any]
                  ) -> tuple[dict[str, Any], list[content.Rejection]]:
    clean, rejections = content.apply_contract(
        authored, ctx.store, deterministic=deterministic_fallbacks(ctx))
    level = content.rejection_alarm_level(rejections)
    for r in rejections:
        logger.warning("Champ éditorial écarté — %s : %s", r.path, r.detail)
    if level == "banner":
        ctx.summary.add_degradation(
            f"{len(rejections)} champs éditoriaux écartés")
    return clean, rejections


# ── assemblage ────────────────────────────────────────────────────────────

def build_payload(
    ctx: Any, *, kind: str, positions: list[Any], ptf_value: float,
    authored: dict[str, Any], finalization: dict[str, Any],
    news_items: Any = None, top: Optional[dict[str, Any]] = None,
    nothing_to_do: Optional[str] = None,
) -> dict[str, Any]:
    """Payload complet d'un mail. Toutes les valeurs sont DÉJÀ formatées."""
    now = datetime.now(timezone.utc)
    top_view = None
    if top:
        top_view = {
            "asset": top["asset"],
            "direction": _DIRECTION_LABEL.get(Direction(top["direction"]),
                                              top["direction"]),
            "notional": fmt.usd(top["notional_usd"]),
            "entry": fmt.price(top["entry"]),
            "target": fmt.price(top["target"]),
            "stop": fmt.price(top["stop"]),
            "net_pnl": fmt.usd(top["expected_pnl_usd_net"], sign=True),
            "delta_required": fmt.fraction_as_pct(top["delta_required"]),
            "p_breakeven": fmt.fraction_as_pct(top["p_breakeven"]),
        }
    return {
        "kind": kind,
        "title": _TITLE_BY_KIND.get(kind, "Rapport"),
        "date_label": fmt.date_full_fr(now),
        "app_version": APP_VERSION,
        "disclaimer": DISCLAIMER,
        "c": COLORS,
        "banner": finalization.get("banner"),
        "book": book_view(ctx),
        "emissions": emissions_view(ctx),
        "rejections": rejections_view(ctx),
        "transitions": transitions_view(ctx),
        "intraday": intraday_view(ctx),
        "portfolio": portfolio_view(positions, ptf_value),
        "metrics": metrics_view(finalization.get("metrics") or []),
        "health": finalization.get("health") or [],
        "news": news_view(news_items),
        "top_action": top_view,
        "nothing_to_do": nothing_to_do,
        "authored": authored,
    }


def subject(kind: str, payload: dict[str, Any]) -> str:
    """Objet du mail. Aucun nombre non formaté n'y entre (I59)."""
    title = payload["title"]
    if kind == "morning":
        top = payload.get("top_action")
        if top:
            return f"{title} · {top['direction']} {top['asset']} {top['notional']}"
        return f"{title} · aucun geste"
    if kind == "evening":
        n = payload["book"]["active_count"]
        return f"{title} · {n} contrat(s) en cours"
    return f"{title} · {payload['book']['closed_count']} contrat(s) clôturé(s)"


def render(payload: dict[str, Any], kind: str,
           charts: Optional[dict[str, str]] = None) -> str:
    """Rend le HTML. Aucun repli silencieux : une erreur de gabarit LÈVE.

    Un gabarit qui échoue est un défaut de code, pas une condition de marché :
    le masquer derrière un « rendu simplifié » (v30) revient à envoyer un mail
    faux plutôt qu'à corriger le bug.
    """
    template = _env.get_template(_TEMPLATE_BY_KIND[kind])
    return template.render(charts=charts or {}, **payload)
