"""Notifications push Telegram (Chantier G, section 8 de l'audit).

À chaque rapport généré (morning/evening/weekly), un message court est poussé sur
Telegram. Omar peut répondre immédiatement en langage naturel pour approfondir.

Conçu pour être appelé en fin de run_morning/run_evening/run_weekly, en
best-effort : un échec d'envoi ne doit JAMAIS faire échouer la génération du
rapport (le mail reste la livraison principale).
"""

from __future__ import annotations

from typing import Any

from src.telegram_bot import telegram_api
from src.utils.logger import get_logger

logger = get_logger(__name__)

_KIND_LABELS = {
    "morning": "☀️ Rapport du matin",
    "evening": "🌙 Rapport du soir",
    "weekly": "📊 Bilan hebdomadaire",
}


def _safe(v: Any) -> str:
    return "" if v is None else str(v)


def _summary_line(payload: dict[str, Any], kind: str) -> str:
    """Construit une ligne de synthèse courte selon le type de rapport."""
    bits: list[str] = []
    snap = payload.get("portfolio_snapshot") or {}
    if snap.get("value_usd") is not None:
        try:
            bits.append(f"PTF ~${float(snap['value_usd']):,.0f}")
        except (ValueError, TypeError):
            pass

    if kind == "morning":
        theses = payload.get("thesis_of_the_day") or []
        if theses:
            bits.append(f"{len(theses)} thèse(s)")
        risk = (payload.get("risk_score") or {}).get("score")
        if risk is not None:
            bits.append(f"risque {risk}/10")
    elif kind == "evening":
        pnl = payload.get("daily_pnl") or {}
        if pnl.get("day_change_pct") is not None:
            try:
                bits.append(f"P&L jour {float(pnl['day_change_pct']):+.1f}%")
            except (ValueError, TypeError):
                pass
    elif kind == "weekly":
        if snap.get("weekly_pnl_pct") is not None:
            try:
                bits.append(f"semaine {float(snap['weekly_pnl_pct']):+.1f}%")
            except (ValueError, TypeError):
                pass
        exp = payload.get("expectancy") or {}
        if exp.get("available") and exp.get("expectancy_pct") is not None:
            bits.append(f"espérance {exp['expectancy_pct']:+.1f}%/reco")

    return " · ".join(bits)


def push_report_notification(payload: dict[str, Any], kind: str) -> bool:
    """Pousse une notification courte après génération d'un rapport.

    Args:
        payload: payload du rapport (mêmes données que le rendu mail).
        kind: 'morning' | 'evening' | 'weekly'.

    Returns:
        True si l'envoi a réussi (False si non configuré ou échec — non bloquant).
    """
    if not telegram_api.bot_configured():
        logger.info("Notification push ignorée : bot Telegram non configuré.")
        return False

    label = _KIND_LABELS.get(kind, "Rapport")
    summary = _summary_line(payload or {}, kind)
    lines = [f"*{label}* est prêt 📬"]
    if summary:
        lines.append(summary)
    lines.append("\nRéponds-moi ici pour approfondir n'importe quel point "
                 "(ex. « pourquoi RENFORCER ETH ? »), ou tape /resume.")
    text = "\n".join(lines)

    try:
        return telegram_api.send_message(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notification push échouée (non bloquant) : %s", exc)
        return False
