"""Source ETF flows : scraping de la page publique Farside Investors.

Récupère les flux quotidiens des ETF spot BTC et ETH. Le HTML de Farside est
un tableau ; on extrait la dernière ligne (jour le plus récent) et le cumulé.
Dégradation gracieuse si la page change de structure ou est indisponible.

v14.1 — fiabilisation : les requêtes passent par ``http.get_text`` (retry
exponentiel sur 429/5xx + en-têtes type navigateur, l'UA « bot » nu déclenchait
des refus intermittents côté Cloudflare/Farside). Le parsing ne suppose plus
que le PREMIER ``<table>`` est le bon : on scanne toutes les tables et on
retient celle qui contient des lignes datées (robuste aux bandeaux/menus).
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from src.data_sources.http import get_text
from src.utils.cache import CACHE
from src.utils.logger import get_logger

logger = get_logger(__name__)

BTC_ETF_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
ETH_ETF_URL = "https://farside.co.uk/ethereum-etf-flow-all-data/"

# v16 — Farside renvoie souvent 403 depuis les IP datacenter (GitHub Actions).
# En-têtes navigateur réalistes pour maximiser les chances d'obtenir le HTML
# (le 403 est fréquemment basé sur un User-Agent « python-requests »).
_ETF_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://farside.co.uk/",
}

# Fallback keyless v16 — endpoint public CoinGlass (flux ETF agrégés). Best-effort :
# si Farside ET CoinGlass échouent, dégradation propre (available=False).
_COINGLASS_BTC = "https://capi.coinglass.com/api/etf/bitcoin/flowHistory"
_COINGLASS_ETH = "https://capi.coinglass.com/api/etf/ethereum/flowHistory"
# v18 (M-A24) — 2e schéma d'URL CoinGlass (l'API alterne entre fapi/capi selon
# l'endpoint). On tente les deux : maximise les chances quand Farside est down.
_COINGLASS_BTC_ALT = "https://fapi.coinglass.com/api/etf/bitcoin/flowHistory"
_COINGLASS_ETH_ALT = "https://fapi.coinglass.com/api/etf/ethereum/flowHistory"

# Une « ligne de données » commence par une date (ex. « 09 Jun 2026 »).
_DATE_CELL = re.compile(r"\d{1,2}\s+\w{3}\s+\d{4}|\d{4}-\d{2}-\d{2}")


def _scrape_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Scrape la dernière ligne de flux d'une page Farside (best-effort)."""
    try:
        from bs4 import BeautifulSoup

        html = get_text(url, timeout=20, headers=_ETF_HEADERS)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in reversed(rows):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cells) < 2:
                    continue
                first = cells[0]
                # Vraie ligne de données : 1re cellule = date (pas « Total »,
                # « Average », ni un libellé de menu).
                if not (_DATE_CELL.search(first) or
                        (any(ch.isdigit() for ch in first) and "total" not in first.lower())):
                    continue
                total = (
                    cells[-1].replace(",", "").replace("(", "-").replace(")", "")
                )
                try:
                    flow = float(total)
                except ValueError:
                    flow = None
                return {"date": first, "total_flow_musd": flow, "source": "Farside"}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Farside scraping échoué (%s) : %s", url, exc)
        return None


def _coinglass_latest_flow(url: str) -> Optional[dict[str, Any]]:
    """Fallback keyless CoinGlass : dernier flux net ETF agrégé. None si KO."""
    try:
        from src.data_sources.http import get_json

        data = get_json(url, timeout=15, headers=_ETF_HEADERS)
        if not isinstance(data, dict):
            return None
        rows = (data.get("data") or {}).get("list") or data.get("data") or []
        if not isinstance(rows, list) or not rows:
            return None
        last = rows[-1] if isinstance(rows[-1], dict) else None
        if not last:
            return None
        # Champs CoinGlass usuels : changeUsd / flowUsd (en USD) → on convertit
        # en millions pour homogénéiser avec Farside.
        raw = (last.get("changeUsd") or last.get("flowUsd")
               or last.get("netFlow") or last.get("total"))
        flow_m = None
        try:
            flow_m = round(float(raw) / 1_000_000, 1) if raw is not None else None
        except (TypeError, ValueError):
            flow_m = None
        _ts = last.get("date") or last.get("timestamp") or ""
        return {"date": str(_ts), "total_flow_musd": flow_m, "source": "CoinGlass"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("CoinGlass ETF échoué (%s) : %s", url, exc)
        return None


# --------------------------------------------------------------------------- #
# v26 (B5/A3) — flux ETF DÉTERMINISTES depuis le canal Telegram « ETF_Flows ».
# --------------------------------------------------------------------------- #
# Sur GitHub Actions, Farside est 403 et CoinGlass 404 : la SEULE voie fiable
# était le canal Telegram ETF_Flows… mais ses chiffres n'étaient exploités que
# par Gemini (news), pendant que le footer déclarait « ETF flows indisponibles »
# — contradiction relevée à l'audit v25 (A3). On parse donc les messages du
# canal EN PYTHON (format stable, vérifié en conditions réelles) :
#   « 🟠 Bitcoin ETF Inflow : 2026-07-01 … 📊 Net Inflow : -$325.8M
#     ⚡ 7-day Avg : -$360.5M »   (idem « ETH ETF Inflow », SOL, XRP…)
# La source devient STRUCTURÉE (available=True, date, net, moyenne 7j) → le
# drapeau de source, les tuiles et l'EN BREF citent le MÊME fait, daté.
_TG_ASSET_HEAD = re.compile(
    r"(bitcoin|btc|eth(?:ereum)?)\s+etf\s+inflow\s*:?\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_TG_NET = re.compile(
    r"net\s+inflow\s*:?\s*(-?)\$?([\d,.]+)\s*([kmb])", re.IGNORECASE)
_TG_AVG = re.compile(
    r"7-day\s+avg\s*:?\s*(-?)\$?([\d,.]+)\s*([kmb])", re.IGNORECASE)
# Récap quotidien multi-actifs : « BTC ETFs : -$325.8M » / « ETH ETFs : $14.8M ».
_TG_SUMMARY_LINE = re.compile(
    r"\b(btc|eth)\s+etfs\s*:?\s*(-?)\$?([\d,.]+)\s*([kmb])", re.IGNORECASE)

_MULT = {"k": 0.001, "m": 1.0, "b": 1000.0}  # tout homogénéisé en M$


def _tg_amount_musd(sign: str, num: str, unit: str) -> Optional[float]:
    """Convertit « -, 325.8, M » en millions de dollars signés."""
    try:
        val = float(num.replace(",", "")) * _MULT[unit.lower()]
    except (ValueError, KeyError):
        return None
    return round(-val if sign == "-" else val, 1)


def parse_flows_from_telegram(
    telegram: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Extrait les flux ETF BTC/ETH des messages Telegram (déterministe).

    Args:
        telegram: sortie de ``telegram_reader.get_telegram_news`` —
            ``{available, messages: [{channel, text, timestamp}]}``.

    Returns:
        ``{available, btc, eth}`` au même schéma que ``get_etf_flows``
        (+ ``avg_7d_musd`` quand le canal le fournit). ``available=False``
        si aucun message exploitable.
    """
    out: dict[str, Any] = {"available": False, "btc": None, "eth": None}
    msgs = (telegram or {}).get("messages") if isinstance(telegram, dict) else None
    if not isinstance(msgs, list):
        return out
    # Les messages Telethon sont datés ISO : on parcourt du plus récent au plus
    # ancien pour que le PREMIER match par actif soit le plus frais.
    def _ts(m: dict[str, Any]) -> str:
        return str(m.get("timestamp") or "")
    ordered = [
        m for m in sorted((x for x in msgs if isinstance(x, dict)),
                          key=_ts, reverse=True)
        if str(m.get("channel") or "").lower() in ("etf_flows", "etf flows")
    ]
    # PASSE 1 — messages PAR ACTIF (les plus riches : date + net + moyenne 7j).
    # Le récap quotidien multi-actifs est posté APRÈS eux : sans les deux
    # passes, il masquerait les messages détaillés (perte date/moyenne).
    for m in ordered:
        text = str(m.get("text") or "")
        head = _TG_ASSET_HEAD.search(text)
        if not head:
            continue
        sym = "btc" if head.group(1).lower().startswith(("bit", "btc")) else "eth"
        if out.get(sym) is not None:
            continue  # déjà couvert par un message plus récent
        net = _TG_NET.search(text)
        if not net:
            continue
        flow = _tg_amount_musd(*net.groups())
        if flow is None:
            continue
        entry: dict[str, Any] = {
            "date": head.group(2), "total_flow_musd": flow,
            "source": "Telegram · ETF_Flows",
        }
        avg = _TG_AVG.search(text)
        if avg:
            avg_v = _tg_amount_musd(*avg.groups())
            if avg_v is not None:
                entry["avg_7d_musd"] = avg_v
        out[sym] = entry
    # PASSE 2 — récap multi-actifs (« BTC ETFs : -$325.8M ») : ne comble que
    # les actifs encore vides (ex. message détaillé tronqué/absent).
    for m in ordered:
        if out.get("btc") is not None and out.get("eth") is not None:
            break
        text = str(m.get("text") or "")
        if _TG_ASSET_HEAD.search(text) or "etf flows" not in text.lower():
            continue
        for sym_raw, sign, num, unit in _TG_SUMMARY_LINE.findall(text):
            sym = sym_raw.lower()
            if out.get(sym) is not None:
                continue
            flow = _tg_amount_musd(sign, num, unit)
            if flow is None:
                continue
            out[sym] = {"date": None, "total_flow_musd": flow,
                        "source": "Telegram · ETF_Flows"}
    out["available"] = bool(out["btc"] or out["eth"])
    return out


# Repli #2 (sans Telethon) : aperçu web public du canal — https://t.me/s/…
# répond sans clé ni session (probé). Best-effort : si Telegram bloque les IP
# datacenter un jour donné, dégradation propre.
_TME_PREVIEW = "https://t.me/s/ETF_Flows"


def _flows_from_tme_preview() -> dict[str, Any]:
    """Parse l'aperçu web t.me/s/ETF_Flows (repli sans session Telegram)."""
    try:
        html = get_text(_TME_PREVIEW, timeout=15, headers=_ETF_HEADERS)
        if not html:
            return {"available": False, "btc": None, "eth": None}
        blocks = re.findall(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html, re.S)
        msgs = []
        for i, b in enumerate(blocks):
            clean = re.sub(r"<br/?>", "\n", b)
            clean = re.sub(r"<[^>]+>", "", clean)
            for a, c in (("&amp;", "&"), ("&#36;", "$"), ("&nbsp;", " "),
                         ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'")):
                clean = clean.replace(a, c)
            # L'ordre du DOM est chronologique : on fabrique un pseudo-timestamp
            # croissant pour que le tri « plus récent d'abord » du parseur tienne.
            msgs.append({"channel": "etf_flows", "text": clean,
                         "timestamp": f"{i:06d}"})
        return parse_flows_from_telegram({"available": True, "messages": msgs})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Aperçu t.me ETF_Flows échoué : %s", exc)
        return {"available": False, "btc": None, "eth": None}


def merge_with_telegram(
    base: dict[str, Any], telegram: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Complète ``get_etf_flows`` avec le canal Telegram quand Farside est KO.

    Priorité : Farside/CoinGlass (si dispo) > messages Telethon > aperçu t.me.
    Ne touche jamais une entrée déjà remplie (pas d'écrasement d'une source
    directe par un repli).
    """
    if isinstance(base, dict) and base.get("available") and base.get("btc") and base.get("eth"):
        return base
    out = dict(base) if isinstance(base, dict) else {"available": False, "btc": None, "eth": None}
    tg = parse_flows_from_telegram(telegram)
    if not tg.get("available"):
        tg = _flows_from_tme_preview()
    if not isinstance(tg, dict):  # défense : un repli cassé ne casse pas le run
        tg = {"available": False, "btc": None, "eth": None}
    for sym in ("btc", "eth"):
        if not out.get(sym) and tg.get(sym):
            out[sym] = tg[sym]
    out["available"] = bool(out.get("btc") or out.get("eth"))
    if out["available"]:
        out.pop("reason", None)
    return out


def get_etf_flows() -> dict[str, Any]:
    """Récupère les flux ETF BTC et ETH du jour le plus récent.

    Returns:
        Dict ``{available, btc: {...}|None, eth: {...}|None}``.
    """

    def _fetch() -> dict[str, Any]:
        # v21 (Logs#2) — sur GitHub Actions, Farside renvoie 403 (geo-block
        # datacenter) ET les endpoints CoinGlass sont morts (404) : tenter ces
        # 6 appels ne produit QUE du bruit pour zéro donnée. Le flux ETF est
        # alors couvert par le canal Telegram « ETF_Flows » (réconcilié au rendu,
        # cf. digests). On saute donc proprement sur Actions. Hors Actions (dev
        # local / IP résidentielle), Farside répond : on garde le best-effort.
        if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
            return {"available": False, "btc": None, "eth": None,
                    "reason": "Farside 403 / CoinGlass 404 sur Actions — repli Telegram ETF_Flows"}
        btc = _scrape_latest_flow(BTC_ETF_URL)
        eth = _scrape_latest_flow(ETH_ETF_URL)
        # fallback CoinGlass si Farside est bloqué (403 datacenter).
        if btc is None:
            btc = _coinglass_latest_flow(_COINGLASS_BTC)
        if eth is None:
            eth = _coinglass_latest_flow(_COINGLASS_ETH)
        # 2e schéma d'URL CoinGlass si le premier a échoué.
        if btc is None:
            btc = _coinglass_latest_flow(_COINGLASS_BTC_ALT)
        if eth is None:
            eth = _coinglass_latest_flow(_COINGLASS_ETH_ALT)
        return {"available": bool(btc or eth), "btc": btc, "eth": eth}

    return CACHE.get_or_compute("etf:flows", 3600, _fetch)
