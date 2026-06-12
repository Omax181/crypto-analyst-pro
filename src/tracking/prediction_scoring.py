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
            created = _parse(reco.get("created_at"))
            if created is not None:
                reco["holding_days"] = max(
                    0, (datetime.now(timezone.utc) - created).days
                )
            if status in ("validated", "invalidated", "neutral"):
                reco["closed_at"] = mem.now_iso()
                history.append(reco)
            else:
                still_active.append(reco)
        mem.save_active_recommendations(still_active)
        mem.save_prediction_history(history)
        return still_active

    def build_scoring_detail(
        self, price_lookup: dict[str, float], period_days: int = 7
    ) -> list[dict[str, Any]]:
        """Tableau de scoring hebdo 100% Python (audit weekly P0).

        v14 laissait Gemini générer ``predictions_scoring.detail`` depuis
        l'historique brut → 11 lignes pour 5 actifs, doublons à résultats
        opposés, scores fantaisistes. v15 : le tableau est construit ICI,
        déterministe, UNE ligne par (actif, action) — la plus récente prime —
        depuis recos actives + clôturées de la fenêtre.

        Returns:
            Liste triée (clôturées d'abord, puis actives) de dicts
            ``{asset, reco, entry_date, entry_price, current_price,
            delta_pct, holding_days, status, score}`` où score ∈ {+1,-1,0}
            (+1 validée, -1 invalidée, 0 en cours/neutre).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        rows: dict[tuple, dict[str, Any]] = {}

        def _consider(reco: dict[str, Any], is_active: bool) -> None:
            asset = reco.get("asset")
            action = (reco.get("action") or "").upper()
            if not asset or action in ("", "SURVEILLER", "MAINTENIR"):
                return
            created = _parse(reco.get("created_at"))
            closed = _parse(reco.get("closed_at"))
            anchor = closed or created
            if anchor is None or anchor < cutoff:
                return
            key = (asset, action)
            prev = rows.get(key)
            prev_anchor = prev.get("_anchor") if prev else None
            if prev_anchor is not None and anchor <= prev_anchor:
                return  # la plus récente prime (dédup audit)
            entry = reco.get("entry_price")
            cur = price_lookup.get(asset) or reco.get("current_price")
            delta = None
            if entry and cur:
                try:
                    delta = round((float(cur) - float(entry)) / float(entry) * 100, 1)
                except (ValueError, TypeError, ZeroDivisionError):
                    delta = None
            status = reco.get("status") or ("in_progress" if is_active else "neutral")
            score = (1 if status == "validated"
                     else -1 if status == "invalidated" else 0)
            holding = reco.get("holding_days")
            if holding is None and created is not None:
                ref = closed or datetime.now(timezone.utc)
                holding = max(0, (ref - created).days)
            rows[key] = {
                "asset": asset,
                "reco": "ALLÉGER" if action == "ALLEGER" else action,
                "entry_date": created.strftime("%d/%m") if created else "—",
                "entry_price": entry,
                "current_price": cur,
                "delta_pct": delta,
                "holding_days": holding,
                "status": status,
                "score": score,
                "_anchor": anchor,
            }

        for reco in mem.load_prediction_history():
            _consider(reco, is_active=False)
        for reco in mem.load_active_recommendations():
            _consider(reco, is_active=True)

        out = sorted(
            rows.values(),
            key=lambda r: (0 if r["score"] != 0 else 1, r["asset"]),
        )
        for r in out:
            r.pop("_anchor", None)
        return out

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

    def compute_calibration(self, period_days: int = 30) -> dict[str, Any]:
        """Compare la confiance annoncée aux recos au taux de réussite réel.

        Regroupe les recos clôturées par palier de confiance (70-75%, 80%+, etc.)
        et calcule le taux de validation réel de chaque palier. Permet de savoir
        si l'agent est sur-confiant, sous-confiant ou bien calibré.

        Returns:
            Dict ``{available, buckets: [{range, realized_pct, label, n}], reading}``.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        # Paliers de confiance.
        bucket_defs = [
            ("50-69%", 50, 70),
            ("70-79%", 70, 80),
            ("80%+", 80, 101),
        ]
        buckets_data = []
        total_over = 0  # sur-confiance cumulée
        total_buckets = 0
        for label, lo, hi in bucket_defs:
            validated = invalidated = 0
            for p in history:
                created = _parse(p.get("created_at"))
                if created is None or created < cutoff:
                    continue
                conf = p.get("confidence")
                if conf is None or not (lo <= conf < hi):
                    continue
                if p.get("status") == "validated":
                    validated += 1
                elif p.get("status") == "invalidated":
                    invalidated += 1
            n = validated + invalidated
            if n == 0:
                continue
            realized = round(validated / n * 100)
            # Centre du palier annoncé pour comparer.
            announced_mid = (lo + min(hi, 100)) / 2
            gap = realized - announced_mid
            if gap < -10:
                cal_label = "sur-confiance"
                total_over += 1
            elif gap > 10:
                cal_label = "sous-confiance"
            else:
                cal_label = "calibré"
            total_buckets += 1
            buckets_data.append(
                {"range": label, "realized_pct": realized, "label": cal_label, "n": n}
            )

        if not buckets_data:
            return {"available": False}

        # Lecture globale.
        if total_over >= max(1, total_buckets // 2):
            reading = (
                "Tendance à la sur-confiance : les % annoncés dépassent le taux "
                "réalisé. Réduire le sizing sur les convictions moyennes."
            )
        else:
            reading = (
                "Calibration globalement correcte : les % de confiance annoncés "
                "sont cohérents avec les résultats observés."
            )
        return {"available": True, "buckets": buckets_data, "reading": reading}

    def compute_per_asset_performance(self, period_days: int = 90) -> dict[str, Any]:
        """Performance par actif + erreurs récentes (boucle de feedback V10).

        Réinjecté dans le prompt matin pour que l'IA apprenne de ses propres
        échecs : si elle s'est trompée plusieurs fois sur SOL, elle doit le
        savoir AVANT de réémettre une thèse sur SOL. Sans cela, chaque matin
        repart de zéro.

        Args:
            period_days: fenêtre d'analyse (90j par défaut).

        Returns:
            Dict ``{available, by_asset: {SYM: {validated, invalidated,
            win_rate_pct}}, recent_errors: [{asset, action, age_days}],
            caution_assets: [SYM,...]}``. ``caution_assets`` = actifs sur
            lesquels l'agent a un win rate faible (< 50% sur >= 2 clôtures).
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        now = datetime.now(timezone.utc)
        by_asset: dict[str, dict[str, int]] = {}
        recent_errors: list[dict[str, Any]] = []

        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            asset = p.get("asset")
            status = p.get("status")
            if not asset or status not in ("validated", "invalidated"):
                continue
            stats = by_asset.setdefault(asset, {"validated": 0, "invalidated": 0})
            stats[status] += 1
            if status == "invalidated":
                recent_errors.append(
                    {
                        "asset": asset,
                        "action": p.get("action", "?"),
                        "age_days": (now - created).days,
                    }
                )

        if not by_asset:
            return {
                "available": False,
                "by_asset": {},
                "recent_errors": [],
                "caution_assets": [],
            }

        caution: list[str] = []
        for sym, stats in by_asset.items():
            total = stats["validated"] + stats["invalidated"]
            stats["win_rate_pct"] = round(stats["validated"] / total * 100) if total else 0
            if total >= 2 and stats["win_rate_pct"] < 50:
                caution.append(sym)

        recent_errors.sort(key=lambda e: e["age_days"])
        return {
            "available": True,
            "by_asset": by_asset,
            "recent_errors": recent_errors[:5],
            "caution_assets": caution,
        }

    def compute_regret(self, period_days: int = 7) -> dict[str, Any]:
        """Chiffre le coût des erreurs / occasions ratées de la période.

        Pour chaque reco invalidée, estime le manque à gagner ou la perte en %
        (via le mouvement de prix depuis l'entrée si disponible). Quantifie
        l'écart entre une grosse occasion ratée et une petite erreur.

        Returns:
            Dict ``{available, items: [{asset, description, cost_pct, cost_label,
            cost_usd}], total_note}``.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        items = []
        total_cost_pct = 0.0
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            if p.get("status") != "invalidated":
                continue
            asset = p.get("asset", "?")
            action = p.get("action", "?")
            # Mouvement de prix depuis l'entrée (si dispo).
            move = p.get("price_change_pct")
            cost_pct = None
            cost_label = "manqués"
            if move is not None:
                # Pour une reco RENFORCER invalidée, le coût = mouvement négatif subi.
                # Pour une SURVEILLER ratée, le coût = hausse non capturée.
                cost_pct = round(abs(move), 1)
                cost_label = "non capturés" if (action or "").upper() in ("SURVEILLER", "MAINTENIR") else "de perte"
                total_cost_pct += cost_pct
            desc = f"{action} → résultat défavorable"
            items.append(
                {
                    "asset": asset,
                    "description": desc,
                    "cost_pct": cost_pct,
                    "cost_label": cost_label,
                    "cost_usd": p.get("cost_usd"),
                }
            )
        if not items:
            return {
                "available": True,
                "entries": [],
                "total_note": "Aucune erreur coûteuse cette semaine. Discipline maintenue.",
            }
        items.sort(key=lambda x: x.get("cost_pct") or 0, reverse=True)
        total_note = (
            f"Coût cumulé estimé des erreurs : ~{round(total_cost_pct, 1)}% sur les "
            f"positions concernées. La plus coûteuse : {items[0]['asset']}."
        )
        return {"available": True, "entries": items[:5], "total_note": total_note}
