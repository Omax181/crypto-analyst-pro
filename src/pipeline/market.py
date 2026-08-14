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
from typing import Optional

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
# La demande dépasse volontairement le maximum : CoinGecko tronque à
# l'historique réellement disponible, et une marge évite qu'un unique jour
# manquant fasse basculer un actif sous son ``depth_min``.
_DEPTH_MARGIN_DAYS = 35
DAILY_SERIES_DAYS = max(s.depth_min for s in SPECS.values()
                        if s.enabled) + _DEPTH_MARGIN_DAYS

# Au-delà de 90 jours, CoinGecko sert du JOURNALIER automatiquement : le
# paramètre ``interval`` devient inutile, et l'omettre supprime une dépendance
# à une option restreinte aux plans payants.
_AUTO_DAILY_ABOVE_DAYS = 90


def _utc(ts: Optional[float]) -> Optional[datetime]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def daily_closes(symbol: str, *, days: int = DAILY_SERIES_DAYS) -> sr.SourceResult:
    """Série de clôtures JOURNALIÈRES.

    Au-delà de 90 jours la granularité journalière est automatique ; en deçà,
    elle est imposée explicitement. On ne demande jamais ``interval`` sans en
    avoir besoin : c'est une option restreinte sur le palier gratuit.
    """
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
    return sr.ok(SOURCE_CLOSES,
                 {"closes": closes, "volumes": raw.get("volumes") or []},
                 depth=len(closes))


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
    if len(bars) < 20:
        return sr.degraded(
            SOURCE_OHLC, bars,
            f"seulement {len(bars)} bougies journalières agrégées",
            depth=len(bars), fallback_rank=1)
    return sr.ok(SOURCE_OHLC, bars, depth=len(bars),
                 as_of=_utc(None))


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
