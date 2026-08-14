"""Contexte du bot — SPEC V31 §1.4, §5.

Le bot reçoit exactement ce que le système SAIT, sans reconstruction parallèle :
le carnet vient de ``core.book``, les mesures de ``core.metrics``, l'état des
sources du dernier ``RunSummary``, et le dernier rapport de son instantané.

La v30 réinjectait ici des blocs entiers du payload LLM — c'est-à-dire de la
prose déjà rédigée, potentiellement rejetée au rendu. Le bot raisonnait donc
parfois sur un texte qui n'avait jamais été envoyé.
"""

from __future__ import annotations

import json
from typing import Any

from src.core import metrics as metrics_mod
from src.core import runlog
from src.core.book import RecommendationBook
from src.state import bot_memory as mem
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _book_context() -> dict[str, Any]:
    """Carnet en lecture seule : contrats actifs et clôtures récentes."""
    try:
        book = RecommendationBook(run_kind="bot", run_id="telegram")
    except Exception as exc:                                    # noqa: BLE001
        logger.info("Carnet indisponible pour le bot : %s", exc)
        return {}
    view = book.view()
    out: dict[str, Any] = {"active": view["active"],
                           "terminal_recent": view["terminal_recent"],
                           "counts": view["counts"]}
    try:
        out["metrics"] = [m.to_dict()
                          for m in (metrics_mod.win_rate(book),
                                    metrics_mod.horizon_calibration(book),
                                    metrics_mod.realized_edge(book))]
    except Exception as exc:                                    # noqa: BLE001
        logger.info("Mesures indisponibles pour le bot : %s", exc)
    return out


def _portfolio_static() -> dict[str, Any]:
    try:
        from src.utils.portfolio_loader import load_portfolio
        pf = load_portfolio()
        positions = [{
            "symbol": sym,
            "quantity": info.get("quantity"),
            "value_usd_baseline": info.get("value_usd"),
            "tier": info.get("tier"),
            "pru": info.get("pru"),
        } for sym, info in (pf.get("portfolio") or {}).items()
            if isinstance(info, dict)]
        return {"positions": positions, "count": len(positions)}
    except Exception as exc:                                    # noqa: BLE001
        logger.info("Portefeuille indisponible pour le bot : %s", exc)
        return {}


def load_full_context() -> dict[str, Any]:
    """Contexte complet du bot. Une clé absente = donnée réellement absente."""
    ctx: dict[str, Any] = {}

    book = _book_context()
    if book:
        ctx["book"] = book

    kind, snapshot = mem.load_latest_snapshot()
    if snapshot:
        ctx["last_report"] = {"kind": kind, **snapshot}

    last_run = runlog.load_last()
    if last_run:
        ctx["last_run"] = {
            "run_id": last_run.get("run_id"),
            "kind": last_run.get("kind"),
            "status": last_run.get("status"),
            "ended_at": last_run.get("ended_at"),
            "counters": last_run.get("counters"),
            "degradations": last_run.get("degradations"),
            "disabled_features": last_run.get("disabled_features"),
            "source_matrix": last_run.get("source_matrix"),
            "models_used": last_run.get("models_used"),
        }

    durable = mem.load_bot_memory(limit=40)
    if durable:
        ctx["durable_memory"] = durable

    pf = _portfolio_static()
    if pf:
        ctx["portfolio"] = pf

    try:
        from src.telegram_bot.live_data import (
            get_live_market_snapshot, get_live_portfolio_snapshot,
            get_price_anchors,
        )
        live_pf = get_live_portfolio_snapshot()
        if live_pf.get("available"):
            ctx["live_portfolio"] = live_pf
        live_mkt = get_live_market_snapshot()
        if live_mkt.get("available"):
            ctx["live_market"] = live_mkt
        anchors = get_price_anchors()
        if anchors.get("available"):
            ctx["price_anchors"] = anchors
    except Exception as exc:                                    # noqa: BLE001
        logger.info("Données live indisponibles pour le bot : %s", exc)

    return ctx


def context_to_text(ctx: dict[str, Any], *, max_chars: int = 50000) -> str:
    """Sérialise le contexte pour le prompt système du bot."""
    if not ctx:
        return "{}  // Aucun contexte disponible (état vide)"
    try:
        text = json.dumps(ctx, ensure_ascii=False, default=str, indent=1)
    except Exception:                                           # noqa: BLE001
        text = str(ctx)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (contexte tronqué pour la taille)"
    return text
