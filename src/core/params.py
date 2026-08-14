"""Paramètres métier — SPEC V31 §9.

RÈGLE ABSOLUE (§9.3) : un paramètre métier absent produit EXACTEMENT le
comportement spécifié au tableau §9.2. Jamais de valeur par défaut silencieuse,
jamais de substitution implicite. ``NON_EVALUABLE`` n'est jamais assimilé à
``VIABLE``, par aucun chemin d'appel.

Ce module est la SEULE porte d'entrée des paramètres métier. Aucun autre module
n'a le droit de coder en dur une valeur figurant ici.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from src.utils.logger import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "params.yaml"

# Paramètres dont l'absence rend TOUT verdict de viabilité NON_EVALUABLE (§9.2).
# L'ordre est celui de la SPEC ; il est repris tel quel dans ``missing_inputs``.
EMISSION_BLOCKING: tuple[str, ...] = (
    "fee_rate",
    "liquidity_bands",
    "delta_claimable",
    "p_target_max",
    "p_stop_max",
    "materiality_reference",
    "k3",
    "monthly_budget",
    "ticket_min",
)

# Paramètres dont l'absence désactive une fonction périphérique SANS impacter
# l'émission (§9.2). La désactivation est explicite, jamais un défaut.
FEATURE_GATED: dict[str, str] = {
    "n_min": "metrics",            # aucune métrique publiée
    "watchdog": "watchdog",        # chien de garde désactivé
    "retention_months": "archival",  # archivage désactivé, alarme maintenue
    "cooldown_days": "cooldown",   # contrainte (b) désactivée, (a) et (c) actives
    "historical_treatment": "migration",  # migration bloquée
}

_lock = threading.Lock()
_cache: Optional[dict[str, Any]] = None


def _load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        raw: dict[str, Any] = {}
        if _CONFIG_PATH.exists():
            try:
                loaded = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except (yaml.YAMLError, OSError) as exc:
                logger.error(
                    "params.yaml illisible (%s) — TOUS les paramètres métier sont "
                    "traités comme ABSENTS.", exc)
                raw = {}
        else:
            logger.warning(
                "config/params.yaml absent — tous les paramètres métier sont "
                "ABSENTS (comportement SPEC §9.3).")
        _cache = raw
        return _cache


def reset_cache() -> None:
    """Vide le cache (tests uniquement)."""
    global _cache
    with _lock:
        _cache = None


def get(name: str) -> Optional[Any]:
    """Valeur d'un paramètre métier, ou ``None`` s'il est absent.

    ``None`` signifie ABSENT au sens de la SPEC : aucun appelant n'a le droit de
    lui substituer une valeur. Une valeur présente mais vide (``""``, ``[]``,
    ``{}``) est traitée comme ABSENTE — un paramètre à moitié rempli est un
    paramètre manquant.
    """
    val = _load().get(name)
    if val is None:
        return None
    if isinstance(val, (str, list, dict)) and len(val) == 0:
        return None
    return val


# ── cohérence structurelle (§9.4) ─────────────────────────────────────────
#
# Une valeur PRÉSENTE mais INCOHÉRENTE est plus dangereuse qu'une valeur
# absente : elle produit un verdict VIABLE sur une base fausse, sans rien
# signaler. Elle est donc traitée EXACTEMENT comme absente — NON_EVALUABLE,
# motif explicite, mail envoyé. Aucune valeur n'est « corrigée » au passage :
# ce serait inventer un paramètre métier.

MATERIALITY_REFERENCES = ("monthly_budget", "ptf_daily_noise", "ticket_min")


def _incoherent() -> dict[str, str]:
    """Paramètres présents mais structurellement invalides. Ne lève jamais."""
    bad: dict[str, str] = {}
    raw = _load()

    def num(name):
        v = raw.get(name)
        return float(v) if isinstance(v, (int, float)) and not isinstance(
            v, bool) else None

    fee = num("fee_rate")
    if fee is not None and not (0.0 <= fee < 0.05):
        bad["fee_rate"] = "frais par jambe hors de [0 %, 5 %["

    pt, ps = num("p_target_max"), num("p_stop_max")
    for name, v in (("p_target_max", pt), ("p_stop_max", ps)):
        if v is not None and not (0.0 < v < 1.0):
            bad[name] = "probabilité hors de ]0, 1["
    if pt is not None and ps is not None and "p_stop_max" not in bad \
            and "p_target_max" not in bad and ps >= pt:
        # Tolérer l'inverse reviendrait à accepter qu'une thèse CORRECTE soit
        # plus exposée au bruit que sa propre cible — le contrat serait
        # structurellement perdant avant même d'exister.
        bad["p_stop_max"] = (
            "la probabilité d'invalidation par le bruit doit être STRICTEMENT "
            "inférieure à celle de la cible")

    dc = num("delta_claimable")
    if dc is not None and not (0.0 < dc < 1.0):
        bad["delta_claimable"] = "avantage revendicable hors de ]0, 1["

    k3v = num("k3")
    if k3v is not None and k3v <= 0:
        bad["k3"] = "multiple de matérialité nul ou négatif"

    mb, tm = num("monthly_budget"), num("ticket_min")
    if mb is not None and mb <= 0:
        bad["monthly_budget"] = "budget nul ou négatif"
    if tm is not None and tm <= 0:
        bad["ticket_min"] = "ticket minimum nul ou négatif"
    if mb is not None and tm is not None and "monthly_budget" not in bad \
            and "ticket_min" not in bad and tm > mb:
        bad["ticket_min"] = "ticket minimum supérieur au budget mensuel"

    ref = raw.get("materiality_reference")
    if isinstance(ref, str) and ref and ref not in MATERIALITY_REFERENCES:
        bad["materiality_reference"] = (
            "référence inconnue (attendu : "
            + ", ".join(MATERIALITY_REFERENCES) + ")")

    bands = raw.get("liquidity_bands")
    if isinstance(bands, list) and bands:
        for i, b in enumerate(bands):
            if not isinstance(b, dict):
                bad["liquidity_bands"] = f"bande {i} malformée"
                break
            for key in ("spread_pct", "slippage_pct"):
                v = b.get(key)
                if not isinstance(v, (int, float)) or v < 0:
                    bad["liquidity_bands"] = f"bande {i} : {key} absent ou négatif"
                    break
            if "liquidity_bands" in bad:
                break
    return bad


def incoherent_params() -> dict[str, str]:
    """Diagnostic lisible des paramètres présents mais invalides."""
    return _incoherent()


def missing_emission_params() -> list[str]:
    """Liste ORDONNÉE des paramètres bloquants absents OU incohérents.

    Alimente directement ``ViabilityVerdict.missing_inputs`` : non vide
    <=> verdict NON_EVALUABLE (I20).
    """
    bad = _incoherent()
    return [name for name in EMISSION_BLOCKING
            if get(name) is None or name in bad]


def feature_enabled(feature: str) -> bool:
    """Une fonction périphérique est active ssi son paramètre est présent."""
    for name, feat in FEATURE_GATED.items():
        if feat == feature:
            return get(name) is not None
    return False


def disabled_features() -> list[str]:
    """Fonctions périphériques désactivées faute de paramètre (pour RunSummary)."""
    return sorted({feat for name, feat in FEATURE_GATED.items()
                   if get(name) is None})


# ── accesseurs typés — un par paramètre, aucune valeur en dur ──────────────

def fee_rate() -> Optional[float]:
    """Frais par jambe, en fraction (0.001 = 0,1 %). Absent => NON_EVALUABLE."""
    v = get("fee_rate")
    return float(v) if isinstance(v, (int, float)) else None


def liquidity_bands() -> Optional[list[dict[str, Any]]]:
    """Bandes de liquidité : [{max_notional_usd, spread_pct, slippage_pct}].

    Le classement d'un actif dans une bande est DÉDUCTIBLE (volume/mcap) ; les
    VALEURS de spread et de slippage sont une décision métier.
    """
    v = get("liquidity_bands")
    if not isinstance(v, list) or not v:
        return None
    out: list[dict[str, Any]] = []
    for band in v:
        if not isinstance(band, dict):
            return None
        if band.get("spread_pct") is None or band.get("slippage_pct") is None:
            return None
        out.append(band)
    return out


def delta_claimable() -> Optional[float]:
    """Avantage revendicable sur le hasard, en FRACTION (0.05 = 5 points)."""
    v = get("delta_claimable")
    return float(v) if isinstance(v, (int, float)) else None


def p_target_max() -> Optional[float]:
    """Probabilité maximale que la cible soit touchée par le seul bruit."""
    v = get("p_target_max")
    return float(v) if isinstance(v, (int, float)) else None


def p_stop_max() -> Optional[float]:
    """Probabilité maximale qu'une thèse correcte soit stoppée par le bruit."""
    v = get("p_stop_max")
    return float(v) if isinstance(v, (int, float)) else None


def materiality_reference() -> Optional[str]:
    """Nom de la référence de matérialité (§4.4 V3). Décision métier."""
    v = get("materiality_reference")
    return str(v) if isinstance(v, str) and v else None


def k3() -> Optional[float]:
    """Multiple de la référence de matérialité."""
    v = get("k3")
    return float(v) if isinstance(v, (int, float)) else None


def monthly_budget() -> Optional[float]:
    """Budget de RECOMMANDATION mensuel, en USD (§4.3)."""
    v = get("monthly_budget")
    return float(v) if isinstance(v, (int, float)) else None


def budget_rollover() -> bool:
    """Report du reliquat d'un mois sur l'autre. Absent => pas de report."""
    return bool(get("budget_rollover"))


def ticket_min() -> Optional[float]:
    """Ticket minimum d'exécution, en USD (§4.4 V4)."""
    v = get("ticket_min")
    return float(v) if isinstance(v, (int, float)) else None


def n_min() -> Optional[int]:
    """Plancher d'échantillon des métriques (§7). Absent => rien n'est publié."""
    v = get("n_min")
    return int(v) if isinstance(v, (int, float)) else None


def cooldown_days() -> Optional[int]:
    """Délai de refroidissement après INVALIDATED, contrainte (b) de §3."""
    v = get("cooldown_days")
    return int(v) if isinstance(v, (int, float)) else None


def retention_months() -> Optional[int]:
    """Fenêtre de rétention avant archivage d'un segment (§8)."""
    v = get("retention_months")
    return int(v) if isinstance(v, (int, float)) else None


def historical_treatment() -> Optional[str]:
    """Traitement de l'historique pré-V31 : 'purge' | 'mark' | 'reset'."""
    v = get("historical_treatment")
    return str(v) if isinstance(v, str) and v else None


def watchdog() -> Optional[dict[str, Any]]:
    """{max_silence_hours, channel} — absent => chien de garde désactivé."""
    v = get("watchdog")
    if not isinstance(v, dict):
        return None
    if v.get("max_silence_hours") is None or not v.get("channel"):
        return None
    return v


def publication_latency_days(source_id: str) -> Optional[int]:
    """Latence de publication déclarée d'une source (§6). Absent => DEGRADED."""
    table = get("publication_latency_days")
    if not isinstance(table, dict):
        return None
    v = table.get(source_id)
    return int(v) if isinstance(v, (int, float)) else None
