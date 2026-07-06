# -*- coding: utf-8 -*-
"""v27 — verrous du hotfix « surrogates échappés » (crash weekly 06/07, run #28).

Le run #28 a prouvé que la sanitisation v26.1 (texte BRUT de Gemini) ne suffit
pas : un surrogate peut arriver ÉCHAPPÉ en ASCII pur (« \\ud83c » sur 6
caractères) — invisible pour strip_surrogates(texte) — puis être DÉCODÉ par
``json.loads`` en véritable surrogate isolé dans les VALEURS du payload. Le
poison a traversé le rendu et fait lever ``MIMEText`` (« surrogates not
allowed ») HORS du try SMTP → run tué, mail jamais parti.

Quatre verrous :
  C1 — strip_surrogates_deep : sanitisation récursive post-parse (JSON).
  C1b — generate_json/_parse_json : le dict rendu est TOUJOURS UTF-8-sûr.
  C2 — send_email ne lève JAMAIS (contrat) et neutralise les surrogates.
  M1 — _classify reconnaît les coupures réseau (RemoteProtocolError) comme
       transitoires → retry sur le modèle DEEP avant repli sur le fast.
  M2 — workflows : TELEGRAM_BOT_TOKEN/CHAT_ID câblés (notif push non inerte).
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# C1 — sanitisation récursive
# --------------------------------------------------------------------------- #
def test_deep_sanitize_nested_structures():
    from src.utils.text_sanitize import strip_surrogates_deep

    lone = chr(0xD83C)
    obj = {
        "a": "x" + lone + "y",
        "b": ["ok", {"c": lone}],
        "d": ("e" + lone,),
        "n": 42, "f": 1.5, "none": None, "bool": True,
        lone + "key": "clé empoisonnée aussi",
    }
    out = strip_surrogates_deep(obj)
    # Tout doit être UTF-8-encodable après passage (default=str pour le tuple).
    json.dumps(out, ensure_ascii=False, default=str).encode("utf-8")
    assert out["a"] == "x�y"
    assert out["b"][1]["c"] == "�"
    assert out["d"][0] == "e�"
    assert isinstance(out["d"], tuple)  # type conservé
    assert out["n"] == 42 and out["none"] is None and out["bool"] is True
    assert "�key" in out  # clé nettoyée


def test_deep_sanitize_recombines_split_pairs_in_values():
    from src.utils.text_sanitize import strip_surrogates_deep

    split_emoji = chr(0xD83C) + chr(0xDFB2)  # 🎲 en 2 moitiés
    out = strip_surrogates_deep({"v": "avant " + split_emoji + " après"})
    assert "\U0001f3b2" in out["v"]  # l'emoji est récupéré


def test_strip_surrogates_mixed_pair_and_orphan():
    """Texte MIXTE (paire récupérable + orphelin) : la recombinaison par PAIRE
    récupère l'emoji même si un orphelin traîne ailleurs — l'ancien roundtrip
    UTF-16 global échouait en bloc et détruisait les deux."""
    from src.utils.text_sanitize import strip_surrogates

    pair = chr(0xD83C) + chr(0xDFB2)
    out = strip_surrogates("a" + pair + "b" + chr(0xD800) + "c")
    out.encode("utf-8")
    assert out == "a\U0001f3b2b�c"  # paire recombinée ET orphelin remplacé
    # Ordre inversé low+high = 2 orphelins (pas une paire valide).
    assert strip_surrogates(chr(0xDFB2) + chr(0xD83C)) == "��"
    # Bornes du plan astral : recombinaison arithmétique exacte.
    for cp in (0x10000, 0x10FFFF):
        hi = chr(0xD800 + ((cp - 0x10000) >> 10))
        lo = chr(0xDC00 + ((cp - 0x10000) & 0x3FF))
        assert strip_surrogates(hi + lo) == chr(cp)


# --------------------------------------------------------------------------- #
# C1b — le chemin JSON complet (repro EXACT du run #28)
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


def test_generate_json_escaped_surrogate_is_neutralized():
    """JSON 100% ASCII avec « \\ud83c » échappé : json.loads le décode en vrai
    surrogate — le parse DOIT le neutraliser (repro du crash weekly #28)."""
    from src.ai_brain.gemini_client import GeminiClient

    raw = '{"note": "avant \\ud83c apres", "nested": {"k": ["a\\ud800b"]}}'
    assert raw.isascii()  # preuve : la sanitisation TEXTE ne voit rien
    client = object.__new__(GeminiClient)
    client._client = _fake_client(raw)
    client.fallback_model = None
    client.model_name = "gemini-2.5-flash"
    out = client.generate_json("p")
    # Le dict complet doit être UTF-8-sûr (rendu mail + state + telegram).
    json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert "\ud83c" not in out["note"]
    assert "\ud800" not in out["nested"]["k"][0]


def test_parse_json_fenced_path_also_sanitized():
    """Le chemin de secours (extraction {...} d'un texte pollué) sanitise aussi."""
    from src.ai_brain.gemini_client import GeminiClient

    raw = 'blabla {"x": "a\\udc00z"} fin'
    out = GeminiClient._parse_json(raw)
    json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert out["x"] == "a�z"


# --------------------------------------------------------------------------- #
# C2 — send_email : contrat « ne lève jamais » + mail UTF-8-sûr
# --------------------------------------------------------------------------- #
def _env_mail(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "omar@test.dev")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pass")
    monkeypatch.setenv("RECIPIENT_EMAIL", "omar@test.dev")


class _FakeSMTP:
    last_msg = ""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def sendmail(self, frm, to, msg):
        _FakeSMTP.last_msg = msg


def test_send_email_with_poisoned_html_still_sends(monkeypatch):
    """Repro run #28 : HTML contenant un surrogate isolé → AVANT : MIMEText
    levait (hors try) et tuait le run. APRÈS : mail envoyé, True."""
    import src.reporting.email_sender as es

    _env_mail(monkeypatch)
    monkeypatch.setattr(es.smtplib, "SMTP", _FakeSMTP)
    poisoned = "<p>Bilan " + chr(0xD83C) + " hebdo</p>"
    ok = es.send_email("Sujet " + chr(0xDFB2), poisoned)
    assert ok is True
    # Le message MIME a bien été construit et remis au SMTP (corps en base64).
    assert "multipart/alternative" in _FakeSMTP.last_msg
    import base64
    assert "Bilan" in base64.b64decode(
        _FakeSMTP.last_msg.split("base64")[-1].split("--")[0]
        .replace("\n", "")).decode("utf-8")


def test_send_email_never_raises_even_on_mime_failure(monkeypatch):
    """Toute erreur de CONSTRUCTION (pas seulement SMTP) → False, jamais raise."""
    import src.reporting.email_sender as es

    _env_mail(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("MIME KO")

    monkeypatch.setattr(es, "MIMEText", _boom)
    ok = es.send_email("Sujet", "<p>x</p>")
    assert ok is False  # pas d'exception propagée


# --------------------------------------------------------------------------- #
# M1 — coupures réseau = transitoires (retry deep avant repli fast)
# --------------------------------------------------------------------------- #
def test_classify_network_drops_as_transient():
    from src.ai_brain import gemini_client as gc

    class RemoteProtocolError(Exception):
        pass

    # Message SANS marqueur (le cas réel du 06/07) : seul le NOM du type matche.
    exc = RemoteProtocolError("Server disconnected without response.")
    out = gc._classify(exc)
    assert isinstance(out, gc._GeminiTransientError)

    class ConnectError(Exception):
        pass

    class CloseError(Exception):
        pass

    assert isinstance(gc._classify(ConnectError("x")), gc._GeminiTransientError)
    assert isinstance(gc._classify(CloseError("x")), gc._GeminiTransientError)
    # LocalProtocolError = bug CLIENT : délibérément NON transitoire.

    class LocalProtocolError(Exception):
        pass

    plain_local = LocalProtocolError("bad usage")
    assert gc._classify(plain_local) is plain_local
    # Un quota reste un quota (priorité inchangée).
    assert isinstance(gc._classify(RuntimeError("429 quota exceeded")),
                      gc.GeminiQuotaError)
    # Une erreur quelconque reste inchangée (pas de sur-classification).
    plain = ValueError("mauvais argument")
    assert gc._classify(plain) is plain


# --------------------------------------------------------------------------- #
# M2 — notif push Telegram : passthrough câblé dans les 3 workflows mails
# --------------------------------------------------------------------------- #
def test_workflows_pass_telegram_push_env():
    wf = _ROOT / ".github" / "workflows"
    for name in ("morning_report.yml", "evening_report.yml", "weekly_report.yml"):
        content = (wf / name).read_text(encoding="utf-8")
        assert "TELEGRAM_BOT_TOKEN" in content, (
            f"{name} : push_report_notification serait INERTE (bot non configuré)")
        assert "TELEGRAM_CHAT_ID" in content, name
