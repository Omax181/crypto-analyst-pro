"""Notifications push Telegram (Chantier G + v27 TG4 : digest à VALEUR).

À chaque rapport généré (morning/evening/weekly), un DIGEST synthétique est
poussé sur Telegram : en-tête clair (type de mail + date/heure), puis 4-7
lignes d'ESSENTIEL uniquement — régime, chiffres clés, LA chose à faire, le
risque à surveiller. 100% Python depuis le payload (déterministe, zéro coût,
zéro hallucination) : Omar peut décider sans ouvrir le mail.

Best-effort : un échec d'envoi ne fait JAMAIS échouer la génération du
rapport (le mail reste la livraison principale).
"""

from __future__ import annotations

from typing import Any, Optional

from src.telegram_bot import telegram_api
from src.utils.logger import get_logger

logger = get_logger(__name__)

_KIND_LABELS = {
    "morning": "☀️ MATIN",
    "evening": "🌙 SOIR",
    "weekly": "📊 HEBDO",
}


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_usd(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ") + " $"
    if abs(v) >= 1:
        return f"{v:,.2f} $"
    return f"{v:.4f} $"


def _regime_line(payload: dict[str, Any]) -> Optional[str]:
    reg = payload.get("market_regime") or {}
    if not reg.get("available"):
        return None
    label = reg.get("label_fr") or reg.get("regime")
    line = f"Régime : {label}"
    if reg.get("days_in_regime") is not None:
        line += f" ({reg['days_in_regime']} j)"
    if reg.get("changed") and reg.get("previous_label_fr"):
        line += f" · ⚠️ CHANGEMENT ({reg['previous_label_fr']} → {label})"
    return line


def _first_firm_thesis_line(payload: dict[str, Any]) -> Optional[str]:
    for t in (payload.get("thesis_of_the_day") or []):
        if not isinstance(t, dict):
            continue
        if (t.get("action") or "").upper() in ("RENFORCER", "ALLÉGER", "ALLEGER"):
            line = f"{t.get('action')} {t.get('asset')}"
            plan = t.get("action_plan") or {}
            entry = _num(plan.get("entry"))
            if entry:
                line += f" ~{_fmt_usd(entry)}"
            if plan.get("rr"):
                line += f" (R:R {plan['rr']})"
            return line
    return None


def _build_digest(payload: dict[str, Any], kind: str) -> str:
    """v27 (TG4) — digest multi-lignes par type de rapport, essentiel seulement."""
    header = payload.get("header") or {}
    when = header.get("time_casablanca") or header.get("date") or ""
    lines: list[str] = [f"*{_KIND_LABELS.get(kind, 'Rapport')} · {when}*"]

    reg = _regime_line(payload)
    if reg:
        lines.append(reg)

    snap = payload.get("portfolio_snapshot") or {}
    macro = payload.get("macro_context") or {}

    if kind == "morning":
        bits = []
        btc = _num(macro.get("btc_price"))
        btc_ch = _num(macro.get("btc_change_24h"))
        if btc:
            bits.append(f"BTC {_fmt_usd(btc)}"
                        + (f" ({btc_ch:+.1f}%)" if btc_ch is not None else ""))
        fg = macro.get("fear_greed")
        if fg is not None:
            bits.append(f"F&G {fg}")
        val = _num(snap.get("value_usd"))
        if val:
            _d = _num(snap.get("change_24h_pct"))
            bits.append(f"PTF {_fmt_usd(val)}"
                        + (f" ({_d:+.1f}%)" if _d is not None else ""))
        if bits:
            lines.append(" · ".join(bits))
        # v27 (RE4) — LA chose à faire ; repli : 1re thèse ferme.
        top = payload.get("top_action") or {}
        if top.get("line"):
            lines.append(f"🎯 {top['line']}")
        else:
            firm = _first_firm_thesis_line(payload)
            if firm:
                lines.append(f"🎯 {firm}")
        # Invalidation FRANCHIE = alerte prioritaire (jamais du bruit).
        for iw in (payload.get("invalidation_watch") or []):
            if isinstance(iw, dict) and iw.get("status") == "franchi":
                lines.append(f"🔴 {iw.get('condition')}")
                break
        risk = (payload.get("risk_score") or {}).get("score")
        if risk is not None:
            lines.append(f"Risque PTF {risk}/10")

    elif kind == "evening":
        pnl = payload.get("daily_pnl") or {}
        _d = _num(pnl.get("day_change_pct"))
        if _d is not None:
            _dusd = _num(pnl.get("day_change_usd"))
            lines.append(f"P&L jour {_d:+.2f}%"
                         + (f" ({_dusd:+,.0f} $)".replace(",", " ")
                            if _dusd is not None else ""))
        since = payload.get("since_morning_facts")
        if isinstance(since, str) and since:
            lines.append(since[:180])
        # 1er niveau à surveiller cette nuit / demain.
        for row in (payload.get("levels_tonight") or []):
            if isinstance(row, dict) and row.get("asset") and row.get("level"):
                lines.append(f"👁 {row['asset']} : {row.get('level')}"
                             + (f" — {row.get('trigger')}"
                                if row.get("trigger") else ""))
                break
        chk = payload.get("tomorrow_checklist") or {}
        _items = chk.get("checks") if isinstance(chk, dict) else None
        if isinstance(_items, list) and _items:
            first = _items[0]
            lines.append(("☑️ Demain : "
                          + (first if isinstance(first, str) else str(first)))[:160])

    elif kind == "weekly":
        bits = []
        wp = _num(snap.get("weekly_pnl_pct"))
        if wp is not None:
            bits.append(f"Semaine {wp:+.1f}%")
        vsb = _num(snap.get("vs_btc_7d_pct"))
        if vsb is not None:
            bits.append(f"vs BTC {vsb:+.1f}%")
        q = (payload.get("ptf_quality_score") or {}).get("score")
        if q is not None:
            bits.append(f"Santé {q}/10")
        if bits:
            lines.append(" · ".join(bits))
        scens = [s for s in (payload.get("scenarios") or []) if isinstance(s, dict)]
        if scens:
            dom = max(scens, key=lambda s: _num(s.get("probability_pct")) or 0)
            if dom.get("label"):
                lines.append(f"Scénario : {str(dom['label']).upper()} "
                             f"({dom.get('probability_pct')}%)")
        plan = payload.get("weekly_action_plan") or []
        if plan and isinstance(plan[0], dict) and plan[0].get("action"):
            lines.append(f"🎯 {str(plan[0]['action'])[:170]}")
        # v27 (ME2) — verdict des appels de la semaine passée, si évalués.
        calls = payload.get("calls_review") or {}
        if calls.get("summary_line"):
            lines.append(f"📏 {calls['summary_line']}"[:170])

    lines.append("_Réponds ici pour creuser (ex. /pourquoi TAO, /analyse ETH)._")
    return "\n".join(lines)


def push_report_notification(payload: dict[str, Any], kind: str) -> bool:
    """Pousse le digest synthétique après génération d'un rapport.

    Args:
        payload: payload du rapport (mêmes données que le rendu mail).
        kind: 'morning' | 'evening' | 'weekly'.

    Returns:
        True si l'envoi a réussi (False si non configuré ou échec — non bloquant).
    """
    if not telegram_api.bot_configured():
        logger.info("Notification push ignorée : bot Telegram non configuré.")
        return False
    try:
        text = _build_digest(payload or {}, kind)
    except Exception as exc:  # noqa: BLE001 — le digest ne bloque jamais l'envoi
        logger.warning("Digest Telegram échoué (%s) — repli court.", exc)
        text = f"*{_KIND_LABELS.get(kind, 'Rapport')}* est prêt 📬"
    try:
        return telegram_api.send_message(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notification push échouée (non bloquant) : %s", exc)
        return False
