"""Tests de bout en bout V2 (sans réseau ni Gemini réel)."""

from __future__ import annotations

import pathlib
import tempfile

from src.reporting.email_html import render
from src.utils.portfolio_loader import load_portfolio, total_value_usd


def test_portfolio_loads_and_validates() -> None:
    """Le portfolio se charge et chaque actif a un tier valide."""
    data = load_portfolio()
    assert data["portfolio"]
    for sym, info in data["portfolio"].items():
        assert info["tier"] in (1, 2, 3, 4), f"{sym} tier invalide"
    assert total_value_usd(data) > 0


def test_render_morning_minimal() -> None:
    """Un payload matin minimal produit un HTML valide."""
    payload = {
        "header": {"date": "2026-05-27", "time_casablanca": "08:30"},
        "story_of_the_day": {"narrative": "Marché calme."},
        "footer": {"next_report_at": "ce soir"},
    }
    html = render(payload, "morning")
    assert "Veille crypto" in html and len(html) > 1000


def test_render_all_kinds() -> None:
    """Les 3 types de rapport rendent sans erreur."""
    expected_titles = {
        "morning": "Veille crypto · matin",
        "evening": "Veille crypto · soir",
        "weekly": "Rapport hebdomadaire",
    }
    for kind, title in expected_titles.items():
        html = render({"header": {"date": "x"}, "footer": {}}, kind)
        assert title in html, f"{kind} doit contenir '{title}'"


def test_degraded_payload_renders() -> None:
    """Le payload dégradé (IA indisponible) reste rendu sans erreur."""
    from src.ai_brain.decision_engine import DecisionEngine

    payload = DecisionEngine._degraded("morning", {}, "Test indispo")
    html = render(payload, "morning")
    assert "Veille crypto" in html and len(html) > 1000


def test_memory_roundtrip(monkeypatch) -> None:
    """La mémoire écrit et relit correctement un rapport."""
    from src.state import report_memory as mem

    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(mem, "_STATE_DIR", tmp)
    mem.save_morning_report({"key": "value"})
    loaded = mem.load_morning_report()
    assert loaded["key"] == "value"
