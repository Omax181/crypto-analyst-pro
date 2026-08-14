"""Horizon — SPEC V31 §4.1 (I5, I6, I11, I22, I24).

L'horizon est une ENTRÉE du calcul, pas une étiquette. Toutes les grandeurs
d'un contrat en dérivent : fenêtre de niveaux, échelle de volatilité, règle de
cible, règle d'invalidation, date d'expiration, fenêtre de scoring, profondeur
d'historique exigée.

Il est déterminé DÉTERMINISTIQUEMENT (jamais par le LLM — I12/I19/I36) et n'est
JAMAIS sélectionné ni modifié pour satisfaire une condition de viabilité (I24).

Les DEUX horizons sont actifs. ``CYCLE`` n'est pas un horizon de
recommandation : il vit dans ``AssetContext`` (contexte, non falsifiable).

HISTORIQUE — POURQUOI POSITION AVAIT ÉTÉ DÉSACTIVÉ, ET POURQUOI C'ÉTAIT FAUX.
La première intégration V31 déclarait POSITION désactivé au motif que sa
profondeur exigée (365 bougies journalières) « n'était pas fournie par le
pipeline ». C'était une limite AUTO-INFLIGÉE : ``pipeline.market`` demandait
130 clôtures parce que 130 suffisait au SWING, et rien d'autre ne l'empêchait
d'en demander davantage.

La conséquence mesurée était grave et exactement inverse de la stratégie :
la règle de précédence envoie vers POSITION tout ce qui est fondamental
dominant, or POSITION était muet. Un actif « sous PRU + drawdown profond +
MVRV bas » — le meilleur setup d'accumulation du profil — ne pouvait produire
AUCUN contrat, tandis qu'un rebond purement technique en produisait un. Plus
la thèse d'accumulation était solide, moins le système pouvait agir.

Le correctif porte sur la CAUSE : le pipeline fournit désormais la profondeur
que POSITION exige. Si un actif jeune ne dispose pas de cet historique, le
gate de profondeur (§4.2) produit un NON_EVALUABLE CHIFFRÉ — « n bougies < 365
exigées » — au lieu d'un refus catégorique et muet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Horizon(str, Enum):
    SWING = "SWING"
    POSITION = "POSITION"


@dataclass(frozen=True)
class HorizonSpec:
    """Jeu de paramètres COHÉRENT d'un horizon. Aucun emprunt inter-horizons."""

    horizon: Horizon
    enabled: bool
    days: int                  # durée de vie du contrat = fenêtre de scoring
    sigma_window_bars: int     # fenêtre d'estimation de sigma
    level_window_bars: int     # fenêtre de calcul des niveaux
    longest_ma_period: int     # plus longue moyenne mobile du jeu de niveaux
    fib_window_bars: int       # fenêtre du swing pour les retracements
    allows_core: bool          # autorisé sur un actif cœur
    allows_satellite: bool
    disabled_reason: Optional[str] = None

    @property
    def depth_min(self) -> int:
        """Profondeur minimale DÉRIVÉE des estimateurs employés (SPEC §4.2).

        depth_min = max(fenêtre sigma, plus longue MM, fenêtre Fibonacci).
        Aucune valeur choisie : elle découle de la composition du jeu de niveaux.
        """
        return max(self.sigma_window_bars, self.longest_ma_period,
                   self.fib_window_bars)


# SWING — 7 à 30 jours. Jeu de niveaux : pivots, MM20, MM50, Bollinger(20),
# Fibonacci 90 j, seuils ronds. MM100/MM200 EXCLUES : ancrer une thèse à 30
# jours sur une moyenne à 200 jours mélange deux horizons (I6).
SWING_SPEC = HorizonSpec(
    horizon=Horizon.SWING,
    enabled=True,
    days=30,
    sigma_window_bars=30,
    level_window_bars=90,
    longest_ma_period=50,
    fib_window_bars=90,
    allows_core=False,
    allows_satellite=True,
)

# POSITION — 6 mois. Jeu de niveaux : pivots, MM20, MM200, Bollinger(20),
# Fibonacci 365 j, seuils ronds. Le swing de référence (365 j) vaut DEUX FOIS
# l'horizon du contrat (180 j) : un retracement se mesure sur un mouvement plus
# long que la position qu'on en tire, jamais sur un mouvement plus court.
# depth_min en découle : max(180, 200, 365) = 365 bougies journalières.
POSITION_SPEC = HorizonSpec(
    horizon=Horizon.POSITION,
    enabled=True,
    days=180,
    sigma_window_bars=180,
    level_window_bars=365,
    longest_ma_period=200,
    fib_window_bars=365,
    allows_core=True,
    allows_satellite=True,
)

SPECS: dict[Horizon, HorizonSpec] = {
    Horizon.SWING: SWING_SPEC,
    Horizon.POSITION: POSITION_SPEC,
}


def spec_for(horizon: Horizon) -> HorizonSpec:
    return SPECS[horizon]


@dataclass(frozen=True)
class HorizonDecision:
    """Résultat de la détermination. ``horizon`` None => aucun contrat possible."""
    horizon: Optional[Horizon]
    spec: Optional[HorizonSpec]
    reason: str

    @property
    def emittable(self) -> bool:
        return self.horizon is not None and self.spec is not None \
            and self.spec.enabled


def determine(
    *,
    asset: str,
    is_core: bool,
    tier: Optional[int],
    fundamental_weight: float,
    catalyst_weight: float,
    technical_struct_weight: float,
) -> HorizonDecision:
    """Précédence STRICTE de la SPEC §4.1. Aucune entrée LLM.

    1. Classe d'actif : cœur -> POSITION uniquement.
    2. Composition du score : fondamental dominant -> POSITION ;
       catalyseur / structure technique dominant -> SWING.
    3. Tier de valeur : Tier 4 -> aucun contrat.
    4. Égalité de poids -> POSITION (règle la plus contraignante).

    La règle 1 est cohérente avec le profil : le cœur se RENFORCE (l'univers de
    directions ne contient que ``LONG_INCREASE`` côté pipeline), il ne se
    tranche pas sur trente jours.
    """
    sym = (asset or "?").upper()

    # Les motifs sont LUS PAR LE LECTEUR (mail, Telegram) : ils sont rédigés en
    # français, sans flèche ni jargon de code — un motif illisible équivaut à un
    # rejet muet.

    def _decide(horizon: Horizon, spec: HorizonSpec, why: str) -> HorizonDecision:
        """Un horizon désactivé le DIT dans son motif, sans le masquer."""
        reason = f"{sym} : {why}"
        if not spec.enabled and spec.disabled_reason:
            reason += f" ; {spec.disabled_reason}"
        return HorizonDecision(horizon, spec, reason)

    # 1 — classe d'actif
    if is_core:
        return _decide(Horizon.POSITION, POSITION_SPEC,
                       "actif cœur, seul l'horizon POSITION est autorisé")

    # 3 — tier 4 (poussière) : exclusion absolue, quelle que soit la suite
    if tier is not None and tier >= 4:
        return HorizonDecision(
            None, None,
            f"{sym} : tier 4 (valeur résiduelle), aucune recommandation")

    # 2 / 4 — composition du score
    technical_side = max(float(catalyst_weight or 0.0),
                         float(technical_struct_weight or 0.0))
    fundamental = float(fundamental_weight or 0.0)
    if fundamental > technical_side:
        return _decide(Horizon.POSITION, POSITION_SPEC,
                       "poids fondamental dominant, ce qui impose l'horizon "
                       "POSITION")
    if technical_side > fundamental:
        return _decide(Horizon.SWING, SWING_SPEC,
                       "catalyseur ou structure technique dominant, "
                       "horizon SWING")
    # 4 — égalité : la règle la plus contraignante. Cas d'un actif SANS AUCUN
    # signal déterministe (tous les poids à zéro) : l'horizon long s'applique,
    # et c'est alors la profondeur d'historique puis la viabilité qui tranchent.
    return _decide(Horizon.POSITION, POSITION_SPEC,
                   "aucun poids dominant, l'horizon le plus prudent (POSITION) "
                   "s'applique")


def determine_from_scoring(asset: str, is_core: bool, tier: Optional[int],
                           signal_scoring: dict[str, Any]) -> HorizonDecision:
    """Adaptateur : lit les poids DÉTERMINISTES de ``signal_scoring``.

    N'accepte JAMAIS la valeur recopiée par le LLM (cause racine R6) : les poids
    proviennent du calcul Python, pas du payload.
    """
    signals = (signal_scoring or {}).get("signals") or []
    weights = {"fundamental_lt": 0.0, "catalyst": 0.0, "technical_struct": 0.0}
    for s in signals:
        if not isinstance(s, dict):
            continue
        cat = str(s.get("category") or "")
        if cat in weights:
            try:
                weights[cat] += float(s.get("weight") or 0.0)
            except (TypeError, ValueError):
                continue
    return determine(
        asset=asset, is_core=is_core, tier=tier,
        fundamental_weight=weights["fundamental_lt"],
        catalyst_weight=weights["catalyst"],
        technical_struct_weight=weights["technical_struct"],
    )
