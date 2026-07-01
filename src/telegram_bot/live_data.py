"""Données de marché LIVE pour le bot Telegram (v18.1 — valeur ajoutée).

Le bot ne doit pas se limiter au dernier rapport figé : à la demande d'Omar, il
récupère des prix LIVE du marché (CoinGecko, mis en cache 5 min) pour :
  • valoriser le portefeuille à l'instant T (quantité × prix live) ;
  • donner un instantané marché (BTC/ETH, dominance, Fear & Greed) à jour ;
  • raisonner sur des scénarios chiffrés réels (« si BTC −15% »).

Principe de NON-INVENTION : toute panne renvoie ``{"available": False}`` et le
bot retombe alors sur les valeurs baseline du dernier rapport — jamais d'invention.
Le snapshot n'est calculé QUE lorsqu'Omar écrit (le bot ne sonde pas l'API à
vide toutes les 5 min) : load_full_context n'est appelé que s'il y a un message.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_live_portfolio_snapshot() -> dict[str, Any]:
    """Valorise le portefeuille au prix live (quantité × prix CoinGecko).

    Returns:
        Dict ``{available, total_value_usd, positions: [{symbol, value_usd,
        price, change_24h, change_7d, tier, weight_pct, priced_live}],
        positions_priced_live, positions_total}`` ou ``{available: False}``.
    """
    try:
        from src.data_sources import coingecko
        from src.utils.portfolio_loader import load_portfolio

        pf = load_portfolio()
        positions = pf.get("portfolio") or {}
        symbols = [s for s, i in positions.items()
                   if isinstance(i, dict) and i.get("role") != "cash_reserve"]
        if not symbols:
            return {"available": False, "reason": "portefeuille vide"}
        market = coingecko.get_market_data(symbols) or {}
        if not market:
            return {"available": False, "reason": "prix live indisponibles"}

        rows: list[dict[str, Any]] = []
        total = 0.0
        cost_basis_total = 0.0   # somme(qté × pru) des positions avec PRU + prix live
        pnl_usd_total = 0.0      # P&L latent total sur ces mêmes positions
        for sym, info in positions.items():
            if not isinstance(info, dict):
                continue
            m = market.get(sym) or {}
            price = m.get("price")
            qty = info.get("quantity")
            priced_live = False
            if price and qty is not None:
                try:
                    val = round(float(qty) * float(price), 2)
                    priced_live = True
                except (TypeError, ValueError):
                    val = float(info.get("value_usd") or 0)
                    price = None
            else:
                # Pas de prix live : repli sur la baseline (dernier snapshot connu).
                val = float(info.get("value_usd") or 0)
                price = None
            total += val
            # P&L latent vs PRU (v21) — uniquement si prix live ET pru>0.
            pru = info.get("pru")
            pnl_pct = pnl_usd = None
            try:
                pru_f = float(pru) if pru is not None else None
            except (TypeError, ValueError):
                pru_f = None
            if priced_live and pru_f and pru_f > 0:
                pnl_pct = round((float(price) - pru_f) / pru_f * 100, 1)
                pnl_usd = round((float(price) - pru_f) * float(qty), 2)
                cost_basis_total += pru_f * float(qty)
                pnl_usd_total += pnl_usd
            rows.append({
                "symbol": sym,
                "value_usd": round(val, 2),
                "price": price,
                "pru": pru_f,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "change_24h": m.get("change_24h"),
                "change_7d": m.get("change_7d"),
                "tier": info.get("tier"),
                "priced_live": priced_live,
            })

        for r in rows:
            r["weight_pct"] = round(r["value_usd"] / total * 100, 1) if total else None
        rows.sort(key=lambda r: r["value_usd"], reverse=True)
        priced = sum(1 for r in rows if r["priced_live"])
        return {
            "available": True,
            "total_value_usd": round(total, 2),
            "positions": rows,
            "positions_priced_live": priced,
            "positions_total": len(rows),
            "cost_basis_usd": round(cost_basis_total, 2) if cost_basis_total else None,
            "pnl_usd": round(pnl_usd_total, 2) if cost_basis_total else None,
            "pnl_pct": (round(pnl_usd_total / cost_basis_total * 100, 1)
                        if cost_basis_total else None),
            "note": ("Valorisation live (quantité × prix CoinGecko). Les positions "
                     "sans prix live retombent sur la baseline du dernier snapshot. "
                     "P&L latent = prix live vs PRU (coût moyen)."),
        }
    except Exception as exc:  # noqa: BLE001 — jamais bloquer le bot
        logger.info("Snapshot PTF live indisponible : %s", exc)
        return {"available": False, "reason": str(exc)}


def get_live_market_snapshot() -> dict[str, Any]:
    """Instantané marché live : BTC/ETH, dominance, cap globale, Fear & Greed.

    Returns:
        Dict ``{available, btc, eth, btc_dominance_pct, total_mcap_change_24h_pct,
        fear_greed, fear_greed_label}`` (champs absents si indisponibles).
    """
    out: dict[str, Any] = {"available": False}
    try:
        from src.data_sources import coingecko
        market = coingecko.get_market_data(["BTC", "ETH"]) or {}
        btc = market.get("BTC") or {}
        eth = market.get("ETH") or {}
        if btc.get("price"):
            out["btc"] = {"price": btc.get("price"),
                          "change_24h": btc.get("change_24h"),
                          "change_7d": btc.get("change_7d")}
            out["available"] = True
        if eth.get("price"):
            out["eth"] = {"price": eth.get("price"),
                          "change_24h": eth.get("change_24h"),
                          "change_7d": eth.get("change_7d")}
        glob = coingecko.get_global() or {}
        if glob.get("available"):
            out["btc_dominance_pct"] = glob.get("btc_dominance_pct")
            out["total_mcap_change_24h_pct"] = glob.get("market_cap_change_24h_pct")
            out["available"] = True
    except Exception as exc:  # noqa: BLE001
        logger.info("Snapshot marché live partiel : %s", exc)

    # Fear & Greed (source keyless, dégrade en silence).
    try:
        from src.data_sources import fear_greed
        fg = fear_greed.get_fear_greed()
        if isinstance(fg, dict) and fg.get("available"):
            out["fear_greed"] = fg.get("value")
            out["fear_greed_label"] = fg.get("classification") or fg.get("label")
            out["available"] = True
    except Exception as exc:  # noqa: BLE001
        logger.info("Fear & Greed live indisponible : %s", exc)

    return out


def get_price_anchors(symbols: tuple[str, ...] = ("BTC", "ETH")) -> dict[str, Any]:
    """Bornes de prix RÉELLES (prix courant + plus-bas/plus-haut 12 mois).

    Anti-hallucination (v21) : donne au bot des bornes factuelles pour vérifier
    toute affirmation de prix historique (ex. « le BTC a-t-il touché 59 500 $ ? »)
    au lieu d'inventer un chiffre. Source : CoinGecko (prix + OHLC 365 j). Dégrade
    en silence (``available=False``) — le bot dit alors qu'il n'a pas l'historique.

    Returns:
        ``{available, assets: {SYM: {now, low_12m, high_12m}}, note}``.
    """
    out: dict[str, Any] = {"available": False, "assets": {}}
    try:
        from src.data_sources import coingecko
        market = coingecko.get_market_data(list(symbols)) or {}
        for sym in symbols:
            entry: dict[str, Any] = {}
            now = (market.get(sym) or {}).get("price")
            if now:
                entry["now"] = round(float(now), 2)
            ohlc = coingecko.get_ohlc(sym, 365)
            if ohlc:
                lows = [c["low"] for c in ohlc if c.get("low")]
                highs = [c["high"] for c in ohlc if c.get("high")]
                if lows and highs:
                    entry["low_12m"] = round(min(lows), 2)
                    entry["high_12m"] = round(max(highs), 2)
            if entry:
                out["assets"][sym] = entry
        out["available"] = bool(out["assets"])
        if out["available"]:
            out["note"] = (
                "Bornes de prix RÉELLES (CoinGecko, OHLC 12 mois). Toute "
                "affirmation de prix/niveau historique DOIT être cohérente avec "
                "ces bornes ; sinon, ne pas l'affirmer (ex. si low_12m BTC = "
                "74000, le BTC n'a PAS touché 55000 sur 12 mois).")
    except Exception as exc:  # noqa: BLE001
        logger.info("Ancres de prix indisponibles : %s", exc)
    return out
