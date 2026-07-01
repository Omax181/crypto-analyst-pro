"""Chargement du contexte complet pour le bot Telegram (Chantier G).

À chaque message, le bot injecte dans le system prompt Gemini TOUT le contexte
produit par le code v18 : derniers rapports morning/evening/weekly, recos actives,
portefeuille live, snapshots de performance, scoring des recos passées. Omar ne
répète jamais le contexte — l'assistant le connaît déjà.

Principe de NON-INVENTION (audit 5.6) : on n'injecte que des données réelles
issues du state et du portefeuille. Si une donnée manque, elle est absente du
contexte (et Gemini doit le dire plutôt que d'inventer).
"""

from __future__ import annotations

import json
from typing import Any

from src.state import report_memory as mem
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _summarize_report(report: dict[str, Any], kind: str) -> dict[str, Any]:
    """Extrait les champs saillants d'un rapport pour un contexte compact.

    On évite de réinjecter le rapport HTML entier (trop volumineux) : on garde
    les blocs analytiques clés (synthèse, thèses, recos, risque, macro).
    """
    if not isinstance(report, dict) or not report:
        return {}
    # v18.1 — whitelist ÉLARGIE : l'ancienne version laissait tomber des blocs
    # analytiques majeurs que le bot est censé exploiter (position_correlation =
    # le beta utilisé pour « si BTC −15% », rotation sectorielle, heatmap, bilan
    # des recos, garde-fou macro, signaux croisés, scénarios…). On injecte
    # désormais TOUTE l'analyse réellement présente dans le payload, en excluant
    # seulement le bruit (statuts de fiabilité de sources, quotes brutes) et les
    # gros dumps redondants (all_positions_summary — couvert par le snapshot live).
    keep_keys = [
        "header", "executive_summary", "synthesis", "today_watch",
        "macro_context", "macro_regime_readout", "macro_guardrail",
        "thesis_of_the_day", "thesis_empty_reason", "firm_postures",
        "active_recommendations", "active_recommendations_tracking",
        "reco_changes", "reco_bilan", "delta_summary",
        "risk_score", "risk_score_readout", "risk_unchanged_since_morning",
        "portfolio_snapshot", "daily_pnl",
        "sector_rotation", "sector_exposure_computed", "sector_exposure_cells",
        "rebalance_alert", "portfolio_heatmap", "market_movers", "weekly_movers",
        "position_correlation", "cross_signals", "macro_impact",
        "invalidation_watch", "invalidation_lessons", "self_critique_global",
        "long_term_positioning", "scenarios", "week_ahead",
        "predictions_scoring", "expectancy", "target_calibration", "calibration",
        "quant_reference", "data_contradictions", "blind_spots",
        "whale_inflows", "stablecoin_supply", "btc_network", "onchain_indicators",
        "etf_flows_facts", "polymarket_facts", "upcoming_calendar_facts",
        "tomorrow_macro_events", "intraday_news", "ath_facts",
        "btc_hold_comparison", "ptf_evolution", "ptf_quality_score",
    ]
    out: dict[str, Any] = {"kind": kind}
    for k in keep_keys:
        if k in report and report[k] not in (None, {}, []):
            out[k] = report[k]
    return out


def _portfolio_live() -> dict[str, Any]:
    """Charge le portefeuille (positions, tiers, valeurs baseline)."""
    try:
        from src.utils.portfolio_loader import load_portfolio
        pf = load_portfolio()
        positions = []
        for sym, info in (pf.get("portfolio") or {}).items():
            positions.append({
                "symbol": sym,
                "quantity": info.get("quantity"),
                "value_usd_baseline": info.get("value_usd"),
                "tier": info.get("tier"),
                "target_pct": info.get("target_pct"),
                "pru": info.get("pru"),
            })
        return {"positions": positions, "count": len(positions)}
    except Exception as exc:  # noqa: BLE001
        logger.info("Portefeuille indisponible pour le bot : %s", exc)
        return {}


def load_full_context() -> dict[str, Any]:
    """Assemble tout le contexte disponible pour le system prompt du bot.

    Returns:
        Dict structuré ``{morning, evening, weekly, active_recos, portfolio,
        snapshots, scoring}`` ; chaque clé absente si la donnée n'existe pas.
    """
    ctx: dict[str, Any] = {}

    morning = _summarize_report(mem.load_morning_report(), "morning")
    if morning:
        ctx["last_morning_report"] = morning
    evening = _summarize_report(mem.load_evening_report(), "evening")
    if evening:
        ctx["last_evening_report"] = evening
    weekly = _summarize_report(mem.load_weekly_report(), "weekly")
    if weekly:
        ctx["last_weekly_report"] = weekly

    recos = mem.load_active_recommendations()
    if recos:
        ctx["active_recommendations"] = recos

    # v21 — MÉMOIRE DURABLE : décisions passées d'Omar (achats/ventes, recos
    # écartées/validées), ses notes et seuils. Assure la continuité d'un échange
    # à l'autre et évite que le bot redemande / répète. Capturée déterministe.
    durable = mem.load_bot_memory(limit=40)
    if durable:
        ctx["durable_memory"] = durable

    pf = _portfolio_live()
    if pf:
        ctx["portfolio"] = pf

    # v18.1 — DONNÉES LIVE (valeur ajoutée du bot) : valorisation du PTF au prix
    # courant + instantané marché (BTC/ETH, dominance, F&G). Calculé seulement
    # quand Omar écrit (load_full_context n'est appelé que s'il y a un message).
    # Dégrade en silence : sans prix live, le bot raisonne sur la baseline.
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
        # v21 — bornes de prix réelles (anti-hallucination des prix historiques).
        anchors = get_price_anchors()
        if anchors.get("available"):
            ctx["price_anchors"] = anchors
    except Exception as exc:  # noqa: BLE001
        logger.info("Données live indisponibles pour le bot : %s", exc)

    snaps = mem.load_weekly_snapshots()
    if snaps:
        # On garde les 8 derniers (performance récente).
        ctx["weekly_snapshots"] = snaps[-8:]

    # Scoring des recos passées (win rate, validées/invalidées).
    try:
        from src.tracking.prediction_scoring import PredictionTracker
        tracker = PredictionTracker()
        wr = tracker.compute_win_rate(30)
        if wr:
            ctx["reco_scoring"] = wr
    except Exception as exc:  # noqa: BLE001
        logger.info("Scoring recos indisponible pour le bot : %s", exc)

    return ctx


def context_to_text(ctx: dict[str, Any], *, max_chars: int = 50000) -> str:
    """Sérialise le contexte en JSON compact pour le system prompt.

    Args:
        ctx: contexte assemblé par load_full_context.
        max_chars: budget de caractères (tronque proprement si dépassé).

    Returns:
        Chaîne JSON indentée et bornée.
    """
    if not ctx:
        return "{}  // Aucun contexte disponible (état vide — première exécution ?)"
    try:
        text = json.dumps(ctx, ensure_ascii=False, default=str, indent=1)
    except Exception:  # noqa: BLE001
        text = str(ctx)
    if len(text) > max_chars:
        # Tronque en signalant la coupe (le contexte reste exploitable).
        text = text[:max_chars] + "\n… (contexte tronqué pour la taille)"
    return text
