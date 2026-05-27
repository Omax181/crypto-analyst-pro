"""Token Unlocks : détecte les unlocks à venir sur les positions du portfolio."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

SYMBOL_TO_ID = {
    "TAO": "bittensor", "ARB": "arbitrum", "OP": "optimism", "ZK": "zksync",
    "IMX": "immutable-x", "RENDER": "render-token", "W": "wormhole",
    "WLD": "worldcoin", "FET": "fetch-ai", "INJ": "injective-protocol",
    "STX": "stacks", "ATOM": "cosmos", "AXL": "axelar"
}

def get_upcoming_unlocks(days_ahead: int = 30) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json("https://api.unlocks.app/v1/unlocks", params={"limit": 50})
            if not isinstance(data, (list, dict)):
                return {"available": False, "unlocks": []}
            items = data if isinstance(data, list) else data.get("data", [])
            cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
            now = datetime.now(timezone.utc)
            portfolio_symbols = set(SYMBOL_TO_ID.keys())
            unlocks = []
            for item in items:
                symbol = (item.get("symbol") or "").upper()
                if symbol not in portfolio_symbols:
                    continue
                ts = item.get("date") or item.get("timestamp")
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if now <= dt <= cutoff:
                    unlocks.append({"symbol": symbol, "date": dt.strftime("%Y-%m-%d"),
                        "amount_usd": item.get("value_usd"), "pct_supply": item.get("pct_total_supply")})
            return {"available": True, "unlocks": unlocks, "count": len(unlocks)}
        except Exception as exc:
            logger.warning("Token unlocks indisponible : %s", exc)
            return {"available": False, "unlocks": []}
    return CACHE.get_or_compute("token_unlocks", 3600, _fetch)
