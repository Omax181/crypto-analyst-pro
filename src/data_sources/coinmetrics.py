"""On-chain institutionnel : Coin Metrics Community API (gratuit, SANS clé).

Fournit les métriques on-chain « de pro » absentes des sources gratuites
basiques (blockchain.info / Etherscan) :
  - MVRV  (CapMVRVCur)   : ratio cap. marché / cap. réalisée → sur/sous-évaluation
  - NVT   (NVTAdj)       : valorisation réseau / volume on-chain — CLÉ REQUISE
  - Realized Price       : dérivé prix/MVRV (CapRealUSD non servi sans clé)
  - Active addresses     : AdrActCnt (adoption réseau)

Endpoint communautaire : ``https://community-api.coinmetrics.io/v4/timeseries/asset-metrics``.
Rate limit communautaire : 10 req / 6 s par IP — on fait 1 seule requête (batch
BTC+ETH) par run, donc large marge.

v28 (4.3) — comportement RÉEL du tier keyless, PROBÉ le 07/07/2026 :
  * le paramètre ``api_key`` DOIT être présent, même VIDE : sans lui l'API
    renvoie 403 y compris pour des métriques servies (« api_key= » → 200) ;
  * seul un sous-ensemble de métriques est servi sans clé : PriceUSD,
    CapMVRVCur, AdrActCnt (et CapMrktCurUSD) → 200 avec données J-1 ;
    TOUT batch contenant NVTAdj, CapRealUSD ou SplyCur → 403 GLOBAL.
  C'est ce couple (paramètre absent + batch trop large) qui rendait l'API
  « indisponible » et figeait l'on-chain ETH sur le miroir du 23/05 alors que
  des données J-1 étaient accessibles. On demande donc le batch VALIDÉ, avec
  ``api_key`` toujours transmis, et on tente l'API MÊME sur GitHub Actions
  (coût : 1 requête) avant le repli miroir.

Dégradation gracieuse totale : toute erreur réseau / métrique absente → la clé
correspondante est simplement omise, jamais d'exception propagée.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Endpoint communautaire (sans clé). Si une clé CoinMetrics gratuite est fournie
# via COINMETRICS_API_KEY, on bascule sur l'endpoint authentifié : cela lève le
# blocage 403 fréquent sur les IP datacenter (GitHub Actions) du tier keyless.
# Sans clé, comportement inchangé (tentative keyless + dégradation gracieuse).
_BASE_COMMUNITY = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
_BASE_AUTH = "https://api.coinmetrics.io/v4/timeseries/asset-metrics"

# REPLI #2 (v14.1) — miroir CSV public Coin Metrics sur GitHub. L'API community
# renvoie 403 depuis les IP datacenter (GitHub Actions) sans clé ; le miroir
# raw.githubusercontent.com, lui, est TOUJOURS accessible depuis les runners.
# Fichiers ~2,5 Mo : on ne télécharge que la FIN (requête Range, supportée par
# le CDN GitHub — vérifié) + l'en-tête pour mapper les colonnes. Colonnes
# disponibles côté community : PriceUSD, CapMVRVCur, AdrActCnt, SplyCur (pas de
# NVTAdj ni CapRealUSD → realized price DÉRIVÉ : MVRV = prix/realized price ⇒
# realized = PriceUSD / CapMVRVCur). Le miroir peut accuser quelques jours de
# retard : on renvoie ``as_of`` (date de la dernière ligne valide) + ``stale``
# si > _MIRROR_STALE_DAYS — le digest l'affiche (« au JJ/MM »), jamais masqué.
_GITHUB_CSV = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{asset}.csv"
_MIRROR_TAIL_BYTES = 262144  # v16 : ~256 Ko — les lignes community sont larges
                             # (30+ colonnes) ; 64 Ko ne couvraient parfois que
                             # quelques jours. 256 Ko garantit plusieurs semaines.
_MIRROR_HEAD_BYTES = 8192    # l'en-tête (jusqu'à ~40 colonnes) tient dedans
_MIRROR_STALE_DAYS = 3       # v16 : seuil resserré (le MVRV dérivé est ~J-1)


def _cm_base_and_key() -> tuple[str, Optional[str]]:
    key = os.environ.get("COINMETRICS_API_KEY", "").strip()
    if key:
        return _BASE_AUTH, key
    return _BASE_COMMUNITY, None

# v28 — DEUX lots de métriques selon le tier (probes 07/07/2026) :
#   * authentifié (clé) : lot complet, NVT/CapRealUSD inclus ;
#   * keyless community : lot VALIDÉ 200 (tout ajout NVTAdj/CapRealUSD/SplyCur
#     fait basculer TOUT le batch en 403). Realized price dérivé prix/MVRV.
_METRICS_AUTH = ["PriceUSD", "CapMVRVCur", "NVTAdj", "CapRealUSD", "SplyCur", "AdrActCnt"]
_METRICS_COMMUNITY = ["PriceUSD", "CapMVRVCur", "AdrActCnt"]
# Sous-ensemble cœur garanti sur les deux tiers (utilisé en repli).
_CORE_METRICS = ["PriceUSD", "CapMVRVCur"]

# Mapping ticker PTF -> id Coin Metrics (minuscule).
_CM_IDS = {"BTC": "btc", "ETH": "eth"}


def _mvrv_zone(mvrv: Optional[float]) -> Optional[str]:
    """Traduit le MVRV en zone de marché lisible."""
    if mvrv is None:
        return None
    if mvrv < 1.0:
        return "sous-évalué (capitulation)"
    if mvrv < 2.0:
        return "neutre"
    if mvrv < 3.5:
        return "élevé"
    return "surchauffe"


def _to_float(value: Any) -> Optional[float]:
    """Convertit en float tolérant (Coin Metrics renvoie des strings)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_mirror_csv(header_text: str, tail_text: str) -> Optional[dict[str, Any]]:
    """Parse l'en-tête + la fin d'un CSV miroir Coin Metrics.

    Args:
        header_text: début du fichier (contient la ligne d'en-tête).
        tail_text: fin du fichier (dernières lignes ; la 1re peut être tronquée).

    Returns:
        Dict ``{time, PriceUSD, CapMVRVCur, AdrActCnt, AdrActCnt_prev}`` de la
        DERNIÈRE ligne portant un MVRV valide (les toutes dernières lignes du
        miroir ne contiennent parfois que ReferenceRate), ou ``None``.
    """
    if not header_text or not tail_text:
        return None
    header_line = header_text.splitlines()[0] if header_text.splitlines() else ""
    cols = [c.strip() for c in header_line.split(",")]
    if "time" not in cols or "CapMVRVCur" not in cols:
        return None
    idx = {name: i for i, name in enumerate(cols)}

    lines = tail_text.splitlines()
    if len(lines) > 1:
        lines = lines[1:]  # 1re ligne du Range probablement tronquée en plein milieu
    rows: list[list[str]] = []
    for ln in lines:
        parts = ln.split(",")
        if len(parts) != len(cols):
            continue  # ligne incomplète/corrompue : ignorée
        if not parts[idx["time"]][:4].isdigit():
            continue
        rows.append(parts)
    # Remonte à la dernière ligne avec un MVRV présent.
    adr_series: list[Optional[float]] = []
    last_valid: Optional[list[str]] = None
    for parts in rows:
        mvrv = _to_float(parts[idx["CapMVRVCur"]])
        if mvrv is not None:
            last_valid = parts
        adr_series.append(_to_float(parts[idx.get("AdrActCnt", idx["time"])])
                          if "AdrActCnt" in idx else None)
    # v16 — la colonne CapMVRVCur du miroir community accuse souvent ~3 semaines
    # de retard (backfill), alors que PriceUSD/CapRealUSD/SplyCur sont à J-1.
    # On DÉRIVE donc le MVRV de la ligne la plus RÉCENTE qui porte CapRealUSD +
    # SplyCur + PriceUSD : MVRV = capMarché / capRéalisée = (prix·supply) /
    # CapRealUSD. Ça rafraîchit le MVRV de plusieurs semaines quand c'est dispo.
    fresh_derived: Optional[list[str]] = None
    if "CapRealUSD" in idx and "SplyCur" in idx and "PriceUSD" in idx:
        for parts in rows:  # rows est chronologique : la dernière éligible gagne
            cr = _to_float(parts[idx["CapRealUSD"]])
            sp = _to_float(parts[idx["SplyCur"]])
            pr = _to_float(parts[idx["PriceUSD"]])
            if cr and sp and pr and cr > 0 and sp > 0:
                fresh_derived = parts
    use_row = None
    derived_mvrv = None
    if fresh_derived is not None:
        cr = _to_float(fresh_derived[idx["CapRealUSD"]])
        sp = _to_float(fresh_derived[idx["SplyCur"]])
        pr = _to_float(fresh_derived[idx["PriceUSD"]])
        realized_price = cr / sp if (cr and sp) else None
        if realized_price and realized_price > 0 and pr:
            derived_mvrv = pr / realized_price
            use_row = fresh_derived
    # Si la dérivation fraîche échoue, on retombe sur la dernière ligne MVRV
    # publiée (comportement v15, jamais pire qu'avant).
    if use_row is None:
        use_row = last_valid
    if use_row is None:
        return None
    out: dict[str, Any] = {"time": use_row[idx["time"]]}
    for col in ("PriceUSD", "CapMVRVCur", "AdrActCnt", "CapRealUSD", "SplyCur"):
        if col in idx:
            out[col] = _to_float(use_row[idx[col]])
    if derived_mvrv is not None:
        # Le MVRV dérivé (frais) écrase la colonne CapMVRVCur (souvent vide/vieille).
        out["CapMVRVCur"] = round(derived_mvrv, 4)
        out["mvrv_derived"] = True
    # Adresse actives ~7 lignes avant la dernière valide (tendance hebdo).
    valid_adr = [a for a in adr_series if a is not None]
    if len(valid_adr) >= 8:
        out["AdrActCnt_prev"] = valid_adr[-8]
    return out


def _fetch_mirror_asset(cm_id: str) -> Optional[dict[str, Any]]:
    """Récupère en-tête + fin du CSV miroir GitHub pour un asset. None si KO.

    ``Accept-Encoding: identity`` est OBLIGATOIRE : sans lui, le CDN GitHub
    sert le flux gzippé et le Range découpe le gzip en plein milieu →
    indécodable (vérifié en conditions réelles). En clair, le Range s'applique
    aux octets du texte : découpe propre.
    """
    from src.data_sources.http import get_text

    url = _GITHUB_CSV.format(asset=cm_id)
    head = get_text(url, headers={
        "Range": f"bytes=0-{_MIRROR_HEAD_BYTES - 1}",
        "Accept-Encoding": "identity",
    })
    if not head:
        return None
    tail = get_text(url, headers={
        "Range": f"bytes=-{_MIRROR_TAIL_BYTES}",
        "Accept-Encoding": "identity",
    })
    if not tail:
        return None
    return _parse_mirror_csv(head, tail)


def _entry_from_mirror(row: dict[str, Any]) -> dict[str, Any]:
    """Construit l'entrée par asset (même schéma que l'API) depuis le miroir."""
    entry: dict[str, Any] = {}
    price = row.get("PriceUSD")
    mvrv = row.get("CapMVRVCur")
    adr = row.get("AdrActCnt")
    adr_prev = row.get("AdrActCnt_prev")
    if price is not None:
        entry["price"] = round(price, 2)
    if mvrv is not None:
        entry["mvrv"] = round(mvrv, 2)
        entry["mvrv_zone"] = _mvrv_zone(mvrv)
        # MVRV = cap marché / cap réalisée = prix / realized price (même supply)
        # ⇒ realized price dérivé sans CapRealUSD (absent du miroir community).
        if price is not None and mvrv:
            rp = price / mvrv
            entry["realized_price"] = round(rp, 2)
            entry["realized_price_ratio"] = round(mvrv, 2)
    if adr is not None:
        entry["active_addresses"] = int(adr)
        if adr_prev:
            entry["active_addresses_trend_pct"] = round(
                (adr - adr_prev) / adr_prev * 100, 1
            )
    # Date de la donnée (le miroir peut traîner) — affichée si stale.
    ts = str(row.get("time") or "")[:10]
    if ts:
        entry["as_of"] = ts
        try:
            d = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - d).days
            entry["stale"] = age_days > _MIRROR_STALE_DAYS
        except ValueError:
            pass
    return entry


def _mirror_fallback() -> dict[str, Any]:
    """Repli miroir GitHub : MVRV/realized price/adresses pour BTC + ETH.

    NVT absent du miroir (TxTfrValAdjUSD non publié côté community) : la clé
    est simplement omise, le rendu et les digests savent déjà la sauter.
    """
    out_assets: dict[str, Any] = {}
    for sym, cm_id in _CM_IDS.items():
        try:
            row = _fetch_mirror_asset(cm_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("Miroir Coin Metrics %s : %s", cm_id, exc)
            continue
        if not row:
            continue
        entry = _entry_from_mirror(row)
        if entry.get("mvrv") is not None:
            out_assets[sym] = entry
    if not out_assets:
        return {"available": False, "source": "coinmetrics"}
    logger.info(
        "Coin Metrics : API indisponible, repli miroir GitHub (au %s).",
        next(iter(out_assets.values())).get("as_of", "?"),
    )
    return {
        "available": True,
        "source": "coinmetrics-github",
        "assets": out_assets,
    }


def _apply_btc_freshness(result: dict[str, Any]) -> dict[str, Any]:
    """Surcouche de fraîcheur BTC via bitcoin-data.com (v21, Logs#1).

    Sur les runners GitHub Actions, l'API CoinMetrics est 403 et le miroir CSV
    est en retard de plusieurs semaines → le MVRV BTC est périmé. bitcoin-data.com
    fournit un MVRV BTC à J-1, sans clé, joignable depuis le datacenter. On
    n'écrase QUE si l'entrée BTC existante est périmée / absente ; si elle est
    fraîche (API up en local), on se contente d'AJOUTER le z-score. Dégradation
    totale : si bitcoin-data est KO, ``result`` est renvoyé tel quel.
    """
    try:
        from src.data_sources import bitcoin_data
        fresh = bitcoin_data.get_btc_mvrv()
    except Exception:  # noqa: BLE001
        return result
    if not fresh.get("available") or fresh.get("mvrv") is None:
        return result

    # Copies superficielles : ne jamais muter l'objet en cache (CACHE renvoie la
    # même référence à chaque appel).
    result = dict(result)
    assets = dict(result.get("assets") or {})
    result["assets"] = assets
    btc = dict(assets.get("BTC") or {})
    existing_stale = bool(btc.get("stale")) or btc.get("mvrv") is None or "BTC" not in assets
    z = fresh.get("mvrv_zscore")
    if existing_stale:
        mvrv = round(float(fresh["mvrv"]), 2)
        btc["mvrv"] = mvrv
        btc["mvrv_zone"] = _mvrv_zone(mvrv)
        # realized price = prix / MVRV (si un prix est connu, même approximatif).
        price = btc.get("price")
        if price and mvrv:
            btc["realized_price"] = round(price / mvrv, 2)
            btc["realized_price_ratio"] = mvrv
        if fresh.get("as_of"):
            btc["as_of"] = fresh["as_of"]
        btc["stale"] = False
        btc["mvrv_source"] = "bitcoin-data.com"
        if z is not None:
            btc["mvrv_zscore"] = z
        assets["BTC"] = btc
        result["available"] = True
    elif z is not None:
        btc["mvrv_zscore"] = z
        assets["BTC"] = btc
    return result


def apply_live_price_mvrv(
    result: dict[str, Any], live_prices: dict[str, Any]
) -> dict[str, Any]:
    """Rafraîchit le MVRV des entrées PÉRIMÉES via le prix LIVE (v23, gratuit).

    Problème réglé : l'API CoinMetrics est 403 sur les runners (datacenter) et le
    miroir CSV accuse plusieurs semaines de retard → MVRV BTC ET surtout ETH
    figés (ex. « au 24/05 »). Aucune source on-chain ETH fraîche, gratuite et
    sans clé n'existe depuis un datacenter (vérifié : BGeometrics = BTC only,
    CryptoQuant/Glassnode = clé/payant).

    Idée : MVRV = cap. marché / cap. réalisée = prix / realized price (même
    supply). Le realized price (coût moyen du marché) évolue LENTEMENT (semaines)
    alors que le prix bouge vite. On recompose donc un MVRV bien plus actuel en
    combinant le realized price (même daté) avec le PRIX LIVE déjà récupéré
    (CoinGecko, aucune source supplémentaire). C'est une ESTIMATION, marquée
    ``mvrv_live_estimate`` et libellée « prix live / prix de revient au JJ/MM »
    dans le digest — jamais présentée comme une mesure on-chain du jour.

    N'agit QUE sur les entrées encore ``stale`` portant un ``realized_price`` :
    une entrée déjà fraîche (API up, ou surcouche bitcoin-data.com pour BTC) est
    laissée intacte. Dégradation gracieuse : donnée manquante → entrée inchangée.

    Args:
        result: sortie de ``get_onchain_metrics``.
        live_prices: ``{SYM: prix_usd}`` (CoinGecko), prix live par actif.

    Returns:
        ``result`` enrichi (copies superficielles, jamais de mutation du cache).
    """
    if not isinstance(result, dict) or not result.get("assets"):
        return result
    if not isinstance(live_prices, dict) or not live_prices:
        return result
    result = dict(result)
    assets = dict(result["assets"])
    for sym, entry in list(assets.items()):
        if not isinstance(entry, dict) or not entry.get("stale"):
            continue  # entrée déjà fraîche → on ne touche pas
        rp = entry.get("realized_price")
        live = live_prices.get(sym)
        if not (isinstance(rp, (int, float)) and rp > 0):
            continue
        if not (isinstance(live, (int, float)) and live > 0):
            continue
        mvrv = round(float(live) / float(rp), 2)
        entry = dict(entry)
        entry["mvrv"] = mvrv
        entry["mvrv_zone"] = _mvrv_zone(mvrv)
        entry["realized_price_ratio"] = mvrv
        entry["mvrv_live_estimate"] = True
        assets[sym] = entry
    result["assets"] = assets
    return result


def get_onchain_metrics() -> dict[str, Any]:
    """Récupère les métriques on-chain avancées BTC + ETH (Coin Metrics community).

    Returns:
        Dict ``{available, source, assets: {SYM: {price, mvrv, mvrv_zone, nvt,
        realized_price, realized_price_ratio, active_addresses,
        active_addresses_trend_pct}}}``. Clés omises si la métrique manque.
    """

    def _fetch() -> dict[str, Any]:
        # v28 (4.3) — l'ancien raccourci « Actions sans clé → miroir direct »
        # est SUPPRIMÉ : les runs du 07/07 ont figé l'on-chain ETH au 23/05
        # alors que l'API keyless servait des données J-1 (MVRV ETH 0.90 réel
        # vs 0.81 miroir). Probé le 07/07 : le 403 ne venait pas de l'IP mais
        # (a) du paramètre ``api_key`` ABSENT et (b) du batch contenant des
        # métriques non servies sans clé (NVTAdj/CapRealUSD/SplyCur). On tente
        # donc TOUJOURS l'API (1 requête, dégradation gracieuse → miroir).
        has_key = bool(os.environ.get("COINMETRICS_API_KEY", "").strip())

        start = (datetime.now(timezone.utc) - timedelta(days=12)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )

        def _query(metrics: list[str]) -> Any:
            base, key = _cm_base_and_key()
            params: dict[str, Any] = {
                "assets": ",".join(_CM_IDS.values()),
                "metrics": ",".join(metrics),
                "frequency": "1d",
                "start_time": start,
                "page_size": 1000,
                "pretty": "false",
                # v28 — TOUJOURS présent : « api_key= » vide → 200 sur le tier
                # community ; paramètre absent → 403 (vérifié 07/07/2026).
                "api_key": key or "",
            }
            return get_json(base, params=params)

        raw = _query(_METRICS_AUTH if has_key else _METRICS_COMMUNITY)
        # Résilience v12 : l'API community refuse parfois un lot complet si UNE
        # métrique n'est pas servie sur le tier gratuit → tout échoue. On réessaie
        # alors avec le sous-ensemble cœur GARANTI (prix + MVRV) pour ne jamais
        # perdre le MVRV, signal de valorisation clé.
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list) or not raw.get("data"):
            raw = _query(_CORE_METRICS)
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("data"), list)
            or not raw.get("data")
        ):
            # v14.1 — repli #2 : l'API (403 keyless sur IP datacenter / réseau
            # KO) ne répond pas → miroir CSV GitHub (toujours joignable depuis
            # les runners). MVRV/realized price/adresses préservés, datés.
            return _mirror_fallback()

        # Regroupe les lignes (1 ligne = 1 asset à 1 date) par asset, triées.
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in raw["data"]:
            if not isinstance(row, dict):
                continue
            a = row.get("asset")
            if a:
                by_asset.setdefault(a, []).append(row)

        id_to_sym = {v: k for k, v in _CM_IDS.items()}
        out_assets: dict[str, Any] = {}
        for cm_id, rows in by_asset.items():
            sym = id_to_sym.get(cm_id)
            if not sym or not rows:
                continue
            rows.sort(key=lambda r: str(r.get("time", "")))
            last = rows[-1]
            prev = rows[-8] if len(rows) >= 8 else rows[0]

            price = _to_float(last.get("PriceUSD"))
            mvrv = _to_float(last.get("CapMVRVCur"))
            nvt = _to_float(last.get("NVTAdj"))
            cap_real = _to_float(last.get("CapRealUSD"))
            supply = _to_float(last.get("SplyCur"))
            adr = _to_float(last.get("AdrActCnt"))
            adr_prev = _to_float(prev.get("AdrActCnt"))

            entry: dict[str, Any] = {}
            if price is not None:
                entry["price"] = round(price, 2)
            if mvrv is not None:
                entry["mvrv"] = round(mvrv, 2)
                entry["mvrv_zone"] = _mvrv_zone(mvrv)
            if nvt is not None:
                entry["nvt"] = round(nvt, 1)
            # Realized price = cap réalisée / supply (prix de revient marché).
            if cap_real is not None and supply:
                rp = cap_real / supply
                entry["realized_price"] = round(rp, 2)
                if price is not None and rp:
                    # > 1 : marché en profit latent ; < 1 : en perte latente.
                    entry["realized_price_ratio"] = round(price / rp, 2)
            elif price is not None and mvrv:
                # v28 — tier community : CapRealUSD non servi. MVRV = prix /
                # realized price (même supply) ⇒ realized = prix / MVRV.
                entry["realized_price"] = round(price / mvrv, 2)
                entry["realized_price_ratio"] = round(mvrv, 2)
            if adr is not None:
                entry["active_addresses"] = int(adr)
                if adr_prev:
                    entry["active_addresses_trend_pct"] = round(
                        (adr - adr_prev) / adr_prev * 100, 1
                    )
            # v28 — fraîcheur honnête aussi sur le chemin API : la donnée est
            # J-1 en temps normal (stale False), mais si l'API traînait, les
            # surcouches (bitcoin-data, prix live) sauraient la rafraîchir et
            # le digest afficherait « au JJ/MM » au lieu de la présenter fraîche.
            ts = str(last.get("time") or "")[:10]
            if entry and ts:
                entry["as_of"] = ts
                try:
                    d = datetime.strptime(ts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    entry["stale"] = (datetime.now(timezone.utc) - d).days > _MIRROR_STALE_DAYS
                except ValueError:
                    pass
            if entry:
                out_assets[sym] = entry

        if not out_assets:
            return _mirror_fallback()
        return {"available": True, "source": "coinmetrics", "assets": out_assets}

    try:
        base = CACHE.get_or_compute("coinmetrics:onchain", 3600, _fetch)
        # Surcouche fraîcheur BTC (bitcoin-data.com) appliquée hors cache de base
        # pour bénéficier de son propre TTL et ne jamais muter l'objet caché.
        return _apply_btc_freshness(base)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Coin Metrics indisponible : %s", exc)
        return {"available": False, "source": "coinmetrics"}
