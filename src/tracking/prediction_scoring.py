"""Tracking et scoring des recommandations passées.

Critères de réussite (déterministes, vérifiables sur prix réels) :
- RENFORCER : succès si prix >= entry * 1.10 dans les 30 jours.
- ALLEGER   : succès si prix <= signal_price * 0.92 dans les 14 jours.
- SURVEILLER / MAINTENIR : neutres (non scorés).

Score : validé +1, invalidé -1, neutre 0. Le win rate est calculé sur les
recos clôturées de la fenêtre.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.state import report_memory as mem
from src.utils.logger import get_logger

logger = get_logger(__name__)

_RENFORCER_TARGET = 1.10   # +10%
_RENFORCER_WINDOW_DAYS = 30
_ALLEGER_TARGET = 0.92     # -8%
_ALLEGER_WINDOW_DAYS = 14


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class PredictionTracker:
    """Évalue les recommandations et calcule des métriques de performance."""

    def evaluate_recommendation(
        self, reco: dict[str, Any], current_price: float
    ) -> str:
        """Évalue l'état d'une reco au prix courant.

        Args:
            reco: dict reco (doit contenir ``action``, ``entry_price`` ou
                ``signal_price``, ``created_at``).
            current_price: prix courant de l'actif.

        Returns:
            ``"validated"``, ``"invalidated"``, ``"in_progress"`` ou ``"neutral"``.
        """
        action = (reco.get("action") or "").upper()
        created = _parse(reco.get("created_at"))
        now = datetime.now(timezone.utc)
        if action in ("SURVEILLER", "MAINTENIR"):
            return "neutral"
        if current_price is None or created is None:
            return "in_progress"

        if action == "RENFORCER":
            entry = reco.get("entry_price")
            if not entry:
                return "in_progress"
            if current_price >= entry * _RENFORCER_TARGET:
                return "validated"
            if now - created > timedelta(days=_RENFORCER_WINDOW_DAYS):
                return "invalidated"
            return "in_progress"

        if action == "ALLEGER":
            signal = reco.get("signal_price") or reco.get("entry_price")
            if not signal:
                return "in_progress"
            if current_price <= signal * _ALLEGER_TARGET:
                return "validated"
            if now - created > timedelta(days=_ALLEGER_WINDOW_DAYS):
                return "invalidated"
            return "in_progress"

        return "neutral"

    def compute_win_rate(self, days: int = 30) -> dict[str, Any]:
        """Calcule le win rate sur les prédictions clôturées de la fenêtre.

        Args:
            days: fenêtre d'analyse en jours.

        Returns:
            Dict ``{total, validated, invalidated, neutral, win_rate_pct}``.
            ``total`` ne compte que validated + invalidated (les neutres et
            en-cours sont exclus du dénominateur).
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        validated = invalidated = neutral = 0
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            status = p.get("status")
            if status == "validated":
                validated += 1
            elif status == "invalidated":
                invalidated += 1
            elif status == "neutral":
                neutral += 1
        total = validated + invalidated
        win_rate = round((validated / total) * 100) if total else 0
        return {
            "total": total,
            "validated": validated,
            "invalidated": invalidated,
            "neutral": neutral,
            "win_rate_pct": win_rate,
        }

    def refresh_active(self, price_lookup: dict[str, float]) -> list[dict[str, Any]]:
        """Réévalue les recos actives et migre les clôturées vers l'historique.

        Args:
            price_lookup: dict ``{symbol: current_price}``.

        Returns:
            La liste mise à jour des recos encore actives.
        """
        active = mem.load_active_recommendations()
        history = mem.load_prediction_history()
        still_active: list[dict[str, Any]] = []
        for reco in active:
            price = price_lookup.get(reco.get("asset"))
            status = self.evaluate_recommendation(reco, price)
            reco["status"] = status
            reco["current_price"] = price
            if status in ("validated", "invalidated", "neutral"):
                reco["closed_at"] = mem.now_iso()
                history.append(reco)
            else:
                still_active.append(reco)
        mem.save_active_recommendations(still_active)
        mem.save_prediction_history(history)
        return still_active

    def extract_lesson(self, period_days: int = 7) -> str:
        """Identifie la principale leçon de la période (erreur la plus coûteuse).

        Args:
            period_days: fenêtre d'analyse.

        Returns:
            Phrase de leçon, ou message neutre si rien d'exploitable.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        invalidated = [
            p
            for p in history
            if p.get("status") == "invalidated"
            and (_parse(p.get("created_at")) or cutoff) >= cutoff
        ]
        if not invalidated:
            return "Aucune invalidation notable sur la période. Discipline maintenue."
        worst = invalidated[0]
        asset = worst.get("asset", "?")
        action = worst.get("action", "?")
        return (
            f"Reco {action} sur {asset} invalidée : revoir le poids des signaux "
            f"ayant motivé l'entrée et durcir le critère d'invalidation."
        )
