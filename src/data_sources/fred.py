"""Source FRED : indicateurs macro USA (Fed Funds, DXY, 10Y, VIX, CPI...).

Clé gratuite, requêtes illimitées. Pour chaque série on récupère la dernière
observation et la précédente (pour le delta).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_BASE = _SOURCES["endpoints"]["fred"]
_SERIES: dict[str, str] = _SOURCES["fred_series"]


def _latest_observation(series_id: str, key: str) -> Optional[dict[str, Any]]:
    """Récupère les 2 dernières observations valides d'une série FRED."""
    data = get_json(
        f"{_BASE}/series/observations",
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        },
    )
    if not data or "observations" not in data:
        return None
    valid = [
        o for o in data["observations"] if o.get("value") not in (".", "", None)
    ]
    if not valid:
        return None
    latest = valid[0]
    prev = valid[1] if len(valid) > 1 else None
    try:
        value = float(latest["value"])
        prev_value = float(prev["value"]) if prev else None
    except (ValueError, TypeError):
        return None
    return {
        "value": value,
        "date": latest.get("date"),
        "previous": prev_value,
        "delta": (value - prev_value) if prev_value is not None else None,
    }


def get_macro() -> dict[str, Any]:
    """Récupère toutes les séries macro configurées.

    Returns:
        Dict ``{available, series: {name: {value, date, previous, delta}}}``.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        logger.info("FRED : pas de clé, macro ignorée.")
        return {"available": False, "series": {}}

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, series_id in _SERIES.items():
            obs = _latest_observation(series_id, key)
            if obs:
                out[name] = obs
        return out

    series = CACHE.get_or_compute("fred:all", 3600, _fetch)
    return {"available": bool(series), "series": series}


# --- Séries datées (corrélations macro ↔ crypto) + calendrier macro --------

# Séries macro retenues pour les corrélations glissantes BTC ↔ macro.
# (clé logique -> id FRED ; ids déjà présents dans sources.yaml fred_series)
# A8 : la série or LBMA (GOLDPMGBD228NLBM) renvoie 400 (gelée côté FRED) et est
# retirée des corrélations — le prix or live reste fourni par Yahoo pour le
# dashboard. On ne garde ici que des séries quotidiennes fiables.
_CORR_SERIES = {
    "dxy": "DTWEXBGS",
    "sp500": "SP500",
    "vix": "VIXCLS",
    "us_10y": "DGS10",
}

# A7/A10/C6 — Calendrier macro À VENIR : prochaines publications officielles via
# l'endpoint FRED /release/dates (dates réelles, jamais inventées). Mapping
# release_id -> libellé pour les publications les plus suivies par le marché.
_UPCOMING_RELEASES = {
    10: "Inflation CPI",
    50: "Emploi US (NFP + chômage)",
    21: "Revenus & dépenses (PCE)",
    53: "PIB US (1re estimation)",
    9: "Ventes au détail US",
}

# Indicateurs « calendrier » : dernière publication + delta (raisonnement causal).
_CALENDAR_SERIES = {
    "cpi": ("CPIAUCSL", "Inflation CPI"),
    "core_pce": ("PCEPILFE", "Inflation core PCE"),
    "unemployment": ("UNRATE", "Chômage US"),
    "fed_funds": ("DFF", "Taux Fed effectif"),
    "nonfarm": ("PAYEMS", "Emploi non-agricole (NFP)"),
}


def _series_observations(series_id: str, key: str, limit: int = 60) -> list[dict[str, Any]]:
    """Récupère les ``limit`` dernières observations valides (ordre croissant)."""
    data = get_json(
        f"{_BASE}/series/observations",
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
    )
    if not data or "observations" not in data:
        return []
    valid = []
    for o in data["observations"]:
        if o.get("value") in (".", "", None):
            continue
        try:
            valid.append({"date": o.get("date"), "value": float(o["value"])})
        except (ValueError, TypeError):
            continue
    valid.reverse()  # croissant
    return valid


def get_macro_series(days: int = 35) -> dict[str, dict[str, float]]:
    """Renvoie les séries macro datées pour corrélation (``{key: {date: val}}``).

    Vide si pas de clé FRED. Aligné par date côté analytics.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return {}

    def _fetch() -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for name, sid in _CORR_SERIES.items():
            obs = _series_observations(sid, key, limit=max(days + 10, 45))
            if obs:
                out[name] = {o["date"]: o["value"] for o in obs}
        return out

    try:
        return CACHE.get_or_compute(f"fred:series:{days}", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED séries indisponibles : %s", exc)
        return {}


def get_calendar_prints() -> dict[str, Any]:
    """Derniers chiffres macro publiés (CPI, chômage, PCE, NFP, Fed funds).

    Donne à l'IA le « où on en est » pour le raisonnement causal (ex. chômage
    en hausse → biais baisse de taux). Chaque entrée : dernière valeur, date,
    précédente, delta. Vide si pas de clé FRED.

    Returns:
        Dict ``{available, prints: [{key, label, value, date, previous, delta}]}``.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return {"available": False, "prints": []}

    def _fetch() -> dict[str, Any]:
        # A5 — les séries CPI/PCE/NFP sont des INDICES (CPI ~332, base 1982),
        # pas des taux. Les afficher bruts (« CPI 332 ») est trompeur. On calcule
        # donc une variation lisible : YoY % pour l'inflation (CPI, core PCE), et
        # on garde le niveau pour le chômage / Fed funds (déjà des %). NFP : on
        # affiche la variation mensuelle d'emplois (en milliers).
        yoy_series = {"cpi", "core_pce"}
        prints: list[dict[str, Any]] = []
        for name, (sid, label) in _CALENDAR_SERIES.items():
            obs = _series_observations(sid, key, limit=14)  # 13 mois pour le YoY
            if not obs:
                continue
            last = obs[-1]
            prev = obs[-2] if len(obs) > 1 else None
            entry: dict[str, Any] = {
                "key": name,
                "label": label,
                "value": round(last["value"], 2),
                "date": last["date"],
                "previous": round(prev["value"], 2) if prev else None,
                "delta": round(last["value"] - prev["value"], 2) if prev else None,
            }
            if name in yoy_series and len(obs) >= 13:
                yago = obs[-13]["value"]
                if yago:
                    yoy = (last["value"] - yago) / yago * 100
                    prev_yoy = None
                    if len(obs) >= 14 and obs[-14]["value"]:
                        prev_yoy = (prev["value"] - obs[-14]["value"]) / obs[-14]["value"] * 100
                    entry["display"] = f"{yoy:+.1f}% sur 1 an"
                    entry["display_value"] = round(yoy, 1)
                    if prev_yoy is not None:
                        entry["display_delta"] = round(yoy - prev_yoy, 1)
            elif name == "nonfarm" and prev:
                # NFP : variation mensuelle de l'emploi, en milliers de postes.
                jobs_k = (last["value"] - prev["value"]) * 1000  # série en milliers
                entry["display"] = f"{jobs_k:+,.0f}k emplois (mois)"
            elif name in ("unemployment", "fed_funds"):
                # Déjà en % : on affiche le niveau tel quel.
                entry["display"] = f"{last['value']:.2f}%"
            prints.append(entry)
        return {"available": bool(prints), "prints": prints}

    try:
        return CACHE.get_or_compute("fred:calendar_prints", 3600, _fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED calendar prints indisponible : %s", exc)
        return {"available": False, "prints": []}


def get_upcoming_releases(horizon_days: int = 10) -> dict[str, Any]:
    """Prochaines publications macro officielles (dates RÉELLES, via FRED).

    Interroge l'endpoint ``/release/dates`` de FRED pour chaque publication
    suivie (CPI, emploi, PCE, PIB, ventes au détail) et retient la prochaine
    date >= aujourd'hui dans la fenêtre ``horizon_days``. Cela élimine toute
    hallucination de calendrier : si FRED ne renvoie rien, la liste est vide.

    Returns:
        Dict ``{available, events: [{key, label, date, days_ahead}]}`` trié par
        date croissante. ``available=False`` si pas de clé ou aucune date.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return {"available": False, "events": []}

    from datetime import date, datetime

    def _fetch() -> dict[str, Any]:
        today = date.today()
        events: list[dict[str, Any]] = []
        for rid, label in _UPCOMING_RELEASES.items():
            data = get_json(
                f"{_BASE}/release/dates",
                params={
                    "release_id": rid,
                    "api_key": key,
                    "file_type": "json",
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc",
                    "limit": 60,
                },
            )
            if not data or "release_dates" not in data:
                continue
            for rd in data["release_dates"]:
                ds = rd.get("date")
                if not ds:
                    continue
                try:
                    d = datetime.strptime(ds, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                if d < today:
                    continue
                days_ahead = (d - today).days
                if days_ahead <= horizon_days:
                    events.append(
                        {
                            "key": str(rid),
                            "label": label,
                            "date": ds,
                            "days_ahead": days_ahead,
                        }
                    )
                break  # première date future suffit pour cette publication
        events.sort(key=lambda e: e["date"])
        return {"available": bool(events), "events": events}

    try:
        return CACHE.get_or_compute(
            f"fred:upcoming:{horizon_days}", 3600, _fetch
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED upcoming releases indisponible : %s", exc)
        return {"available": False, "events": []}
