"""Mémoire inter-rapports persistée sur disque (commitée par les workflows).

Permet au rapport du soir de lire le matin, au matin de lire le soir précédent,
et à l'hebdo d'agréger la semaine. Tous les fichiers vivent dans ``state/`` à
la racine du repo et sont commités avec ``[skip ci]``.

Robustesse : toute lecture d'un fichier absent/corrompu renvoie un défaut sûr
plutôt que de lever, pour ne jamais bloquer un rapport.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Racine du repo = parent de src/. state/ est à la racine.
_STATE_DIR = Path(__file__).resolve().parents[2] / "state"

MORNING_FILE = "last_morning_report.json"
EVENING_FILE = "last_evening_report.json"
WEEKLY_FILE = "last_weekly_report.json"
ACTIVE_RECOS_FILE = "active_recommendations.json"
PREDICTION_HISTORY_FILE = "prediction_history.json"
RECO_CHANGES_FILE = "reco_changes.json"
WEEKLY_SNAPSHOTS_FILE = "weekly_snapshots.json"
SOURCE_HEALTH_FILE = "source_health.json"


def _path(name: str) -> Path:
    return _STATE_DIR / name


def _read(name: str, default: Any) -> Any:
    """Lit un JSON de state, renvoie ``default`` si absent ou illisible."""
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("State illisible %s : %s — défaut utilisé.", name, exc)
        return default


def _write(name: str, data: Any) -> None:
    """Écrit un JSON de state (crée le dossier au besoin)."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _path(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Échec écriture state %s : %s", name, exc)


def now_iso() -> str:
    """Timestamp ISO UTC courant."""
    return datetime.now(timezone.utc).isoformat()


# --------------------------- rapports --------------------------------------- #
def save_morning_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport du matin (lu par le soir)."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(MORNING_FILE, payload)


def load_morning_report() -> dict[str, Any]:
    """Charge le dernier rapport du matin (``{}`` si absent)."""
    return _read(MORNING_FILE, {})


def save_evening_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport du soir (lu par le matin suivant)."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(EVENING_FILE, payload)


def load_evening_report() -> dict[str, Any]:
    """Charge le dernier rapport du soir (``{}`` si absent)."""
    return _read(EVENING_FILE, {})


def save_weekly_report(payload: dict[str, Any]) -> None:
    """Persiste le rapport hebdo."""
    payload = dict(payload)
    payload.setdefault("_saved_at", now_iso())
    _write(WEEKLY_FILE, payload)


def load_weekly_report() -> dict[str, Any]:
    """Charge le dernier rapport hebdo (``{}`` si absent)."""
    return _read(WEEKLY_FILE, {})


# --------------------------- recommandations -------------------------------- #
def load_active_recommendations() -> list[dict[str, Any]]:
    """Charge les recommandations actives (non clôturées)."""
    return _read(ACTIVE_RECOS_FILE, [])


def save_active_recommendations(recos: list[dict[str, Any]]) -> None:
    """Sauvegarde la liste des recommandations actives."""
    _write(ACTIVE_RECOS_FILE, recos)


def add_recommendation(reco: dict[str, Any]) -> None:
    """Ajoute une nouvelle reco à la liste active (dédupliquée par id).

    V6 — versioning : si une reco active existe déjà pour le même asset mais avec
    une action DIFFÉRENTE, on enregistre la transition dans ``reco_changes`` (qui
    garde l'historique des changements d'avis avec leur raisonnement) avant de
    remplacer l'ancienne reco.
    """
    recos = load_active_recommendations()
    existing_ids = {r.get("id") for r in recos}
    if reco.get("id") in existing_ids:
        logger.info("Reco %s déjà active, ignorée.", reco.get("id"))
        return

    asset = reco.get("asset")
    new_action = (reco.get("action") or "").upper()
    kept: list[dict[str, Any]] = []
    for r in recos:
        if r.get("asset") == asset and (r.get("action") or "").upper() != new_action:
            # Changement d'avis détecté : on archive la transition.
            record_reco_change(
                asset=asset,
                from_action=(r.get("action") or "").upper(),
                to_action=new_action,
                reason=reco.get("change_reason") or reco.get("rationale")
                or "réévaluation sur nouveaux signaux",
                signals=reco.get("signals_summary"),
                from_date=r.get("created_at"),
            )
        else:
            kept.append(r)
    reco.setdefault("created_at", now_iso())
    reco.setdefault("status", "in_progress")
    kept.append(reco)
    save_active_recommendations(kept)


# --------------------------- versioning des recos (V6) ---------------------- #
def load_reco_changes() -> list[dict[str, Any]]:
    """Charge l'historique des changements d'avis (RENFORCER->ALLÉGER, etc.)."""
    return _read(RECO_CHANGES_FILE, [])


def record_reco_change(
    asset: str,
    from_action: str,
    to_action: str,
    reason: str,
    signals: Any = None,
    from_date: str | None = None,
) -> None:
    """Enregistre un changement d'avis sur un asset, avec son raisonnement.

    Permet au rapport d'expliquer "j'ai dit RENFORCER lundi, j'ALLÈGE mercredi
    parce que tel signal a changé" plutôt que de présenter une reco sans contexte.
    ``from_date`` est la date d'émission de la reco d'origine (ISO), convertie en
    libellé court JJ/MM pour l'affichage.
    """
    from_date_short = None
    if from_date:
        try:
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(from_date)
            from_date_short = dt.strftime("%d/%m")
        except (ValueError, TypeError):
            from_date_short = None
    changes = load_reco_changes()
    changes.append(
        {
            "asset": asset,
            "from_action": from_action,
            "to_action": to_action,
            "reason": reason,
            "signals": signals,
            "from_date": from_date_short,
            "changed_at": now_iso(),
        }
    )
    # On garde les 50 derniers changements (anti-gonflement du fichier).
    _write(RECO_CHANGES_FILE, changes[-50:])


def recent_reco_changes(days: int = 7) -> list[dict[str, Any]]:
    """Renvoie les changements d'avis des N derniers jours."""
    import datetime as _dt

    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    out = []
    for c in load_reco_changes():
        ts = c.get("changed_at")
        try:
            when = _dt.datetime.fromisoformat(ts) if ts else None
        except ValueError:
            when = None
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        if when is None or when >= cutoff:
            out.append(c)
    return out


# --------------------------- historique prédictions ------------------------- #
def load_prediction_history() -> list[dict[str, Any]]:
    """Charge l'historique complet des prédictions (clôturées + en cours)."""
    return _read(PREDICTION_HISTORY_FILE, [])


def save_prediction_history(history: list[dict[str, Any]]) -> None:
    """Sauvegarde l'historique des prédictions."""
    _write(PREDICTION_HISTORY_FILE, history)


# --------------------------- snapshots hebdomadaires (V6) ------------------- #
def load_weekly_snapshots() -> list[dict[str, Any]]:
    """Charge l'historique des snapshots hebdomadaires du portefeuille.

    Chaque snapshot : ``{date, value_usd, btc_price, week_label}``. Sert à
    tracer l'évolution du PTF (H7) et la comparaison vs BTC hold (H6).
    """
    return _read(WEEKLY_SNAPSHOTS_FILE, [])


def record_weekly_snapshot(
    value_usd: float, btc_price: float | None, week_label: str | None = None,
    drawdown_ath_pct: float | None = None,
) -> None:
    """Enregistre un snapshot hebdomadaire (valeur PTF + prix BTC + drawdown).

    Déduplique par semaine ISO : un seul snapshot par semaine (le dernier écrase).
    Garde les 12 dernières semaines. Le drawdown stocké permet de calculer la
    variation de drawdown semaine vs semaine (champ ``drawdown_change_pts``).
    """
    import datetime as _dt

    snaps = load_weekly_snapshots()
    now = _dt.datetime.now(_dt.timezone.utc)
    iso_week = now.strftime("%G-W%V")
    label = week_label or now.strftime("S%V")
    # Retire un éventuel snapshot de la même semaine ISO.
    snaps = [s for s in snaps if s.get("iso_week") != iso_week]
    snaps.append(
        {
            "iso_week": iso_week,
            "week_label": label,
            "date": now_iso(),
            "value_usd": round(value_usd, 2) if value_usd is not None else None,
            "btc_price": round(btc_price, 2) if btc_price else None,
            "drawdown_ath_pct": round(drawdown_ath_pct, 1) if drawdown_ath_pct is not None else None,
        }
    )
    # Tri chronologique + garde 12 semaines.
    snaps.sort(key=lambda s: s.get("iso_week", ""))
    _write(WEEKLY_SNAPSHOTS_FILE, snaps[-12:])


# --------------------------- santé des sources (angles morts) --------------- #
def record_source_health(all_sources: list[str], active_sources: list[str]) -> None:
    """Enregistre quelles sources étaient actives/indisponibles lors d'un run.

    Garde un log daté pour calculer les indispos récurrentes sur la semaine
    (section hebdo "angles morts récurrents"). Conserve 30 jours de logs.
    """
    import datetime as _dt

    logs = _read(SOURCE_HEALTH_FILE, [])
    down = [s for s in all_sources if s not in active_sources]
    logs.append({"date": now_iso(), "down": down})
    # Garde 30 jours.
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
    kept = []
    for entry in logs:
        try:
            when = _dt.datetime.fromisoformat(entry.get("date", ""))
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.timezone.utc)
            if when >= cutoff:
                kept.append(entry)
        except (ValueError, TypeError):
            kept.append(entry)
    _write(SOURCE_HEALTH_FILE, kept[-120:])


def compute_blind_spots_weekly() -> dict[str, Any]:
    """Compte les indispos de sources sur 7j (vs 7j précédents).

    Returns:
        Dict ``{available, items: [{source, days_down, prev_days_down, note}],
        reading}``.
    """
    import datetime as _dt

    logs = _read(SOURCE_HEALTH_FILE, [])
    if not logs:
        return {"available": False}

    now = _dt.datetime.now(_dt.timezone.utc)
    week1_start = now - _dt.timedelta(days=7)
    week2_start = now - _dt.timedelta(days=14)

    def _count_down(start, end):
        counts: dict[str, set] = {}
        for entry in logs:
            try:
                when = _dt.datetime.fromisoformat(entry.get("date", ""))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
            except (ValueError, TypeError):
                continue
            if start <= when < end:
                day_key = when.strftime("%Y-%m-%d")
                for src in entry.get("down", []):
                    counts.setdefault(src, set()).add(day_key)
        return {src: len(days) for src, days in counts.items()}

    this_week = _count_down(week1_start, now)
    prev_week = _count_down(week2_start, week1_start)

    # B10 — fiabilité statistique : ne rien conclure tant qu'on n'a pas observé
    # une semaine COMPLÈTE de runs. Avec 1-2 jours de logs, « 2 j/7 » est du
    # bruit trompeur. On exige >= 7 jours distincts d'observation sur la fenêtre.
    observed_days = set()
    for entry in logs:
        try:
            when = _dt.datetime.fromisoformat(entry.get("date", ""))
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        if week1_start <= when < now:
            observed_days.add(when.strftime("%Y-%m-%d"))
    if len(observed_days) < 7:
        return {"available": False, "observed_days": len(observed_days)}

    if not this_week:
        return {"available": False}

    items = []
    for src, days in sorted(this_week.items(), key=lambda x: -x[1]):
        if days < 2:  # on ne signale que les indispos récurrentes (≥2 jours)
            continue
        prev = prev_week.get(src)
        note = None
        if prev is not None and days > prev:
            note = "dégradation vs semaine précédente"
        items.append(
            {"source": src, "days_down": days, "prev_days_down": prev, "note": note}
        )

    if not items:
        return {"available": False}

    worst = items[0]
    reading = (
        f"{worst['source']} est la lacune la plus récurrente ({worst['days_down']} j/7). "
        "Si la tendance persiste, envisager une source de remplacement."
    )
    return {"available": True, "entries": items, "reading": reading}
