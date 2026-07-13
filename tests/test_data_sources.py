"""Tests des data sources : focalisés sur la robustesse (dégradation gracieuse).

Ces tests ne tapent PAS les vraies APIs (pas de réseau en CI sans secrets).
Ils vérifient que les fonctions renvoient des structures correctes même quand
les clés sont absentes ou les appels échouent.
"""

from __future__ import annotations


from src.data_sources import coinmarketcap, fred, onchain_eth


def test_cmc_no_key_returns_empty(monkeypatch) -> None:
    """Sans clé CMC, get_quotes renvoie un dict vide sans planter."""
    monkeypatch.delenv("COINMARKETCAP_API_KEY", raising=False)
    assert coinmarketcap.get_quotes(["BTC"]) == {}


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

