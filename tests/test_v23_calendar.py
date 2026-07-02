"""v23.x — AUDIT du calendrier « Semaine à venir » (demande d'Omar : un seul
événement s'affichait certaines semaines).

Cause trouvée : (1) FRED ne mappait que 5 publications mensuelles → souvent 0-1
dans la fenêtre ; (2) le scraper Boursorama récupérait la table de COTATIONS (le
calendrier est rendu en JS, absent du HTML statique) et se déclarait « actif » à
tort. Corrigés : FRED élargi (dont jobless claims HEBDO) + Boursorama honnête.
"""

from __future__ import annotations

from datetime import date, timedelta


def test_fred_upcoming_releases_expanded():
    from src.data_sources import fred
    # release 180 = inscriptions chômage HEBDO → ≥ 1 événement garanti/semaine.
    assert 180 in fred._UPCOMING_RELEASES
    label, importance = fred._UPCOMING_RELEASES[180]
    assert ("chômage" in label.lower() or "claims" in label.lower())
    assert importance in ("high", "medium")
    assert len(fred._UPCOMING_RELEASES) >= 8       # élargi (était 5)


def test_fred_releases_carry_importance(monkeypatch):
    from src.data_sources import fred
    monkeypatch.setenv("FRED_API_KEY", "x")
    fut = (date.today() + timedelta(days=2)).isoformat()
    monkeypatch.setattr(fred, "get_json", lambda *a, **k: {"release_dates": [{"date": fut}]})
    try:
        fred.CACHE._store.clear()
    except Exception:
        pass
    out = fred.get_upcoming_releases(horizon_days=8)
    assert out["available"] and out["events"]
    assert all("importance" in e for e in out["events"])   # propagée pour le rendu


def test_boursorama_calendar_heuristic():
    from src.data_sources import boursorama_calendar as bc
    # Vraie ligne de calendrier (heure + mot-clé macro) → gardée.
    assert bc._looks_like_calendar_event(
        {"time": "14:30", "country": "États-Unis", "event": "Inflation CPI"})
    # Ligne de COTATION (asset + prix + %) → rejetée.
    assert not bc._looks_like_calendar_event(
        {"time": "Pétrole Brent", "country": "72,8", "event": "-2,73%"})
    assert not bc._looks_like_calendar_event(
        {"time": "valeur", "country": "dernier", "event": "var."})


def test_boursorama_rejects_quotes_table(monkeypatch):
    """Une page ne contenant qu'une table de cotations → available False HONNÊTE
    (avant : 'available True' avec 6 fausses lignes)."""
    import requests
    from src.data_sources import boursorama_calendar as bc
    html = ("<html><body><table>"
            "<tr><td>valeur</td><td>dernier</td><td>var.</td></tr>"
            "<tr><td>CAC 40</td><td>8 367,33</td><td>-0,21%</td></tr>"
            "<tr><td>Or</td><td>4 016,14</td><td>-0,04%</td></tr>"
            "</table></body></html>")

    class _Resp:
        status_code = 200
        text = html

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    try:
        bc.CACHE._store.clear()
    except Exception:
        pass
    out = bc.get_boursorama_calendar()
    assert out["available"] is False
    assert "JS" in (out.get("reason") or "") or "statique" in (out.get("reason") or "")


def test_boursorama_keeps_real_calendar_rows(monkeypatch):
    """Si le HTML contient de VRAIES lignes de calendrier, elles sont gardées."""
    import requests
    from src.data_sources import boursorama_calendar as bc
    html = ("<html><body><table>"
            "<tr><td>14:30</td><td>États-Unis</td><td>Emploi NFP</td><td>200k</td></tr>"
            "<tr><td>16:00</td><td>Zone euro</td><td>Confiance consommateur</td><td>-15</td></tr>"
            "</table></body></html>")

    class _Resp:
        status_code = 200
        text = html

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    try:
        bc.CACHE._store.clear()
    except Exception:
        pass
    out = bc.get_boursorama_calendar()
    assert out["available"] is True
    labels = " ".join(e["event"] for e in out["events"]).lower()
    assert "nfp" in labels and "confiance" in labels


def test_consolidated_calendar_merges_multiple_fred(monkeypatch):
    """Plusieurs publications FRED dans la fenêtre → plusieurs événements affichés
    (le bug « un seul événement » ne se reproduit plus tant que FRED répond)."""
    from src.data_sources import macro_calendar as mc
    d1 = (date.today() + timedelta(days=2)).isoformat()
    d2 = (date.today() + timedelta(days=4)).isoformat()
    monkeypatch.setattr(mc.fred, "get_upcoming_releases", lambda horizon_days=10: {
        "available": True, "events": [
            {"label": "Emploi US (NFP)", "date": d1, "importance": "high"},
            {"label": "Inscriptions chômage hebdo", "date": d2, "importance": "medium"},
        ]})
    monkeypatch.setattr(mc, "get_boursorama_calendar", lambda: {"available": False})
    from src.data_sources import econ_calendar as _ec
    monkeypatch.setattr(_ec, "get_econ_calendar",
                        lambda horizon_days=8: {"available": False, "events": []})
    try:
        mc.CACHE._store.clear()
    except Exception:
        pass
    out = mc.get_consolidated_calendar(horizon_days=8)
    assert out["available"]
    labels = [e["label"] for e in out["events"]]
    assert any("NFP" in lbl for lbl in labels)
    assert any("chômage" in lbl.lower() for lbl in labels)
    # l'importance FRED est propagée (high vs medium), pas forcée à high.
    imp = {e["label"]: e.get("importance") for e in out["events"]}
    assert "medium" in imp.values()
