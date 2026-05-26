"""Résolution du tier effectif d'un actif et du seuil de signaux associé.

Le refactor introduit un **Tier 0** (BTC, ETH) distinct, et redéfinit les
tiers par ``value_usd`` plutôt que par le champ statique du portfolio :
- Tier 0 : BTC, ETH
- Tier 1 : value_usd > 50
- Tier 2 : 10 <= value_usd <= 50
- Tier 3 : 1 <= value_usd < 10
- Tier 4 : value_usd < 1 (poussières)
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_SIGNAL_TH = _TH["signal_thresholds"]

_TIER0 = {"BTC", "ETH"}


def resolve_tier(symbol: str, value_usd: float | None) -> int:
    """Détermine le tier effectif (0-4) d'un actif.

    Args:
        symbol: ticker.
        value_usd: valeur détenue en USD (``None`` traité comme 0).

    Returns:
        Tier entier de 0 à 4.
    """
    if symbol in _TIER0:
        return 0
    v = value_usd or 0.0
    if v > 50:
        return 1
    if v >= 10:
        return 2
    if v >= 1:
        return 3
    return 4


def _tier_key(tier: int) -> str:
    """Mappe un tier entier vers la clé de config ``signal_thresholds``."""
    return {0: "btc_eth", 1: "tier_1", 2: "tier_2", 3: "tier_3", 4: "tier_4"}[tier]


def min_signals_for_firm_reco(tier: int) -> int:
    """Nombre de signaux convergents requis pour une reco ferme à ce tier."""
    return int(_SIGNAL_TH[_tier_key(tier)]["firm_reco_min_signals"])


def spike_threshold_pct(tier: int) -> float | None:
    """Seuil de spike (%) pour les poussières (Tier 4), sinon ``None``."""
    cfg = _SIGNAL_TH[_tier_key(tier)]
    return cfg.get("spike_threshold_pct")
