"""Collecte — SPEC V31 §1.1, §1.2, §6 (I30, I31, I32, I34).

Toute source du projet passe par ici et en ressort dans une enveloppe
``SourceResult``. Aucune fonction nue de ``data_sources`` n'est appelée ailleurs
dans le chemin de production : la couche transport ne détruit plus l'information
d'échec avant que le consommateur puisse en tenir compte (cause racine R5).

Deux comportements normatifs, appliqués ici et nulle part ailleurs :
  - une source marquée DEAD n'est pas rappelée avant sa date de re-sondage ;
  - une source qui lève ou renvoie une charge vide est CLASSÉE (§1.1), jamais
    effondrée sur ``None``.

Le SOIR et l'HEBDO collectent PARTIELLEMENT (SPEC §2.2/§2.3) : ni OHLC, ni
recalcul de niveaux — ces runs ne construisent aucun plan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.core import source_result as sr
from src.core import registry
from src.pipeline import market
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Profondeur du contexte d'actualité présenté au lecteur.
NEWS_LIMIT = 12
CALENDAR_HORIZON_DAYS = 8


def _as_of(value: Any) -> Optional[datetime]:
    """Horodatage de la DONNÉE, lu sur la charge quand la source le fournit."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 0:
        ts = float(value)
        if ts > 1e11:          # millisecondes
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


# Marqueur d'un DEAD hérité de la persistance, par opposition à un DEAD
# nouvellement constaté. La distinction est indispensable : re-marquer un DEAD
# hérité repousserait sa date de re-sondage à chaque run, et la source ne serait
# JAMAIS re-testée — un mécanisme de re-sondage qui ne re-sonde jamais.
SKIPPED_MARKER = "MarkedDead"


def _guarded(source_id: str, fn: Callable[[], Any]) -> tuple[bool, Any,
                                                             Optional[sr.SourceResult]]:
    """Exécute ``fn`` sous le contrat DEAD + classement d'erreur.

    Returns:
        (exécutée, charge, résultat_d_échec). Si ``exécutée`` est False, le
        troisième élément porte le SourceResult à publier tel quel.
    """
    if registry.should_skip(source_id):
        return False, None, sr.dead(
            source_id, sr.Failure(exception_class=SKIPPED_MARKER,
                                  retryable=False))
    try:
        return True, fn(), None
    except Exception as exc:                                    # noqa: BLE001
        res = sr.classify(source_id, exception=exc)
        logger.warning("Source %s indisponible : %s", source_id, exc)
        return False, None, res


def _is_newly_dead(res: sr.SourceResult) -> bool:
    """DEAD constaté PENDANT ce run, et non hérité de la persistance."""
    return (res.status is sr.SourceStatus.DEAD
            and (res.failure is None
                 or res.failure.exception_class != SKIPPED_MARKER))


def _wrap(source_id: str, fn: Callable[[], Any], *,
          extract_as_of: Optional[Callable[[Any], Any]] = None,
          empty_when: Optional[Callable[[Any], bool]] = None,
          degraded_when: Optional[Callable[[Any], Optional[str]]] = None,
          ) -> sr.SourceResult:
    """Enveloppe générique d'une source de CONTEXTE."""
    ran, payload, failure = _guarded(source_id, fn)
    if not ran:
        return failure                                          # type: ignore[return-value]
    if payload is None or (empty_when(payload) if empty_when
                           else not payload):
        return sr.empty(source_id)
    as_of = _as_of(extract_as_of(payload)) if extract_as_of else None
    note = degraded_when(payload) if degraded_when else None
    if note:
        return sr.degraded(source_id, payload, note, as_of=as_of,
                           fallback_rank=1)
    return sr.ok(source_id, payload, as_of=as_of)


# ── chemin marché (bloquant) ──────────────────────────────────────────────

def market_closes(symbols: list[str]) -> dict[str, sr.SourceResult]:
    """Séries de clôtures journalières, une par actif du périmètre."""
    return {s: market.daily_closes(s) for s in symbols}


def market_bars(symbols: list[str]) -> dict[str, sr.SourceResult]:
    """Bougies journalières agrégées. Run du MATIN uniquement."""
    return {s: market.daily_bars(s) for s in symbols}


def market_spot(symbols: list[str]) -> sr.SourceResult:
    return market.spot_prices(symbols)


# ── sources de contexte ───────────────────────────────────────────────────

def fear_greed() -> sr.SourceResult:
    from src.data_sources import fear_greed as mod
    return _wrap("fear_greed", mod.get_fear_greed,
                 extract_as_of=lambda p: p.get("timestamp") or p.get("as_of"),
                 empty_when=lambda p: p.get("value") is None)


def fred_macro() -> sr.SourceResult:
    from src.data_sources import fred as mod
    return _wrap("fred", mod.get_macro,
                 empty_when=lambda p: not isinstance(p, dict) or not p)


def onchain() -> sr.SourceResult:
    """On-chain BTC + ETH. DEGRADED si une seule des deux chaînes répond."""
    from src.data_sources import onchain_btc, onchain_eth

    def _fetch() -> dict[str, Any]:
        return {"BTC": onchain_btc.get_btc_onchain() or {},
                "ETH": onchain_eth.get_eth_onchain() or {}}

    def _degraded(p: dict[str, Any]) -> Optional[str]:
        missing = [k for k, v in p.items() if not v]
        return ("chaîne(s) sans donnée : " + ", ".join(missing)) if missing else None

    return _wrap("onchain", _fetch,
                 empty_when=lambda p: not any(p.values()),
                 degraded_when=_degraded)


def etf_flows() -> sr.SourceResult:
    """Flux ETF. Les deux replis partagent le même parseur (constat d'audit) :
    leur défaillance est CORRÉLÉE, ce que la matrice de santé rend visible."""
    from src.data_sources import etf_flows as mod
    return _wrap("etf_flows", mod.get_etf_flows,
                 extract_as_of=lambda p: p.get("as_of") or p.get("date"),
                 empty_when=lambda p: not p.get("available"))


def prediction_markets() -> sr.SourceResult:
    from src.data_sources import prediction_markets as mod
    return _wrap("polymarket", mod.get_key_markets,
                 empty_when=lambda p: not p.get("markets"))


def news() -> sr.SourceResult:
    from src.data_sources import crypto_rss as mod
    return _wrap("news", lambda: mod.get_news(limit=NEWS_LIMIT),
                 empty_when=lambda p: not p)


def macro_calendar() -> sr.SourceResult:
    from src.data_sources import macro_calendar as mod
    return _wrap(
        "macro_calendar",
        lambda: mod.get_consolidated_calendar(horizon_days=CALENDAR_HORIZON_DAYS),
        empty_when=lambda p: not p.get("events"))


def derivatives(symbols: list[str]) -> sr.SourceResult:
    from src.data_sources import binance_futures as mod

    def _fetch() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for s in symbols:
            try:
                d = mod.get_derivatives(s)
            except Exception:                                   # noqa: BLE001
                continue
            if d and d.get("available"):
                out[s] = d
        return out

    def _degraded(p: dict[str, Any]) -> Optional[str]:
        if len(p) < len(symbols):
            return f"{len(p)}/{len(symbols)} actifs couverts"
        return None

    return _wrap("derivatives", _fetch, degraded_when=_degraded)


def equities() -> sr.SourceResult:
    from src.data_sources import market_prices as mod
    return _wrap("equities", mod.get_macro_quotes_detailed,
                 empty_when=lambda p: not isinstance(p, dict) or not p)


# ── orchestration ─────────────────────────────────────────────────────────

CONTEXT_SOURCES: tuple[str, ...] = (
    "fear_greed", "fred", "onchain", "etf_flows", "polymarket", "news",
    "macro_calendar", "equities",
)


def context(symbols: list[str], *, full: bool) -> dict[str, sr.SourceResult]:
    """Sources de contexte. ``full`` False = collecte partielle (soir, hebdo).

    Le périmètre partiel n'est pas un mode dégradé : le soir et l'hebdo n'ont
    structurellement pas besoin des entrées qui n'alimentent qu'un plan.
    """
    out: dict[str, sr.SourceResult] = {
        "fear_greed": fear_greed(),
        "news": news(),
        "equities": equities(),
    }
    if full:
        out.update({
            "fred": fred_macro(),
            "onchain": onchain(),
            "etf_flows": etf_flows(),
            "polymarket": prediction_markets(),
            "macro_calendar": macro_calendar(),
            "derivatives": derivatives(symbols[:6]),
        })
    for sid, res in out.items():
        if _is_newly_dead(res):
            spec = registry.CATALOG.get(sid) or registry.SourceSpec(sid, sid)
            registry.mark_dead(sid, spec)
            logger.warning("Source %s marquée DEAD, re-sondage dans %d jours.",
                           sid, spec.dead_reprobe_days)
    return out
