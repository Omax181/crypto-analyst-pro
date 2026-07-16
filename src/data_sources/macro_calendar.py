"""Calendrier macro CONSOLIDÉ — v15.

Problème v14 (audit Omar) : « Aucun événement macro majeur identifié » alors
que le FOMC de juin était même cité par Polymarket dans le même mail. Cause :
le hebdo ne s'appuyait que sur FRED /release/dates, qui peut renvoyer vide
(clé absente, horizon trop court, releases hors mapping) — et il n'y avait
aucun filet. Sans calendrier, les scénarios de la semaine sont aveugles.

v15 : trois couches fusionnées, dédupliquées, triées par date :
  1. FRED /release/dates (dates RÉELLES — CPI, NFP, PCE, PIB, retail).
  2. Boursorama calendrier macro (scraping best-effort, plusieurs URL
     candidates + pages datées ; source explicitement demandée par Omar).
  3. Repli OFFICIEL banques centrales : dates FOMC 2026 (calendrier publié
     par la Fed), réunions BoJ 2026, + récurrences CPI/NFP marquées
     « estimé » si rien d'autre ne couvre la fenêtre.

Chaque événement porte : label, date (YYYY-MM-DD), days_ahead, importance
(high/medium), source, estimated (bool). La couche 3 n'INVENTE rien : les
dates FOMC/BoJ proviennent des calendriers officiels publiés à l'avance ;
les récurrences statistiques sont étiquetées « estimé » dans le label final.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.data_sources import fred
from src.data_sources.boursorama_calendar import get_boursorama_calendar
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# v18 (W-A1) — jours/mois français autonomes (GitHub Actions n'a pas la locale
# fr_FR ; on ne dépend pas de src.main pour éviter un import circulaire).
_CAL_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_CAL_MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                "août", "septembre", "octobre", "novembre", "décembre"]

# ── Couche 3a : décisions banques centrales 2026 (calendriers OFFICIELS,
# publiés à l'avance par la Fed / la BoJ — pas des estimations). Jour retenu =
# 2e jour de réunion (jour de la décision/annonce).
_FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
              "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
_BOJ_2026 = ["2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16",
             "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18"]
# v16 — dates BCE 2026 (réunions de politique monétaire) : renforce le
# calendrier consolidé pour la zone euro (Omar suit le carry/EUR).
_BCE_2026 = ["2026-01-29", "2026-03-12", "2026-04-16", "2026-06-04",
             "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17"]

_CENTRAL_BANK_EVENTS = (
    [{"label": "Décision FOMC (taux Fed)", "date": d, "importance": "high",
      "source": "Calendrier Fed officiel", "estimated": False} for d in _FOMC_2026]
    + [{"label": "Décision BoJ (taux Japon)", "date": d, "importance": "high",
        "source": "Calendrier BoJ officiel", "estimated": False} for d in _BOJ_2026]
    + [{"label": "Décision BCE (taux zone euro)", "date": d, "importance": "high",
        "source": "Calendrier BCE officiel", "estimated": False} for d in _BCE_2026]
)


def _recurring_estimates(today: date, horizon_days: int) -> list[dict[str, Any]]:
    """Récurrences US classiques (CPI ~mi-mois, NFP 1er vendredi) — ESTIMÉES.

    Filet de dernier recours quand ni FRED ni Boursorama ne couvrent la
    fenêtre : mieux vaut « CPI US attendu ~mar. 14 (estimé) » qu'un calendrier
    vide qui rend les scénarios aveugles. Toujours flagué ``estimated=True``.
    """
    out: list[dict[str, Any]] = []
    end = today + timedelta(days=horizon_days)
    cur = date(today.year, today.month, 1)

    def _us_market_holiday(d: date) -> bool:
        """Fériés US affectant les publications BLS du vendredi (cas connus).

        v26 (A10) : 4 juillet (Independence Day), 3 juillet OBSERVÉ quand le 4
        tombe un samedi, 1er janvier. Le BLS avance alors l'Emploi US au jeudi
        — sans ce décalage, le filet « estimé » redonnerait silencieusement la
        MAUVAISE date quand ForexFactory est down (audit v25 : le vrai NFP de
        juillet 2026 tombait le JEUDI 2, pas le « 1er vendredi »).
        """
        if d.month == 7 and d.day == 4:
            return True
        if d.month == 7 and d.day == 3 and (d + timedelta(days=1)).weekday() == 5:
            return True  # 4 juillet un samedi → férié observé le vendredi 3
        if d.month == 1 and d.day == 1:
            return True
        return False

    while cur <= end:
        # NFP : premier vendredi du mois, 14h30 (Casablanca) — reculé d'un jour
        # ouvré tant qu'il tombe sur un férié US (v26/A10).
        first_friday = cur + timedelta(days=(4 - cur.weekday()) % 7)
        nfp_day = first_friday
        while _us_market_holiday(nfp_day) or nfp_day.weekday() >= 5:
            nfp_day -= timedelta(days=1)
        if today <= nfp_day <= end:
            out.append({"label": "Emploi US (NFP)", "date": nfp_day.isoformat(),
                        "importance": "high", "source": "récurrence",
                        "estimated": True})
        # CPI : typiquement entre le 10 et le 15 — on pose le 1er jour ouvré
        # à partir du 11 (approximation honnête, flaguée estimée).
        cpi = date(cur.year, cur.month, 11)
        while cpi.weekday() >= 5:
            cpi += timedelta(days=1)
        if today <= cpi <= end:
            out.append({"label": "Inflation US (CPI)", "date": cpi.isoformat(),
                        "importance": "high", "source": "récurrence",
                        "estimated": True})
        cur = (date(cur.year + 1, 1, 1) if cur.month == 12
               else date(cur.year, cur.month + 1, 1))
    return out


# Familles d'événements pour la dédup cross-source (FR + EN : FRED & ForexFactory
# sont en anglais, récurrences/banques centrales en français). v24 : le générique
# « fed » a été RETIRÉ (il capturait « Fed … Speaks » de ForexFactory et fusionnait
# des speeches distincts) — seul « fomc » marque la famille FOMC.
_FAMILY_TOKENS = (
    # v25 — libellés ForexFactory des DÉCISIONS de taux : même famille que le
    # calendrier officiel (sinon « Federal Funds Rate (US) » s'affichait EN PLUS
    # de « Décision FOMC » le jour J — doublon du même événement).
    ("federal funds rate", "fomc"), ("main refinancing rate", "bce"),
    ("cpi", "cpi"), ("inflation", "cpi"),
    ("non-farm", "nfp"), ("nonfarm", "nfp"), ("employment change", "nfp"),
    ("payroll", "nfp"), ("emploi", "nfp"), ("nfp", "nfp"),
    ("fomc", "fomc"), ("boj", "boj"), ("bce", "bce"), ("ecb", "bce"),
    ("pce", "pce"), ("pib", "gdp"), ("gdp", "gdp"),
    ("retail sales", "retail"), ("retail", "retail"), ("ventes au détail", "retail"),
)


def _family(label: str) -> Optional[str]:
    """Famille d'un événement (nfp/cpi/fomc/…) pour la dédup, ou None si autre."""
    low = (label or "").lower()
    # v25 (audit M2) — un DISCOURS n'est pas la décision/publication : deux
    # « FOMC Member X/Y Speaks » le même jour restent deux lignes distinctes.
    if "speak" in low or "speech" in low or "testif" in low:
        return None
    # v25 (audit M2) — « Inflation Expectations » (UoM) n'est PAS le CPI : ni
    # fusion avec un vrai CPI, ni suppression de la récurrence CPI estimée.
    if "inflation expectations" in low:
        return None
    for token, name in _FAMILY_TOKENS:
        if token in low:
            return name
    return None


# v25 — marqueurs de zone NON-US dans les libellés (ForexFactory ajoute
# « (Zone euro) », « (Japon) »…). Sans eux, un « GDP q/q (UK) » et le PIB US le
# même jour partageraient la famille « gdp » et fusionneraient À TORT. Défaut =
# us (FRED, récurrences et banques centrales US n'ont pas de marqueur).
_ZONE_MARKERS = (("zone euro", "eu"), ("japon", "jp"), ("uk", "uk"),
                 ("chine", "cn"), ("(eur)", "eu"), ("(jpy)", "jp"),
                 ("(gbp)", "uk"), ("(cny)", "cn"))


def _zone_of(label_low: str) -> str:
    for marker, code in _ZONE_MARKERS:
        if marker in label_low:
            return code
    return "us"


def _norm_key(label: str, d: str) -> str:
    """Clé de dédup : famille + zone + date (CPI FRED == CPI estimé US le même
    jour ; un CPI zone euro ne fusionne jamais avec le CPI US)."""
    low = (label or "").lower()
    fam = _family(label)
    return f"{fam}:{_zone_of(low)}:{d}" if fam else f"other:{low[:32]}:{d}"


def _parse_bourso_events(raw: dict[str, Any], today: date,
                         horizon_days: int) -> list[dict[str, Any]]:
    """Convertit la sortie Boursorama (lignes de tableau) en événements datés.

    Boursorama liste le jour courant ; les pages datées (si servies) couvrent
    les jours suivants. On ne garde que les lignes à importance perceptible
    (mots-clés majeurs) pour ne pas noyer le calendrier sous 40 stats mineures.
    """
    if not raw.get("available"):
        return []
    majors = ("cpi", "inflation", "taux", "fed", "fomc", "bce", "boj", "pib",
              "gdp", "emploi", "nfp", "chômage", "pce", "retail",
              "ventes au détail", "pmi", "confiance")
    out: list[dict[str, Any]] = []
    for ev in raw.get("events", []):
        name = (ev.get("event") or "").strip()
        if not name or not any(m in name.lower() for m in majors):
            continue
        d = ev.get("date") or today.isoformat()  # page du jour par défaut
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if not (today <= dd <= today + timedelta(days=horizon_days)):
            continue
        label = name if len(name) <= 60 else name[:57] + "…"
        if ev.get("country"):
            label = f"{label} ({ev['country']})"
        if ev.get("time"):
            label = f"{label} · {ev['time']}"
        out.append({"label": label, "date": d, "importance": "medium",
                    "source": "Boursorama", "estimated": False})
    return out


def get_consolidated_calendar(horizon_days: int = 8) -> dict[str, Any]:
    """Calendrier macro fusionné FRED + Boursorama + repli officiel/estimé.

    Returns:
        ``{available, events:[{label,date,days_ahead,importance,source,
        estimated}], sources_used:[...]}`` trié par date. ``available`` n'est
        False que si la fenêtre est réellement vide après TOUTES les couches
        (quasi impossible avec le repli banques centrales + récurrences).
    """
    def _build() -> dict[str, Any]:
        today = date.today()
        merged: dict[str, dict[str, Any]] = {}
        sources_used: list[str] = []

        def _add(ev: dict[str, Any]) -> None:
            try:
                dd = datetime.strptime(ev["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError, TypeError):
                return
            if not (today <= dd <= today + timedelta(days=horizon_days)):
                return
            ev = dict(ev)
            ev["days_ahead"] = (dd - today).days
            k = _norm_key(ev.get("label", ""), ev["date"])
            prev = merged.get(k)
            # Une date réelle (FRED/officiel) écrase toujours une estimation.
            if prev is None or (prev.get("estimated") and not ev.get("estimated")):
                merged[k] = ev

        # Couche 1 — FRED (dates réelles).
        fred_cal = fred.get_upcoming_releases(horizon_days=horizon_days)
        if fred_cal.get("available"):
            sources_used.append("FRED")
            for e in fred_cal.get("events", []):
                _add({"label": e.get("label"), "date": e.get("date"),
                      "importance": e.get("importance", "high"), "source": "FRED",
                      "estimated": False})

        # Couche 1a — banques centrales (calendriers officiels 2026). AVANT
        # ForexFactory pour que le libellé officiel « Décision FOMC/BCE/BoJ » gagne
        # sa clé de famille face à un éventuel speech du même jour.
        for e in _CENTRAL_BANK_EVENTS:
            _add(e)
        if any(v.get("source", "").startswith("Calendrier") for v in merged.values()):
            sources_used.append("Calendriers banques centrales")

        # Couche 1b — ForexFactory (v24) : calendrier éco RICHE, gratuit, criticité.
        # LA source qui donne enfin une vraie couverture de la semaine (avant, le
        # weekly n'affichait qu'un seul événement). Corrige aussi les décalages de
        # date (ex. NFP avancé au jeudi quand le vendredi est férié aux US).
        try:
            from src.data_sources import econ_calendar as _ec
            ff = _ec.get_econ_calendar(horizon_days=horizon_days)
            if ff.get("available"):
                sources_used.append("ForexFactory")
                for e in ff.get("events", []):
                    _add({"label": e.get("label"), "date": e.get("date"),
                          "importance": e.get("importance", "medium"),
                          "source": "ForexFactory", "estimated": False,
                          "time": e.get("time"), "zone": e.get("zone"),
                          "forecast": e.get("forecast"),
                          "previous": _sane_mm_value(
                              e.get("label"), e.get("previous"))})
        except Exception as exc:  # noqa: BLE001
            logger.info("Calendrier éco (ForexFactory) indisponible : %s", exc)

        # Couche 2 — Boursorama (demande historique d'Omar ; souvent JS-only).
        try:
            bourso = get_boursorama_calendar()
            b_events = _parse_bourso_events(bourso, today, horizon_days)
            if b_events:
                sources_used.append("Boursorama")
                for e in b_events:
                    _add(e)
        except Exception as exc:  # noqa: BLE001
            logger.info("Boursorama calendrier indisponible : %s", exc)

        # Couche 3 — récurrences ESTIMÉES, seulement pour les (famille, ZONE)
        # ENCORE absentes des sources RÉELLES (v24/v25 : suppression par famille
        # ET zone — un vrai NFP ForexFactory supprime « NFP (estimé) », mais un
        # CPI zone euro réel ne supprime PAS l'estimation du CPI US). Jamais en
        # doublon d'une date réelle non plus (cf. _norm_key).
        _real_fams = set()
        for v in merged.values():
            if v.get("estimated"):
                continue
            _f = _family(v.get("label", ""))
            if _f:
                _real_fams.add((_f, _zone_of((v.get("label") or "").lower())))
        for e in _recurring_estimates(today, horizon_days):
            _f = _family(e.get("label", ""))
            if _f and (_f, "us") in _real_fams:  # récurrences = US uniquement
                continue
            _add(e)

        events = sorted(merged.values(), key=lambda e: (e["date"], e["label"]))
        # v24 — cap anti-mur : garde TOUS les high + complète avec les medium les
        # plus proches (plafond 28) pour ne pas noyer le mail sous 50 lignes.
        _MAXE = 28
        if len(events) > _MAXE:
            _highs = [e for e in events if e.get("importance") == "high"]
            _meds = [e for e in events if e.get("importance") != "high"]
            events = sorted(_highs + _meds[:max(0, _MAXE - len(_highs))],
                            key=lambda e: (e["date"], e["label"]))
        for e in events:
            if e.get("estimated"):
                e["label"] = f"{e['label']} (estimé)"
            da = e.get("days_ahead")
            e["when"] = ("aujourd'hui" if da == 0 else "demain" if da == 1
                         else f"dans {da}j")
            # v18 (W-A1/W-A13/W-B1) : jour de la semaine + date lisible française
            # calculés EN PYTHON depuis la date ISO. Source unique → matin, soir,
            # weekly et Gemini héritent du même libellé exact. Corrige le décalage
            # « BoJ (lundi) » pour un 16 juin qui tombe un mardi, et bannit le
            # format ISO dans les phrases.
            try:
                _d = datetime.strptime(e["date"], "%Y-%m-%d")
                e["weekday_label"] = _CAL_JOURS_FR[_d.weekday()]
                e["date_label"] = f"{_CAL_JOURS_FR[_d.weekday()]} {_d.day} {_CAL_MOIS_FR[_d.month - 1]}"
            except (ValueError, TypeError, KeyError):
                e["weekday_label"] = None
                e["date_label"] = None
        return {"available": bool(events), "events": events,
                "sources_used": sources_used}

    try:
        return CACHE.get_or_compute(
            f"macro_calendar:consolidated:{horizon_days}", 3600, _build
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Calendrier consolidé indisponible : %s", exc)
        return {"available": False, "events": [], "sources_used": []}
def _sane_mm_value(label, value):
    """v30 (#10) — borne de sanité : un « préc. 1,1% » sur un PPI m/m
    (≈ 14% annualisé) est presque sûrement un artefact de source. Au-delà de
    ±1,5% sur un indicateur m/m, la valeur est marquée « à vérifier »."""
    try:
        lbl = str(label or "").lower()
        if "m/m" not in lbl:
            return value
        v = float(str(value).replace("%", "").replace(",", ".").strip())
        if abs(v) > 1.5:
            return f"{value} (à vérifier)"
    except (TypeError, ValueError):
        return value
    return value
