"""Signaux d'analyse transverses (Chantier E / Partie 4 de l'audit v18).

Ce module calcule, en Python pur et de façon déterministe, une série de signaux
de CONTEXTE que l'analyse de Gemini doit prendre en compte pour être « digne
d'une vraie analyse complète » (exigence de l'audit). Aucune hallucination : tout
est dérivé de données réelles (FRED, séries de prix, snapshots, calendrier).

Signaux couverts ici :
  • liquidity_regime      (#9)  — M2 global en expansion/contraction
  • dxy_cycle             (#10) — tendance du dollar sur ~3-6 mois
  • credit_risk           (#11) — spread high yield (risk-off structurel)
  • seasonality           (#12) — contexte saisonnier statistique du mois
  • realized_vol_regime   (#4)  — vol réalisée du PTF en expansion/compression
  • market_structure      (#17) — HH/HL vs LH/LL par actif (D1)
  • confirmation_bias     (#16) — garde-fou : 3 dernières thèses même sens
  • mvrv_context          (#7)  — MVRV mis en perspective (sous/sur-évaluation)

Chaque fonction dégrade proprement (retourne ``{available: False}``) si les
données manquent. Le rendu mail est optionnel : l'essentiel est que ces signaux
nourrissent le prompt.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _fr(v, nd: int = 1, sign: bool = False) -> str:
    """Décimale FR (virgule) pour les lectures rendues dans les mails —
    v30.1 (ré-audit #67) : les readings mélangeaient +1.4% (US) à la prose FR."""
    s = f"{float(v):+.{nd}f}" if sign else f"{float(v):.{nd}f}"
    return s.replace(".", ",")


# --------------------------------------------------------------------------- #
# #9 — Liquidité globale (M2) comme driver structurel
# --------------------------------------------------------------------------- #
def compute_allocation_gap(
    portfolio: dict[str, Any],
    enriched: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """#1 — sur/sous-pondération des positions vs leurs cibles d'allocation.

    Lit le champ optionnel ``target_pct`` de chaque position dans portfolio.yaml.
    Sans cibles définies, c'est impossible de savoir si une position est sur ou
    sous-pondérée — l'audit en fait le fondement de toute reco sérieuse (#1).
    Le code dégrade proprement tant qu'aucune cible n'est renseignée (Omar les
    communiquera après livraison, comme les PRU).

    Args:
        portfolio: dict ``{symbol: {target_pct?, ...}}`` (section ``portfolio``).
        enriched: dict ``{symbol: {value_usd, ...}}`` (valeurs live).

    Returns:
        Dict ``{available, total_target_pct, positions: [{asset, current_pct,
        target_pct, gap_pct, status}], reading}`` ou ``{available: False}``.
    """
    pf = (portfolio or {}).get("portfolio") if "portfolio" in (portfolio or {}) else portfolio
    pf = pf or {}
    # Cibles déclarées ? Coercition SÛRE : une valeur non numérique saisie à la
    # main dans portfolio.yaml (ex. "5%") ne doit pas faire planter l'analyse.
    targets: dict[str, float] = {}
    for s, info in pf.items():
        if not isinstance(info, dict) or info.get("target_pct") is None:
            continue
        try:
            targets[s] = float(info["target_pct"])
        except (ValueError, TypeError):
            logger.info("target_pct non numérique pour %s, ignoré.", s)
    if not targets:
        return {"available": False, "reason": "Aucune cible d'allocation (target_pct) définie."}

    total_val = sum(
        (e.get("value_usd") or 0) for e in enriched.values()
    ) or 1.0
    positions: list[dict[str, Any]] = []
    for s, target in sorted(targets.items(), key=lambda kv: kv[1], reverse=True):
        cur_val = (enriched.get(s) or {}).get("value_usd") or 0
        cur_pct = cur_val / total_val * 100
        gap = cur_pct - target
        if gap >= 3:
            status = "surpondérée"
        elif gap <= -3:
            status = "sous-pondérée"
        else:
            status = "à la cible"
        positions.append({
            "asset": s,
            "current_pct": round(cur_pct, 1),
            "target_pct": round(target, 1),
            "gap_pct": round(gap, 1),
            "status": status,
        })

    over = [p for p in positions if p["status"] == "surpondérée"]
    under = [p for p in positions if p["status"] == "sous-pondérée"]
    parts = []
    if over:
        parts.append("surpondéré : " + ", ".join(
            f"{p['asset']} ({p['gap_pct']:+.0f} pts)" for p in over[:4]))
    if under:
        parts.append("sous-pondéré : " + ", ".join(
            f"{p['asset']} ({p['gap_pct']:+.0f} pts)" for p in under[:4]))
    reading = (
        "Allocation vs cibles — " + " ; ".join(parts)
        if parts else "Allocation conforme aux cibles définies."
    )
    return {
        "available": True,
        "total_target_pct": round(sum(targets.values()), 1),
        "positions": positions,
        "reading": reading,
    }


def compute_price_fundamentals_divergence(
    onchain_assets: dict[str, dict[str, Any]],
    market: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """#6 — divergence prix / fondamentaux (activité réseau).

    Si le prix d'un actif BAISSE mais que son activité réseau (adresses actives)
    AUGMENTE → divergence haussière fondamentale (accumulation discrète).
    Inversement, prix en hausse + activité en baisse → signal de prudence.

    Args:
        onchain_assets: dict ``{symbol: {active_addresses_trend_pct, ...}}``.
        market: dict ``{symbol: {change_7d, change_24h, ...}}``.

    Returns:
        Dict ``{available, divergences: [{asset, price_chg, activity_chg,
        type}], reading}``.
    """
    divergences: list[dict[str, Any]] = []
    for sym, oc in (onchain_assets or {}).items():
        if not isinstance(oc, dict):
            continue
        activity = oc.get("active_addresses_trend_pct")
        if not isinstance(activity, (int, float)):
            continue
        mk = market.get(sym) or {}
        price_chg = mk.get("change_7d")
        if not isinstance(price_chg, (int, float)):
            price_chg = mk.get("change_24h")
        if not isinstance(price_chg, (int, float)):
            continue
        # Seuils : on ne signale que les divergences NETTES (≥ 5 pts d'écart).
        if price_chg <= -3 and activity >= 3:
            divergences.append({
                "asset": sym, "price_chg": round(price_chg, 1),
                "activity_chg": round(activity, 1), "type": "haussière",
            })
        elif price_chg >= 3 and activity <= -3:
            divergences.append({
                "asset": sym, "price_chg": round(price_chg, 1),
                "activity_chg": round(activity, 1), "type": "prudence",
            })
    if not divergences:
        return {"available": False}
    bull = [d for d in divergences if d["type"] == "haussière"]
    caution = [d for d in divergences if d["type"] == "prudence"]
    parts = []
    if bull:
        parts.append("divergence haussière (prix↓ activité↑) : " + ", ".join(
            f"{d['asset']}" for d in bull[:4]))
    if caution:
        parts.append("signal de prudence (prix↑ activité↓) : " + ", ".join(
            f"{d['asset']}" for d in caution[:4]))
    return {
        "available": True,
        "divergences": divergences,
        "reading": "Divergence prix/fondamentaux — " + " ; ".join(parts),
    }


def compute_similar_context(
    current_macro: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """#8 — mémoire des contextes macro similaires.

    Cherche dans l'historique des snapshots weekly un contexte macro (VIX, F&G,
    DXY) proche des conditions actuelles, et rappelle ce qui s'est passé ensuite
    sur le portefeuille (perf de la semaine suivante). Mise en perspective, PAS
    de prédiction.

    Args:
        current_macro: dict ``{vix, fear_greed, dxy}`` actuel.
        snapshots: historique ``[{vix?, fear_greed?, dxy?, value_usd, week_label}]``.

    Returns:
        Dict ``{available, match: {week_label, distance, next_week_pct}, reading}``.
    """
    cur_vix = current_macro.get("vix")
    cur_fg = current_macro.get("fear_greed")
    cur_dxy = current_macro.get("dxy")
    if not any(isinstance(v, (int, float)) for v in (cur_vix, cur_fg, cur_dxy)):
        return {"available": False}

    snaps = snapshots or []
    if len(snaps) < 4:
        return {"available": False}

    best = None
    best_dist = float("inf")
    # On compare chaque snapshot passé (sauf les 2 derniers, trop récents pour
    # avoir un « après ») aux conditions actuelles, distance euclidienne
    # normalisée sur les dimensions disponibles.
    for i, snap in enumerate(snaps[:-2]):
        dims = []
        if isinstance(cur_vix, (int, float)) and isinstance(snap.get("vix"), (int, float)):
            dims.append((cur_vix - snap["vix"]) / 10.0)
        if isinstance(cur_fg, (int, float)) and isinstance(snap.get("fear_greed"), (int, float)):
            dims.append((cur_fg - snap["fear_greed"]) / 20.0)
        if isinstance(cur_dxy, (int, float)) and isinstance(snap.get("dxy"), (int, float)):
            dims.append((cur_dxy - snap["dxy"]) / 5.0)
        if len(dims) < 2:  # besoin d'au moins 2 dimensions pour un match fiable
            continue
        dist = math.sqrt(sum(d * d for d in dims))
        # Perf de la semaine SUIVANTE (le snapshot d'après).
        cur_val = snap.get("value_usd")
        nxt_val = snaps[i + 1].get("value_usd") if i + 1 < len(snaps) else None
        if not (isinstance(cur_val, (int, float)) and isinstance(nxt_val, (int, float)) and cur_val):
            continue
        if dist < best_dist:
            best_dist = dist
            best = {
                "week_label": snap.get("week_label") or f"S-{len(snaps) - i}",
                "distance": round(dist, 2),
                "next_week_pct": round((nxt_val - cur_val) / cur_val * 100, 1),
            }
    # On ne retient le match que s'il est réellement proche (distance < 1.0).
    if not best or best["distance"] >= 1.0:
        return {"available": False}
    _dir = "progressé" if best["next_week_pct"] >= 0 else "reculé"
    return {
        "available": True,
        "match": best,
        "reading": (
            f"Contexte macro proche de {best['week_label']} (VIX/F&G/DXY "
            f"similaires) : la semaine suivante, le portefeuille avait {_dir} de "
            f"{_fr(abs(best['next_week_pct']))}%. Mise en perspective historique, "
            "pas une prédiction."
        ),
    }


def compute_implied_move(
    dvol: Optional[float],
    upcoming_events: Optional[list[dict[str, Any]]] = None,
    asset: str = "BTC",
) -> dict[str, Any]:
    """#2 — move implicite des options sur les événements (depuis DVOL Deribit).

    DVOL est la volatilité implicite annualisée (≈ « VIX crypto »). Le move
    attendu sur N jours ≈ DVOL × sqrt(N/365). Avant un FOMC/BoJ imminent, c'est
    la meilleure mesure OBJECTIVE du risque événementiel (meilleure qu'une
    opinion). On calcule le move attendu sur 2 jours (horizon événementiel
    typique) et, si un événement majeur est proche, on le mentionne.

    Args:
        dvol: indice DVOL (en %, annualisé).
        upcoming_events: événements macro proches (pour contextualiser).
        asset: actif concerné.

    Returns:
        Dict ``{available, dvol, move_2d_pct, move_7d_pct, reading}``.
    """
    if not isinstance(dvol, (int, float)) or dvol <= 0:
        return {"available": False}
    move_2d = dvol * math.sqrt(2 / 365)
    move_7d = dvol * math.sqrt(7 / 365)
    # Événement majeur dans les 48h ?
    near_event = None
    for e in upcoming_events or []:
        da = e.get("days_ahead")
        if isinstance(da, (int, float)) and da <= 2:
            near_event = e.get("label")
            break
    if near_event:
        reading = (
            f"Move implicite options {asset} (DVOL {dvol:.0f}%) : le marché price "
            f"un mouvement attendu de ±{_fr(move_2d)}% sur 48h. Avec « {near_event} » "
            "imminent, c'est la mesure objective du risque événementiel — "
            "dimensionner les positions en conséquence."
        )
    else:
        reading = (
            f"Move implicite options {asset} (DVOL {dvol:.0f}%) : mouvement "
            f"attendu ±{_fr(move_2d)}% sur 48h, ±{_fr(move_7d)}% sur 7j."
        )
    return {
        "available": True,
        "dvol": round(dvol, 1),
        "move_2d_pct": round(move_2d, 1),
        "move_7d_pct": round(move_7d, 1),
        "reading": reading,
    }


def compute_derivatives_signal(derivatives_by_asset: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """#14 — signal dérivés : surchauffe du funding (proxy de l'effet de levier).

    Un funding fortement positif = longs en excès (risque de liquidation en
    cascade à la baisse) ; fortement négatif = shorts en excès (potentiel short
    squeeze). C'est souvent plus prédictif à court terme qu'un indicateur
    technique (audit #14, complément de l'OI).

    Args:
        derivatives_by_asset: dict ``{symbol: {funding_annualized_pct?, ...}}``.

    Returns:
        Dict ``{available, signals: [{asset, funding_pct, state}], reading}``.
    """
    signals: list[dict[str, Any]] = []
    for sym, d in (derivatives_by_asset or {}).items():
        if not isinstance(d, dict) or not d.get("available", True):
            continue
        fa = d.get("funding_annualized_pct")
        if not isinstance(fa, (int, float)):
            continue
        # Seuils annualisés : > +30% = surchauffe longs ; < -10% = excès shorts.
        if fa >= 30:
            signals.append({"asset": sym, "funding_pct": round(fa, 1),
                            "state": "longs en excès"})
        elif fa <= -10:
            signals.append({"asset": sym, "funding_pct": round(fa, 1),
                            "state": "shorts en excès"})
    if not signals:
        return {"available": False}
    longs = [s for s in signals if s["state"] == "longs en excès"]
    shorts = [s for s in signals if s["state"] == "shorts en excès"]
    parts = []
    if longs:
        parts.append("longs en excès (risque de purge baissière) : " + ", ".join(
            f"{s['asset']}" for s in longs[:4]))
    if shorts:
        parts.append("shorts en excès (potentiel short squeeze) : " + ", ".join(
            f"{s['asset']}" for s in shorts[:4]))
    return {
        "available": True,
        "signals": signals,
        "reading": "Dérivés (funding) — " + " ; ".join(parts),
    }


def compute_narrative_lifecycle(
    sector_rotation: dict[str, Any],
    holdings_sectors: Optional[list[str]] = None,
) -> dict[str, Any]:
    """#3 — stade du cycle de vie de chaque narratif (émergence/adoption/euphorie/rotation).

    Heuristique déterministe basée sur le momentum sectoriel (7j vs 30j) :
      • 30j fort ET 7j fort           → euphorie (sortie possible)
      • 30j fort ET 7j faible/négatif → rotation/refroidissement (prudence)
      • 30j faible ET 7j fort         → émergence/reprise (upside potentiel)
      • 30j faible ET 7j faible       → consolidation/dormance
    Mise en perspective, PAS une prédiction.

    Args:
        sector_rotation: dict ``{sectors: {nom: {avg_change_7d, avg_change_30d}}}``.
        holdings_sectors: secteurs où l'utilisateur est exposé (priorité).

    Returns:
        Dict ``{available, narratives: [{sector, stage, c7, c30}], reading}``.
    """
    sectors = (sector_rotation or {}).get("sectors") or {}
    if not sectors:
        return {"available": False}
    narratives: list[dict[str, Any]] = []
    for name, data in sectors.items():
        if holdings_sectors and name not in holdings_sectors:
            continue
        c7 = data.get("avg_change_7d")
        c30 = data.get("avg_change_30d")
        if not isinstance(c7, (int, float)) or not isinstance(c30, (int, float)):
            continue
        strong_30 = c30 >= 10
        strong_7 = c7 >= 3
        if strong_30 and strong_7:
            stage = "euphorie"
        elif strong_30 and not strong_7:
            # Fort sur 30j mais l'élan retombe (7j tiède ou négatif) = rotation.
            stage = "rotation"
        elif not strong_30 and strong_7:
            stage = "émergence"
        else:
            stage = "consolidation"
        narratives.append({
            "sector": name, "stage": stage,
            "c7": round(c7, 1), "c30": round(c30, 1),
        })
    if not narratives:
        return {"available": False}
    # Trie : euphorie et émergence d'abord (les plus actionnables).
    _order = {"euphorie": 0, "émergence": 1, "rotation": 2, "consolidation": 3}
    narratives.sort(key=lambda n: _order.get(n["stage"], 9))
    _parts = [f"{n['sector']} en {n['stage']}" for n in narratives[:4]]
    return {
        "available": True,
        "narratives": narratives,
        "reading": (
            "Cycle des narratifs (momentum 7j/30j) : " + ", ".join(_parts)
            + ". Euphorie = sortie possible ; émergence = upside potentiel."
        ),
    }


def liquidity_regime(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Régime de liquidité à partir du M2 (FRED M2SL).

    Le M2 est mensuel : on compare la dernière valeur à celle de ~3 mois avant
    pour caractériser l'expansion/contraction. La liquidité mondiale est le
    driver le plus corrélé aux cycles crypto (audit #9).

    Args:
        fred_series: dict ``{name: {date: value}}`` issu de get_macro_series.

    Returns:
        Dict ``{available, trend, change_pct, reading}``.
    """
    m2 = (fred_series or {}).get("m2") or {}
    if len(m2) < 2:
        return {"available": False}
    # Trie par date croissante.
    items = sorted(m2.items())
    values = [v for _, v in items if isinstance(v, (int, float))]
    if len(values) < 2:
        return {"available": False}
    latest = values[-1]
    # ~3 mois avant si dispo (séries quasi-mensuelles), sinon le plus ancien.
    ref = values[-4] if len(values) >= 4 else values[0]
    if not ref:
        return {"available": False}
    change_pct = (latest - ref) / ref * 100
    if change_pct > 0.3:
        trend = "expansion"
        reading = (
            f"Liquidité (M2) en EXPANSION (+{_fr(change_pct)}% sur ~3 mois) : "
            "contexte structurel porteur pour le risque, quelle que soit la "
            "volatilité de court terme."
        )
    elif change_pct < -0.3:
        trend = "contraction"
        reading = (
            f"Liquidité (M2) en CONTRACTION ({_fr(change_pct)}% sur ~3 mois) : "
            "vent de face structurel pour les actifs risqués, rester sélectif."
        )
    else:
        trend = "stable"
        reading = (
            f"Liquidité (M2) globalement stable ({_fr(change_pct, sign=True)}% sur ~3 mois) : "
            "pas d'impulsion structurelle nette dans un sens ou l'autre."
        )
    return {
        "available": True,
        "trend": trend,
        "change_pct": round(change_pct, 1),
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# #10 — Cycle du dollar (DXY sur 3-6 mois)
# --------------------------------------------------------------------------- #
def dxy_cycle(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Tendance du dollar (DXY) sur ~3-6 mois — pas seulement le snapshot du jour.

    Un DXY en cycle baissier est historiquement favorable aux alts (audit #10).

    Returns:
        Dict ``{available, trend, change_pct, reading}``.
    """
    dxy = (fred_series or {}).get("dxy") or {}
    if len(dxy) < 5:
        return {"available": False}
    items = sorted(dxy.items())
    values = [v for _, v in items if isinstance(v, (int, float))]
    if len(values) < 5:
        return {"available": False}
    latest = values[-1]
    ref = values[0]  # début de la fenêtre fournie (~35j par défaut, élargissable)
    if not ref:
        return {"available": False}
    change_pct = (latest - ref) / ref * 100
    if change_pct <= -1.0:
        trend = "baissier"
        reading = (
            f"Dollar (DXY) en cycle BAISSIER ({_fr(change_pct)}% sur la fenêtre) : "
            "historiquement un contexte favorable aux altcoins."
        )
    elif change_pct >= 1.0:
        trend = "haussier"
        reading = (
            f"Dollar (DXY) en cycle HAUSSIER (+{_fr(change_pct)}%) : "
            "pression structurelle sur les actifs risqués, dont le crypto."
        )
    else:
        trend = "neutre"
        reading = (
            f"Dollar (DXY) sans tendance marquée ({_fr(change_pct, sign=True)}%) : "
            "neutre pour le risque à ce stade."
        )
    return {
        "available": True,
        "trend": trend,
        "change_pct": round(change_pct, 1),
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# #11 — Spread de crédit high yield (risque systémique)
# --------------------------------------------------------------------------- #
def credit_risk(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Spread high yield (FRED BAMLH0A0HYM2) — indicateur avancé de risk-off.

    Des spreads qui s'écartent précèdent souvent la pression sur le crypto
    (audit #11). On compare le niveau courant à sa moyenne récente.

    Returns:
        Dict ``{available, level, avg, widening, reading}``.
    """
    hy = (fred_series or {}).get("hy_spread") or {}
    if len(hy) < 5:
        return {"available": False}
    items = sorted(hy.items())
    values = [v for _, v in items if isinstance(v, (int, float))]
    if len(values) < 5:
        return {"available": False}
    level = values[-1]
    avg = statistics.fmean(values[:-1])
    delta = level - avg
    # Un écartement net = +0.3 pt vs moyenne (les HY spreads bougent en dixièmes).
    if delta >= 0.3:
        widening = True
        reading = (
            f"Spreads high yield en ÉCARTEMENT ({_fr(level, 2)}% vs moyenne "
            f"{_fr(avg, 2)}%) : signal de risk-off structurel, prudence accrue — "
            "le crypto suit souvent avec retard."
        )
    elif delta <= -0.3:
        widening = False
        reading = (
            f"Spreads high yield en RESSERREMENT ({_fr(level, 2)}% vs moyenne "
            f"{_fr(avg, 2)}%) : appétit pour le risque sain, contexte porteur."
        )
    else:
        widening = False
        reading = (
            f"Spreads high yield stables ({_fr(level, 2)}%, proche de la moyenne "
            f"{_fr(avg, 2)}%) : pas de stress de crédit notable."
        )
    return {
        "available": True,
        "level": round(level, 2),
        "avg": round(avg, 2),
        "widening": widening,
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# P0 #55 — Alignement macro au risque (alimente le signal composite macro_alignment)
# --------------------------------------------------------------------------- #
def macro_alignment_score(
    fred_series: dict[str, dict[str, float]],
    vix: Optional[float] = None,
) -> Optional[float]:
    """Score 0-100 d'ALIGNEMENT du régime macro au risque (market-wide).

    Remplace le signal ``macro_alignment`` du score composite, jusque-là TOUJOURS
    ``None`` (5% du poids gaspillés). Combine des signaux macro déterministes
    déjà calculés : liquidité M2, cycle du dollar, spreads de crédit, VIX.

    DÉLIBÉRÉMENT PLAFONNÉ à [40, 60] : conforme au principe « la macro est un
    CONTEXTE, pas un déclencheur ». Le score reste donc sous le seuil de
    convergence (±12 du neutre) — il nuance le composite sans jamais, à lui seul,
    rendre un actif éligible. ``None`` si aucune donnée macro disponible.

    Args:
        fred_series: séries FRED datées (``m2``, ``dxy``, ``hy_spread``).
        vix: niveau VIX courant (optionnel).

    Returns:
        Score float dans [40, 60], ou ``None`` si rien de calculable.
    """
    pts = 0.0
    used = False

    lr = liquidity_regime(fred_series)
    if lr.get("available"):
        used = True
        pts += 2.5 if lr["trend"] == "expansion" else (-2.5 if lr["trend"] == "contraction" else 0.0)

    dx = dxy_cycle(fred_series)
    if dx.get("available"):
        used = True
        pts += 2.5 if dx["trend"] == "baissier" else (-2.5 if dx["trend"] == "haussier" else 0.0)

    cr = credit_risk(fred_series)
    if cr.get("available"):
        used = True
        delta = cr["level"] - cr["avg"]
        pts += -2.5 if delta >= 0.3 else (2.5 if delta <= -0.3 else 0.0)

    if isinstance(vix, (int, float)):
        used = True
        pts += -2.5 if vix >= 25 else (2.5 if vix <= 16 else 0.0)

    if not used:
        return None
    return round(max(40.0, min(60.0, 50.0 + pts)), 1)


def altseason_context(global_market: dict[str, Any]) -> dict[str, Any]:
    """Contexte « saison des alts » via la dominance BTC (v22 #22)."""
    btc_dom = (global_market or {}).get("btc_dominance_pct")
    if not isinstance(btc_dom, (int, float)):
        return {"available": False}
    if btc_dom >= 55:
        reading = (
            f"Dominance BTC élevée ({btc_dom:.0f}%) : capital concentré sur BTC, "
            "conditions DÉFAVORABLES aux alts (pas de saison des alts)."
        )
    elif btc_dom <= 45:
        reading = (
            f"Dominance BTC basse ({btc_dom:.0f}%) : rotation vers les alts "
            "(saison des alts plus probable)."
        )
    else:
        reading = (
            f"Dominance BTC intermédiaire ({btc_dom:.0f}%) : pas de signal "
            "altseason franc."
        )
    return {"available": True, "btc_dominance_pct": round(btc_dom, 1), "reading": reading}


def _latest_value(fred_series: dict[str, dict[str, float]], key: str) -> Optional[float]:
    """Dernière valeur d'une série FRED datée (``{date: value}``)."""
    d = (fred_series or {}).get(key) or {}
    if not d:
        return None
    items = sorted(d.items())
    return items[-1][1] if items else None


# --------------------------------------------------------------------------- #
# v22 #34 — Courbe des taux 2s10s (cycle / récession)
# --------------------------------------------------------------------------- #
def yield_curve(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Spread 10Y − 2Y. Négatif (inversé) = signal avancé de récession."""
    t10 = _latest_value(fred_series, "us_10y")
    t2 = _latest_value(fred_series, "us_2y")
    if t10 is None or t2 is None:
        return {"available": False}
    spread = t10 - t2
    if spread < 0:
        reading = (
            f"Courbe des taux INVERSÉE (10Y−2Y = {spread:+.2f} pt) : signal "
            "historique de récession, prudence sur les actifs risqués à moyen terme."
        )
    elif spread < 0.5:
        reading = (
            f"Courbe des taux plate (10Y−2Y = {spread:+.2f} pt) : cycle en "
            "transition, pas de signal franc."
        )
    else:
        reading = (
            f"Courbe des taux pentue (10Y−2Y = {spread:+.2f} pt) : configuration "
            "de reprise, favorable au risque."
        )
    return {"available": True, "spread": round(spread, 2), "reading": reading}


# --------------------------------------------------------------------------- #
# v22 #33 — Taux réels 10Y (coût d'opportunité du risque)
# --------------------------------------------------------------------------- #
def real_rates(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Taux réel 10Y (TIPS). Élevé = vent de face pour le risque/crypto."""
    r = _latest_value(fred_series, "real_10y")
    if r is None:
        return {"available": False}
    if r >= 2.0:
        reading = (
            f"Taux réel 10Y élevé ({_fr(r, 2)}%) : coût d'opportunité fort, vent de "
            "face structurel pour les actifs sans rendement (dont le crypto)."
        )
    elif r <= 0.5:
        reading = (
            f"Taux réel 10Y bas ({_fr(r, 2)}%) : contexte porteur pour les actifs "
            "risqués et le crypto (peu d'alternative sans risque attractive)."
        )
    else:
        reading = f"Taux réel 10Y intermédiaire ({_fr(r, 2)}%) : impact neutre."
    return {"available": True, "real_10y": round(r, 2), "reading": reading}


# --------------------------------------------------------------------------- #
# v22 #35 — Liquidité Fed (bilan WALCL + reverse repo RRP)
# --------------------------------------------------------------------------- #
def fed_liquidity(fred_series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Tendance du bilan Fed (QE/QT) et du reverse repo (liquidité drainée)."""
    assets = (fred_series or {}).get("fed_assets") or {}
    items = sorted(assets.items())
    if len(items) < 2:
        return {"available": False}
    latest = items[-1][1]
    ref = items[0][1]
    if not ref:
        return {"available": False}
    change_pct = (latest - ref) / ref * 100
    rrp = _latest_value(fred_series, "reverse_repo")
    if change_pct <= -0.5:
        trend = "contraction"
        reading = (
            f"Bilan Fed en CONTRACTION ({_fr(change_pct, sign=True)}% sur la fenêtre, QT) : "
            "liquidité retirée, vent de face pour le risque."
        )
    elif change_pct >= 0.5:
        trend = "expansion"
        reading = (
            f"Bilan Fed en EXPANSION ({_fr(change_pct, sign=True)}%, QE) : liquidité injectée, "
            "contexte porteur."
        )
    else:
        trend = "stable"
        reading = f"Bilan Fed quasi stable ({_fr(change_pct, sign=True)}%) : pas d'impulsion nette."
    if rrp is not None:
        reading += f" Reverse repo à {rrp:.0f} Mds$ (liquidité parquée)."
    return {"available": True, "trend": trend, "change_pct": round(change_pct, 2),
            "reverse_repo": rrp, "reading": reading}


# --------------------------------------------------------------------------- #
# #12 — Saisonnalité crypto
# --------------------------------------------------------------------------- #
# Contexte saisonnier statistique indicatif (BTC, 2014-2025). Ordres de grandeur,
# PAS une garantie : sert uniquement de mise en perspective probabiliste.
_BTC_SEASONALITY: dict[int, str] = {
    1: "janvier historiquement positif (effet début d'année), mais volatil",
    2: "février souvent positif sur 10 ans",
    3: "mars mitigé, sans biais net",
    4: "avril historiquement l'un des meilleurs mois pour BTC",
    5: "mai mitigé (« sell in May » parfois observé)",
    6: "juin neutre à légèrement négatif sur 5 ans",
    7: "juillet souvent positif (rebond d'été)",
    8: "août mitigé à négatif",
    9: "septembre historiquement le PIRE mois pour BTC",
    10: "octobre (« Uptober ») historiquement très favorable",
    11: "novembre souvent haussier (saison Q4)",
    12: "décembre mitigé, prises de profit de fin d'année fréquentes",
}


def seasonality(now: Optional[datetime] = None) -> dict[str, Any]:
    """Contexte saisonnier statistique du mois courant (audit #12).

    Returns:
        Dict ``{available, month, reading}``.
    """
    now = now or datetime.now(timezone.utc)
    note = _BTC_SEASONALITY.get(now.month)
    if not note:
        return {"available": False}
    return {
        "available": True,
        "month": now.month,
        "reading": (
            f"Saisonnalité (BTC, ~10 ans) : {note}. "
            "Contexte probabiliste indicatif, jamais déterministe."
        ),
    }


# --------------------------------------------------------------------------- #
# #4 — Régime de volatilité réalisée du portefeuille
# --------------------------------------------------------------------------- #
def realized_vol_regime(
    price_series: dict[str, list[float]],
    weights: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Volatilité réalisée du PTF : expansion ou compression ? (audit #4)

    Une compression prolongée (squeeze) précède souvent un mouvement violent.
    On calcule la vol réalisée des rendements quotidiens du portefeuille pondéré
    sur une fenêtre récente vs une fenêtre antérieure.

    Args:
        price_series: dict ``{symbol: [prix chronologiques]}`` (~30 points).
        weights: pondération optionnelle par actif (sinon équipondéré).

    Returns:
        Dict ``{available, regime, recent_vol, prior_vol, reading}``.
    """
    # Construit la série de rendements du portefeuille pondéré.
    series = {s: v for s, v in (price_series or {}).items()
              if isinstance(v, list) and len(v) >= 21}
    if not series:
        return {"available": False}
    n = min(len(v) for v in series.values())
    if n < 21:
        return {"available": False}
    # Aligne sur les n derniers points.
    aligned = {s: v[-n:] for s, v in series.items()}
    w = weights or {s: 1.0 for s in aligned}
    w_total = sum(w.get(s, 0) for s in aligned) or 1.0
    # Rendements quotidiens du portefeuille.
    ptf_returns: list[float] = []
    for i in range(1, n):
        r = 0.0
        for s, prices in aligned.items():
            if prices[i - 1]:
                r += (w.get(s, 0) / w_total) * (prices[i] - prices[i - 1]) / prices[i - 1]
        ptf_returns.append(r)
    if len(ptf_returns) < 20:
        return {"available": False}
    # Vol récente (10 derniers) vs antérieure (10 précédents).
    recent = ptf_returns[-10:]
    prior = ptf_returns[-20:-10]
    try:
        recent_vol = statistics.pstdev(recent) * 100
        prior_vol = statistics.pstdev(prior) * 100
    except statistics.StatisticsError:
        return {"available": False}
    if prior_vol <= 0:
        return {"available": False}
    ratio = recent_vol / prior_vol
    if ratio >= 1.25:
        regime = "expansion"
        reading = (
            f"Volatilité réalisée du portefeuille en EXPANSION "
            f"({_fr(recent_vol)}% vs {_fr(prior_vol)}% avant) : le marché bouge "
            "plus fort, gérer la taille des positions."
        )
    elif ratio <= 0.8:
        regime = "compression"
        reading = (
            f"Volatilité réalisée en COMPRESSION ({_fr(recent_vol)}% vs "
            f"{_fr(prior_vol)}% avant) : « calme avant la tempête » possible — "
            "un squeeze prolongé précède souvent un mouvement violent."
        )
    else:
        regime = "stable"
        reading = (
            f"Volatilité réalisée stable ({_fr(recent_vol)}%) : "
            "pas de changement de régime notable."
        )
    return {
        "available": True,
        "regime": regime,
        "recent_vol": round(recent_vol, 1),
        "prior_vol": round(prior_vol, 1),
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# #17 — Structure de marché (Higher Highs/Lows vs Lower Highs/Lows)
# --------------------------------------------------------------------------- #
def _swing_structure(prices: list[float]) -> Optional[str]:
    """Caractérise la structure D1 d'une série de prix via pivots simples.

    Détecte les sommets/creux locaux (pivots sur fenêtre de 1, soit 3 points) et
    compare les deux derniers de chaque type pour qualifier : haussière (HH+HL),
    baissière (LH+LL) ou range.
    """
    if len(prices) < 7:
        return None
    highs: list[float] = []
    lows: list[float] = []
    for i in range(1, len(prices) - 1):
        if prices[i] > prices[i - 1] and prices[i] >= prices[i + 1]:
            highs.append(prices[i])
        if prices[i] < prices[i - 1] and prices[i] <= prices[i + 1]:
            lows.append(prices[i])
    if len(highs) < 2 or len(lows) < 2:
        return None
    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]
    if hh and hl:
        return "haussière"
    if lh and ll:
        return "baissière"
    return "range"


def market_structure(
    price_series: dict[str, list[float]],
    focus_assets: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Structure de marché D1 par actif (audit #17).

    Un actif peut être en tendance baissière de structure même avec un RSI bas.
    On qualifie chaque actif suivi en haussière / baissière / range.

    Args:
        price_series: dict ``{symbol: [prix chronologiques]}``.
        focus_assets: actifs à analyser en priorité (sinon tous ceux dispo).

    Returns:
        Dict ``{available, structures: {symbol: label}, reading}``.
    """
    series = {s: v for s, v in (price_series or {}).items()
              if isinstance(v, list) and len(v) >= 10}
    if focus_assets:
        series = {s: v for s, v in series.items() if s in focus_assets}
    if not series:
        return {"available": False}
    structures: dict[str, str] = {}
    for s, prices in series.items():
        label = _swing_structure(prices)
        if label:
            structures[s] = label
    if not structures:
        return {"available": False}
    bearish = [s for s, l in structures.items() if l == "baissière"]
    bullish = [s for s, l in structures.items() if l == "haussière"]
    parts = []
    if bullish:
        parts.append("structure haussière (HH/HL) : " + ", ".join(sorted(bullish)[:5]))
    if bearish:
        parts.append("structure baissière (LH/LL) : " + ", ".join(sorted(bearish)[:5]))
    reading = (
        "Structure de marché D1 — " + " ; ".join(parts)
        if parts else "Structure de marché majoritairement en range."
    )
    return {
        "available": True,
        "structures": structures,
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# #16 — Garde-fou biais de confirmation
# --------------------------------------------------------------------------- #
def confirmation_bias_guard(recent_theses: list[dict[str, Any]]) -> dict[str, Any]:
    """Détecte un biais de confirmation : 3 dernières thèses même sens (audit #16).

    Si les 3 dernières thèses sur un actif vont toutes dans le même sens
    (toutes bullish ou toutes bearish), on demande à Gemini d'argumenter
    explicitement le scénario CONTRAIRE.

    Args:
        recent_theses: liste de dicts ``{asset, action_type|direction, created_at}``
            triés du plus récent au plus ancien.

    Returns:
        Dict ``{active, flagged_assets: [{asset, direction, count}], note}``.
    """
    from collections import defaultdict

    by_asset: dict[str, list[str]] = defaultdict(list)
    for t in recent_theses or []:
        asset = t.get("asset")
        direction = (t.get("action_type") or t.get("direction") or "").lower()
        if not asset or direction not in ("bullish", "bearish"):
            continue
        by_asset[asset].append(direction)

    flagged = []
    for asset, dirs in by_asset.items():
        last3 = dirs[:3]
        if len(last3) >= 3 and len(set(last3)) == 1:
            flagged.append({"asset": asset, "direction": last3[0], "count": len(last3)})

    if not flagged:
        return {"active": False}
    _names = ", ".join(f["asset"] for f in flagged)
    return {
        "active": True,
        "flagged_assets": flagged,
        "note": (
            f"Biais de confirmation potentiel sur : {_names} (3 dernières thèses "
            "dans le même sens). Argumente EXPLICITEMENT le scénario contraire "
            "avant de confirmer la direction — évite le momentum bias."
        ),
    }


# --------------------------------------------------------------------------- #
# #7 — MVRV mis en perspective (sous/sur-évaluation vs cycles)
# --------------------------------------------------------------------------- #
def mvrv_context(mvrv_value: Optional[float], asset: str = "BTC") -> dict[str, Any]:
    """Met le MVRV en perspective historique (audit #7).

    MVRV = prix spot / realized price. < 1 = marché globalement en perte
    (zone d'accumulation historique), > 3.5 = euphorie (zone de distribution).

    Args:
        mvrv_value: ratio MVRV courant.
        asset: actif concerné (pour le libellé).

    Returns:
        Dict ``{available, zone, reading}``.
    """
    if not isinstance(mvrv_value, (int, float)) or mvrv_value <= 0:
        return {"available": False}
    if mvrv_value < 1.0:
        zone = "accumulation"
        reading = (
            f"MVRV {asset} à {_fr(mvrv_value, 2)} (< 1) : le marché est globalement "
            "EN PERTE sur ses coins — historiquement une zone d'accumulation de "
            "cycle, pas de distribution."
        )
    elif mvrv_value > 3.5:
        zone = "euphorie"
        reading = (
            f"MVRV {asset} à {_fr(mvrv_value, 2)} (> 3,5) : zone d'EUPHORIE "
            "historique (profits latents élevés) — prudence, risque de "
            "distribution de cycle."
        )
    else:
        zone = "neutre"
        reading = (
            f"MVRV {asset} à {_fr(mvrv_value, 2)} : zone intermédiaire, ni "
            "survente de cycle ni euphorie."
        )
    return {"available": True, "zone": zone, "reading": reading}


def compute_all(
    fred_series: dict[str, dict[str, float]],
    price_series: dict[str, list[float]],
    *,
    weights: Optional[dict[str, float]] = None,
    focus_assets: Optional[list[str]] = None,
    recent_theses: Optional[list[dict[str, Any]]] = None,
    mvrv_value: Optional[float] = None,
    portfolio: Optional[dict[str, Any]] = None,
    enriched: Optional[dict[str, dict[str, Any]]] = None,
    onchain_assets: Optional[dict[str, dict[str, Any]]] = None,
    market: Optional[dict[str, dict[str, Any]]] = None,
    current_macro: Optional[dict[str, Any]] = None,
    snapshots: Optional[list[dict[str, Any]]] = None,
    dvol: Optional[float] = None,
    upcoming_events: Optional[list[dict[str, Any]]] = None,
    derivatives_by_asset: Optional[dict[str, dict[str, Any]]] = None,
    sector_rotation: Optional[dict[str, Any]] = None,
    holdings_sectors: Optional[list[str]] = None,
    strategic_wallets: Optional[dict[str, Any]] = None,
    global_market: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Agrège tous les signaux transverses disponibles en un seul bloc.

    Chaque sous-signal dégrade indépendamment ; on n'inclut que ceux qui sont
    réellement calculables. Pensé pour être injecté dans le payload/prompt.

    Returns:
        Dict ``{signals: {nom: {...}}, readings: [str]}`` (readings = la liste
        des lectures non vides, prête à passer à Gemini).
    """
    signals: dict[str, Any] = {}
    out_readings: list[str] = []

    def _add(name: str, result: dict[str, Any]) -> None:
        if result and (result.get("available") or result.get("active")):
            signals[name] = result
            if result.get("reading"):
                out_readings.append(result["reading"])
            elif result.get("note"):
                out_readings.append(result["note"])

    _add("liquidity_regime", liquidity_regime(fred_series))
    _add("dxy_cycle", dxy_cycle(fred_series))
    _add("credit_risk", credit_risk(fred_series))
    _add("yield_curve", yield_curve(fred_series))      # v22 #34
    _add("real_rates", real_rates(fred_series))         # v22 #33
    _add("fed_liquidity", fed_liquidity(fred_series))   # v22 #35
    if global_market is not None:
        _add("altseason", altseason_context(global_market))  # v22 #22
    _add("seasonality", seasonality(now))
    _add("realized_vol_regime", realized_vol_regime(price_series, weights))
    _add("market_structure", market_structure(price_series, focus_assets))
    if recent_theses is not None:
        _add("confirmation_bias", confirmation_bias_guard(recent_theses))
    if mvrv_value is not None:
        _add("mvrv_context", mvrv_context(mvrv_value))
    if portfolio is not None and enriched is not None:
        _add("allocation_gap", compute_allocation_gap(portfolio, enriched))
    if onchain_assets is not None and market is not None:
        _add("price_fundamentals_divergence",
             compute_price_fundamentals_divergence(onchain_assets, market))
    if current_macro is not None and snapshots is not None:
        _add("similar_context", compute_similar_context(current_macro, snapshots))
    if dvol is not None:
        _add("implied_move", compute_implied_move(dvol, upcoming_events))
    if derivatives_by_asset is not None:
        _add("derivatives_signal", compute_derivatives_signal(derivatives_by_asset))
    if sector_rotation is not None:
        _add("narrative_lifecycle",
             compute_narrative_lifecycle(sector_rotation, holdings_sectors))
    if strategic_wallets is not None and strategic_wallets.get("available"):
        # On reprend l'interprétation déjà calculée par la source (déterministe).
        _sw = {
            "available": True,
            "movements": strategic_wallets.get("movements", []),
            "reading": strategic_wallets.get("interpretation"),
        }
        _add("strategic_wallets", _sw)

    return {"signals": signals, "readings": out_readings}
