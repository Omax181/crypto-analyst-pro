"""Source ETF flows : scraping de la page publique Farside Investors.

Récupère les flux quotidiens des ETF spot BTC et ETH. Le HTML de Farside est
un tableau ; on extrait la dernière ligne (jour le plus récent) et le cumulé.
Dégradation gracieuse si la page change de structure ou est indisponible.

v14.1 — fiabilisation : les requêtes passent par ``http.get_text`` (retry
exponentiel sur 429/5xx + en-têtes type navigateur, l'UA « bot » nu déclenchait
des refus intermittents côté Cloudflare/Farside). Le parsing ne suppose plus
que le PREMIER ``<table>`` est le bon : on scanne toutes les tables et on
retient celle qui contient des lignes datées (robuste aux bandeaux/menus).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.data_sources.http import get_text
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

BTC_ETF_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
ETH_ETF_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"

# Une « ligne de données » commence par une date (ex. « 09 Jun 2026 »).
_DATE_CELL = re.compile(r"\d{1,2}\s+\w{3}\s+\d{4}|\d{4}-\d{2}-\d{2}")


def _scrape_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Scrape la dernière ligne de flux d'une page Farside (best-effort)."""
    try:
        from bs4 import BeautifulSoup

        html = get_text(url, timeout=20)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in reversed(rows):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cells) < 2:
                    continue
                first = cells[0]
                # Vraie ligne de données : 1re cellule = date (pas « Total »,
                # « Average », ni un libellé de menu).
                if not (_DATE_CELL.search(first) or
                        (any(ch.isdigit() for ch in first) and "total" not in first.lower())):
                    continue
                total = (
                    cells[-1].replace(",", "").replace("(", "-").replace(")", "")
                )
                try:
                    flow = float(total)
                except ValueError:
                    flow = None
                return {"date": first, "total_flow_musd": flow}
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
