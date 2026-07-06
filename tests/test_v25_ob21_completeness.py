# -*- coding: utf-8 -*-
"""OB21 — transparence de l'incertitude : la COMPLÉTUDE des données est exhibée
à côté du score pondéré (⚠ si analyse partielle), pour ne pas donner une fausse
impression de précision. Rendu réel via email_html.render."""

from __future__ import annotations

from src.reporting.email_html import render


def _thesis(asset, completeness):
    return {
        "asset": asset, "action": "RENFORCER", "action_type": "bullish",
        "thesis_scoring": {
            "score": 6, "threshold": 3,
            "signals": [{"label": "MVRV", "weight": 3}],
            "dimensions_count": 4,
            "completeness": completeness,
        },
    }


def _render(completeness):
    payload = {"header": {"date": "05/07"},
               "thesis_of_the_day": [_thesis("TAO", completeness)]}
    return render(payload, "morning")


def test_partial_completeness_shows_warning_and_missing():
    html = _render({"pct": 40, "available_count": 2, "total": 6,
                    "missing": ["dérivés", "sentiment", "on-chain"]})
    assert "complétude 40%" in html
    assert "analyse partielle" in html          # ⚠ car < 50 %
    assert "manque : dérivés" in html


def test_full_completeness_no_warning():
    html = _render({"pct": 100, "available_count": 6, "total": 6, "missing": []})
    assert "complétude 100%" in html
    assert "analyse partielle" not in html


def test_no_completeness_renders_without_error():
    """Absence de complétude → bloc omis, aucun crash (rétro-compat)."""
    payload = {"header": {"date": "05/07"}, "thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER", "action_type": "bullish",
        "thesis_scoring": {"score": 5, "threshold": 3,
                           "signals": [{"label": "RSI", "weight": 2}]},
    }]}
    html = render(payload, "morning")
    assert "Score pondéré" in html
    assert "complétude" not in html
