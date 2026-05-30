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
