"""AUTORITÉ UNIQUE du formatage numérique français (v32, findings 5.6 / 2.2 / 3.7).

Avant v32, CINQ implémentations concurrentes formataient un prix :
``reporting/email_html._fmt_price`` (correcte), ``analytics/key_levels._fmt_usd``,
``analytics/asset_plan._fmt_usd``, ``analytics/liquidation_zones._fmt_usd`` et
``tracking/prediction_scoring._fmt``. Les quatre dernières partageaient deux
défauts, visibles en production :

1. **Précision fixe à 4 décimales sous 1 $.** Un actif à 0,001446 $ s'affichait
   « 0,0014 $ » — deux chiffres significatifs PERDUS sur le stop d'une position
   réelle (RSR, mails des 24/08 matin et hebdo). Pire, un ATR de 0,000045 $
   s'affichait « 0.0000 $/j » : le nombre montré au lecteur était ZÉRO.
2. **``key_levels`` ne localisait pas** cette branche : trois de ses quatre
   branches appliquaient ``.replace(".", ",")``, la quatrième non. D'où, dans le
   même mail du soir, « ATR 1,8 % (≈1 413 $/j) » pour BTC et
   « ATR 3,5 % (≈0.2100 $/j) » pour INJ.

Règle unique retenue (celle de ``_fmt_price``, qui était juste) :
  - ≥ 1000       : 0 décimale, milliers en espace fine insécable  (64 489 $)
  - ≥ 1, < 1000  : 2 décimales                                    (8,98 $)
  - ≥ 0,01, < 1  : 4 décimales                                    (0,0526 $)
  - < 0,01       : 4 chiffres SIGNIFICATIFS, zéros de fin retirés,
                   jamais de notation scientifique                (0,001446 $)
"""

from __future__ import annotations

import math
from typing import Any, Optional

NNBSP = "\u202f"  # espace fine insécable : milliers ET avant le « $ »


def fr_number(anglo: str) -> str:
    """« 64,489.00 » → « 64 489,00 » (milliers U+202F, décimale virgule)."""
    return anglo.replace(",", NNBSP).replace(".", ",")


def to_float(value: Any) -> Optional[float]:
    """Convertit en float fini, ou None. N'accepte ni NaN ni ±inf."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fr_decimals(v: float) -> int:
    """Nombre de décimales à afficher pour ``v`` selon la règle unique."""
    a = abs(v)
    if a >= 1000:
        return 0
    if a >= 1:
        return 2
    if a >= 0.01:
        return 4
    if a == 0:
        return 2
    # 4 chiffres significatifs : 0,00523 → 5 décimales ; 1e-8 → 11.
    return min(-math.floor(math.log10(a)) + 3, 18)


def arrondi_niveau(value: Any) -> Optional[float]:
    """Arrondit un NIVEAU de prix sans jamais perdre de chiffres significatifs.

    RED TEAM (RT-12) — ``round(x, 6)`` était appliqué à tous les niveaux
    (invalidation, cibles, paliers DCA, scénarios). Sur un actif à 1,66e-4 $
    — 1000SATS, position réelle du portefeuille — le pas d'arrondi vaut 1e-6,
    soit **0,6 % du prix** : deux niveaux distants de moins de 0,6 % se
    confondent. Vérifié par fuzzing : sous 1e-5 $, la cible 30 j devient égale
    au prix, l'invalidation aussi, et le R:R se calcule sur un risque nul.

    Règle : au moins 6 décimales (identique à l'existant pour tout prix
    ≥ 0,01 $, donc aucune régression) ET au moins 6 chiffres significatifs.
    """
    v = to_float(value)
    if v is None:
        return None
    a = abs(v)
    if a == 0:
        return 0.0
    return round(v, max(6, -math.floor(math.log10(a)) + 5))


def fr_num(value: Any, *, default: str = "—", thin: bool = True) -> str:
    """Nombre au format français, précision adaptée à sa magnitude (sans « $ »).

    Args:
        value: nombre (ou chaîne convertible).
        default: rendu si la valeur est absente/non finie.
        thin: ``True`` → milliers en espace fine insécable (rendu mail) ;
            ``False`` → espace ordinaire. Les modules ``analytics`` utilisent
            historiquement l'espace ordinaire et des tests l'ancrent : v32 ne
            corrige QUE la précision décimale, pas cette convention (aucun
            défaut démontré sur le séparateur, invisible à l'écran).
    """
    v = to_float(value)
    if v is None:
        return default
    d = fr_decimals(v)
    s = f"{v:,.{d}f}"
    if d > 4:                       # micro-prix : on retire les zéros de fin
        s = s.rstrip("0").rstrip(".")
    out = fr_number(s)
    return out if thin else out.replace(NNBSP, " ")


def fr_usd(value: Any, *, default: str = "—") -> str:
    """Prix en dollars, format français unique. ``0,001446 $``, ``64 489 $``."""
    v = to_float(value)
    if v is None:
        return default
    return fr_num(v) + NNBSP + "$"
