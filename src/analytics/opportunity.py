"""Moteur d'opportunité v33 — décision DÉTERMINISTE d'un changement d'allocation.

CE QUE CE MODULE FAIT — ET CE QU'IL REFUSE DE FAIRE
====================================================

Il ne prévoit PAS le rendement d'un actif. Aucun alpha n'est estimable sur ces
données : l'erreur-type d'un alpha cumulé vaut ``σ_ε·H/√n``, soit ±15,8 points
à 30 jours et ±192 points à 12 mois pour un altcoin typique sur 90 jours
d'historique. Un moteur qui affirmerait savoir quel actif surperformera se
tromperait sur ce qu'il sait.

Il calcule à la place le **rendement REQUIS** — grandeur exactement
calculable à une hypothèse de coûts près — et vérifie qu'un **potentiel
MESURÉ** le couvre ::

    R_requis = coûts + contrefactuel·H + prime_référence × M

    M = f_volatilité × f_liquidité × f_dilution × f_couverture

où chaque facteur compare l'actif à la MÉDIANE de l'univers du jour. Le seul
niveau absolu est le repère métier d'Omar (« l'actif médian doit m'offrir
≈ 20 % »), déclaré dans ``thresholds.yaml > opportunity_engine``.

PROPRIÉTÉ STRUCTURELLE CENTRALE
-------------------------------
``R_requis`` **ne dépend pas de la taille**. Une taille plus grande ne peut
donc JAMAIS rendre une thèse acceptable : le test qualité (potentiel ≥ requis)
est invariant en ``Δw``. C'est une garantie de construction — et c'est ce qui
distingue ce moteur de toute variante de Kelly.

AUCUN PLAFOND D'EXPOSITION (décision d'Omar, 01/10/2026)
--------------------------------------------------------
« Pas de hard cap d'exposition. Toute crypto peut être renforcée si la
conviction le justifie, y compris BTC/ETH au-delà de 25 %. Les seuils doivent
encadrer le sizing du renforcement, pas plafonner l'exposition totale. Aucun
seuil ne déclenche une vente ; l'allègement dépend de la conviction. »

Les bandes de taille encadrent donc le RENFORT ; le poids déjà détenu et le
poids ajusté du bêta sont PUBLIÉS (information de concentration), jamais
utilisés comme plafond ni comme déclencheur de vente.

LE POTENTIEL N'EST JAMAIS DÉRIVÉ DE L'ATH
-----------------------------------------
Tout ancrage sur l'ATH recommande mécaniquement les actifs mourants, puisque
chuter de 95 % maximise le potentiel affiché. Le potentiel vient d'un ratio
MESURÉ ramené à une référence MESURÉE :

* **MVRV** (BTC/ETH) vers la **médiane de sa propre distribution historique**
  (Coin Metrics, depuis 2010 pour BTC). L'ancre 1,0 utilisée jusqu'au 31/08
  était le prix réalisé — la zone de creux de cycle, pas la valeur centrale.
* **MC/TVL** vers la médiane des protocoles de la **même catégorie DeFiLlama**.
  Mesuré le 30/09 : la dispersion intra-catégorie va de MAD_log 0,88 à 2,88,
  au-delà de la limite de comparabilité (0,40). Ce chemin s'abstient donc en
  pratique — avec son motif — plutôt que d'inventer une décote.

**Sans ancrage, le potentiel est ABSENT** et l'actif n'est pas recommandable
de façon autonome. Il reste analysé, affiché, et son motif est nommé.

DEUX SOURCES DE FINANCEMENT
---------------------------
* **CAS A — apport externe.** Contrefactuel : trésorerie à 5 %/an.
* **CAS B — réallocation.** Contrefactuel : conserver la position vendue.
  Deux coûts, dominance mesurable exigée, et jamais la vente d'un actif
  « intouchable » du profil d'Omar.

Le LLM n'a aucune autorité ici. Aucune fonction de ce module ne lit une sortie
de modèle de langage.
"""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any, Optional

from src.analytics import evidence as EV
from src.utils.portfolio_loader import load_config
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CFG: dict[str, Any] = (load_config("thresholds") or {}).get(
    "opportunity_engine", {}) or {}


# ── accès configuration (aucune constante métier en dur) ──────────────────

def _cfg(path: str, default: Any = None) -> Any:
    """Lit ``opportunity_engine.<a>.<b>…`` avec repli explicite."""
    node: Any = _CFG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and math.isfinite(f) else None


# ── chaînes publiées : décimale FRANÇAISE (RT-14) ─────────────────────────
# Les motifs du moteur sont rendus tels quels dans le mail et sur Telegram
# (« rendement requis 21.7% », « MVRV 1.56 » le 01/10). Les nombres restent des
# float ; seules les CHAÎNES publiées sont localisées, à la sortie du moteur.
_DECIMALE_EN = re.compile(r"(?<=\d)\.(?=\d)")
_CLES_TEXTE = {"reason", "basis", "motif", "note", "benchmark_basis", "reasons",
               "reserves", "calcul", "statut"}


def _localiser(node: Any, cle: str = "") -> Any:
    if isinstance(node, str):
        return _DECIMALE_EN.sub(",", node) if cle in _CLES_TEXTE else node
    if isinstance(node, list):
        return [_localiser(x, cle) for x in node]
    if isinstance(node, dict):
        return {k: _localiser(v, k) for k, v in node.items()}
    return node


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class MissingBusinessParameter(RuntimeError):
    """Un ancrage métier manque dans ``thresholds.yaml``.

    Les ancrages — rendement requis de référence, contrefactuel trésorerie,
    coûts, bandes de taille — n'ont **aucun repli numérique dans le code**. Un
    repli silencieux serait un seuil en dur déguisé : on préfère l'abstention
    bruyante.
    """


def _anchor(path: str) -> float:
    """Ancrage métier OBLIGATOIRE — aucune valeur par défaut."""
    v = _num(_cfg(path, None))
    if v is None:
        raise MissingBusinessParameter(
            f"opportunity_engine.{path} absent de thresholds.yaml — "
            "le moteur refuse de décider avec un seuil non déclaré")
    return v


def _band(path: str) -> list[float]:
    """Bande de taille OBLIGATOIRE ``[bas, haut]`` — aucune valeur par défaut."""
    rng = _cfg(path, None)
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        raise MissingBusinessParameter(
            f"opportunity_engine.{path} absent ou mal formé — bande [bas, haut] "
            "attendue")
    lo, hi = _num(rng[0]), _num(rng[1])
    if lo is None or hi is None or lo <= 0 or hi < lo:
        raise MissingBusinessParameter(
            f"opportunity_engine.{path} invalide ({rng!r})")
    return [lo, hi]


def core_sizing_assets() -> set[str]:
    """Actifs dimensionnés avec les bandes CŒUR (décision d'Omar : BTC, ETH).

    TAO et LINK restent « intouchables » dans le profil (jamais d'allègement
    proposé par seuil) mais suivent les bandes SATELLITES : « l'exposition doit
    rester inférieure à celle que j'accepterais sur BTC/ETH » (Omar).
    """
    raw = _cfg("core_sizing_assets", None)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise MissingBusinessParameter(
            "opportunity_engine.core_sizing_assets absent — la liste des actifs "
            "aux bandes cœur est une décision métier")
    return {str(x).upper() for x in raw}


# ── 1 · mesure de l'univers (références MÉDIANES, pas de niveau choisi) ────

def measure_universe(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Médianes de l'univers du jour, servant de références de modulation.

    Chaque facteur de modulation compare un actif à ces médianes : l'actif
    médian obtient M = 1 sur les quatre axes, et le paramètre de configuration
    exprime alors directement le rendement requis DE CET ACTIF MÉDIAN.
    """
    def _vals(key: str, *, strict: bool = True) -> list[float]:
        out = []
        for c in candidates:
            v = _num(c.get(key)) if isinstance(c, dict) else None
            if v is None:
                continue
            if (strict and v > 0) or (not strict and v >= 0):
                out.append(v)
        return out

    vols = _vals("daily_vol_pct")
    # Liquidité = volume ABSOLU en dollars (audit zero-trust 01/10). Le ratio
    # volume/capitalisation classait BTC (2 %) parmi les actifs les MOINS
    # liquides du portefeuille, derrière un alt médian (~6 %) : il mesure la
    # rotation du capital, pas la capacité à exécuter un ordre. Le coût de
    # sortie dépend de la profondeur absolue. Le ratio reste dans le gate de
    # viabilité, où il signale un marché ÉTEINT.
    liqs = _vals("volume_24h_usd")
    dils = _vals("dilution_remaining_pct", strict=False)
    covs = _vals("coverage")
    return {
        "vol_median": median(vols) if vols else None,
        "liquidity_median": median(liqs) if liqs else None,
        "dilution_median": median(dils) if dils else None,
        "coverage_median": median(covs) if covs else None,
        "n_vol": len(vols), "n_liq": len(liqs), "n_dil": len(dils),
        "n_cov": len(covs),
    }


# ── 2 · gate de viabilité (binaire, éliminatoire pour un RENFORT) ─────────

def check_viability(c: dict[str, Any]) -> dict[str, Any]:
    """Un actif est-il seulement recommandable au RENFORT ? Binaire.

    Une donnée ABSENTE ne fait jamais échouer le gate — elle pénalise ailleurs
    (couverture, facteurs de modulation). Le gate ne sanctionne que ce qui est
    MESURÉ et mauvais. Un échec bloque un renfort ; il ne déclenche JAMAIS une
    vente (« aucun seuil ne déclenche une vente », Omar, 01/10).

    Audit zero-trust (01/10) : la fraîcheur et le « prix suspect » ont quitté
    ce gate. Le premier lisait une clé (``data_freshness.age_days``) que rien
    ne produit ; le second qualifiait l'ATH, que le moteur n'utilise pas — et
    une donnée douteuse n'est pas une propriété de l'actif. Le 30/09, JASMY
    recevait « ALLÉGER — prix ou historique suspect » : une vente motivée par
    une anomalie de données.
    """
    reasons: list[str] = []
    unknown: list[str] = []

    liq = _num(c.get("volume_to_mcap"))
    liq_min = _anchor("viability.min_volume_to_mcap")
    if liq is None:
        unknown.append("liquidité")
    elif liq < liq_min:
        reasons.append(
            f"liquidité {liq * 100:.2f}% de la capitalisation "
            f"(plancher {liq_min * 100:.2f}%)")

    dil = _num(c.get("dilution_remaining_pct"))
    dil_max = _anchor("viability.max_dilution_remaining_pct")
    if dil is None:
        unknown.append("dilution restante")
    elif dil > dil_max:
        reasons.append(
            f"dilution restante {dil:.0f}% (plafond {dil_max:.0f}%)")

    # Activité de développement : uniquement si MESURÉE et nulle.
    if c.get("dev_active") is False:
        reasons.append("aucun commit mesuré depuis plus de 90 jours")
    elif c.get("dev_active") is None:
        unknown.append("activité de développement")

    return {"viable": not reasons, "reasons": reasons, "unknown": unknown}


# ── 3 · multiplicateur de risque (grandeurs MESURÉES, bornes = garde-fous) ─

def modulation_multiplier(
    c: dict[str, Any], universe: dict[str, Any]
) -> dict[str, Any]:
    """Multiplicateur ``M`` de la prime de risque, et son détail.

    Quatre facteurs, tous relatifs à la médiane de l'univers ::

        f_vol      σ_i / σ_médiane                     — risque idiosyncratique
        f_liq      liquidité_médiane / liquidité_i     — coût de sortie
        f_dil      dilution restante mesurée           — pression vendeuse
        f_cov      couverture_médiane / couverture_i   — incertitude de mesure

    Un facteur > 1 exige davantage de potentiel. **Une donnée absente pousse
    toujours le facteur à son plafond** : ignorer une donnée manquante
    reviendrait à récompenser l'ignorance.
    """
    detail: dict[str, Any] = {}

    def _bornes(nom: str) -> tuple[float, float]:
        return _anchor(f"modulation.{nom}.min"), _anchor(f"modulation.{nom}.max")

    # ── volatilité relative
    vol, vol_med = _num(c.get("daily_vol_pct")), _num(universe.get("vol_median"))
    lo, hi = _bornes("volatility")
    if vol is not None and vol_med and vol_med > 0:
        f_vol = _clamp(vol / vol_med, lo, hi)
        detail["volatility"] = {"value": round(f_vol, 3), "measured": True,
                                "basis": f"σ {vol:.2f}%/j vs médiane {vol_med:.2f}%/j"}
    else:
        f_vol = hi
        detail["volatility"] = {"value": round(f_vol, 3), "measured": False,
                                "basis": "volatilité non mesurée — pénalisée au plafond"}

    # ── liquidité relative (inversée : moins liquide ⇒ facteur plus grand),
    # sur le volume 24 h ABSOLU — voir ``measure_universe``.
    liq, liq_med = _num(c.get("volume_24h_usd")), _num(universe.get("liquidity_median"))
    lo, hi = _bornes("liquidity")
    if liq and liq > 0 and liq_med and liq_med > 0:
        f_liq = _clamp(liq_med / liq, lo, hi)
        detail["liquidity"] = {"value": round(f_liq, 3), "measured": True,
                               "basis": f"volume 24 h {liq / 1e6:,.1f} M$ vs médiane "
                                        f"{liq_med / 1e6:,.1f} M$".replace(",", " ").replace(".", ",")}
    else:
        f_liq = hi
        detail["liquidity"] = {"value": round(f_liq, 3), "measured": False,
                               "basis": "liquidité non mesurée — pénalisée au plafond"}

    # ── dilution restante (mesurée via max_supply), relative à la médiane.
    # Le +1 évite la division par zéro d'un actif à offre figée tout en
    # conservant la monotonie.
    dil, dil_med = (_num(c.get("dilution_remaining_pct")),
                    _num(universe.get("dilution_median")))
    lo, hi = _bornes("dilution")
    if dil is not None and dil_med is not None:
        f_dil = _clamp((1.0 + max(dil, 0.0) / 100.0)
                       / (1.0 + max(dil_med, 0.0) / 100.0), lo, hi)
        detail["dilution"] = {"value": round(f_dil, 3), "measured": True,
                              "basis": f"dilution restante {dil:.0f}% vs "
                                       f"médiane {dil_med:.0f}%"}
    else:
        f_dil = hi
        detail["dilution"] = {"value": round(f_dil, 3), "measured": False,
                              "basis": "dilution restante inconnue — pénalisée "
                                       "au plafond"}

    # ── couverture des données, relative à la médiane (inversée)
    cov, cov_med = _num(c.get("coverage")), _num(universe.get("coverage_median"))
    lo, hi = _bornes("coverage")
    if cov is not None and cov > 0 and cov_med and cov_med > 0:
        f_cov = _clamp(cov_med / cov, lo, hi)
        detail["coverage"] = {"value": round(f_cov, 3), "measured": True,
                              "basis": f"couverture {cov * 100:.0f}% vs médiane "
                                       f"{cov_med * 100:.0f}%"}
    else:
        f_cov = hi
        detail["coverage"] = {"value": round(f_cov, 3), "measured": False,
                              "basis": "couverture inconnue — pénalisée au plafond"}

    m_lo, m_hi = _bornes("multiplier")
    raw = f_vol * f_liq * f_dil * f_cov
    return {"multiplier": round(_clamp(raw, m_lo, m_hi), 3),
            "raw": round(raw, 3), "clamped": not (m_lo <= raw <= m_hi),
            "factors": detail}


# ── 4 · rendement requis (arithmétique pure, INDÉPENDANT de la taille) ────

def required_return(
    c: dict[str, Any],
    universe: dict[str, Any],
    *,
    funding_case: str = "A",
    funded_by: Optional[dict[str, Any]] = None,
    horizon_days: Optional[float] = None,
) -> dict[str, Any]:
    """Rendement requis sur l'horizon de référence, et sa décomposition.

    ``R_requis = coûts + contrefactuel·H + prime_référence × M``

    Aucun terme ne dépend de ``Δw`` : c'est ce qui interdit structurellement
    qu'une taille plus grande rende une thèse acceptable.
    """
    h_days = _num(horizon_days) or _anchor("reference_horizon_days")
    h_years = h_days / 365.0
    case = str(funding_case).upper()

    cost_one = _anchor("round_trip_cost_pct")
    # CAS B : deux gestes (vendre puis acheter), donc deux coûts.
    n_legs = 2.0 if case == "B" else 1.0
    costs = cost_one * n_legs

    # ── prime de référence, DÉRIVÉE du rendement requis déclaré pour l'actif
    # MÉDIAN de l'univers (CAS A, horizon de référence), puis appliquée telle
    # quelle aux autres cas : les coûts supplémentaires d'une réallocation
    # s'AJOUTENT au lieu d'être absorbés par une prime réduite.
    ref_total = _anchor("required_return_reference_pct")
    bench_annual = _anchor("cash_benchmark_annual_pct")
    ref_h = _anchor("reference_horizon_days")
    prime_ref = max(ref_total - cost_one - bench_annual * (ref_h / 365.0), 0.0)

    mod = modulation_multiplier(c, universe)
    prime = prime_ref * mod["multiplier"]

    if case == "B":
        # Le contrefactuel n'est PAS la trésorerie : c'est CONSERVER la
        # position vendue. Son rendement attendu n'étant pas estimable, on
        # n'en invente aucun — la comparaison se fait entre POTENTIELS MESURÉS
        # des deux actifs, et la prime devient un DIFFÉRENTIEL.
        bench = 0.0
        bench_basis = ("conserver la position vendue — comparaison "
                       "différentielle de potentiels mesurés")
        mod_sell = (modulation_multiplier(funded_by, universe)
                    if funded_by else None)
        if mod_sell is not None:
            prime = prime_ref * (mod["multiplier"] - mod_sell["multiplier"])
    else:
        bench = bench_annual * h_years
        bench_basis = f"trésorerie {bench_annual:.1f}%/an sur {h_days:.0f} j"

    # ── CLASSE DE PREUVE DU RENDEMENT REQUIS. Les coûts sont une HYPOTHÈSE
    # déclarée (ni frais, ni spread, ni slippage ne sont instrumentés) :
    # ``R_requis`` est donc publié comme SCÉNARIO tant qu'ils ne le sont pas.
    costs_mesures = bool(_cfg("cost_is_measured", False))

    total = costs + bench + prime

    # ── Le canal court terme ne peut pas être plus facile que la conviction :
    # le contrefactuel prorata temporis abaissait la barre de 4,6 points à
    # 30 jours. Un horizon court doit offrir AUTANT qu'une conviction.
    plancher_ref = None
    if h_days < ref_h:
        plancher_ref = cost_one * n_legs + (
            bench_annual * (ref_h / 365.0) if case != "B" else 0.0) + prime
        total = max(total, plancher_ref)

    return {
        "required_pct": round(total, 2),
        "reference_floor_pct": (round(plancher_ref, 2)
                                if plancher_ref is not None else None),
        "components": {
            "costs_pct": round(costs, 2),
            "costs_measured": costs_mesures,
            "costs_legs": int(n_legs),
            "benchmark_pct": round(bench, 2),
            "benchmark_basis": bench_basis,
            "risk_premium_pct": round(prime, 2),
            "risk_premium_reference_pct": round(prime_ref, 2),
            "required_return_reference_pct": ref_total,
            "differential": case == "B",
        },
        "preuve": EV.piece(
            round(total, 2),
            EV.CALCUL if costs_mesures else EV.SCENARIO,
            "configuration + mesures de l'univers",
            "coûts + contrefactuel·H + prime_référence × M",
            reserves=([] if costs_mesures else [
                f"les coûts ({costs:.2f} pt) sont une HYPOTHÈSE déclarée — ni "
                "frais, ni spread, ni slippage ne sont mesurés ; le point mort "
                "n'est donc pas exact",
                (f"sensibilité : {costs / total * 100:.0f} % du rendement requis"
                 if total > 0 else "sensibilité : non définie (requis nul)")])),
        "costs_measured": costs_mesures,
        "multiplier": mod["multiplier"],
        "multiplier_detail": mod["factors"],
        "multiplier_clamped": mod["clamped"],
        "horizon_days": round(h_days, 0),
        "funding_case": case,
        "funded_by": (funded_by or {}).get("asset") if funded_by else None,
    }


# ── 5 · potentiel MESURÉ (jamais l'ATH, jamais un quantile de rendement) ──

def _absent(basis: str, source: str, *, anchor: Optional[str] = None,
            reserves: Optional[list[str]] = None,
            comparabilite: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out = {"available": False, "potential_pct": None,
           "potential_conservative_pct": None, "anchor": anchor,
           "basis": basis,
           "preuve": EV.piece(None, EV.SCENARIO, source,
                              "aucun calcul défendable", reserves=reserves)}
    if comparabilite is not None:
        out["comparabilite"] = comparabilite
    return out


def measured_potential(
    c: dict[str, Any], universe: dict[str, Any]
) -> dict[str, Any]:
    """Potentiel implicite d'un ancrage de valorisation MESURÉ, et sa PREUVE.

    Deux grandeurs par ancrage :

    * ``potential_pct`` — réévaluation impliquée par un retour à l'ancre
      CENTRALE (médiane) ;
    * ``potential_conservative_pct`` — la même vers l'ancre PRUDENTE (premier
      quartile). C'est elle qui ouvre la bande exceptionnelle : une thèse qui
      tient même si la réévaluation s'arrête au premier quartile est plus
      robuste qu'une thèse qui exige la médiane.

    Que la valorisation REVIENNE vers son ancre en 12 mois est la prémisse du
    moteur (thèse « value ») : documentée ailleurs, ni vérifiable ni réfutable
    avec ces données. Les ENTRÉES sont mesurées ; la prémisse ne l'est pas.
    """
    cov = _num(c.get("coverage"))
    cov_min = _anchor("preuve.couverture_min")
    if cov is None:
        return _absent("couverture des sources non mesurée — impossible de "
                       "qualifier la fiabilité des grandeurs dérivées",
                       "couverture inconnue")
    if cov < cov_min:
        return _absent(f"couverture des sources {cov * 100:.0f}% sous le "
                       f"plancher de {cov_min * 100:.0f}% — les grandeurs "
                       "dérivées seraient des hypothèses, pas des mesures",
                       "couverture insuffisante")

    # ── ancrage 1 : MVRV vers la médiane de SA distribution historique
    mvrv = _num(c.get("mvrv"))
    if mvrv is not None and mvrv > 0:
        if c.get("mvrv_stale") is True:
            return _absent(
                f"MVRV {mvrv:.2f} périmé (source en retard) — une mesure "
                "ancienne n'est pas un fait du jour", "Coin Metrics (MVRV)",
                anchor="mvrv")
        hist = c.get("mvrv_history") if isinstance(c.get("mvrv_history"), dict) else {}
        med, q25 = _num(hist.get("median")), _num(hist.get("q25"))
        h_min, h_max = _num(hist.get("min")), _num(hist.get("max"))
        if not (med and q25 and h_min and h_max):
            return _absent(
                f"MVRV {mvrv:.2f} mesuré mais historique indisponible — sans "
                "distribution observée, il n'existe pas d'ancre défendable",
                "Coin Metrics (MVRV)", anchor="mvrv")
        # ── PLAUSIBILITÉ = support historiquement OBSERVÉ (l'analogue exact du
        # support des pairs). Une valeur jamais vue n'est pas une occasion :
        # c'est une mesure fausse (un MVRV à 0,01 donnait +300 % « calculés »).
        if not (h_min <= mvrv <= h_max):
            return _absent(
                f"MVRV {mvrv:.3f} hors du support historiquement observé "
                f"[{h_min:.2f} ; {h_max:.2f}] — donnée invraisemblable, pas une "
                "décote", "Coin Metrics (MVRV)", anchor="mvrv",
                reserves=[f"MVRV {mvrv:.3f} ∉ [{h_min:.2f} ; {h_max:.2f}]"])
        pot = (med / mvrv - 1.0) * 100.0
        cons = (q25 / mvrv - 1.0) * 100.0
        periode = f"{hist.get('first', '?')} → {hist.get('last', '?')}"
        return {
            "available": True,
            "potential_pct": round(pot, 2),
            "potential_conservative_pct": round(cons, 2),
            "anchor": "mvrv",
            "anchor_value": med,
            "anchor_conservative_value": q25,
            "basis": (f"MVRV {mvrv:.2f} → médiane historique {med:.2f} "
                      f"(Q1 {q25:.2f}, {hist.get('n', '?')} jours, {periode})"),
            "preuve": EV.piece(
                round(pot, 2), EV.CALCUL, "Coin Metrics (MVRV) + historique",
                f"médiane historique {med:.2f} / MVRV {mvrv:.2f} − 1",
                reserves=["l'ancre est MESURÉE (médiane de la distribution "
                          "observée) ; qu'elle soit rejointe en 12 mois est la "
                          "prémisse du moteur, non démontrable"]),
        }

    # ── ancrage 2 : MC/TVL contre les protocoles de la même catégorie
    mc_tvl = _num(c.get("mc_tvl"))
    peers = c.get("mc_tvl_peers") if isinstance(c.get("mc_tvl_peers"), dict) else {}
    ratios = [r for r in (peers.get("ratios") or []) if _num(r) and _num(r) > 0]
    if mc_tvl is None or mc_tvl <= 0:
        return _absent("aucun ancrage de valorisation mesurable pour cet actif",
                       "aucune source de valorisation")
    if not ratios:
        return _absent(
            f"MC/TVL {mc_tvl:.2f}× mesuré, mais aucun pair de même catégorie "
            "DeFiLlama — pas de référence", "DeFiLlama (MC/TVL)",
            anchor="mc_tvl_peer")
    disp = EV.dispersion_log(ratios)
    cat = peers.get("category") or "?"
    comp = EV.comparabilite(
        mc_tvl, disp,
        k_aberrant=_anchor("preuve.k_aberrant"),
        mad_max=_anchor("preuve.mad_log_max"),
        n_min=int(_anchor("valuation.peer_min_sample")))
    comp["categorie"] = cat
    if not comp["comparable"]:
        return _absent(f"catégorie « {cat} » : {comp['motif']}",
                       "DeFiLlama (MC/TVL)", anchor="mc_tvl_peer",
                       reserves=[comp["motif"]], comparabilite=comp)
    pot = (math.exp(comp["ecart_log"]) - 1.0) * 100.0
    cons = (float(disp["q25"]) / mc_tvl - 1.0) * 100.0
    med = disp["mediane"]
    return {
        "available": True,
        "potential_pct": round(pot, 2),
        "potential_conservative_pct": round(cons, 2),
        "anchor": "mc_tvl_peer",
        "anchor_value": med,
        "anchor_conservative_value": disp["q25"],
        "basis": (f"MC/TVL {mc_tvl:.1f}× vs médiane de la catégorie « {cat} » "
                  f"{med:.1f}× ({disp['n']} protocoles, {comp['motif']})"),
        "comparabilite": comp,
        "preuve": EV.piece(
            round(pot, 2), EV.CALCUL, "DeFiLlama (MC/TVL) + pairs de catégorie",
            f"ln(médiane {med:.1f}× / ratio {mc_tvl:.1f}×) sur {disp['n']} pairs",
            reserves=[f"plafond de crédibilité {comp['potentiel_max_pct']:+.0f}% "
                      f"(borne : {comp['borne_liante']})"]),
    }


# ── 6 · dominance (CAS B uniquement) ──────────────────────────────────────

def check_dominance(
    buy: dict[str, Any], sell: dict[str, Any]
) -> dict[str, Any]:
    """``buy`` domine-t-il ``sell`` sur des grandeurs MESURABLES ?

    Contrainte d'Omar : « le système ne doit jamais vendre X uniquement pour
    financer Y ». ``buy`` doit être au moins aussi bon partout et strictement
    meilleur quelque part. Un axe mesuré d'un seul côté ne peut pas établir la
    dominance.
    """
    axes: list[dict[str, Any]] = []

    def _axis(name: str, a: Optional[float], b: Optional[float],
              lower_is_better: bool, label: str) -> None:
        if a is None or b is None:
            axes.append({"axis": name, "verdict": "non mesuré", "label": label})
            return
        better = (a < b) if lower_is_better else (a > b)
        worse = (a > b) if lower_is_better else (a < b)
        axes.append({"axis": name,
                     "verdict": "meilleur" if better else ("moins bon" if worse
                                                           else "égal"),
                     "label": label, "buy": a, "sell": b})

    _axis("dilution", _num(buy.get("dilution_remaining_pct")),
          _num(sell.get("dilution_remaining_pct")), True, "dilution restante")
    _axis("liquidite", _num(buy.get("volume_24h_usd")),
          _num(sell.get("volume_24h_usd")), False, "liquidité (volume 24 h)")
    _axis("couverture", _num(buy.get("coverage")),
          _num(sell.get("coverage")), False, "couverture des données")

    worse = [a for a in axes if a["verdict"] == "moins bon"]
    better = [a for a in axes if a["verdict"] == "meilleur"]
    ok = not worse and bool(better)
    if ok:
        note = ("domine sur " + ", ".join(a["label"] for a in better))
    elif worse:
        note = ("ne domine pas — moins bon sur "
                + ", ".join(a["label"] for a in worse))
    else:
        note = "aucun avantage mesurable démontré sur la position vendue"
    return {"dominates": ok, "axes": axes, "note": note}


# ── 7 · bandes de taille (encadrent le RENFORT — aucun plafond d'exposition) ─

def size_bands(c: dict[str, Any]) -> dict[str, Any]:
    """Bandes de taille du RENFORT, selon le palier de l'actif.

    Décisions d'Omar : BTC/ETH 5–10 % (exceptionnel 15–20 %) ; tous les autres
    actifs, TAO et LINK compris, 3–5 % (exceptionnel 5–8 %). Les bandes sont
    des ENTRÉES — jamais la sortie d'une optimisation (Kelly déguisé).

    Le poids détenu et le poids ajusté du bêta sont PUBLIÉS pour informer de
    la concentration ; ils ne plafonnent rien (« pas de hard cap
    d'exposition », Omar, 01/10).
    """
    sym = str(c.get("asset") or "").upper()
    tier = "core" if sym in core_sizing_assets() else "satellite"
    normal = _band(f"sizing_bands.{tier}.normal")
    exceptional = _band(f"sizing_bands.{tier}.exceptional")
    w = _num(c.get("weight_pct"))
    beta = _num(c.get("beta_portfolio"))
    return {
        "tier": tier,
        "normal": normal,
        "exceptional": exceptional,
        "weight_pct": round(w, 2) if w is not None else None,
        "beta_portfolio": beta,
        "effective_weight_pct": (round(w * beta, 2)
                                 if (w is not None and beta is not None) else None),
    }


# ── 8 · décision (entièrement déterministe) ───────────────────────────────

def decide(
    c: dict[str, Any],
    universe: dict[str, Any],
    *,
    funding_case: str = "A",
    funded_by: Optional[dict[str, Any]] = None,
    ptf_value_usd: Optional[float] = None,
    horizon_days: Optional[float] = None,
) -> dict[str, Any]:
    """Décision déterministe et reproductible pour un changement d'allocation.

    Conditions conjonctives ::

        C1 VIABILITÉ      l'actif franchit le gate (renfort uniquement)
        C3 CALCULABILITÉ  le rendement requis est calculable
        C0 PREUVE         le potentiel n'est pas un scénario (classe D)
        C4 POTENTIEL      un potentiel MESURÉ couvre le rendement requis
        C5 DOMINANCE      (CAS B) l'achat domine la vente sur du mesurable
        C6 HORIZON        un horizon court ne déclenche jamais

    Bande de taille : normale si le potentiel CENTRAL couvre le requis ;
    exceptionnelle si le potentiel PRUDENT (premier quartile) le couvre aussi.
    """
    asset = str(c.get("asset") or "?").upper()
    out: dict[str, Any] = {"asset": asset, "action": "AUCUNE ACTION",
                           "decided": False,
                           "funding_case": str(funding_case).upper()}
    try:
        return _localiser(_decide_inner(
            c, universe, out, funding_case=funding_case, funded_by=funded_by,
            ptf_value_usd=ptf_value_usd, horizon_days=horizon_days))
    except MissingBusinessParameter as exc:
        logger.error("Moteur d'opportunité désarmé : %s", exc)
        out["condition_failed"] = "CONFIG"
        out["reason"] = f"paramètre métier manquant — {exc}"
        return out


def _decide_inner(
    c: dict[str, Any],
    universe: dict[str, Any],
    out: dict[str, Any],
    *,
    funding_case: str = "A",
    funded_by: Optional[dict[str, Any]] = None,
    ptf_value_usd: Optional[float] = None,
    horizon_days: Optional[float] = None,
) -> dict[str, Any]:
    """Corps de ``decide`` — voir sa docstring."""
    case = str(funding_case).upper()

    # ── C1
    viab = check_viability(c)
    out["viability"] = viab
    if not viab["viable"]:
        out["condition_failed"] = "C1"
        out["reason"] = "actif non viable — " + " · ".join(viab["reasons"])
        return out

    bands = size_bands(c)
    out["size_bands"] = bands

    # ── C3
    req = required_return(c, universe, funding_case=case,
                          funded_by=funded_by, horizon_days=horizon_days)
    out["required"] = req
    r_req = _num(req.get("required_pct"))
    if r_req is None:
        out["condition_failed"] = "C3"
        out["reason"] = "rendement requis non calculable"
        return out

    # ── C0 · STANDARD DE PREUVE. Une pièce de classe D ne déclenche JAMAIS
    # une recommandation, quelle que soit l'amplitude annoncée.
    pot = measured_potential(c, universe)
    out["potential"] = pot
    out["preuve"] = pot.get("preuve")
    if not EV.suffisante(pot.get("preuve") or {}):
        faible = EV.classe_faible(pot.get("preuve") or {}) or {}
        out["condition_failed"] = "C0"
        out["reason"] = (
            f"preuve insuffisante ({EV.libelle(faible.get('classe') or EV.SCENARIO)}) "
            f"— {pot.get('basis')}. Rendement requis {r_req:.1f}% : une "
            "hypothèse ne déclenche pas une recommandation, quelle que soit "
            "son amplitude")
        return out

    p = _num(pot.get("potential_pct"))
    p_cons = _num(pot.get("potential_conservative_pct"))
    if p is None:
        out["condition_failed"] = "C4"
        out["reason"] = (f"rendement requis {r_req:.1f}% — potentiel non "
                         "chiffrable")
        return out
    p_eff, p_cons_eff = p, p_cons
    p_label = f"potentiel mesuré {p:.1f}%"

    if case == "B":
        if not funded_by:
            out["condition_failed"] = "C5"
            out["reason"] = "réallocation sans position source déclarée"
            return out
        sold = str(funded_by.get("asset") or "?").upper()
        pot_sell = measured_potential(funded_by, universe)
        out["potential_sold"] = pot_sell
        out["preuve_vendue"] = pot_sell.get("preuve")
        if not EV.suffisante(pot_sell.get("preuve") or {}) or not pot_sell.get("available"):
            out["condition_failed"] = "C0"
            out["reason"] = (
                f"preuve insuffisante sur {sold} ({pot_sell.get('basis')}) — "
                "vendre sur une hypothèse est exclu")
            return out
        p_sell = _num(pot_sell.get("potential_pct")) or 0.0
        p_eff = p - p_sell
        p_cons_eff = (p_cons - p_sell) if p_cons is not None else None
        p_label = (f"différentiel de potentiel {p_eff:+.1f}% "
                   f"({p:.1f}% − {p_sell:.1f}% sur {sold})")

    # ── C4
    if p_eff < r_req:
        out["condition_failed"] = "C4"
        out["reason"] = (f"{p_label} sous le rendement requis {r_req:.1f}% "
                         f"({pot['basis']})")
        return out

    # ── C5 — dominance mesurable (CAS B uniquement)
    if case == "B":
        dom = check_dominance(c, funded_by or {})
        out["dominance"] = dom
        if not dom["dominates"]:
            out["condition_failed"] = "C5"
            out["reason"] = (
                f"vendre {str((funded_by or {}).get('asset') or '?').upper()} "
                f"non justifié — {dom['note']}")
            return out

    # ── C6 — le canal court terme ne déclenche pas. Le potentiel provient d'un
    # ratio de valorisation qui ne varie pas significativement en 30 jours :
    # une reco « tactique » réutiliserait exactement le même potentiel qu'une
    # conviction, avec une horloge plus courte.
    ref_h = _anchor("reference_horizon_days")
    h = _num(req.get("horizon_days")) or ref_h
    out["channel"] = "tactique" if h < ref_h else "conviction"
    if h < ref_h:
        out["condition_failed"] = "C6"
        out["reason"] = (
            f"analyse à {h:.0f} jours : le potentiel dérive d'un ratio de "
            "valorisation qui ne varie pas sur cet horizon. Aucune grandeur "
            "propre au court terme n'est mesurable — le canal informe le "
            "timing et le risque de parcours, il ne déclenche pas")
        return out

    # ── taille : la bande exceptionnelle exige que la thèse tienne à l'ancre
    # PRUDENTE (premier quartile). Aucun multiplicateur choisi : la robustesse
    # se lit sur la distribution observée elle-même.
    exceptional = p_cons_eff is not None and p_cons_eff >= r_req
    band = bands["exceptional"] if exceptional else bands["normal"]
    band_label = "exceptionnelle" if exceptional else "normale"
    size_pct = band[0]
    ptf = _num(ptf_value_usd)
    out["size"] = {
        "pct": size_pct, "band": band, "band_label": band_label,
        "kind": bands["tier"],
        "usd": round(ptf * size_pct / 100.0, 0) if ptf else None,
        "usd_band": ([round(ptf * band[0] / 100.0, 0),
                      round(ptf * band[1] / 100.0, 0)] if ptf else None),
        "basis": ("le potentiel tient aussi à l'ancre prudente (premier "
                  "quartile)" if exceptional else
                  "le potentiel central couvre le requis ; l'ancre prudente "
                  "ne le couvre pas" if p_cons_eff is not None else
                  "ancre prudente indisponible"),
    }
    out["excess_pct"] = round(p_eff - r_req, 2)
    out["potential_effective_pct"] = round(p_eff, 2)
    out["potential_conservative_effective_pct"] = (
        round(p_cons_eff, 2) if p_cons_eff is not None else None)

    out["action"] = "RENFORCER"
    out["decided"] = True
    out["reason"] = (
        f"{p_label} ≥ requis {r_req:.1f}% "
        f"(coûts {req['components']['costs_pct']:.1f} + contrefactuel "
        f"{req['components']['benchmark_pct']:.1f} + prime "
        f"{req['components']['risk_premium_pct']:.1f}) · {pot['basis']}")
    return out


def decide_universe(
    candidates: list[dict[str, Any]],
    *,
    ptf_value_usd: Optional[float] = None,
    funding_case: str = "A",
    horizon_days: Optional[float] = None,
    exit_signals: Optional[dict[str, dict[str, Any]]] = None,
    intouchables: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Évalue tout l'univers et renvoie décisions + motifs d'abstention.

    Aucun quota, aucun « top N », aucun relâchement adaptatif : zéro est une
    sortie normale, et le candidat le plus proche est publié pour que
    l'abstention soit informative plutôt que muette.

    Les ARBITRAGES (CAS B) sont évalués sur toutes les paires (achat, vente)
    où la position vendue est détenue et n'est pas « intouchable » (profil
    d'Omar : BTC, ETH, TAO, LINK ne sont jamais vendus pour en financer un
    autre). Ils n'apparaissent que s'ils franchissent toutes les conditions.
    """
    universe = measure_universe(candidates)
    decisions = [
        decide(c, universe, funding_case=funding_case,
               ptf_value_usd=ptf_value_usd, horizon_days=horizon_days)
        for c in candidates
    ]
    exit_signals = exit_signals or {}
    reductions = [
        decide_reduce(c, exit_signal=exit_signals.get(
            str(c.get("asset") or "").upper()))
        for c in candidates
    ]

    # ── CAS B : uniquement entre actifs dont le potentiel est MESURÉ des deux
    # côtés (sinon C0 assuré — inutile de l'évaluer 800 fois).
    intouchables = {s.upper() for s in (intouchables or set())}
    mesurables = [c for c, d in zip(candidates, decisions)
                  if (d.get("potential") or {}).get("available")]
    arbitrages: list[dict[str, Any]] = []
    for buy in mesurables:
        for sell in mesurables:
            sb = str(buy.get("asset") or "").upper()
            ss = str(sell.get("asset") or "").upper()
            if sb == ss or ss in intouchables:
                continue
            if not ((_num(sell.get("weight_pct")) or 0.0) > 0):
                continue
            d = decide(buy, universe, funding_case="B", funded_by=sell,
                       ptf_value_usd=ptf_value_usd, horizon_days=horizon_days)
            if d.get("decided"):
                d["sell_asset"] = ss
                arbitrages.append(d)

    firm = [d for d in decisions if d.get("decided")]
    rejected = [d for d in decisions if not d.get("decided")]

    def _gap(d: dict[str, Any]) -> float:
        p = _num((d.get("potential") or {}).get("potential_pct"))
        r = _num((d.get("required") or {}).get("required_pct"))
        return (r - p) if (p is not None and r is not None) else float("inf")

    # Le « candidat le plus proche » se classe d'abord par ÉCART chiffré, puis
    # par sévérité de la condition en échec : une journée où tout échoue faute
    # de preuve publie quand même un motif.
    _severite = {"C4": 1, "C3": 2, "C5": 2, "C6": 2, "C0": 4, "C1": 5,
                 "CONFIG": 6}

    def _rang(d: dict[str, Any]) -> tuple[float, int, str]:
        g = _gap(d)
        return (0.0 if g != float("inf") else 1.0,
                _severite.get(d.get("condition_failed") or "", 9),
                str(d.get("asset") or ""))

    closest = (min(rejected, key=lambda d: (_rang(d), _gap(d)))
               if rejected else None)
    n_mesurable = len(mesurables)
    return {
        "available": True,
        "universe": universe,
        "decisions": decisions,
        "firm": firm,
        "rejected": rejected,
        "closest_miss": closest,
        "count": len(firm),
        "potential_measurable": n_mesurable,
        "arbitrages": arbitrages,
        "reductions": reductions,
        "reduce_firm": [r for r in reductions if r.get("decided")],
        "signalements": _signalements(decisions),
    }


def _signalements(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Écarts importants mais non corroborables — à examiner à la main.

    Ni recommandations, ni rejets silencieux : un actif hors du support de ses
    pairs a la même signature qu'une mesure cassée ou qu'une vraie asymétrie,
    et rien dans ces données ne les distingue.
    """
    out: list[dict[str, Any]] = []
    for d in decisions:
        if d.get("decided") or d.get("condition_failed") != "C0":
            continue
        comp = (d.get("potential") or {}).get("comparabilite") or {}
        ecart, borne = _num(comp.get("ecart_log")), _num(comp.get("borne_log"))
        if ecart is None or borne is None or ecart <= 0 or ecart <= borne:
            continue
        out.append({
            "asset": d.get("asset"),
            "ecart_implique_pct": round((math.exp(ecart) - 1) * 100, 1),
            "borne_credible_pct": round((math.exp(borne) - 1) * 100, 1),
            "motif": comp.get("motif"),
            "statut": ("asymétrie possible mais NON corroborable avec les "
                       "données disponibles — le moteur ne tranche pas, "
                       "examen manuel"),
        })
    return out


# ── 9 · réduction (ALLÉGER) — uniquement sur les règles d'Omar ────────────

def decide_reduce(
    c: dict[str, Any],
    universe: Optional[dict[str, Any]] = None,
    *,
    exit_signal: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Faut-il ALLÉGER ? Jamais par seuil de poids ni par anomalie de donnée.

    « Aucun seuil ne déclenche une vente ; l'allègement dépend de la
    conviction » (Omar, 01/10). Le seul déclencheur est donc le radar de
    sortie déterministe, qui applique les règles de PRISE DE PROFIT du profil
    d'Omar (paliers +80 % / ×2 / ×3 et offload sur pump, sur les SATELLITES
    seulement — aucun allègement proposé sur BTC/ETH/TAO/LINK, Omar 02/10).
    Une perte de viabilité ferme le renfort ; elle ne déclenche pas de vente.

    Audit zero-trust (01/10) — la version précédente vendait sur trois motifs
    contraires à ces règles : dépassement d'un plafond de poids (BTC à 38,5 %
    le 30/09), viabilité « perdue » sur une anomalie de donnée (JASMY), et un
    radar lu sous une clé ``triggered`` que le radar ne produit pas — le seul
    déclencheur légitime était donc mort.
    """
    asset = str(c.get("asset") or "?").upper()
    out: dict[str, Any] = {"asset": asset, "action": "AUCUNE ACTION",
                           "decided": False, "trigger": None}
    if isinstance(exit_signal, dict) and exit_signal.get("reason"):
        out.update(action="ALLÉGER", decided=True, trigger="radar_sortie",
                   reason=(f"{exit_signal.get('reason')} → "
                           f"{exit_signal.get('action') or 'alléger une tranche'}"),
                   radar=exit_signal)
        return out
    out["reason"] = "aucune règle de prise de profit déclenchée"
    return out
