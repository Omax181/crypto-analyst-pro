"""Tracking et scoring des recommandations passées.

Critères de réussite (déterministes, vérifiables sur prix réels) :
- RENFORCER : succès si prix >= entry * 1.10 dans les 30 jours.
- ALLEGER   : succès si prix <= signal_price * 0.92 dans les 14 jours.
- SURVEILLER / MAINTENIR : neutres (non scorés).

Score : validé +1, invalidé -1, neutre 0. Le win rate est calculé sur les
recos clôturées de la fenêtre.
"""

from __future__ import annotations

import statistics
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


def latest_open_reco_by_asset(recos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """v18 (M-A2 / W-A7 / W-B5) — SOURCE DE VÉRITÉ UNIQUE des recos actives.

    Renvoie un dict ``{asset: reco}`` où, pour chaque actif, on garde la reco
    OUVERTE (RENFORCER/ALLÉGER) la plus récente. Matin, soir et weekly
    consomment TOUS cette même fonction pour leur set actif → impossible que le
    matin liste 9 recos et le weekly 8, ou qu'ils sélectionnent une entrée
    différente pour le même actif. Les SURVEILLER/MAINTENIR sont exclus (ce ne
    sont pas des recos fermes à suivre).

    Args:
        recos: liste brute des recos actives (``mem.load_active_recommendations()``).

    Returns:
        ``{asset: reco}`` dédupliqué, la plus récente prime.
    """
    best: dict[str, dict[str, Any]] = {}
    best_anchor: dict[str, Optional[datetime]] = {}
    for reco in recos or []:
        asset = reco.get("asset")
        action = (reco.get("action") or "").upper()
        if not asset or action in ("", "SURVEILLER", "MAINTENIR"):
            continue
        anchor = _parse(reco.get("created_at"))
        prev_anchor = best_anchor.get(asset)
        if asset in best:
            # Garde la plus récente. Si l'une des deux n'a pas de date, on
            # privilégie celle qui en a une (déterministe).
            if prev_anchor is not None and anchor is not None and anchor <= prev_anchor:
                continue
            if prev_anchor is not None and anchor is None:
                continue
        best[asset] = reco
        best_anchor[asset] = anchor
    return best


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

    def compute_invalidation_lessons(self, days: int = 60) -> dict[str, Any]:
        """v18 (W-B14) — mémoire des thèses invalidées (boucle d'apprentissage).

        Analyse les recos invalidées de la fenêtre pour faire ressortir des
        SCHÉMAS récurrents (un actif invalidé plusieurs fois, un type d'action
        qui échoue souvent). L'objectif est d'apprendre des erreurs passées
        plutôt que de répéter les mêmes paris perdants.

        Args:
            days: fenêtre d'analyse (par défaut 60j, plus large que le win rate).

        Returns:
            Dict ``{available, count, repeated_assets: [{asset, times}],
            reading}`` ou ``{available: False}``.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        invalidated: list[dict[str, Any]] = []
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            if p.get("status") == "invalidated":
                invalidated.append(p)
        if not invalidated:
            return {"available": False}
        # Actifs invalidés plusieurs fois = signal d'apprentissage fort.
        from collections import Counter
        _asset_counts = Counter(
            (p.get("asset") or "?") for p in invalidated
        )
        repeated = [
            {"asset": a, "times": n}
            for a, n in _asset_counts.most_common()
            if n >= 2
        ]
        if repeated:
            _r = repeated[0]
            reading = (
                f"{_r['asset']} a été invalidé {_r['times']} fois sur "
                f"{days}j : éviter de re-jouer la même thèse sans nouveau "
                f"catalyseur. Au total {len(invalidated)} invalidation(s) sur "
                f"la période — privilégier les setups à forte conviction."
            )
        else:
            reading = (
                f"{len(invalidated)} invalidation(s) isolée(s) sur {days}j, "
                f"aucun schéma répété : les pertes sont dispersées, pas de biais "
                f"systématique identifié."
            )
        return {
            "available": True,
            "count": len(invalidated),
            "repeated_assets": repeated,
            "reading": reading,
        }

    def compute_target_calibration(self, days: int = 90) -> dict[str, Any]:
        """v18 (Chantier E #15) — précision des cibles de prix (auto-apprentissage).

        Le système track les recos (RENFORCER/ALLÉGER) mais pas si les CIBLES de
        prix sont atteintes. Un système au bon timing mais aux cibles trop
        optimistes doit le savoir pour ajuster. On compare, sur les recos
        clôturées avec cible, le prix de sortie au prix cible.

        Args:
            days: fenêtre d'analyse.

        Returns:
            Dict ``{available, sample, hit_rate_pct, avg_overshoot_pct, bias,
            reading}`` ou ``{available: False}``.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        ratios: list[float] = []  # (gain réalisé) / (gain visé), en %
        hits = 0
        sample = 0
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            if p.get("status") not in ("validated", "invalidated"):
                continue
            entry = p.get("entry_price")
            target = p.get("ct_target") or p.get("target_price")
            exit_price = p.get("exit_price") or p.get("close_price")
            try:
                entry_f = float(entry) if entry is not None else None
                target_f = float(target) if target is not None else None
                exit_f = float(exit_price) if exit_price is not None else None
            except (ValueError, TypeError):
                continue
            if not (entry_f and target_f and exit_f) or entry_f == target_f:
                continue
            sample += 1
            aimed = target_f - entry_f  # gain visé (signé)
            realized = exit_f - entry_f  # gain réalisé (signé)
            if aimed != 0:
                ratios.append(realized / aimed * 100)
            # « hit » = la cible a été atteinte ou dépassée dans le bon sens.
            if aimed > 0 and exit_f >= target_f:
                hits += 1
            elif aimed < 0 and exit_f <= target_f:
                hits += 1
        if sample < 5:
            return {
                "available": False,
                "sample": sample,
                "reason": (
                    f"Calibration des cibles disponible dès 5 recos clôturées "
                    f"avec cible et prix de sortie (actuellement {sample})."
                ),
            }
        hit_rate = round(hits / sample * 100)
        avg_ratio = statistics.fmean(ratios) if ratios else 0.0
        if avg_ratio >= 90:
            bias = "calibrées"
            reading = (
                f"Cibles de prix bien calibrées : {hit_rate}% atteintes, le prix "
                f"de sortie capture en moyenne {avg_ratio:.0f}% du gain visé."
            )
        elif avg_ratio >= 50:
            bias = "légèrement optimistes"
            reading = (
                f"Cibles légèrement optimistes : {hit_rate}% atteintes, "
                f"{avg_ratio:.0f}% du gain visé capturé en moyenne — envisager "
                "des cibles un peu plus conservatrices."
            )
        else:
            bias = "trop optimistes"
            reading = (
                f"Cibles trop optimistes : seulement {avg_ratio:.0f}% du gain "
                f"visé capturé en moyenne ({hit_rate}% atteintes). Resserrer les "
                "cibles améliorerait la prise de profit réelle."
            )
        return {
            "available": True,
            "sample": sample,
            "hit_rate_pct": hit_rate,
            "avg_overshoot_pct": round(avg_ratio, 0),
            "bias": bias,
            "reading": reading,
        }

    def compute_expectancy(self, days: int = 30) -> dict[str, Any]:
        """v18 (W-B12) — espérance mathématique des recos clôturées.

        Pour chaque reco clôturée on estime le RÉSULTAT en % :
          • validée   → gain potentiel = (cible − entrée) / entrée
          • invalidée → perte = (stop − entrée) / entrée (négatif)
        Puis : espérance = (gain_moyen × taux_gain) + (perte_moyenne × taux_perte),
        où perte_moyenne est négative. Une espérance > 0 = stratégie gagnante en
        moyenne. On exige ≥ 5 recos clôturées pour publier un chiffre fiable.

        Args:
            days: fenêtre d'analyse.

        Returns:
            Dict ``{available, sample, win_rate_pct, avg_gain_pct, avg_loss_pct,
            expectancy_pct, reading}`` ou ``{available: False, reason}``.
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        gains: list[float] = []
        losses: list[float] = []
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            status = p.get("status")
            entry = p.get("entry_price")
            try:
                entry_f = float(entry) if entry is not None else None
            except (ValueError, TypeError):
                entry_f = None
            if not entry_f:
                continue
            action = (p.get("action") or "").upper()
            # Direction bearish : tout le vocabulaire de réduction d'exposition
            # (ALLÉGER, SORTIR, VENDRE). Avant, seul « ALLÉG » était reconnu, donc
            # une reco SORTIR voyait son signe de gain/perte inversé à tort.
            bearish = any(kw in action for kw in
                          ("ALLÉG", "ALLEG", "SORT", "VEND"))
            if status == "validated":
                tgt = p.get("ct_target") or p.get("target_price")
                try:
                    tgt_f = float(tgt) if tgt is not None else None
                except (ValueError, TypeError):
                    tgt_f = None
                if tgt_f:
                    g = (tgt_f - entry_f) / entry_f * 100
                    gains.append(-g if bearish else g)
            elif status == "invalidated":
                sl = p.get("stop_loss")
                try:
                    sl_f = float(sl) if sl is not None else None
                except (ValueError, TypeError):
                    sl_f = None
                if sl_f:
                    l = (sl_f - entry_f) / entry_f * 100
                    losses.append(-l if bearish else l)

        sample = len(gains) + len(losses)
        if sample < 5:
            return {
                "available": False,
                "sample": sample,
                "reason": (
                    f"Espérance mathématique disponible dès 5 recos clôturées "
                    f"avec niveaux (actuellement {sample})."
                ),
            }
        win_rate = len(gains) / sample
        avg_gain = (sum(gains) / len(gains)) if gains else 0.0
        avg_loss = (sum(losses) / len(losses)) if losses else 0.0  # négatif
        expectancy = avg_gain * win_rate + avg_loss * (1 - win_rate)
        reading = (
            f"Sur {sample} recos clôturées : gain moyen {avg_gain:+.1f}% "
            f"({len(gains)} gagnantes), perte moyenne {avg_loss:+.1f}% "
            f"({len(losses)} perdantes) → espérance {expectancy:+.1f}% par reco. "
            + ("Stratégie statistiquement gagnante."
               if expectancy > 0 else
               "Espérance négative : resserrer la sélectivité ou les niveaux.")
        )
        return {
            "available": True,
            "sample": sample,
            "win_rate_pct": round(win_rate * 100),
            "avg_gain_pct": round(avg_gain, 1),
            "avg_loss_pct": round(avg_loss, 1),
            "expectancy_pct": round(expectancy, 1),
            "reading": reading,
        }

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
        # v21 (M11/W6) — AUCUNE reco clôturée → win rate INDÉFINI (None), PAS 0%.
        # Afficher « 0% » laissait croire à 100% de pertes ; le rendu (matin ET
        # hebdo) affiche « — » pour None. Corrige l'incohérence cross-mail où le
        # matin montrait « — » et l'hebdo « 0% » pour la même absence d'historique.
        win_rate = round((validated / total) * 100) if total else None
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
            # v18 (W-A7) : le cutoff de fenêtre ne s'applique qu'aux recos
            # CLÔTURÉES (historique). Une reco ACTIVE est une position ouverte :
            # elle doit apparaître quel que soit son âge, sinon le weekly perd
            # des actifs que le matin affiche encore (divergence tracker).
            # v20 (audit W2) : la fenêtre des CLÔTURÉES s'applique sur la DATE
            # D'ÉMISSION (created_at), PAS sur closed_at. Sinon des recos LEGACY
            # émises il y a des mois mais re-clôturées récemment polluaient le
            # « bilan de la semaine » (ex. 15 validées affichées alors que le
            # header matin, basé sur created_at, comptait 0 → faux « 100 % win
            # rate » en contradiction avec « pas encore d'historique »). anchor
            # reste utilisé pour le tri/dédup (la plus récente prime).
            window_ref = created or closed
            if not is_active and (window_ref is None or window_ref < cutoff):
                return
            if is_active and anchor is None:
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
        # v18 (W-A7) : pour les recos ACTIVES, on ne considère que la source de
        # vérité unique (1 par actif, la plus récente) — identique au matin.
        for reco in latest_open_reco_by_asset(mem.load_active_recommendations()).values():
            _consider(reco, is_active=True)

        out = sorted(
            rows.values(),
            key=lambda r: (0 if r["score"] != 0 else 1, r["asset"]),
        )
        for r in out:
            r.pop("_anchor", None)
        return out

    def active_for_display(
        self, price_lookup: dict[str, float]
    ) -> list[dict[str, Any]]:
        """v17 (T-DEDUP / M-A2) — recos actives DÉDUPLIQUÉES pour le rendu matin.

        ``refresh_active`` renvoie toutes les recos actives, y compris les
        doublons legacy (BTC ×4, ETH ×3…) issus de prod v13 antérieure au
        correctif. Le rendu matin les affichait toutes, nues. Ici : UNE ligne
        par actif (la plus récente prime), enrichie de l'entrée, du Δ% live et
        de la cible CT — comme le weekly. Tri par |Δ%| décroissant.

        Returns:
            Liste de dicts ``{asset, action, issued_at, entry_price,
            progress_pct, progress_label, ct_target, status, status_color}``.
        """
        active = mem.load_active_recommendations()
        # v18 (M-A2) : sélection via la source de vérité UNIQUE partagée avec le
        # weekly → garantit le même set actif et la même entrée par actif.
        selected = latest_open_reco_by_asset(active)
        best: dict[str, dict[str, Any]] = {}
        for asset, reco in selected.items():
            action = (reco.get("action") or "").upper()
            anchor = _parse(reco.get("created_at"))
            entry = reco.get("entry_price")
            cur = price_lookup.get(asset) or reco.get("current_price")
            progress = None
            if entry and cur:
                try:
                    progress = round((float(cur) - float(entry)) / float(entry) * 100, 1)
                    # ALLÉGER : la « progression » est inversée (baisse = favorable).
                    if action in ("ALLEGER", "ALLÉGER"):
                        progress = -progress
                except (ValueError, TypeError, ZeroDivisionError):
                    progress = None
            status = reco.get("status") or "in_progress"
            _color = {"validated": "#3B6D11", "invalidated": "#A32D2D"}.get(
                status, "#BA7517")
            _ct = reco.get("ct_target") or reco.get("target_price")
            _sl = reco.get("stop_loss")
            # v26 (A12/B6) — PROGRESSION VERS LA CIBLE : % du chemin entrée →
            # cible réellement parcouru (0% = à l'entrée, 100% = cible touchée).
            # C'est LA mesure honnête du badge — « Sur objectif » à +3% quand la
            # cible exige +8,5% était un mensonge d'affichage (audit A12).
            _path_pct = None
            _dist_to_target_pct = None
            if _ct and entry and cur:
                try:
                    _e, _t, _c = float(entry), float(_ct), float(cur)
                    if abs(_t - _e) > 1e-12:
                        _path_pct = round((_c - _e) / (_t - _e) * 100, 0)
                    if _c > 0:
                        _dist_to_target_pct = round((_t - _c) / _c * 100, 1)
                except (ValueError, TypeError, ZeroDivisionError):
                    _path_pct = None
            # v18 (M-B18) / v26 (A12) — STATUT DE SANTÉ déterministe, distinct
            # du statut de validation :
            #   ✅ Cible atteinte : la cible CT est touchée (chemin ≥ 100%)
            #   🟢 En bonne voie  : ≥ 40% du chemin vers la cible (ou ≥ +3% si
            #                       aucune cible connue — legacy sans ct_target)
            #   🔴 Stop approché  : proche du stop d'invalidation (≤ 30% de marge)
            #   ⚠️ Sous pression  : en territoire défavorable sans stop imminent
            #   ● Neutre          : proche de l'entrée, pas de signal franc
            _health = None
            _health_color = "#8a8880"
            _comment = None
            if status == "validated":
                _health, _health_color = "✅ Cible atteinte", "#3B6D11"
                _comment = "Objectif touché — envisager prise de profit partielle."
            elif status == "invalidated":
                _health, _health_color = "🔴 Invalidée", "#A32D2D"
                _comment = "Seuil d'invalidation franchi — thèse caduque."
            elif progress is not None:
                # Distance au stop (si connu) en % du prix courant.
                _near_stop = False
                if _sl and cur:
                    try:
                        _entry_f, _sl_f, _cur_f = float(entry), float(_sl), float(cur)
                        _span = abs(_entry_f - _sl_f)
                        if _span > 0:
                            _margin = abs(_cur_f - _sl_f) / _span  # 1=à l'entrée, 0=au stop
                            _near_stop = _margin <= 0.30
                    except (ValueError, TypeError, ZeroDivisionError):
                        _near_stop = False
                if _near_stop:
                    _health, _health_color = "🔴 Stop approché", "#A32D2D"
                    _comment = "Proche du seuil d'invalidation — surveiller de près."
                elif _path_pct is not None and _path_pct >= 100:
                    _health, _health_color = "✅ Cible atteinte", "#3B6D11"
                    _comment = "Cible CT touchée — envisager prise de profit partielle."
                elif _path_pct is not None:
                    if _path_pct >= 40:
                        _health, _health_color = "🟢 En bonne voie", "#3B6D11"
                        _comment = f"{_path_pct:.0f}% du chemin vers la cible."
                    elif progress <= -3:
                        _health, _health_color = "⚠️ Sous pression", "#BA7517"
                    else:
                        _health, _health_color = "● Neutre", "#8a8880"
                elif progress >= 3:
                    # Legacy sans cible persistée : jamais « Sur objectif » —
                    # on dit ce qu'on mesure vraiment (Δ favorable).
                    _health, _health_color = "🟢 En bonne voie", "#3B6D11"
                    _comment = f"{progress:+.1f}% depuis l'entrée (cible n/d)."
                elif progress <= -3:
                    # v21 (M22) — PAS de commentaire boilerplate ici : le badge
                    # « ⚠️ Sous pression » + le Δ% affiché disent déjà tout.
                    _health, _health_color = "⚠️ Sous pression", "#BA7517"
                    _comment = None
                else:
                    # v21 (M22) — idem : badge « ● Neutre » suffit, pas de phrase.
                    _health, _health_color = "● Neutre", "#8a8880"
                    _comment = None
            # v27 (RE5) — FICHE DE VIE : âge de la reco + distance au stop en
            # % du prix courant (le lecteur voit d'un coup d'œil si le stop
            # respire ou s'il est menacé).
            _days_open = None
            if anchor is not None:
                try:
                    _days_open = max(
                        (datetime.now(timezone.utc) - anchor).days, 0)
                except (TypeError, ValueError):
                    _days_open = None
            _stop_dist_pct = None
            if _sl and cur:
                try:
                    _cur_f = float(cur)
                    if _cur_f > 0:
                        _stop_dist_pct = round(
                            (float(_sl) - _cur_f) / _cur_f * 100, 1)
                except (ValueError, TypeError, ZeroDivisionError):
                    _stop_dist_pct = None
            _life_bits = []
            if _days_open is not None:
                _life_bits.append(
                    "émise aujourd'hui" if _days_open == 0
                    else f"il y a {_days_open} j")
            if progress is not None:
                _life_bits.append(f"{progress:+.1f}%")
            if _path_pct is not None:
                _life_bits.append(f"{max(min(_path_pct, 999), -999):.0f}% du chemin vers la cible")
            if _stop_dist_pct is not None:
                _life_bits.append(f"stop à {_stop_dist_pct:+.1f}%")
            best[asset] = {
                "asset": asset,
                "action": "ALLÉGER" if action == "ALLEGER" else action,
                "issued_at": (anchor.strftime("%d/%m") if anchor else None),
                "entry_price": entry,
                "progress_pct": progress,
                "ct_target": _ct,
                "stop_loss": _sl,
                # v27 (RE5) — fiche de vie compacte.
                "days_open": _days_open,
                "stop_distance_pct": _stop_dist_pct,
                "life_line": " · ".join(_life_bits) if _life_bits else None,
                # v26 (B6) — progression vers la cible + distance restante,
                # affichées dans le Tracking (fini le badge binaire).
                "target_path_pct": _path_pct,
                "dist_to_target_pct": _dist_to_target_pct,
                "current_price": cur,
                "status": {"validated": "✓ validée",
                           "invalidated": "✗ invalidée"}.get(status, "● en cours"),
                "status_color": _color,
                # v18 (M-B18) — nouveaux champs pour le tableau refondu.
                "action_badge_bg": ("#E7F0DD" if action in ("RENFORCER",)
                                    else "#FBF0DA" if action in ("ALLEGER", "ALLÉGER")
                                    else "#EEF2F7"),
                "action_badge_fg": ("#27500A" if action in ("RENFORCER",)
                                    else "#7A4E12" if action in ("ALLEGER", "ALLÉGER")
                                    else "#33312e"),
                "health_status": _health,
                "health_color": _health_color,
                "comment": _comment,
                "_anchor": anchor,
            }
        out = sorted(
            best.values(),
            key=lambda r: abs(r["progress_pct"]) if r.get("progress_pct") is not None else -1,
            reverse=True,
        )
        for r in out:
            r.pop("_anchor", None)
        return out

    def check_invalidations(
        self, price_lookup: dict[str, float]
    ) -> list[dict[str, Any]]:
        """v27 (TH1) — invalidations FRANCHIES ou MENACÉES des recos actives.

        Une thèse avec un niveau d'invalidation n'a de valeur que si le
        franchissement est SIGNALÉ : ici, chaque reco active dont le prix a
        franchi le stop (ou s'en approche à ≤ 2,5%) produit une alerte
        structurée ``{asset, status, condition, implication}`` prête pour le
        bloc « Ce que je surveille pour invalider mon scénario ».
        """
        def _fmt(v: float) -> str:
            if abs(v) >= 1000:
                return f"{v:,.0f}".replace(",", " ") + " $"
            if abs(v) >= 1:
                return f"{v:,.2f} $"
            return f"{v:.4f} $"

        active = mem.load_active_recommendations()
        selected = latest_open_reco_by_asset(active)
        out: list[dict[str, Any]] = []
        for asset, reco in selected.items():
            sl = reco.get("stop_loss")
            cur = price_lookup.get(asset) or reco.get("current_price")
            action = (reco.get("action") or "").upper()
            try:
                sl_f, cur_f = float(sl), float(cur)
            except (TypeError, ValueError):
                continue
            if sl_f <= 0 or cur_f <= 0:
                continue
            # Pour une reco d'achat, l'invalidation est SOUS le prix ; pour un
            # allègement/short, elle est AU-DESSUS.
            bearish_exit = action in ("ALLEGER", "ALLÉGER", "SORTIR", "SELL")
            breached = cur_f >= sl_f if bearish_exit else cur_f <= sl_f
            dist_pct = (sl_f - cur_f) / cur_f * 100
            if breached:
                out.append({
                    "asset": asset, "status": "franchi",
                    "level": sl_f, "current": cur_f,
                    "condition": (f"{asset} : invalidation {_fmt(sl_f)} "
                                  f"FRANCHIE (prix {_fmt(cur_f)})"),
                    "implication": ("thèse caduque — statuer (sortie ou "
                                    "réduction), ne pas laisser dériver"),
                })
            elif abs(dist_pct) <= 2.5:
                out.append({
                    "asset": asset, "status": "menacé",
                    "level": sl_f, "current": cur_f,
                    "condition": (f"{asset} : à "
                                  f"{abs(dist_pct):.1f}% de l'invalidation "
                                  f"{_fmt(sl_f)}"),
                    "implication": "zone de décision imminente — surveiller la clôture",
                })
        # Franchissements d'abord (les plus urgents).
        out.sort(key=lambda r: 0 if r["status"] == "franchi" else 1)
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

    def compute_brier_score(self, period_days: int = 90) -> dict[str, Any]:
        """v27 (ES4) — score de Brier des recos clôturées (calibration fine).

        Brier = moyenne des (confiance/100 − issue)², issue = 1 si validée,
        0 si invalidée. Plus bas = mieux : 0.25 = pile-ou-face annoncé à 50%,
        ≤ 0.18 = bien calibré, ≥ 0.30 = les probas annoncées desservent.
        Complète le win rate : il mesure la QUALITÉ des probabilités
        annoncées, pas seulement le taux de réussite.

        Returns:
            ``{available, brier, n, grade, reading}`` — ``available=False``
            si < 5 recos clôturées AVEC confiance (pas de stat sur 2 cas).
        """
        history = mem.load_prediction_history()
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        sq_errors: list[float] = []
        for p in history:
            created = _parse(p.get("created_at"))
            if created is None or created < cutoff:
                continue
            conf = p.get("confidence")
            status = p.get("status")
            if conf is None or status not in ("validated", "invalidated"):
                continue
            try:
                f = float(conf) / 100.0
            except (TypeError, ValueError):
                continue
            outcome = 1.0 if status == "validated" else 0.0
            sq_errors.append((f - outcome) ** 2)
        if len(sq_errors) < 5:
            return {"available": False,
                    "reason": (f"{len(sq_errors)}/5 recos clôturées avec "
                               "confiance — Brier disponible dès 5")}
        brier = round(sum(sq_errors) / len(sq_errors), 3)
        if brier <= 0.18:
            grade, reading = "bien calibré", (
                "les probabilités annoncées reflètent fidèlement la réalité")
        elif brier <= 0.25:
            grade, reading = "acceptable", (
                "calibration proche du hasard informé — resserrer les % annoncés")
        else:
            grade, reading = "mal calibré", (
                "les % de confiance annoncés desservent la décision — "
                "les revoir à la baisse")
        return {"available": True, "brier": brier, "n": len(sq_errors),
                "grade": grade, "reading": reading}

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
