"""Tests v17 — corrections de l'audit du 14/06 (morning / evening / weekly).

Verrouille les comportements INTRODUITS en v17 (sans réseau) :
- T-RISK   : 5e barre Volatilité dans components + dominant_axes
- T-FMT    : fmt_num_human (grands nombres) + coercition prix thèses
- T-DEDUP  : active_for_display (1 ligne/actif, enrichie)
- T-SOURCES: _is_truly_active gère les dicts symbole→data (CoinGecko) ;
             _blind_spots audite le set complet
- M-A4     : plancher 10Y relevé (0.45% écarté)
- heatmap  : 2 secteurs + AUTRE (moyenne pondérée)
- M-A12    : libellé catégorie news mappé (pas de troncature mid-mot)
- M-A21    : tickers parasites filtrés
- T-STATE  : delta/timing neutralisés si state matin périmé
- W-A3/W-A4: <details> retiré (Gmail) + |md sur concentration_reading
- M-B2     : take profit retiré du plan d'action (cibles dédupliquées)
- M-B7     : top movers marché réduits à 2+2
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# T-RISK (M-A5/M-A18) — 5e barre Volatilité + axes dominants
# --------------------------------------------------------------------------- #
def _risk_inputs():
    import sys
    import types
    ten = types.ModuleType("tenacity")
    ten.retry = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    ten.stop_after_attempt = ten.wait_exponential = ten.retry_if_exception_type = lambda *a, **k: None
    ten.before_sleep_log = lambda *a, **k: (lambda *aa, **kk: None)
    sys.modules.setdefault("tenacity", ten)


def test_risk_score_has_volatility_bar_and_total_matches():
    _risk_inputs()
    from src.main import _compute_portfolio_risk_score
    enriched = {
        "BTC": {"value_usd": 100, "change_24h": 12.0, "ath_distance_pct": -40},
        "ETH": {"value_usd": 80, "change_24h": 8.0, "ath_distance_pct": -55},
    }
    snapshot = {"change_7d_pct": -3.0, "usdc_pct": 0.0}
    sector_exposure = {"sectors": [{"sector": "L1", "ptf_pct": 60.0}]}
    macro = {"fear_greed": 13}
    portfolio = {"BTC": {"value_usd": 100}, "ETH": {"value_usd": 80}}
    rs = _compute_portfolio_risk_score(snapshot, sector_exposure, macro, enriched, portfolio)
    labels = [c["label"] for c in rs["components"]]
    # La 5e barre Volatilité 24h DOIT être présente (T-RISK).
    assert "Volatilité 24h" in labels
    # La somme des barres doit (à l'arrondi) égaler le score affiché : plus
    # d'écart « barres 3.7 ≠ total 4.4 ».
    somme = round(sum(c["pts"] for c in rs["components"]), 1)
    assert abs(somme - rs["score"]) <= 0.11, (somme, rs["score"])
    # dominant_axes trié par ratio pts/max (M-A18) : le 1er a le plus haut ratio.
    da = rs["dominant_axes"]
    assert da and "ratio_pct" in da[0]
    if len(da) >= 2:
        assert da[0]["ratio_pct"] >= da[1]["ratio_pct"]


def test_risk_dominant_axes_names_real_driver():
    _risk_inputs()
    from src.main import _compute_portfolio_risk_score
    # v18 (M-B8) : le Cash n'est plus une composante. Ici, drawdown + sentiment
    # saturent et doivent primer sur une concentration partielle.
    enriched = {"BTC": {"value_usd": 50, "change_24h": 1.0, "ath_distance_pct": -30}}
    snapshot = {"change_7d_pct": -20.0}  # drawdown fort → 3.0/3.0 (ratio 100%)
    sector_exposure = {"sectors": [{"sector": "L1", "ptf_pct": 40.0}]}  # concentration 1.0/2.5 (40%)
    macro = {"fear_greed": 12}  # peur extrême → 1.0/1.5 (67%)
    portfolio = {"BTC": {"value_usd": 50}}
    rs = _compute_portfolio_risk_score(snapshot, sector_exposure, macro, enriched, portfolio)
    da = rs["dominant_axes"]
    top_label = da[0]["label"]
    # Drawdown sature à 100% → doit être le driver dominant, devant le reste.
    assert top_label == "Drawdown 7j", da
    # Le Cash ne doit jamais apparaître dans les axes dominants.
    assert all(a["label"] != "Cash" for a in da)
    # Les axes sont triés par ratio décroissant.
    if len(da) >= 2:
        assert da[0]["ratio_pct"] >= da[1]["ratio_pct"]


# --------------------------------------------------------------------------- #
# T-FMT — fmt_num_human + coercition prix
# --------------------------------------------------------------------------- #
def test_fmt_num_human():
    from src.reporting.email_html import _fmt_num_human
    assert _fmt_num_human(265492887109) == "265 Mds"
    assert _fmt_num_human(2_400_000) == "2.4 M"
    assert _fmt_num_human(63000).replace("\u202f", " ") == "63 000"
    assert _fmt_num_human(1.41) == "1.41"
    assert _fmt_num_human(0) == "0"
    assert _fmt_num_human(None) == "—"
    assert _fmt_num_human(1_500_000_000_000) == "1.5 Bn"
    # préfixe $ optionnel
    assert _fmt_num_human(265492887109, "$") == "$265 Mds"
    # déjà formaté (string non numérique) → renvoyé tel quel
    assert _fmt_num_human("n/d") == "n/d"


def test_thesis_prices_coerced_to_numbers():
    # Le filet Python doit transformer une string pré-formatée en nombre pour un
    # rendu homogène. On simule la coercition via _parse_num (même fonction).
    from src.main import _parse_num
    assert _parse_num("302,17 $") == 302.17
    assert _parse_num("285,00 $") == 285.0
    assert _parse_num("$264.3") == 264.3
    assert _parse_num(285) == 285.0


# --------------------------------------------------------------------------- #
# T-DEDUP (M-A2) — active_for_display
# --------------------------------------------------------------------------- #
def test_active_for_display_dedupes_and_enriches(tmp_path, monkeypatch):
    _risk_inputs()
    import src.state.report_memory as mem
    from src.tracking.prediction_scoring import PredictionTracker
    # Doublons legacy : BTC ×2 (dont une plus récente), ETH ×1.
    fake = [
        {"asset": "BTC", "action": "RENFORCER", "entry_price": 60000,
         "created_at": "2026-06-01T08:00:00+00:00", "status": "in_progress"},
        {"asset": "BTC", "action": "RENFORCER", "entry_price": 62000,
         "created_at": "2026-06-10T08:00:00+00:00", "status": "in_progress"},
        {"asset": "ETH", "action": "ALLÉGER", "entry_price": 1700,
         "created_at": "2026-06-09T08:00:00+00:00", "status": "in_progress"},
        {"asset": "ADA", "action": "SURVEILLER", "entry_price": None,
         "created_at": "2026-06-09T08:00:00+00:00", "status": "in_progress"},
    ]
    monkeypatch.setattr(mem, "load_active_recommendations", lambda: fake)
    tracker = PredictionTracker()
    out = tracker.active_for_display({"BTC": 64000, "ETH": 1600})
    by = {r["asset"]: r for r in out}
    # 1 seule ligne BTC (la plus récente : entrée 62000), ETH présent, ADA exclu
    # (SURVEILLER, pas une reco ferme).
    assert "BTC" in by and "ETH" in by and "ADA" not in by
    assert len([r for r in out if r["asset"] == "BTC"]) == 1
    assert by["BTC"]["entry_price"] == 62000  # la plus récente prime
    # Δ% enrichi : BTC (64000-62000)/62000 ≈ +3.2%
    assert by["BTC"]["progress_pct"] == round((64000 - 62000) / 62000 * 100, 1)
    # ALLÉGER ETH : progression inversée (prix baisse = favorable). 1600<1700.
    assert by["ETH"]["progress_pct"] == round(-((1600 - 1700) / 1700 * 100), 1)


# --------------------------------------------------------------------------- #
# T-SOURCES (W-A1) — _is_truly_active gère CoinGecko (dict symbole→data)
# --------------------------------------------------------------------------- #
def test_is_truly_active_coingecko_shape():
    from src.main import _is_truly_active
    # CoinGecko renvoie {BTC:{...}, ETH:{...}} SANS clé 'available' → actif.
    coingecko = {"BTC": {"price": 64000}, "ETH": {"price": 1600}}
    assert _is_truly_active(coingecko) is True
    # dict vide → inactif
    assert _is_truly_active({}) is False
    # dict avec available=False → inactif
    assert _is_truly_active({"available": False}) is False
    # dict avec available=True + contenu → actif
    assert _is_truly_active({"available": True, "value": 1}) is True


def test_blind_spots_full_set():
    from src.main import _blind_spots
    # 8 sources dégradées → toutes listées (ou résumées au-delà de 8).
    degraded = ["LunarCrush", "DeFiLlama", "YouTube", "Binance futures",
                "Farside", "Coinglass", "CoinMetrics", "Kaito"]
    out = _blind_spots(degraded_sources=degraded)
    # Au moins plusieurs des sources réelles sont nommées (plus « seulement ETF »).
    assert "LunarCrush" in out and "Kaito" in out
    # 9 sources → résumé « +N autres »
    out9 = _blind_spots(degraded_sources=degraded + ["NewsAPI"])
    assert "+1 autres" in out9 or "+1 autre" in out9


# --------------------------------------------------------------------------- #
# M-A4 — plancher 10Y relevé
# --------------------------------------------------------------------------- #
def test_macro_range_10y_floor():
    from src.main import _MACRO_RANGES, _vm, _macro_validation_flags
    _macro_validation_flags.clear()
    # 0.45% sous le plancher 0.5 → écarté (None).
    assert _vm("us_10y", 0.4487) is None
    # 4.49% plausible → conservé.
    assert _vm("us_10y", 4.49) == 4.49
    assert _MACRO_RANGES["us_10y"][0] == 0.5


# --------------------------------------------------------------------------- #
# Heatmap secteurs — v19/W-B15 : 4 + AUTRE (moyenne pondérée)
# --------------------------------------------------------------------------- #
def test_sector_heatmap_autre_consolidation():
    # v23.x : exposition = 5 secteurs individuels + 1 « Autres secteurs » = 6 cases
    # (remplace l'ancien 4 + AUTRE = 5).
    _risk_inputs()
    from src.main import _compute_sector_exposure
    enriched = {"A": {"value_usd": 50}, "B": {"value_usd": 30},
                "C": {"value_usd": 20}, "D": {"value_usd": 15},
                "E": {"value_usd": 10}, "F": {"value_usd": 6}, "G": {"value_usd": 4}}
    rotation = {"sectors": {
        "L1": {"members": ["A"], "avg_change_24h": 2.0},
        "AI": {"members": ["B"], "avg_change_24h": 5.0},
        "DeFi": {"members": ["C"], "avg_change_24h": 1.5},
        "RWA": {"members": ["D"], "avg_change_24h": 4.0},
        "Oracle": {"members": ["E"], "avg_change_24h": -1.0},
        "Gaming": {"members": ["F"], "avg_change_24h": 3.0},
        "Meme": {"members": ["G"], "avg_change_24h": -2.0},
    }}
    r = _compute_sector_exposure(enriched, rotation)
    secs = r["sectors"]
    assert len(secs) == 6  # 5 individuels + agrégat « Autres secteurs »
    assert secs[-1]["sector"].startswith("Autres secteurs")  # v23 (W7)
    # Moyenne pondérée de l'agrégat (F,G) : (6×3 + 4×-2)/10 = 1.0
    assert secs[-1]["market_change_24h"] == 1.0
    assert secs[-1]["is_aggregate"] is True


# --------------------------------------------------------------------------- #
# M-A21 — tickers parasites filtrés
# --------------------------------------------------------------------------- #
def test_parasite_tickers_filtered():
    import src.data_sources.cryptobubbles as cb

    raw = [
        {"symbol": "H", "name": "H", "performance": {"day": 5.0}, "marketcap": 9e9},
        {"symbol": "B", "name": "B", "performance": {"day": 3.0}, "marketcap": 9e9},
        {"symbol": "BTC", "name": "Bitcoin", "performance": {"day": 1.0}, "marketcap": 9e11},
        {"symbol": "TAO", "name": "Bittensor", "performance": {"day": 18.0}, "marketcap": 5e9},
    ]
    import types
    fake_cache = types.SimpleNamespace(get_or_compute=lambda *a, **k: raw)
    orig_cache = cb.CACHE
    cb.CACHE = fake_cache
    try:
        out = cb.get_market_movers(["BTC", "TAO"], top_n=3)
    finally:
        cb.CACHE = orig_cache
    syms = {c["symbol"] for c in (out.get("gainers", []) + out.get("losers", [])
                                  + out.get("portfolio_movers", []))}
    # Les tickers mono-lettre sont écartés ; BTC/TAO conservés.
    assert "H" not in syms and "B" not in syms
    assert "TAO" in syms or "BTC" in syms


# --------------------------------------------------------------------------- #
# T-STATE — delta/timing neutralisés si state matin périmé
# --------------------------------------------------------------------------- #
def test_render_evening_no_stale_timing_when_morning_old():
    # Quand morning_is_today est faux, le rendu ne doit pas afficher de timing
    # « matin … · Δ…h » fabriqué. On vérifie au niveau du rendu : sans timing_line,
    # pas de « Δ » aberrant.
    from src.reporting.email_html import render
    html = render({
        "header": {},  # pas de timing_line (cas state périmé neutralisé)
        "daily_pnl": {"value_usd": 1800, "day_change_usd": 0.0, "day_change_pct": 0.0,
                      "day_change_label": "stable", "top_movers": []},
        "footer": {"next_morning_time": "08h30"},
    }, "evening")
    # Pas de delta intraday factice ni de Δ aberrant.
    assert "Δ12h" not in html
    assert "rendu simplifié" not in html


# --------------------------------------------------------------------------- #
# W-A3 / W-A4 — <details> retiré + markdown rendu
# --------------------------------------------------------------------------- #
def test_weekly_no_details_tag_and_md_concentration():
    from src.reporting.email_html import render
    html = render({
        "header": {"week_number": 24},
        "portfolio_snapshot": {"value_usd": 1800},
        "concentration_reading": "Les **L1 (44.2%)** et l'**AI (29.1%)** dominent.",
        "long_term_positioning": [
            {"asset": "BTC", "thesis": "Réserve de valeur numérique adoptée largement par les institutions et de plus en plus retenue comme couverture macro de long terme.",
             "target": "126k", "status": "consolide", "status_color": "#5a5852"}],
        "ath_facts": {"BTC": {"ath": 126080, "from_ath_pct": -49.0}},
    }, "weekly")
    # Plus de balise <details> (non supportée Gmail → doublon).
    assert "<details" not in html
    # Markdown rendu : les ** deviennent du gras, pas des astérisques littéraux.
    assert "**L1" not in html
    assert "<strong>L1 (44.2%)</strong>" in html or "<b>L1 (44.2%)</b>" in html


# --------------------------------------------------------------------------- #
# M-B2 — take profit retiré du plan d'action (cibles dédupliquées)
# --------------------------------------------------------------------------- #
def test_morning_plan_no_duplicate_take_profit():
    from src.reporting.email_html import render
    html = render({
        "header": {"active_sources_count": 20, "total_sources_count": 25},
        "portfolio_snapshot": {"value_usd": 1700},
        "thesis_of_the_day": [{
            "asset": "TAO", "action": "RENFORCER", "action_type": "bullish",
            "confidence": 70,
            "targets": {"short_term_30d": 285, "short_term_label": "Tactique 30j",
                        "long_term_6_12m_low": 320},
            "action_plan": {"entry": 260, "stop_loss": 245,
                            "take_profit": {"30pct": 285, "30pct_b": 302, "40pct": 320},
                            "rr": "2.5:1"},
        }],
    }, "morning")
    # Le plan d'action ne montre plus « Take profit : » (doublon des cibles).
    assert "Take profit :" not in html
    # Mais l'entrée, le stop et la cible CT restent visibles.
    assert "Entrée :" in html and "Stop loss :" in html
    assert "285" in html  # la cible existe toujours (encadré cibles)


# --------------------------------------------------------------------------- #
# v23.x — section « TOP MOUVEMENTS MARCHÉ » RETIRÉE du morning (demande d'Omar)
# --------------------------------------------------------------------------- #
def test_morning_market_movers_section_removed():
    from src.reporting.email_html import render
    html = render({
        "header": {"active_sources_count": 20, "total_sources_count": 25},
        "portfolio_snapshot": {"value_usd": 1700},
        "market_movers": {
            "available": True,
            "gainers": [{"symbol": f"G{i}", "change_24h": 30 - i} for i in range(5)],
            "losers": [{"symbol": f"L{i}", "change_24h": -(30 - i)} for i in range(5)],
        },
    }, "morning")
    # La section n'est plus rendue : ni l'en-tête ni les tickers hors PTF.
    assert "Top mouvements marché" not in html
    assert "G0" not in html and "L0" not in html
