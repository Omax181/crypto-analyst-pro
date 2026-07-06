"""Pertinence crypto des news + nettoyage des titres (v26 E-A3/E-A5/E-B3/E-B4).

L'audit du mail evening v25 a montré la section « Ce qui est tombé depuis ce
matin » polluée à 60 % par du bruit tradfi sans AUCUN lien crypto (« Franklin
U.S. Treasury Bond ETF declares monthly distribution of $$0.07308 », « Dynatrace
plans FedRAMP high authorization »), avec des titres malformés (« $$ ») et des
horodatages ISO bruts (« 2026-07-02T13:58:09+00:00 »).

Règles de pertinence (demande Omar : « pas que crypto, mais crypto related,
lien direct ou indirect ») :
  1. lien DIRECT crypto (bitcoin, stablecoin, exchange, tokenisation…) → gardé,
     même si le titre matche par ailleurs un motif d'exclusion ;
  2. motif d'EXCLUSION (plomberie de fonds tradfi : dividendes/distributions,
     sport, divertissement) → écarté ;
  3. lien INDIRECT macro qui meut la crypto (Fed, inflation, emploi US, dollar,
     géopolitique risk-off) → gardé ;
  4. AUCUN motif reconnu → écarté (défaut-refus : « La France gagne la coupe du
     monde » ne matche rien → dehors).

Tout est matché en MOT ENTIER (pluriel toléré) pour éviter les faux positifs
(« etf » dans « netflix », leçon des audits Polymarket v18/v24).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from typing import Any, Optional


def _wb(words: tuple[str, ...]) -> "re.Pattern[str]":
    """Motif MOT ENTIER, pluriel « s » toléré, insensible à la casse."""
    return re.compile(
        r"\b(" + "|".join(re.escape(w) for w in words) + r")s?\b", re.I
    )


# ── 1. LIEN DIRECT CRYPTO (prioritaire sur tout) ─────────────────────────────
_DIRECT_WORDS = (
    "bitcoin", "btc", "ethereum", "ether", "eth", "crypto", "cryptocurrency",
    "cryptocurrencies", "blockchain", "stablecoin", "usdc", "usdt", "tether",
    "circle", "defi", "nft", "altcoin", "memecoin", "web3", "onchain",
    "on-chain", "solana", "sol", "xrp", "ripple", "cardano", "ada", "dogecoin",
    # Audit v26 final — « gemini » nu retiré : depuis 2025, l'écrasante majorité
    # des titres « Gemini » concernent l'IA de Google (bruit pur pour le PTF) ;
    # l'exchange reste couvert par « gemini exchange »/« winklevoss » et par les
    # mots crypto génériques qui accompagnent ses titres.
    "doge", "bnb", "binance", "coinbase", "kraken", "bitfinex", "okx", "bybit",
    "gemini exchange", "winklevoss",
    "microstrategy", "saylor", "metaplanet", "grayscale", "halving",
    "satoshi", "wallet", "staking", "airdrop", "chainlink", "polkadot",
    "avalanche", "litecoin", "monero", "tron", "cbdc", "ledger", "custody",
    "digital asset", "digital assets", "spot etf", "ondo", "lightning network",
    "token",
)
# Tokenisation : stem explicite en sus (« tokenizes/tokenized/tokenization »
# ne matchent pas \btokens?\b) — « token(s) » est déjà dans la liste mot entier.
_DIRECT_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _DIRECT_WORDS) + r")s?\b"
    r"|\btokeni[sz]\w*\b",
    re.I,
)

# ── 2. EXCLUSIONS DURES (sauf lien direct crypto) ────────────────────────────
_EXCLUDE_PATTERNS = (
    # Plomberie de fonds tradfi : zéro signal marché, pur bruit de flux RSS.
    r"declares\s+(monthly|quarterly|annual|semi-annual)?\s*(distribution|dividend)",
    r"\bdividends?\b", r"\bex-dividend\b", r"distribution of \$",
    r"\bfedramp\b",  # PR IT d'entreprise (« FedRAMP » ≠ « Fed » mot entier)
    # Sport / divertissement / people.
    r"\bworld cup\b", r"\bfifa\b", r"\bnba\b", r"\bnfl\b", r"\bnhl\b", r"\bmlb\b",
    r"\bolympics?\b", r"\bpremier league\b", r"\bchampions league\b",
    r"\bsuper bowl\b", r"\bwimbledon\b", r"\btennis\b", r"\bgolf\b",
    r"\bbox office\b", r"\bgrammy\b", r"\boscars?\b", r"\balbum\b",
    r"\bmovie\b", r"\bcelebrity\b", r"\beurovision\b",
)
_EXCLUDE_RE = re.compile("|".join(_EXCLUDE_PATTERNS), re.I)

# ── 3. LIEN INDIRECT — macro/géopo qui meut la crypto ────────────────────────
_MACRO_WORDS = (
    "fed", "fomc", "powell", "federal reserve", "rate cut", "rate hike",
    "rate decision", "interest rate", "inflation", "cpi", "pce", "nfp",
    "nonfarm", "non-farm", "jobs report", "payroll", "unemployment", "gdp",
    "recession", "treasury yield", "yield", "dollar index", "dxy", "dollar",
    "tariff", "debt ceiling", "shutdown", "liquidity", "quantitative easing",
    "quantitative tightening", "stimulus", "central bank", "ecb", "boj",
    "risk-off", "risk assets", "sec", "etf approval", "capital markets",
    "war", "invasion", "sanctions", "geopolitical", "nuclear", "middle east",
    "iran", "taiwan", "gold price", "safe haven",
)
_MACRO_RE = _wb(_MACRO_WORDS)


def is_crypto_relevant(title: Any) -> bool:
    """True si le titre a un lien direct (crypto) ou indirect (macro) avec le PTF.

    Défaut-refus : un titre qui ne matche rien est écarté — c'est la garantie
    anti-bruit (corp PR, sport, dividendes de fonds obligataires…).
    """
    if not title:
        return False
    t = str(title)
    if _DIRECT_RE.search(t):
        return True
    if _EXCLUDE_RE.search(t):
        return False
    return bool(_MACRO_RE.search(t))


def sanitize_title(title: Any) -> str:
    """Nettoie un titre de flux : « $$0.07 » → « $0.07 », espaces multiples."""
    t = re.sub(r"\${2,}", "$", str(title or ""))
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def fmt_time_local(ts: Any, tz: Optional[tzinfo] = None) -> Optional[str]:
    """Horodatage ISO → « 14h58 » heure locale ; None si non parsable.

    v26 (E-A4/E-B4) : le repli news affichait l'ISO brut
    (« 2026-07-02T13:58:09+00:00 ») — illisible dans un mail.
    """
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if tz is not None:
            d = d.astimezone(tz)
        return d.strftime("%Hh%M")
    except (ValueError, TypeError):
        return None
