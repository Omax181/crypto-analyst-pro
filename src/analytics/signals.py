"""Signaux déterministes par actif — SPEC V31 §4.1 (I12, I19, I36).

Ce module produit l'UNIQUE entrée de la détermination d'horizon :
``{"signals": [{label, category, weight}]}``. Les poids par catégorie décident
de l'horizon (fondamental dominant -> POSITION, catalyseur ou structure
technique dominant -> SWING), et rien d'autre.

Ce qu'il ne fait PLUS, par rapport à ``thesis_scoring`` (v30) :
  - aucun seuil d'éligibilité : la SPEC ne prévoit qu'un gate d'émission, la
    viabilité. Un second gate serait une seconde autorité de décision ;
  - aucun ``thesis_type`` : l'horizon se déduit des POIDS, pas d'une étiquette
    que le LLM pouvait réécrire (cause racine R6) ;
  - aucun plafond de confiance ni score de complétude : l'indice de confiance
    est supprimé (BB2), remplacé par le bandeau de dégradation.

Les libellés restent affichables : ils expliquent POURQUOI un actif est
candidat, sans jamais valoir décision.
"""

from __future__ import annotations

from typing import Any, Optional

from src.analytics.technical_local import compute_local_technical

# Barème de pondération — hérité et inchangé : seule compte la comparaison
# fondamental / max(catalyseur, structure technique) dans la règle d'horizon.
W_FUNDAMENTAL_LT = 3
W_TECHNICAL_STRUCT = 2
W_CATALYST = 2
W_SHORT_TERM = 1
W_SENTIMENT = 1

CATEGORIES = ("fundamental_lt", "technical_struct", "catalyst",
              "short_term", "sentiment")


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) \
        else None


def evaluate(
    *,
    asset: str,
    tier: int,
    closes: Optional[list[float]] = None,
    change_24h: Optional[float] = None,
    change_from_ath_pct: Optional[float] = None,
    pru_gap_pct: Optional[float] = None,
    mvrv: Optional[float] = None,
    mvrv_stale: bool = False,
    active_addresses_trend_pct: Optional[float] = None,
    upcoming_catalyst_days: Optional[int] = None,
    token_unlock_soon: bool = False,
    fear_greed: Optional[float] = None,
    funding_annualized_pct: Optional[float] = None,
    news_count: int = 0,
) -> dict[str, Any]:
    """Signaux déterministes d'un actif. Ne lève jamais, ne décide de rien."""
    signals: list[dict[str, Any]] = []

    def sig(label: str, category: str, weight: int) -> None:
        signals.append({"label": label, "category": category, "weight": weight})

    # ── fondamentaux long terme ──────────────────────────────────────────
    mv = _num(mvrv)
    if mv is not None and mv < 1.0:
        # Une donnée périmée pèse moins : un MVRV de six semaines ne pilote pas
        # une thèse (constat d'audit v28, conservé).
        sig("MVRV sous 1 (sous-évaluation historique)"
            + (" · donnée datée" if mvrv_stale else ""),
            "fundamental_lt",
            max(1, W_FUNDAMENTAL_LT - 1) if mvrv_stale else W_FUNDAMENTAL_LT)
    gap = _num(pru_gap_pct)
    if gap is not None and gap <= -10:
        sig("position sous le prix de revient", "fundamental_lt",
            W_FUNDAMENTAL_LT)
    dd = _num(change_from_ath_pct)
    if dd is not None and dd <= -60 and tier in (1, 2):
        sig("drawdown profond depuis le plus haut historique", "fundamental_lt",
            W_FUNDAMENTAL_LT)
    aa = _num(active_addresses_trend_pct)
    ch = _num(change_24h)
    if aa is not None and aa >= 5 and ch is not None and ch <= 0:
        sig("activité réseau en hausse malgré un prix atone", "fundamental_lt",
            W_FUNDAMENTAL_LT)

    # ── structure technique ──────────────────────────────────────────────
    tech = compute_local_technical(list(closes or []))
    if tech.get("available"):
        rsi = _num(tech.get("rsi"))
        if rsi is not None and rsi <= 35:
            sig("RSI en survente", "technical_struct", W_TECHNICAL_STRUCT)
        boll = tech.get("bollinger") or {}
        if boll.get("position") == "lower":
            sig("prix sur la bande basse de Bollinger", "technical_struct",
                W_TECHNICAL_STRUCT)
        width = _num(boll.get("width_pct"))
        if width is not None and 0 < width < 8:
            sig("compression de volatilité", "technical_struct",
                W_TECHNICAL_STRUCT)
        if tech.get("bullish_divergence"):
            sig("divergence haussière prix / RSI", "technical_struct",
                W_TECHNICAL_STRUCT)

    # ── catalyseurs ──────────────────────────────────────────────────────
    days = _num(upcoming_catalyst_days)
    if days is not None and 0 <= days <= 7:
        sig("catalyseur propre à l'actif à court terme", "catalyst", W_CATALYST)
    if token_unlock_soon:
        sig("déblocage de jetons imminent", "catalyst", W_CATALYST)

    # ── court terme ──────────────────────────────────────────────────────
    if ch is not None and abs(ch) >= 5:
        sig("mouvement marqué sur vingt-quatre heures", "short_term",
            W_SHORT_TERM)
    if news_count >= 1:
        sig("actualité récente sur l'actif", "short_term", W_SHORT_TERM)

    # ── sentiment ────────────────────────────────────────────────────────
    fg = _num(fear_greed)
    if fg is not None and fg < 20 and gap is not None and gap <= 0:
        sig("peur extrême conjuguée à une position sous le prix de revient",
            "sentiment", W_SENTIMENT)
    fund = _num(funding_annualized_pct)
    if fund is not None and fund <= -10:
        sig("financement négatif extrême sur les perpétuels", "sentiment",
            W_SENTIMENT)

    weights = {c: 0 for c in CATEGORIES}
    for s in signals:
        if s["category"] in weights:
            weights[s["category"]] += int(s["weight"])
    return {"asset": asset, "signals": signals, "weights": weights,
            "technical": tech}


def dominant_category(scoring: dict[str, Any]) -> Optional[str]:
    """Catégorie la plus lourde — pour l'affichage seul, jamais pour décider."""
    weights = (scoring or {}).get("weights") or {}
    if not weights or not any(weights.values()):
        return None
    return max(weights.items(), key=lambda kv: kv[1])[0]
