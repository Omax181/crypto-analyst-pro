"""Tests du bot Telegram (Chantier G, Partie 6 de l'audit).

Verrouillent : le filtrage de sécurité par chat_id, le découpage des longs
messages, le routage des commandes (état / lecture / IA), les actions sur le
state (dismiss/validate/snooze), la mémoire conversationnelle (offset +
historique), et l'assemblage du prompt de l'assistant. Aucun appel réseau réel.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import types


def _stub_tenacity() -> None:
    ten = types.ModuleType("tenacity")
    ten.retry = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    ten.stop_after_attempt = ten.wait_exponential = ten.retry_if_exception_type = (
        lambda *a, **k: None
    )
    ten.before_sleep_log = lambda *a, **k: (lambda *aa, **kk: None)
    sys.modules["tenacity"] = ten


def _fresh_state() -> None:
    """Redirige le state vers un dossier temporaire vierge."""
    import src.state.report_memory as mem
    mem._STATE_DIR = pathlib.Path(tempfile.mkdtemp())


# --------------------------------------------------------------------------- #
# Sécurité + transport
# --------------------------------------------------------------------------- #
def test_security_only_owner_chat_served():
    """Seul le chat_id d'Omar est servi ; les autres sont ignorés."""
    from src.telegram_bot import telegram_api

    updates = [
        {"update_id": 10, "message": {"text": "salut", "chat": {"id": 123}, "message_id": 1}},
        {"update_id": 11, "message": {"text": "intrus", "chat": {"id": 999}, "message_id": 2}},
    ]
    msgs, max_id = telegram_api.extract_text_messages(updates, "123")
    assert len(msgs) == 1
    assert msgs[0]["text"] == "salut"
    # L'offset avance au-delà de TOUS les updates (même ignorés).
    assert max_id == 11


def test_long_message_split_under_limit():
    """Un message > 4096 caractères est découpé en morceaux valides."""
    from src.telegram_bot import telegram_api

    chunks = telegram_api._split_message("x" * 9000)
    assert len(chunks) >= 3
    assert all(len(c) <= 4096 for c in chunks)


def test_split_preserves_short_message():
    """Un message court n'est pas découpé."""
    from src.telegram_bot import telegram_api

    assert telegram_api._split_message("bonjour") == ["bonjour"]


def test_bot_not_configured_without_secrets(monkeypatch):
    """Sans token/chat_id, le bot se sait non configuré."""
    from src.telegram_bot import telegram_api

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert telegram_api.bot_configured() is False


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def test_command_detection():
    from src.telegram_bot import commands

    assert commands.is_command("/recos") is True
    assert commands.is_command("salut") is False
    assert commands.is_state_command("/dismiss TAO") is True
    assert commands.is_state_command("/recos") is False


def test_parse_command():
    from src.telegram_bot import commands

    cmd, args = commands.parse_command("/snooze ETH extra")
    assert cmd == "/snooze"
    assert args == ["ETH", "extra"]


def test_read_command_help_and_unknown():
    from src.telegram_bot import commands

    assert commands.handle_read_command("/aide") is not None
    assert "Assistant" in commands.handle_read_command("/aide")
    # Une commande non listée en lecture renvoie None (routée ailleurs).
    assert commands.handle_read_command("/ask question") is None


def test_state_command_dismiss():
    """/dismiss retire la reco de la liste active et modifie le state."""
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem
    from src.telegram_bot import commands

    mem.save_active_recommendations([
        {"asset": "TAO", "action": "RENFORCER", "id": "TAO-1", "status": "en cours"},
        {"asset": "ETH", "action": "RENFORCER", "id": "ETH-1", "status": "en cours"},
    ])
    reply, modified = commands.handle_state_command("/dismiss TAO")
    assert modified is True
    assert "écartée" in reply
    remaining = mem.load_active_recommendations()
    assert len(remaining) == 1
    assert remaining[0]["asset"] == "ETH"


def test_state_command_validate_archives():
    """/validate clôture la reco et l'archive dans l'historique de prédictions."""
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem
    from src.telegram_bot import commands

    mem.save_active_recommendations([
        {"asset": "BTC", "action": "RENFORCER", "id": "BTC-1", "status": "en cours"},
    ])
    reply, modified = commands.handle_state_command("/validate BTC")
    assert modified is True
    assert "validée" in reply
    assert mem.load_active_recommendations() == []
    hist = mem.load_prediction_history()
    assert any(p.get("asset") == "BTC" and p.get("status") == "validated" for p in hist)


def test_state_command_snooze_keeps_but_flags():
    """/snooze garde la reco mais la signale."""
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem
    from src.telegram_bot import commands

    mem.save_active_recommendations([
        {"asset": "SOL", "action": "RENFORCER", "id": "SOL-1", "status": "en cours"},
    ])
    reply, modified = commands.handle_state_command("/snooze SOL")
    assert modified is True
    recos = mem.load_active_recommendations()
    assert recos[0]["snoozed"] is True


def test_state_command_unknown_asset():
    """/dismiss sur un actif absent ne modifie pas le state."""
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem
    from src.telegram_bot import commands

    mem.save_active_recommendations([{"asset": "BTC", "action": "RENFORCER", "id": "BTC-1"}])
    reply, modified = commands.handle_state_command("/dismiss DOGE")
    assert modified is False
    assert "Aucune reco" in reply


# --------------------------------------------------------------------------- #
# Mémoire conversationnelle + offset
# --------------------------------------------------------------------------- #
def test_offset_persistence():
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem

    assert mem.load_telegram_offset() == 0
    mem.save_telegram_offset(42)
    assert mem.load_telegram_offset() == 42


def test_conversation_history_rotation():
    _stub_tenacity()
    _fresh_state()
    import src.state.report_memory as mem

    for i in range(45):
        mem.append_telegram_turn("user", f"message {i}")
    hist = mem.load_telegram_history(limit=12)
    assert len(hist) == 12
    # Le plus récent doit être le dernier ajouté.
    assert hist[-1]["content"] == "message 44"


# --------------------------------------------------------------------------- #
# Assistant (prompt building, sans appel réseau)
# --------------------------------------------------------------------------- #
def test_assistant_prompt_assembles_all_parts():
    _stub_tenacity()
    from src.telegram_bot import assistant

    ctx = {"portfolio": {"positions": [{"symbol": "BTC", "value_usd_baseline": 500}]}}
    history = [{"role": "user", "content": "parle-moi de BTC"},
               {"role": "assistant", "content": "BTC est à..."}]
    prompt = assistant.build_assistant_prompt("et pour ETH ?", ctx, history)
    assert "analyste crypto personnel" in prompt
    assert "NON-INVENTION" in prompt
    assert "BTC" in prompt  # contexte injecté
    assert "parle-moi de BTC" in prompt  # historique
    assert "et pour ETH ?" in prompt  # message courant


def test_context_to_text_handles_empty():
    from src.telegram_bot.context_loader import context_to_text

    txt = context_to_text({})
    assert "Aucun contexte" in txt


def test_context_to_text_truncates():
    from src.telegram_bot.context_loader import context_to_text

    big = {"x": ["data"] * 50000}
    txt = context_to_text(big, max_chars=1000)
    assert len(txt) <= 1100
    assert "tronqué" in txt


# --------------------------------------------------------------------------- #
# Mode relais (v18.1 — réveil par message via Cloudflare Worker)
# --------------------------------------------------------------------------- #
def test_relay_configured_reflects_env(monkeypatch):
    """relay_configured() est piloté par la présence de RELAY_PULL_URL."""
    from src.telegram_bot import telegram_api

    monkeypatch.delenv("RELAY_PULL_URL", raising=False)
    assert telegram_api.relay_configured() is False
    monkeypatch.setenv("RELAY_PULL_URL", "https://x.workers.dev/pull")
    assert telegram_api.relay_configured() is True


def test_pull_relay_updates_parses_both_shapes(monkeypatch):
    """Le drain accepte {"updates":[...]} ET une liste directe ; None → []."""
    from src.telegram_bot import telegram_api

    monkeypatch.setenv("RELAY_PULL_URL", "https://x.workers.dev/pull")
    monkeypatch.setenv("RELAY_SECRET", "s3cr3t")

    monkeypatch.setattr(telegram_api, "get_json",
                        lambda url, headers=None, timeout=20: {"updates": [{"update_id": 1}]})
    assert telegram_api.pull_relay_updates() == [{"update_id": 1}]

    monkeypatch.setattr(telegram_api, "get_json",
                        lambda url, headers=None, timeout=20: [{"update_id": 2}])
    assert telegram_api.pull_relay_updates() == [{"update_id": 2}]

    # Dégradation gracieuse : relais injoignable → liste vide, pas d'exception.
    monkeypatch.setattr(telegram_api, "get_json",
                        lambda url, headers=None, timeout=20: None)
    assert telegram_api.pull_relay_updates() == []


def test_pull_relay_updates_authenticates(monkeypatch):
    """Le drain envoie bien le bearer RELAY_SECRET à l'URL /pull."""
    from src.telegram_bot import telegram_api

    monkeypatch.setenv("RELAY_PULL_URL", "https://x.workers.dev/pull")
    monkeypatch.setenv("RELAY_SECRET", "s3cr3t")
    captured = {}

    def fake_get(url, headers=None, timeout=20):
        captured["url"] = url
        captured["headers"] = headers
        return []

    monkeypatch.setattr(telegram_api, "get_json", fake_get)
    telegram_api.pull_relay_updates()
    assert captured["url"] == "https://x.workers.dev/pull"
    assert captured["headers"] == {"Authorization": "Bearer s3cr3t"}


def test_pull_relay_updates_empty_without_url(monkeypatch):
    """Sans RELAY_PULL_URL, aucun appel réseau, liste vide."""
    from src.telegram_bot import telegram_api

    monkeypatch.delenv("RELAY_PULL_URL", raising=False)
    assert telegram_api.pull_relay_updates() == []


def test_main_routes_to_relay_when_configured(monkeypatch):
    """main() draine le relais si configuré, sinon poll getUpdates (rétro-compat)."""
    from src.telegram_bot import bot, telegram_api

    calls = []
    monkeypatch.setattr(bot, "run_from_relay", lambda: calls.append("relay") or 0)
    monkeypatch.setattr(bot, "run_once", lambda: calls.append("once") or 0)

    monkeypatch.setattr(telegram_api, "relay_configured", lambda: True)
    assert bot.main() == 0
    monkeypatch.setattr(telegram_api, "relay_configured", lambda: False)
    assert bot.main() == 0
    assert calls == ["relay", "once"]


def test_run_from_relay_processes_only_owner(monkeypatch):
    """run_from_relay traite le message d'Omar et ignore les intrus (sécurité)."""
    _stub_tenacity()
    _fresh_state()
    from src.telegram_bot import assistant, bot, telegram_api

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    updates = [
        {"update_id": 5, "message": {"text": "salut", "chat": {"id": 123}, "message_id": 1}},
        {"update_id": 6, "message": {"text": "intrus", "chat": {"id": 999}, "message_id": 2}},
    ]
    monkeypatch.setattr(telegram_api, "pull_relay_updates", lambda: updates)
    monkeypatch.setattr(bot, "load_full_context", lambda: {})
    monkeypatch.setattr(assistant, "answer", lambda *a, **k: "réponse test")
    sent = []
    monkeypatch.setattr(telegram_api, "send_message",
                        lambda text, *a, **k: sent.append(text) or True)

    assert bot.run_from_relay() == 0
    assert sent == ["réponse test"]  # seul Omar est servi


def test_run_from_relay_empty_queue_is_noop(monkeypatch):
    """File vide (run redondant) → sortie immédiate sans rien envoyer."""
    from src.telegram_bot import bot, telegram_api

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(telegram_api, "pull_relay_updates", lambda: [])
    sent = []
    monkeypatch.setattr(telegram_api, "send_message",
                        lambda text, *a, **k: sent.append(text) or True)
    assert bot.run_from_relay() == 0
    assert sent == []


# --------------------------------------------------------------------------- #
# Notifications push
# --------------------------------------------------------------------------- #
def test_notify_summary_line_morning():
    # v28 (TG-refonte) — _build_digest = digest à 2 zones : 📌 EN BREF (⚡ verdict
    # + 📊 marché + 💼) puis détail (🌍 marché, 🎯 actions, 📈 positions, ⚠️).
    from src.telegram_bot import notify

    digest = notify._build_digest(
        {"header": {"time_casablanca": "jeudi 3 juillet, 08:30"},
         "portfolio_snapshot": {"value_usd": 2626, "change_24h_pct": 1.2},
         "market_regime": {"available": True, "label_fr": "range",
                           "days_in_regime": 5},
         "macro_context": {"fear_greed": 19, "fear_greed_label": "peur extrême"},
         "top_action": {"line": "RENFORCER TAO · +2% du PTF"},
         "thesis_of_the_day": [{"asset": "TAO", "action": "RENFORCER",
                                "confidence": 74}]},
        "morning",
    )
    assert "MATIN" in digest and "jeudi 3 juillet" in digest
    assert "📌 EN BREF" in digest
    assert "⚡" in digest and "RENFORCER TAO" in digest      # verdict adaptatif
    assert "Fond range" in digest                            # état marché
    assert "/pourquoi TAO" in digest                         # commandes perso
    # Éléments RETIRÉS de l'ancien format.
    assert "Risque PTF" not in digest
    assert "Réponds ici pour creuser" not in digest


def test_notify_digest_evening_and_weekly():
    """v28 (TG-refonte) — digests soir & hebdo : EN BREF + détail, FR (virgule)."""
    from src.telegram_bot import notify

    ev = notify._build_digest(
        {"header": {"time_casablanca": "jeudi 3 juillet, 20:00"},
         "daily_pnl": {"day_change_pct": -1.4, "day_change_usd": -37}},
        "evening")
    assert "SOIR" in ev and "📌 EN BREF" in ev
    assert "−1,40%" in ev and "(−37 $)" in ev

    wk = notify._build_digest(
        {"header": {"time_casablanca": "dimanche 6 juillet, 12:00"},
         "market_regime": {"available": True, "label_fr": "baissier"},
         "portfolio_snapshot": {"weekly_pnl_pct": 3.8, "vs_btc_7d_pct": 0.3},
         "scenarios": [{"label": "range", "probability_pct": 55}],
         "weekly_action_plan": [{"action": "Renforcer BTC si cassure 60k"}]},
        "weekly")
    assert "HEBDO" in wk and "Semaine +3,8%" in wk and "RANGE (55%)" in wk


def test_notify_not_configured_returns_false(monkeypatch):
    from src.telegram_bot import notify

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.push_report_notification({"portfolio_snapshot": {}}, "morning") is False
