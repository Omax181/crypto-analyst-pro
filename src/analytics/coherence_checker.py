"""Validation factuelle d'un rapport Gemini avant envoi.

Attrape les violations des règles non négociables qui auraient échappé au
modèle. Ne bloque pas l'envoi (un rapport imparfait vaut mieux que rien) mais
annote les problèmes et peut neutraliser une thèse manifestement invalide.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

_VAGUE_SOURCES = ("selon les sources", "d'après les sources", "selon diverses")


def _is_firm_action(action: str | None) -> bool:
    """Action ferme directionnelle (RENFORCER / ALLÉGER), accent-insensible.

    Le vocabulaire v18 émis par Gemini est ACCENTUÉ (« ALLÉGER ») : l'ancienne
    égalité stricte ``action in ("RENFORCER", "ALLEGER")`` ne matchait JAMAIS
    « ALLÉGER », laissant toutes les recos d'allègement échapper aux garde-fous
    (confiance < 55, plan incomplet, sources vagues) et à la rétrogradation. On
    détecte par sous-chaîne, comme le reste du code (firm_postures, persistance).
    """
    a = (action or "").upper()
    return ("RENFORC" in a) or ("ALLÉG" in a) or ("ALLEG" in a)


def check_report(
    payload: dict[str, Any],
    confidence_caps: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Vérifie un payload de rapport et renvoie un diagnostic.

    Args:
        payload: dict produit par Gemini.
        confidence_caps: P0 #60 — plafonds de confiance par actif (MAJUSCULE),
            issus de la COMPLÉTUDE de l'analyse (data.eligible_theses[].
            thesis_scoring.confidence_bounds.cap). Si une thèse ferme dépasse son
            plafond, la confiance est ramenée au plafond (garde-fou déterministe
            contre une reco à haute confiance sur une analyse à trous).

    Returns:
        Dict ``{ok, warnings, sanitized_payload}``. ``ok`` est ``False`` si au
        moins un problème majeur a été corrigé/neutralisé.
    """
    warnings: list[str] = []
    payload = dict(payload)
    caps = confidence_caps or {}

    theses = payload.get("thesis_of_the_day") or []
    clean_theses = []
    for th in theses:
        # v30.1 — check_report est sur le CHEMIN CRITIQUE sans try/except :
        # une thèse non-dict (payload LLM dégénéré) plantait tout le mail.
        if not isinstance(th, dict):
            warnings.append("thèse non structurée écartée (payload dégénéré).")
            continue
        # P0 #60 — plafond imposé par la complétude de l'analyse.
        cap = caps.get((th.get("asset") or "").upper())
        conf = th.get("confidence")
        if cap is not None and isinstance(conf, (int, float)) and conf > cap:
            warnings.append(
                f"{th.get('asset')} : confiance {conf}% > plafond complétude "
                f"{cap}% → ramenée à {cap}%."
            )
            th["confidence"] = cap
            # v30 (#77) — le plafonnement devient VISIBLE dans la fiche
            # (« taille standard · confiance plafonnée »), fini l'uniformité
            # muette à 75% sur 4 recos.
            th["_capped_completeness"] = True
            th["_confidence_capped"] = True
        problems = _check_thesis(th)
        if problems:
            warnings.extend(problems)
            # Rétrograder en surveillance plutôt que supprimer.
            if _is_firm_action(th.get("action")):
                th["action"] = "SURVEILLER"
                th["_downgraded"] = True
        clean_theses.append(th)
    if theses:
        payload["thesis_of_the_day"] = clean_theses

    # ATH impossible dans le récap positions.
    for pos in payload.get("all_positions_summary") or []:
        if not isinstance(pos, dict):
            continue
        ath = pos.get("ath_distance_pct")
        if ath is not None and ath <= -100:
            warnings.append(f"{pos.get('asset')} : ATH -100% corrigé à -99.99%.")
            pos["ath_distance_pct"] = -99.99

    ok = len(warnings) == 0
    if warnings:
        logger.warning("Cohérence : %d problème(s) corrigé(s).", len(warnings))
    return {"ok": ok, "warnings": warnings, "sanitized_payload": payload}


def _check_thesis(thesis: dict[str, Any]) -> list[str]:
    """Vérifie une thèse individuelle, renvoie la liste des problèmes."""
    problems: list[str] = []
    asset = thesis.get("asset", "?")
    action = (thesis.get("action") or "").upper()

    if _is_firm_action(action):
        # RÈGLE 4 : confiance < 55 interdit pour une reco ferme.
        conf = thesis.get("confidence")
        if isinstance(conf, (int, float)) and conf < 55:
            problems.append(f"{asset} : reco ferme avec confiance {conf}% (<55%).")

        # RÈGLE 3 : reco basée uniquement sur les commits.
        signals = thesis.get("reasoning_signals") or []
        if len(signals) <= 1 and any(
            "commit" in str(s).lower() for s in signals
        ):
            problems.append(f"{asset} : reco basée uniquement sur les commits.")

        # RÈGLE 6 : plan d'action complet requis.
        plan = thesis.get("action_plan") or {}
        if not plan.get("stop_loss") or not plan.get("take_profit"):
            problems.append(f"{asset} : plan d'action incomplet (stop/TP manquant).")

    # RÈGLE 9 : sources non vagues.
    src = str(thesis.get("sources_timestamps", "")).lower()
    if any(v in src for v in _VAGUE_SOURCES):
        problems.append(f"{asset} : sources vagues ('selon les sources').")

    # RÈGLE 5 : pattern "vérifié" doit avoir un compte d'occurrences.
    hp = thesis.get("historical_pattern") or {}
    if hp.get("verified") and not hp.get("occurrences_count"):
        problems.append(f"{asset} : pattern dit vérifié sans occurrences comptées.")

    return problems
