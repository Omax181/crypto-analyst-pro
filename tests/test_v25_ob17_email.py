# -*- coding: utf-8 -*-
"""OB17 — hiérarchie du mail matin : affichage DÉTERMINISTE du radar de sortie
(« À considérer pour allègement », OB1) en haute priorité + note transparente de
calibration de confiance (OB24). Rendu réel via email_html.render."""

from __future__ import annotations

from src.reporting.email_html import render


def test_exit_signals_section_rendered_high_priority():
    payload = {
        "header": {"date": "05/07"},
        "exit_signals": {"available": True, "signals": [
            {"symbol": "PEPE", "reason": "×3 atteint (+250% vs PRU)",
             "action": "allège une grosse tranche (prise de profit)",
             "urgency": 3, "pnl_pct": 250},
            {"symbol": "JASMY", "reason": "surpondéré (15% du PTF)",
             "action": "réduis le risque de concentration", "urgency": 1,
             "pnl_pct": 5},
        ]},
    }
    html = render(payload, "morning")
    assert "À considérer pour allègement" in html
    assert "PEPE" in html and "×3 atteint" in html
    assert "allège une grosse tranche" in html
    assert "JASMY" in html


def test_no_exit_signals_no_section():
    html = render({"header": {"date": "05/07"},
                   "exit_signals": {"available": False}}, "morning")
    assert "À considérer pour allègement" not in html


def test_calibration_note_shown_when_active():
    payload = {"header": {"date": "05/07"},
               "confidence_calibration": {
                   "available": True, "multiplier": 0.85,
                   "reason": "sur-confiance historique → confiance réduite ×0.85"}}
    html = render(payload, "morning")
    assert "Calibration auto" in html
    assert "×0.85" in html


def test_calibration_note_hidden_when_neutral():
    payload = {"header": {"date": "05/07"},
               "confidence_calibration": {"available": True, "multiplier": 1.0,
                                          "reason": "calibration correcte"}}
    html = render(payload, "morning")
    assert "Calibration auto" not in html
