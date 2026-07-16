"""Scoring pondéré multi-dimensions pour l'éligibilité des thèses (Chantier F).

Remplace le comptage binaire de signaux (« 4+ signaux = éligible ») par un SCORE
PONDÉRÉ qui reflète qu'un signal fondamental long terme (MVRV < 1, position sous
PRU) pèse plus qu'un mouvement de +6% sur 24h. Conçu pour un investisseur LONG
TERME : les meilleures entrées arrivent souvent dans le calme, pas dans
l'agitation.

Barème de pondération (audit Partie 5) :
  • Fondamentaux LT (MVRV<1, sous PRU, drawdown profond + conviction) → poids 3
  • Techniques structurels (support W1/M1, RSI survente, divergence, BB squeeze) → poids 2
  • Catalyseurs calendrier confirmés (≤ 7j)                                → poids 2
  • Court terme (mouvement 24h, news isolée)                              → poids 1
  • Sentiment / dérivés (funding extrême, F&G<20, put/call extrême)       → poids 1

Seuils d'éligibilité (score total) : ≥4 Tier 1, ≥3 Tier 2, ≥2 Tier 3/4.

v21 (#73) — CONVERGENCE OBLIGATOIRE : en plus du score, une thèse exige des
signaux d'au moins 2 FAMILLES distinctes (fini les recos sur une dimension
unique « pas forcément bonne »). EXCEPTION accumulateur : un cluster fondamental
LT fort (≥2 signaux fondamentaux, poids ≥6 — MVRV<1 + sous PRU + drawdown
profond) reste éligible seul. Un signal LT unique faible ne suffit donc plus.

Distinction de type (audit Partie 5) :
  • « tactical »   : porté surtout par technique + catalyseur court terme
  • « conviction » : porté par fondamentaux + structure W1/M1 (+ sous PRU)
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Poids par catégorie de signal.
_W_FUNDAMENTAL_LT = 3
_W_TECHNICAL_STRUCT = 2
_W_CATALYST = 2
_W_SHORT_TERM = 1
_W_SENTIMENT = 1

# Seuils d'éligibilité par tier (score pondéré total).
_ELIGIBILITY_THRESHOLD = {0: 2, 1: 4, 2: 3, 3: 2, 4: 2}


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float))


def evaluate_thesis_eligibility(
    asset: dict[str, Any],
    *,
    tier: int,
    mvrv: Optional[float] = None,
    mvrv_stale: bool = False,
    mvrv_as_of: Optional[str] = None,
    pru_gap_pct: Optional[float] = None,
    drawdown_from_ath_pct: Optional[float] = None,
    upcoming_catalyst_days: Optional[int] = None,
    token_unlock_soon: bool = False,
    sector_change_7d: Optional[float] = None,
    funding_annualized_pct: Optional[float] = None,
    fear_greed: Optional[float] = None,
    put_call_ratio: Optional[float] = None,
    market_structure: Optional[str] = None,
) -> dict[str, Any]:
    """Évalue l'éligibilité d'un actif à une thèse via un score pondéré.

    Args:
        asset: dict des signaux de l'actif (porte ``change_24h``, ``news_24h``,
            ``tech_advanced`` avec rsi/macd/bollinger/structure, etc.).
        tier: tier de l'actif (0 = cœur BTC/ETH … 4 = poussière).
        mvrv: ratio MVRV (si dispo, BTC/ETH surtout).
        mvrv_stale: v28 (M-A7) — True si la donnée MVRV est PÉRIMÉE (> 7 j) :
            son poids est alors halvé (3 → 2) et le libellé porte la date.
            Le 07/07, un MVRV ETH du 23/05 (6 semaines) était le signal n°1
            (+3) de la reco ETH — un signal daté ne peut pas piloter la thèse.
        mvrv_as_of: date (« AAAA-MM-JJ » ou « JJ/MM ») de la donnée MVRV.
        pru_gap_pct: écart au PRU en % (négatif = sous le PRU). None si pas de PRU.
        drawdown_from_ath_pct: distance à l'ATH en % (négatif).
        upcoming_catalyst_days: jours avant un catalyseur calendrier (None si aucun).
        token_unlock_soon: True si un unlock significatif est imminent.
        sector_change_7d: variation 7j du secteur (rotation favorable si > 0).
        funding_annualized_pct: funding annualisé (extrême négatif = capitulation).
        fear_greed: indice Fear & Greed (0-100).
        put_call_ratio: ratio put/call (extrême = signal).
        market_structure: 'haussière' / 'baissière' / 'range' (D1, depuis cross_signals).

    Returns:
        Dict ``{score, eligible, threshold, thesis_type, signals: [{label,
        category, weight}], dimensions_count}``.
    """
    signals: list[dict[str, Any]] = []

    def _sig(label: str, category: str, weight: int) -> None:
        signals.append({"label": label, "category": category, "weight": weight})

    ta = asset.get("tech_advanced") or {}
    # P0 #56 — Sources techniques RÉELLES, jusque-là débranchées :
    #   • RSI HEBDO : exposé par TradingView dans technical.per_tf["1w"].rsi, PAS
    #     dans tech_advanced.rsi_weekly (toujours None) — le signal poids 2 était
    #     donc MORT. On lit la vraie valeur, avec repli défensif.
    #   • DIVERGENCE prix/RSI : produite par technical_local (calcul local sur
    #     l'OHLC), pas par tech_advanced — le signal poids 2 était mort aussi.
    #   • BOLLINGER : repli sur le bloc local si tech_advanced n'a pas tourné
    #     (ex. Tier 2-3 sans OHLC profond au scan).
    _tech_mtf = asset.get("technical") or {}
    _per_tf = _tech_mtf.get("per_tf") or {}
    _tlocal = asset.get("tech_local") or {}
    _rsi_weekly_real = (_per_tf.get("1w") or {}).get("rsi")

    # ----- Fondamentaux LT (poids 3) -----
    if _is_num(mvrv) and mvrv < 1.0:
        # v28 (M-A7) — MVRV PÉRIMÉ (> 7 j) : poids halvé (3 → 2) et libellé
        # daté. Le 07/07, un MVRV ETH du 23/05 pesait +3 en signal n°1 de la
        # reco : une donnée de 6 semaines ne pilote plus une thèse à plein poids.
        _w_mvrv = _W_FUNDAMENTAL_LT
        _lbl_mvrv = f"MVRV {mvrv:.2f} < 1 (sous-évaluation historique)"
        if mvrv_stale:
            _w_mvrv = max(1, _W_FUNDAMENTAL_LT - 1)
            _d_mvrv = (f"{mvrv_as_of[8:10]}/{mvrv_as_of[5:7]}"
                       if isinstance(mvrv_as_of, str) and len(mvrv_as_of) >= 10
                       else None)
            _lbl_mvrv += f" · au {_d_mvrv}" if _d_mvrv else " · donnée datée"
        _sig(_lbl_mvrv, "fundamental_lt", _w_mvrv)
    if _is_num(pru_gap_pct) and pru_gap_pct <= -10:
        _sig(f"position sous PRU de {abs(pru_gap_pct):.0f}% (opportunité de moyenner)",
             "fundamental_lt", _W_FUNDAMENTAL_LT)
    if (_is_num(drawdown_from_ath_pct) and drawdown_from_ath_pct <= -60
            and tier in (0, 1, 2)):
        _sig(f"drawdown {abs(drawdown_from_ath_pct):.0f}% vs ATH sur conviction tier {tier}",
             "fundamental_lt", _W_FUNDAMENTAL_LT)
    # Divergence fondamentale : activité réseau en hausse vs prix qui stagne/baisse.
    _aa_trend = (asset.get("onchain") or {}).get("active_addresses_trend_pct")
    _ch24 = asset.get("change_24h")
    if (_is_num(_aa_trend) and _aa_trend >= 5 and _is_num(_ch24) and _ch24 <= 0):
        _sig("activité réseau en hausse malgré prix stagnant (fondamentaux > prix)",
             "fundamental_lt", _W_FUNDAMENTAL_LT)

    # ----- Techniques structurels (poids 2) -----
    # On lit la structure RÉELLE de tech_advanced (bollinger/moving_averages sont
    # des sous-objets, pas des clés à plat). RSI hebdo et divergence ne sont pas
    # fournis par la source actuelle : on les garde défensifs (clés à plat OU
    # éventuel sous-objet) pour le jour où une source les exposera, sans présumer.
    _boll = ta.get("bollinger") or (_tlocal.get("bollinger") if _tlocal.get("available") else None) or {}
    _mas = ta.get("moving_averages") or {}
    _sr = ta.get("support_resistance") or {}
    # RSI hebdo réel (TradingView per_tf) avec repli sur l'ancienne clé à plat.
    _rsi_w = _rsi_weekly_real if _is_num(_rsi_weekly_real) else ta.get("rsi_weekly")
    if _is_num(_rsi_w) and _rsi_w <= 35:
        _sig(f"RSI hebdo {_rsi_w:.0f} en survente structurelle",
             "technical_struct", _W_TECHNICAL_STRUCT)
    # Prix sur la bande basse de Bollinger = support technique majeur.
    if _boll.get("position") == "lower" or ta.get("bollinger_position") == "lower":
        _sig("prix sur la bande basse de Bollinger (support technique)",
             "technical_struct", _W_TECHNICAL_STRUCT)
    # Très proche d'un support identifié (≤ 2%) = zone d'intérêt structurelle.
    # CORRECTIF v18.1 : ``dist_to_support_pct`` est niché sous ``support_resistance``
    # (cf. technical_advanced), pas à plat sur tech_advanced. L'ancienne lecture
    # ``ta.get("dist_to_support_pct")`` renvoyait toujours None → signal poids 2
    # MORT (même classe que les bugs d'audit). On lit la structure réelle, avec
    # repli à plat défensif si une future source l'expose autrement.
    _dist_sup = _sr.get("dist_to_support_pct")
    if _dist_sup is None:
        _dist_sup = ta.get("dist_to_support_pct")
    if _is_num(_dist_sup) and 0 <= _dist_sup <= 2:
        _sig(f"prix à {_dist_sup:.1f}% d'un support clé",
             "technical_struct", _W_TECHNICAL_STRUCT)
    if _tlocal.get("bullish_divergence") or ta.get("bullish_divergence"):
        _sig("divergence haussière prix/RSI (plus-bas prix, RSI plus haut)",
             "technical_struct", _W_TECHNICAL_STRUCT)
    # Golden cross SMA50/200 (clé réelle : moving_averages.cross == 'golden').
    if _mas.get("cross") == "golden" or ta.get("ma_cross") in ("golden_cross", "bull_cross"):
        _sig("golden cross MM 50/200", "technical_struct", _W_TECHNICAL_STRUCT)
    # Compression de volatilité : bande de Bollinger étroite (width_pct faible)
    # OU flag explicite. Seuil prudent (< 8% de largeur relative).
    _bb_width = _boll.get("width_pct")
    if ta.get("bb_squeeze") or (_is_num(_bb_width) and 0 < _bb_width < 8):
        _sig("compression de volatilité (bandes de Bollinger resserrées)",
             "technical_struct", _W_TECHNICAL_STRUCT)
    if market_structure == "haussière":
        _sig("structure de marché haussière (HH/HL)",
             "technical_struct", _W_TECHNICAL_STRUCT)

    # ----- Catalyseurs calendrier (poids 2) -----
    if _is_num(upcoming_catalyst_days) and 0 <= upcoming_catalyst_days <= 7:
        # v30 (#76) — libellé lisible (« aujourd'hui », plus « dans 0j ») et
        # explicitement PROPRE À L'ACTIF (le caller ne transmet plus le macro).
        _cat_lbl = ("catalyseur de l'actif aujourd'hui"
                    if int(upcoming_catalyst_days) == 0
                    else f"catalyseur de l'actif dans {int(upcoming_catalyst_days)}j")
        _sig(_cat_lbl, "catalyst", _W_CATALYST)
    if token_unlock_soon:
        _sig("token unlock significatif imminent (risque baissier = signal ALLÉGER)",
             "catalyst", _W_CATALYST)

    # ----- Sectoriels (poids 1, rangé en court terme/contexte) -----
    if _is_num(sector_change_7d) and sector_change_7d >= 5:
        _sig(f"rotation sectorielle favorable (+{sector_change_7d:.0f}% sur 7j)",
             "short_term", _W_SHORT_TERM)

    # ----- Court terme (poids 1) -----
    if _is_num(_ch24) and abs(_ch24) >= 5:
        _sig(f"mouvement 24h de {_ch24:+.0f}%", "short_term", _W_SHORT_TERM)
    if (asset.get("news_24h_count") or 0) >= 1:
        _sig("news récente sur l'actif", "short_term", _W_SHORT_TERM)

    # ----- Sentiment / dérivés (poids 1) -----
    if _is_num(funding_annualized_pct) and funding_annualized_pct <= -10:
        _sig("funding négatif extrême (capitulation des leveragés, fond probable)",
             "sentiment", _W_SENTIMENT)
    if (_is_num(fear_greed) and fear_greed < 20
            and _is_num(pru_gap_pct) and pru_gap_pct <= 0):
        _sig("Fear & Greed < 20 + position sous PRU (setup d'accumulation)",
             "sentiment", _W_SENTIMENT)
    if _is_num(put_call_ratio) and (put_call_ratio >= 1.3 or put_call_ratio <= 0.5):
        _sig(f"put/call ratio extrême ({put_call_ratio:.2f})",
             "sentiment", _W_SENTIMENT)

    score = sum(s["weight"] for s in signals)
    threshold = _ELIGIBILITY_THRESHOLD.get(tier, 3)

    # ----- Type de thèse (tactique vs conviction) -----
    fund_weight = sum(s["weight"] for s in signals if s["category"] == "fundamental_lt")
    struct_weight = sum(s["weight"] for s in signals if s["category"] == "technical_struct")
    catalyst_weight = sum(s["weight"] for s in signals if s["category"] == "catalyst")

    # ----- ÉLIGIBILITÉ v21 (#73) : SCORE + CONVERGENCE ≥ 2 FAMILLES -----
    # Une thèse n'est plus éligible sur un seul axe : il faut le score pondéré
    # ET des signaux d'au moins 2 FAMILLES distinctes (fondamental / technique /
    # catalyseur / dérivés-sentiment / court terme). Objectif : supprimer les
    # recos déclenchées sur une dimension unique « pas forcément bonne ».
    # EXCEPTION (profil accumulateur de conviction) : un CLUSTER FONDAMENTAL LT
    # FORT (≥ 2 signaux fondamentaux, poids cumulé ≥ 6 — ex. MVRV<1 + sous PRU +
    # drawdown profond) reste éligible seul : ce sont les meilleures fenêtres
    # d'accumulation, qui surviennent justement dans le calme (sans news ni
    # mouvement de prix). On expose ``families_count`` et ``convergent`` pour le
    # rendu (fin de la contradiction « seuil non atteint » alors que le score est
    # élevé : l'éligibilité affichée reflète exactement cette règle déterministe).
    families = {s["category"] for s in signals}
    fund_signals_count = sum(1 for s in signals if s["category"] == "fundamental_lt")
    strong_fundamental_cluster = fund_signals_count >= 2 and fund_weight >= 6
    convergent = len(families) >= 2 or strong_fundamental_cluster
    eligible = (score >= threshold) and convergent
    # Conviction : portée par les fondamentaux LT (et idéalement la structure W1).
    # Tactique : portée surtout par le technique + un catalyseur court terme.
    if fund_weight >= _W_FUNDAMENTAL_LT:
        thesis_type = "conviction"
    elif catalyst_weight >= _W_CATALYST or struct_weight >= _W_TECHNICAL_STRUCT:
        thesis_type = "tactical"
    else:
        thesis_type = "tactical"

    # Nombre de DIMENSIONS distinctes qui convergent (pour le plafond de confiance).
    dimensions = {s["category"] for s in signals}

    return {
        "score": score,
        "eligible": eligible,
        "threshold": threshold,
        "thesis_type": thesis_type,
        "signals": signals,
        "dimensions_count": len(dimensions),
        "families_count": len(families),
        "convergent": convergent,
        "strong_fundamental_cluster": strong_fundamental_cluster,
        "fundamental_weight": fund_weight,
    }


# P0 #53 — Dimensions analytiques RÉELLEMENT disponibles par actif. Sert à calculer
# un SCORE DE COMPLÉTUDE qui plafonne la confiance : pas de reco ferme à haute
# confiance sur une analyse à trous (ex. un alt sans on-chain ni dérivés). C'est le
# verrou anti « reco sur analyse incomplète/insuffisante ».
_COMPLETENESS_DIMS = (
    "technique", "on-chain", "fondamental", "dérivés", "sentiment", "structure de prix",
)


def compute_completeness(asset: dict[str, Any]) -> dict[str, Any]:
    """Part des dimensions d'analyse réellement disponibles pour un actif.

    Args:
        asset: dict de signaux de l'actif (tel que construit par main).

    Returns:
        Dict ``{pct, available_count, total, available: {dim: bool}, missing: [..]}``.
    """
    ta = asset.get("tech_advanced") or {}
    tl = asset.get("tech_local") or {}
    tech_mtf = asset.get("technical") or {}
    onchain = asset.get("onchain") or {}
    tvl = asset.get("tvl") or {}
    dev = asset.get("dev") or {}
    deriv = asset.get("derivatives") or {}
    social = asset.get("social") or {}
    available = {
        "technique": bool(
            ta.get("available") or tl.get("available")
            or tech_mtf.get("score") is not None
        ),
        "on-chain": bool(
            isinstance(onchain, dict)
            and (_is_num(onchain.get("mvrv"))
                 or _is_num(onchain.get("active_addresses_trend_pct")))
        ),
        "fondamental": bool(
            tvl.get("available") or dev.get("available")
            or (asset.get("valuation") or {}).get("available")
        ),
        "dérivés": bool(deriv.get("available")),
        "sentiment": bool(social.get("available")),
        "structure de prix": bool(asset.get("price_series_30d")),
    }
    n_avail = sum(1 for v in available.values() if v)
    total = len(available)
    return {
        "pct": round(n_avail / total * 100),
        "available_count": n_avail,
        "total": total,
        "available": available,
        "missing": [k for k, v in available.items() if not v],
    }


def completeness_cap(completeness_pct: float) -> int:
    """Plafond de confiance imposé par la complétude de l'analyse (#53).

    Plus l'analyse couvre de dimensions réelles, plus la confiance peut monter.
    Une analyse partielle ne peut PAS fonder une reco à haute confiance.
    """
    if completeness_pct >= 80:
        return 85
    if completeness_pct >= 60:
        return 75
    if completeness_pct >= 40:
        return 65
    return 60


def confidence_bounds(
    thesis_type: str,
    dimensions_count: int,
    completeness_pct: Optional[float] = None,
) -> dict[str, Any]:
    """Retourne les bornes de confiance honnêtes selon le type (audit Partie 5).

    • v23.x — SEUIL D'AFFICHAGE UNIQUE 75% (Omar) : toute thèse affichée exige
      ≥ 75% de confiance. Les planchers sont donc relevés à 75% pour les deux types,
      et le plafond TACTIQUE est porté de 70% à 80% afin qu'une thèse tactique
      RÉELLEMENT forte puisse franchir le seuil (sinon le type serait condamné).
    • tactique   : seuil 75%, plafond 80%
    • conviction : seuil 75%, plafond 85%
    • > 80% interdit sauf si ≥ 5 dimensions convergent.
    • P0 #53 : le plafond est EN PLUS borné par la complétude de l'analyse
      (une analyse à trous ne fonde pas une reco à haute confiance → filtrée par
      le seuil 75%, ce qui élimine le bruit recherché).

    Args:
        thesis_type: 'tactical' ou 'conviction'.
        dimensions_count: nombre de dimensions distinctes qui convergent.
        completeness_pct: % de dimensions analytiques disponibles (optionnel).

    Returns:
        Dict ``{floor, cap, completeness_pct, completeness_cap, hard_cap_note}``.
    """
    if thesis_type == "conviction":
        floor, cap = 75, 85
    else:
        floor, cap = 75, 80
    # Plafond dur : > 80% seulement si ≥ 5 dimensions.
    if cap > 80 and dimensions_count < 5:
        cap = 80
    comp_cap: Optional[int] = None
    if completeness_pct is not None:
        comp_cap = completeness_cap(completeness_pct)
        cap = min(cap, comp_cap)
        floor = min(floor, cap)  # garde floor <= cap si la complétude est très basse
    return {
        "floor": floor,
        "cap": cap,
        "completeness_pct": completeness_pct,
        "completeness_cap": comp_cap,
        "hard_cap_note": (
            "Confiance > 80% autorisée uniquement avec ≥ 5 dimensions convergentes ; "
            "plafond abaissé si l'analyse est incomplète (peu de dimensions disponibles)."
        ),
    }
