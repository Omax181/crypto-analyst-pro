# -*- coding: utf-8 -*-
"""v29 — verrous de l'audit des mails du 10/07 (points A : MA*/EA*/WA*).

Chaque test reproduit le défaut OBSERVÉ dans les PDF du 10/07 et verrouille la
correction déterministe. Références : MA1-MA17 (matin), EA1-EA6 (soir),
WA1-WA12 (hebdo).
"""

from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════════
# daily_guards — gardes déterministes matin/soir
# ═══════════════════════════════════════════════════════════════════════════

def test_ma3_active_addresses_named_assets_locked():
    """MA3 — « (BTC -11.8%, ETH -4.4%) » réaligné sur les tuiles (−9.2 / −4.4)."""
    from src.analytics.daily_guards import fix_active_addresses

    txt = ("Cependant, la baisse des adresses actives sur 7j (BTC -11.8%, "
           "ETH -4.4%) montre une divergence prix/activité.")
    out, fixes = fix_active_addresses(txt, {"BTC": -9.2, "ETH": -4.4})
    assert "−9,2%" in out          # BTC réécrit sur la valeur de la tuile
    assert "-11.8" not in out
    assert "ETH -4.4%" in out      # ETH conforme → intact
    assert len(fixes) == 1 and "BTC" in fixes[0]


def test_ma3_active_addresses_asset_hint_form():
    """MA3 — forme sans actif nommé : l'actif vient du contexte de la thèse."""
    from src.analytics.daily_guards import fix_active_addresses

    txt = "faiblesse de l'activité réseau (adresses actives -11.8% sur 7j)"
    out, fixes = fix_active_addresses(txt, {"BTC": -9.2}, asset_hint="BTC")
    assert "−9,2%" in out and "-11.8" not in out and fixes
    # Valeur déjà conforme → aucun changement.
    out2, fixes2 = fix_active_addresses(
        "adresses actives -4.4% sur 7j", {"ETH": -4.4}, asset_hint="ETH")
    assert out2 == "adresses actives -4.4% sur 7j" and not fixes2


def test_ma4_dxy_wording_single_qualifier():
    """MA4 — « dollar fort » ET « dollar faible » sur le même DXY → canonique."""
    from src.analytics.daily_guards import dxy_qualifier, fix_dxy_wording

    assert dxy_qualifier(0.1) == "stable"
    assert dxy_qualifier(0.9) == "en hausse"
    assert dxy_qualifier(-0.6) == "en baisse"
    assert dxy_qualifier(None) is None

    node = {"a": "le dollar fort (DXY 100.799) limite la liquidité",
            "b": "Le dollar faible (DXY 100.799) soutient le BTC"}
    out, fixes = fix_dxy_wording(node, "stable")
    assert "dollar stable" in out["a"] and "dollar stable" in out["b"]
    assert len(fixes) == 2
    # Qualificatif compatible → intact (fort ↔ en hausse).
    out2, fixes2 = fix_dxy_wording("le dollar fort pèse", "en hausse")
    assert out2 == "le dollar fort pèse" and not fixes2


def test_ma10_cpi_claims_rewritten_or_stripped():
    """MA10 — « CPI 4.3% » : réaligné sur FRED, ou chiffre RETIRÉ sans source."""
    from src.analytics.daily_guards import fix_cpi_claims

    txt = "la persistance de l'inflation US (CPI 4.3%)."
    out, fixes = fix_cpi_claims(txt, 2.7)
    assert "2,7%" in out and "4.3" not in out and fixes
    out2, fixes2 = fix_cpi_claims(txt, None)
    assert "4.3" not in out2 and "%" not in out2 and fixes2
    assert "CPI" in out2  # la référence reste, le chiffre inventé disparaît
    # Valeur conforme → intact.
    out3, fixes3 = fix_cpi_claims("CPI 2.7% en glissement annuel", 2.7)
    assert out3 == "CPI 2.7% en glissement annuel" and not fixes3


def test_ma11_polymarket_recycled_pct_on_unknown_strike_dropped():
    """MA11 — « Polymarket donne 84% de toucher 65 000 $ » : strike inconnu →
    la phrase saute MÊME si 84 ≈ 84.5 (proba Fed recyclée)."""
    from src.analytics.daily_guards import sanitize_polymarket_claims

    txt = ("Polymarket donne 84% de chances de toucher 65 000 $ en juillet, "
           "agissant comme un catalyseur psychologique. Le funding reste sain.")
    known = [84.5, 15.5, 50.0, 50.0]
    strikes = {"67500"}
    out, fixes = sanitize_polymarket_claims(txt, known, strikes)
    assert "65 000" not in out and "84%" not in out
    assert "Le funding reste sain." in out  # le reste du paragraphe survit
    assert fixes
    # Marché réellement fourni → conservé (strike ET proba connus).
    ok_txt = "Polymarket donne 50% de chances d'atteindre 67 500 $ en juillet."
    out2, fixes2 = sanitize_polymarket_claims(ok_txt, known, strikes)
    assert out2 == ok_txt and not fixes2


def test_wa4_fed_balance_locked_cross_mail():
    """WA4 — « bilan Fed +2.0% » (matin) vs « +1,4% » (hebdo) → un seul chiffre."""
    from src.analytics.daily_guards import fix_fed_balance_claims

    out, fixes = fix_fed_balance_claims(
        "Le bilan de la Fed reste en expansion de +1,4% (QE).", 2.0)
    assert "+2%" in out and "1,4" not in out and fixes
    out2, fixes2 = fix_fed_balance_claims("bilan Fed en hausse (+2.0%)", 2.0)
    assert out2 == "bilan Fed en hausse (+2.0%)" and not fixes2
    out3, fixes3 = fix_fed_balance_claims("bilan Fed +5%", None)
    assert out3 == "bilan Fed +5%" and not fixes3  # pas de référence → no-op


def test_ma8_historical_spin_replaced_when_stats_bad():
    """MA8 — stats défavorables (−2.3%, 28%) : la conclusion en spin saute."""
    from src.analytics.daily_guards import fix_historical_spin

    eth = ("Sur 95 jours d'historique, une configuration similaire s'est "
           "présentée 18 fois. Le rendement moyen à 7 jours est de -2.3%, "
           "positif dans 28% des cas, confirmant que nous achetons dans la "
           "douleur avant le retournement.")
    out, fixes = fix_historical_spin(eth)
    assert "confirmant" not in out
    assert "défavorable à 7 j" in out and fixes
    # « reflétant la résistance » (BTC 10/07) est aussi du spin.
    btc = ("Le rendement moyen à 7 jours est de -2.2%, positif dans 27% des "
           "cas, reflétant la résistance dans un marché baissier.")
    out2, fixes2 = fix_historical_spin(btc)
    assert "reflétant" not in out2 and fixes2
    # Stats favorables (TAO : +0.6%, 55%) → texte INTACT.
    tao = ("Le rendement moyen à 7 jours est de +0.6%, positif dans 55% des "
           "cas, confirmant la stabilisation du prix.")
    out3, fixes3 = fix_historical_spin(tao)
    assert out3 == tao and not fixes3


def test_ma2_enbref_reinforce_claim_rewritten():
    """MA2 — puce EN BREF « Renforcement … (BTC, ETH, TAO, LINK) » alors que
    BTC/ETH/TAO sont au plafond → réécrite avec les recos fermes réelles."""
    from src.analytics.daily_guards import fix_reinforce_claims

    bullets = [
        "Sentiment de marché en Peur extrême (F&G 23).",
        "Renforcement tactique et de conviction sur le cœur du portefeuille "
        "(BTC, ETH, TAO, LINK) via des injections de capital externe.",
    ]
    out, fixes = fix_reinforce_claims(
        bullets, {"RENDER", "LINK", "RSR", "INJ"}, {"BTC", "ETH", "TAO"})
    assert out[0] == bullets[0]
    assert "plafond de concentration" in out[1]
    assert "RENDER" in out[1] and "BTC" in out[1]
    assert "injections de capital externe" not in out[1]
    assert fixes
    # Puce « renforcer » ne citant QUE des recos fermes réelles → intacte.
    ok = ["Renforcer LINK sur repli."]
    out2, fixes2 = fix_reinforce_claims(ok, {"LINK"}, {"BTC"})
    assert out2 == ok and not fixes2


def test_ma12_rotation_note_hidden_sector_sentences_dropped():
    """MA12 — les phrases sur des secteurs NON affichés (DeFi, IA) sautent."""
    from src.analytics.daily_guards import filter_rotation_note

    note = ("Le secteur L2 mène le rebond à court terme (+3.98% sur 24h). "
            "À l'inverse, le secteur DeFi montre des signes d'émergence "
            "tactique (+5.34% sur 7j), tandis que l'IA consolide après sa "
            "correction mensuelle (-5.56%).")
    displayed = ["L2", "IoT/Data", "Autres · 8 secteurs", "Oracle/Infra"]
    all_sectors = ["L2", "IoT/Data", "Oracle/Infra", "DeFi", "AI", "L1", "Autre"]
    out, fixes = filter_rotation_note(note, displayed, all_sectors)
    assert "L2 mène le rebond" in out
    assert "DeFi" not in out and "IA" not in out
    assert fixes
    # Fail-safe : si TOUT saute, l'original est conservé.
    only_hidden = "Le secteur DeFi rebondit fort."
    out2, fixes2 = filter_rotation_note(only_hidden, displayed, all_sectors)
    assert out2 == only_hidden and not fixes2


def test_ea1_speculative_actionable_downgraded():
    """EA1 — news au conditionnel taguée « actionnable » → « à suivre »."""
    from src.analytics.daily_guards import downgrade_speculative_actionable

    items = [
        {"title": "Le dollar numérique du gouvernement US pourrait être "
                  "interdit ce soir", "impact": "favorable aux cryptos "
                  "décentralisées.", "status": "actionnable"},
        {"title": "Circle obtient l'approbation finale de l'OCC",
         "impact": "Renforce la légitimité des stablecoins.",
         "status": "actionnable"},
    ]
    fixes = downgrade_speculative_actionable(items)
    assert items[0]["status"] == "à suivre"      # spéculative → dégradée
    assert items[1]["status"] == "actionnable"   # factuelle → conservée
    assert len(fixes) == 1


def test_walk_strings_drops_emptied_list_items_and_skips_meta():
    """walk_strings — une puce vidée par une garde disparaît ; les clés de
    métadonnées (asset, label…) ne sont jamais réécrites."""
    from src.analytics.daily_guards import walk_strings

    node = {"asset": "dollar fort", "items": ["garde", "  "],
            "txt": "dollar fort"}
    out = walk_strings(node, lambda s: "" if s.strip() == "garde" else s)
    assert out["asset"] == "dollar fort"     # clé méta intacte
    assert out["items"] == ["  "]            # « garde » vidée → retirée
    assert out["txt"] == "dollar fort"


# ═══════════════════════════════════════════════════════════════════════════
# weekly_guards — nouvelles gardes hebdo
# ═══════════════════════════════════════════════════════════════════════════

def test_wa2_cbdc_news_trio_deduplicated():
    """WA2 — 3 titres couvrant la même actu CBDC/loi logement → 1 seul."""
    from src.analytics.weekly_guards import dedupe_weekly_news, is_duplicate_news

    t1 = ("US CBDC ban set to become law without Trump's signature on the "
          "housing bill")
    t2 = ("US government's digital dollar set to be banned tonight under "
          "housing bill CBDC limit")
    t3 = ("Trump won't sign housing bill with CBDC ban — will it become law "
          "tonight anyway?")
    t_other = "Circle wins final OCC approval for national trust bank"
    assert is_duplicate_news(t1, t2) and is_duplicate_news(t1, t3)
    assert not is_duplicate_news(t1, t_other)
    items = [{"title": t} for t in (t1, t2, t3, t_other)]
    kept, fixes = dedupe_weekly_news(items)
    assert [k["title"] for k in kept] == [t1, t_other]
    assert len(fixes) == 2


def test_wa9_ptf_impact_appended_next_to_cited_loss():
    """WA9 — « FET (-14,7%) » commenté comme LA perte → poids et impact chiffrés."""
    from src.analytics.weekly_guards import append_ptf_impact

    txt = ("la baisse de FET (-14,7%) affecte notre allocation IA globale, "
           "validant notre choix.")
    impacts = {"FET": {"weight_pct": 1.1, "impact_pt": -0.16}}
    out, fixes = append_ptf_impact(txt, impacts)
    assert "poids 1,1%" in out and "impact PTF −0,16 pt" in out and fixes
    # Déjà qualifié (« impact » à proximité) → pas de double ajout.
    out2, fixes2 = append_ptf_impact(out, impacts)
    assert out2 == out and not fixes2
    # Actif sans données → intact.
    out3, fixes3 = append_ptf_impact("XYZ (-12%) chute.", impacts)
    assert out3 == "XYZ (-12%) chute." and not fixes3


def test_wa10_dust_liquidate_now_becomes_abandon():
    """WA10 — « liquidation immédiate » d'une poussière sous le seuil de frais
    (prémisse : frais > valeur) → abandon assumé, pas un ordre de vente."""
    from src.analytics.weekly_guards import fix_dust_advice

    node = {"diagnosis": (
        "Pour SXT (valeur 0,12 $), nous recommandons une liquidation immédiate "
        "sans attendre, les frais de transaction risquant de dépasser la "
        "valeur résiduelle. Le reste attend un rebond.")}
    out, fixes = fix_dust_advice(node, {"SXT"})
    assert "liquidation immédiate" not in out["diagnosis"]
    assert "ABANDONNER" in out["diagnosis"]
    assert "Le reste attend un rebond." in out["diagnosis"]
    assert fixes
    # Poussière absente du texte → intact.
    out2, fixes2 = fix_dust_advice(node, {"ZZZ"})
    assert out2 == node and not fixes2


def test_wa12_bearish_structure_reinforce_gets_lt_framing():
    """WA12 — « structure daily baissière » + action Renforcer → cadrage LT."""
    from src.analytics.weekly_guards import reconcile_bearish_reinforce

    entries = [
        {"asset": "INJ", "action": "renforcer",
         "analysis": "-90.5% sous ATH. Structure daily baissière mais rebond "
                     "en cours sur support historique."},
        {"asset": "TAO", "action": "renforcer",
         "analysis": "Structure daily baissière, accumulation en zone de "
                     "capitulation."},                      # déjà cadré
        {"asset": "ANKR", "action": "garder",
         "analysis": "Structure daily baissière, pas de catalyseur."},
    ]
    fixes = reconcile_bearish_reinforce(entries)
    assert "accumulation LT (DCA)" in entries[0]["analysis"]
    assert "accumulation LT (DCA)" not in entries[1]["analysis"]  # déjà qualifié
    assert "accumulation LT (DCA)" not in entries[2]["analysis"]  # pas renforcer
    assert len(fixes) == 1 and "INJ" in fixes[0]


# ═══════════════════════════════════════════════════════════════════════════
# reco_gate — MA7 : plafond + EV négative → mention frontale
# ═══════════════════════════════════════════════════════════════════════════

def test_ma7_capped_maintain_with_negative_ev_gets_ct_warning():
    """MA7 — MAINTENIR (plafond) n'échappe plus au check EV : mention posée."""
    from src.analytics.reco_gate import apply_reco_gate

    payload = {"thesis_of_the_day": [
        {"asset": "ETH", "action": "RENFORCER", "thesis_type": "conviction",
         "confidence": 78, "action_plan": {"position_size_pct": 0},
         "asset_plan": {"ev_30d_pct": -2.1, "rr_30d": 0.5}},
        {"asset": "BTC", "action": "RENFORCER", "thesis_type": "conviction",
         "confidence": 76, "action_plan": {"position_size_pct": 0},
         "asset_plan": {"ev_30d_pct": 1.3, "rr_30d": 0.8}},
    ]}
    fixes = apply_reco_gate(payload)
    eth, btc = payload["thesis_of_the_day"]
    assert eth["action"] == "MAINTENIR" and btc["action"] == "MAINTENIR"
    assert "EV 30j −2.1%" in (eth.get("ct_warning") or "")
    assert btc.get("ct_warning") is None          # EV positive → pas de mention
    assert any("EV<0" in f for f in fixes)


# ═══════════════════════════════════════════════════════════════════════════
# main.py — cibles Python forcées (MA5/WA1), rotation (MA14/WA11), EA2
# ═══════════════════════════════════════════════════════════════════════════

def _mk_plan(target_level: float, cyc_low: float, cyc_high: float) -> dict:
    return {
        "available": True,
        "invalidation": {"level": 60862.0, "level_label": "60 862 $",
                         "basis": "pivot", "dist_pct": -4.8},
        "target_30d": {"level": target_level, "level_label": f"{target_level:,.0f} $",
                       "basis": "résistance ancrée", "low": target_level - 800,
                       "high": target_level + 800, "low_label": "l", "high_label": "h",
                       "upside_pct": 2.4},
        "target_cycle": {"low": cyc_low, "high": cyc_high, "kind": "cycle",
                         "low_label": "L", "high_label": "H", "upside_pct": 97},
        "rr_30d": 0.8, "prob_up_30d": 0.5, "ev_30d_pct": -0.5,
        "scenarios": {"bull": {"probability_pct": 22, "level": target_level + 800,
                               "level_label": "b"},
                      "base": {"probability_pct": 55, "low": 62900.0,
                               "high": target_level, "range_label": "r"},
                      "bear": {"probability_pct": 23, "level": 60000.0,
                               "level_label": "x"}},
        "accumulation_zone": {"low": 61000.0, "high": 63000.0,
                              "low_label": "a", "high_label": "z"},
        "dca": [{"price": 63921.0, "price_label": "63 921 $", "weight_pct": 40,
                 "basis": "prix actuel"}],
        "plan_line": "plan",
    }


def test_ma5_wa1_python_targets_overwrite_llm_targets():
    """MA5/WA1 — les cibles IA (30j > scénario bull ; bornes LT divergentes du
    weekly) sont ÉCRASÉES par asset_plan (source unique cross-mail)."""
    from src.main import _apply_asset_plans_to_theses

    payload = {"thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER",
        "targets": {"short_term_30d": 70401.0,        # IA : > bull (66 281)
                    "long_term_6_12m_low": 87666.0,   # IA : ≠ fib weekly
                    "long_term_6_12m_high": 126080.0},
    }]}
    data = {"eligible_theses": [{
        "asset": "BTC", "conviction": True, "value_usd": 1158.0,
        "asset_plan": _mk_plan(65481.0, 102366.0, 126080.0),
    }], "portfolio_snapshot": {"value_usd": 2735.0}}
    _apply_asset_plans_to_theses(payload, data)
    t = payload["thesis_of_the_day"][0]
    assert t["targets"]["short_term_30d"] == 65481.0       # Python, ≤ bull
    assert t["targets"]["short_term_30d"] <= t["asset_plan"]["scenarios"]["bull"]["level"]
    assert t["targets"]["long_term_6_12m_low"] == 102366.0  # même fib que weekly
    assert t["targets"]["long_term_6_12m_high"] == 126080.0
    assert "fourchette" in (t["targets"].get("short_term_note") or "")


def test_ma14_wa11_rotation_tiles_exclude_pseudo_sector():
    """MA14 — « Autre » n'est jamais une tuile individuelle ; WA11 — libellé
    d'agrégat explicite « Autres · N secteurs »."""
    from src.main import _select_rotation_tiles

    sec = {
        "L2": {"avg_change_24h": 3.98, "members": ["ARB"]},
        "IoT/Data": {"avg_change_24h": 3.13, "members": ["JASMY"]},
        "Autre": {"avg_change_24h": 12.0, "members": ["1000SATS"]},  # énorme move
        "Oracle/Infra": {"avg_change_24h": 2.54, "members": ["LINK"]},
        "AI": {"avg_change_24h": -1.2, "members": ["TAO"]},
        "L1": {"avg_change_24h": 0.9, "members": ["BTC"]},
        "DeFi": {"avg_change_24h": 0.5, "members": ["INJ"]},
    }
    tiles = _select_rotation_tiles(sec)
    names = [t["sector"] for t in tiles]
    assert "Autre" not in names                     # jamais en tuile malgré +12%
    assert names[0] == "L2"                         # tri |Δ| conservé (hors pseudo)
    assert tiles[-1]["is_aggregate"] is True
    assert tiles[-1]["sector"].startswith("Autres ·")
    assert len(tiles) == 5
    # ≤ 5 secteurs nommés, pas de pseudo → tous individuels (comportement v18).
    small = {f"S{i}": {"avg_change_24h": float(i), "members": ["X"]}
             for i in range(4)}
    assert len(_select_rotation_tiles(small)) == 4


def test_ea2_hawkish_repricing_named_in_since_morning_line():
    """EA2 — proba hausse Fed 14,6% → 22,1% (+7,5 pts) : nommé et qualifié."""
    from src.main import _build_since_morning_facts

    morning_state = {"macro_context": {
        "btc_price": 63921, "fear_greed": 23, "dxy": 100.80,
        "polymarket_fed_bars": {"dominant": "maintien", "dominant_pct": 84.5,
                                "hike_pct": 14.6, "cut_pct": 0.5},
    }}
    evening_macro = {"btc_price": 63753, "fear_greed": 23, "dxy": 100.95}
    polymarket = {"fed_bars": {"dominant": "maintien", "dominant_pct": 76.5,
                               "hike_pct": 22.1, "cut_pct": 0.0}}
    facts = _build_since_morning_facts(
        morning_state, True, evening_macro, polymarket, "08h38")
    assert facts and "repricing hawkish" in facts["line"]
    assert "22,1%" in facts["line"] and "+7,5 pts" in facts["line"]
    # Delta < 3 pts → pas de ligne d'alerte.
    polymarket2 = {"fed_bars": {"dominant": "maintien", "dominant_pct": 83.5,
                                "hike_pct": 15.6, "cut_pct": 0.5}}
    facts2 = _build_since_morning_facts(
        morning_state, True, evening_macro, polymarket2, "08h38")
    assert facts2 and "repricing" not in facts2["line"]


# ═══════════════════════════════════════════════════════════════════════════
# Rendus (templates) — MA6/MA9/MA13/MA1, EA3/EA5/EA6/EA4, WA6
# ═══════════════════════════════════════════════════════════════════════════

def _thesis_card(action: str, **extra) -> dict:
    t = {
        "asset": "ETH", "name": "Ethereum", "action": action,
        "action_type": "bullish", "thesis_type": "conviction",
        "confidence": 70, "_expand": True, "current_price": 1774.0,
        "rr_value": 0.5,
        "targets": {"short_term_30d": 1875.0, "long_term_6_12m_low": 3742.0,
                    "long_term_6_12m_high": 4946.0},
        "observation": "Setup d'accumulation.",
        # v29 (MB4) — le plan DCA vit désormais dans le bloc « plan d'action »,
        # qui exige un action_plan (comme en prod : _apply_asset_plans_to_theses
        # le pose toujours sur une thèse ferme).
        "action_plan": {"entry": 1774.0, "stop_loss": 1621.0, "rr": "0.5",
                        "invalidation_conditions": "sous 1 621 $ (pivot)"},
        "asset_plan": _mk_plan(1875.0, 3742.0, 4946.0),
        "thesis_scoring": {
            "score": 14, "threshold": 2, "dimensions_count": 4,
            "signals": [{"label": "MVRV 0.87 < 1", "weight": 3}],
            "completeness": {"pct": 83, "missing": ["sentiment"]},
        },
    }
    t.update(extra)
    return t


def test_ma6_ma15_dca_only_for_effective_reinforce():
    """MA6 — MAINTENIR (plafond) : plus de plan DCA contradictoire.
    MA15 — RENFORCER : la 1re tranche est « immédiat », pas un faux palier."""
    from src.reporting.email_html import render

    base = {"header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2735}}
    html_m = render({**base, "thesis_of_the_day": [
        _thesis_card("MAINTENIR", gate_note="déjà 19% du PTF (plafond 12%)")
    ]}, "morning")
    assert "DCA en 3 tranches" not in html_m
    html_r = render({**base, "thesis_of_the_day": [_thesis_card("RENFORCER")]},
                    "morning")
    assert "DCA en 3 tranches" in html_r
    assert "40% immédiat" in html_r


def test_ma9_ma13_dagger_and_completeness_label():
    """MA9 — R:R < seuil sur conviction LT : † + note de lecture.
    MA13 — « manque : sentiment » désambiguïsé (sentiment SOCIAL)."""
    from src.reporting.email_html import render

    html = render({
        "header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2735},
        "thesis_of_the_day": [_thesis_card(
            "RENFORCER",
            ct_warning="⚠ EV 30j −2.1% · R:R 0.5 — accumulation LT")],
    }, "morning")
    assert "†" in html
    assert "le R:R tactique 30 j n'est pas le critère" in html
    assert "sentiment social (LunarCrush/social trending)" in html
    assert "accumulation LT — stats CT défavorables" in html


def test_ma1_chart_referenced_inline_for_expanded_thesis():
    """MA1 — la carte dépliée référence <img src="cid:chart_X"> quand le chart
    de CET actif existe (inline = attaché, zéro pièce jointe orpheline)."""
    from src.reporting.email_html import render

    payload = {"header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2735},
               "thesis_of_the_day": [_thesis_card("RENFORCER")]}
    html = render(payload, "morning", charts={"ETH": b"png"})
    assert 'cid:chart_ETH' in html
    html2 = render(payload, "morning", charts={"RENDER": b"png"})
    assert "cid:chart_" not in html2  # chart d'un actif non déplié → pas de ref


def test_ea3_ea5_ea6_evening_render():
    """EA3 — « baisse 0.0% » → « ≈0% » ; EA5 — « fin de séance » ;
    EA6 — ligne de réconciliation du régime baissier."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "x", "us_market_open": True,
                   "us_session_label": "fin de séance"},
        "portfolio_snapshot": {"value_usd": 2731},
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "BAISSIER", "days_in_regime": 4},
        "evening_macro": {"btc_price": 63753, "fear_greed": 23, "dxy": 100.95},
        "polymarket_facts": {"fed_bars": {
            "dominant": "maintien", "dominant_pct": 76.5,
            "cut_pct": 0.0, "hike_pct": 22.1}},
    }
    html = render(payload, "evening")
    assert "Marchés · fin de séance" in html
    assert "≈0%" in html and "baisse 0.0%" not in html
    assert "hausse 22.1%" in html
    assert "coexistent sans se contredire" in html   # EA6 (régime bear)


def test_ea4_movers_dollar_impact_homogeneous():
    """EA4 — impact $ homogène : chaque mover chiffrable l'affiche (< 1 $ → <$1)."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2731},
        "daily_pnl": {"day_change_pct": -0.15, "top_movers": [
            {"symbol": "ETH", "change": 1.9, "pnl_usd": 10.2},
            {"symbol": "QNT", "change": 2.5, "pnl_usd": 2.4},
            {"symbol": "STX", "change": -2.0, "pnl_usd": -0.4},
        ]},
    }
    html = render(payload, "evening")
    assert "(+$10)" in html and "(+$2)" in html
    assert "(&lt;$1)" in html


def test_wa6_weekly_win_rate_30d_gated_below_five_closed():
    """WA6 — sous 5 clôturées : « en calibration (2/5) », jamais « 100% »."""
    from src.reporting.email_html import render

    payload = {
        "header": {"date": "x"}, "portfolio_snapshot": {"value_usd": 2748},
        "predictions_scoring": {
            "issued": 7, "validated": 0, "invalidated": 0, "neutral": 0,
            "win_rate_pct": None, "win_rate_30d": None,
            "win_rate_30d_gate": "en calibration (2/5)",
            "winrate_gate_label": "Recos clôturées : 0/5 minimum pour calibration",
        },
    }
    html = render(payload, "weekly")
    assert "en calibration (2/5)" in html
    assert "Win rate 30j · 100%" not in html
