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

import os
import re
from typing import Any, Optional

from src.data_sources.http import get_text
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

BTC_ETF_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
ETH_ETF_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"

# v16 — Farside renvoie souvent 403 depuis les IP datacenter (GitHub Actions).
# En-têtes navigateur réalistes pour maximiser les chances d'obtenir le HTML
# (le 403 est fréquemment basé sur un User-Agent « python-requests »).
_ETF_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://farside.co.uk/",
}

# Fallback keyless v16 — endpoint public CoinGlass (flux ETF agrégés). Best-effort :
# si Farside ET CoinGlass échouent, dégradation propre (available=False).
_COINGLASS_BTC = "https://capi.coinglass.com/api/etf/bitcoin/flowHistory"
_COINGLASS_ETH = "https://capi.coinglass.com/api/etf/ethereum/flowHistory"
# v18 (M-A24) — 2e schéma d'URL CoinGlass (l'API alterne entre fapi/capi selon
# l'endpoint). On tente les deux : maximise les chances quand Farside est down.
_COINGLASS_BTC_ALT = "https://fapi.coinglass.com/api/etf/bitcoin/flowHistory"
_COINGLASS_ETH_ALT = "https://fapi.coinglass.com/api/etf/ethereum/flowHistory"

# Une « ligne de données » commence par une date (ex. « 09 Jun 2026 »).
_DATE_CELL = re.compile(r"\d{1,2}\s+\w{3}\s+\d{4}|\d{4}-\d{2}-\d{2}")


def _scrape_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Scrape la dernière ligne de flux d'une page Farside (best-effort)."""
    try:
        from bs4 import BeautifulSoup

        html = get_text(url, timeout=20, headers=_ETF_HEADERS)
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
                return {"date": first, "total_flow_musd": flow, "source": "Farside"}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Farside scraping échoué (%s) : %s", url, exc)
        return None


def _coinglass_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Fallback keyless CoinGlass : dernier flux net ETF agrégé. None si KO."""
    try:
        from src.data_sources.http import get_json

        data = get_json(url, timeout=15, headers=_ETF_HEADERS)
        if not isinstance(data, dict):
            return None
        rows = (data.get("data") or {}).get("list") or data.get("data") or []
        if not isinstance(rows, list) or not rows:
            return None
        last = rows[-1] if isinstance(rows[-1], dict) else None
        if not last:
            return None
        # Champs CoinGlass usuels : changeUsd / flowUsd (en USD) → on convertit
        # en millions pour homogénéiser avec Farside.
        raw = (last.get("changeUsd") or last.get("flowUsd")
               or last.get("netFlow") or last.get("total"))
        flow_m = None
        try:
            flow_m = round(float(raw) / 1_000_000, 1) if raw is not None else None
        except (TypeError, ValueError):
            flow_m = None
        _ts = last.get("date") or last.get("timestamp") or ""
        return {"date": str(_ts), "total_flow_musd": flow_m, "source": "CoinGlass"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("CoinGlass ETF échoué (%s) : %s", url, exc)
        return None


def get_etf_flows() -> dict[str, Any]:
    """Récupère les flux ETF BTC et ETH du jour le plus récent.

    Returns:
        Dict ``{available, btc: {...}|None, eth: {...}|None}``.
    """

    def _fetch() -> dict[str, Any]:
        # v21 (Logs#2) — sur GitHub Actions, Farside renvoie 403 (geo-block
        # datacenter) ET les endpoints CoinGlass sont morts (404) : tenter ces
        # 6 appels ne produit QUE du bruit pour zéro donnée. Le flux ETF est
        # alors couvert par le canal Telegram « ETF_Flows » (réconcilié au rendu,
        # cf. digests). On saute donc proprement sur Actions. Hors Actions (dev
        # local / IP résidentielle), Farside répond : on garde le best-effort.
        if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
            return {"available": False, "btc": None, "eth": None,
                    "reason": "Farside 403 / CoinGlass 404 sur Actions — repli Telegram ETF_Flows"}
        btc = _scrape_latest_flow(BTC_ETF_URL)
        eth = _scrape_latest_flow(ETH_ETF_URL)
        # fallback CoinGlass si Farside est bloqué (403 datacenter).
        if btc is None:
            btc = _coinglass_latest_flow(_COINGLASS_BTC)
        if eth is None:
            eth = _coinglass_latest_flow(_COINGLASS_ETH)
        # 2e schéma d'URL CoinGlass si le premier a échoué.
        if btc is None:
            btc = _coinglass_latest_flow(_COINGLASS_BTC_ALT)
        if eth is None:
            eth = _coinglass_latest_flow(_COINGLASS_ETH_ALT)
        return {"available": bool(btc or eth), "btc": btc, "eth": eth}

    return CACHE.get_or_compute("etf:flows", 3600, _fetch)
