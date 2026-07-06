# -*- coding: utf-8 -*-
"""OB18 — commande Telegram /suivi (track record consultable, mobile)."""

from __future__ import annotations

from src.telegram_bot import commands


def test_suivi_registered_as_read_command():
    for c in ("/suivi", "/historique", "/bilan", "/track"):
        assert c in commands._READ_COMMANDS
    assert commands.handle_read_command("/suivi") is not None
    assert commands.handle_read_command("/historique") is not None


def test_suivi_returns_valid_string_even_empty_state():
    """Best-effort : historique vide → réponse honnête, jamais d'exception."""
    out = commands._cmd_suivi()
    assert isinstance(out, str)
    assert "Suivi & track record" in out
    assert "Recos actives" in out          # bloc toujours présent
    assert "Clôturées 90j" in out          # bloc win rate toujours présent


def test_help_lists_suivi():
    assert "/suivi" in commands._cmd_help()


def test_unknown_command_still_returns_none():
    assert commands.handle_read_command("/inconnu") is None
