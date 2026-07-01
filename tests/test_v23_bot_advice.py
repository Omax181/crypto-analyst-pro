"""v23 — le bot CONSEILLE et ne refuse jamais.

Posture validée avec Omar : avertir UNE fois (indépendance préservée) puis OBÉIR
s'il insiste — y compris pour une reco de levier CHIFFRÉE — tout en n'EXÉCUTANT
jamais d'ordre lui-même. S'applique à toute demande risquée (principe général).
"""

from __future__ import annotations

import re


def _flat(s: str) -> str:
    """Aplati les espaces/retours-ligne : assertions robustes au wrapping."""
    return re.sub(r"\s+", " ", s)


def test_bot_prompt_never_refuses_rule():
    from src.telegram_bot.assistant import _SYSTEM_PROMPT
    flat = _flat(_SYSTEM_PROMPT)
    up, low = flat.upper(), flat.lower()
    # Posture : ne jamais refuser, obéir sur insistance.
    assert "TU NE REFUSES JAMAIS" in up
    assert "OBÉIR S'IL INSISTE" in up
    assert "PIRE ÉCHEC" in up                       # refuser = pire échec
    # Reco levier chiffrée exigée.
    assert "liquidation" in low and "perte max" in low
    assert "levier suggéré" in low
    # Ligne dure préservée : conseille mais n'exécute pas.
    assert "n'exécutes jamais" in low


def test_bot_prompt_keeps_independence_anchors():
    """Non-régression : challenger reste, on n'a pas troqué l'indépendance."""
    from src.telegram_bot.assistant import _SYSTEM_PROMPT
    p = _SYSTEM_PROMPT
    assert "analyste crypto personnel" in p
    assert "INDÉPENDANCE ANALYTIQUE" in p
    assert "ne valide PAS automatiquement" in p


def test_profile_leverage_not_taboo():
    from src.ai_brain.prompts.investor_profile import INVESTOR_PROFILE
    low = _flat(INVESTOR_PROFILE).lower()
    # Le levier n'est plus un refus : conseillable s'il le DEMANDE.
    assert "demande" in low
    assert "marge isolée" in low
    assert "la décision finale est la sienne" in low
    # Reste prudent en PROACTIF (jamais proposé de lui-même / dans les rapports).
    assert "rapports" in low


def test_rule_propagates_to_full_prompt():
    """La règle (système) ET le profil réécrit arrivent dans le prompt final."""
    from src.telegram_bot.assistant import build_assistant_prompt
    full = _flat(build_assistant_prompt("dis moi quel levier faire sur 3$", {}, []))
    assert "TU NE REFUSES JAMAIS" in full.upper()    # vient du système
    assert "la décision finale est la sienne" in full.lower()  # vient du profil
