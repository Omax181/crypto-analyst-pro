"""Audit 02/10 — l'aperçu V32 (scripts/apercu_v32.py) n'envoie rien et
n'écrit jamais dans state/ : il permet de lire une vraie réponse du modèle aux
prompts V33 AVANT la mise en production."""

from __future__ import annotations

import importlib.util
import os
import pathlib

import src.main as M
from src.state import report_memory as mem

RACINE = pathlib.Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location(
        "apercu_v32", RACINE / "scripts" / "apercu_v32.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sans_cle_gemini_l_apercu_refuse(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _script().main([]) == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_l_apercu_n_envoie_rien_et_n_ecrit_pas_l_etat(monkeypatch, tmp_path):
    apercu = _script()
    reel = tmp_path / "depot"
    (reel / "state").mkdir(parents=True)
    (reel / "state" / "morning_report.json").write_text('{"x": 1}', encoding="utf-8")
    monkeypatch.setattr(apercu, "RACINE", reel)
    monkeypatch.setattr(apercu, "SORTIE", tmp_path / "sortie")
    monkeypatch.setenv("GEMINI_API_KEY", "factice")
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(k, "secret")
    monkeypatch.setattr(M, "send_email", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("envoi réel appelé")))
    vu = {}

    def faux_matin():
        vu["etat"] = mem._STATE_DIR
        vu["env"] = {k: os.environ.get(k) for k in ("GMAIL_USER", "TELEGRAM_BOT_TOKEN")}
        mem._write("morning_report.json", {"ecrit": "par l'aperçu"})
        M.send_email("☀️ sujet", '<img src="cid:graph">', {"graph": b"\x89PNG"})
        M._push_telegram_notification({}, "morning")
        return 0

    monkeypatch.setattr(M, "run_morning", faux_matin)
    monkeypatch.chdir(tmp_path)
    assert apercu.main(["matin"]) == 0
    assert vu["etat"] != reel / "state"                     # copie temporaire
    assert vu["env"] == {"GMAIL_USER": None, "TELEGRAM_BOT_TOKEN": None}
    assert (reel / "state" / "morning_report.json").read_text(encoding="utf-8") == '{"x": 1}'
    html = (tmp_path / "sortie" / "matin.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in html and "cid:graph" not in html
    assert (tmp_path / "sortie" / "matin.telegram.txt").exists()
    assert apercu.main(["lundi"]) == 2


def test_le_dossier_d_apercu_est_ignore_par_git():
    assert "apercu_v32/" in (RACINE / ".gitignore").read_text(encoding="utf-8").split()
