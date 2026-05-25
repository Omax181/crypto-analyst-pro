"""Tests de bout en bout (sans réseau ni Gemini réel)."""

from __future__ import annotations

from src.reporting.email_html import render_alert, render_report
from src.utils.portfolio_loader import load_portfolio, total_value_usd


def test_portfolio_loads_and_validates() -> None:
    """Le portfolio se charge et chaque actif a un tier valide."""
    data = load_portfolio()
    assert data["portfolio"]
    for sym, info in data["portfolio"].items():
        assert info["tier"] in (1, 2, 3, 4), f"{sym} tier invalide"
    assert total_value_usd(data) > 0


def test_render_report_minimal() -> None:
    """Un payload minimal produit un HTML valide (sections vides omises)."""
    payload = {
        "header": {"title": "Veille crypto", "subtitle": "test"},
        "essentiel": ["Rien de notable aujourd'hui"],
        "footer": "Prochain rapport ce soir",
    }
    html = render_report(payload)
    assert "<html" in html and "Veille crypto" in html
    # Pas de section positions vide affichée.
    assert "mérite attention" not in html


def test_render_report_full() -> None:
    """Un payload complet rend toutes les sections."""
    payload = {
        "header": {"title": "T", "subtitle": "s"},
        "essentiel": ["a"],
        "marche_global": {"commentaire": "c", "indicateurs": {"BTC": "$1"}, "narratives": "n"},
        "macro": {"indicateurs": "m", "calendrier": ["e"], "geopolitique": "g"},
        "positions": [
            {"symbol": "BTC", "pourquoi": "p", "lecture": "l", "avis": "a",
             "invalidation": "i", "sources": ["CG"]}
        ],
        "spikes": [{"symbol": "ETH", "change_24h": "+6%", "note": "n"}],
        "sante_projets": {"global_ok": True, "alertes": []},
        "footer": "f",
    }
    html = render_report(payload)
    for token in ("BTC", "ETH", "aucun signal", "Invalidation"):
        assert token in html


def test_render_alert_severities() -> None:
    """L'alerte rend correctement selon la sévérité."""
    for sev in ("info", "warning", "danger"):
        html = render_alert({"title": f"t-{sev}", "body": "b", "severity": sev}, "now")
        assert f"t-{sev}" in html


def test_degraded_payload_renders() -> None:
    """Le payload dégradé (IA indisponible) reste rendu sans erreur."""
    from src.ai_brain.decision_engine import DecisionEngine

    payload = DecisionEngine._degraded_payload("calm", "Test indispo")
    html = render_report(payload)
    assert "Test indispo" in html
