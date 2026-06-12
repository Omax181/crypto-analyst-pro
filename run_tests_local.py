#!/usr/bin/env python3
"""Runner de tests maison (environnement Claude SANS réseau ni pytest).

- Stub `tenacity` (décorateur retry no-op) et `google.genai` AVANT tout import
  de src/ : ces libs ne sont pas installables ici (réseau coupé). Chez Omar,
  pytest tourne avec les vraies libs — ce runner ne sert qu'à valider la
  LOGIQUE en local Claude.
- Émule les fixtures pytest utilisées par la suite : monkeypatch (setattr objet
  ou chemin string, setenv/delenv), tmp_path, caplog minimal.
- Usage : python3 run_tests_local.py tests/test_v15.py [tests/test_x.py ...]
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import tempfile
import traceback
import types
from pathlib import Path

# ── Stub tenacity ────────────────────────────────────────────────────────────
ten = types.ModuleType("tenacity")


def _retry(*dargs, **dkwargs):
    # @retry sans parenthèses OU @retry(...) → no-op dans les deux formes.
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]
    def deco(fn):
        return fn
    return deco


ten.retry = _retry
ten.stop_after_attempt = lambda *a, **k: None
ten.wait_exponential = lambda *a, **k: None
ten.retry_if_exception_type = lambda *a, **k: None
ten.before_sleep_log = lambda *a, **k: (lambda *aa, **kk: None)
sys.modules.setdefault("tenacity", ten)

# ── Stub pytest minimal (certains tests font `import pytest`) ───────────────
if "pytest" not in sys.modules:
    pt = types.ModuleType("pytest")

    class _Approx:
        def __init__(self, expected, rel=None, abs=None):
            self.expected, self.rel, self.abs = expected, rel, abs
        def __eq__(self, other):
            tol = self.abs if self.abs is not None else (
                (self.rel if self.rel is not None else 1e-6) * abs(self.expected) + 1e-12)
            return abs(other - self.expected) <= tol
        def __repr__(self):
            return f"approx({self.expected})"

    class _Raises:
        def __init__(self, exc, match=None):
            self.exc, self.match = exc, match
        def __enter__(self):
            return self
        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"{self.exc} non levée")
            ok = issubclass(et, self.exc)
            if ok and self.match:
                import re as _re
                ok = bool(_re.search(self.match, str(ev)))
            return ok

    class _Mark:
        def __getattr__(self, name):
            def deco(*a, **k):
                if len(a) == 1 and callable(a[0]) and not k:
                    return a[0]
                return lambda f: f
            return deco

    pt.approx = lambda expected, rel=None, abs=None: _Approx(expected, rel, abs)
    pt.raises = lambda exc, match=None: _Raises(exc, match)
    pt.mark = _Mark()
    pt.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    class _Skip(Exception):
        pass
    pt.skip = lambda reason="": (_ for _ in ()).throw(_Skip(reason))
    pt.SkipTest = _Skip
    sys.modules["pytest"] = pt

# ── Stub google.genai (gemini_client l'importe au chargement) ───────────────
try:
    import google.genai  # noqa: F401 — présent ? on garde le vrai
except Exception:
    g = types.ModuleType("google")
    gg = types.ModuleType("google.genai")
    gg.Client = object
    gtypes = types.ModuleType("google.genai.types")
    class _Any:  # attribut magique pour types.* utilisés en annotations/config
        def __getattr__(self, name):
            return _Any()
        def __call__(self, *a, **k):
            return _Any()
    gg.types = _Any()
    g.genai = gg
    sys.modules.setdefault("google", g)
    sys.modules["google.genai"] = gg
    sys.modules["google.genai.types"] = gtypes

sys.path.insert(0, str(Path(__file__).parent))


class _MonkeyPatch:
    def __init__(self):
        self._attrs: list[tuple] = []
        self._env: list[tuple] = []

    def setattr(self, target, name=None, value=None, raising=True):
        if isinstance(target, str) and value is None and name is not None:
            # forme monkeypatch.setattr("mod.attr", value) — name=value ici
            target, attr, value = target.rsplit(".", 1)[0], target.rsplit(".", 1)[1], name
            mod = importlib.import_module(target)
            self._attrs.append((mod, attr, getattr(mod, attr, None), hasattr(mod, attr)))
            setattr(mod, attr, value)
            return
        self._attrs.append((target, name, getattr(target, name, None), hasattr(target, name)))
        setattr(target, name, value)

    def setitem(self, mapping, key, value):
        sentinel = object()
        old = mapping.get(key, sentinel)
        self._attrs.append((mapping, key, old, old is not sentinel, "item"))
        mapping[key] = value

    def setenv(self, k, v):
        self._env.append((k, os.environ.get(k)))
        os.environ[k] = v

    def delenv(self, k, raising=True):
        self._env.append((k, os.environ.get(k)))
        os.environ.pop(k, None)

    def undo(self):
        for entry in reversed(self._attrs):
            if len(entry) == 5 and entry[4] == "item":
                mapping, key, old, existed, _ = entry
                if existed:
                    mapping[key] = old
                else:
                    mapping.pop(key, None)
                continue
            obj, name, old, existed = entry
            if existed:
                setattr(obj, name, old)
            else:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
        for k, old in reversed(self._env):
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def run_file(path: str) -> tuple[int, int, int]:
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        print(f"COLLECT ERROR {path}")
        traceback.print_exc(limit=4)
        return 0, 1, 0
    ok = fail = skip = 0
    for name, fn in sorted(inspect.getmembers(mod, inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        params = inspect.signature(fn).parameters
        kwargs = {}
        mp = _MonkeyPatch()
        tmp = None
        unsupported = [p for p in params
                       if p not in ("monkeypatch", "tmp_path")]
        if unsupported:
            skip += 1
            print(f"SKIP {name} (fixtures non émulées : {unsupported})")
            continue
        if "monkeypatch" in params:
            kwargs["monkeypatch"] = mp
        if "tmp_path" in params:
            tmp = tempfile.TemporaryDirectory()
            kwargs["tmp_path"] = Path(tmp.name)
        try:
            fn(**kwargs)
            ok += 1
            print(f"PASS {name}")
        except Exception:
            fail += 1
            print(f"FAIL {name}")
            traceback.print_exc(limit=5)
        finally:
            mp.undo()
            if tmp:
                tmp.cleanup()
    return ok, fail, skip


if __name__ == "__main__":
    files = sys.argv[1:] or ["tests/test_v15.py"]
    t_ok = t_fail = t_skip = 0
    for f in files:
        print(f"\n══════ {f} ══════")
        a, b, c = run_file(f)
        t_ok += a; t_fail += b; t_skip += c
    print(f"\nTOTAL : {t_ok} passed · {t_fail} failed · {t_skip} skipped")
    sys.exit(1 if t_fail else 0)
