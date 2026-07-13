"""v29 (TG-refonte briefing) — messages Telegram au format « briefing d'analyste ».

Couvre la refonte de ``src/telegram_bot/notify.py`` validée avec Omar (12/07) :
régime TOUJOURS argumenté, 🎯 action explicite (formulation ≠ EN BREF),
📊 « Évolution des thèses énoncées » (top 3 : conviction + évolution, date
d'émission, entrée → actuel → cible, lecture courte), soir conditionnel (une
thèse n'apparaît que si elle a bougé), calendrier expliqué (Polymarket jamais
accolé à un événement non-Fed), rétro hebdo, commandes personnalisées limitées
aux actifs affichés, formatage FR et robustesse (jamais de crash, Markdown
équilibré, < 4096 caractères).
"""

from __future__ import annotations

import pytest

from src.telegram_bot import notify


# --------------------------------------------------------------------------- #
# Fixtures — payloads aux VRAIS noms de champs du pipeline
# --------------------------------------------------------------------------- #
def _morning() -> dict:
    from src.analytics.reco_gate import NOTHING_TO_DO_LINE
    return {
        "header": {"time_casablanca": "mardi 7 juillet · 08:35"},
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "BAISSIER", "days_in_regime": 1,
                          "price_vs_ma200_pct": -8.3,
                          "reasons": ["prix −8,3% vs MM200"]},
        "macro_regime_readout": {"regime": "transition", "confidence_pct": 70,
                                 "crypto_bias": "neutre"},
        "macro_context": {
            "regime_synthesis": "Le dollar reste ferme et les actions US hésitent "
                                "avant la Fed : contexte indécis. La peur domine.",
            "fear_greed": 27, "fear_greed_label": "peur",
            "polymarket_fed_bars": {"dominant": "maintien", "dominant_pct": 84.5}},
        "macro_agenda": {"available": True, "events": [
            {"importance": "high", "label": "CPI US", "when": "mardi"}]},
        "top_action": {"is_nothing": True, "line": NOTHING_TO_DO_LINE},
        "thesis_of_the_day": [
            {"asset": "TAO", "action": "MAINTENIR", "confidence": 70,
             "gate_note": "plafond de concentration atteint — aucun renfort",
             "observation": "TAO consolide."},
            {"asset": "ETH", "action": "RENFORCER", "confidence": 70,
             "ct_warning": "⚠ EV 30j −0.7% · R:R 0.7 — signaux court-terme "
                           "défavorables : accumulation LT (DCA), pas un trade 30 j",
             "observation": "ETH sous PRU."}],
        "portfolio_snapshot": {"value_usd": 2728, "change_24h_pct": 0.3},
        "active_recommendations_tracking": [
            {"asset": "INJ", "action": "RENFORCER", "issued_at": "05/07",
             "confidence": 70, "prev_confidence": 62,
             "entry_price": 4.54, "current_price": 4.75,
             "progress_pct": 4.4, "ct_target": 4.96, "target_path_pct": 48,
             "health_status": "🟢 En bonne voie",
             "comment": "48% du chemin vers la cible."}],
        "portfolio_heatmap": {"cells": [{"symbol": "BTC", "ptf_pct": 42.0}]},
    }


def _evening() -> dict:
    return {
        "header": {"time_casablanca": "mardi 7 juillet · 20:02",
                   "us_market_open": False},
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "BAISSIER", "days_in_regime": 1,
                          "price_vs_ma200_pct": -8.1},
        "evening_macro": {"fear_greed": 26, "fear_greed_label": "Peur"},
        "daily_pnl": {"value_usd": 2716, "day_change_usd": 3, "day_change_pct": 0.11,
                      "top_movers": [{"symbol": "HBAR", "change": -4.5},
                                     {"symbol": "TAO", "change": 12.3}]},
        "reco_bilan": [{"asset": "ETH", "action": "RENFORCER", "confidence": 70,
                        "entry": 1777.16, "current": 1767.81, "target": 1950.0,
                        "delta_pct": -0.53, "status": "stable",
                        "reason": "proche de l'entrée (bruit court terme) · "
                                  "invalidation $1,621"}],
        "levels_tonight": [{"asset": "BTC", "level": "60 862 $", "type": "support",
                            "trigger": "Sous 60 862 $ → appui 58 551 $."}],
        "since_morning_facts": "BTC est resté stable autour de 62 900 $ depuis ce matin.",
        "polymarket_facts": {"fed_bars": {"dominant": "maintien",
                                          "dominant_pct": 84.5}},
        "macro_agenda": {"available": True, "events": [
            {"importance": "high", "label": "CPI US", "when": "demain"}]},
        "portfolio_heatmap": {"cells": [{"symbol": "BTC", "ptf_pct": 42.0}]},
    }


def _weekly() -> dict:
    return {
        "header": {"time_casablanca": "mardi 7 juillet · 09:48"},
        "market_regime": {"available": True, "regime": "bear",
                          "label_fr": "BAISSIER", "days_in_regime": 1,
                          "price_vs_ma200_pct": -8.3},
        "macro_context": {"fear_greed": 26, "fear_greed_label": "Peur",
                          "polymarket_fed_bars": {"dominant": "maintien",
                                                  "dominant_pct": 84.5}},
        "portfolio_snapshot": {"value_usd": 2716, "weekly_pnl_pct": 6.06,
                               "vs_btc_7d_pct": 0.04},
        "weekly_summary": [
            "**BTC +2,2%** sur la semaine sans repasser au-dessus des moyennes "
            "longues → **fond baissier** intact.",
            "**F&G 24 → 27** : la peur recule mais reste dominante."],
        "scenarios": [{"label": "neutre", "probability_pct": 50}],
        "scenarios_context": {"catalyst": "CPI US (mardi)",
                              "bascule": "clôture BTC > 66,1k $ = haussier · "
                                         "< 60,1k $ = baissier · entre = neutre"},
        "weekly_action_plan": [{"action": "Si BTC casse 60 862 $ après les "
                                "minutes → renforcer le cœur BTC de +3% du PTF"}],
        "calls_review": {"summary_line": "Neutre annoncé (45%) → conforme."},
        "predictions_scoring": {"detail": [
            {"asset": "ETH", "reco": "RENFORCER", "entry_date": "28/06",
             "entry_price": 1980, "current_price": 1850, "delta_pct": -6.6,
             "status": "in_progress", "score": 0,
             "confidence": 78, "prev_confidence": 72, "ct_target": None},
            {"asset": "RSR", "reco": "RENFORCER", "entry_date": "20/06",
             "entry_price": 0.00115, "current_price": 0.0012, "delta_pct": 4.3,
             "status": "in_progress", "score": 0,
             "confidence": 60, "prev_confidence": None, "ct_target": None}]},
        "positions_review": [
            {"asset": "RSR", "conviction": False, "current_price": 0.0012,
             "pru_pct": -81.4,
             "h30": {"reco": "RENFORCER", "status": "in_progress", "delta_pct": 1.0},
             "lt_status": "capitulation", "lt_target_low": 0.073,
             "lt_target_high": 0.117, "lt_target_kind": "cycle",
             "analysis": "rebond technique amorcé", "action": "renforcer"},
            {"asset": "ETH", "conviction": True, "pru_pct": -41.7,
             "lt_status": "accumulation", "lt_target_low": 3746,
             "lt_target_high": 4946, "lt_target_kind": "6-12m",
             "analysis": "MVRV 0,89 : capitulation historique", "action": "renforcer"}],
        "week_ahead": [
            {"importance": "high", "label": "CPI US", "when": "mardi"},
            {"importance": "medium", "label": "BOE Gov Bailey Speaks (UK)",
             "when": "jeudi"}],
    }


# --------------------------------------------------------------------------- #
# Structure commune (EN BREF + séparateur)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind,builder", [
    ("morning", _morning), ("evening", _evening), ("weekly", _weekly)])
def test_two_zone_structure(kind, builder):
    d = notify._build_digest(builder(), kind)
    assert d.startswith("*")
    assert "*📌 EN BREF*" in d
    assert "──────────" in d
    # EN BREF est AVANT le séparateur ; le détail est après.
    assert d.index("EN BREF") < d.index("──────────")
    # Verdict ⚡ toujours présent dans l'EN BREF.
    assert "⚡" in d and d.index("⚡") < d.index("──────────")
    # Commandes personnalisées en pied, jamais l'ancien pied de page.
    assert "/pourquoi" in d
    assert "Réponds ici pour creuser" not in d
    assert "dans le mail" not in d.lower()


@pytest.mark.parametrize("kind,builder", [
    ("morning", _morning), ("evening", _evening), ("weekly", _weekly)])
def test_markdown_balanced_and_clean(kind, builder):
    d = notify._build_digest(builder(), kind)
    assert d.count("*") % 2 == 0, "gras Markdown déséquilibré"
    assert d.count("_") % 2 == 0, "italique Markdown déséquilibré"
    assert "None" not in d
    assert "{" not in d and "}" not in d
    assert "··" not in d and " ·  " not in d           # séparateurs vides
    assert "**" not in d                                # md IA nettoyé
    assert len(d) < 4096                                # 1 seul message Telegram


@pytest.mark.parametrize("kind,builder", [
    ("morning", _morning), ("evening", _evening), ("weekly", _weekly)])
def test_regime_never_naked(kind, builder):
    """Décision Omar 12/07 : le régime n'apparaît JAMAIS sans argument chiffré."""
    d = notify._build_digest(builder(), kind)
    assert "Crypto baissière" in d
    assert "vs MM200" in d                              # l'argument déterministe
    assert "F&G 2" in d                                 # le sentiment chiffré
    # L'ancien libellé nu a disparu.
    assert "Fond BAISSIER" not in d and "Fond baissier" not in d


# --------------------------------------------------------------------------- #
# Morning
# --------------------------------------------------------------------------- #
def test_morning_verdict_nothing_distinct_from_action_block():
    d = notify._build_digest(_morning(), "morning")
    # ⚡ verdict sec ; 🎯 = le pourquoi, formulé AUTREMENT (pas de copié-collé).
    assert "⚡ *Aucun achat aujourd'hui.*" in d
    assert "🎯 *Action du jour*" in d
    assert "Rien à exécuter ce matin" in d
    assert d.count("Aucun achat aujourd'hui") == 1
    assert "Pas de nouvelle entrée." not in d           # ancien libellé retiré


def test_morning_verdict_adapts_to_action():
    p = _morning()
    p["top_action"] = {"asset": "ETH", "action": "RENFORCER",
                       "line": "RENFORCER ETH · +20 $ · R:R 1.6"}
    d = notify._build_digest(p, "morning")
    assert "⚡ *RENFORCER ETH · +20 $ · R:R 1.6*" in d
    assert "→ RENFORCER ETH · +20 $ · R:R 1.6" in d     # relayé dans 🎯
    assert "Rien à exécuter" not in d


def test_morning_gate_and_ct_warning_rendered():
    d = notify._build_digest(_morning(), "morning")
    assert "*TAO* — on garde : plafond de concentration atteint" in d
    assert "*ETH* (conv. 70%) — on accumule (DCA)" in d


def test_morning_market_narrative_reuses_ai_synthesis():
    d = notify._build_digest(_morning(), "morning")
    assert "🌍 *Le marché*" in d
    assert "Le dollar reste ferme" in d                # texte IA réutilisé


def test_morning_theses_evolution_full_card():
    """Format validé : ticker (conv.) · émise JJ/MM / entrée → actuel · cible /
    → lecture courte. La conviction AFFICHE SON ÉVOLUTION quand elle a changé."""
    d = notify._build_digest(_morning(), "morning")
    assert "📊 *Évolution des thèses énoncées*" in d
    assert "*INJ* (conv. 62% → 70%) · émise 05/07" in d
    assert "4,54 $ → 4,75 $ (+4,4%) · cible 4,96 $" in d
    assert "→ 🟢 En bonne voie — 48% du chemin vers la cible." in d
    # Anciens libellés retirés.
    assert "Tes positions" not in d


def test_morning_conviction_stable_no_arrow():
    p = _morning()
    p["active_recommendations_tracking"][0]["prev_confidence"] = None
    d = notify._build_digest(p, "morning")
    assert "*INJ* (conv. 70%) · émise 05/07" in d
    assert "62%" not in d


def test_morning_enbref_no_positions_health_phrase():
    """Audit 10/07 : « certaines positions sous pression » contredisait des
    lignes toutes vertes — la phrase de santé a été retirée de l'EN BREF."""
    d = notify._build_digest(_morning(), "morning")
    assert "positions sous pression" not in d
    assert "positions avancent" not in d
    assert "💼 2 728 $ (+0,3% / 24h)" in d


def test_morning_watch_event_explained():
    """Le calendrier est EXPLIQUÉ (pourquoi l'événement compte), et la cote
    Polymarket est étiquetée Fed."""
    d = notify._build_digest(_morning(), "morning")
    assert "⚠️ *À surveiller*" in d
    assert "CPI US (mardi) — chaud → baisses de taux repoussées" in d
    assert "Fed : ~84% maintien attendu (Polymarket)" in d


def test_polymarket_never_attached_to_non_fed_event():
    """Audit 10/07 : « ~80% maintien (Polymarket) » était accolé à un discours
    BOE. Désormais : événement non-Fed → explication devises, pas de cote."""
    p = _morning()
    p["macro_agenda"]["events"] = [
        {"importance": "high", "label": "BOE Gov Bailey Speaks (UK)",
         "when": "dans 2j"}]
    d = notify._build_digest(p, "morning")
    watch = d.split("⚠️")[1]
    assert "impact via les devises" in watch
    assert "Polymarket" not in watch.split("\n\n")[0]


def test_regime_change_is_headline():
    p = _morning()
    p["market_regime"].update({"changed": True, "previous": "range",
                               "previous_label_fr": "RANGE"})
    d = notify._build_digest(p, "morning")
    assert "Crypto passe en BAISSIER (était range)" in d


def test_commands_line_is_personalized():
    d = notify._build_digest(_morning(), "morning")
    # 1er actif = 1re thèse (TAO), 2e = suivant (ETH) — jamais figé.
    assert d.rstrip().endswith("_/pourquoi TAO · /analyse ETH_")


# --------------------------------------------------------------------------- #
# Evening — thèses affichées SEULEMENT si elles ont bougé
# --------------------------------------------------------------------------- #
def test_evening_calm_no_thesis_rows():
    d = notify._build_digest(_evening(), "evening")
    assert "⚡ *Aucune thèse touchée aujourd'hui.*" in d
    assert "🎯 *Action ce soir*" in d
    assert "Rien à faire : aucun stop ni cible touché (1 thèse suivie)" in d
    # La ligne détaillée ETH (stable) n'est PAS rendue le soir.
    assert "1 777 $ → 1 768 $" not in d


def test_evening_stop_hit_renders_action():
    p = _evening()
    p["reco_bilan"][0].update({"status": "invalidated", "current": 1600.0,
                               "delta_pct": -9.97, "reason": "stop $1,621 franchi"})
    d = notify._build_digest(p, "evening")
    assert "⚡ *ETH : stop franchi, thèse invalidée.*" in d
    assert "🔴 stop $1,621 franchi : thèse invalidée, on ne renforce plus." in d


def test_evening_target_hit_renders_action():
    p = _evening()
    p["reco_bilan"][0].update({"status": "on_track", "current": 1960.0,
                               "delta_pct": 10.3})
    d = notify._build_digest(p, "evening")
    assert "⚡ *ETH : cible touchée.*" in d
    assert "✅ cible 1 950 $ touchée : prise de profit partielle à envisager." in d


def test_evening_pressure_renders_action():
    p = _evening()
    p["reco_bilan"][0].update({"status": "under_pressure", "current": 1730.0,
                               "delta_pct": -2.65,
                               "reason": "repli sous l'entrée · invalidation $1,621"})
    d = notify._build_digest(p, "evening")
    assert "⚡ *ETH : passe sous pression.*" in d
    assert "⚠️ sous pression (−2,65% vs entrée)" in d


def test_evening_us_closed_note_once():
    d = notify._build_digest(_evening(), "evening")
    # « marchés US inchangés » dans l'EN BREF, PAS répété dans le 🌍.
    assert d.count("marchés US inchangés") == 1


def test_evening_movers_threshold_and_pnl_usd():
    d = notify._build_digest(_evening(), "evening")
    assert "P&L +0,11% (+3 $)" in d                    # dollar entier
    assert "TAO +12,3%" in d                           # ≥ ±10% affiché
    assert "HBAR" not in d.split("Gros mouvements")[1].split("\n")[0]  # −4,5% exclu


def test_evening_level_in_enbref_no_watch_doublon():
    d = notify._build_digest(_evening(), "evening")
    assert "👁 Niveau clé cette nuit : *60 862 $*" in d
    assert "⚠️ *Cette nuit / demain*" in d
    assert "Sous 60 862 $ → appui 58 551 $." in d      # mécanique du trigger
    # Le niveau n'est PAS répété en préfixe (« Niveau clé X — Sous X »).
    assert d.count("60 862 $") == 2                    # EN BREF + trigger


def test_evening_commands_exclude_hidden_assets():
    """Audit 10/07 : « /analyse ANKR » alors qu'ANKR n'apparaissait nulle part.
    Les commandes ne citent QUE des actifs affichés (bilan + movers ≥10%)."""
    d = notify._build_digest(_evening(), "evening")
    assert d.rstrip().endswith("_/pourquoi ETH · /analyse TAO_")
    assert "HBAR" not in d                             # <10% : ni affiché ni cité


# --------------------------------------------------------------------------- #
# Weekly
# --------------------------------------------------------------------------- #
def test_weekly_verdict_is_the_plan_action():
    d = notify._build_digest(_weekly(), "weekly")
    assert "⚡ *Si BTC casse 60 862 $ après les minutes → renforcer le cœur BTC" in d


def test_weekly_action_block_conditional_wording():
    d = notify._build_digest(_weekly(), "weekly")
    assert "🎯 *Action de la semaine*" in d
    assert "Une seule, conditionnelle : si BTC casse 60 862 $" in d
    assert "Sinon, on ne bouge pas." in d
    assert "Scénario dominant : NEUTRE (50%)." in d
    assert "Bascule : clôture BTC > 66,1k $ = haussier" in d


def test_weekly_theses_evolution_from_tracker():
    d = notify._build_digest(_weekly(), "weekly")
    assert "📊 *Évolution des thèses énoncées*" in d
    assert "*ETH* (conv. 72% → 78%) · émise 28/06" in d
    assert "1 980 $ → 1 850 $ (−6,6%) · cible 6-12m 3 746 $–4 946 $" in d
    assert "→ Accumulation — MVRV 0,89 : capitulation historique." in d
    # RSR : cible du positions_review (zéros superflus retirés).
    assert "cible cycle 0,073 $–0,117 $" in d
    # Ancien bloc retiré.
    assert "Tes positions à suivre" not in d


def test_weekly_theses_fallback_positions_review():
    p = _weekly()
    del p["predictions_scoring"]
    d = notify._build_digest(p, "weekly")
    assert "📌 *Positions clés*" in d
    assert "*ETH*" in d and "vs PRU" in d


def test_weekly_enbref_line3_and_retro():
    d = notify._build_digest(_weekly(), "weekly")
    assert "💼 2 716 $ · +6,1% / 7j · vs BTC +0,0%" in d
    assert "↩️ *La semaine passée*" in d
    assert "Neutre annoncé (45%) → conforme." in d


def test_weekly_market_reuses_summary_bullets():
    d = notify._build_digest(_weekly(), "weekly")
    assert "🌍 *La tendance*" in d
    assert "fond baissier intact" in d                 # bullets joints, md nettoyé


def test_weekly_calendar_two_events_explained():
    d = notify._build_digest(_weekly(), "weekly")
    assert "📅 *La semaine à venir*" in d
    cal = d.split("📅")[1]
    assert "CPI US (mardi) — chaud →" in cal
    assert "BOE Gov Bailey Speaks (UK) (jeudi) — impact via les devises" in cal
    # Polymarket sur la ligne CPI (Fed-related), PAS sur la ligne BOE.
    cpi_line = [ln for ln in cal.split("\n") if "CPI" in ln][0]
    boe_line = [ln for ln in cal.split("\n") if "BOE" in ln][0]
    assert "Polymarket" in cpi_line
    assert "Polymarket" not in boe_line


def test_weekly_event_from_upcoming_calendar_facts_source():
    """already_published exclu, quelle que soit la source calendrier."""
    p = _weekly()
    del p["week_ahead"]
    p["upcoming_calendar_facts"] = {"available": True, "events": [
        {"label": "NFP", "when": "aujourd'hui", "already_published": True},
        {"label": "Minutes de la Fed", "when": "demain"}]}
    d = notify._build_digest(p, "weekly")
    assert "Minutes de la Fed (demain)" in d
    assert "NFP" not in d                               # déjà publié → exclu


# --------------------------------------------------------------------------- #
# Robustesse — ne crashe jamais, sections conditionnelles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["morning", "evening", "weekly"])
@pytest.mark.parametrize("payload", [
    {}, None, {"header": {}}, {"header": None},
    {"thesis_of_the_day": [None, "x", {}]},
    {"daily_pnl": {}, "reco_bilan": [None]},
    {"scenarios": [None], "positions_review": [None]},
    {"active_recommendations_tracking": [None]},
    {"predictions_scoring": {"detail": [None, {}]}},
    {"portfolio_heatmap": {"cells": [None, {}]}},
    {"market_regime": {"available": True}},
    {"levels_tonight": [None, {}]},
])
def test_never_crashes_on_partial_payload(kind, payload):
    d = notify._build_digest(payload, kind)
    assert isinstance(d, str) and d
    assert d.count("*") % 2 == 0


def test_empty_payload_still_has_header_and_commands():
    d = notify._build_digest({}, "morning")
    assert "☀️ MATIN" in d
    assert "📌 EN BREF" in d
    assert d.rstrip().endswith("_/pourquoi BTC · /analyse ETH_")   # repli générique


def test_push_not_configured_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.push_report_notification(_morning(), "morning") is False


def test_push_sends_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    sent = {}

    def _fake_send(text, **k):
        sent["text"] = text
        return True

    monkeypatch.setattr(notify.telegram_api, "send_message", _fake_send)
    assert notify.push_report_notification(_weekly(), "weekly") is True
    assert "HEBDO" in sent["text"] and "📌 EN BREF" in sent["text"]


# --------------------------------------------------------------------------- #
# Helpers de formatage
# --------------------------------------------------------------------------- #
def test_fmt_usd_fr():
    assert notify._fmt_usd(2728) == "2 728 $"
    assert notify._fmt_usd(340.0) == "340 $"            # v29 : pas de « 340,00 $ »
    assert notify._fmt_usd(4.54) == "4,54 $"
    assert notify._fmt_usd(0.073) == "0,073 $"          # pas 0,0730
    assert notify._fmt_usd(0.0012) == "0,0012 $"
    assert notify._fmt_usd(None) is None


def test_pct_fr_signed():
    assert notify._pct(0.3) == "+0,3%"
    assert notify._pct(-0.53, 2) == "−0,53%"
    assert notify._pct(None) is None


def test_int_usd_whole_dollars():
    assert notify._int_usd(3) == "3 $"
    assert notify._int_usd(1234.6) == "1 235 $"


def test_plain_strips_markdown():
    assert notify._plain("**BTC** _fort_ `x`") == "BTC fort x"


def test_clip_never_cuts_mid_number():
    """Audit 10/07 : les paragraphes étaient tronqués en plein chiffre
    (« CPI 4.3%… ») — la coupe se fait désormais en fin de phrase."""
    txt = ("Première phrase complète avec du contexte utile. Deuxième phrase "
           "qui contient le chiffre CPI 4,3% et qui serait coupée en plein "
           "milieu par l'ancien clip car elle est trop longue pour la limite.")
    out = notify._clip(txt, 90)
    assert out == "Première phrase complète avec du contexte utile."
    assert "…" not in out


def test_conv_note_evolution():
    assert notify._conv_note(78, 72) == "conv. 72% → 78%"
    assert notify._conv_note(78, None) == "conv. 78%"
    assert notify._conv_note(78, 78) == "conv. 78%"
    assert notify._conv_note(None, 72) is None


def test_event_explainer_mapping():
    assert "baisses de taux" in notify._event_explainer("CPI US")
    assert "taux" in notify._event_explainer("FOMC Rate Decision")
    assert "emploi" in notify._event_explainer("NFP (US)")
    assert "devises" in notify._event_explainer("BOE Gov Bailey Speaks")
    assert notify._event_explainer("Random Event") is None


def test_regime_adj_from_label_fallback():
    assert notify._regime_adj({"regime": "bear"}) == "baissière"
    assert notify._regime_adj({"label_fr": "haussier"}) == "haussière"
    assert notify._regime_adj({"label_fr": "range"}) == "en range"
    assert notify._regime_adj({}) is None


# --------------------------------------------------------------------------- #
# État — évolution de conviction persistée à la ré-émission
# --------------------------------------------------------------------------- #
def test_reissue_records_prev_confidence(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)

    base = {"id": "ETH-2026-07-01-RENFORCER", "asset": "ETH",
            "action": "RENFORCER", "confidence": 72, "entry_price": 1980.0,
            "created_at": "2026-07-01T08:00:00+00:00", "status": "in_progress"}
    mem.add_recommendation(dict(base))
    # Ré-émission avec conviction REVUE À LA HAUSSE → prev_confidence gardée.
    mem.add_recommendation({**base, "confidence": 78})
    r = mem.load_active_recommendations()[0]
    assert r["confidence"] == 78
    assert r["prev_confidence"] == 72
    # Ré-émission SANS changement → prev_confidence (dernier changement) intacte.
    mem.add_recommendation({**base, "confidence": 78})
    r = mem.load_active_recommendations()[0]
    assert r["prev_confidence"] == 72


def test_active_for_display_exposes_confidence(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    from src.tracking import prediction_scoring as ps
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.save_active_recommendations([
        {"id": "ETH-2026-07-01-RENFORCER", "asset": "ETH", "action": "RENFORCER",
         "confidence": 78, "prev_confidence": 72, "entry_price": 1980.0,
         "ct_target": 2178.0, "created_at": "2026-07-01T08:00:00+00:00",
         "status": "in_progress"}])
    rows = ps.PredictionTracker().active_for_display({"ETH": 1850.0})
    assert rows[0]["confidence"] == 78
    assert rows[0]["prev_confidence"] == 72


def test_scoring_detail_exposes_confidence_and_target(tmp_path, monkeypatch):
    from src.state import report_memory as mem
    from src.tracking import prediction_scoring as ps
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    mem.save_active_recommendations([
        {"id": "ETH-2026-07-01-RENFORCER", "asset": "ETH", "action": "RENFORCER",
         "confidence": 78, "prev_confidence": 72, "entry_price": 1980.0,
         "ct_target": 2178.0, "created_at": "2026-07-01T08:00:00+00:00",
         "status": "in_progress"}])
    detail = ps.PredictionTracker().build_scoring_detail({"ETH": 1850.0}, 7)
    row = [r for r in detail if r["asset"] == "ETH"][0]
    assert row["confidence"] == 78
    assert row["prev_confidence"] == 72
    assert row["ct_target"] == 2178.0
