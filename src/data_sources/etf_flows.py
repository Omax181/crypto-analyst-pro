"""Source ETF flows : scraping de la page publique Farside Investors.

Récupère les flux quotidiens des ETF spot BTC et ETH. Le HTML de Farside est
un tableau ; on extrait la dernière ligne (jour le plus récent) et le cumulé.
Dégradation gracieuse si la page change de structure ou est indisponible.
"""

from __future__ import annotations

from typing import Any, Optional

from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

BTC_ETF_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
ETH_ETF_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"
_HEADERS = {"User-Agent": "crypto-analyst-pro/2.0 (personal research)"}


def _scrape_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Scrape la dernière ligne de flux d'une page Farside."""
    try:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return None
        rows = table.find_all("tr")
        # Cherche la dernière ligne de données (avec une date en première cellule).
        for row in reversed(rows):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) >= 2 and any(ch.isdigit() for ch in cells[0]):
                total = cells[-1].replace(",", "").replace("(", "-").replace(")", "")
                try:
                    flow = float(total)
                except ValueError:
                    flow = None
                return {"date": cells[0], "total_flow_musd": flow}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Farside scraping échoué (%s) : %s", url, exc)
        return None


def get_etf_flows() -> dict[str, Any]:
    """Récupère les flux ETF BTC et ETH du jour le plus récent.

    Returns:
        Dict ``{available, btc: {...}|None, eth: {...}|None}``.
    """

    def _fetch() -> dict[str, Any]:
        btc = _scrape_latest_flow(BTC_ETF_URL)
        eth = _scrape_latest_flow(ETH_ETF_URL)
        return {"available": bool(btc or eth), "btc": btc, "eth": eth}

    return CACHE.get_or_compute("etf:flows", 3600, _fetch)
