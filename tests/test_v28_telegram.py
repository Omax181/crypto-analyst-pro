"""v28 (TG-refonte) — digest Telegram à VALEUR (📌 EN BREF + détail).

Couvre la refonte de ``src/telegram_bot/notify.py`` : structure en 2 zones,
verdict adaptatif (action vs rien), réutilisation du narratif IA, sections
propres au type, « à surveiller » relié au portefeuille, commandes
personnalisées, formatage FR et robustesse (payloads vides/partiels, jamais de
crash, Markdown équilibré).
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
                          "label_fr": "baissier", "days_in_regime": 1,
                          "reasons": ["prix sous MM200"]},
        "macro_regime_readout": {"regime": "transition", "confidence_pct": 70,
                                 "crypto_bias": "neutre"},
        "macro_context": {
            "regime_synthesis": "Le dollar reste ferme et les actions US hésitent "
                                "avant la Fed : contexte indécis. La peur domine.",
            "fear_greed": 27, "fear_greed_label": "peur",
            "polymarket_fed_bars": {"dominant": "maintien", "dominant_pct": 84.5}},
        "macro_agenda": {"available": True, "events": [
            {"importance": "high", "label": "Minutes de la Fed", "when": "ce soir"}]},
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
            {"asset": "INJ", "entry_price": 4.54, "current_price": 4.75,
             "progress_pct": 4.4, "ct_target": 4.96,
             "health_status": "🟢 En bonne voie",
             "comment": "40% du chemin vers la cible."}],
        "portfolio_heatmap": {"cells": [{"symbol": "BTC", "ptf_pct": 42.0}]},
    }


def _evening() -> dict:
    return {
        "header": {"time_casablanca": "mardi 7 juillet · 20:02",
                   "us_market_open": False},
        "market_regime": {"available": True, "label_fr": "baissier",
                          "days_in_regime": 1},
        "daily_pnl": {"value_usd": 2716, "day_change_usd": 3, "day_change_pct": 0.11,
                      "top_movers": [{"symbol": "HBAR", "change": -4.5},
                                     {"symbol": "TAO", "change": 12.3}]},
        "reco_bilan": [{"asset": "ETH", "action": "RENFORCER", "confidence": 70,
                        "entry": 1777.16, "current": 1767.81, "delta_pct": -0.53,
                        "status": "stable",
                        "reason": "proche de l'entrée (bruit court terme) · "
                                  "invalidation $1,621"}],
        "levels_tonight": [{"asset": "BTC", "level": "60 862 $", "type": "support",
                            "trigger": "Sous 60 862 $ → appui 58 551 $."}],
        "since_morning_facts": "BTC est resté stable autour de 62 900 $ depuis ce matin.",
        "polymarket_facts": {"fed_bars": {"dominant": "maintien",
                                          "dominant_pct": 84.5}},
        "portfolio_heatmap": {"cells": [{"symbol": "BTC", "ptf_pct": 42.0}]},
    }


def _weekly() -> dict:
    return {
        "header": {"time_casablanca": "mardi 7 juillet · 09:48"},
        "market_regime": {"available": True, "label_fr": "baissier",
                          "days_in_regime": 1},
        "portfolio_snapshot": {"value_usd": 2716, "weekly_pnl_pct": 6.06,
                               "vs_btc_7d_pct": 0.04},
        "weekly_summary": [
            "**BTC +2,2%** sur la semaine sans repasser au-dessus des moyennes "
            "longues → **fond baissier** intact.",
            "**F&G 24 → 27** : la peur recule mais reste dominante."],
        "scenarios": [{"label": "neutre", "probability_pct": 50}],
        "weekly_action_plan": [{"action": "Si BTC casse 60 862 $ après les "
                                "minutes → renforcer le cœur BTC de +3% du PTF"}],
        "calls_review": {"summary_line": "Neutre annoncé (45%) → conforme."},
        "positions_review": [
            {"asset": "RSR", "conviction": False, "current_price": 0.0012,
             "pru_pct": -81.4,
             "h30": {"reco": "RENFORCER", "status": "in_progress", "delta_pct": 1.0},
             "lt_status": "capitulation", "lt_target_low": 0.073,
             "lt_target_high": 0.117, "lt_target_kind": "cycle",
             "analysis": "rebond technique amorcé", "action": "renforcer"},
            {"asset": "TAO", "conviction": True, "pru_pct": 5.0,
             "lt_status": "expansion", "analysis": "solide", "action": "garder"}],
        "macro_agenda": {"available": True, "events": [
            {"importance": "high", "label": "Minutes de la Fed", "when": "mercredi"}]},
        "macro_context": {"polymarket_fed_bars": {"dominant": "maintien",
                                                  "dominant_pct": 84.5}},
        "portfolio_heatmap_7d": {"cells": [{"symbol": "BTC", "ptf_pct": 42.0}]},
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


# --------------------------------------------------------------------------- #
# Morning
# --------------------------------------------------------------------------- #
def test_morning_verdict_nothing_to_do():
    d = notify._build_digest(_morning(), "morning")
    assert "⚡ *Rien à faire aujourd'hui*" in d
    assert "🎯 *Ce qu'on fait*" in d
    assert "Pas de nouvelle entrée." in d


def test_morning_verdict_adapts_to_action():
    p = _morning()
    p["top_action"] = {"asset": "ETH", "action": "RENFORCER",
                       "line": "RENFORCER ETH · +20 $ · R:R 1.6"}
    d = notify._build_digest(p, "morning")
    assert "⚡ *RENFORCER ETH · +20 $ · R:R 1.6*" in d
    assert "Pas de nouvelle entrée." not in d          # plus le mode « rien »


def test_morning_gate_and_ct_warning_rendered():
    d = notify._build_digest(_morning(), "morning")
    assert "*TAO* — on garde : plafond de concentration atteint" in d
    assert "*ETH* (conf. 70%) — on accumule (DCA)" in d


def test_morning_market_narrative_reuses_ai_synthesis():
    d = notify._build_digest(_morning(), "morning")
    assert "🌍 *Le marché*" in d
    assert "Le dollar reste ferme" in d                # texte IA réutilisé


def test_morning_tracking_and_positions_health():
    d = notify._build_digest(_morning(), "morning")
    assert "📈 *Tes positions*" in d
    assert "*INJ* — 4,54 $ → 4,75 $ (+4,4%) 🟢 En bonne voie" in d
    assert "tes positions avancent bien" in d


def test_morning_watch_is_personal_to_book():
    d = notify._build_digest(_morning(), "morning")
    assert "⚠️ *À surveiller*" in d
    assert "Polymarket" in d
    assert "Book concentré (BTC pèse ~42% du portefeuille)" in d


def test_commands_line_is_personalized():
    d = notify._build_digest(_morning(), "morning")
    # 1er actif = 1re thèse (TAO), 2e = suivant (ETH) — jamais figé.
    assert d.rstrip().endswith("_/pourquoi TAO · /analyse ETH_")


# --------------------------------------------------------------------------- #
# Evening
# --------------------------------------------------------------------------- #
def test_evening_verdict_qualifiers():
    base = _evening()
    assert "Journée blanche (+0,11%)" in notify._build_digest(base, "evening")

    up = _evening(); up["daily_pnl"]["day_change_pct"] = 1.8
    up["daily_pnl"]["day_change_usd"] = 48
    assert "Journée positive (+1,80%)" in notify._build_digest(up, "evening")

    dn = _evening(); dn["daily_pnl"]["day_change_pct"] = -2.3
    dn["daily_pnl"]["day_change_usd"] = -60
    assert "Journée sous pression (−2,30%)" in notify._build_digest(dn, "evening")


def test_evening_us_closed_note_once():
    d = notify._build_digest(_evening(), "evening")
    # « marchés US inchangés » dans l'EN BREF, PAS répété dans le 🌍.
    assert d.count("marchés US inchangés") == 1
    assert "📊 Fond baissier, marchés US inchangés" in d


def test_evening_movers_threshold_and_pnl_usd():
    d = notify._build_digest(_evening(), "evening")
    assert "P&L +0,11% (+3 $)" in d                    # dollar entier
    assert "TAO +12,3%" in d                           # ≥ ±10% affiché
    assert "HBAR" not in d.split("Gros mouvements")[1].split("\n")[0]  # −4,5% exclu


def test_evening_reco_bilan_status_mapping():
    d = notify._build_digest(_evening(), "evening")
    assert "*ETH* (conf. 70%) — 1 777 $ → 1 768 $ (−0,53%) ● stable" in d


def test_evening_level_in_enbref():
    d = notify._build_digest(_evening(), "evening")
    assert "👁 Niveau clé : *60 862 $*" in d
    assert "⚠️ *Cette nuit / demain*" in d


# --------------------------------------------------------------------------- #
# Weekly
# --------------------------------------------------------------------------- #
def test_weekly_verdict_vs_btc_relation():
    d = notify._build_digest(_weekly(), "weekly")
    assert "Semaine +6,1%, en ligne avec BTC" in d     # |vs BTC| < 0,5

    better = _weekly(); better["portfolio_snapshot"]["vs_btc_7d_pct"] = 3.0
    assert "mieux que BTC" in notify._build_digest(better, "weekly")

    worse = _weekly(); worse["portfolio_snapshot"]["vs_btc_7d_pct"] = -2.0
    assert "moins bien que BTC" in notify._build_digest(worse, "weekly")


def test_weekly_plan_and_scenario():
    d = notify._build_digest(_weekly(), "weekly")
    assert "🎯 *Le plan* — scénario NEUTRE (50%)" in d
    assert "renforcer le cœur BTC de +3% du PTF" in d
    assert "Semaine passée : Neutre annoncé (45%) → conforme." in d


def test_weekly_positions_target_formatting():
    d = notify._build_digest(_weekly(), "weekly")
    assert "📌 *Tes positions à suivre*" in d
    assert "*RSR* — −81,4% vs PRU, capitulation" in d
    assert "cible cycle 0,073 $–0,117 $" in d          # zéros superflus retirés


def test_weekly_market_reuses_summary_bullets():
    d = notify._build_digest(_weekly(), "weekly")
    assert "🌍 *La semaine*" in d
    assert "fond baissier intact" in d                 # bullets joints, md nettoyé


def test_weekly_event_from_week_ahead_source():
    """Le hebdo expose son calendrier via week_ahead / upcoming_calendar_facts
    (pas macro_agenda) : la ligne « semaine à venir » doit quand même le citer,
    et un événement DÉJÀ TOMBÉ est ignoré."""
    p = _weekly()
    del p["macro_agenda"]
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
    {"portfolio_heatmap": {"cells": [None, {}]}},
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
