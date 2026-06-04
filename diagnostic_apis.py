"""Diagnostic des API keys pour Crypto Analyst Pro.

Teste chaque source de données une par une avec un appel minimal.
Affiche pour chacune : ✅ OK avec preuve (ex: prix BTC) / ❌ KO avec raison.

Usage dans le Codespace :
    python diagnostic_apis.py

Lit les clés depuis les variables d'environnement. Sur GitHub Actions
les secrets sont injectés ; en local il faut les exporter ou créer .env.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ requests n'est pas installé. Lance : pip install requests")
    sys.exit(1)

# Charger .env si présent (pour test local)
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

results: dict[str, tuple[bool, str]] = {}


def report(name: str, ok: bool, msg: str) -> None:
    """Affiche le résultat d'un test et le mémorise."""
    icon = OK if ok else KO
    print(f"{icon} {name:25s} → {msg}")
    results[name] = (ok, msg)


def test(name: str, key_name: Optional[str] = None):
    """Décorateur pour tester une source. Vérifie la clé puis exécute le test."""
    def decorator(func):
        def wrapper():
            if key_name:
                key = os.environ.get(key_name, "").strip()
                if not key:
                    report(name, False, f"Variable {key_name} absente ou vide")
                    return
            try:
                func()
            except Exception as exc:
                report(name, False, f"Exception : {type(exc).__name__}: {exc}")
        return wrapper
    return decorator


def http_get(url: str, **kwargs) -> requests.Response:
    """GET avec timeout court pour ne pas bloquer le diagnostic."""
    kwargs.setdefault("timeout", 10)
    return requests.get(url, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Tests par API
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
        report("CoinGecko", False, f"HTTP {r.status_code} : {r.text[:80]}")
        return
    price = r.json().get("bitcoin", {}).get("usd")
    if price:
        report("CoinGecko", True, f"BTC = ${price:,}")
    else:
        report("CoinGecko", False, f"Réponse vide : {r.text[:80]}")


@test("CoinMarketCap", "COINMARKETCAP_API_KEY")
def test_coinmarketcap():
    key = os.environ["COINMARKETCAP_API_KEY"]
    r = http_get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
        params={"symbol": "BTC"},
        headers={"X-CMC_PRO_API_KEY": key, "Accept": "application/json"},
    )
    if r.status_code != 200:
        report("CoinMarketCap", False, f"HTTP {r.status_code} : {r.text[:80]}")
        return
    data = r.json()
    price = data.get("data", {}).get("BTC", {}).get("quote", {}).get("USD", {}).get("price")
    if price:
        report("CoinMarketCap", True, f"BTC = ${price:,.0f}")
    else:
        report("CoinMarketCap", False, "Pas de prix dans la réponse")


@test("Fear & Greed", None)
def test_fear_greed():
    r = http_get("https://api.alternative.me/fng/", params={"limit": 1})
    if r.status_code != 200:
        report("Fear & Greed", False, f"HTTP {r.status_code}")
        return
    data = r.json().get("data", [])
    if data:
        report("Fear & Greed", True, f"F&G = {data[0].get('value')} ({data[0].get('value_classification')})")
    else:
        report("Fear & Greed", False, "Réponse vide")


@test("FRED (macro)", "FRED_API_KEY")
def test_fred():
    key = os.environ["FRED_API_KEY"]
    r = http_get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": "DTWEXBGS",
            "api_key": key,
            "file_type": "json",
            "limit": 1,
            "sort_order": "desc",
        },
    )
    if r.status_code != 200:
        report("FRED (macro)", False, f"HTTP {r.status_code} : {r.text[:80]}")
        return
    obs = r.json().get("observations", [])
    if obs:
        report("FRED (macro)", True, f"DXY = {obs[0].get('value')} (le {obs[0].get('date')})")
    else:
        report("FRED (macro)", False, "Pas d'observation")


@test("NewsAPI", "NEWSAPI_KEY")
def test_newsapi():
    key = os.environ["NEWSAPI_KEY"]
    r = http_get(
        "https://newsapi.org/v2/everything",
        params={"q": "bitcoin", "pageSize": 1, "language": "en", "sortBy": "publishedAt"},
        headers={"X-Api-Key": key},
    )
    if r.status_code != 200:
        report("NewsAPI", False, f"HTTP {r.status_code} : {r.text[:120]}")
        return
    data = r.json()
    total = data.get("totalResults", 0)
    articles = data.get("articles", [])
    if articles:
        title = articles[0].get("title", "")[:60]
        report("NewsAPI", True, f"{total} articles dispo, dernier: '{title}...'")
    else:
        report("NewsAPI", False, f"Pas d'articles (status: {data.get('status')})")


@test("Etherscan", "ETHERSCAN_API_KEY")
def test_etherscan():
    key = os.environ["ETHERSCAN_API_KEY"]
    r = http_get(
        "https://api.etherscan.io/api",
        params={
            "module": "stats",
            "action": "ethsupply",
            "apikey": key,
        },
    )
    if r.status_code != 200:
        report("Etherscan", False, f"HTTP {r.status_code}")
        return
    data = r.json()
    if data.get("status") == "1":
        supply_wei = int(data.get("result", 0))
        supply_eth = supply_wei / 1e18
        report("Etherscan", True, f"ETH supply = {supply_eth:,.0f} ETH")
    else:
        report("Etherscan", False, f"API error: {data.get('message')} / {data.get('result')}")


@test("GitHub Token", "GH_TOKEN")
def test_github():
    token = os.environ["GH_TOKEN"]
    r = http_get(
        "https://api.github.com/repos/bitcoin/bitcoin",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
    )
    if r.status_code != 200:
        report("GitHub Token", False, f"HTTP {r.status_code} : {r.text[:80]}")
        return
    data = r.json()
    stars = data.get("stargazers_count", 0)
    # Check rate limit
    remaining = r.headers.get("X-RateLimit-Remaining", "?")
    report("GitHub Token", True, f"bitcoin/bitcoin = {stars:,} stars (quota restant: {remaining})")


@test("YouTube Data", "YOUTUBE_API_KEY")
def test_youtube():
    key = os.environ["YOUTUBE_API_KEY"]
    # Channel UC4nXWTjZqK4G_n-DhxxnpBA = "Hasheur" (mentionné dans ton compact)
    r = http_get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "channelId": "UC4nXWTjZqK4G_n-DhxxnpBA",
            "order": "date",
            "maxResults": 1,
            "key": key,
        },
    )
    if r.status_code != 200:
        report("YouTube Data", False, f"HTTP {r.status_code} : {r.text[:120]}")
        return
    items = r.json().get("items", [])
    if items:
        title = items[0].get("snippet", {}).get("title", "")[:60]
        report("YouTube Data", True, f"Hasheur dernière vidéo: '{title}...'")
    else:
        report("YouTube Data", False, "Pas de vidéos retournées")


@test("Coinglass", "COINGLASS_API_KEY")
def test_coinglass():
    key = os.environ["COINGLASS_API_KEY"]
    r = http_get(
        "https://open-api-v3.coinglass.com/api/futures/openInterest/exchange-list",
        params={"symbol": "BTC"},
        headers={"CG-API-KEY": key},
    )
    if r.status_code != 200:
        report("Coinglass", False, f"HTTP {r.status_code} : {r.text[:120]}")
        return
    data = r.json()
    if data.get("code") == "0" and data.get("data"):
        report("Coinglass", True, f"BTC OI data dispo ({len(data['data'])} exchanges)")
    else:
        report("Coinglass", False, f"API error: {data.get('msg', data)}")


@test("CryptoQuant", "CRYPTOQUANT_API_KEY")
def test_cryptoquant():
    key = os.environ["CRYPTOQUANT_API_KEY"]
    r = http_get(
        "https://api.cryptoquant.com/v1/btc/exchange-flows/inflow",
        params={"exchange": "binance", "window": "day", "limit": 1},
        headers={"Authorization": f"Bearer {key}"},
    )
    if r.status_code != 200:
        report("CryptoQuant", False, f"HTTP {r.status_code} : {r.text[:120]}")
        return
    data = r.json()
    result = data.get("result", {}).get("data", [])
    if result:
        report("CryptoQuant", True, f"BTC inflow Binance = {result[0]}")
    else:
        report("CryptoQuant", False, f"Réponse vide ou erreur : {data}")


@test("Telegram session", "TELEGRAM_SESSION_STRING")
def test_telegram():
    session = os.environ["TELEGRAM_SESSION_STRING"]
    # Test 1 : longueur multiple de 4 ?
    if len(session) % 4 != 0:
        report("Telegram session", False,
               f"Session corrompue: {len(session)} caractères (doit être multiple de 4)")
        return
    # Test 2 : base64 décodable ?
    try:
        import base64
        base64.b64decode(session)
        report("Telegram session", True, f"Session valide ({len(session)} chars, base64 OK)")
    except Exception as e:
        report("Telegram session", False, f"Base64 invalide: {e}")


@test("DeFiLlama", None)
def test_defillama():
    r = http_get("https://api.llama.fi/v2/chains")
    if r.status_code != 200:
        report("DeFiLlama", False, f"HTTP {r.status_code}")
        return
    chains = r.json()
    if chains and isinstance(chains, list):
        total_tvl = sum(c.get("tvl", 0) for c in chains if c.get("tvl"))
        report("DeFiLlama", True, f"TVL global = ${total_tvl/1e9:,.1f}B sur {len(chains)} chaînes")
    else:
        report("DeFiLlama", False, "Réponse vide")


@test("Reddit (public)", None)
def test_reddit():
    r = http_get(
        "https://www.reddit.com/r/CryptoCurrency/hot.json",
        params={"limit": 1},
        headers={"User-Agent": "crypto-analyst-pro-diag/1.0"},
    )
    if r.status_code != 200:
        report("Reddit (public)", False, f"HTTP {r.status_code}")
        return
    posts = r.json().get("data", {}).get("children", [])
    if posts:
        title = posts[0].get("data", {}).get("title", "")[:60]
        report("Reddit (public)", True, f"r/CryptoCurrency hot: '{title}...'")
    else:
        report("Reddit (public)", False, "Pas de posts")


@test("Polymarket", None)
def test_polymarket():
    r = http_get(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": 1, "active": "true"},
    )
    if r.status_code != 200:
        report("Polymarket", False, f"HTTP {r.status_code}")
        return
    data = r.json()
    if isinstance(data, list) and data:
        report("Polymarket", True, f"{len(data)}+ marchés actifs (1er: {data[0].get('question', '')[:60]}...)")
    else:
        report("Polymarket", False, "Réponse vide")


def test_coinmetrics():
    """On-chain avancé (Coin Metrics Community — sans clé). Améliorations V10."""
    r = http_get(
        "https://api.coinmetrics.io/v4/timeseries/asset-metrics",
        params={
            "assets": "btc",
            "metrics": "PriceUSD,CapMVRVCur,NVTAdj",
            "frequency": "1d",
            "page_size": 1,
        },
    )
    if r.status_code != 200:
        report("Coin Metrics", False, f"HTTP {r.status_code} (métriques on-chain MVRV/NVT)")
        return
    data = r.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if rows:
        mvrv = rows[0].get("CapMVRVCur", "?")
        report("Coin Metrics", True, f"on-chain OK (BTC MVRV={mvrv})")
    else:
        report("Coin Metrics", False, "Réponse sans données")


def test_deribit():
    """Dérivés options (Deribit public — sans clé). Améliorations V10."""
    r = http_get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
        params={"currency": "BTC", "kind": "option"},
    )
    if r.status_code != 200:
        report("Deribit", False, f"HTTP {r.status_code} (put/call · max pain · DVOL)")
        return
    data = r.json()
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(result, list) and result:
        report("Deribit", True, f"{len(result)} instruments options BTC")
    else:
        report("Deribit", False, "Réponse vide")


@test("Gemini", "GEMINI_API_KEY")
def test_gemini():
    key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": "Say OK"}]}]},
        timeout=15,
    )
    if r.status_code != 200:
        report("Gemini", False, f"HTTP {r.status_code} : {r.text[:120]}")
        return
    data = r.json()
    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if text:
        report("Gemini", True, f"Modèle {model} OK (répond: '{text.strip()[:30]}')")
    else:
        report("Gemini", False, f"Pas de réponse texte: {data}")


@test("Gmail SMTP", "GMAIL_APP_PASSWORD")
def test_gmail():
    import smtplib
    user = os.environ.get("GMAIL_USER", "").strip()
    pwd = os.environ["GMAIL_APP_PASSWORD"]
    if not user:
        report("Gmail SMTP", False, "GMAIL_USER absent")
        return
    try:
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        s.login(user, pwd)
        s.quit()
        report("Gmail SMTP", True, f"Auth OK pour {user}")
    except smtplib.SMTPAuthenticationError as e:
        report("Gmail SMTP", False, f"Auth refusée: {e.smtp_code} {e.smtp_error}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 70)
    print("  DIAGNOSTIC API — Crypto Analyst Pro")
    print("=" * 70)
    print()

    # 1. Inventaire des variables présentes
    expected = [
        "GEMINI_API_KEY", "GEMINI_MODEL", "GMAIL_USER", "GMAIL_APP_PASSWORD",
        "RECIPIENT_EMAIL", "COINGECKO_API_KEY", "COINMARKETCAP_API_KEY",
        "FRED_API_KEY", "NEWSAPI_KEY", "ETHERSCAN_API_KEY", "GH_TOKEN",
        "YOUTUBE_API_KEY", "COINGLASS_API_KEY", "CRYPTOQUANT_API_KEY",
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING",
    ]
    print(f"{DIM}[1/3] Variables d'environnement :{RESET}")
    missing = []
    for var in expected:
        val = os.environ.get(var, "").strip()
        if val:
            preview = val[:8] + "…" if len(val) > 12 else "(courte)"
            print(f"  {OK} {var:30s} = {preview}")
        else:
            missing.append(var)
            print(f"  {KO} {var:30s} = (vide)")
    print()

    if missing:
        print(f"{WARN} {len(missing)} variable(s) manquante(s). Le test continuera "
              f"mais les sources concernées seront marquées KO.")
        print()

    # 2. Tests live
    print(f"{DIM}[2/3] Tests live (1 appel par API) :{RESET}")
    print()
    test_coingecko()
    test_coinmarketcap()
    test_fear_greed()
    test_fred()
    test_newsapi()
    test_etherscan()
    test_github()
    test_youtube()
    test_coinglass()
    test_cryptoquant()
    test_telegram()
    test_defillama()
    test_reddit()
    test_polymarket()
    test_coinmetrics()
    test_deribit()
    test_gemini()
    test_gmail()
    print()

    # 3. Synthèse
    print(f"{DIM}[3/3] Synthèse :{RESET}")
    print()
    ok = [n for n, (s, _) in results.items() if s]
    ko = [n for n, (s, _) in results.items() if not s]
    print(f"  {OK} Fonctionnent ({len(ok)}) : {', '.join(ok)}")
    print()
    if ko:
        print(f"  {KO} Ne fonctionnent pas ({len(ko)}) :")
        for name in ko:
            print(f"      • {name} : {results[name][1]}")
    print()
    print("=" * 70)
    return 0 if not ko else 1


if __name__ == "__main__":
    sys.exit(main())
