"""Source Crypto Bubbles : top mouvements multi-horizons (API publique, sans clé).

Crypto Bubbles expose un endpoint JSON non officiel utilisé par leur site web,
qui renvoie le top ~1000 cryptos avec leurs performances sur plusieurs horizons
(heure, jour, semaine, mois, année). On l'utilise comme source COMPLÉMENTAIRE
pour :
- repérer les plus gros mouvements du marché (gainers/losers du jour) ;
- croiser avec le portefeuille (un token du PTF qui surchauffe ou décroche
  fortement vs le reste du marché) ;
- donner à l'IA une vue macro de la rotation au-delà des seules positions.

API non officielle : dégradation gracieuse totale si l'endpoint change ou
devient indisponible. Aucune clé requise.
"""

from __future__ import annotations

from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Endpoint public utilisé par le frontend de cryptobubbles.net.
_URL = "https://cryptobubbles.net/backend/data/bubbles1000.usd.json"
_HEADERS = {"User-Agent": "crypto-analyst-pro/2.0 (personal research)"}
_TTL = 1800  # 30 min : les perfs bougent mais pas besoin de temps réel.

# Mapping des horizons Crypto Bubbles → libellés internes.
_HORIZONS = {"hour": "1h", "day": "24h", "week": "7d", "month": "30d", "year": "1y"}


def get_market_movers(
    portfolio_symbols: list[str] | None = None, *, top_n: int = 3,
    min_market_cap: float = 50_000_000,
) -> dict[str, Any]:
    """Récupère les plus gros mouvements du marché sur 24h, et le focus PTF.

    Args:
        portfolio_symbols: tickers du portefeuille (pour le focus PTF).
        top_n: nombre de gainers/losers à renvoyer (v16 : 3 par défaut).
        min_market_cap: capitalisation minimale en USD pour qu'une crypto
            soit éligible aux tops du marché (v16 : 50 M$ — sous ce seuil, le
            risque pump & dump / scam est trop élevé pour une reco utile).

    Returns:
        Dict ``{available, gainers, losers, portfolio_movers, total_tracked}``
        où chaque entrée est ``{symbol, name, change_24h, change_7d, rank,
        market_cap}``. ``available=False`` si l'endpoint est injoignable.
    """

    def _fetch() -> Any:
        return get_json(_URL, headers=_HEADERS, timeout=15)

    try:
        raw = CACHE.get_or_compute("cryptobubbles_1000", _TTL, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Crypto Bubbles indisponible : %s", exc)
        return {"available": False}

    if not isinstance(raw, list) or not raw:
        logger.warning("Crypto Bubbles : réponse inattendue (non-liste ou vide).")
        return {"available": False}

    coins: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        sym = (c.get("symbol") or "").upper()
        perf = c.get("performance") or {}
        if not sym or not isinstance(perf, dict):
            continue
        # v17 (M-A21) : écarte les tickers parasites (mono-lettre « H »/« B »,
        # artefacts de parsing). Un vrai ticker crypto fait ≥ 2 caractères
        # alphanumériques. On garde les symboles connus du PTF même courts.
        _wanted = {s.upper() for s in (portfolio_symbols or [])}
        if sym not in _wanted and (len(sym) < 2 or not any(ch.isalpha() for ch in sym)):
            continue
        change_24h = perf.get("day")
        if change_24h is None:
            continue
        coins.append(
            {
                "symbol": sym,
                "name": c.get("name") or sym,
                "change_1h": _round(perf.get("hour")),
                "change_24h": _round(change_24h),
                "change_7d": _round(perf.get("week")),
                "change_30d": _round(perf.get("month")),
                "rank": c.get("rank"),
                # v16 — capitalisation (USD) : sert à filtrer les micro-caps
                # (pump & dump / scam) des tops du marché. Le champ s'appelle
                # « marketcap » dans le flux cryptobubbles ; on tolère l'absence.
                "market_cap": _num(c.get("marketcap")),
            }
        )

    if not coins:
        return {"available": False}

    # Gainers / losers du jour. v16 — filtrage MarketCap > seuil (50 M$) pour
    # écarter les micro-caps illiquides / pump & dump des tops du marché. On
    # garde le garde-fou de rang (top 500) en complément. Si trop peu de coins
    # ont une marketcap renseignée, on retombe sur le filtre de rang seul pour
    # ne jamais renvoyer une liste vide.
    cap_filtered = [
        c for c in coins
        if (c.get("rank") or 9999) <= 500
        and (c.get("market_cap") is None or c["market_cap"] >= min_market_cap)
    ]
    strict_cap = [c for c in cap_filtered if c.get("market_cap") is not None]
    pool = strict_cap if len(strict_cap) >= top_n * 2 else (cap_filtered or coins)
    by_change = sorted(pool, key=lambda c: c["change_24h"], reverse=True)
    gainers = by_change[:top_n]
    losers = list(reversed(by_change[-top_n:]))

    # Focus portefeuille : mouvements des tokens détenus.
    portfolio_movers: list[dict[str, Any]] = []
    if portfolio_symbols:
        wanted = {s.upper() for s in portfolio_symbols}
        portfolio_movers = [c for c in coins if c["symbol"] in wanted]
        portfolio_movers.sort(key=lambda c: abs(c["change_24h"]), reverse=True)

    return {
        "available": True,
        "gainers": gainers,
        "losers": losers,
        "portfolio_movers": portfolio_movers[:12],
        "total_tracked": len(coins),
    }


def _round(value: Any) -> float | None:
    """Arrondit à 1 décimale, tolérant aux valeurs absentes/non numériques."""
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (ValueError, TypeError):
        return None


def _num(value: Any) -> float | None:
    """Convertit en float brut (capitalisation), tolérant aux valeurs absentes."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
