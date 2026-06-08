"""Analyse de corrélation entre les positions du portefeuille.

Calcule la corrélation des rendements quotidiens (sur ~30j) entre les tokens
détenus, puis identifie les "clusters" de positions qui bougent ensemble. Le
but : quantifier le risque de concentration croisé que l'analyse par secteur
seule ne capture pas.

Exemple : si TAO, FET et RENDER sont corrélés à 0.85+, un choc sur le narratif
AI les fait tous chuter ensemble — même s'ils sont dans des "lignes" séparées
du portefeuille. Cette analyse rend ce risque explicite et chiffré.

Pur Python (pas de numpy requis), basé sur les séries de prix CoinGecko déjà
récupérées par le pipeline. Dégradation gracieuse si trop peu de données.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Seuil au-dessus duquel deux actifs sont considérés "fortement corrélés".
_HIGH_CORR = 0.7
# Minimum de points de prix communs pour un calcul fiable.
_MIN_POINTS = 10


def _daily_returns(prices: list[float]) -> list[float]:
    """Convertit une série de prix en rendements quotidiens."""
    returns = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev:
            returns.append((prices[i] - prev) / prev)
    return returns


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    """Coefficient de corrélation de Pearson entre deux séries de même longueur."""
    n = min(len(xs), len(ys))
    if n < _MIN_POINTS:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def compute_correlation_analysis(
    price_series: dict[str, list[float]],
    position_values: dict[str, float],
) -> dict[str, Any]:
    """Calcule la matrice de corrélation et les clusters de risque du portefeuille.

    Args:
        price_series: ``{symbol: [prix_j-30, ..., prix_j]}`` (séries alignées).
        position_values: ``{symbol: valeur_usd}`` pour pondérer l'exposition.

    Returns:
        Dict ``{available, pairs_high_corr, clusters, concentration_reading,
        max_cluster_pct}``.
    """
    # Rendements pour chaque actif ayant assez de données.
    returns: dict[str, list[float]] = {}
    for sym, prices in price_series.items():
        if prices and len(prices) >= _MIN_POINTS + 1:
            r = _daily_returns(prices)
            if len(r) >= _MIN_POINTS:
                returns[sym] = r

    if len(returns) < 2:
        return {
            "available": False,
            "reason": "pas assez de séries de prix pour corréler les positions",
        }

    symbols = sorted(returns.keys())
    total_value = sum(position_values.get(s, 0) for s in symbols) or 1.0

    # Paires fortement corrélées.
    high_pairs: list[dict[str, Any]] = []
    # Graphe d'adjacence pour le clustering (union-find léger).
    parent = {s: s for s in symbols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            corr = _pearson(returns[a], returns[b])
            if corr is None:
                continue
            if corr >= _HIGH_CORR:
                high_pairs.append({"a": a, "b": b, "corr": round(corr, 2)})
                union(a, b)

    high_pairs.sort(key=lambda p: p["corr"], reverse=True)

    # Construit les clusters (groupes connectés).
    cluster_map: dict[str, list[str]] = {}
    for s in symbols:
        root = find(s)
        cluster_map.setdefault(root, []).append(s)

    clusters: list[dict[str, Any]] = []
    for members in cluster_map.values():
        if len(members) < 2:
            continue  # un actif seul n'est pas un cluster de risque
        cluster_value = sum(position_values.get(s, 0) for s in members)
        pct = round(cluster_value / total_value * 100, 1)
        clusters.append(
            {
                "members": sorted(members),
                "ptf_pct": pct,
                "value_usd": round(cluster_value, 2),
            }
        )
    clusters.sort(key=lambda c: c["ptf_pct"], reverse=True)

    max_cluster_pct = clusters[0]["ptf_pct"] if clusters else 0.0

    # Lecture textuelle synthétique (factuelle, l'IA peut l'enrichir).
    if clusters:
        top = clusters[0]
        reading = (
            f"{len(top['members'])} positions corrélées ({', '.join(top['members'])}) "
            f"pèsent {top['ptf_pct']}% du portefeuille et tendent à bouger ensemble. "
            f"Un choc commun les impacterait simultanément — risque de concentration "
            f"réel au-delà de la diversification apparente."
        )
    else:
        reading = (
            "aucun cluster de forte corrélation détecté entre les positions · "
            "diversification effective sur la fenêtre analysée."
        )

    return {
        "available": True,
        "pairs_high_corr": high_pairs[:10],
        "clusters": clusters,
        "max_cluster_pct": max_cluster_pct,
        "concentration_reading": reading,
        "threshold": _HIGH_CORR,
    }


# --- Corrélations macro ↔ crypto (BTC vs gold/DXY/S&P500/VIX/10Y) -----------

_MACRO_LABELS = {
    "gold": "Or",
    "dxy": "DXY (dollar)",
    "sp500": "S&P 500",
    "vix": "VIX",
    "us_10y": "US 10Y",
}


def _align_returns(
    a_dated: dict[str, float], b_dated: dict[str, float]
) -> tuple[list[float], list[float]]:
    """Aligne deux séries datées sur leurs dates communes -> rendements."""
    common = sorted(set(a_dated) & set(b_dated))
    if len(common) < _MIN_POINTS + 1:
        return [], []
    a_vals = [a_dated[d] for d in common]
    b_vals = [b_dated[d] for d in common]
    return _daily_returns(a_vals), _daily_returns(b_vals)


def _corr_label(key: str, corr: float) -> str:
    """Lecture qualitative d'une corrélation BTC ↔ macro (régime de marché)."""
    strength = "forte" if abs(corr) >= 0.6 else "modérée" if abs(corr) >= 0.3 else "faible"
    sign = "positive" if corr > 0 else "négative" if corr < 0 else "nulle"
    return f"{strength} {sign}"


def compute_macro_crypto_correlation(
    crypto_dated: dict[str, float],
    macro_series: dict[str, dict[str, float]],
    window: int = 30,
) -> dict[str, Any]:
    """Corrélations glissantes des rendements BTC ↔ indicateurs macro.

    Args:
        crypto_dated: ``{date: close}`` du BTC (référence crypto).
        macro_series: ``{key: {date: value}}`` (gold/dxy/sp500/vix/us_10y).
        window: fenêtre cible (jours). L'alignement par date fait foi.

    Returns:
        Dict ``{available, window, correlations: [{key, label, corr, reading}],
        regime_hint}``. ``available=False`` si données insuffisantes.
    """
    if not crypto_dated or not macro_series:
        return {"available": False, "reason": "séries macro ou BTC absentes"}

    correlations: list[dict[str, Any]] = []
    for key, dated in macro_series.items():
        ra, rb = _align_returns(crypto_dated, dated)
        if not ra or not rb:
            continue
        # Borne à la fenêtre demandée (derniers points).
        n = min(len(ra), len(rb), window)
        corr = _pearson(ra[-n:], rb[-n:])
        if corr is None:
            continue
        correlations.append(
            {
                "key": key,
                "label": _MACRO_LABELS.get(key, key),
                "corr": round(corr, 2),
                "reading": _corr_label(key, corr),
            }
        )

    if not correlations:
        return {"available": False, "reason": "alignement insuffisant"}

    correlations.sort(key=lambda c: abs(c["corr"]), reverse=True)

    # Indice de régime : DXY (négatif = risk-on sain) et S&P (positif = couplé actions).
    dxy = next((c["corr"] for c in correlations if c["key"] == "dxy"), None)
    spx = next((c["corr"] for c in correlations if c["key"] == "sp500"), None)
    gold = next((c["corr"] for c in correlations if c["key"] == "gold"), None)
    hints = []
    if dxy is not None:
        hints.append(
            "BTC découplé du dollar (risk-on)" if dxy < -0.3
            else "BTC sensible au dollar" if dxy > 0.3
            else "lien BTC/dollar ténu"
        )
    if spx is not None and spx > 0.4:
        hints.append("fort couplage aux actions US (bêta marché élevé)")
    if gold is not None and gold > 0.4:
        hints.append("co-mouvement avec l'or (narratif réserve de valeur)")
    regime_hint = " · ".join(hints) if hints else "régime de corrélation neutre"

    return {
        "available": True,
        "window": window,
        "correlations": correlations,
        "regime_hint": regime_hint,
    }


def _beta(asset_returns: list[float], macro_returns: list[float]) -> Optional[float]:
    """Bêta (pente de régression) des rendements actif sur rendements macro.

    beta = cov(actif, macro) / var(macro). Interprétation : variation attendue
    (%) de l'actif pour +1% du facteur macro. None si données insuffisantes.
    """
    n = min(len(asset_returns), len(macro_returns))
    if n < _MIN_POINTS:
        return None
    a, m = asset_returns[-n:], macro_returns[-n:]
    mean_a = sum(a) / n
    mean_m = sum(m) / n
    cov = sum((x - mean_a) * (y - mean_m) for x, y in zip(a, m))
    var_m = sum((y - mean_m) ** 2 for y in m)
    if var_m <= 0:
        return None
    return cov / var_m


def compute_per_asset_macro_beta(
    asset_dated: dict[str, dict[str, float]],
    macro_series: dict[str, dict[str, float]],
    window: int = 30,
    factors: tuple[str, ...] = ("dxy", "sp500", "vix"),
    min_abs_corr: float = 0.25,
    beta_cap: float = 3.0,
) -> dict[str, Any]:
    """Bêtas et corrélations par actif vs facteurs macro (DXY/S&P/VIX).

    Pour chaque actif (clôtures datées) et chaque facteur macro, calcule le bêta
    (sensibilité) et la corrélation des rendements quotidiens alignés par date.
    Sert à remplir le champ ``beta_dxy`` des thèses et à chiffrer le lien
    macro → crypto par position (recommandation A9).

    Robustesse (v12) : un bêta n'est conservé QUE si la corrélation est
    statistiquement significative (``|corr| >= min_abs_corr``) ET dans une plage
    plausible (``|beta| <= beta_cap``). Sinon il est écarté (None) : sur 30
    jours, un crypto très volatil régressé sur un DXY quasi plat produit des
    pentes aberrantes (β = cov/var, var_macro ~0) qui ne veulent rien dire.

    Args:
        asset_dated: ``{symbol: {date: close}}`` (clôtures datées par actif).
        macro_series: ``{factor: {date: value}}``.
        window: fenêtre cible (jours).
        factors: facteurs macro à traiter (clés de ``macro_series``).
        min_abs_corr: corrélation minimale pour juger le bêta exploitable.
        beta_cap: borne absolue de plausibilité du bêta.

    Returns:
        Dict ``{available, window, by_asset: {sym: {factor: {beta, corr}}}}``.
    """
    if not asset_dated or not macro_series:
        return {"available": False, "by_asset": {}}

    by_asset: dict[str, dict[str, Any]] = {}
    for sym, dated in asset_dated.items():
        if not dated:
            continue
        per_factor: dict[str, Any] = {}
        for fac in factors:
            macro_dated = macro_series.get(fac)
            if not macro_dated:
                continue
            ra, rm = _align_returns(dated, macro_dated)
            if not ra or not rm:
                continue
            n = min(len(ra), len(rm), window)
            corr = _pearson(ra[-n:], rm[-n:])
            beta = _beta(ra[-n:], rm[-n:])
            # Garde-fous v12 : corrélation significative + bêta plausible.
            if corr is None or abs(corr) < min_abs_corr:
                continue
            if beta is None or abs(beta) > beta_cap:
                continue
            per_factor[fac] = {"beta": round(beta, 2), "corr": round(corr, 2)}
        if per_factor:
            by_asset[sym] = per_factor

    return {
        "available": bool(by_asset),
        "window": window,
        "by_asset": by_asset,
    }
