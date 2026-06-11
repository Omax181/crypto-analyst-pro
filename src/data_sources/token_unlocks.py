"""Token Unlocks : déblocages à venir sur les positions du portefeuille.

v14.1 — SOURCE REMPLACÉE. L'ancien endpoint ``api.unlocks.app`` est MORT
(404 définitif, confirmé lors de l'audit). On passe sur l'API publique
DefiLlama « emissions » (gratuite, sans clé, domaine ``api.llama.fi`` déjà
throttlé dans http.py), qui alimente https://defillama.com/unlocks.

Parsing DÉFENSIF : le schéma exact de ``/emissions`` n'étant pas contractuel,
chaque champ est lu avec plusieurs noms candidats et toute déviation aboutit à
l'omission de l'item — JAMAIS à un crash ni à une valeur inventée. Pire cas :
``{available: False}``, strictement identique au comportement actuel avec
l'endpoint mort. Le schéma de sortie est INCHANGÉ pour les appelants :
``{available, unlocks: [{symbol, date, amount_usd, pct_supply}], count}``.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_EMISSIONS_URL = "https://api.llama.fi/emissions"

# Positions du PTF avec calendrier de vesting connu (les autres n'ont pas
# d'unlocks programmés significatifs).
_SYMBOLS = {"TAO", "ARB", "ZK", "IMX", "RENDER", "FET", "INJ", "STX", "ATOM", "AXL"}

# Reverse-map id CoinGecko -> ticker PTF (DefiLlama réutilise les gecko_id).
_GECKO_TO_SYM: dict[str, str] = {
    v: k for k, v in (load_config("sources").get("coingecko_ids") or {}).items()
}


def _sym_of(item: dict[str, Any]) -> Optional[str]:
    """Résout le ticker PTF d'un item DefiLlama (tSymbol/symbol/gecko_id)."""
    for key in ("tSymbol", "symbol", "ticker"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            s = v.strip().upper()
            # DefiLlama note parfois RNDR pour Render (ancien ticker).
            if s == "RNDR":
                s = "RENDER"
            if s in _SYMBOLS:
                return s
    for key in ("gecko_id", "geckoId", "token"):
        v = item.get(key)
        if isinstance(v, str):
            s = _GECKO_TO_SYM.get(v.strip().lower())
            if s in _SYMBOLS:
                return s
    return None


def _parse_when(value: Any) -> Optional[datetime]:
    """Parse une date DefiLlama : timestamp unix (s) OU chaîne ISO. None si KO."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            ts = float(value)
            if ts > 1e12:  # millisecondes
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _next_events(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrait le(s) prochain(s) événement(s) d'unlock d'un item (tolérant)."""
    ev = item.get("nextEvent") or item.get("next_event") or item.get("events")
    if isinstance(ev, dict):
        return [ev]
    if isinstance(ev, list):
        return [e for e in ev if isinstance(e, dict)][:4]
    return []


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None  # exclut NaN
    except (TypeError, ValueError):
        return None


def get_upcoming_unlocks(days_ahead: int = 30) -> dict[str, Any]:
    """Unlocks à venir (fenêtre ``days_ahead``) sur les positions concernées.

    Returns:
        Dict ``{available, unlocks: [{symbol, date, amount_usd, pct_supply}],
        count, source}``. ``amount_usd``/``pct_supply`` valent None quand
        DefiLlama ne fournit pas de quoi les calculer (jamais inventés).
    """

    def _fetch() -> dict[str, Any]:
        try:
            data = get_json(_EMISSIONS_URL)
            items = data if isinstance(data, list) else (data or {}).get("data", [])
            if not isinstance(items, list) or not items:
                return {"available": False, "unlocks": []}
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(days=days_ahead)
            unlocks: list[dict[str, Any]] = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                sym = _sym_of(it)
                if not sym:
                    continue
                price = _num(it.get("tPrice") or it.get("price"))
                max_supply = _num(it.get("maxSupply") or it.get("max_supply"))
                circ = _num(it.get("circSupply") or it.get("circ_supply"))
                supply_ref = max_supply or circ
                for ev in _next_events(it):
                    dt = _parse_when(
                        ev.get("date") or ev.get("timestamp") or ev.get("ts")
                    )
                    if dt is None or not (now <= dt <= cutoff):
                        continue
                    amount = _num(
                        ev.get("toUnlock") or ev.get("amount") or ev.get("tokens")
                    )
                    amount_usd = (
                        round(amount * price, 0)
                        if amount is not None and price is not None else None
                    )
                    pct_supply = (
                        round(amount / supply_ref * 100, 2)
                        if amount is not None and supply_ref else None
                    )
                    unlocks.append({
                        "symbol": sym,
                        "date": dt.strftime("%Y-%m-%d"),
                        "amount_usd": amount_usd,
                        "pct_supply": pct_supply,
                    })
            unlocks.sort(key=lambda u: u["date"])
            return {
                "available": True,
                "unlocks": unlocks,
                "count": len(unlocks),
                "source": "DefiLlama",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Token unlocks (DefiLlama) : %s", exc)
            return {"available": False, "unlocks": []}

    return CACHE.get_or_compute("token_unlocks", 3600, _fetch)
