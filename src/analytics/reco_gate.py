"""Gate de cohérence des recos du matin (v28 · M-A1/A2/A3/A4).

L'audit des mails du 07/07 a montré des recos incohérentes avec leurs propres
preuves : « RENFORCER » avec « Taille : +0.0% » (plafond de concentration
atteint), espérance 30 j NÉGATIVE et backtest défavorable (BTC : win rate
historique 20%), R:R < 1 sur 6 recos sur 7 — et le « SI TU NE FAIS QU'UNE
CHOSE » poussait TAO… dont le renfort était « non suggéré » dans la même ligne.

Décision d'Omar (07/07) — GATE PAR TYPE :
  * plafond de concentration atteint (sizing 0%) → action « MAINTENIR »
    (jamais « RENFORCER +0.0% ») ;
  * thèse TACTIQUE 7-30 j → dégradée en « SURVEILLER » si EV 30 j < 0
    ou R:R < 1.2 (un trade court terme sans espérance n'est pas un trade) ;
  * thèse CONVICTION LT → reste « RENFORCER » (accumulation DCA) mais la
    confiance est plafonnée à 70% et la fiche porte une mention explicite
    « stats CT défavorables » (l'auto-critique ne suffisait pas) ;
  * le « one thing » ne propose que des gestes EXÉCUTABLES (sizing > 0,
    EV ≥ 0, R:R ≥ 1.2 pour les tactiques) — sinon « Ne rien faire
    aujourd'hui » est la recommandation honnête.

Toutes les fonctions sont pures/best-effort : payload partiel → inchangé.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Seuils de la décision Omar (07/07). R:R minimal d'un trade tactique : 1.2.
TACTICAL_MIN_RR = 1.2
LT_CONFIDENCE_CAP = 70


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _is_reinforce(action: Any) -> bool:
    a = str(action or "").upper()
    return "RENFORC" in a or "ACCUMUL" in a or a == "BUY"


def _thesis_type(t: dict[str, Any]) -> str:
    """« conviction » (LT) ou « tactical » — champ LLM, repli prudent tactique."""
    tt = str(t.get("thesis_type") or "").lower()
    if tt in ("conviction", "tactical"):
        return tt
    return "tactical"  # sans étiquette, on applique le gate le plus strict


def _ct_stats(t: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """(EV 30 j %, R:R 30 j) depuis le plan déterministe de la thèse."""
    plan = t.get("asset_plan") if isinstance(t.get("asset_plan"), dict) else {}
    return _num(plan.get("ev_30d_pct")), _num(plan.get("rr_30d"))


def apply_reco_gate(payload: dict[str, Any]) -> list[str]:
    """Applique le gate par type aux thèses fermes. Mutation in-place.

    À appeler APRÈS ``_apply_asset_plans_to_theses`` (le sizing et le plan
    doivent être attachés) et AVANT ``_compute_top_action``.

    Returns:
        Liste des corrections opérées (pour le log).
    """
    fixes: list[str] = []
    theses = payload.get("thesis_of_the_day")
    if not isinstance(theses, list):
        return fixes
    for t in theses:
        if not isinstance(t, dict) or not _is_reinforce(t.get("action")):
            continue
        asset = str(t.get("asset") or "?").upper()
        ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
        ev, rr = _ct_stats(t)

        # ── M-A1/M-A2 — plafond de concentration atteint : MAINTENIR.
        size_pct = _num(ap.get("position_size_pct"))
        if size_pct is not None and size_pct == 0:
            t["action"] = "MAINTENIR"
            t["_gated"] = "plafond"
            t["gate_note"] = (ap.get("sizing_note")
                              or "plafond de concentration atteint — aucun renfort")
            fixes.append(f"{asset} : RENFORCER+0.0% → MAINTENIR (plafond)")
            continue

        ct_bad = ((ev is not None and ev < 0)
                  or (rr is not None and rr < TACTICAL_MIN_RR))
        if not ct_bad:
            continue
        _stats_bits = []
        if ev is not None:
            _stats_bits.append(f"EV 30j {'+' if ev >= 0 else '−'}{abs(ev)}%")
        if rr is not None:
            _stats_bits.append(f"R:R {rr}")
        _stats = " · ".join(_stats_bits) or "stats CT indisponibles"

        if _thesis_type(t) == "tactical":
            # ── M-A3/M-A4 — tactique sans espérance : SURVEILLER.
            t["action"] = "SURVEILLER"
            t["_gated"] = "stats_ct"
            t["gate_note"] = (f"{_stats} — stats court-terme défavorables : "
                              "pas de trade tactique aujourd'hui")
            fixes.append(f"{asset} : RENFORCER (tactique) → SURVEILLER ({_stats})")
        else:
            # ── conviction LT : RENFORCER (DCA) conservé, confiance plafonnée
            # + mention EXPLICITE (l'auto-critique seule ne suffisait pas).
            conf = _num(t.get("confidence"))
            if conf is not None and conf > LT_CONFIDENCE_CAP:
                t["confidence"] = LT_CONFIDENCE_CAP
                t["_confidence_capped_ct"] = True
            t["ct_warning"] = (f"⚠ {_stats} — signaux court-terme défavorables : "
                               "accumulation LT (DCA), pas un trade 30 j")
            fixes.append(
                f"{asset} : conviction LT conservée, confiance ≤ "
                f"{LT_CONFIDENCE_CAP}% + mention CT ({_stats})")
    if fixes:
        logger.info("Reco gate v28 : %d ajustement(s) — %s",
                    len(fixes), " | ".join(fixes))
    return fixes


def executable_for_top_action(t: dict[str, Any]) -> bool:
    """Un geste n'entre dans le « one thing » que s'il est EXÉCUTABLE.

    RENFORCER : sizing > 0 ET EV ≥ 0 ET (tactique → R:R ≥ 1.2).
    ALLÉGER : toujours exécutable (réduire ne dépend pas d'un plafond).
    Actions non fermes (MAINTENIR/SURVEILLER…) : jamais.
    """
    if not isinstance(t, dict):
        return False
    a = str(t.get("action") or "").upper()
    if "ALLÉG" in a or "ALLEG" in a:
        return True
    if not _is_reinforce(a):
        return False
    ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
    size_pct = _num(ap.get("position_size_pct"))
    if size_pct is not None and size_pct <= 0:
        return False
    ev, rr = _ct_stats(t)
    if ev is not None and ev < 0:
        return False
    if _thesis_type(t) == "tactical" and rr is not None and rr < TACTICAL_MIN_RR:
        return False
    return True


NOTHING_TO_DO_LINE = (
    "Ne rien faire aujourd'hui — aucun geste exécutable : plafonds de "
    "concentration atteints ou signaux court-terme défavorables. "
    "S'abstenir est aussi une décision."
)
