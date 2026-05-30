"""Source calendrier macro : scraping Boursorama (complément/fallback).

Récupère le calendrier macroéconomique depuis Boursorama :
https://www.boursorama.com/bourse/actualites/calendriers/macroeconomique

ATTENTION — fragilité assumée :
- Boursorama applique un anti-scraping (peut renvoyer 403). On envoie des
  en-têtes navigateur réalistes mais le succès n'est pas garanti.
- La structure HTML peut changer ; le parsing est défensif (try/except partout).
- En cas d'échec, renvoie ``{available: False}`` — le calendrier Trading
  Economics et la macro via Gemini prennent alors le relais.

Ce module est un COMPLÉMENT, pas une source critique. Le pipeline ne doit
jamais dépendre de lui seul.
"""

from __future__ import annotations

import re
from typing import Any

from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_URL = "https://www.boursorama.com/bourse/actualites/calendriers/macroeconomique"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Pays/zones à fort impact pour le crypto.
_KEY_COUNTRIES = ("états-unis", "etats-unis", "USA", "zone euro", "chine", "japon")


def get_boursorama_calendar() -> dict[str, Any]:
    """Scrape le calendrier macro Boursorama (best-effort, dégradation gracieuse).

    Returns:
        Dict ``{available, source, events: [{time, country, event, importance,
        previous, forecast, actual}], reason}``.
    """

    def _fetch() -> dict[str, Any]:
        try:
            import requests
        except ImportError:
            return {"available": False, "reason": "requests indisponible"}
        try:
            resp = requests.get(_URL, headers=_BROWSER_HEADERS, timeout=15)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"réseau : {exc}"}

        if resp.status_code != 200:
            return {
                "available": False,
                "reason": f"HTTP {resp.status_code} (anti-scraping probable)",
            }

        html = resp.text
        events: list[dict[str, Any]] = []

        # Parsing défensif avec BeautifulSoup si dispo, sinon regex.
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            # Boursorama structure le calendrier en lignes de tableau.
            rows = soup.select("table tr") or soup.select("[class*=calendar] tr")
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 3:
                    continue
                # Heuristique : [heure, pays, événement, ...valeurs]
                event = {
                    "time": cells[0] if cells else "",
                    "country": cells[1] if len(cells) > 1 else "",
                    "event": cells[2] if len(cells) > 2 else "",
                    "previous": cells[3] if len(cells) > 3 else "",
                    "forecast": cells[4] if len(cells) > 4 else "",
                    "actual": cells[5] if len(cells) > 5 else "",
                }
                if event["event"] and len(event["event"]) > 2:
                    events.append(event)
        except ImportError:
            # Fallback regex minimaliste si bs4 absent.
            matches = re.findall(
                r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE
            )
            for m in matches[:40]:
                texts = re.findall(r">([^<]{2,})<", m)
                texts = [t.strip() for t in texts if t.strip()]
                if len(texts) >= 3:
                    events.append(
                        {
                            "time": texts[0],
                            "country": texts[1] if len(texts) > 1 else "",
                            "event": texts[2] if len(texts) > 2 else "",
                            "previous": texts[3] if len(texts) > 3 else "",
                            "forecast": texts[4] if len(texts) > 4 else "",
                            "actual": "",
                        }
                    )

        if not events:
            return {
                "available": False,
                "reason": "aucun événement parsé (structure HTML modifiée ?)",
            }

        return {
            "available": True,
            "source": "Boursorama",
            "events": events[:30],
            "events_count": len(events),
        }

    try:
        return CACHE.get_or_compute("boursorama:calendar", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Boursorama calendar échoué : %s", exc)
        return {"available": False, "reason": str(exc)}
