#!/usr/bin/env python3
"""_step_recorder.py — record the code a step ACTUALLY RAN (R-0924-3 r5).

WHY A RECORDER AND NOT A BETTER STATIC CLOSURE
----------------------------------------------
Four review rounds found the same hole class in a static AST closure, and the
fourth said the quiet part: Python's dynamism guarantees a fifth. Measured
escapes from r4's closure, every one a real path into a shipped artefact:

  * ``from routing_layer_upper_bound import … as _rub`` — recorded as a member
    named ``_rub`` that does not exist, so it was DROPPED WITH NO NOTE, and
    ``set_routing_layers`` shapes the routed DEF;
  * ``pdn_ring_dimensions as _pdn_ring_dimensions`` — the PDN ring;
  * ``importlib.import_module("gds_port_label_check")`` — the GDS label
    restore, invisible to any import-graph walk;
  * ``PdkConfig`` built in ``main()`` by ``_detect_pdk`` ->
    ``_pdk_config_from_registry`` -> ``_derive_tapcell_master``: outside every
    seed closure, so a ``pdk_registry.json`` edit changed the DEF with all four
    identity components unchanged.

A closure over the source can only ever approximate what the interpreter does.
So this stops approximating: the step RUNS, and what ran is recorded.

WHAT IS RECORDED
----------------
``{relative file: {"<name>@<firstlineno>": sha256(source of that function)}}``
plus, per file, ``"__module__"`` — the sha256 of that module's TOP-LEVEL
NON-def statements. The second half matters as much as the first: constants,
compiled regex tables, dict dispatch tables and imports are not functions and
never "run" as a call, yet changing one changes what every function in the
file does.

COST
----
``sys.setprofile`` fires on every Python call, so the hook is the smallest
thing that can work: one set lookup for a code object already seen, and only
then a filename test. Steady state after the first call of each function is a
single ``set.__contains__``. (``sys.monitoring`` with per-code ``DISABLE``
would be cheaper still, but the runner executes on a host at Python 3.10 where
it does not exist; this degrades to the same recording on either.)

FAILS CLOSED
------------
If the profiler cannot be installed, or another profiler already owns the
hook, the recorder reports that it recorded NOTHING — and a step whose code
could not be recorded gets no cache. An empty recording is never treated as
"this step ran no code".
"""
from __future__ import annotations

import ast
import hashlib
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

#: Key under which a file's top-level non-def body hash is stored.
MODULE_BODY = "__module__"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Recorder:
    """Collect the plugin code objects executed inside a ``with`` block."""

    def __init__(self, root: Path):
        self._root = str(Path(root).resolve()) + "/"
        self._seen: Set[Tuple[str, str, int]] = set()
        self._hits: Set[Tuple[str, str, int]] = set()
        self._plugin: Dict[str, bool] = {}
        self._prev = None
        self._installed = False
        self.why_not = ""

    # -- the hook, kept deliberately small ---------------------------------
    def _hook(self, frame, event, arg):
        if event != "call":
            return
        code = frame.f_code
        key = (code.co_filename, code.co_name, code.co_firstlineno)
        if key in self._seen:
            return
        self._seen.add(key)
        mine = self._plugin.get(code.co_filename)
        if mine is None:
            mine = code.co_filename.startswith(self._root)
            self._plugin[code.co_filename] = mine
        if mine and code.co_name != "<module>":
            # A module's own top-level code object is covered by
            # `MODULE_BODY`; recording it as a "function" would only add a
            # permanently-ABSENT key.
            self._hits.add(key)

    def __enter__(self) -> "Recorder":
        try:
            self._prev = sys.getprofile()
            if self._prev is not None:
                # Another profiler owns the hook. Displacing it would break
                # whatever installed it; recording nothing and saying so is
                # the honest outcome.
                self.why_not = ("another profiler is installed, so this step's "
                                "code could not be recorded")
                return self
            sys.setprofile(self._hook)
            threading.setprofile(self._hook)
            self._installed = True
        except Exception as exc:  # noqa: BLE001 — never break the step
            self.why_not = (f"the profiler could not be installed "
                            f"({type(exc).__name__}: {exc})")
        return self

    def __exit__(self, *_exc) -> None:
        if self._installed:
            try:
                sys.setprofile(self._prev)
                threading.setprofile(None)
            except Exception:  # noqa: BLE001
                pass
        self._installed = False

    # -- the recording -----------------------------------------------------
    def recorded(self) -> Tuple[Optional[Dict[str, Dict[str, str]]], str]:
        """``(record, why_not)``. ``None`` when nothing could be recorded."""
        if self.why_not:
            return None, self.why_not
        if not self._hits:
            return None, ("no plugin code was recorded for this step, so what "
                          "it ran cannot be established")
        by_file: Dict[str, List[Tuple[str, int]]] = {}
        for fname, name, lineno in self._hits:
            by_file.setdefault(fname, []).append((name, lineno))
        out: Dict[str, Dict[str, str]] = {}
        for fname, entries in by_file.items():
            digests, err = source_digests(Path(fname), entries)
            if err:
                # A file we ran but cannot re-read is not a file we can prove
                # unchanged.
                return None, err
            try:
                rel = str(Path(fname).resolve().relative_to(self._root[:-1]))
            except (OSError, ValueError):
                rel = Path(fname).name
            out[rel] = digests
        return out, ""


def _segment(lines: List[str], node: ast.AST) -> Optional[str]:
    lo = getattr(node, "lineno", None)
    hi = getattr(node, "end_lineno", None)
    if lo is None or hi is None:
        return None
    for dec in getattr(node, "decorator_list", ()) or ():
        dlo = getattr(dec, "lineno", None)
        if dlo is not None and dlo < lo:
            lo = dlo
    return "".join(lines[lo - 1:hi])


def _function_index(tree: ast.AST, lines: List[str]
                    ) -> Dict[Tuple[str, int], str]:
    """``{(name, firstlineno): source}`` for every function at any depth.

    Keyed the way a code object names itself, so a recording can be looked up
    without re-deriving scope."""
    out: Dict[Tuple[str, int], str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = _segment(lines, node)
            if seg is not None:
                # `co_firstlineno` points at the `def`, decorators excluded.
                out[(node.name, node.lineno)] = seg
        elif isinstance(node, ast.Lambda):
            seg = _segment(lines, node)
            if seg is not None:
                out.setdefault(("<lambda>", node.lineno), seg)
    return out


def module_body_digest(tree: ast.AST, lines: List[str]) -> str:
    """sha256 of a module's TOP-LEVEL NON-def statements.

    Constants, compiled regex tables, dict dispatch tables and imports are not
    functions and never appear as a call, yet changing one changes what every
    function in that file does. A recorder that watched only calls would be
    blind to exactly the tuning constants earlier rounds had to special-case."""
    parts: List[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        seg = _segment(lines, node)
        if seg is not None:
            parts.append(seg)
    return _sha("".join(parts).encode("utf-8"))


def source_digests(path: Path, entries) -> Tuple[Dict[str, str],
                                                 Optional[str]]:
    """``({"<name>@<lineno>": sha}, error)`` for the named functions in
    ``path``, plus the module-body digest."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"{path} ran but could not be re-read ({exc})"
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {}, f"{path} ran but could not be parsed ({exc})"
    lines = text.splitlines(keepends=True)
    index = _function_index(tree, lines)
    out: Dict[str, str] = {MODULE_BODY: module_body_digest(tree, lines)}
    for name, lineno in sorted(entries):
        src = index.get((name, lineno))
        if src is None:
            # Recorded as executed, absent from the current source: the code
            # that ran is not the code on disk. That is the strongest possible
            # reason to re-run, so it is reported as a value, not skipped.
            out[f"{name}@{lineno}"] = "ABSENT"
            continue
        out[f"{name}@{lineno}"] = _sha(src.encode("utf-8"))
    return out, None


def rederive(record: Dict[str, Dict[str, str]], root: Path
             ) -> Tuple[Optional[Dict[str, Dict[str, str]]], str]:
    """Recompute a recording's keys against the CURRENT source.

    Same shape in, same shape out, so the caller compares two dicts rather
    than trusting this to decide anything."""
    root = Path(root)
    out: Dict[str, Dict[str, str]] = {}
    for rel, entries in record.items():
        path = root / rel
        want = []
        for key in entries:
            if key == MODULE_BODY:
                continue
            name, _, lineno = key.rpartition("@")
            try:
                want.append((name, int(lineno)))
            except ValueError:
                return None, f"unreadable recording key {key!r} for {rel}"
        digests, err = source_digests(path, want)
        if err:
            return None, err
        out[rel] = digests
    return out, ""


def digest_of(record: Dict[str, Dict[str, str]]) -> str:
    """One digest over a recording, order-independent."""
    h = hashlib.sha256()
    for rel in sorted(record):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        for key in sorted(record[rel]):
            h.update(key.encode("utf-8"))
            h.update(b"=")
            h.update(record[rel][key].encode("utf-8"))
            h.update(b";")
        h.update(b"\n")
    return h.hexdigest()
