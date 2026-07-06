# -*- coding: utf-8 -*-
"""OB6 — détection de narratifs émergents (gratuit, remplace Kaito mort).

Teste le filtrage (bruit micro-cap, écosystèmes de chaînes) et le classement
sur données SYNTHÉTIQUES, sans réseau.
"""

from __future__ import annotations

from src.analytics import narratives


def _cats(*rows):
    """rows: (name, market_cap, volume_24h, change_24h)."""
    return {"available": True, "categories": [
        {"name": n, "market_cap": m, "volume_24h": v, "change_24h": c,
         "top_coins": ["x"]}
        for (n, m, v, c) in rows]}


def test_detect_hot_and_cold():
    r = narratives.detect_hot_narratives(_cats(
        ("Real World Assets (RWA)", 5e9, 5e8, 8.2),
        ("AI Agents", 2e9, 2e8, 6.1),
        ("Gaming (GameFi)", 3e9, 1e8, -4.3),
        ("Meme", 6e9, 9e8, 0.5),
    ))
    assert r["available"]
    assert r["hot"][0]["name"] == "Real World Assets (RWA)"
    assert [c["name"] for c in r["hot"]] == ["Real World Assets (RWA)", "AI Agents"]
    assert any("Gaming" in c["name"] for c in r["cold"])
    assert "🔥" in r["reading"] and "🧊" in r["reading"]


def test_filters_microcap_noise():
    r = narratives.detect_hot_narratives(_cats(
        ("Sticker-Themed Coins", 34e6, 1e6, 79.5),   # micro-cap → exclu
        ("Zodiac-Themed", 1e6, 1e5, 36.0),            # micro-cap → exclu
        ("AI", 2e9, 2e8, 5.0),
        ("DePIN", 1e9, 1e8, 4.0),
    ))
    names = [c["name"] for c in r["hot"]]
    assert "Sticker-Themed Coins" not in names
    assert "Zodiac-Themed" not in names
    assert set(names) == {"AI", "DePIN"}


def test_excludes_chain_ecosystems():
    r = narratives.detect_hot_narratives(_cats(
        ("Solana Ecosystem", 1e10, 1e9, 9.0),   # écosystème de chaîne → exclu
        ("Real World Assets", 5e9, 5e8, 7.0),
        ("AI", 2e9, 2e8, 6.0),
    ))
    names = [c["name"] for c in r["hot"]]
    assert "Solana Ecosystem" not in names
    assert "Real World Assets" in names


def test_low_volume_excluded():
    r = narratives.detect_hot_narratives(_cats(
        ("Illiquide", 1e9, 1e5, 20.0),   # gros mcap mais volume ridicule → exclu
        ("AI", 2e9, 2e8, 6.0),
        ("RWA", 5e9, 5e8, 5.0),
    ))
    assert "Illiquide" not in [c["name"] for c in r["hot"]]


def test_excludes_ecosystem_anywhere_in_name():
    r = narratives.detect_hot_narratives(_cats(
        ("Four.meme Ecosystem (BNB Memes)", 1e9, 1e8, 18.0),   # contient ecosystem
        ("Real World Assets", 5e9, 5e8, 7.0),
        ("AI", 2e9, 2e8, 6.0),
    ))
    assert "Four.meme Ecosystem (BNB Memes)" not in [c["name"] for c in r["hot"]]


def test_excludes_extreme_change_artifacts():
    r = narratives.detect_hot_narratives(_cats(
        ("Artefact", 1e9, 1e8, 79.0),   # +79 % = artefact de composition → exclu
        ("RWA", 5e9, 5e8, 7.0),
        ("AI", 2e9, 2e8, 6.0),
    ))
    assert "Artefact" not in [c["name"] for c in r["hot"]]


def test_unavailable_when_source_down():
    assert narratives.detect_hot_narratives({"available": False})["available"] is False


def test_unavailable_when_all_filtered():
    # Une seule ligne exploitable → pas de signal.
    r = narratives.detect_hot_narratives(_cats(("AI", 1e6, 1e5, 5.0)))
    assert r["available"] is False


def test_narratives_line_digest():
    """La ligne de digest (canal LLM) reflète le signal, vide si indisponible."""
    from src.analytics import digests
    line = digests.narratives_line(
        {"available": True, "reading": "🔥 RWA +8.0% | 🧊 Gaming -4.0%"})
    assert line.startswith("Narratifs 24h") and "RWA" in line
    assert digests.narratives_line({"available": False}) == ""
    assert digests.narratives_line({}) == ""


def test_only_cold_when_no_hot():
    r = narratives.detect_hot_narratives(_cats(
        ("A", 1e9, 1e8, 0.5),
        ("B", 1e9, 1e8, -6.0),
        ("C", 1e9, 1e8, -8.0),
    ))
    assert r["available"] and not r["hot"] and r["cold"]
    assert "🧊" in r["reading"] and "🔥" not in r["reading"]
