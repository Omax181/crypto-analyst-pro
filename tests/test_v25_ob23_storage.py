# -*- coding: utf-8 -*-
"""OB23 — durcissement du stockage JSON de state (écriture atomique + validation
de type au chargement). Redirigé vers un dossier temporaire (aucun toucher au
vrai state)."""

from __future__ import annotations

import pytest

from src.state import report_memory as mem

# Des suites héritées (test_v14_features, test_v15) remplacent mem._read/_write
# par des lambdas in-memory SANS restauration → fuite qui écraserait le vrai I/O
# ici. On capture les VRAIES fonctions à l'import (avant toute exécution de test)
# et on les restaure dans la fixture (monkeypatch les ré-annule proprement).
_REAL_READ = mem._read
_REAL_WRITE = mem._write


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(mem, "_read", _REAL_READ)
    monkeypatch.setattr(mem, "_write", _REAL_WRITE)
    return tmp_path


# ── écriture atomique + round-trip ─────────────────────────────────────────
def test_roundtrip_list(state_dir):
    mem._write("t_list.json", [1, 2, 3])
    assert mem._read("t_list.json", []) == [1, 2, 3]


def test_roundtrip_dict(state_dir):
    mem._write("t_dict.json", {"a": 1, "b": [2, 3]})
    assert mem._read("t_dict.json", {}) == {"a": 1, "b": [2, 3]}


def test_atomic_write_leaves_no_tmp(state_dir):
    mem._write("t_atom.json", {"ok": 1})
    assert not list(state_dir.glob(".*.tmp"))     # aucun temporaire résiduel
    assert (state_dir / "t_atom.json").exists()


# ── OB23 : validation de type au chargement ────────────────────────────────
def test_type_guard_dict_when_list_expected(state_dir):
    mem._write("t_c1.json", {"a": 1})             # fichier = dict
    assert mem._read("t_c1.json", []) == []       # défaut list → mismatch → défaut


def test_type_guard_list_when_dict_expected(state_dir):
    mem._write("t_c2.json", [1, 2])
    assert mem._read("t_c2.json", {}) == {}


def test_none_default_accepts_any_type(state_dir):
    mem._write("t_any.json", {"x": 1})
    assert mem._read("t_any.json", None) == {"x": 1}


def test_same_type_is_kept(state_dir):
    mem._write("t_ok.json", [{"k": "v"}])
    assert mem._read("t_ok.json", []) == [{"k": "v"}]


# ── tolérance : fichier absent / JSON corrompu ─────────────────────────────
def test_missing_file_returns_default(state_dir):
    assert mem._read("absent.json", {"d": 1}) == {"d": 1}


def test_corrupt_json_returns_default(state_dir):
    mem._path("t_bad.json").write_text("{not valid json", encoding="utf-8")
    assert mem._read("t_bad.json", []) == []
