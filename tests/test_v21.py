"""Tests v21 — profil investisseur (mails+bot), édition portefeuille protégée,
PRU/P&L réel, mémoire durable du bot.

Hermétiques (aucun réseau) : monkeypatch du state, mocks des prix live.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest


# --------------------------------------------------------------------------- #
# 1) Profil investisseur — source unique injectée dans les mails ET le bot
# --------------------------------------------------------------------------- #
def test_investor_profile_in_mail_persona() -> None:
    from src.ai_brain.prompts.analyst_persona import ANALYST_PERSONA
    from src.ai_brain.prompts.investor_profile import INVESTOR_PROFILE

    assert "PROFIL DE L'INVESTISSEUR" in ANALYST_PERSONA
    assert INVESTOR_PROFILE.strip() in ANALYST_PERSONA
    for s in ("accumulateur de conviction", "Technique + Qualité projet + Macro",
              "marge ISOLÉE", "Renforce BTC", "x5 à x10"):
        assert s in ANALYST_PERSONA, s


def test_investor_profile_in_bot_prompt() -> None:
    from src.telegram_bot.assistant import build_assistant_prompt

    p = build_assistant_prompt("salut", {}, [])
    assert "PROFIL DE L'INVESTISSEUR" in p
    assert "MÉMOIRE & CONTINUITÉ" in p  # instruction anti-répétition


def test_profile_reaches_all_three_mail_prompts() -> None:
    from src.ai_brain.prompts.evening_prompt import build_evening_prompt
    from src.ai_brain.prompts.morning_prompt import build_morning_prompt
    from src.ai_brain.prompts.weekly_prompt import build_weekly_prompt

    ts = "2026-06-27T08:00:00Z"
    morning = build_morning_prompt(timestamp=ts, data={}, portfolio_yaml="",
                                   evening_state={})
    evening = build_evening_prompt(timestamp=ts, data={}, morning_state={})
    weekly = build_weekly_prompt(timestamp=ts, data={}, week_state={})
    for out in (morning, evening, weekly):
        assert "PROFIL DE L'INVESTISSEUR" in out
        assert "accumulateur de conviction" in out


# --------------------------------------------------------------------------- #
# 2) portfolio.yaml — PRU partout + corrections de quantités
# --------------------------------------------------------------------------- #
def test_portfolio_has_pru_and_corrections() -> None:
    from src.utils.portfolio_loader import load_portfolio

    p = load_portfolio()["portfolio"]
    assert all("pru" in v for v in p.values()), "pru manquant sur une position"
    assert p["JASMY"]["quantity"] == 14028
    assert p["RENDER"]["quantity"] == 39.6 and p["RENDER"]["symbol"] == "RNDR"
    assert "1000SATS" in p


# --------------------------------------------------------------------------- #
# 3) Moteur d'édition déterministe (portfolio_editor)
# --------------------------------------------------------------------------- #
_SAMPLE = """portfolio:
  # === TIER 1 (analyse deep) ===
  ETH:
    quantity: 0.3
    value_usd: 900
    pru: 3000
    tier: 1
  TAO:
    quantity: 1.6
    value_usd: 400
    pru: 200
    tier: 1
"""


def test_editor_buy_recomputes_weighted_pru() -> None:
    from src.utils import portfolio_editor as pe

    nt, s = pe.apply_quantity_change(_SAMPLE, "ETH", "buy", 0.1, 1000.0)
    expected = (0.3 * 3000 + 0.1 * 1000) / 0.4
    assert abs(s["new_qty"] - 0.4) < 1e-9
    assert abs(s["new_pru"] - expected) < 1e-6
    assert "quantity: 0.4" in nt


def test_editor_sell_keeps_pru() -> None:
    from src.utils import portfolio_editor as pe

    _, s = pe.apply_quantity_change(_SAMPLE, "TAO", "sell", 0.6)
    assert abs(s["new_qty"] - 1.0) < 1e-9
    assert s["new_pru"] == s["old_pru"]


def test_editor_sell_more_than_balance_raises() -> None:
    from src.utils import portfolio_editor as pe

    with pytest.raises(pe.PortfolioEditError):
        pe.apply_quantity_change(_SAMPLE, "TAO", "sell", 99)


def test_editor_unknown_asset_raises() -> None:
    from src.utils import portfolio_editor as pe

    with pytest.raises(pe.PortfolioEditError):
        pe.apply_quantity_change(_SAMPLE, "DOGE", "buy", 1, 1.0)


def test_editor_set_with_price() -> None:
    from src.utils import portfolio_editor as pe

    _, s = pe.apply_quantity_change(_SAMPLE, "ETH", "set", 0.5, 2500.0)
    assert s["new_qty"] == 0.5 and s["new_pru"] == 2500.0


def test_editor_add_asset() -> None:
    from src.utils import portfolio_editor as pe

    nt, _ = pe.add_asset(_SAMPLE, "SOL", 2, 1, price=150.0)
    assert "SOL:" in nt and "pru: 150" in nt
    with pytest.raises(pe.PortfolioEditError):
        pe.add_asset(_SAMPLE, "ETH", 1, 1)  # déjà présent


# --------------------------------------------------------------------------- #
# 4) Parsing & garde-fous bot (portfolio_edit)
# --------------------------------------------------------------------------- #
def test_parse_edit_slash_and_natural() -> None:
    from src.telegram_bot import portfolio_edit as ped

    r = ped.parse_edit("/buy ETH 0.1 1600 " + ped.EDIT_PASSWORD)
    assert (r["action"], r["asset"], r["qty"], r["price"], r["has_password"]) == \
        ("buy", "ETH", 0.1, 1600.0, True)
    r2 = ped.parse_edit("j'ai acheté 0,1 ETH à 1600")
    assert r2["action"] == "buy" and r2["price"] == 1600.0 and not r2["has_password"]
    r3 = ped.parse_edit("/sell TAO 0.5 " + ped.EDIT_PASSWORD)
    assert r3["action"] == "sell" and r3["price"] is None and r3["has_password"]


def test_parse_edit_ignores_questions_and_chitchat() -> None:
    from src.telegram_bot import portfolio_edit as ped

    assert ped.parse_edit("est-ce le bon moment pour acheter ETH ?") is None
    assert ped.parse_edit("comment va BTC") is None        # pas de verbe d'édition
    assert ped.parse_edit("/buy ETH " + ped.EDIT_PASSWORD) is None  # pas de quantité


def test_handle_edit_preview_does_not_write(monkeypatch) -> None:
    from src.telegram_bot import portfolio_edit as ped
    from src.utils import portfolio_editor as pe

    written: list = []
    monkeypatch.setattr(pe, "write_portfolio_text", lambda t, *a, **k: written.append(t))
    reply, mod = ped.handle_edit("j'ai acheté 0.1 ETH à 1600")  # sans mot de passe
    assert mod is False and "Aperçu" in reply and not written


def test_handle_edit_with_password_writes_and_logs_memory(monkeypatch) -> None:
    from src.state import report_memory as mem
    from src.telegram_bot import portfolio_edit as ped
    from src.utils import portfolio_editor as pe

    written: list = []
    monkeypatch.setattr(pe, "write_portfolio_text", lambda t, *a, **k: written.append(t))
    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    reply, mod = ped.handle_edit("/buy ETH 0.1 1600 " + ped.EDIT_PASSWORD)
    assert mod is True and "mis à jour" in reply and len(written) == 1
    assert any("ETH" in m["text"] for m in mem.load_bot_memory())


def test_handle_edit_business_error_is_clean(monkeypatch) -> None:
    from src.telegram_bot import portfolio_edit as ped
    from src.utils import portfolio_editor as pe

    written: list = []
    monkeypatch.setattr(pe, "write_portfolio_text", lambda t, *a, **k: written.append(t))
    reply, mod = ped.handle_edit("/sell TAO 999999 " + ped.EDIT_PASSWORD)
    assert mod is False and reply.startswith("❌") and not written


# --------------------------------------------------------------------------- #
# 5) P&L réel (live_data) + affichage /ptf
# --------------------------------------------------------------------------- #
def test_live_snapshot_computes_pnl(monkeypatch) -> None:
    from src.data_sources import coingecko
    from src.telegram_bot import live_data
    from src.utils import portfolio_loader

    monkeypatch.setattr(portfolio_loader, "load_portfolio", lambda: {"portfolio": {
        "ETH": {"quantity": 1.0, "pru": 1000, "value_usd": 1, "tier": 1}}})
    monkeypatch.setattr(coingecko, "get_market_data",
                        lambda syms: {"ETH": {"price": 1200, "change_24h": 2.0}})
    snap = live_data.get_live_portfolio_snapshot()
    assert snap["available"]
    row = snap["positions"][0]
    assert row["pnl_pct"] == 20.0 and row["pnl_usd"] == 200.0
    assert snap["pnl_pct"] == 20.0 and snap["pnl_usd"] == 200.0


def test_cmd_ptf_shows_pnl(monkeypatch) -> None:
    from src.telegram_bot import commands, live_data

    monkeypatch.setattr(live_data, "get_live_portfolio_snapshot", lambda: {
        "available": True, "total_value_usd": 1200,
        "positions_priced_live": 1, "positions_total": 1,
        "pnl_usd": 200.0, "pnl_pct": 20.0,
        "positions": [{"symbol": "ETH", "value_usd": 1200, "weight_pct": 100,
                       "pnl_pct": 20.0, "change_24h": 2.0}],
    })
    out = commands._cmd_portfolio()
    assert "P&L latent" in out and "+20.0%" in out and "PRU +20%" in out


# --------------------------------------------------------------------------- #
# 6) Mémoire durable du bot
# --------------------------------------------------------------------------- #
def test_bot_memory_crud(monkeypatch) -> None:
    from src.state import report_memory as mem

    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    mem._write(mem.BOT_MEMORY_FILE, [])  # départ propre, indépendant de l'ordre
    mem.append_bot_memory("note", "A")
    mem.append_bot_memory("decision", "B")
    assert [m["text"] for m in mem.load_bot_memory()] == ["A", "B"]
    assert mem.load_bot_memory(limit=1)[0]["text"] == "B"
    assert mem.remove_bot_memory(0) is True
    assert [m["text"] for m in mem.load_bot_memory()] == ["B"]
    assert mem.remove_bot_memory(99) is False
    n = len(mem.load_bot_memory())
    mem.append_bot_memory("note", "")  # vide = no-op
    assert len(mem.load_bot_memory()) == n


def test_remember_memory_forget_commands(monkeypatch) -> None:
    from src.state import report_memory as mem
    from src.telegram_bot import commands

    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    mem._write(mem.BOT_MEMORY_FILE, [])  # départ propre, indépendant de l'ordre
    r, mod = commands.handle_state_command("/remember accumuler ETH sous 1500")
    assert mod is True and "Mémorisé" in r
    out = commands.handle_read_command("/memory")
    assert "accumuler ETH sous 1500" in out and "1." in out
    r2, mod2 = commands.handle_state_command("/forget 1")
    assert mod2 is True and mem.load_bot_memory() == []


def test_dismiss_records_decision_memory(monkeypatch) -> None:
    from src.state import report_memory as mem
    from src.telegram_bot import commands

    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    mem._write(mem.BOT_MEMORY_FILE, [])  # départ propre
    mem.save_active_recommendations([{"asset": "TAO", "action": "RENFORCER", "id": "x"}])
    r, mod = commands.handle_state_command("/dismiss TAO")
    assert mod is True
    assert any("TAO" in m["text"] for m in mem.load_bot_memory())


def test_durable_memory_injected_into_context(monkeypatch) -> None:
    from src.state import report_memory as mem
    from src.telegram_bot import context_loader

    monkeypatch.setattr(mem, "_STATE_DIR", pathlib.Path(tempfile.mkdtemp()))
    mem.append_bot_memory("note", "seuil ETH 1500")
    ctx = context_loader.load_full_context()
    assert "durable_memory" in ctx
    assert any("seuil ETH 1500" in m["text"] for m in ctx["durable_memory"])


# --------------------------------------------------------------------------- #
# 7) Routage : l'édition passe AVANT l'assistant
# --------------------------------------------------------------------------- #
def test_edit_routed_before_assistant(monkeypatch) -> None:
    from src.telegram_bot import bot, portfolio_edit

    monkeypatch.setattr(portfolio_edit, "is_edit_intent", lambda t: True)
    monkeypatch.setattr(portfolio_edit, "handle_edit", lambda t: ("EDIT_OK", True))
    reply, mod = bot._route_message("/buy ETH 0.1 1600 x", {}, [])
    assert reply == "EDIT_OK" and mod is True


# --------------------------------------------------------------------------- #
# 8) Formatage Telegram (Markdown -> HTML) + envoi
# --------------------------------------------------------------------------- #
def test_to_telegram_html_basics() -> None:
    from src.telegram_bot.formatting import to_telegram_html

    assert to_telegram_html("**gras**") == "<b>gras</b>"
    assert to_telegram_html("*gras*") == "<b>gras</b>"
    assert to_telegram_html("_ita_") == "<i>ita</i>"
    assert to_telegram_html("`code`") == "<code>code</code>"
    # échappement HTML
    out = to_telegram_html("BTC < 60000 & ETH > 1000")
    assert "&lt; 60000 &amp;" in out and "&gt; 1000" in out


def test_to_telegram_html_realistic() -> None:
    from src.telegram_bot.formatting import to_telegram_html

    msg = ("**Renforcement du Dollar (DXY) :**\n"
           "* Le dollar est à 101.5\n"
           "- Impact : l'or baisse\n"
           "## Conclusion\n"
           "BTC à 62 626 $")
    h = to_telegram_html(msg)
    assert "**" not in h                       # plus de ** affiché en clair
    assert "<b>Renforcement du Dollar (DXY) :</b>" in h
    assert "• Le dollar est à 101.5" in h
    assert "• Impact : l'or baisse" in h
    assert "<b>Conclusion</b>" in h


def test_strip_html_roundtrip() -> None:
    from src.telegram_bot.formatting import strip_html

    assert strip_html("<b>x</b> et <code>y</code>") == "x et y"
    assert strip_html("a &lt; b") == "a < b"


def test_send_message_converts_to_html(monkeypatch) -> None:
    from src.telegram_bot import telegram_api

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    sent: list = []
    monkeypatch.setattr(telegram_api, "post_json",
                        lambda url, json_body=None, **k: (sent.append(json_body) or {"ok": True}))
    ok = telegram_api.send_message("Verdict **net** et `code`", parse_mode="HTML")
    assert ok and sent
    body = sent[-1]
    assert body["parse_mode"] == "HTML"
    assert "<b>net</b>" in body["text"] and "<code>code</code>" in body["text"]
    assert "**" not in body["text"]


def test_send_message_html_failure_falls_back_to_plain(monkeypatch) -> None:
    from src.telegram_bot import telegram_api

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    calls: list = []

    def fake_post(url, json_body=None, **k):
        calls.append(json_body)
        if json_body.get("parse_mode"):           # 1er essai HTML -> échoue
            return {"ok": False, "description": "can't parse entities"}
        return {"ok": True}                        # repli texte brut -> OK

    monkeypatch.setattr(telegram_api, "post_json", fake_post)
    ok = telegram_api.send_message("**gras**", parse_mode="HTML")
    assert ok and len(calls) == 2
    assert "parse_mode" not in calls[1]
    assert "<b>" not in calls[1]["text"] and "gras" in calls[1]["text"]


# --------------------------------------------------------------------------- #
# 9) Anti-hallucination : ancres de prix réelles + garde-fous prompt
# --------------------------------------------------------------------------- #
def test_price_anchors_compute_12m_range(monkeypatch) -> None:
    from src.data_sources import coingecko
    from src.telegram_bot import live_data

    monkeypatch.setattr(coingecko, "get_market_data",
                        lambda syms: {"BTC": {"price": 100000}, "ETH": {"price": 3000}})
    monkeypatch.setattr(coingecko, "get_ohlc", lambda sym, days: [
        {"open": 1, "high": 120000 if sym == "BTC" else 4000,
         "low": 74000 if sym == "BTC" else 2000, "close": 1},
        {"open": 1, "high": 110000 if sym == "BTC" else 3500,
         "low": 80000 if sym == "BTC" else 2200, "close": 1},
    ])
    a = live_data.get_price_anchors()
    assert a["available"]
    btc = a["assets"]["BTC"]
    assert btc["now"] == 100000 and btc["low_12m"] == 74000 and btc["high_12m"] == 120000


def test_bot_prompt_has_antihallucination_and_format_rules() -> None:
    from src.telegram_bot.assistant import build_assistant_prompt

    p = build_assistant_prompt("x", {}, [])
    assert "FAITS HISTORIQUES" in p and "price_anchors" in p
    assert "FORMATAGE TELEGRAM" in p
