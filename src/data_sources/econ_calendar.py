"""Calendrier économique RICHE — feed hebdo ForexFactory (faireconomy).

v24 — la source qui manquait. FRED ne couvre que quelques releases US (key-gated)
et Boursorama est rendu en JS (illisible en statique) : résultat, le calendrier
weekly n'affichait souvent qu'UN événement (« NFP »), ce qui rend les scénarios
et thèses aveugles à la semaine à venir.

Ce feed public, GRATUIT et SANS CLÉ, liste ~100 événements/semaine pour toutes les
grandes économies, chacun CLASSÉ PAR CRITICITÉ (High/Medium/Low/Holiday) :
    https://nfs.faireconomy.media/ff_calendar_thisweek.json
Champs par événement : title, country (code devise), date (ISO + fuseau),
impact, forecast, previous.

Bénéficie aux 3 mails via ``macro_calendar.get_consolidated_calendar`` : semaine à
venir (weekly), contexte macro (morning), macro du lendemain (evening) — donc aussi
aux prédictions/scénarios qui s'appuient dessus.

Limite connue : seul le feed de la semaine COURANTE existe (pas de « nextweek » —
404) ; au-delà, FRED + calendriers banques centrales + récurrences prennent le
relais. Dégradation gracieuse : ``{available: False}`` si le feed est injoignable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# v25 (audit M6) — fuseau PRODUIT (Casablanca), pas celui du process : sur GitHub
# Actions (UTC), astimezone() nu donnait des heures décalées vs le reste des mails.
_TZ_PRODUCT = ZoneInfo("Africa/Casablanca")

# Devises crypto-pertinentes → libellé zone FR. On IGNORE AUD/NZD/CAD/CHF : leur
# impact sur le crypto est quasi nul et elles noieraient le calendrier.
_ZONE = {"USD": "US", "EUR": "Zone euro", "JPY": "Japon", "GBP": "UK", "CNY": "Chine"}
# On ne garde que High + Medium (Low = bruit, Holiday = jour férié, non actionnable).
_IMPACT = {"High": "high", "Medium": "medium"}


def _to_local_dt(iso: Any) -> Optional[datetime]:
    """Parse une date ISO (avec fuseau) et la convertit en heure CASABLANCA
    (fuseau produit — tous les mails affichent l'heure de Casablanca)."""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TZ_PRODUCT)
    return dt


def get_econ_calendar(horizon_days: int = 8) -> dict[str, Any]:
    """Événements macro de la semaine (ForexFactory), filtrés zones + criticité.

    Args:
        horizon_days: fenêtre en jours à partir d'aujourd'hui.

    Returns:
        ``{available, events: [{label, title, zone, country, date (YYYY-MM-DD),
        time, importance (high|medium), forecast, previous, source}], reason?}``.
    """

    def _build() -> dict[str, Any]:
        data = get_json(_FEED_URL, timeout=15)
        if not isinstance(data, list):
            return {"available": False, "events": [],
                    "reason": "feed ForexFactory injoignable"}
        today = date.today()
        end = today + timedelta(days=horizon_days)
        out: list[dict[str, Any]] = []
        for ev in data:
            if not isinstance(ev, dict):
                continue
            imp = _IMPACT.get(str(ev.get("impact") or "").strip())
            if not imp:
                continue
            ccy = str(ev.get("country") or "").strip().upper()
            zone = _ZONE.get(ccy)
            if not zone:
                continue
            dt = _to_local_dt(ev.get("date"))
            if dt is None:
                continue
            d = dt.date()
            if not (today <= d <= end):
                continue
            title = str(ev.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title,
                "zone": zone,
                "country": ccy,
                "date": d.isoformat(),
                "time": dt.strftime("%H:%M"),
                "importance": imp,
                "forecast": (str(ev.get("forecast") or "").strip() or None),
                "previous": (str(ev.get("previous") or "").strip() or None),
                "label": f"{title} ({zone})",
                "source": "ForexFactory",
            })
        # Tri : par date, High avant Medium le même jour.
        out.sort(key=lambda e: (e["date"], 0 if e["importance"] == "high" else 1))
        return {"available": bool(out), "events": out}

    try:
        return CACHE.get_or_compute(f"econ_calendar:ff:{horizon_days}", 3600, _build)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Calendrier éco (ForexFactory) indisponible : %s", exc)
        return {"available": False, "events": []}
