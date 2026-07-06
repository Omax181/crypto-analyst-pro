"""Tests v20 — corrections de l'audit des 3 emails (passage v19 → v20).

Couvre au fil de l'eau les points de l'audit. Bloc 1 : charts/graphiques
Gmail-safe (images CID au lieu de data-URI/SVG inline) + version.
"""

from __future__ import annotations

import src.reporting.email_sender as es
from src.reporting import charts
from src.reporting.email_html import APP_VERSION, render


# --------------------------------------------------------------------------- #
# C1/M1 — graphiques en images CID (Gmail-safe)
# --------------------------------------------------------------------------- #
def test_charts_return_png_bytes(monkeypatch) -> None:
    """price_bollinger_png / charts_for_theses renvoient des OCTETS PNG (pour
    pièce jointe CID), plus du base64."""
    from src.data_sources import coingecko
    closes = [100 + (i % 7) for i in range(40)]
    monkeypatch.setattr(coingecko, "get_price_volume_series",
                        lambda sym, days=90: {"closes": closes})
    png = charts.price_bollinger_png("ETH")
    assert isinstance(png, (bytes, bytearray))
    assert png[:4] == b"\x89PNG"
    imgs = charts.charts_for_theses([{"asset": "ETH"}], limit=2)
    assert isinstance(imgs.get("ETH"), (bytes, bytearray))


def test_morning_template_emits_cid_not_data_uri() -> None:
    """Le template matin référence les graphiques en cid:chart_<ASSET>, jamais
    en data:image/png;base64 (que Gmail supprime)."""
    payload = {
        "header": {"date": "19/06"},
        "thesis_of_the_day": [
            {"asset": "ETH", "action": "RENFORCER", "confidence": 70,
             "reasoning_signals": ["signal"], "action_plan": {}},
        ],
    }
    html = render(payload, "morning", charts={"ETH": b"\x89PNG0000000000"})
    assert "cid:chart_ETH" in html
    assert "data:image/png" not in html


class _FakeSMTP:
    last_msg = ""

    def __init__(self, *a, **k) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, frm, to, msg):
        _FakeSMTP.last_msg = msg


def _env_mail(monkeypatch):
    monkeypatch.setattr(es.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("GMAIL_USER", "x@y.z")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("RECIPIENT_EMAIL", "to@y.z")


def test_send_email_attaches_cid_images(monkeypatch) -> None:
    """Avec inline_images, l'email est un multipart/related portant un
    Content-ID référençable par <img src='cid:...'>."""
    _env_mail(monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 60
    ok = es.send_email(
        "Sujet", "<p><img src='cid:chart_ETH'></p>",
        inline_images={"chart_ETH": png})
    assert ok is True
    msg = _FakeSMTP.last_msg
    assert "multipart/related" in msg
    assert "Content-ID: <chart_ETH>" in msg


def test_send_email_without_images_stays_alternative(monkeypatch) -> None:
    """Sans images, comportement v18 inchangé (multipart/alternative)."""
    _env_mail(monkeypatch)
    ok = es.send_email("Sujet", "<p>Hello</p>")
    assert ok is True
    assert "multipart/alternative" in _FakeSMTP.last_msg


# --------------------------------------------------------------------------- #
# C2 — version produit bumpée v20
# --------------------------------------------------------------------------- #
def test_app_version_is_v20() -> None:
    # Nommage final : le livrable est étiqueté v26 (décision Omar, 2026-07-05 —
    # la v25 existe déjà sur main).
    assert APP_VERSION == "v26"
    payload = {"header": {"date": "19/06"}, "portfolio_snapshot": {"value_usd": 1.0}}
    html = render(payload, "weekly")
    assert "v26" in html
    assert "v18" not in html


# --------------------------------------------------------------------------- #
# W2 — pas de faux « 100 % win rate » legacy dans le bilan hebdo
# --------------------------------------------------------------------------- #
def test_w2_legacy_closed_reco_excluded_from_weekly(monkeypatch) -> None:
    """Une reco LEGACY (émise il y a 60 j) mais re-clôturée récemment ne doit pas
    compter dans le bilan « de la semaine » (fenêtre sur created_at)."""
    import datetime as dt
    from src.tracking import prediction_scoring as ps
    now = dt.datetime.now(dt.timezone.utc)
    legacy = (now - dt.timedelta(days=60)).isoformat()
    recent = (now - dt.timedelta(days=1)).isoformat()
    hist = [{"asset": "OLD", "action": "RENFORCER", "created_at": legacy,
             "closed_at": recent, "entry_price": 1.0, "status": "validated"}]
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: hist)
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [])
    detail = ps.PredictionTracker().build_scoring_detail({"OLD": 2.0}, 7)
    closed = [r for r in detail if r.get("score") in (1, -1)]
    assert closed == []  # legacy (created 60 j) hors fenêtre 7 j → 0 validée


def test_w2_recent_closed_reco_still_counts(monkeypatch) -> None:
    """Non-régression : une reco émise DANS la fenêtre et clôturée compte bien."""
    import datetime as dt
    from src.tracking import prediction_scoring as ps
    now = dt.datetime.now(dt.timezone.utc)
    created = (now - dt.timedelta(days=3)).isoformat()
    closed = (now - dt.timedelta(days=1)).isoformat()
    hist = [{"asset": "ETH", "action": "RENFORCER", "created_at": created,
             "closed_at": closed, "entry_price": 1500.0, "status": "validated"}]
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: hist)
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [])
    detail = ps.PredictionTracker().build_scoring_detail({"ETH": 1650.0}, 7)
    assert any(r["asset"] == "ETH" and r["score"] == 1 for r in detail)


# --------------------------------------------------------------------------- #
# A1 — Morning : tableau récap + détail complet limité au top-3
# --------------------------------------------------------------------------- #
def test_a1_summary_table_all_theses_detail_only_top3() -> None:
    syms = ["ETH", "TAO", "LINK", "INJ", "XRP"]
    theses = []
    for i, sym in enumerate(syms):
        theses.append({
            "asset": sym, "action": "RENFORCER", "confidence": 70 - i,
            "action_type": "bullish",
            "action_plan": {"entry": 100 + i, "stop_loss": 90 + i,
                            "take_profit": {"tp1": 130 + i}},
            "thesis_scoring": {"score": 6 - i, "threshold": 3,
                               "signals": [{"label": "drawdown", "weight": 3}]},
            "reasoning_signals": [f"RAISONMARK{sym}"],
            "_expand": i < 3,
        })
    payload = {"header": {"date": "19/06"}, "thesis_of_the_day": theses}
    html = render(payload, "morning")
    # Tableau récap : TOUS les actifs présents.
    for sym in syms:
        assert sym in html
    # Détail (raisonnement) seulement pour le top-3 (_expand=True).
    assert "RAISONMARKETH" in html and "RAISONMARKLINK" in html
    assert "RAISONMARKINJ" not in html and "RAISONMARKXRP" not in html
    assert "plus fortes convictions" in html  # légende ★
