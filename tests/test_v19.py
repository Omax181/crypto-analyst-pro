"""Tests v19 — verrouillent les correctifs du passage V18 → V19.

Chaque workstream (WS*) ajoute ici ses invariants pour empêcher toute
régression future. Aucun appel réseau réel (prompts = chaînes ; scoring =
pur Python).
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# WS1 — Thèses & zéro-reco (Partie 5)
# --------------------------------------------------------------------------- #
def test_ws1_morning_prompt_has_anti_empty_rule():
    """Le prompt matin contient la règle v19 anti-thèse-vide (débloque les recos)."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    prompt = build_morning_prompt(
        timestamp="17/06 08:30", data={}, portfolio_yaml="",
        evening_state={},
    )
    assert "ANTI-THÈSE-VIDE" in prompt
    # La règle doit s'appuyer sur le poids fondamental réellement exposé par
    # thesis_scoring (sinon la directive serait inapplicable par Gemini).
    assert "fundamental_weight" in prompt
    # Et imposer un niveau précis quand on choisit SURVEILLER plutôt que vide.
    assert "watch_trigger" in prompt


def test_ws1_morning_prompt_cash_not_in_risk():
    """v23 : la « note de risque » a été remplacée par la note de SANTÉ PTF ;
    le prompt référence désormais data.health_score (auto-générée)."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    prompt = build_morning_prompt(
        timestamp="17/06 08:30", data={}, portfolio_yaml="",
        evening_state={},
    )
    assert "data.health_score" in prompt


def test_ws1_strong_fundamental_setup_is_conviction_and_eligible():
    """Un setup fondamental fort (MVRV<1 + sous PRU) → conviction, éligible,
    fundamental_weight ≥ 3, MÊME sans mouvement de prix (cœur du fix zéro-reco)."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    asset = {"change_24h": 0.2, "tech_advanced": {}, "news_24h_count": 0}
    out = evaluate_thesis_eligibility(
        asset, tier=1, mvrv=0.92, pru_gap_pct=-18.0,
        drawdown_from_ath_pct=-63.0,
    )
    assert out["eligible"] is True
    assert out["thesis_type"] == "conviction"
    assert out["fundamental_weight"] >= 3


# --------------------------------------------------------------------------- #
# WS4 — Cash retiré du Weekly (Qualité PTF)
# --------------------------------------------------------------------------- #
def test_ws4_weekly_quality_excludes_cash_axis():
    """Le code du score qualité weekly ne crée plus d'axe « Réserve cash »."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_weekly)
    # L'axe cash a été supprimé : plus aucune création d'un axe « Réserve cash ».
    assert "\"Réserve cash\"" not in src and "'Réserve cash'" not in src


# --------------------------------------------------------------------------- #
# WS2 — Source de vérité unique cross-mail
# --------------------------------------------------------------------------- #
def test_ws2_snapshot_carries_computed_date():
    """Le snapshot matin estampille computed_date (clé du guard same-day du hebdo)."""
    import inspect

    import src.main as main

    src = inspect.getsource(main._portfolio_snapshot)
    assert "computed_date" in src
    # Et le hebdo réutilise vs_btc_7d_pct du matin du jour (un seul nombre, X1/W-B2).
    wsrc = inspect.getsource(main.run_weekly)
    assert "_morning_is_today" in wsrc


def test_ws2_active_reco_single_open_per_asset(tmp_path, monkeypatch):
    """active_recos[asset] = dernière reco ouverte : jamais 2 recos ouvertes sur
    le même actif (cause-racine M-A2 tracker matin ≠ weekly)."""
    import src.state.report_memory as mem

    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)

    mem.add_recommendation({"asset": "ETH", "action": "RENFORCER",
                            "id": "ETH-2026-06-17-RENFORCER", "entry_price": 1640.0,
                            "confidence": 70, "status": "in_progress"})
    # Ré-émission le lendemain (id différent) → PAS de doublon (mise à jour).
    mem.add_recommendation({"asset": "ETH", "action": "RENFORCER",
                            "id": "ETH-2026-06-18-RENFORCER", "entry_price": 1700.0,
                            "confidence": 72, "status": "in_progress"})
    recos = mem.load_active_recommendations()
    eth = [r for r in recos if r["asset"] == "ETH"]
    assert len(eth) == 1
    assert eth[0]["entry_price"] == 1640.0  # entrée d'origine préservée (scoring)

    # Changement d'avis RENFORCER → ALLEGER : remplace, n'empile pas.
    mem.add_recommendation({"asset": "ETH", "action": "ALLEGER",
                            "id": "ETH-2026-06-19-ALLEGER", "entry_price": 1750.0,
                            "confidence": 66, "status": "in_progress"})
    recos = mem.load_active_recommendations()
    eth = [r for r in recos if r["asset"] == "ETH"]
    assert len(eth) == 1 and eth[0]["action"] == "ALLEGER"
    # La transition est tracée.
    assert any(c.get("asset") == "ETH" for c in mem.load_reco_changes())


# --------------------------------------------------------------------------- #
# WS11 — Garde-fou macro Python (forçage déterministe)
# --------------------------------------------------------------------------- #
def test_ws11_macro_guardrail_forces_caution_on_high_vix():
    """VIX=28 → garde-fou actif → régime FORCÉ prudent même si Gemini dit risk-on
    85% (M-B14 : indépendance vis-à-vis du récit IA)."""
    import src.main as main

    payload = {
        "macro_guardrail": main._compute_macro_guardrail({"vix": 28.0}),
        "macro_regime_readout": {
            "regime": "risk-on", "confidence_pct": 85,
            "crypto_bias": "appétit pour le risque, favorable au crypto",
        },
    }
    assert payload["macro_guardrail"]["active"] is True
    main._apply_macro_guardrail_override(payload)
    ro = payload["macro_regime_readout"]
    assert ro["forced_caution"] is True
    assert "pruden" in ro["crypto_bias"].lower()
    assert "forced_caution_note" in ro


def test_ws11_no_override_when_calm():
    """Marché calme (VIX 16, F&G 50, DXY 99) → garde-fou inactif → aucun forçage."""
    import src.main as main

    payload = {
        "macro_guardrail": main._compute_macro_guardrail(
            {"vix": 16.0, "fear_greed": 50, "dxy": 99.0}),
        "macro_regime_readout": {"crypto_bias": "neutre"},
    }
    main._apply_macro_guardrail_override(payload)
    assert payload["macro_regime_readout"].get("forced_caution") is None
    assert payload["macro_regime_readout"]["crypto_bias"] == "neutre"


# --------------------------------------------------------------------------- #
# WS7 / v23.x — Heatmap plafonnée à 4 lignes (M-B17)
# --------------------------------------------------------------------------- #
def test_ws7_heatmap_capped_to_four_rows():
    """v28 (M-A11) : >15 positions visibles → 15 plus gros IMPACTS + 1
    « +N autres » = 16 max (les poussières < 0.5% PTF vont à l'agrégat)."""
    from src.main import _portfolio_heatmap

    enriched = {f"A{i}": {"value_usd": 100 - i, "change_24h": (i % 7) - 3}
                for i in range(26)}
    hm = _portfolio_heatmap(enriched)
    assert len(hm["cells"]) == 15
    assert hm["extra"] is not None and hm["extra"]["count"] == 11  # 26 − 15
    total_cells = len(hm["cells"]) + (1 if hm["extra"] else 0)
    assert total_cells <= 16  # 3 lignes de 5 + agrégat


def test_ws7_heatmap_small_ptf_no_extra():
    """≤8 positions → tout affiché, pas de case agrégée."""
    from src.main import _portfolio_heatmap

    enriched = {f"A{i}": {"value_usd": 100 - i, "change_24h": 1.0} for i in range(6)}
    hm = _portfolio_heatmap(enriched)
    assert hm["extra"] is None
    assert len(hm["cells"]) == 6


def test_ws7_positions_block_removed_from_morning():
    """v23.x — la boîte « Tes positions ▲/▼ » a été RETIRÉE du morning (demande
    d'Omar). Même avec des portfolio_movers fournis, elle ne s'affiche plus
    (les mouvements des positions restent lisibles via la heatmap)."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "17/06"},
        "portfolio_snapshot": {"value_usd": 2630},
        "market_movers": {"available": False, "portfolio_movers": [
            {"symbol": "CFX", "change_24h": 10.0},
            {"symbol": "ADA", "change_24h": -1.9},
        ]},
    }
    html = render(payload, "morning")
    assert "Tes positions en hausse" not in html
    assert "Tes positions en baisse" not in html


# --------------------------------------------------------------------------- #
# WS3 — Run hors-cycle / fenêtre dégénérée
# --------------------------------------------------------------------------- #
def test_ws3_evening_degenerate_window_wired():
    """run_evening calcule le flag fenêtre dégénérée + injecte run_window + filtre
    les news postérieures au matin (E-B2/E-B3/V18-E2)."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "_degenerate_window" in src
    assert "run_window" in src
    assert "_after_morning" in src  # filtre news strictement post-matin (E-B3)


def test_ws3_evening_prompt_has_degenerate_rule():
    """Le prompt soir instruit le mode rejeu quand run_window.degenerate."""
    import inspect

    import src.ai_brain.prompts.evening_prompt as ep

    src = inspect.getsource(ep)
    assert "FENÊTRE DÉGÉNÉRÉE" in src
    assert "run_window.degenerate" in src


# --------------------------------------------------------------------------- #
# WS6 — Métriques définies/nommées (cluster de corrélation)
# --------------------------------------------------------------------------- #
def test_ws6_cluster_pct_uses_full_portfolio_denominator():
    """V18-M3 : le « % du portefeuille corrélé » se calcule sur le PTF COMPLET,
    pas sur le sous-ensemble ayant des séries de prix (sinon 30% devenait 100%)."""
    from src.analytics.correlation import compute_correlation_analysis

    series = [100 + i for i in range(12)]  # rendements identiques → corr = 1.0
    price_series = {"A": list(series), "B": list(series), "C": list(series)}
    # D pèse 700 $ mais n'a PAS de série de prix (hors univers analysé).
    position_values = {"A": 100, "B": 100, "C": 100, "D": 700}
    out = compute_correlation_analysis(price_series, position_values)
    assert out["available"]
    # Cluster {A,B,C} = 300 $ sur un PTF de 1000 $ → 30%, PAS 100% (ancien bug).
    assert out["max_cluster_pct"] == 30.0
    assert "30,0% du portefeuille" in out["concentration_reading"]
    # Métrique désormais DÉFINIE : base de corrélation explicitée (M-A7).
    assert "0,70" in out["concentration_reading"] and "30j" in out["concentration_reading"]


# --------------------------------------------------------------------------- #
# WS12 — News horodatage absolu + fraîcheur
# --------------------------------------------------------------------------- #
def test_ws12_fr_when_no_relative_label():
    """_fr_when n'émet plus « hier »/« avant-hier » : date absolue hors jour même."""
    import datetime as dt

    from src.main import TZ, _fr_when

    now = dt.datetime.now(TZ)
    assert "09:05" in _fr_when(now.replace(hour=9, minute=5).isoformat())
    for days in (1, 2, 5):
        d = now - dt.timedelta(days=days)
        label = _fr_when(d.isoformat())
        assert "hier" not in label
        assert d.strftime("%d/%m") in label


def test_ws12_morning_prompt_news_freshness_rule():
    """Le prompt matin classe une news > 12h en contexte, pas catalyseur (M-A10)."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    prompt = build_morning_prompt(
        timestamp="x", data={}, portfolio_yaml="", evening_state={})
    assert "FRAÎCHEUR DES NEWS" in prompt
    assert "PLUS DE 12h" in prompt


# --------------------------------------------------------------------------- #
# WS8 — Rotation sectorielle
# --------------------------------------------------------------------------- #
def test_ws8_rotation_sorted_by_abs_change_negative_first():
    """M-A23 (test demandé par l'audit) : un secteur à −25% sort AVANT un +15%
    (tri par |variation|, pas par valeur signée)."""
    from src.main import _merge_python_facts

    data = {"sector_rotation": {"sectors": {
        "L2": {"avg_change_24h": 5.0, "members": ["A"]},
        "AI": {"avg_change_24h": -25.0, "members": ["B"]},
        "L1": {"avg_change_24h": 15.0, "members": ["C"]},
    }}}
    payload: dict = {}
    _merge_python_facts(payload, data, "17/06 08:30")
    rot = payload["sector_rotation"]
    assert rot[0]["sector"] == "AI"          # |−25| > |15| > |5|
    assert rot[0]["change_24h"] == -25.0
    assert rot[1]["sector"] == "L1"


def test_ws8_sector_exposure_six_cases():
    """v23.x : exposition sectorielle = 5 secteurs + 1 « Autres secteurs » = 6 cases max."""
    from src.main import _compute_sector_exposure

    rotation = {"sectors": {
        f"S{i}": {"members": [f"M{i}"], "avg_change_24h": float(i)} for i in range(8)
    }}
    enriched = {f"M{i}": {"value_usd": (8 - i) * 100.0} for i in range(8)}
    out = _compute_sector_exposure(enriched, rotation)
    assert out["available"]
    assert len(out["sectors"]) == 6          # 5 individuels + agrégat (avant : 4 + agg)
    # v29 (WA11) — libellé « Autres · N secteurs (M actifs) » (le « (4) » de
    # l'ancien libellé se lisait comme un compte d'actifs faux).
    assert out["sectors"][-1]["sector"].startswith("Autres ·")
    assert "3 secteurs" in out["sectors"][-1]["sector"]
    assert out["sectors"][-1].get("is_aggregate") is True


def test_ws8_morning_prompt_reads_three_windows():
    """M-B13 : le prompt impose de lire 24h/7j/30j dans la note rotation PTF."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    prompt = build_morning_prompt(
        timestamp="x", data={}, portfolio_yaml="", evening_state={})
    assert "LIS LES 3 FENÊTRES" in prompt
    assert "change_30d" in prompt


# --------------------------------------------------------------------------- #
# WS10 — Weekly refonte (sous-ensemble : W-A11, V18-W1, W-A17)
# --------------------------------------------------------------------------- #
def test_ws10_weekly_lt_thesis_not_truncated():
    """W-A11 : plus de troncature « … » en plein mot des thèses LT du weekly."""
    import pathlib

    tpl = (pathlib.Path(__file__).resolve().parent.parent
           / "src" / "reporting" / "templates" / "report_weekly.html.j2"
           ).read_text(encoding="utf-8")
    assert "thesis[:90]" not in tpl   # ancienne troncature supprimée
    # v23.x — tableau fusionné « positions_review » : l'analyse n'est pas tronquée.
    assert "analysis[:" not in tpl
    assert "r.analysis" in tpl


def test_ws10_weekly_prompt_rules():
    """Le prompt weekly impose : thesis_short complète (W-A11), statut de cycle
    (V18-W1), dérivation des % de scénarios (W-A17)."""
    import inspect

    import src.ai_brain.prompts.weekly_prompt as wp

    src = inspect.getsource(wp)
    # v23.x — schéma long_term_positioning refondu : analyse chiffrée + cible
    # numérique + action (plus de thesis_short descriptif).
    assert "analysis" in src and "target_price" in src and "action" in src
    assert "vocabulaire de CYCLE" in src           # V18-W1
    # v23.x — la dérivation des % de scénarios est désormais ANCRÉE sur le
    # scaffold déterministe (W-A17 conservé comme repère historique).
    assert "W-A17" in src and "scenario_scaffold" in src


# --------------------------------------------------------------------------- #
# WS9 — Evening tuiles & actions
# --------------------------------------------------------------------------- #
def test_ws9_evening_template_fixes():
    """V18-E3 (fin du double % BTC), V18-E10 (Or Δ% chiffré), E-A14/V18-E9
    (prix ETH de référence près des niveaux)."""
    import pathlib

    tpl = (pathlib.Path(__file__).resolve().parent.parent
           / "src" / "reporting" / "templates" / "report_evening.html.j2"
           ).read_text(encoding="utf-8")
    # Plus de seconde ligne redondante affichant à nouveau le Δ24h BTC.
    assert "_em.btc_change_24h|fmt_pct" not in tpl
    # Or : Δ24h chiffré (avant : flèche seule).
    assert "V18-E10" in tpl and "gold_delta" in tpl
    # ETH : prix de référence sous le symbole dans le tableau de niveaux.
    assert "evening_macro.eth_price" in tpl


def test_ws9_evening_macro_exposes_eth_price():
    """run_evening alimente evening_macro avec le prix ETH (référence niveaux)."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_evening)
    assert "\"eth_price\"" in src


def test_ws10_weekly_per_asset_rebalance_alert():
    """W-B13 : alerte de rééquilibrage AUSSI au niveau actif (mono-position ≥12%,
    ex. TAO 15.5%), pas seulement sectorielle."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_weekly)
    assert "mono-actifs " in src          # v23.x : fusionné dans l'alerte unique
    assert "heavy_assets" in src
    assert "12.0" in src


def test_ws10_ptf_evolution_renders_as_gmail_safe_bars():
    """v20 (audit C1) : l'évolution PTF est rendue en barres HTML (Gmail-safe),
    plus aucune sparkline SVG inline."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "19/06"},
        "portfolio_snapshot": {"value_usd": 2600.0},
        "ptf_evolution": [
            {"label": f"S{i}", "value": 2400 + i * 50} for i in range(5)
        ],
    }
    html = render(payload, "weekly")
    assert "<svg" not in html
    assert "Évolution PTF" in html


def test_ws10_weekly_reconstructs_sparkline_from_price_series():
    """W-B16 : le weekly reconstruit la courbe d'évolution PTF depuis les séries
    de prix par actif quand l'historique de snapshots est insuffisant (sparkline
    visible dès le 1er hebdo, sans attendre 3 semaines)."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_weekly)
    assert "weekly_price_series" in src and "W-B16" in src
    assert "_recon" in src


def test_ws10_snapshot_stores_diversification_score(tmp_path, monkeypatch):
    """W-A10 : le snapshot hebdo mémorise le score de diversification (compar. N-1)."""
    import src.state.report_memory as mem

    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.record_weekly_snapshot(2600.0, 65000.0, diversification_score=3.2)
    snaps = mem.load_weekly_snapshots()
    assert snaps and snaps[-1]["diversification_score"] == 3.2


def test_ws10_weekly_diversification_vs_n1_wired():
    """W-A10 : le weekly calcule l'écart de diversification vs la semaine N-1."""
    import inspect

    import src.main as main

    src = inspect.getsource(main.run_weekly)
    assert "vs N-1" in src and "_div_prev" in src


# --------------------------------------------------------------------------- #
# WS13 — Espérance mathématique dans les 3 mails (W-B12)
# --------------------------------------------------------------------------- #
def test_ws13_expectancy_wired_in_morning_and_evening():
    """W-B12 : l'espérance (compute_expectancy) est calculée pour le matin ET le
    soir (et plus seulement le weekly), et affichée dans les deux templates."""
    import inspect
    import pathlib

    import src.main as main

    assert "compute_expectancy" in inspect.getsource(main._collect_morning_data)
    assert "compute_expectancy" in inspect.getsource(main.run_evening)

    base = (pathlib.Path(__file__).resolve().parent.parent
            / "src" / "reporting" / "templates")
    for _tpl in ("report_morning.html.j2", "report_evening.html.j2"):
        txt = (base / _tpl).read_text(encoding="utf-8")
        assert "Espérance mathématique" in txt


# --------------------------------------------------------------------------- #
# WS14 — Audit bot Telegram (Partie 6)
# --------------------------------------------------------------------------- #
def test_ws14_assistant_independent_reasoning():
    """Partie 6 : le bot ne valide pas automatiquement les hypothèses d'Omar et
    garde une logique analytique indépendante (peut contredire si les données
    l'imposent)."""
    from src.telegram_bot.assistant import build_assistant_prompt

    prompt = build_assistant_prompt("est-ce que TAO va monter ?", {}, [])
    assert "INDÉPENDANCE ANALYTIQUE" in prompt
    assert "ne valide PAS automatiquement" in prompt


# --------------------------------------------------------------------------- #
# Partie 5 — score pondéré exhibé + 9 dimensions
# --------------------------------------------------------------------------- #
def test_partie5_weighted_score_breakdown_displayed():
    """§4.2 : le score pondéré et le détail des signaux par poids sont AFFICHÉS
    dans la thèse (preuve d'éligibilité multi-dimensionnelle, plus invisible)."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "17/06"},
        "portfolio_snapshot": {"value_usd": 2630},
        "thesis_of_the_day": [{
            "asset": "ETH", "action": "RENFORCER", "action_type": "bullish",
            "thesis_type": "conviction", "confidence": 70,
            "thesis_scoring": {
                "score": 6, "threshold": 4, "dimensions_count": 2,
                "signals": [
                    {"label": "MVRV < 1", "category": "fundamental_lt", "weight": 3},
                    {"label": "drawdown −63% vs ATH", "category": "fundamental_lt", "weight": 3},
                ]},
        }],
    }
    html = render(payload, "morning")
    assert "Score pondéré" in html
    # NB : Jinja échappe « < » en « &lt; » → on teste sans le caractère spécial.
    assert "MVRV" in html and "+3" in html
    assert "seuil 4" in html
    assert "2 dimension" in html


def test_partie5_prompt_requires_nine_dimensions():
    """§3 : le prompt exige explicitement les 9 dimensions de la thèse."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    p = build_morning_prompt(timestamp="x", data={}, portfolio_yaml="", evening_state={})
    assert "9 DIMENSIONS" in p
    for dim in ("ON-CHAIN", "DÉRIVÉS", "ROTATION SECTORIELLE",
                "FONDAMENTAUX PROJET", "POSITION DANS"):
        assert dim in p


# --------------------------------------------------------------------------- #
# Partie 6 — bot : traçabilité + cohérence des actions opérationnelles
# --------------------------------------------------------------------------- #
def test_partie6_dismiss_traceability_and_anti_reemit(tmp_path, monkeypatch):
    """Un /dismiss est TRACÉ (traçabilité) et bloque la ré-émission 48h (cohérence)."""
    import src.state.report_memory as mem

    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.record_reco_dismissal("ETH", "RENFORCER", "ETH-1")
    dismissals = mem.load_reco_dismissals()
    assert dismissals and dismissals[-1]["asset"] == "ETH"
    assert mem.is_recently_dismissed("ETH", "RENFORCER") is True
    assert mem.is_recently_dismissed("ETH", "ALLEGER") is False   # action différente
    assert mem.is_recently_dismissed("BTC", "RENFORCER") is False  # autre actif
    # Une dismissal ANCIENNE (au-delà de la fenêtre) n'est plus bloquante.
    mem._write(mem.RECO_DISMISSALS_FILE, [
        {"asset": "XRP", "action": "RENFORCER",
         "dismissed_at": "2020-01-01T00:00:00+00:00"}])
    assert mem.is_recently_dismissed("XRP", "RENFORCER", days=2) is False


def test_partie6_dismiss_command_records_trace(tmp_path, monkeypatch):
    """La commande /dismiss du bot enregistre bien la trace."""
    import src.state.report_memory as mem
    from src.telegram_bot import commands

    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.save_active_recommendations([
        {"asset": "TAO", "action": "RENFORCER", "id": "TAO-1", "status": "in_progress"},
    ])
    reply, modified = commands.handle_state_command("/dismiss TAO")
    assert modified is True
    assert mem.is_recently_dismissed("TAO", "RENFORCER") is True


def test_partie6_persist_skips_recently_dismissed(tmp_path, monkeypatch):
    """_persist_firm_recos NE ré-émet PAS une reco écartée récemment (cohérence)."""
    import src.main as main
    import src.state.report_memory as mem

    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.record_reco_dismissal("ETH", "RENFORCER")
    payload = {"thesis_of_the_day": [{
        "asset": "ETH", "action": "RENFORCER", "confidence": 70,
    }]}
    data = {"all_positions_summary": [{"asset": "ETH", "price": 1640.0}]}
    main._persist_firm_recos(payload, data)
    # Aucune reco active créée pour ETH (car écartée < 48h).
    assert all(r.get("asset") != "ETH" for r in mem.load_active_recommendations())


# --------------------------------------------------------------------------- #
# Points déterministes restants (#28) + nuances prompt (#29)
# --------------------------------------------------------------------------- #
def test_v18m10_footer_names_down_sources():
    """V18-M10/M-A20 : le footer NOMME les sources indisponibles (déterministe)."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "17/06", "active_sources_count": 21,
                   "total_sources_count": 25},
        "portfolio_snapshot": {"value_usd": 2630},
        "footer": {"active_sources": ["CoinGecko", "FRED"],
                   "down_sources": ["Farside", "Kaito", "LunarCrush", "Token Unlocks"]},
    }
    html = render(payload, "morning")
    assert "Indisponibles" in html
    assert "Farside" in html and "Kaito" in html


def test_v19_editorial_nuances_present_in_prompts():
    """#29 : les nuances éditoriales restantes sont injectées dans les 3 prompts."""
    import inspect

    import src.ai_brain.prompts.evening_prompt as ep
    import src.ai_brain.prompts.weekly_prompt as wp
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    m = build_morning_prompt(timestamp="x", data={}, portfolio_yaml="", evening_state={})
    assert "STYLE D'ANALYSE UNIFIÉ" in m            # M-B10
    assert "ANTI-RÉPÉTITION" in m                    # M-A6 / V18-M9
    assert "ACTIFS SURVEILLÉS JUSTIFIÉS" in m        # X10
    assert "REGROUPER les drivers" in m              # V18-M11

    es = inspect.getsource(ep)
    assert "TIMING FOMC" in es                       # E-A15 / V18-E4
    assert "DIAGNOSTIC COHÉRENT" in es               # V18-E12 / X4
    assert "ACTION SANS THÈSE MATIN" in es           # V18-E1

    ws = inspect.getsource(wp)
    # v27 (RE1) — la règle W-B8 « cash 0% = risque opérationnel » est REMPLACÉE :
    # le cash n'est plus une contrainte (Omar peut toujours injecter).
    assert "LE CASH N'EST PAS UNE CONTRAINTE" in ws  # v27/RE1 (ex V18-W8)
    assert "ÉVOLUTION F&G" in ws                     # W-A18
    assert "BOUCLE D'APPRENTISSAGE" in ws            # W-B14
    assert "SANTÉ PTF" in ws                          # W-A19 (v23 : santé, plus risque)


def test_x6_morning_prompt_health_note_auto():
    """v23 — la note de SANTÉ PTF (data.health_score, plus haut = plus sain) est
    auto-générée par le système ; le prompt l'indique et n'évoque plus de
    « note de risque » à remplir (concept supprimé)."""
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt

    p = build_morning_prompt(timestamp="x", data={}, portfolio_yaml="", evening_state={})
    assert "data.health_score" in p
    assert "PLUS HAUT = PLUS SAIN" in p
