"""Prix de marché temps réel via l'API publique Yahoo Finance (chart v8).

Deux usages :

1. **Macro temps réel** (``get_macro_quotes``) : Gold, Brent, WTI, DXY (vrai
   indice ICE), indices actions, FX. Remplace les séries FRED qui sont soit
   en retard de plusieurs jours (matières premières), soit carrément gelées
   (la série or ``GOLDAMGBD228NLBM`` s'arrête en 2020). Yahoo donne le dernier
   prix de marché, mis à jour en continu.

2. **Cross-check crypto** (``get_crypto_quotes``) : prix d'une sélection de
   cryptos via les paires ``-USD`` de Yahoo, pour recouper CoinGecko et
   détecter une valeur aberrante (écart > seuil → on signale au lieu d'afficher
   un chiffre faux en confiance).

Aucune clé requise. Endpoint : ``query1.finance.yahoo.com/v8/finance/chart``.
Dégradation gracieuse totale : toute erreur renvoie ``None`` / dict vide, le
code appelant retombe alors sur FRED (macro) ou n'affiche pas de cross-check.

NOTE sandbox : Yahoo renvoie 403 derrière certains proxys (dont le sandbox de
dev). Sur GitHub Actions (IP US) l'endpoint répond normalement. Le parsing est
testé indépendamment du réseau via des payloads mockés.
"""

from __future__ import annotations

from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; crypto-analyst-pro/2.0)"}
_TTL = 600  # 10 min : suffisant pour de la macro, économise les appels.

# Tickers Yahoo pour les actifs macro. Clé = nom interne (aligné sur les clés
# déjà utilisées dans _macro_context), valeur = symbole Yahoo Finance.
#   ^GSPC = S&P 500 · ^IXIC = Nasdaq Composite · ^VIX = VIX
#   DX-Y.NYB = US Dollar Index (ICE, le "vrai" DXY ~99-105)
#   GC=F = Gold futures · BZ=F = Brent · CL=F = WTI
#   ^TNX = US 10Y (en %×10 chez Yahoo → divisé par 10) · ^TYX non utilisé
#   EURUSD=X · JPY=X (USD/JPY)
_MACRO_TICKERS: dict[str, str] = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "vix": "^VIX",
    "dxy_ice": "DX-Y.NYB",
    "gold": "GC=F",
    "brent": "BZ=F",
    "wti": "CL=F",
    "us_10y": "^TNX",
    "eur_usd": "EURUSD=X",
    "usd_jpy": "JPY=X",
}

# Cryptos cross-checkées via Yahoo (paires -USD). On se limite aux grosses
# positions : c'est là qu'un prix faux fait le plus de dégâts, et toutes les
# cryptos n'ont pas de paire Yahoo fiable.
_CRYPTO_TICKERS: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "XRP": "XRP-USD",
    "ADA": "ADA-USD",
    "LINK": "LINK-USD",
    "ATOM": "ATOM-USD",
    "FIL": "FIL-USD",
    "INJ": "INJ-USD",
    "RENDER": "RENDER-USD",
    "TAO": "TAO22974-USD",
}


def _extract_price(payload: Any) -> Optional[float]:
    """Extrait le dernier prix d'une réponse chart v8 Yahoo. None si absent.

    Structure attendue :
    ``{"chart": {"result": [{"meta": {"regularMarketPrice": <float>}}]}}``.
    Tolérant : toute déviation (clé manquante, result vide, erreur) → None.
    """
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return None
    if chart.get("error"):
        return None
    result = chart.get("result")
    if not isinstance(result, list) or not result:
        return None
    meta = result[0].get("meta") if isinstance(result[0], dict) else None
    if not isinstance(meta, dict):
        return None
    price = meta.get("regularMarketPrice")
    if isinstance(price, (int, float)):
        return float(price)
    return None


def _fetch_one(yahoo_symbol: str) -> Optional[float]:
    """Récupère le prix courant d'un symbole Yahoo. None si indisponible."""
    url = _CHART.format(symbol=yahoo_symbol)
    data = get_json(url, headers=_HEADERS, params={"interval": "1d", "range": "1d"})
    return _extract_price(data)


def get_macro_quotes() -> dict[str, float]:
    """Prix macro temps réel depuis Yahoo. Dict {nom_interne: prix}.

    Renvoie uniquement les métriques effectivement récupérées (les autres sont
    absentes → l'appelant retombe sur FRED). ``us_10y`` est converti de l'unité
    Yahoo (pourcentage ×10) vers le pourcentage réel.

    Vide si Yahoo est totalement injoignable (dégradation → FRED).
    """

    def _fetch() -> dict[str, float]:
        out: dict[str, float] = {}
        for name, ysym in _MACRO_TICKERS.items():
            price = _fetch_one(ysym)
            if price is None:
                continue
            if name == "us_10y":
                # Yahoo cote ^TNX en pourcentage ×10 (ex. 44.5 = 4.45 %).
                price = round(price / 10.0, 4)
            out[name] = price
        return out

    try:
        return CACHE.get_or_compute("yahoo_macro_quotes", _TTL, _fetch) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo macro quotes indisponible : %s", exc)
        return {}


def get_crypto_quotes() -> dict[str, float]:
    """Prix crypto (USD) depuis Yahoo pour cross-check. Dict {TICKER: prix}.

    Sert uniquement à recouper CoinGecko ; n'est jamais la source primaire.
    Vide si indisponible (le cross-check est alors simplement sauté).
    """

    def _fetch() -> dict[str, float]:
        out: dict[str, float] = {}
        for ticker, ysym in _CRYPTO_TICKERS.items():
            price = _fetch_one(ysym)
            if price is not None:
                out[ticker] = price
        return out

    try:
        return CACHE.get_or_compute("yahoo_crypto_quotes", _TTL, _fetch) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Yahoo crypto quotes indisponible : %s", exc)
        return {}


def cross_check_prices(
    market: dict[str, dict[str, Any]],
    *,
    tolerance_pct: float = 10.0,
) -> dict[str, Any]:
    """Recoupe les prix CoinGecko (``market``) avec Yahoo.

    Pour chaque crypto présente dans les deux sources, calcule l'écart relatif.
    Si l'écart dépasse ``tolerance_pct`` (10% par défaut, garde-fou unique), la
    crypto est signalée comme divergente et son prix sera masqué (—) côté rendu,
    avec report dans les angles morts — on préfère prévenir qu'afficher faux.

    Returns:
        ``{"checked": int, "divergent": [{symbol, coingecko, yahoo, gap_pct}],
           "available": bool}``. ``available=False`` si Yahoo est injoignable.
    """
    yahoo = get_crypto_quotes()
    if not yahoo:
        return {"checked": 0, "divergent": [], "available": False}

    divergent: list[dict[str, Any]] = []
    checked = 0
    for ticker, ydata_price in yahoo.items():
        cg = market.get(ticker) or {}
        cg_price = cg.get("price")
        if not isinstance(cg_price, (int, float)) or not cg_price:
            continue
        if not ydata_price:
            continue
        checked += 1
        gap_pct = abs(cg_price - ydata_price) / ydata_price * 100.0
        if gap_pct > tolerance_pct:
            divergent.append({
                "symbol": ticker,
                "coingecko": round(cg_price, 6),
                "yahoo": round(ydata_price, 6),
                "gap_pct": round(gap_pct, 1),
            })
            logger.warning(
                "Cross-check prix %s : CoinGecko=%s vs Yahoo=%s (écart %.1f%%).",
                ticker, cg_price, ydata_price, gap_pct,
            )

    return {"checked": checked, "divergent": divergent, "available": True}


def compute_crypto_price_status(
    market: dict[str, dict[str, Any]],
    cmc_quotes: Optional[dict[str, dict[str, Any]]] = None,
    yahoo_quotes: Optional[dict[str, float]] = None,
    *,
    tolerance_pct: float = 10.0,
) -> dict[str, dict[str, Any]]:
    """Statut de fiabilité par crypto, pour les pastilles du rendu.

    Croise le prix CoinGecko (source primaire) avec CoinMarketCap et/ou Yahoo.
    Garde-fou unique : ``tolerance_pct`` = 10% (décision produit).

    Statut par symbole :
      - ``"confirmed"`` : >= 2 sources concordantes (écart max <= 10%) → pastille verte.
      - ``"single"``    : une seule source disponible (CoinGecko seul) → pastille orange.
      - ``"diverged"``  : 2+ sources mais écart > 10% → prix masqué (—) + angle mort.

    Args:
        market: ``{symbol: {price, ...}}`` (CoinGecko, source primaire).
        cmc_quotes: ``{symbol: {price, ...}}`` (CoinMarketCap) ou None.
        yahoo_quotes: ``{symbol: price}`` (Yahoo) ou None.

    Returns:
        ``{symbol: {"status": str, "gap_pct": float|None, "sources": [str], "price": float}}``.
        Ne contient que les symboles ayant au moins un prix CoinGecko.
    """
    cmc = cmc_quotes or {}
    yq = yahoo_quotes or {}
    out: dict[str, dict[str, Any]] = {}

    for symbol, data in market.items():
        cg_price = (data or {}).get("price")
        if not isinstance(cg_price, (int, float)) or cg_price <= 0:
            continue  # pas de prix primaire → rien à statuer

        # Prix alternatifs disponibles (sources secondaires).
        alts: list[tuple[str, float]] = []
        cmc_entry = cmc.get(symbol) or {}
        cmc_price = cmc_entry.get("price")
        if isinstance(cmc_price, (int, float)) and cmc_price > 0:
            alts.append(("CoinMarketCap", float(cmc_price)))
        y_price = yq.get(symbol)
        if isinstance(y_price, (int, float)) and y_price > 0:
            alts.append(("Yahoo", float(y_price)))

        if not alts:
            out[symbol] = {
                "status": "single", "gap_pct": None,
                "sources": ["CoinGecko"], "price": float(cg_price),
            }
            continue

        # Écart relatif max entre CoinGecko et les sources secondaires.
        max_gap = max(abs(cg_price - p) / p * 100.0 for _, p in alts)
        sources = ["CoinGecko"] + [name for name, _ in alts]
        if max_gap > tolerance_pct:
            status = "diverged"
            logger.warning(
                "Statut prix %s : DIVERGENT (écart max %.1f%% > %.0f%%) entre %s.",
                symbol, max_gap, tolerance_pct, ", ".join(sources),
            )
        else:
            status = "confirmed"
        out[symbol] = {
            "status": status, "gap_pct": round(max_gap, 1),
            "sources": sources, "price": float(cg_price),
        }

    return out


def compute_macro_source_status(
    macro_context: dict[str, Any],
    yahoo_quotes: Optional[dict[str, float]] = None,
    fred_raw: Optional[dict[str, Any]] = None,
    *,
    tolerance_pct: float = 10.0,
) -> dict[str, str]:
    """Statut de fiabilité par métrique macro, pour les pastilles.

    Pour chaque métrique macro affichée, détermine :
      - ``"confirmed"`` : Yahoo ET FRED disponibles et concordants (<= 10%).
      - ``"single"``    : une seule source (Yahoo OU FRED).
    La validation par plage plausible est gérée en amont (``_vm`` met la valeur à
    None si aberrante) ; ici on ne statue que la concordance des sources.

    Args:
        macro_context: dict déjà assemblé (clés ``gold_usd``, ``vix``, etc.).
        yahoo_quotes: ``{nom_interne: prix}`` (clés ``gold``, ``vix``, ...).
        fred_raw: dict FRED brut (mêmes clés internes) ou None.

    Returns:
        ``{clé_macro_context: "confirmed"|"single"}`` pour les métriques présentes.
    """
    yq = yahoo_quotes or {}
    fred = fred_raw or {}

    # Correspondance clé d'affichage (macro_context) -> (clé Yahoo, clé FRED).
    mapping: dict[str, tuple[str, str]] = {
        "gold_usd": ("gold", "gold"),
        "brent_usd": ("brent", "brent"),
        "wti_usd": ("wti", "wti"),
        "sp500": ("sp500", "sp500"),
        "nasdaq": ("nasdaq", "nasdaq"),
        "vix": ("vix", "vix"),
        "us_10y": ("us_10y", "us_10y"),
        "dxy_ice": ("dxy_ice", ""),     # Yahoo seul (FRED n'a pas le DXY ICE)
        "dxy": ("", "dxy"),             # FRED seul (indice large)
        "eur_usd": ("eur_usd", "eur_usd"),
        "usd_jpy": ("usd_jpy", "usd_jpy"),
    }

    def _fred_val(key: str) -> Optional[float]:
        if not key:
            return None
        entry = fred.get(key)
        if isinstance(entry, dict):
            v = entry.get("value")
        else:
            v = entry
        return float(v) if isinstance(v, (int, float)) else None

    status: dict[str, str] = {}
    for disp_key, (ykey, fkey) in mapping.items():
        # On ne statue que si la métrique est effectivement affichée (non None).
        if macro_context.get(disp_key) is None:
            continue
        y_val = yq.get(ykey) if ykey else None
        f_val = _fred_val(fkey)
        has_y = isinstance(y_val, (int, float))
        has_f = isinstance(f_val, (int, float))
        if has_y and has_f and f_val:
            gap = abs(y_val - f_val) / f_val * 100.0
            status[disp_key] = "confirmed" if gap <= tolerance_pct else "single"
        else:
            # Une seule source disponible (cas normal pour dxy_ice / dxy).
            status[disp_key] = "single"
    return status
