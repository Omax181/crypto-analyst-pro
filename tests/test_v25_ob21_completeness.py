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


# v33 (audit 01/10) — « test_partial_completeness_shows_warning_and_missing » retiré : la complétude s'affichait dans le score V30 ; elle est désormais la « couverture des sources » de la fiche moteur (test ci-dessous)


# v33 (audit 01/10) — « test_full_completeness_no_warning » retiré : la complétude s'affichait dans le score V30 ; elle est désormais la « couverture des sources » de la fiche moteur (test ci-dessous)


def test_no_completeness_renders_without_error():
    """v33 — la couverture des sources (et ses manques) figure dans la fiche
    du moteur ; son absence ne casse rien."""
    from src.reporting.email_html import render
    from tests.test_v32_redteam import _these_moteur
    t = _these_moteur()
    t["engine_view"]["coverage_pct"] = 40
    t["engine_view"]["coverage_missing"] = ["dérivés", "sentiment"]
    html = render({"thesis_of_the_day": [t]}, "morning")
    assert "couverture des sources 40 %" in html and "dérivés" in html
    t2 = _these_moteur()
    t2["engine_view"]["coverage_pct"] = None
    html2 = render({"thesis_of_the_day": [t2]}, "morning")
    assert "Décision du moteur" in html2 and "couverture des sources" not in html2


