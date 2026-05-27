"""Token Unlocks : unlocks à venir sur les positions du portfolio (API publique)."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any
from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
logger = get_logger(__name__)

_SYMBOLS = {"TAO","ARB","ZK","IMX","RENDER","W","WLD","FET","INJ","STX","ATOM","AXL"}


def get_upcoming_unlocks(days_ahead: int = 30) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        try:
            data = get_json("https://api.unlocks.app/v1/unlocks", params={"limit": 50})
            items = data if isinstance(data, list) else (data or {}).get("data", [])
            if not items:
                return {"available": False, "unlocks": []}
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(days=days_ahead)
            unlocks = []
            for it in items:
                sym = (it.get("symbol") or "").upper()
                if sym not in _SYMBOLS:
                    continue
                ts = it.get("date") or it.get("timestamp")
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if now <= dt <= cutoff:
                    unlocks.append({"symbol": sym, "date": dt.strftime("%Y-%m-%d"),
                        "amount_usd": it.get("value_usd"), "pct_supply": it.get("pct_total_supply")})
            return {"available": True, "unlocks": unlocks, "count": len(unlocks)}
        except Exception as exc:
            logger.warning("Token unlocks : %s", exc)
            return {"available": False, "unlocks": []}
    return CACHE.get_or_compute("token_unlocks", 3600, _fetch)
