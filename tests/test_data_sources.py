"""Tests des data sources : focalisés sur la robustesse (dégradation gracieuse).

Ces tests ne tapent PAS les vraies APIs (pas de réseau en CI sans secrets).
Ils vérifient que les fonctions renvoient des structures correctes même quand
les clés sont absentes ou les appels échouent.
"""

from __future__ import annotations

import os

from src.data_sources import coinmarketcap, cryptopanic, fred, onchain_eth


def test_cmc_no_key_returns_empty(monkeypatch) -> None:
    """Sans clé CMC, get_quotes renvoie un dict vide sans planter."""
    monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
    assert coinmarketcap.get_quotes(["BTC"]) == {}


def test_cryptopanic_no_key(monkeypatch) -> None:
    """Sans clé CryptoPanic, news indisponibles mais structure cohérente."""
    monkeypatch.delenv("CRYPTOPANIC_API_KEY", raising=False)
    result = cryptopanic.get_news(["BTC"])
    assert result["available"] is False
    assert result["items"] == []


def test_fred_no_key(monkeypatch) -> None:
    """Sans clé FRED, macro indisponible mais clé 'series' présente."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    result = fred.get_macro()
    assert result["available"] is False
    assert "series" in result


def test_etherscan_no_key(monkeypatch) -> None:
    """Sans clé Etherscan, on-chain ETH indisponible."""
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    assert onchain_eth.get_eth_onchain()["available"] is False


def test_news_score_by_symbol() -> None:
    """Le scoring de news par symbole agrège correctement."""
    news = {
        "items": [
            {"currencies": ["BTC"], "sentiment": 0.8},
            {"currencies": ["BTC", "ETH"], "sentiment": -0.5},
        ]
    }
    scores = cryptopanic.news_score_by_symbol(news, ["BTC", "ETH", "XRP"])
    assert "BTC" in scores and scores["BTC"] > 0
    assert "XRP" not in scores  # pas de news -> absent
