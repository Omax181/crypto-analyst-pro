"""Univers, portefeuille et faits de contexte — SPEC V31 §2.1 étapes 1-2.

Ce module construit, DANS L'ORDRE NORMATIF et avant tout appel LLM :
  1. l'univers d'actifs (portefeuille + périmètre de marché) ;
  2. le FactStore complet, seul porteur des nombres destinés au lecteur ;
  3. les spécifications de candidats consommées par ``pipeline.runs``.

Aucun fait n'est créé après le scellement du store (I36 : le LLM consomme, il
ne produit pas). Aucun nombre n'est formaté ici : le formatage appartient au
``Fact``, qui délègue à ``core.formatter`` (I27).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.ai_brain.prompts.investor_profile import CORE_ASSETS
from src.core import source_result as sr
from src.core.book import Direction
from src.core.facts import Unit, UsageRight
from src.core.volatility import DailyBar
from src.pipeline import collect
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_portfolio

logger = get_logger(__name__)

# Actifs présentés dans le contexte de marché même hors portefeuille.
MARKET_REFERENCE = ("BTC", "ETH")


# ── univers ───────────────────────────────────────────────────────────────

class Position:
    """Ligne de portefeuille résolue au prix du run."""

    __slots__ = ("symbol", "cg_key", "tier", "quantity", "pru",
                 "baseline_usd", "price", "value_usd", "weight_pct", "is_core")

    def __init__(self, symbol: str, cg_key: str, info: dict[str, Any]) -> None:
        self.symbol = symbol
        self.cg_key = cg_key
        self.tier = int(info.get("tier") or 3)
        self.quantity = _f(info.get("quantity"))
        self.pru = _f(info.get("pru"))
        self.baseline_usd = _f(info.get("value_usd")) or 0.0
        forced = info.get("core")
        self.is_core = bool(forced) if forced is not None else symbol in CORE_ASSETS
        self.price: Optional[float] = None
        self.value_usd: float = 0.0
        self.weight_pct: Optional[float] = None

    @property
    def pnl_pct(self) -> Optional[float]:
        if not self.pru or not self.price:
            return None
        return (self.price - self.pru) / self.pru * 100.0


def _f(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        else None


def load_universe() -> list[Position]:
    """Positions du portefeuille, triées par valeur de référence décroissante."""
    data = load_portfolio()
    out: list[Position] = []
    for key, info in (data.get("portfolio") or {}).items():
        if not isinstance(info, dict):
            continue
        # ``symbol`` remplace la clé pour l'affichage ; la clé reste l'identifiant
        # de résolution CoinGecko (mapping config/sources.yaml).
        out.append(Position(str(info.get("symbol") or key), str(key), info))
    out.sort(key=lambda p: p.baseline_usd, reverse=True)
    return out


def market_symbols(positions: list[Position]) -> list[str]:
    """Clés de résolution marché : portefeuille + références, sans doublon."""
    seen: list[str] = []
    for k in [p.cg_key for p in positions] + list(MARKET_REFERENCE):
        if k not in seen:
            seen.append(k)
    return seen


# ── valorisation ──────────────────────────────────────────────────────────

def value_portfolio(positions: list[Position],
                    spot: sr.SourceResult) -> float:
    """Valorise au prix live ; retombe sur le dernier instantané connu.

    Le repli est TRACÉ (log) mais ne change pas le statut du run : c'est la
    matrice de sources qui porte l'information de dégradation.
    """
    quotes = (spot.value or {}) if spot.usable else {}
    total = 0.0
    for p in positions:
        q = quotes.get(p.cg_key) or {}
        price = _f(q.get("price"))
        p.price = price
        if price and p.quantity:
            p.value_usd = price * p.quantity
        else:
            p.value_usd = p.baseline_usd
            if price is None:
                logger.info("%s : prix live absent — valeur de référence retenue.",
                            p.symbol)
        total += p.value_usd
    for p in positions:
        p.weight_pct = (p.value_usd / total * 100.0) if total > 0 else None
    return round(total, 2)


# ── faits ─────────────────────────────────────────────────────────────────

def register_facts(store, *, positions: list[Position], ptf_value: float,
                   spot: sr.SourceResult, sources: dict[str, sr.SourceResult],
                   now: Optional[datetime] = None) -> None:
    """Enregistre TOUS les faits de contexte. À appeler avant le scellement."""
    ref = now or datetime.now(timezone.utc)
    store.register("run.date", ref, Unit.DATE)
    store.register("ptf.value", ptf_value, Unit.USD_AMOUNT, source=spot)
    store.register("ptf.positions", len(positions), Unit.COUNT)

    quotes = (spot.value or {}) if spot.usable else {}
    for p in positions:
        a = p.symbol.lower()
        q = quotes.get(p.cg_key) or {}
        if p.price:
            store.register(f"market.{a}.price", p.price, Unit.USD_PRICE,
                           source=spot)
        for field, unit, fid in (("change_24h", Unit.PCT, "chg24"),
                                 ("change_7d", Unit.PCT, "chg7"),
                                 ("change_30d", Unit.PCT, "chg30")):
            v = _f(q.get(field))
            if v is not None:
                store.register(f"market.{a}.{fid}", v, unit, source=spot)
        vol = _f(q.get("volume_24h"))
        if vol is not None:
            store.register(f"market.{a}.volume", vol, Unit.USD_COMPACT,
                           source=spot, usage_right=UsageRight.CONTEXT)
        store.register(f"ptf.{a}.value", p.value_usd, Unit.USD_AMOUNT)
        if p.weight_pct is not None:
            store.register(f"ptf.{a}.weight", p.weight_pct, Unit.PCT)
        if p.pnl_pct is not None:
            store.register(f"ptf.{a}.pnl", p.pnl_pct, Unit.PCT)

    _register_context(store, sources)


# Séries FRED réellement servies par ``fred.get_macro()`` (clé -> unité).
# Les noms proviennent de ``fred._SERIES`` ; toute clé absente de cette table
# ne produirait AUCUN fait. L'audit final a montré que lire « cpi_yoy » à la
# racine de la charge — alors que get_macro renvoie ``{available, series}`` —
# tuait silencieusement TOUS les faits macro.
_FRED_FACTS = (("dxy", Unit.RATIO), ("us_10y", Unit.PCT),
               ("us_2y", Unit.PCT), ("real_10y", Unit.PCT),
               ("vix", Unit.RATIO), ("hy_spread", Unit.PCT))


def _register_context(store, sources: dict[str, sr.SourceResult]) -> None:
    """Faits issus des sources de contexte. Absence = fait NON créé (jamais 0).

    Chaque lecture ci-dessous suit la forme RÉELLE de la charge produite par sa
    source, vérifiée dans le module producteur. Un fait dont la clé n'existe pas
    ne lève pas : il n'apparaît simplement jamais — c'est la définition même du
    signal mort, et c'est ce que cette fonction avait introduit avant correction.
    """
    fg = sources.get("fear_greed")
    if fg is not None and fg.usable:
        val = _f((fg.value or {}).get("value"))
        if val is not None:
            store.register("macro.fear_greed", val, Unit.COUNT, source=fg)
        label = (fg.value or {}).get("classification")
        if label:
            store.register("macro.fear_greed_label", label, Unit.TEXT, source=fg)

    # fred.get_macro() -> {available, series: {nom: {value, date, previous, delta}}}
    fred = sources.get("fred")
    if fred is not None and fred.usable:
        series = (fred.value or {}).get("series") or {}
        for key, unit in _FRED_FACTS:
            entry = series.get(key)
            val = _f(entry.get("value")) if isinstance(entry, dict) else None
            if val is not None:
                store.register(f"macro.{key}", val, unit, source=fred)

    # onchain -> {"BTC": get_btc_onchain(), "ETH": get_eth_onchain()}
    #   BTC : {available, hash_rate_ehs, miners_revenue_usd, difficulty, …}
    #   ETH : {available, gas_safe_gwei, gas_propose_gwei, gas_fast_gwei, …}
    # Aucune des deux ne fournit MVRV : la source qui le servait a été retirée.
    oc = sources.get("onchain")
    if oc is not None and oc.usable:
        btc = (oc.value or {}).get("BTC") or {}
        hashrate = _f(btc.get("hash_rate_ehs"))
        if hashrate is not None:
            store.register("onchain.btc.hashrate", hashrate, Unit.RATIO,
                           source=oc, usage_right=UsageRight.CONTEXT)
        revenue = _f(btc.get("miners_revenue_usd"))
        if revenue is not None:
            store.register("onchain.btc.miners_revenue", revenue,
                           Unit.USD_COMPACT, source=oc)
        eth = (oc.value or {}).get("ETH") or {}
        gas = _f(eth.get("gas_propose_gwei"))
        if gas is not None:
            store.register("onchain.eth.gas", gas, Unit.RATIO, source=oc,
                           usage_right=UsageRight.CONTEXT)

    # etf_flows -> {available, btc: {date, total_flow_musd, source}, eth: {…}}
    # Le flux est exprimé en MILLIONS de dollars : la conversion est explicite.
    etf = sources.get("etf_flows")
    if etf is not None and etf.usable:
        for chain in ("btc", "eth"):
            block = (etf.value or {}).get(chain) or {}
            musd = _f(block.get("total_flow_musd"))
            if musd is not None:
                store.register(f"macro.etf_{chain}_flow", musd * 1e6,
                               Unit.USD_COMPACT, source=etf)


# ── spécifications de candidats ───────────────────────────────────────────

def candidate_specs(
    positions: list[Position], *, ptf_value: float,
    closes: dict[str, sr.SourceResult], bars: dict[str, sr.SourceResult],
    spot: sr.SourceResult, signals_by_asset: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Construit les entrées de ``runs.evaluate_candidates``.

    Aucun pré-filtrage par score : la SPEC ne prévoit qu'UN gate d'émission, la
    viabilité (§4.4). Un seuil d'éligibilité supplémentaire serait une seconde
    autorité de décision — exactement ce que V31 supprime. Tous les actifs du
    portefeuille sont donc évalués, et chaque rejet est chiffré.
    """
    quotes = (spot.value or {}) if spot.usable else {}
    specs: list[dict[str, Any]] = []
    for p in positions:
        cl = closes.get(p.cg_key)
        series = list((cl.value or {}).get("closes") or []) if cl and cl.usable \
            else []
        br = bars.get(p.cg_key)
        daily: Optional[list[DailyBar]] = br.value if br and br.usable else None
        q = quotes.get(p.cg_key) or {}
        specs.append({
            "asset": p.symbol,
            "direction": Direction.LONG_INCREASE,
            "is_core": p.is_core,
            "tier": p.tier,
            "signal_scoring": signals_by_asset.get(p.symbol) or {},
            "price": p.price,
            "closes": series,
            "daily_bars": daily,
            "ptf_value_usd": ptf_value,
            "weight_pct": p.weight_pct,
            "position_value_usd": p.value_usd,
            "daily_volume_usd": _f(q.get("volume_24h")),
        })
    return specs


def daily_close_map(positions: list[Position],
                    closes: dict[str, sr.SourceResult]) -> dict[str, float]:
    """Table actif -> dernière clôture journalière COMPLÈTE (SPEC §2.4)."""
    from src.pipeline.market import last_complete_daily_close
    out: dict[str, float] = {}
    for p in positions:
        res = closes.get(p.cg_key)
        if res is None:
            continue
        val = last_complete_daily_close(res)
        if val is not None:
            out[p.symbol] = val
    return out


def spot_map(positions: list[Position], spot: sr.SourceResult) -> dict[str, float]:
    quotes = (spot.value or {}) if spot.usable else {}
    out: dict[str, float] = {}
    for p in positions:
        px = _f((quotes.get(p.cg_key) or {}).get("price"))
        if px:
            out[p.symbol] = px
    return out


def collect_all(symbols: list[str], *, full: bool
                ) -> tuple[dict[str, sr.SourceResult], sr.SourceResult,
                           dict[str, sr.SourceResult], dict[str, sr.SourceResult]]:
    """Collecte complète ou partielle. Retourne (contexte, spot, clôtures, bougies).

    Le SOIR et l'HEBDO ne demandent NI bougies OHLC NI clôtures profondes : ils
    ne construisent aucun plan (SPEC §2.2/§2.3). Ils lisent tout de même la
    dernière clôture, seule base d'évaluation des transitions.
    """
    ctx_sources = collect.context(symbols, full=full)
    spot = collect.market_spot(symbols)
    closes = collect.market_closes(symbols)
    bars = collect.market_bars(symbols) if full else {}
    return ctx_sources, spot, closes, bars
