"""Diagnostic des sources de données pour Crypto Analyst Pro — v14.1.

Teste chaque source RÉELLEMENT UTILISÉE par l'agent avec un appel minimal et
affiche : ✅ OK (avec preuve) / ⚠️ dégradé-attendu (fallback actif ou source
optionnelle) / ❌ KO (action requise).

v14.1 — refonte complète. L'ancien script testait des sources JAMAIS utilisées
par le code (CryptoQuant, Coinglass avec une clé inexistante) → faux ❌
systématiques, et ignorait la moitié de la stack réelle (Yahoo, OKX fallback,
Farside ETF, miroir Coin Metrics, calendrier FRED, DefiLlama emissions).
Désormais : 1 test = 1 source du pipeline, fallbacks inclus.

Usage dans le Codespace :
    python diagnostic_apis.py

Lit les clés depuis les variables d'environnement. Sur GitHub Actions les
secrets sont injectés ; en local il faut les exporter ou créer .env.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ requests n'est pas installé. Lance : pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

OK = "\033[92m✅\033[0m"
KO = "\033[91m❌\033[0m"
WARN = "\033[93m⚠️\033[0m"
DIM = "\033[90m"
RESET = "\033[0m"

# status ∈ {"ok", "warn", "ko"} — warn = dégradé ATTENDU (fallback actif,
# source optionnelle sans clé) : pas une erreur, pas d'action requise.
results: dict[str, tuple[str, str]] = {}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.8,fr;q=0.6",
}


def report(name: str, status: str, msg: str) -> None:
    icon = {"ok": OK, "warn": WARN, "ko": KO}[status]
    print(f"{icon} {name:34s} → {msg}")
    results[name] = (status, msg)


def test(name: str, key_name: Optional[str] = None, optional: bool = False):
    """Décorateur : vérifie la clé (si requise) puis exécute le test.

    ``optional=True`` : clé absente → ⚠️ (source facultative), pas ❌.
    """
    def decorator(func):
        def wrapper():
            if key_name:
                key = os.environ.get(key_name, "").strip()
                if not key:
                    if optional:
                        report(name, "warn",
                               f"{key_name} absente — source optionnelle, ignorée")
                    else:
                        report(name, "ko", f"Variable {key_name} absente ou vide")
                    return
            try:
                func()
            except Exception as exc:  # noqa: BLE001
                report(name, "ko", f"Exception : {type(exc).__name__}: {exc}")
        return wrapper
    return decorator


def http_get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 12)
    return requests.get(url, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Cœur — prix & marché
# ─────────────────────────────────────────────────────────────────────────────

@test("CoinGecko", "COINGECKO_API_KEY")
def test_coingecko():
    key = os.environ["COINGECKO_API_KEY"]
    r = http_get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "bitcoin", "vs_currencies": "usd"},
        headers={"x-cg-demo-api-key": key},
    )
    if r.status_code != 200:
        report("CoinGecko", "ko", f"HTTP {r.status_code} : {r.text[:80]}")
        return
    price = r.json().get("bitcoin", {}).get("usd")
    if price:
        report("CoinGecko", "ok", f"BTC = ${price:,}")
    else:
        report("CoinGecko", "ko", f"Réponse vide : {r.text[:80]}")


@test("CoinMarketCap", "COINMARKETCAP_API_KEY")
def test_coinmarketcap():
    key = os.environ["COINMARKETCAP_API_KEY"]
    r = http_get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
        params={"symbol": "BTC"},
        headers={"X-CMC_PRO_API_KEY": key, "Accept": "application/json"},
    )
    if r.status_code != 200:
        report("CoinMarketCap", "ko", f"HTTP {r.status_code} : {r.text[:80]}")
        return
    price = (r.json().get("data", {}).get("BTC", {})
             .get("quote", {}).get("USD", {}).get("price"))
    if price:
        report("CoinMarketCap", "ok", f"BTC = ${price:,.0f}")
    else:
        report("CoinMarketCap", "ko", "Pas de prix dans la réponse")


@test("Fear & Greed", None)
def test_fear_greed():
    r = http_get("https://api.alternative.me/fng/", params={"limit": 1})
    data = (r.json().get("data") or [{}])[0]
    if data.get("value"):
        report("Fear & Greed", "ok",
               f"{data['value']} ({data.get('value_classification', '?')})")
    else:
        report("Fear & Greed", "ko", f"HTTP {r.status_code}")


@test("Yahoo Finance (macro + actions)", None)
def test_yahoo():
    """Source LIVE prioritaire : indices US/EU/JP, or, FX, VIX — et les actions
    liées crypto (NVDA…) pour les corrélations actions ↔ crypto (v14.1)."""
    base = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
    r = http_get(base.format("%5EGSPC"), headers=_BROWSER_HEADERS,
                 params={"interval": "1d", "range": "1d"})
    meta = ((r.json().get("chart") or {}).get("result") or [{}])[0].get("meta", {})
    spx = meta.get("regularMarketPrice")
    r2 = http_get(base.format("NVDA"), headers=_BROWSER_HEADERS,
                  params={"interval": "1d", "range": "1d"})
    meta2 = ((r2.json().get("chart") or {}).get("result") or [{}])[0].get("meta", {})
    nvda = meta2.get("regularMarketPrice")
    if spx and nvda:
        report("Yahoo Finance (macro + actions)", "ok",
               f"S&P 500 = {spx:,.0f} · NVDA = ${nvda:,.2f}")
    elif spx or nvda:
        report("Yahoo Finance (macro + actions)", "warn",
               f"Partiel (S&P={spx}, NVDA={nvda}) — réessayer")
    else:
        report("Yahoo Finance (macro + actions)", "ko",
               f"HTTP {r.status_code}/{r2.status_code} — aucun prix")


@test("Binance Futures → OKX", None)
def test_binance_okx():
    """Funding/OI. Binance renvoie 451 (géo-block) depuis GitHub Actions :
    ATTENDU — le code bascule automatiquement sur OKX (binance_futures.py)."""
    try:
        rb = http_get("https://fapi.binance.com/fapi/v1/premiumIndex",
                      params={"symbol": "BTCUSDT"})
        binance_ok = rb.status_code == 200
        binance_code = rb.status_code
    except Exception:
        binance_ok, binance_code = False, "réseau"
    if binance_ok:
        fr = float(rb.json().get("lastFundingRate", 0)) * 100
        report("Binance Futures → OKX", "ok", f"Binance direct, funding {fr:+.4f}%")
        return
    ro = http_get("https://www.okx.com/api/v5/public/funding-rate",
                  params={"instId": "BTC-USDT-SWAP"})
    data = (ro.json().get("data") or [{}])[0]
    fr = data.get("fundingRate")
    if fr is not None:
        report("Binance Futures → OKX", "warn",
               f"Binance {binance_code} (géo-block attendu) → fallback OKX OK, "
               f"funding {float(fr) * 100:+.4f}%")
    else:
        report("Binance Futures → OKX", "ko",
               f"Binance {binance_code} ET OKX {ro.status_code} : funding indisponible")


@test("ETF flows (Farside)", None)
def test_farside():
    r = http_get("https://farside.co.uk/bitcoin-etf-flow-all-data/",
                 headers=_BROWSER_HEADERS, timeout=20)
    if r.status_code != 200:
        report("ETF flows (Farside)", "ko", f"HTTP {r.status_code}")
        return
    if "<table" in r.text.lower():
        report("ETF flows (Farside)", "ok",
               f"Page OK ({len(r.text) // 1024} Ko, tableau présent)")
    else:
        report("ETF flows (Farside)", "warn",
               "Page répond mais sans <table> — structure changée ?")


# ─────────────────────────────────────────────────────────────────────────────
# Macro & calendrier
# ─────────────────────────────────────────────────────────────────────────────

@test("FRED (macro US + intl)", "FRED_API_KEY")
def test_fred():
    key = os.environ["FRED_API_KEY"]
    r = http_get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": "DTWEXBGS", "api_key": key, "file_type": "json",
                "sort_order": "desc", "limit": 1},
    )
    obs = (r.json().get("observations") or [{}])[0]
    dxy = obs.get("value")
    # v14.1 : série internationale (taux de dépôt BCE).
    r2 = http_get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": "ECBDFR", "api_key": key, "file_type": "json",
                "sort_order": "desc", "limit": 1},
    )
    ecb = ((r2.json().get("observations") or [{}])[0]).get("value")
    if dxy and dxy != ".":
        msg = f"DXY broad = {dxy}"
        msg += f" · BCE dépôt = {ecb}%" if ecb and ecb != "." else " · BCE : n/d"
        report("FRED (macro US + intl)", "ok", msg)
    else:
        report("FRED (macro US + intl)", "ko", f"HTTP {r.status_code} : {r.text[:80]}")


@test("FRED release dates (calendrier)", "FRED_API_KEY")
def test_fred_calendar():
    """Calendrier macro = FRED exclusivement (l'ancien Trading Economics est
    mort — 410 — et a été retiré du code en v14.1)."""
    key = os.environ["FRED_API_KEY"]
    r = http_get(
        "https://api.stlouisfed.org/fred/release/dates",
        params={"release_id": "10", "api_key": key, "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "sort_order": "desc", "limit": 3},  # release 10 = CPI
    )
    dates = r.json().get("release_dates") or []
    if dates:
        report("FRED release dates (calendrier)", "ok",
               f"CPI : dernière date {dates[0].get('date')}")
    else:
        report("FRED release dates (calendrier)", "ko",
               f"HTTP {r.status_code} : {r.text[:80]}")


@test("Boursorama (calendrier affichage)", None)
def test_boursorama():
    r = http_get("https://www.boursorama.com/bourse/economie/calendrier/",
                 headers=_BROWSER_HEADERS, timeout=15)
    if r.status_code == 200 and len(r.text) > 5000:
        report("Boursorama (calendrier affichage)", "ok",
               f"Page OK ({len(r.text) // 1024} Ko)")
    else:
        report("Boursorama (calendrier affichage)", "warn",
               f"HTTP {r.status_code} — source d'appoint, B8 reste FRED-only")


@test("Polymarket", None)
def test_polymarket():
    r = http_get("https://gamma-api.polymarket.com/markets",
                 params={"limit": 1, "active": "true"})
    items = r.json()
    if isinstance(items, list) and items:
        report("Polymarket", "ok", f"Marché actif : {items[0].get('question', '?')[:50]}")
    else:
        report("Polymarket", "ko", f"HTTP {r.status_code}")


@test("Polymarket étendu (v15 · barres Fed + marchés majeurs)", None)
def test_polymarket_extended():
    """v15 — vérifie le module enrichi : dominant Fed + autres probabilités."""
    from src.data_sources import prediction_markets as _pm
    res = _pm.get_key_markets()
    fb = res.get("fed_bars") or {}
    extra = res.get("extra_markets") or []
    if fb.get("dominant"):
        report("Polymarket étendu (v15 · barres Fed + marchés majeurs)", "ok",
               f"Dominant : {fb['dominant']} {fb.get('dominant_pct')}% · "
               f"{len(extra)} autre(s) marché(s) majeur(s)")
    elif res.get("available"):
        report("Polymarket étendu (v15 · barres Fed + marchés majeurs)", "warn",
               "Marchés Fed reçus mais agrégation barres incomplète "
               "(probas hors patterns) — fallback % baisse actif, "
               "AUCUNE action requise")
    else:
        report("Polymarket étendu (v15 · barres Fed + marchés majeurs)", "ko",
               "Aucun marché reçu")


@test("Calendrier macro consolidé (v15)", None)
def test_macro_calendar_consolidated():
    """v15 — FRED + Boursorama + FOMC/BoJ officiels : ne doit JAMAIS être vide."""
    from src.data_sources import macro_calendar as _mc
    res = _mc.get_consolidated_calendar(horizon_days=10)
    evts = res.get("events") or []
    if evts:
        nxt = evts[0]
        report("Calendrier macro consolidé (v15)", "ok",
               f"{len(evts)} événement(s) sur 10j · prochain : "
               f"{nxt.get('label')} ({nxt.get('when')}) · "
               f"sources : {', '.join(res.get('sources_used') or []) or 'repli'}")
    else:
        report("Calendrier macro consolidé (v15)", "ko",
               "Fenêtre vide malgré le repli banques centrales — anormal")


# ─────────────────────────────────────────────────────────────────────────────
# On-chain
# ─────────────────────────────────────────────────────────────────────────────

@test("Etherscan", "ETHERSCAN_API_KEY")
def test_etherscan():
    key = os.environ["ETHERSCAN_API_KEY"]
    r = http_get("https://api.etherscan.io/v2/api",
                 params={"chainid": 1, "module": "stats", "action": "ethsupply",
                         "apikey": key})
    data = r.json()
    if data.get("status") == "1":
        supply = int(data["result"]) / 1e18
        report("Etherscan", "ok", f"Supply ETH = {supply:,.0f}")
    else:
        report("Etherscan", "ko", data.get("result", f"HTTP {r.status_code}")[:80])


@test("blockchain.info (on-chain BTC)", None)
def test_blockchain_info():
    r = http_get("https://api.blockchain.info/stats")
    data = r.json()
    hr = data.get("hash_rate")
    if hr:
        report("blockchain.info (on-chain BTC)", "ok", f"Hashrate = {hr / 1e9:,.0f} EH/s"
               if hr > 1e9 else f"Hashrate = {hr:,.0f} GH/s")
    else:
        report("blockchain.info (on-chain BTC)", "ko", f"HTTP {r.status_code}")


@test("Coin Metrics (API + miroir GitHub)", None)
def test_coinmetrics():
    """MVRV/realized price. Sans clé, l'API community renvoie 403 depuis les
    IP datacenter : ATTENDU — le code bascule sur le miroir CSV GitHub
    (v14.1). Avec COINMETRICS_API_KEY (gratuite), l'API directe reprend."""
    key = os.environ.get("COINMETRICS_API_KEY", "").strip()
    base = ("https://api.coinmetrics.io" if key
            else "https://community-api.coinmetrics.io")
    params = {"assets": "btc", "metrics": "CapMVRVCur", "frequency": "1d",
              "page_size": 1}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    api_ok, api_code = False, "?"
    try:
        r = http_get(f"{base}/v4/timeseries/asset-metrics",
                     params=params, headers=headers)
        api_code = r.status_code
        rows = r.json().get("data") or []
        if r.status_code == 200 and rows:
            mvrv = rows[0].get("CapMVRVCur")
            report("Coin Metrics (API + miroir GitHub)", "ok",
                   f"API directe{' (clé)' if key else ''} : MVRV BTC = {float(mvrv):.2f}")
            api_ok = True
    except Exception:
        pass
    if api_ok:
        return
    # Repli : miroir GitHub (Range + identity, cf. coinmetrics.py v14.1).
    rh = http_get(
        "https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv",
        headers={"Range": "bytes=-4096", "Accept-Encoding": "identity"},
    )
    if rh.status_code in (200, 206) and "," in rh.text:
        last = [ln for ln in rh.text.splitlines() if ln[:4].isdigit()]
        as_of = last[-1].split(",")[0] if last else "?"
        report("Coin Metrics (API + miroir GitHub)", "warn",
               f"API {api_code} (403 keyless attendu) → miroir GitHub OK "
               f"(données au {as_of}). Clé gratuite coinmetrics.io = API directe.")
    else:
        report("Coin Metrics (API + miroir GitHub)", "ko",
               f"API {api_code} ET miroir {rh.status_code} : MVRV indisponible")


@test("Deribit (options)", None)
def test_deribit():
    r = http_get("https://www.deribit.com/api/v2/public/get_index_price",
                 params={"index_name": "btc_usd"})
    price = (r.json().get("result") or {}).get("index_price")
    if price:
        report("Deribit (options)", "ok", f"Index BTC = ${price:,.0f}")
    else:
        report("Deribit (options)", "ko", f"HTTP {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# DeFi · narratifs · social
# ─────────────────────────────────────────────────────────────────────────────

@test("DeFiLlama (TVL)", None)
def test_defillama():
    r = http_get("https://api.llama.fi/v2/chains")
    chains = r.json()
    if isinstance(chains, list) and chains:
        eth = next((c for c in chains if c.get("name") == "Ethereum"), {})
        report("DeFiLlama (TVL)", "ok", f"TVL Ethereum = ${eth.get('tvl', 0) / 1e9:,.1f}B")
    else:
        report("DeFiLlama (TVL)", "ko", f"HTTP {r.status_code}")


@test("DeFiLlama (unlocks v14.1)", None)
def test_defillama_unlocks():
    """Remplace l'endpoint MORT api.unlocks.app (404 définitif)."""
    r = http_get("https://api.llama.fi/emissions", timeout=20)
    data = r.json()
    items = data if isinstance(data, list) else (data or {}).get("data", [])
    if isinstance(items, list) and items:
        report("DeFiLlama (unlocks v14.1)", "ok",
               f"{len(items)} protocoles avec calendrier d'émission")
    else:
        report("DeFiLlama (unlocks v14.1)", "warn",
               f"HTTP {r.status_code} ou schéma inattendu — l'agent dégrade proprement")


@test("LunarCrush", "LUNARCRUSH_API_KEY", optional=True)
def test_lunarcrush():
    key = os.environ["LUNARCRUSH_API_KEY"]
    r = http_get("https://lunarcrush.com/api4/public/coins/list/v1",
                 params={"limit": 1}, headers={"Authorization": f"Bearer {key}"})
    if r.status_code == 200 and (r.json().get("data") or []):
        report("LunarCrush", "ok", "Sentiment social disponible")
    elif r.status_code == 402:
        report("LunarCrush", "warn",
               "402 (fréquent sur IP datacenter) — dégradation propre prévue")
    else:
        report("LunarCrush", "ko", f"HTTP {r.status_code} : {r.text[:60]}")


@test("RSS crypto (CryptoSlate)", None)
def test_rss():
    r = http_get("https://cryptoslate.com/feed/", headers=_BROWSER_HEADERS)
    if r.status_code == 200 and "<item>" in r.text:
        report("RSS crypto (CryptoSlate)", "ok", "Flux OK (1 des 16 flux testé)")
    else:
        report("RSS crypto (CryptoSlate)", "warn",
               f"HTTP {r.status_code} — les 15 autres flux compensent")


# ─────────────────────────────────────────────────────────────────────────────
# News · vidéos · messageries
# ─────────────────────────────────────────────────────────────────────────────

@test("NewsAPI", "NEWSAPI_KEY")
def test_newsapi():
    key = os.environ["NEWSAPI_KEY"]
    r = http_get("https://newsapi.org/v2/everything",
                 params={"q": "bitcoin", "pageSize": 1, "apiKey": key})
    data = r.json()
    if data.get("status") == "ok":
        report("NewsAPI", "ok", f"{data.get('totalResults', 0)} articles bitcoin")
    else:
        report("NewsAPI", "ko", data.get("message", f"HTTP {r.status_code}")[:80])


@test("YouTube Data", "YOUTUBE_API_KEY")
def test_youtube():
    key = os.environ["YOUTUBE_API_KEY"]
    # v14.1 : le pipeline utilise playlistItems (1 unité de quota) — on teste
    # ce chemin-là (Coin Bureau : UCqK_GSMbpiV8spgD3ZGloSw → UU...).
    r = http_get("https://www.googleapis.com/youtube/v3/playlistItems",
                 params={"part": "snippet", "maxResults": 1,
                         "playlistId": "UUqK_GSMbpiV8spgD3ZGloSw", "key": key})
    items = r.json().get("items") or []
    if r.status_code == 200 and items:
        title = items[0].get("snippet", {}).get("title", "?")[:45]
        report("YouTube Data", "ok", f"playlistItems OK (1 unité) : « {title} »")
    else:
        err = (r.json().get("error") or {}).get("message", "")[:70]
        report("YouTube Data", "ko", f"HTTP {r.status_code} : {err}")


@test("GitHub Token", "GH_TOKEN")
def test_github():
    token = os.environ["GH_TOKEN"]
    r = http_get("https://api.github.com/rate_limit",
                 headers={"Authorization": f"Bearer {token}"})
    core = (r.json().get("resources") or {}).get("core", {})
    if core.get("limit"):
        report("GitHub Token", "ok",
               f"Rate limit {core.get('remaining')}/{core.get('limit')}")
    else:
        report("GitHub Token", "ko", f"HTTP {r.status_code}")


@test("Telegram session", "TELEGRAM_SESSION_STRING")
def test_telegram():
    if not os.environ.get("TELEGRAM_API_ID") or not os.environ.get("TELEGRAM_API_HASH"):
        report("Telegram session", "ko", "TELEGRAM_API_ID/HASH manquants")
        return
    sess = os.environ["TELEGRAM_SESSION_STRING"]
    if len(sess) > 200:
        report("Telegram session", "ok",
               f"Session présente ({len(sess)} caractères) — connexion testée au run")
    else:
        report("Telegram session", "ko", "Session string suspecte (trop courte)")


# ─────────────────────────────────────────────────────────────────────────────
# IA & envoi
# ─────────────────────────────────────────────────────────────────────────────

@test("Gemini", "GEMINI_API_KEY")
def test_gemini():
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": "Réponds uniquement : OK"}]}]},
        timeout=30,
    )
    if r.status_code == 200:
        try:
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            report("Gemini", "ok", f"Modèle {model} répond : « {txt[:20]} »")
        except (KeyError, IndexError):
            report("Gemini", "warn", "200 mais réponse inattendue (quota ? filtre ?)")
    else:
        err = (r.json().get("error") or {}).get("message", "")[:70]
        report("Gemini", "ko", f"HTTP {r.status_code} : {err}")


@test("Gmail SMTP", "GMAIL_APP_PASSWORD")
def test_gmail():
    import smtplib
    user = os.environ.get("GMAIL_USER", "").strip()
    if not user:
        report("Gmail SMTP", "ko", "GMAIL_USER absent")
        return
    pwd = os.environ["GMAIL_APP_PASSWORD"]
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(user, pwd)
        report("Gmail SMTP", "ok", f"Login OK ({user})")
    except smtplib.SMTPAuthenticationError as exc:
        report("Gmail SMTP", "ko", f"Auth refusée : {str(exc)[:60]}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n═══ Diagnostic Crypto Analyst Pro · v14.1 ═══\n")
    sections = [
        ("Cœur — prix & marché", [test_coingecko, test_coinmarketcap,
                                  test_fear_greed, test_yahoo,
                                  test_binance_okx, test_farside]),
        ("Macro & calendrier", [test_fred, test_fred_calendar,
                                test_boursorama, test_polymarket]),
        ("On-chain", [test_etherscan, test_blockchain_info,
                      test_coinmetrics, test_deribit]),
        ("DeFi · social · news", [test_defillama, test_defillama_unlocks,
                                  test_lunarcrush, test_rss]),
        ("News · vidéos · messageries", [test_newsapi, test_youtube,
                                         test_github, test_telegram]),
        ("IA & envoi", [test_gemini, test_gmail]),
    ]
    for title, fns in sections:
        print(f"{DIM}── {title} {'─' * max(1, 46 - len(title))}{RESET}")
        for fn in fns:
            fn()
        print()

    ok = sum(1 for s, _ in results.values() if s == "ok")
    warn = sum(1 for s, _ in results.values() if s == "warn")
    ko = sum(1 for s, _ in results.values() if s == "ko")
    print("═" * 60)
    print(f"Bilan : {OK} {ok} OK · {WARN} {warn} dégradé-attendu · {KO} {ko} KO")
    if warn:
        print(f"{DIM}⚠️ = fallback actif ou source optionnelle : AUCUNE action requise.{RESET}")
    if ko:
        print(f"\nÀ corriger ({ko}) :")
        for name, (s, msg) in results.items():
            if s == "ko":
                print(f"  {KO} {name} : {msg}")
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(main())
