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


# v32 (4.1) — ORDRE DE PRIORITÉ DU CONTEXTE.
#
# La sérialisation était un ``json.dumps`` de TOUT le contexte suivi d'un
# ``text[:50000]`` — une coupe AVEUGLE au caractère près. Or l'ordre
# d'insertion plaçait les TROIS rapports complets (~35 blocs analytiques
# chacun) AVANT le portefeuille. Le portefeuille, la valorisation live et les
# ancres de prix tombaient donc systématiquement dans la partie coupée.
#
# Effet mesuré dans les échanges Telegram des 21-22/08/2026. À la demande
# « donne-moi la quantité exacte de chacune de mes cryptos […] et la valeur
# totale qui doit matcher », le bot :
#   * n'a listé que 7 positions sur 29 ;
#   * a annoncé « PRU : non disponible » pour BTC et LINK, alors que
#     ``config/portfolio.yaml`` porte un PRU pour LES 29 ;
#   * a FABRIQUÉ la quantité de BTC — « 0.01727 BTC (estimation basée sur
#     l'allocation précédente de 42 % du portefeuille) » ;
#   * a présenté 2 675 $ comme « valeur totale actuelle du portefeuille » là
#     où la valeur réelle était ~3 300 $, soit 16 % d'écart sur la question la
#     plus élémentaire qu'on puisse poser à l'agent.
# Il a même diagnostiqué la cause lui-même : « les informations détaillées de
# ces ~21 autres positions ne sont pas incluses dans les rapports du jour ».
# Le fichier était bien chargé par ``_portfolio_live`` — il n'arrivait jamais
# jusqu'au modèle.
#
# Deux corrections indissociables :
#   1. les données FACTUELLES et FRAÎCHES passent devant les rapports ;
#   2. la coupe devient STRUCTURELLE : on omet des blocs ENTIERS, jamais un
#      demi-objet JSON — un tableau coupé en son milieu fait lire « 7
#      positions » là où il y en a 29 — et on DIT lesquels ont été omis.
_PRIORITE_CONTEXTE = (
    "portfolio",                   # les 29 positions : le socle factuel
    "live_portfolio",              # valorisation au prix courant
    "live_market",
    "price_anchors",
    "active_recommendations",
    "reco_scoring",
    "durable_memory",
    "weekly_snapshots",
    "last_morning_report",         # volumineux : passent en dernier
    "last_evening_report",
    "last_weekly_report",
)

_RAPPORTS = frozenset(
    ("last_morning_report", "last_evening_report", "last_weekly_report"))

# Noyau d'un rapport : ce qui reste quand la place manque. Ce sont les blocs
# dont le bot a besoin pour RAISONNER (thèses, régime, risque, scénarios) ;
# les annexes descriptives (heatmaps, rotations, tuiles) sautent d'abord.
_NOYAU_RAPPORT = frozenset((
    "kind", "header", "executive_summary", "synthesis",
    "thesis_of_the_day", "thesis_empty_reason", "firm_postures",
    "active_recommendations_tracking", "reco_bilan", "delta_summary",
    "macro_context", "macro_regime_readout", "risk_score",
    "invalidation_watch", "self_critique_global", "scenarios", "week_ahead",
    "blind_spots", "data_contradictions",
    # v33 — la décision du MOTEUR (et son motif d'abstention) fait partie du
    # noyau : sans elle, le bot raisonnait sur des thèses sans savoir laquelle
    # le système recommande réellement.
    "opportunity_summary", "top_action", "exit_signals",
))


def context_to_text(ctx: dict[str, Any], *, max_chars: int = 120000) -> str:
    """Sérialise le contexte pour le system prompt, sans coupe aveugle.

    Args:
        ctx: contexte assemblé par ``load_full_context``.
        max_chars: budget de caractères. Un bloc qui ne tient pas est OMIS EN
            ENTIER et signalé, jamais coupé en son milieu.

    Returns:
        Chaîne JSON indentée, complète pour tout bloc présent.
    """
    if not ctx:
        return "{}  // Aucun contexte disponible (état vide — première exécution ?)"
    # Audit 02/10 — même vue que les trois mails : ni plan V30, ni confiance
    # du modèle, ni niveau de reco du mauvais côté (vue_modele).
    from src.ai_brain.prompts.vue_modele import vue_modele
    ctx = vue_modele(ctx)

    ordre = [k for k in _PRIORITE_CONTEXTE if k in ctx]
    ordre += [k for k in ctx if k not in _PRIORITE_CONTEXTE]

    retenus: dict[str, Any] = {}
    omis: list[str] = []
    reduits: list[str] = []
    budget = max_chars - 400          # marge pour l'enveloppe et la note

    def _taille(cle: str, valeur: Any) -> int:
        return len(json.dumps({cle: valeur}, ensure_ascii=False,
                              default=str, indent=1))

    for cle in ordre:
        valeur = ctx[cle]
        try:
            taille = _taille(cle, valeur)
        except Exception:  # noqa: BLE001
            omis.append(cle)
            continue
        if taille <= budget:
            retenus[cle] = valeur
            budget -= taille
            continue
        # Trop gros : pour un RAPPORT, on tente une version réduite avant de
        # renoncer. Perdre l'analyse d'hier est un appauvrissement ; la perdre
        # SANS LE DIRE serait une faute. Les données factuelles, elles, sont
        # passées avant et ne subissent jamais cette réduction.
        if cle in _RAPPORTS and isinstance(valeur, dict):
            court = {k: v for k, v in valeur.items() if k in _NOYAU_RAPPORT}
            try:
                taille_courte = _taille(cle, court)
            except Exception:  # noqa: BLE001
                taille_courte = budget + 1
            if court and taille_courte <= budget:
                court["_reduit"] = ("blocs secondaires retirés faute de place "
                                    "dans le contexte")
                retenus[cle] = court
                budget -= taille_courte
                reduits.append(cle)
                continue
        omis.append(cle)

    if omis or reduits:
        # Le bot DOIT pouvoir dire ce qu'il ne voit pas plutôt que de combler.
        retenus["_completude_contexte"] = {
            "blocs_omis": omis,
            "blocs_reduits": reduits,
            "consigne": ("Ces blocs sont absents ou partiels. Dis-le si la "
                         "question porte dessus ; n'estime jamais une donnée "
                         "manquante."),
        }
        logger.info("Contexte Telegram : %d bloc(s) omis, %d réduit(s).",
                    len(omis), len(reduits))

    try:
        return json.dumps(retenus, ensure_ascii=False, default=str, indent=1)
    except Exception:  # noqa: BLE001
        return str(retenus)
