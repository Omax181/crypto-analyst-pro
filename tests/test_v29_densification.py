# -*- coding: utf-8 -*-
"""v29 — verrous de la DENSIFICATION des mails (points B de l'audit 10/07).

Chaque test rend un mail réel et verrouille une fusion/dédup de la Partie B
(MB*/EB*/WB*/ZB*) : la redite disparaît, l'analyse reste. Aucun réseau.
"""

from __future__ import annotations

from src.reporting.email_html import render


# ═══════════════════════════════════════════════════════════════════ MORNING

def _detailed_thesis() -> dict:
    return {
        "asset": "ETH", "name": "Ethereum", "action": "RENFORCER",
        "action_type": "bullish", "thesis_type": "conviction", "confidence": 70,
        "_expand": True, "current_price": 1774.0, "rr_value": 1.4,
        "observation": "Setup d'accumulation.",
        "reasoning_signals": ["1) Fondamental MVRV 0.87", "2) Technique RSI 55"],
        "self_critique": "Activité réseau molle.",
        "counter_thesis": "Cassure de 1 527 $ invaliderait la structure.",
        "macro_coherence": "NE DOIT PAS APPARAITRE dans la fiche v29.",
        "historical_pattern": {"narrative": "18 cas, +7j −2,3%."},
        "targets": {"short_term_30d": 1875.0, "short_term_label": "Tactique 30j",
                    "long_term_6_12m_low": 2986.0, "long_term_6_12m_high": 4946.0},
        "action_plan": {"entry": 1774.0, "stop_loss": 1621.0, "rr": "1.4",
                        "invalidation_conditions": "sous 1 621 $ (pivot)"},
        "asset_plan": {
            "ev_30d_pct": -2.1, "prob_up_30d": 0.51,
            "ev_note": "estimation indicative",
            "scenarios": {"bull": {"probability_pct": 23, "level_label": "1 879 $"},
                          "base": {"probability_pct": 55, "range_label": "1 690–1 850 $"},
                          "bear": {"probability_pct": 22, "level_label": "1 592 $"}},
            "dca": [{"weight_pct": 40, "price_label": "1 774 $", "basis": "prix actuel"},
                    {"weight_pct": 30, "price_label": "1 690 $", "basis": "support"},
                    {"weight_pct": 30, "price_label": "1 629 $", "basis": "au-dessus inval."}],
        },
        "thesis_scoring": {"score": 14, "threshold": 2, "dimensions_count": 4,
                           "signals": [{"label": "MVRV 0.87", "weight": 3}],
                           "completeness": {"pct": 83, "missing": ["sentiment"]}},
    }


def test_zb1_health_and_structure_merged():
    """ZB1 — Santé + Structure/stress-test = UNE carte (un seul titre)."""
    html = render({"header": {"date": "x"},
                   "health_score": {"score": 3.0, "level": "à risque",
                                    "axes": [{"label": "Diversification",
                                              "score": 1.5, "max": 10}]},
                   "portfolio_risk": {"available": True, "readings": [
                       "Stress-test : −20% BTC → −23%.", "VaR 95% : −3,2%."]}},
                  "morning")
    assert "Santé du portefeuille" in html
    assert "Structure &amp; stress-test" in html
    assert "VaR 95%" in html
    # un seul conteneur : « Structure » n'a plus son propre <h2>/carte séparée.
    assert html.count("Structure &amp; stress-test") == 1


def test_mb3_macro_fused_no_coherence_in_fiche():
    """MB3 — « Macro · liens chiffrés » fondue dans « Contexte global » ; plus de
    section séparée ; « Cohérence macro » retirée des fiches."""
    html = render({"header": {"date": "x"},
                   "macro_context": {"regime_synthesis": "Risk-on."},
                   "macro_impact": {"intro": "Le régime porte les alts.",
                                    "exposed_positions": [{"asset": "TAO",
                                                           "driver": "VIX bas",
                                                           "effect": "soutien"}],
                                    "implication": "Vent arrière."},
                   "thesis_of_the_day": [_detailed_thesis()]}, "morning")
    assert "Liens chiffrés sur ton portefeuille" in html
    assert "Macro · liens chiffrés sur ton PTF" not in html  # plus de section H2
    assert "Cohérence avec la macro du jour" not in html      # retirée des fiches
    assert "NE DOIT PAS APPARAITRE" not in html


def test_mb4_thesis_card_8_blocks():
    """MB4 — fiche : « Ce qui me ferait tort » (auto-critique + contre-thèse
    fusionnées), « Niveaux & scénarios » (cibles + bull/base/bear), DCA dans le
    plan."""
    html = render({"header": {"date": "x"},
                   "thesis_of_the_day": [_detailed_thesis()]}, "morning")
    assert "Ce qui me ferait tort" in html
    assert "auto-critique" in html.lower() and "invaliderait la structure" in html
    assert "Niveaux &amp; scénarios" in html
    assert "Bull 23%" in html and "Espérance 30j" in html
    assert "DCA en 3 tranches" in html and "40% immédiat" in html
    # plus les anciens titres séparés.
    assert "Mon auto-critique" not in html
    assert "Contre-thèse (ce qui me ferait tort)" not in html


def test_mb6_watch_sections_merged():
    """MB6 — « À surveiller » + « invalider » fusionnés ; auto-critique à part."""
    html = render({"header": {"date": "x"},
                   "today_watch": "Surveiller USD/JPY.",
                   "invalidation_watch": [{"condition": "DXY > 101,5",
                                           "implication": "pression alts",
                                           "status": "ok"}],
                   "self_critique_global": {"bullets": ["ETF indispo ce matin."]}},
                  "morning")
    assert "À surveiller · seuils d'invalidation" in html
    assert "Surveiller USD/JPY." in html and "DXY &gt; 101,5" in html
    assert "Auto-critique de l'analyse globale" in html      # séparée
    assert "Ce que je surveille pour invalider" not in html  # ancien titre parti


def test_mb7_tracking_progress_bar_no_doublon():
    """MB7 — table compacte + barre ; « X% du chemin » (doublon) supprimé."""
    html = render({"header": {"date": "x"},
                   "active_recommendations_tracking": [
                       {"asset": "RSR", "action": "RENFORCER", "days_open": 5,
                        "entry_price": 0.00119, "current_price": 0.00127,
                        "progress_pct": 6.6, "ct_target": 0.0013,
                        "dist_to_target_pct": 2.3, "target_path_pct": 73,
                        "status": "🟢 en bonne voie", "comment": "73% du chemin vers la cible."}]},
                  "morning")
    assert "du chemin" not in html            # doublon commentaire supprimé
    assert "73%" in html and "Progrès" in html
    assert "Entrée→actuel" in html


def test_mb8_score_line_only_for_non_expanded():
    """MB8 — la ligne ⚙ du tableau récap n'apparaît que pour les non-dépliées ;
    la dépliée renvoie « ★ détail ci-dessous »."""
    t_exp = _detailed_thesis()
    t_flat = {**_detailed_thesis(), "asset": "LINK", "_expand": False,
              "thesis_scoring": {"score": 8, "threshold": 2,
                                 "signals": [{"label": "structure HH/HL", "weight": 2}]}}
    html = render({"header": {"date": "x"},
                   "thesis_of_the_day": [t_exp, t_flat]}, "morning")
    assert "★ détail ci-dessous" in html          # ligne de la dépliée
    assert "structure HH/HL" in html              # ⚙ de la non-dépliée


def test_mb9_news_no_confidence():
    """MB9 — plus de « Confiance X% » par news ; impact conservé."""
    html = render({"header": {"date": "x"},
                   "news_24h": [{"category": "Catalyseur", "title": "IA & puces",
                                 "source": "Yahoo", "impact_on_ptf": "NVDA↔FET +0.54.",
                                 "confidence": 80}]}, "morning")
    assert "NVDA↔FET +0.54." in html
    assert "Confiance 80" not in html


# ═══════════════════════════════════════════════════════════════════ EVENING

def test_eb1_no_dark_box_keep_market_changes():
    """EB1 — boîte noire « À retenir » supprimée ; « Ce qui a évolué » gardé."""
    html = render({"header": {"date": "x"},
                   "delta_summary": [{"icon": "✓", "text": "NE PAS AFFICHER"}],
                   "market_changes": [{"status": "confirmed", "tag": "Macro",
                                       "description": "S&P +29 pts", "source": "Yahoo"}]},
                  "evening")
    assert "À retenir" not in html and "NE PAS AFFICHER" not in html
    assert "Ce qui a évolué" in html and "S&amp;P +29 pts" in html


def test_eb3_night_levels_compact():
    """EB3 — niveaux nuit densifiés (1 bloc/actif, readout conservé)."""
    html = render({"header": {"date": "x"},
                   "levels_tonight": [{"asset": "BTC", "level": "64 072 $",
                                       "type": "resistance", "trigger": "test 65 970 (MM50)"},
                                      {"asset": "BTC", "level": "62 900 $",
                                       "type": "support", "trigger": "risque 60 862"}],
                   "levels_readout": {"BTC": "RSI 52 · MACD haussier · ATR 1,3%"},
                   "evening_macro": {"btc_price": 63753}}, "evening")
    assert "test 65 970 (MM50)" in html and "risque 60 862" in html
    assert "RSI 52 · MACD haussier · ATR 1,3%" in html


def test_zb5_evening_international_light():
    """ZB5 — le soir garde USD/JPY (carry), retire Nikkei/Stoxx/EUR."""
    html = render({"header": {"date": "x"},
                   "evening_macro": {"btc_price": 63753, "usd_jpy": 161.69,
                                     "nikkei": 68558, "stoxx50": 6270,
                                     "eur_usd": 1.14}}, "evening")
    assert "USD/JPY" in html and "carry trade yen" in html
    assert "Nikkei" not in html and "Stoxx" not in html


# ═══════════════════════════════════════════════════════════════════ WEEKLY

def test_zb2_retro_banner_merged():
    """ZB2 — « Verdict des appels » + « Depuis le hebdo précédent » = 1 bandeau."""
    html = render({"header": {"date": "x"},
                   "calls_review": {"available": True, "summary_line": "Neutre 50% conforme"},
                   "week_over_week": {"available": True, "lines": ["PTF 2704 → 2748"],
                                      "prev_date_label": "05/07"}}, "weekly")
    i_verdict = html.find("Mes appels de la semaine passée")
    i_wow = html.find("Depuis le hebdo précédent")
    i_ptf = html.find("Portfolio · vue d'ensemble")
    assert i_verdict != -1 and i_wow != -1 and i_ptf != -1
    # bandeau unique : verdict PUIS WoW, les deux AVANT la vue d'ensemble PTF.
    assert i_verdict < i_wow < i_ptf


def test_wb5_scenarios_preamble_bascule():
    """WB5 — préambule catalyseur + ligne de bascule avant les scénarios."""
    html = render({"header": {"date": "x"},
                   "scenarios": [{"type": "bearish", "label": "BAISSIER",
                                  "probability_pct": 22, "triggers": ["CPI > consensus"],
                                  "points": ["DXY monte"], "action": "ordres limites"}],
                   "scenarios_context": {"catalyst": "Inflation US CPI (dans 3j)",
                                         "btc_pivot_label": "62 900 $",
                                         "bascule": "clôture BTC > 65 970 $ = haussier · < 62 900 $ = baissier · entre = neutre"}},
                  "weekly")
    assert "Catalyseur commun :" in html and "Inflation US CPI (dans 3j)" in html
    assert "Signal de bascule :" in html and "65 970 $ = haussier" in html


def test_wb7_sources_prose_removed():
    """WB7 — le paragraphe filler « Sources actives » (summary) n'est plus rendu."""
    html = render({"header": {"date": "x", "active_sources_count": 22,
                              "total_sources_count": 25},
                   "sources_review": {"summary": "Nous exploitons un ensemble filler.",
                                      "gaps": "ETF flows indispo 7 j/7."}}, "weekly")
    assert "Nous exploitons un ensemble filler." not in html
    assert "ETF flows indispo 7 j/7." in html                # lacunes conservées
    assert "22 / 25 actives" in html or "22 /" in html


def test_wb2_postmortem_empty_collapsed():
    """WB2 — sans clôture ni pertes, « Coût des erreurs » vide ne s'affiche pas en
    plus (fusion) ; « Mon erreur » (rempli) reste."""
    html = render({"header": {"date": "x"},
                   "regret": {"available": False, "empty_reason": "rien à mesurer"},
                   "my_errors": "Sous-estimé la Peur Extrême."}, "weekly")
    # (le filtre |md peut lisser l'apostrophe → on teste un fragment sans « ' »)
    assert "sous-estimé la peur extrême" in html.lower()
    # une seule mention du « rien à mesurer » (pas deux sous-sections vides).
    assert html.count("rien à mesurer") <= 1
    assert "Pertes &amp; coût des erreurs" in html


def test_zb3_watchlist_folded_in_plan():
    """ZB3 — la Watchlist est fondue dans « Plan d'action » (pas de H2 séparé)."""
    html = render({"header": {"date": "x"},
                   "weekly_action_plan": [{"priority": 1, "action": "Accumuler BTC",
                                           "rationale": "support"}],
                   "watchlist": [{"direction": "entrée", "asset": "BTC",
                                  "trigger": "62 900 $"}]}, "weekly")
    assert "Plan d'action de la semaine" in html
    assert "Entrées / sorties surveillées" in html
    assert ">Watchlist" not in html                          # plus de section H2
