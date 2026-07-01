"""Valorisation fondamentale d'un actif (v22 P1 #3/#4/#5).

Comble l'asymétrie « BTC/ETH riches, alts aveugles » : donne à l'analyse des
ratios de valorisation RÉELS (pas juste le prix) à partir de données déjà
collectées (CoinGecko market + DeFiLlama TVL/fees) :

  • FDV / MC          : dilution future implicite (émissions à venir).
  • % en circulation  + dilution restante : pression vendeuse structurelle.
  • P/F (MC / frais annualisés)  & P/S (MC / revenus annualisés) : cher/pas cher
    FONDAMENTALEMENT (le protocole gagne-t-il de l'argent).
  • MC / TVL          : capitalisation vs fonds déposés (DeFi).

Pur Python, déterministe. ``{available: False}`` si rien de calculable (honnête).
"""

from __future__ import annotations

from typing import Any, Optional


def _num(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


def compute_valuation(
    market: dict[str, Any],
    tvl: Optional[dict[str, Any]] = None,
    fees: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Calcule les ratios de valorisation d'un actif.

    Args:
        market: entrée marché CoinGecko (market_cap, fully_diluted_valuation,
            circulating_supply, total_supply, max_supply).
        tvl: sortie ``defillama.get_protocol_tvl`` (optionnel).
        fees: sortie ``defillama.get_protocol_fees`` (optionnel).

    Returns:
        Dict ``{available, metrics: {...}, signals: [str], reading}``.
    """
    market = market or {}
    mc = _num(market.get("market_cap"))
    fdv = _num(market.get("fully_diluted_valuation"))
    circ = _num(market.get("circulating_supply"))
    max_s = _num(market.get("max_supply"))

    metrics: dict[str, Any] = {}
    signals: list[str] = []

    # FDV / MC : dilution future implicite.
    if mc and fdv and mc > 0:
        ratio = fdv / mc
        metrics["fdv_mc_ratio"] = round(ratio, 2)
        if ratio >= 2.0:
            signals.append(
                f"FDV/MC {ratio:.1f}× : forte dilution future (beaucoup de tokens "
                "non encore en circulation)"
            )

    # % en circulation + dilution restante.
    if circ and max_s and circ > 0 and max_s > 0:
        circ_pct = circ / max_s * 100
        dilution_remaining = (max_s - circ) / circ * 100
        metrics["circulating_pct"] = round(circ_pct, 1)
        metrics["dilution_remaining_pct"] = round(dilution_remaining, 1)
        if dilution_remaining >= 50:
            signals.append(
                f"{circ_pct:.0f}% en circulation : +{dilution_remaining:.0f}% "
                "d'émission restante (pression vendeuse structurelle)"
            )

    # P/F & P/S à partir des frais/revenus DeFiLlama.
    if fees and fees.get("available"):
        fa = _num(fees.get("fees_annualized"))
        ra = _num(fees.get("revenue_annualized"))
        if fa is not None:
            metrics["fees_annualized"] = fa
        if ra is not None:
            metrics["revenue_annualized"] = ra
        if mc and mc > 0:
            if fa and fa > 0:
                metrics["pf_ratio"] = round(mc / fa, 1)
            if ra and ra > 0:
                ps = mc / ra
                metrics["ps_ratio"] = round(ps, 1)
                if ps < 20:
                    signals.append(
                        f"P/S {ps:.0f} : valorisation raisonnable vs revenus réels"
                    )
                elif ps > 200:
                    signals.append(
                        f"P/S {ps:.0f} : très cher vs revenus générés"
                    )

    # MC / TVL (DeFi).
    if tvl and tvl.get("available"):
        tv = _num(tvl.get("tvl_usd"))
        if mc and tv and tv > 0:
            mctvl = mc / tv
            metrics["mc_tvl_ratio"] = round(mctvl, 2)
            if mctvl < 1.0:
                signals.append(
                    f"MC/TVL {mctvl:.2f} (< 1) : capitalisation inférieure aux "
                    "fonds déposés (potentiellement sous-évalué)"
                )

    if not metrics:
        return {"available": False}
    return {
        "available": True,
        "metrics": metrics,
        "signals": signals,
        "reading": " · ".join(signals) if signals else "valorisation dans les normes",
    }


def compute_tradability(
    volume_24h: Any, position_value_usd: Optional[float] = None
) -> dict[str, Any]:
    """Qualité d'exécution / liquidité d'un actif (v22 P3 #48).

    Garde-fou de TAILLE : une thèse peut être excellente, mais accumuler
    agressivement un microcap illiquide est dangereux (slippage). Dérivé du
    volume 24h (déjà collecté) et, si fourni, du poids de la position vs ce volume.
    """
    v = _num(volume_24h)
    if v is None or v <= 0:
        return {"available": False}
    if v < 1_000_000:
        liquidity = "faible"
        reading = "volume 24h < 1 M$ : microcap illiquide, dimensionner petit (slippage)"
    elif v < 10_000_000:
        liquidity = "modérée"
        reading = "volume 24h modéré (1-10 M$) : exécuter en plusieurs fois"
    else:
        liquidity = "bonne"
        reading = "volume 24h > 10 M$ : liquidité suffisante pour agir"
    out: dict[str, Any] = {
        "available": True,
        "volume_24h": round(v, 0),
        "liquidity": liquidity,
        "reading": reading,
    }
    pv = _num(position_value_usd)
    if pv and v:
        out["position_vs_volume_pct"] = round(pv / v * 100, 3)
    return out
