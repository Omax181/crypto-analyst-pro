# -*- coding: utf-8 -*-
"""V31 — intégration de bout en bout (SPEC §2.1, §5, §11).

Ces tests exercent le CHEMIN RÉEL : collecte enveloppée -> FactStore -> carnet
-> plan -> viabilité -> contenu -> rendu -> envoi -> persistance. Ils vérifient
les interactions, pas seulement les unités.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from src.core import params, source_result as sr
from src.core.volatility import DailyBar

UTC = timezone.utc

FULL_PARAMS = {
    "fee_rate": 0.001,
    "liquidity_bands": [{"min_daily_volume_usd": 0, "spread_pct": 0.02,
                         "slippage_pct": 0.02}],
    "delta_claimable": 0.05,
    "p_target_max": 0.30,
    "p_stop_max": 0.20,
    "materiality_reference": "monthly_budget",
    "k3": 0.01,
    "monthly_budget": 500.0,
    "ticket_min": 40.0,
    "n_min": 3,
}

# Portefeuille de test dimensionné pour que le satellite dispose d'une marge
# de concentration ET d'un budget suffisants : sans cela, aucun geste ne peut
# franchir la matérialité (V3), et l'on ne testerait que le chemin du rejet.
PORTFOLIO = {
    "meta": {},
    "portfolio": {
        # PRU sous le cours : pas de signal « position sous le prix de revient »,
        # donc pas de poids FONDAMENTAL — sans quoi l'horizon bascule en
        # POSITION (désactivé) et plus rien ne peut être émis.
        "RENDER": {"symbol": "RENDER", "quantity": 5.0, "value_usd": 450.0,
                   "pru": 60.0, "tier": 1},
        "BTC": {"quantity": 0.15, "value_usd": 9000.0, "pru": 40000.0,
                "tier": 1},
    },
}


def _oversold_series() -> list[float]:
    """Canal large (76-124) terminé par un glissement vers 90.

    Le repli produit un RSI de survente — signal de STRUCTURE TECHNIQUE, seul
    chemin vers l'horizon SWING : un actif sans signal reste en POSITION, donc
    désactivé. Le prix final reste AU-DESSUS du plancher du canal, sinon aucune
    invalidation n'existe sous l'entrée et V2 rejette.
    """
    base = [100.0 + 24.0 * math.sin(i / 11.0) for i in range(87)]
    start, end, steps = base[-1], 90.0, 25
    return base + [start + (end - start) * (k + 1) / steps
                   for k in range(steps)]


def _bars(n: int = 40, base: float = 100.0) -> list[DailyBar]:
    """Amplitude journalière ~2,4 % -> sigma_jour ~1,4 %, sigma_30 ~7,9 %."""
    return [DailyBar(f"d{i}", base, base * 1.012, base * 0.988, base)
            for i in range(n)]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isole TOUT l'état sur disque et neutralise le réseau."""
    from src.core import book as book_mod, registry, runlog
    from src.state import bot_memory
    monkeypatch.setattr(book_mod, "_STATE_DIR", tmp_path / "book")
    monkeypatch.setattr(runlog, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(registry, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bot_memory, "_STATE_DIR", tmp_path / "state")
    params.reset_cache()
    params._cache = dict(FULL_PARAMS)
    yield tmp_path
    params.reset_cache()


@pytest.fixture
def wired(isolated, monkeypatch):
    """Câble un run complet avec des sources contrôlées."""
    from src.ai_brain import llm as llm_mod
    from src.pipeline import context as context_mod
    from src.reporting import email_sender
    import src.main as main_mod

    monkeypatch.setattr(context_mod, "load_portfolio", lambda: PORTFOLIO)

    series = _oversold_series()
    quotes = {
        "RENDER": {"price": series[-1], "volume_24h": 5e8,
                   "change_24h": -6.0, "change_7d": -9.0,
                   "change_from_ath_pct": -40.0},
        "BTC": {"price": 60000.0, "volume_24h": 3e10, "change_24h": 0.4,
                "change_7d": 1.2, "change_from_ath_pct": -18.0},
        "ETH": {"price": 3000.0, "volume_24h": 1e10, "change_24h": 0.2},
    }

    def _collect_all(symbols, *, full):
        spot = sr.ok("market.spot", quotes, depth=len(quotes))
        closes = {s: sr.ok("market.closes", {"closes": series, "volumes": []},
                           depth=len(series)) for s in symbols}
        bars = ({s: sr.ok("market.ohlc", _bars(), depth=40) for s in symbols}
                if full else {})
        sources = {
            "fear_greed": sr.ok("fear_greed",
                                {"value": 18, "classification": "Extreme Fear"},
                                as_of=datetime.now(UTC)),
            "news": sr.ok("news", [{"title": "ETF spot : collecte nette",
                                    "source": "CoinDesk",
                                    "published_label": "il y a 2 h"}]),
            "equities": sr.empty("equities"),
        }
        return sources, spot, closes, bars

    monkeypatch.setattr(context_mod, "collect_all", _collect_all)

    sent: list[dict] = []

    def _send(subject, html, inline_images=None):
        sent.append({"subject": subject, "html": html,
                     "images": inline_images or {}})
        return True

    monkeypatch.setattr(main_mod, "send_email", _send)
    monkeypatch.setattr(email_sender, "send_email", _send)
    monkeypatch.setattr(main_mod, "_notify", lambda payload, kind: None)

    authored = {
        "macro_reading": "Le marché reste sous pression, sans capitulation nette.",
        "macro_implication": "Rien n'impose d'agir dans l'urgence.",
        "counter_thesis": "Un rebond général invaliderait cette lecture.",
        "self_critique": "La couverture on-chain reste incomplète.",
        "evening_reading": "La séance n'a rien changé de structurel.",
        "weekly_lesson": "Patienter reste une décision.",
        "tracking_note": "Les contrats suivent leur cours.",
    }
    monkeypatch.setattr(
        llm_mod.LLMSession, "compose",
        lambda self, kind, **kw: dict(authored))
    return sent


# ══════════════════════════════════════════════════════════════════════════
# Chaîne complète
# ══════════════════════════════════════════════════════════════════════════

def test_morning_runs_end_to_end_and_sends(wired):
    import src.main as main_mod
    assert main_mod.run_morning() == 0
    assert len(wired) == 1
    html = wired[0]["html"]
    assert "Rapport du matin" in html
    assert "Crypto Analyst Pro v31" in html


def test_the_three_runs_all_complete(wired, isolated):
    import src.main as main_mod
    assert main_mod.run_morning() == 0
    assert main_mod.run_evening() == 0
    assert main_mod.run_weekly() == 0
    assert [s["subject"].split(" · ")[0] for s in wired] == [
        "Rapport du matin", "Point du soir", "Bilan de la semaine"]


def test_I46_evening_and_weekly_leave_the_book_byte_identical(wired, isolated):
    import src.main as main_mod
    main_mod.run_morning()
    path = isolated / "book" / "contracts.json"
    before = path.read_bytes() if path.exists() else b""
    main_mod.run_evening()
    main_mod.run_weekly()
    after = path.read_bytes() if path.exists() else b""
    assert after == before


def test_I53_a_failed_send_persists_nothing(wired, isolated, monkeypatch):
    import src.main as main_mod
    monkeypatch.setattr(main_mod, "send_email",
                        lambda *a, **k: False)
    assert main_mod.run_morning() == 1
    assert not (isolated / "book" / "contracts.json").exists()


def test_a_crash_mid_run_persists_nothing(wired, isolated, monkeypatch):
    import src.main as main_mod
    from src.reporting import render
    monkeypatch.setattr(render, "render",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("gabarit cassé")))
    assert main_mod.run_morning() == 1
    assert not (isolated / "book" / "contracts.json").exists()


def test_report_snapshot_is_written_only_after_a_successful_send(wired,
                                                                 isolated):
    import src.main as main_mod
    from src.state import bot_memory
    assert bot_memory.load_report_snapshot("morning") == {}
    main_mod.run_morning()
    snap = bot_memory.load_report_snapshot("morning")
    assert snap and snap["title"] == "Rapport du matin"


# ══════════════════════════════════════════════════════════════════════════
# Émission réelle et invariants économiques sur le chemin complet
# ══════════════════════════════════════════════════════════════════════════

def test_a_satellite_with_a_technical_signal_reaches_an_emission(wired,
                                                                  isolated):
    """Vérifie que la chaîne signaux -> horizon -> plan -> viabilité aboutit."""
    import src.main as main_mod
    main_mod.run_morning()
    from src.core.book import RecommendationBook
    book = RecommendationBook(run_kind="bot", run_id="t",
                              state_dir=isolated / "book")
    assets = {r.asset for r in book.active()}
    assert "RENDER" in assets, "le satellite survendu doit produire un contrat"
    assert "BTC" not in assets, "un actif cœur ne peut pas émettre en V31"


def test_a_core_asset_rejection_is_quantified_never_categorical(wired,
                                                                 isolated):
    """BTC n'émet pas ICI faute d'historique — et le mail dit lequel.

    Ce qui est verrouillé n'est pas « le cœur n'émet jamais » (c'était le
    défaut) mais « tout refus est chiffré ». Avec 365 clôtures, BTC émettrait.
    """
    import src.main as main_mod
    main_mod.run_morning()
    html = wired[0]["html"]
    assert "BTC" in html
    assert "365" in html, "le motif doit citer la profondeur exigée"
    assert "évaluable" in html.lower()


def test_emitted_contract_carries_its_full_economics_in_the_mail(wired):
    import src.main as main_mod
    main_mod.run_morning()
    html = wired[0]["html"]
    for token in ("avantage exigé", "point neutre", "espérance nette",
                  "Bruit de l'horizon", "coût aller-retour"):
        assert token in html, token


# ══════════════════════════════════════════════════════════════════════════
# NON_EVALUABLE — aucun paramètre inventé
# ══════════════════════════════════════════════════════════════════════════

def test_without_business_parameters_nothing_is_emitted_and_it_is_said(
        wired, isolated, monkeypatch):
    import src.main as main_mod
    params.reset_cache()
    params._cache = {}
    assert main_mod.run_morning() == 0
    html = wired[0]["html"]
    assert "évaluable" in html.lower()
    assert "fee_rate" in html
    # Aucun contrat ne peut naître sans paramètre métier.
    assert not (isolated / "book" / "contracts.json").exists()


def test_missing_parameters_disable_metrics_rather_than_faking_them(
        wired, isolated, monkeypatch):
    import src.main as main_mod
    params.reset_cache()
    params._cache = {}
    main_mod.run_weekly()
    html = wired[0]["html"]
    assert "non publiée" in html
    # L'apostrophe est échappée par le gabarit : on cible le motif rendu.
    assert "plancher d&#39;échantillon non défini" in html


# ══════════════════════════════════════════════════════════════════════════
# Locale — I60 sur la SORTIE RENDUE
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("runner", ["morning", "evening", "weekly"])
def test_I60_rendered_html_has_no_anglo_numeral(wired, isolated, runner):
    import src.main as main_mod
    from src.core import formatter as fmt
    getattr(main_mod, f"run_{runner}")()
    html = wired[-1]["html"]
    # Les attributs CSS/HTML portent légitimement des points décimaux ; on
    # balaie donc le TEXTE visible, balises retirées.
    import re
    visible = re.sub(r"<[^>]+>", " ", html)
    violations = fmt.find_format_violations(visible)
    assert not violations, violations


def test_I59_subject_uses_the_single_formatter(wired):
    import src.main as main_mod
    from src.core import formatter as fmt
    main_mod.run_morning()
    assert not fmt.find_format_violations(wired[0]["subject"])


# ══════════════════════════════════════════════════════════════════════════
# Contenu — rejet, jamais réparation
# ══════════════════════════════════════════════════════════════════════════

def test_a_raw_numeral_in_an_authored_field_is_rejected_not_repaired(
        wired, isolated, monkeypatch):
    import src.main as main_mod
    from src.ai_brain import llm as llm_mod
    from src.core import content
    monkeypatch.setattr(
        llm_mod.LLMSession, "compose",
        lambda self, kind, **kw: {"macro_reading": "BTC vaut 60000 dollars."})
    main_mod.run_morning()
    html = wired[0]["html"]
    assert "60000" not in html
    assert content.DECL_MACRO in html


def test_llm_unavailable_still_produces_a_complete_mail(wired, isolated,
                                                        monkeypatch):
    import src.main as main_mod
    from src.ai_brain import llm as llm_mod
    monkeypatch.setattr(
        llm_mod.LLMSession, "compose",
        lambda self, kind, **kw: (_ for _ in ()).throw(
            llm_mod.LLMUnavailable("hors service")))
    assert main_mod.run_morning() == 0
    html = wired[0]["html"]
    assert "Le geste du jour" in html
    assert "Portefeuille" in html


# ══════════════════════════════════════════════════════════════════════════
# Telegram — projection, jamais recalcul
# ══════════════════════════════════════════════════════════════════════════

def test_telegram_message_reuses_the_rendered_numbers(wired, isolated):
    import src.main as main_mod
    from src.reporting import render
    from src.telegram_bot import notify
    from src.core import formatter as fmt
    from src.pipeline import runs
    from src.state import bot_memory

    main_mod.run_morning()
    snap = bot_memory.load_report_snapshot("morning")
    payload = {"date_label": snap["date_label"], "banner": snap.get("banner"),
               "top_action": snap.get("top_action"),
               "nothing_to_do": snap.get("nothing_to_do"),
               "transitions": snap.get("transitions") or [],
               "intraday": snap.get("intraday") or [],
               "rejections": [], "metrics": [],
               "book": {"active": [], "active_count": fmt.integer(0)}}
    text = notify.build_message(payload, "morning")
    assert not fmt.find_format_violations(text)
    assert "Rapport du matin" in text
    assert runs.MORNING == "morning" and render.APP_VERSION == "v31"


def test_telegram_never_recomputes_a_number(wired, isolated):
    """Le module de notification n'importe aucun moteur de calcul."""
    import inspect
    from src.telegram_bot import notify
    source = inspect.getsource(notify)
    for forbidden in ("viability", "plan", "levels", "sizing", "volatility"):
        assert f"src.core.{forbidden}" not in source


# ══════════════════════════════════════════════════════════════════════════
# Commandes du bot — une seule écriture autorisée
# ══════════════════════════════════════════════════════════════════════════

def test_dismiss_is_the_only_write_and_produces_cancelled(wired, isolated,
                                                           monkeypatch):
    import src.main as main_mod
    from src.core import book as book_mod
    from src.core.book import RecommendationBook, State
    from src.telegram_bot import commands

    main_mod.run_morning()
    monkeypatch.setattr(book_mod, "_STATE_DIR", isolated / "book")
    answer, changed = commands.handle_state_command("/dismiss RENDER")
    assert changed and "annulé" in answer
    book = RecommendationBook(run_kind="bot", run_id="t",
                              state_dir=isolated / "book")
    states = {r.asset: r.state_value for r in book.all()}
    assert states["RENDER"] is State.CANCELLED


def test_validate_and_snooze_no_longer_exist(wired):
    from src.telegram_bot import commands
    assert "/validate" not in commands.STATE_COMMANDS
    assert "/snooze" not in commands.STATE_COMMANDS
    assert commands.handle_read_command("/validate") is None


def test_read_commands_never_write(wired, isolated):
    import src.main as main_mod
    from src.core import book as book_mod
    from src.telegram_bot import commands
    main_mod.run_morning()
    monkeypatch_path = isolated / "book" / "contracts.json"
    before = monkeypatch_path.read_bytes()
    for cmd in ("/carnet", "/sources", "/resume", "/memoire", "/aide"):
        assert commands.handle_read_command(cmd) is not None
    assert monkeypatch_path.read_bytes() == before
    assert book_mod.SCORING_REGIME == "v31"
