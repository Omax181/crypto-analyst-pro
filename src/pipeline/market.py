"""Chemin MARCHÉ — SPEC V31 Phase 1 (I30 scopé, I31, I22).

Seul point d'accès aux données de marché du chemin décisionnel. Tout y est
enveloppé dans un ``SourceResult`` : statut, as_of, provenance, profondeur.
Les fonctions nues de ``coingecko`` ne sont plus appelées ailleurs.

GRANULARITÉ OHLC — vérification Phase 0 (V1), établie sur le code source :
``coingecko.get_ohlc`` documente « ~4h pour 3-30j, ~4j pour days>=31 ». Une
fenêtre >= 31 jours renverrait donc des bougies PLURI-JOURNALIÈRES et
gonflerait sigma d'un facteur ~2. On appelle donc TOUJOURS days=30 et on
agrège explicitement en bougies journalières (``aggregate_to_daily``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.core import source_result as sr
from src.core.horizon import SPECS
from src.core.volatility import DailyBar, aggregate_to_daily
from src.data_sources import coingecko
from src.utils.logger import get_logger

logger = get_logger(__name__)

SOURCE_CLOSES = "market.closes"
SOURCE_OHLC = "market.ohlc"
SOURCE_SPOT = "market.spot"

# Fenêtre OHLC maximale conservant une granularité intra-journalière.
_OHLC_WINDOW_DAYS = 30

# PROFONDEUR DE SÉRIE JOURNALIÈRE — dérivée, jamais choisie.
#
# Elle vaut le MAXIMUM des ``depth_min`` de tous les horizons ACTIFS. La v31
# initiale demandait 130 jours parce que 130 suffisait au SWING ; POSITION,
# qui en exige 365, était alors déclaré « non fourni par le pipeline » et
# désactivé. C'était une limite auto-infligée : le pipeline dictait la SPEC au
# lieu de la servir.
#
# PLAFOND DU FOURNISSEUR — MESURÉ, PAS SUPPOSÉ.
#
# La v31.0 demandait ``max(depth_min) + 35 = 400`` en supposant que « CoinGecko
# tronque à l'historique réellement disponible ». C'ÉTAIT FAUX. Le palier
# gratuit REFUSE : au premier run réel du 15/08/2026, les 29 actifs ont reçu un
# 401 et le rapport n'a porté AUCUNE analyse. Vérifié ensuite sans clé :
#
#     days=365 -> HTTP 200, 366 points
#     days=366 -> HTTP 401, error_code 10012
#                 « Your request exceeds the allowed time range. »
#
# La frontière est donc exactement 365, et 365 SUFFIT : 366 points couvrent le
# ``depth_min`` de POSITION (365). Il n'y a en revanche AUCUNE marge — un actif
# plus jeune que 365 jours reçoit un refus chiffré, ce qui est le comportement
# voulu, jamais un silence.
#
# Sources plus profondes écartées, mesures à l'appui : Binance est géo-bloqué
# (451) depuis les runners GitHub US — cf. ``binance_futures`` et son repli
# OKX ; OKX ne sert que 22 des 29 actifs et n'a que 47 bougies sur TAO, ce qui
# rendrait σ et niveaux non comparables d'un actif à l'autre.
_PROVIDER_MAX_DAYS = 365
_DEPTH_MARGIN_DAYS = 35
_DEPTH_WANTED = max(s.depth_min for s in SPECS.values()
                    if s.enabled) + _DEPTH_MARGIN_DAYS
DAILY_SERIES_DAYS = min(_DEPTH_WANTED, _PROVIDER_MAX_DAYS)

# Au-delà de 90 jours, CoinGecko sert du JOURNALIER automatiquement : le
# paramètre ``interval`` devient inutile, et l'omettre supprime une dépendance
# à une option restreinte aux plans payants.
_AUTO_DAILY_ABOVE_DAYS = 90


def _utc(ts: Optional[float]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def _utc_ms(ts: Any) -> Optional[datetime]:
    """Epoch en MILLISECONDES (format CoinGecko) -> datetime UTC."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _jour_utc(jour: Any) -> Optional[datetime]:
    """« AAAA-MM-JJ » -> minuit UTC de ce jour."""
    if not isinstance(jour, str) or not jour:
        return None
    try:
        return datetime.strptime(jour[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def daily_closes(symbol: str, *, days: int = DAILY_SERIES_DAYS) -> sr.SourceResult:
    """Série de clôtures JOURNALIÈRES.

    Au-delà de 90 jours la granularité journalière est automatique ; en deçà,
    elle est imposée explicitement. On ne demande jamais ``interval`` sans en
    avoir besoin : c'est une option restreinte sur le palier gratuit.
    """
    # Garde de dernier recours : un appelant ne peut pas demander au-delà de ce
    # que le palier gratuit sert. Dépasser ne « tronque » pas, ça REFUSE (401).
    days = min(int(days), _PROVIDER_MAX_DAYS)
    interval = None if days > _AUTO_DAILY_ABOVE_DAYS else "daily"
    try:
        raw = coingecko.get_price_volume_series(symbol, days=days,
                                                interval=interval)
    except Exception as exc:  # noqa: BLE001
        return sr.classify(SOURCE_CLOSES, exception=exc)
    if not isinstance(raw, dict):
        return sr.unavailable(SOURCE_CLOSES,
                              sr.Failure(exception_class="NoPayload",
                                         retryable=True))
    closes = [float(c) for c in (raw.get("closes") or [])
              if isinstance(c, (int, float)) and c > 0]
    if not closes:
        return sr.empty(SOURCE_CLOSES)
    # FRAÎCHEUR de la série : l'horodatage du dernier point. Les clôtures sont
    # une source BLOQUANTE ; sans `as_of`, le système ne pouvait pas savoir
    # s'il décidait sur une série périmée.
    return sr.ok(SOURCE_CLOSES,
                 {"closes": closes, "volumes": raw.get("volumes") or []},
                 depth=len(closes), as_of=_utc_ms(raw.get("last_ts")))


def daily_bars(symbol: str) -> sr.SourceResult:
    """Bougies JOURNALIÈRES agrégées depuis l'OHLC intra-journalier.

    Renvoie ``DEGRADED`` si l'agrégation produit trop peu de bougies : le
    consommateur bascule alors sur l'estimateur de repli, marqué comme tel.
    """
    try:
        rows = coingecko.get_ohlc_raw(symbol, days=_OHLC_WINDOW_DAYS)
    except Exception as exc:  # noqa: BLE001
        return sr.classify(SOURCE_OHLC, exception=exc)
    if not rows:
        return sr.unavailable(SOURCE_OHLC,
                              sr.Failure(exception_class="NoPayload",
                                         retryable=True))
    bars: list[DailyBar] = aggregate_to_daily(rows)
    # `_utc(None)` valait INCONDITIONNELLEMENT None : du code qui avait l'air
    # de dater la source sans jamais la dater. La dernière bougie porte
    # pourtant son jour (`DailyBar.day`) — c'est lui, la fraîcheur.
    as_of = _jour_utc(bars[-1].day) if bars else None
    if len(bars) < 20:
        return sr.degraded(
            SOURCE_OHLC, bars,
            f"seulement {len(bars)} bougies journalières agrégées",
            depth=len(bars), fallback_rank=1, as_of=as_of)
    return sr.ok(SOURCE_OHLC, bars, depth=len(bars), as_of=as_of)


def spot_prices(symbols: list[str]) -> sr.SourceResult:
    """Prix spot + métadonnées de marché pour la liste de symboles."""
    try:
        data = coingecko.get_market_data(symbols)
    except Exception as exc:  # noqa: BLE001
        return sr.classify(SOURCE_SPOT, exception=exc)
    if not isinstance(data, dict) or not data:
        return sr.empty(SOURCE_SPOT)
    resolved = len(data)
    if resolved < len(symbols):
        return sr.degraded(
            SOURCE_SPOT, data,
            f"{resolved}/{len(symbols)} symboles résolus",
            depth=resolved, fallback_rank=0)
    return sr.ok(SOURCE_SPOT, data, depth=resolved)


def last_complete_daily_close(closes_result: sr.SourceResult) -> Optional[float]:
    """Dernière clôture journalière COMPLÈTE (SPEC §2.4).

    Les transitions d'état ne s'évaluent QUE sur cette valeur : jamais sur le
    prix spot du run, qui est sensible aux mèches intra-journalières.
    """
    if not closes_result.usable:
        return None
    closes = (closes_result.value or {}).get("closes") or []
    return float(closes[-1]) if closes else None
