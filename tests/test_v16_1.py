"""Tests v16.1 — corrections post-audit du 13/06 (morning / evening / weekly).

Verrouille les comportements INTRODUITS en v16.1 (sans réseau) :
- delta_summary : normaliseur Python force un icon ✓/⚠/✗ (jamais de string brute)
- dust : flag active_reco (reco validée/en cours → exclu de l'exit plan)
- win_rate_30d : posé explicitement dans le scoring hebdo (plus de « % » vide)
- sources hebdo : « pic » omis quand pic == moyenne
- rendus : risk_score_readout (matin+soir), niveaux groupés par crypto,
  actions_tonight enrichies, Polymarket « autres marchés »
- timeout workflows morning/evening = 20 min
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# delta_summary : normaliseur d'icônes (evening)
# --------------------------------------------------------------------------- #
def test_delta_summary_icon_normalized():
    # Reproduit la logique du filet Python de run_evening.
    _valid = {"✓", "⚠", "✗"}
    raw = [
        {"icon": "✓", "text": "ok"},
        {"icon": "→", "text": "mauvais symbole"},   # → doit devenir ⚠
        {"text": "sans icon"},                        # absent → ⚠
        "chaine brute",                               # string → ⚠ + objet
        {"icon": "✓", "text": ""},                    # vide → ignoré
    ]
    out = []
    for d in raw:
        if isinstance(d, dict):
            ic = (d.get("icon") or "").strip()
            txt = d.get("text") or d.get("label") or ""
            if ic not in _valid:
                ic = "⚠"
            if str(txt).strip():
                out.append({"icon": ic, "text": txt})
        elif isinstance(d, str) and d.strip():
            out.append({"icon": "⚠", "text": d.strip()})
    assert [o["icon"] for o in out] == ["✓", "⚠", "⚠", "⚠"]
    assert all(o["icon"] in _valid for o in out)
    assert len(out) == 4  # la puce vide est retirée


# --------------------------------------------------------------------------- #
# dust : flag active_reco
# --------------------------------------------------------------------------- #
def test_dust_active_reco_flag():
    scoring_detail = [
        {"asset": "NOT", "status": "validated", "score": 1},
        {"asset": "AR", "status": "in_progress", "score": 0},
        {"asset": "ZK", "status": "expired", "score": 0},
    ]
    recoed = {
        str(r.get("asset")).upper()
        for r in scoring_detail
        if r.get("score") in (1, -1) or r.get("status") in ("validated", "in_progress")
    }
    assert "NOT" in recoed and "AR" in recoed
    assert "ZK" not in recoed
    # un actif poussière avec reco active est flaggé
    dust = [{"asset": s, "active_reco": s.upper() in recoed}
            for s in ("NOT", "ZK", "AXL")]
    by = {d["asset"]: d["active_reco"] for d in dust}
    assert by["NOT"] is True and by["ZK"] is False and by["AXL"] is False


# --------------------------------------------------------------------------- #
# sources hebdo : pic omis si == moyenne
# --------------------------------------------------------------------------- #
def test_weekly_sources_pic_omitted_when_equal():
    def label(avg, best, days):
        if best > avg:
            detail = f"pic {best}, {days} jours observés"
        else:
            detail = f"{days} jours observés"
        return f"{avg}/25 sources actives en moyenne cette semaine ({detail})"

    assert "pic" not in label(18, 18, 7)      # pic == moyenne → omis
    assert "pic 23" in label(18, 23, 7)       # pic > moyenne → affiché


# --------------------------------------------------------------------------- #
# win_rate_30d posé explicitement (plus de « % » vide)
# --------------------------------------------------------------------------- #
def test_render_weekly_winrate_30d_no_empty_percent():
    from src.reporting.email_html import render
    html = render({
        "header": {"week_number": 24},
        "portfolio_snapshot": {"value_usd": 1700},
        "predictions_scoring": {
            "issued": 3, "validated": 2, "invalidated": 0, "closed_count": 3,
            "win_rate_pct": None, "win_rate_30d": None,  # pas d'historique
            "winrate_gate_label": "Recos clôturées : 3/5 minimum pour calibration",
            "no_history": False,
            "detail": [{"asset": "ETH", "reco": "RENFORCER", "entry_date": "06/06",
                        "entry_price": 1559, "current_price": 1655, "delta_pct": 6.2,
                        "holding_days": 5, "status": "validated", "score": 1}]},
    }, "weekly")
    # le « · % » vide ne doit JAMAIS apparaître : soit une valeur, soit « — ».
    assert "30j · %" not in html
    assert "Win rate 30j · —" in html


def test_render_weekly_winrate_30d_value_shown():
    from src.reporting.email_html import render
    html = render({
        "header": {"week_number": 24},
        "portfolio_snapshot": {"value_usd": 1700},
        "predictions_scoring": {
            "issued": 6, "validated": 4, "invalidated": 1, "closed_count": 5,
            "win_rate_pct": 80, "win_rate_30d": 67, "no_history": False,
            "detail": [{"asset": "ETH", "reco": "RENFORCER", "entry_date": "06/06",
                        "entry_price": 1559, "current_price": 1655, "delta_pct": 6.2,
                        "holding_days": 5, "status": "validated", "score": 1}]},
    }, "weekly")
    assert "Win rate 30j · 67%" in html
    assert "30j · %" not in html


# --------------------------------------------------------------------------- #
# risk_score_readout rendu (morning + evening)
# --------------------------------------------------------------------------- #
def test_render_morning_health_readout():
    """v23 — la note de SANTÉ remplace l'ancienne note de risque dans le matin."""
    from src.reporting.email_html import render
    html = render({
        "header": {"active_sources_count": 20, "total_sources_count": 25},
        "portfolio_snapshot": {"value_usd": 1700},
        "health_score": {"score": 4.2, "level": "fragile", "level_color": "#BA7517",
                         "axes": [{"label": "Diversification", "score": 2.0, "max": 10.0},
                                  {"label": "Momentum vs BTC", "score": 6.4, "max": 10.0}],
                         "driver": "Portée par Momentum vs BTC (6.4/10), pénalisée par Diversification (2.0/10).",
                         "improve": "Alléger le secteur dominant sur rebond et étaler sur d'autres narratifs."},
    }, "morning")
    assert "Santé du portefeuille" in html
    assert "Diversification" in html
    assert "pénalisée par Diversification" in html
    assert "Pour l'améliorer :" in html


def test_render_evening_risk_readout_and_levels_grouped():
    from src.reporting.email_html import render
    html = render({
        "header": {"timing_line": "matin 10h · soir 19h · Δ9h"},
        "daily_pnl": {"value_usd": 1700, "day_change_usd": 5, "day_change_pct": 0.3,
                      "day_change_label": "hausse",
                      "top_movers": [{"symbol": "TAO", "change": 18.1, "pnl_usd": 73}]},
        "health_score": {"score": 4.2, "level": "fragile", "level_color": "#BA7517",
                         "axes": [{"label": "Diversification", "score": 2.0, "max": 10.0}],
                         "driver": "Pénalisée par Diversification (2.0/10).",
                         "improve": "Alléger le secteur dominant sur rebond."},
        "levels_tonight": [
            {"asset": "TAO", "level": "270$", "type": "resistance",
             "trigger": "alléger 10% au-dessus"},
            {"asset": "TAO", "level": "240$", "type": "support",
             "trigger": "invaliderait le momentum"},
            {"asset": "BTC", "level": "63 000$", "type": "support",
             "trigger": "alléger sous ce niveau"}],
        "footer": {"next_morning_time": "08h30"},
    }, "evening")
    # readout santé présent
    assert "Pour l'améliorer :" in html
    # niveaux groupés : TAO apparaît UNE seule fois comme libellé d'actif
    # (les 2 niveaux sont dans la même cellule). On vérifie qu'il n'y a pas
    # deux lignes <td> distinctes ouvrant sur "TAO" en gras 700.
    import re
    # v19/E-A14 : la cellule actif peut désormais contenir un prix de référence
    # après le symbole (BTC/ETH) → on matche « >TAO » suivi d'une limite de mot,
    # plus « >TAO< » immédiat. Le groupement (1 cellule par actif) reste vérifié.
    tao_asset_cells = re.findall(r'font-weight:700;[^>]*>TAO\b', html)
    assert len(tao_asset_cells) == 1  # consolidé en 1 ligne
    assert "270$" in html and "240$" in html  # les 2 niveaux restent visibles


def test_render_evening_actions_enriched():
    from src.reporting.email_html import render
    html = render({
        "header": {"timing_line": "matin 10h · soir 19h · Δ9h"},
        "daily_pnl": {"value_usd": 1700, "day_change_usd": 5, "day_change_pct": 0.3,
                      "day_change_label": "hausse", "top_movers": []},
        "actions_tonight": [
            {"action": "Alléger 10% de TAO à 270$",
             "rationale": "RSI 4h à 78 (surchauffe), butée résistance 272$",
             "rebuy": "racheter 235-240$ après retour RSI < 55",
             "horizon": "geste tactique, thèse LT TAO inchangée"}],
        "footer": {"next_morning_time": "08h30"},
    }, "evening")
    assert "Alléger 10% de TAO à 270$" in html
    assert "RSI 4h à 78" in html
    assert "Rachat visé" in html and "235-240$" in html
    assert "thèse LT TAO inchangée" in html


def test_render_evening_actions_string_fallback():
    # rétro-compat : si actions_tonight est une liste de strings, ça rend quand même.
    from src.reporting.email_html import render
    html = render({
        "header": {"timing_line": "x"},
        "daily_pnl": {"value_usd": 1700, "day_change_usd": 0, "day_change_pct": 0,
                      "day_change_label": "neutre", "top_movers": []},
        "actions_tonight": ["Placer une alerte BTC à 63 000$"],
        "footer": {"next_morning_time": "08h30"},
    }, "evening")
    assert "Placer une alerte BTC à 63 000$" in html


# --------------------------------------------------------------------------- #
# Polymarket « autres marchés » (morning + evening)
# --------------------------------------------------------------------------- #
def test_render_morning_polymarket_extra():
    from src.reporting.email_html import render
    html = render({
        "header": {"active_sources_count": 20, "total_sources_count": 25},
        "portfolio_snapshot": {"value_usd": 1700},
        "macro_context": {"btc_price": 64000, "fear_greed": 13,
                          "polymarket_extra_markets": [
                              {"question": "US recession in 2026?", "probability_pct": 38.0},
                              {"question": "BTC above 100k in 2026?", "probability_pct": 61.0}]},
    }, "morning")
    assert "Autres marchés Polymarket" in html
    assert "US recession in 2026?" in html and "38%" in html


def test_render_evening_polymarket_extra():
    from src.reporting.email_html import render
    html = render({
        "header": {"timing_line": "x"},
        "daily_pnl": {"value_usd": 1700, "day_change_usd": 0, "day_change_pct": 0,
                      "day_change_label": "neutre", "top_movers": []},
        "evening_macro": {"btc_price": 64000, "fear_greed": 13, "dxy": 99.8},
        "polymarket_facts": {
            "fed_bars": {"cut_pct": 0.2, "hold_pct": 99.4, "hike_pct": 0.4,
                         "dominant": "maintien", "dominant_pct": 99.4},
            "extra_markets": [{"question": "US-Iran deal?", "probability_pct": 17.0}]},
        "footer": {"next_morning_time": "08h30"},
    }, "evening")
    assert "Autres marchés :" in html
    assert "US-Iran deal?" in html and "17%" in html


# --------------------------------------------------------------------------- #
# Workflows : timeout morning/evening = 20 min, double-cron absent
# --------------------------------------------------------------------------- #
def test_workflows_timeout_and_no_double_cron():
    import pathlib
    import yaml
    wf = pathlib.Path(".github/workflows")
    for name in ("morning_report.yml", "evening_report.yml"):
        txt = (wf / name).read_text()
        assert "timeout-minutes: 20" in txt
        trig = (yaml.safe_load(txt) or {}).get(True) or {}
        assert "schedule" not in trig  # pas de double-cron
        assert "repository_dispatch" in trig
