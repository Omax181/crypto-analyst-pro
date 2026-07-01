"""Mémoire inter-rapports persistée sur disque (commitée par les workflows).

Permet au rapport du soir de lire le matin, au matin de lire le soir précédent,
et à l'hebdo d'agréger la semaine. Tous les fichiers vivent dans ``state/`` à
la racine du repo et sont commités avec ``[skip ci]``.

Robustesse : toute lecture d'un fichier absent/corrompu renvoie un défaut sûr
plutôt que de lever, pour ne jamais bloquer un rapport.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
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
SEEN_NEWS_FILE = "seen_news.json"  # v18 (M-A10) — dédup news multi-runs
RECO_DISMISSALS_FILE = "reco_dismissals.json"  # v19 (Partie 6) — recos écartées via le bot
TELEGRAM_OFFSET_FILE = "telegram_offset.json"  # v18 (G) — dernier update_id traité
TELEGRAM_HISTORY_FILE = "telegram_history.json"  # v18 (G) — mémoire conversationnelle
BOT_MEMORY_FILE = "bot_memory.json"  # v21 — mémoire DURABLE (décisions, notes, seuils)


def _path(name: str) -> Path:
    return _STATE_DIR / name


# ---------------------------------------------------------------------------
# M-A1 (v18) — RESET de l'historique au premier run v18.
# Le code v18 doit repartir À ZÉRO : recos passées, win rate, scoring et
# snapshots hérités de v17 sont effacés une seule fois. Une sentinelle évite
# de re-wiper aux runs suivants. Les rapports (morning/evening/weekly) NE sont
# PAS effacés (le soir a besoin du matin du jour) — seul l'historique de
# performance (recos/scoring/snapshots) est remis à zéro.
# ---------------------------------------------------------------------------
_RESET_SENTINEL = "_v18_reset_done.flag"
_RESET_TARGETS = [
    ACTIVE_RECOS_FILE,
    PREDICTION_HISTORY_FILE,
    RECO_CHANGES_FILE,
    WEEKLY_SNAPSHOTS_FILE,
    SOURCE_HEALTH_FILE,
    SEEN_NEWS_FILE,
    RECO_DISMISSALS_FILE,
]


def ensure_v18_reset() -> None:
    """Efface l'historique de performance une seule fois (premier run v18).

    Idempotent : si la sentinelle existe déjà, ne fait rien. Appelée au
    démarrage de chaque run (morning/evening/weekly) avant toute lecture.
    """
    sentinel = _path(_RESET_SENTINEL)
    if sentinel.exists():
        return
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    wiped = []
    for name in _RESET_TARGETS:
        p = _path(name)
        if p.exists():
            try:
                p.unlink()
                wiped.append(name)
            except OSError as exc:  # noqa: BLE001
                logger.warning("Reset v18 : échec suppression %s : %s", name, exc)
    try:
        sentinel.write_text(
            json.dumps({"reset_at": datetime.now(timezone.utc).isoformat(),
                        "version": "v18", "wiped": wiped}),
            encoding="utf-8",
        )
    except OSError as exc:  # noqa: BLE001
        logger.error("Reset v18 : échec écriture sentinelle : %s", exc)
    logger.info("Reset v18 effectué : historique de performance remis à zéro (%s fichiers).", len(wiped))


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
    """Écrit un JSON de state de façon ATOMIQUE (crée le dossier au besoin).

    Écrit d'abord dans un fichier temporaire du même dossier puis le renomme :
    ``os.replace`` est atomique sur le même système de fichiers. Si le process
    est tué en cours d'écriture, le fichier de destination reste intact (ancienne
    version) au lieu d'être tronqué/corrompu — protection des données financières
    (recos, historique de prédictions).
    """
    import os
    import tempfile

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        # Fichier temporaire dans le MÊME dossier (pour que os.replace soit atomique).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(_STATE_DIR), prefix=f".{name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(_path(name)))
        except BaseException:
            # Nettoie le temporaire en cas d'échec (y compris interruption).
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
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
    """Ajoute une nouvelle reco à la liste active, dédupliquée par actif+action.

    V14 — correctif anti-doublons : l'id est ``{asset}-{date}-{action}`` donc un
    même actif recommandé plusieurs jours d'affilée créait autant d'entrées
    (BTC-05, BTC-06, BTC-08…), gonflant artificiellement le tracker (le soir les
    listait toutes, l'hebdo en scorait 11 en 3 jours). Désormais :
      - si une reco OUVERTE existe déjà pour le même actif ET la même action,
        on NE crée PAS de doublon — on conserve la PREMIÈRE (avec son prix
        d'entrée d'origine, ce qui est la référence correcte pour le scoring) ;
      - si l'action DIFFÈRE (RENFORCER -> ALLÉGER), on archive la transition
        dans ``reco_changes`` puis on remplace l'ancienne reco.
    """
    recos = load_active_recommendations()
    asset = reco.get("asset")
    new_action = (reco.get("action") or "").upper()

    # v15 (audit Omar, evening P0 partie B / weekly « la dernière reco prime ») :
    # même actif + même action déjà OUVERTE -> on MET À JOUR la reco existante
    # avec le contenu le plus récent (confiance, rationale, prix signal du jour)
    # au lieu de l'ignorer. On préserve VOLONTAIREMENT deux ancrages :
    #   - entry_price : prix de la PREMIÈRE émission — sinon la cible +10%/-8%
    #     se ré-ancrerait chaque matin et le win rate deviendrait inatteignable
    #     (biais structurel) ;
    #   - created_at : date de première émission — sinon la fenêtre de 30j
    #     glisserait indéfiniment et aucune reco ne serait jamais invalidée.
    # Tout le reste reflète la dernière émission (« on est à jour »), et
    # ``reissues`` compte les ré-émissions pour la transparence.
    for r in recos:
        if (
            r.get("asset") == asset
            and (r.get("action") or "").upper() == new_action
            and (r.get("status") or "in_progress") == "in_progress"
        ):
            preserved_entry = r.get("entry_price")
            preserved_created = r.get("created_at")
            for k, v in reco.items():
                if k in ("entry_price", "created_at", "id"):
                    continue
                if v is not None:
                    r[k] = v
            if preserved_entry is not None:
                r["entry_price"] = preserved_entry
            if preserved_created:
                r["created_at"] = preserved_created
            r["last_issued_at"] = now_iso()
            r["reissues"] = int(r.get("reissues") or 0) + 1
            save_active_recommendations(recos)
            logger.info(
                "Reco %s %s ré-émise : contenu mis à jour (entrée d'origine "
                "conservée pour le scoring, ré-émission n°%d).",
                asset, new_action, r["reissues"],
            )
            return

    existing_ids = {r.get("id") for r in recos}
    if reco.get("id") in existing_ids:
        logger.info("Reco %s déjà active, ignorée.", reco.get("id"))
        return

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


# --------------------- v19 (Partie 6) — dismissals via le bot --------------- #
def load_reco_dismissals() -> list[dict[str, Any]]:
    """Recos écartées manuellement par Omar via le bot (/dismiss)."""
    return _read(RECO_DISMISSALS_FILE, [])


def record_reco_dismissal(asset: str, action: str | None = None,
                          reco_id: str | None = None) -> None:
    """Trace un /dismiss (TRAÇABILITÉ Partie 6) + alimente l'anti-ré-émission.

    Sans cette trace, une reco écartée disparaissait sans historique ET pouvait
    être ré-émise dès le lendemain (incohérence). On garde les 50 dernières.
    """
    items = load_reco_dismissals()
    items.append({
        "asset": (asset or "").upper(),
        "action": ((action or "").upper() or None),
        "reco_id": reco_id,
        "dismissed_at": now_iso(),
    })
    _write(RECO_DISMISSALS_FILE, items[-50:])


def is_recently_dismissed(asset: str, action: str | None = None,
                          days: int = 2) -> bool:
    """L'actif a-t-il été écarté récemment (même action) ? Anti ré-émission.

    Évite que le matin ré-émette IMMÉDIATEMENT une reco qu'Omar vient d'écarter.
    Après ``days`` jours, l'émission redevient possible (le marché a évolué).
    """
    import datetime as _dt

    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    a_up = (asset or "").upper()
    act_up = (action or "").upper() or None
    for it in load_reco_dismissals():
        if it.get("asset") != a_up:
            continue
        if act_up and it.get("action") and it["action"] != act_up:
            continue
        ts = it.get("dismissed_at")
        if not ts:
            continue
        try:
            d = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            if d >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    return False


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


def load_recent_theses(limit: int = 12) -> list[dict[str, Any]]:
    """v18 (Chantier E #16) — thèses récentes avec leur direction.

    Dérive depuis l'historique des prédictions une liste compacte
    ``{asset, action_type, created_at}`` triée du plus récent au plus ancien.
    Sert au garde-fou « biais de confirmation » (3 dernières thèses même sens).
    La direction est inférée de l'action : RENFORCER/ACHETER → bullish,
    ALLÉGER/VENDRE → bearish.

    Args:
        limit: nombre maximum d'entrées retournées.

    Returns:
        Liste de dicts ``{asset, action_type, created_at}``.
    """
    history = _read(PREDICTION_HISTORY_FILE, [])

    def _sort_key(p: dict[str, Any]) -> str:
        return p.get("created_at") or ""

    out: list[dict[str, Any]] = []
    for p in sorted(history, key=_sort_key, reverse=True):
        action = (p.get("action") or "").upper()
        if "RENFOR" in action or "ACHET" in action or "ACCUM" in action:
            direction = "bullish"
        elif "ALLÉG" in action or "ALLEG" in action or "VEND" in action or "SORT" in action:
            direction = "bearish"
        else:
            continue
        out.append({
            "asset": p.get("asset"),
            "action_type": direction,
            "created_at": p.get("created_at"),
        })
        if len(out) >= limit:
            break
    return out


# --------------------------- snapshots hebdomadaires (V6) ------------------- #
def load_weekly_snapshots() -> list[dict[str, Any]]:
    """Charge l'historique des snapshots hebdomadaires du portefeuille.

    Chaque snapshot : ``{date, value_usd, btc_price, week_label}``. Sert à
    tracer l'évolution du PTF (H7) et la comparaison vs BTC hold (H6).
    """
    return _read(WEEKLY_SNAPSHOTS_FILE, [])


def record_weekly_snapshot(
    value_usd: float, btc_price: float | None, week_label: str | None = None,
    drawdown_ath_pct: float | None = None, quality_score: float | None = None,
    vix: float | None = None, fear_greed: float | None = None,
    dxy: float | None = None, diversification_score: float | None = None,
) -> None:
    """Enregistre un snapshot hebdomadaire (valeur PTF + prix BTC + drawdown).

    Déduplique par semaine ISO : un seul snapshot par semaine (le dernier écrase).
    Garde les 12 dernières semaines. Le drawdown stocké permet de calculer la
    variation de drawdown semaine vs semaine (champ ``drawdown_change_pts``) ;
    ``quality_score`` (v15) permet l'évolution WoW du score qualité PTF.
    v18 (Chantier E #8) : ``vix``/``fear_greed``/``dxy`` permettent la mémoire des
    contextes macro similaires (compute_similar_context).
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
            "quality_score": round(quality_score, 1) if quality_score is not None else None,
            # v19/W-A10 : score de diversification stocké pour la comparaison N-1.
            "diversification_score": (round(diversification_score, 1)
                                      if diversification_score is not None else None),
            "vix": round(vix, 1) if isinstance(vix, (int, float)) else None,
            "fear_greed": round(fear_greed) if isinstance(fear_greed, (int, float)) else None,
            "dxy": round(dxy, 2) if isinstance(dxy, (int, float)) else None,
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


def compute_weekly_source_stats(total_sources: int) -> dict[str, Any]:
    """Sources actives RÉELLES sur la semaine écoulée (pour le header hebdo).

    v15 (audit weekly P0) : « Sources actives 4/23 » affichait le compte du
    seul run hebdo (6 sources max interrogées le dimanche) alors que le matin
    même en avait 16/23 — chiffre trompeur qui faisait croire à une collecte
    en panne. On calcule désormais, depuis ``source_health.json`` (alimenté à
    chaque run matin), la MOYENNE quotidienne de sources actives sur 7 jours
    + le meilleur jour, ce qui reflète la réalité de la semaine.

    Returns:
        ``{available, avg_active, best_active, days_observed, total}``.
    """
    import datetime as _dt

    logs = _read(SOURCE_HEALTH_FILE, [])
    if not logs or total_sources <= 0:
        return {"available": False}
    now = _dt.datetime.now(_dt.timezone.utc)
    start = now - _dt.timedelta(days=7)
    per_day_active: dict[str, int] = {}
    for entry in logs:
        try:
            when = _dt.datetime.fromisoformat(entry.get("date", ""))
            if when.tzinfo is None:
                when = when.replace(tzinfo=_dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        if not (start <= when < now):
            continue
        day = when.strftime("%Y-%m-%d")
        active = total_sources - len(entry.get("down", []))
        # Plusieurs runs/jour (matin+soir) : on garde le meilleur du jour.
        per_day_active[day] = max(per_day_active.get(day, 0), active)
    if not per_day_active:
        return {"available": False}
    vals = list(per_day_active.values())
    return {
        "available": True,
        "avg_active": round(sum(vals) / len(vals)),
        "best_active": max(vals),
        "days_observed": len(vals),
        "total": total_sources,
    }


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


# ---------------------------------------------------------------------------
# M-A10 (v18) — déduplication des news sur plusieurs runs.
# L'audit a vu la même news (« interdiction Claude Fable 5 ») affichée en
# CATALYSEUR sur 2 runs successifs (v16.1 puis v17). On mémorise une signature
# normalisée (titre simplifié) de chaque news affichée, avec sa date, et on
# filtre les news déjà vues récemment au run suivant — SAUF si elles apportent
# un complément (géré en amont). La fenêtre par défaut est 48h.
# ---------------------------------------------------------------------------
def _news_signature(title: str) -> str:
    """Signature normalisée d'un titre de news (pour la dédup multi-runs).

    Minuscules, sans ponctuation ni accents. On retient les mots SIGNIFICATIFS
    (longueur ≥ 4, hors mots vides courants), TRIÉS par ordre alphabétique et
    limités aux 8 plus distinctifs. Insensible à l'ordre et aux reformulations
    de surface. La comparaison fine (conjugaisons, synonymes partiels) se fait
    par RECOUVREMENT via ``news_titles_match`` plutôt que par égalité stricte.
    """
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    _STOP = {
        "les", "des", "une", "un", "la", "le", "de", "du", "et", "en", "au", "aux",
        "pour", "par", "sur", "dans", "avec", "que", "qui", "son", "ses", "leur",
        "the", "and", "for", "with", "from", "this", "that", "are", "was",
        "etats", "unis", "usa",
    }
    words = [w for w in t.split() if len(w) >= 4 and w not in _STOP]
    sig_words = sorted(set(words))[:8]
    return " ".join(sig_words)


def _sig_tokens(sig: str) -> set[str]:
    """Tokens d'une signature, tronqués à 5 lettres (absorbe les conjugaisons).

    « interdisent » et « interdit » partagent le préfixe « interd » → en tronquant
    à 5 lettres (« inter ») ils convergent. Réduit les ratés dus aux flexions.
    """
    return {w[:5] for w in (sig or "").split() if w}


def news_titles_match(title_a: str, title_b: str, threshold: float = 0.55) -> bool:
    """Deux titres décrivent-ils le même évènement ? (recouvrement de tokens).

    Calcule un indice de recouvrement (intersection / plus petit ensemble) sur
    les tokens significatifs tronqués. ≥ threshold ⇒ même évènement (doublon).
    """
    ta, tb = _sig_tokens(_news_signature(title_a)), _sig_tokens(_news_signature(title_b))
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    overlap = inter / min(len(ta), len(tb))
    return overlap >= threshold


def is_news_seen(title: str, seen_sigs: set[str], threshold: float = 0.55) -> bool:
    """Le titre correspond-il à une news déjà vue (parmi les signatures) ?

    Compare par recouvrement de tokens (tolère conjugaisons/reformulations).
    """
    cand = _sig_tokens(_news_signature(title))
    if not cand:
        return False
    for sig in seen_sigs:
        ref = _sig_tokens(sig)
        if not ref:
            continue
        if len(cand & ref) / min(len(cand), len(ref)) >= threshold:
            return True
    return False


def load_seen_news(hours: int = 48) -> set[str]:
    """Renvoie l'ensemble des signatures de news vues dans la fenêtre donnée.

    Args:
        hours: fenêtre de rétention (défaut 48h).

    Returns:
        Ensemble de signatures (str) encore valides.
    """
    raw = _read(SEEN_NEWS_FILE, [])
    if not isinstance(raw, list):
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sig = entry.get("sig")
        seen_at = entry.get("seen_at")
        if not sig:
            continue
        try:
            ts = datetime.fromisoformat(str(seen_at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out.add(sig)
    return out


def record_seen_news(titles: list[str], retention_hours: int = 96) -> None:
    """Enregistre les signatures des news affichées (purge au-delà de la rétention).

    Args:
        titles: titres des news effectivement affichées ce run.
        retention_hours: au-delà, les entrées sont purgées (défaut 96h).
    """
    raw = _read(SEEN_NEWS_FILE, [])
    if not isinstance(raw, list):
        raw = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    kept: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("sig"):
            continue
        try:
            ts = datetime.fromisoformat(str(entry.get("seen_at")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            kept.append(entry)
    now = now_iso()
    existing_sigs = {e["sig"] for e in kept}
    for title in titles or []:
        sig = _news_signature(title)
        if sig and sig not in existing_sigs:
            kept.append({"sig": sig, "seen_at": now})
            existing_sigs.add(sig)
    _write(SEEN_NEWS_FILE, kept)


# --------------------------- bot Telegram (v18 / Chantier G) ----------------- #
def load_telegram_offset() -> int:
    """Dernier update_id Telegram traité + 1 (0 si jamais lancé)."""
    data = _read(TELEGRAM_OFFSET_FILE, {})
    try:
        return int(data.get("offset", 0))
    except (ValueError, TypeError):
        return 0


def save_telegram_offset(offset: int) -> None:
    """Persiste l'offset Telegram (ne jamais retraiter un message déjà vu)."""
    _write(TELEGRAM_OFFSET_FILE, {"offset": int(offset), "updated_at": now_iso()})


def load_telegram_history(limit: int = 12) -> list[dict[str, Any]]:
    """Charge les N derniers tours de conversation (mémoire conversationnelle).

    Returns:
        Liste ``[{role: 'user'|'assistant', content: str, at: iso}]``.
    """
    hist = _read(TELEGRAM_HISTORY_FILE, [])
    return hist[-limit:] if isinstance(hist, list) else []


def append_telegram_turn(role: str, content: str, *, max_keep: int = 40) -> None:
    """Ajoute un tour de conversation à l'historique (rotation à max_keep).

    Args:
        role: 'user' ou 'assistant'.
        content: texte du message.
        max_keep: nombre de tours conservés (au-delà, on tronque le plus ancien).
    """
    hist = _read(TELEGRAM_HISTORY_FILE, [])
    if not isinstance(hist, list):
        hist = []
    hist.append({"role": role, "content": content, "at": now_iso()})
    if len(hist) > max_keep:
        hist = hist[-max_keep:]
    _write(TELEGRAM_HISTORY_FILE, hist)


# ---------------------------------------------------------------------------
# v21 — MÉMOIRE DURABLE du bot (au-delà de l'historique de conversation).
# Faits persistants qu'Omar veut que le bot retienne dans la durée : ses
# décisions (achats/ventes, recos écartées/validées), ses seuils, ses notes.
# Capturés de façon DÉTERMINISTE (jamais par extraction IA → zéro hallucination) :
# automatiquement à chaque action (édition de portefeuille, action sur reco) et
# manuellement via /remember. Injectés dans le contexte du bot pour assurer la
# continuité et éviter les répétitions.
# ---------------------------------------------------------------------------
def load_bot_memory(limit: int = 0) -> list[dict[str, Any]]:
    """Charge la mémoire durable (liste ``[{ts, kind, text}]``).

    Args:
        limit: si > 0, ne renvoie que les ``limit`` entrées les plus récentes.
    """
    mems = _read(BOT_MEMORY_FILE, [])
    if not isinstance(mems, list):
        return []
    return mems[-limit:] if limit and limit > 0 else mems


def append_bot_memory(kind: str, text: str, *, max_keep: int = 200) -> None:
    """Ajoute un fait durable (rotation à max_keep). No-op si texte vide.

    Args:
        kind: 'decision' (action portefeuille/reco), 'note' (saisie manuelle),
            'preference' (préférence exprimée).
        text: contenu du fait.
    """
    text = (text or "").strip()
    if not text:
        return
    mems = _read(BOT_MEMORY_FILE, [])
    if not isinstance(mems, list):
        mems = []
    mems.append({"ts": now_iso(), "kind": kind, "text": text})
    if len(mems) > max_keep:
        mems = mems[-max_keep:]
    _write(BOT_MEMORY_FILE, mems)


def remove_bot_memory(index: int) -> bool:
    """Supprime l'entrée n° ``index`` (0-based). True si supprimée."""
    mems = _read(BOT_MEMORY_FILE, [])
    if isinstance(mems, list) and 0 <= index < len(mems):
        mems.pop(index)
        _write(BOT_MEMORY_FILE, mems)
        return True
    return False
