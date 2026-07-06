# -*- coding: utf-8 -*-
"""Régression prod 06/07 — surrogates Unicode isolés → crash weekly AVANT mail.

Le weekly a planté (`UnicodeEncodeError: surrogates not allowed`) au moment de
`save_weekly_report` → `_write` → `fh.write`, AVANT l'envoi du mail : Gemini
avait émis un emoji tronqué (moitié de surrogate). Deux verrous :
  1. `strip_surrogates` neutralise à la source (recombine les paires, remplace
     les orphelins) — ni state, ni mail, ni Telegram ne voient un surrogate.
  2. `_write` est best-effort : sanitisation ultime + n'attrape plus seulement
     `OSError` → la persistance du state ne peut PLUS bloquer la livraison.
"""

from __future__ import annotations

import json

# --------------------------------------------------------------------------- #
# 1 — strip_surrogates : recombine les paires, strippe les orphelins, n'altère
#     pas le texte normal, idempotent.
# --------------------------------------------------------------------------- #
def test_strip_surrogates_recombines_split_pair():
    from src.utils.text_sanitize import strip_surrogates

    # "🎲" (U+1F3B2) émis en 2 moitiés surrogates séparées (le cas prod exact).
    split_emoji = chr(0xD83C) + chr(0xDFB2)
    out = strip_surrogates("avant " + split_emoji + " apres")
    out.encode("utf-8")  # ne doit PAS lever
    assert "\ud83c" not in out and "\udfb2" not in out
    assert "\U0001f3b2" in out  # l'emoji est RÉCUPÉRÉ, pas détruit


def test_strip_surrogates_replaces_lone_orphan():
    from src.utils.text_sanitize import strip_surrogates

    out = strip_surrogates("x\ud800y")  # high surrogate orphelin
    assert out.encode("utf-8")  # encode sans lever
    assert "\ud800" not in out
    assert out == "x�y"


def test_strip_surrogates_keeps_normal_text_intact():
    from src.utils.text_sanitize import strip_surrogates

    txt = "Bilan hebdo \U0001f4ca +3.8% éàü — niveaux 58 454–82 416 $"
    assert strip_surrogates(txt) == txt
    # Idempotent.
    assert strip_surrogates(strip_surrogates(txt)) == txt


def test_strip_surrogates_passthrough_non_str():
    from src.utils.text_sanitize import strip_surrogates

    assert strip_surrogates("") == ""
    assert strip_surrogates(None) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 2 — _write : un payload contenant un surrogate NE crashe PAS et produit un
#     fichier UTF-8 valide relisable.
# --------------------------------------------------------------------------- #
def test_write_state_with_surrogate_does_not_crash(monkeypatch, tmp_path):
    import src.state.report_memory as rm

    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)
    # 🎲 (U+1F3B2) émis en 2 MOITIÉS de surrogate séparées : le cas prod exact.
    # Construit via chr() → deux code points surrogates ISOLÉS sans ambiguïté.
    split_emoji = chr(0xD83C) + chr(0xDFB2)
    payload = {"thesis": "TAO casse " + split_emoji + " la résistance", "ok": True}
    # Preuve que le repro est RÉEL : la sérialisation brute planterait à l'écriture.
    import pytest
    with pytest.raises(UnicodeEncodeError):
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Ancien comportement : UnicodeEncodeError propagée → tout le rapport tué.
    rm._write("t_surro.json", payload)
    written = (tmp_path / "t_surro.json")
    assert written.exists()
    # Fichier UTF-8 valide et relisable (surrogates neutralisés/recombinés).
    reloaded = json.loads(written.read_text(encoding="utf-8"))
    assert reloaded["ok"] is True
    assert "\ud83c" not in reloaded["thesis"] and "\udfb2" not in reloaded["thesis"]


def test_write_state_is_best_effort_never_raises(monkeypatch, tmp_path):
    """Même une sérialisation impossible ne doit pas propager (best-effort)."""
    import src.state.report_memory as rm

    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)

    class _Boom:
        def __repr__(self):  # default=str appellera repr → on le fait exploser
            raise RuntimeError("nope")

    # json.dumps(default=str) lèvera sur _Boom ; _write doit AVALER, pas propager.
    rm._write("t_boom.json", {"x": _Boom()})  # ne doit pas lever


# --------------------------------------------------------------------------- #
# 3 — gemini_client : la sortie brute du modèle est sanitisée avant tout usage.
# --------------------------------------------------------------------------- #
def _fake_client(text: str):
    class _Resp:
        pass
    resp = _Resp()
    resp.text = text

    class _Models:
        def generate_content(self, **kwargs):
            return resp

    class _Client:
        models = _Models()

    return _Client()


def test_gemini_call_text_strips_surrogates():
    from src.ai_brain.gemini_client import GeminiClient

    client = object.__new__(GeminiClient)  # bypass __init__ (pas de clé/réseau)
    # Paire splittée (chr) + orphelin isolé dans la même sortie modèle.
    poisoned = "analyse " + chr(0xD83C) + chr(0xDFB2) + " puis " + chr(0xD800) + " fin"
    client._client = _fake_client(poisoned)
    out = client._call_text("p", 0.5, "gemini-2.5-flash")
    out.encode("utf-8")  # ne doit PAS lever
    assert "\ud83c" not in out and "\ud800" not in out


def test_gemini_call_json_strips_surrogates_before_parse():
    from src.ai_brain.gemini_client import GeminiClient

    client = object.__new__(GeminiClient)
    # JSON valide mais avec un surrogate orphelin dans une valeur.
    client._client = _fake_client('{"note": "x\ud800y"}')
    raw = client._call_json("p", 0.4, "gemini-2.5-flash")
    raw.encode("utf-8")
    assert json.loads(raw)["note"] == "x�y"
