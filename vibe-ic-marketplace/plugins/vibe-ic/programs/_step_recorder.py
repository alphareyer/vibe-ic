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
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

#: Key under which a file's top-level non-def body hash is stored.
MODULE_BODY = "__module__"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


#: Any token that names a Python script, wherever it appears — a bare argv
#: element, inside a `bash -lc "…"` string, or after klayout's `-r` / `-rm`.
_SCRIPT_RE = re.compile(r"[^\s\"\'=]+\.py\b")

#: Environment that CHOOSES which engine runs, rather than what it runs on.
#: `$VIBEIC_KLAYOUT_TOOLS` points `_klayout_launch` at a fork checkout, so the
#: same recipe can execute entirely different code (r5 review finding 2).
ENGINE_ENV = ("VIBEIC_KLAYOUT_TOOLS",)


def _static_imports(path: Path, root: Path, seen=None) -> List[Path]:
    """``path`` plus the in-tree modules it statically imports, transitively.

    A launched script runs in ANOTHER PROCESS, so the profiler never sees a
    single one of its calls. There is nothing to be clever with: the file is
    taken WHOLE, and so is everything it imports from this tree. Coarse, and
    honestly so — the alternative is not seeing it at all, which is what r5
    measured."""
    seen = seen if seen is not None else set()
    out: List[Path] = []
    try:
        rp = path.resolve()
    except OSError:
        return out
    if rp in seen or not path.is_file():
        return out
    seen.add(rp)
    out.append(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return out
    for node in ast.walk(tree):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        for dotted in names:
            cand = Path(root).joinpath(*dotted.split("."))
            for q in (cand.with_suffix(".py"), cand / "__init__.py"):
                if q.is_file():
                    out.extend(_static_imports(q, root, seen))
    return out


class Recorder:
    """Collect the plugin code objects executed inside a ``with`` block."""

    def __init__(self, root: Path):
        self._root = str(Path(root).resolve()) + "/"
        self._seen: Set[Tuple[str, str, int]] = set()
        self._hits: Set[Tuple[str, str, int]] = set()
        self._plugin: Dict[str, bool] = {}
        self._prev = None
        self._installed = False
        self._prev_popen = None
        self._launched: Set[str] = set()
        self._engine_env: Dict[str, str] = {}
        self.why_not = ""

    def _note_launch(self, argv) -> None:
        """Record any PLUGIN SCRIPT this step launches out of process.

        r5 review finding 2: `sys.setprofile` cannot see another process, so
        `metal_fill/metal_fill.py` under klayout, `die_finishing_gen`'s seal
        ring, `pad_assignment_gen` / `pad_ring_gen` via `_docker_exec python3`
        and `decap_route_short_guard` all ran with nothing recorded. Rather
        than enumerate launch sites — the mistake of rounds 1 to 5 — this
        watches the ONE place every launch passes through."""
        try:
            text = argv if isinstance(argv, str) else " ".join(
                str(x) for x in argv)
        except Exception:  # noqa: BLE001
            return
        for tok in _SCRIPT_RE.findall(text):
            self._launched.add(tok)
        for name in ENGINE_ENV:
            if name in text or name in os.environ:
                self._engine_env[name] = os.environ.get(name, "")

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
            # This module is INSTRUMENTATION, not the step's code. Recording
            # its own `_WatchedPopen` made every recording unnameable.
            mine = (code.co_filename.startswith(self._root)
                    and code.co_filename != __file__)
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
            self._prev_popen = subprocess.Popen
            _rec = self

            class _WatchedPopen(self._prev_popen):  # type: ignore[misc]
                def __init__(self, args, *a, **k):
                    _rec._note_launch(args)
                    super().__init__(args, *a, **k)

            subprocess.Popen = _WatchedPopen   # type: ignore[assignment]
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
            try:
                if self._prev_popen is not None:
                    subprocess.Popen = self._prev_popen  # type: ignore
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
            digests, err = stamp_digests(Path(fname), entries)
            if err:
                # A file we ran but cannot re-read is not a file we can prove
                # unchanged.
                return None, err
            try:
                rel = str(Path(fname).resolve().relative_to(self._root[:-1]))
            except (OSError, ValueError):
                rel = Path(fname).name
            out[rel] = digests
        launched, why = self._launched_digests()
        if why:
            return None, why
        out.update(launched)
        if self._engine_env:
            out["__engine_env__"] = {
                k: _sha(v.encode("utf-8"))
                for k, v in sorted(self._engine_env.items())}
        return out, ""

    def _resolve_script(self, tok: str) -> Optional[Path]:
        """Where a launched script token lives, if it is one of ours."""
        root = Path(self._root[:-1])
        for cand in (Path(tok), root / tok, root / Path(tok).name):
            try:
                if cand.is_file() and cand.resolve().is_relative_to(root):
                    return cand
            except (OSError, ValueError):
                continue
        return None

    def _launched_digests(self) -> Tuple[Dict[str, Dict[str, str]], str]:
        """Whole-file digests for every PLUGIN script launched out of process,
        plus its static import closure.

        A token that names a plugin script we cannot READ is a refusal: an
        unattributable plugin launch means this kind gets no cache, which is
        the rule the review asked for."""
        root = Path(self._root[:-1])
        out: Dict[str, Dict[str, str]] = {}
        for tok in sorted(self._launched):
            path = self._resolve_script(tok)
            if path is None:
                # Not ours (a system script, or a name that resolves nowhere
                # in this tree). Only a PLUGIN launch has to be attributable.
                if (root / Path(tok).name).suffix == ".py" and \
                        (root / Path(tok).name).exists():
                    return {}, (f"a plugin script was launched as {tok!r} and "
                                f"could not be read, so what this step ran "
                                f"out of process cannot be established")
                continue
            for q in _static_imports(path, root):
                d = _sha256_file(q)
                if d is None:
                    return {}, (f"{q} was launched (or imported by a launched "
                                f"script) and could not be read")
                try:
                    rel = str(q.resolve().relative_to(root))
                except (OSError, ValueError):
                    rel = q.name
                out.setdefault(f"launched:{rel}", {})["__whole__"] = d
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


#: Code objects Python names for a comprehension or a lambda. They are not
#: functions anyone edits on their own, and on 3.10 a `<listcomp>` carries the
#: line it appears on — so keying them separately made an edit anywhere above
#: turn them ABSENT. They FOLD into the function that encloses them.
_ANON = ("<lambda>", "<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>")


def _qualnames(tree: ast.AST, lines: List[str]
               ) -> Tuple[Dict[int, str], Dict[str, str]]:
    """``({code-object start line: qualname}, {qualname: source})``.

    KEYED BY QUALNAME, NEVER BY LINE NUMBER — r5 review finding 4. A key
    carrying a line number turns every function below an inserted line into
    ABSENT, which degrades the whole file to one bit of granularity: exactly
    run23's symptom. Line numbers appear here only to map a CODE OBJECT back
    to its definition at record time, and never leave this module.

    The line recorded is the code object's OWN first line, which for a
    DECORATED function is its first DECORATOR (r5 review finding 3: keying on
    the `def` line made those ABSENT at stamp AND at check, so ABSENT ==
    ABSENT and a body edit was never detected). The hashed source INCLUDES the
    decorators, because a changed decorator changes the function."""
    by_line: Dict[int, str] = {}
    src: Dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                seg = _segment(lines, child)
                if seg is not None:
                    src[qual] = seg
                start = child.lineno
                for dec in child.decorator_list or ():
                    dlo = getattr(dec, "lineno", None)
                    if dlo is not None and dlo < start:
                        start = dlo
                by_line[start] = qual
                walk(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                # A class body is a code object named after the class, and it
                # RUNS at definition time — so it needs a name here or a
                # recording that touches one cannot be resolved at all.
                qual = f"{prefix}{child.name}"
                seg = _segment(lines, child)
                if seg is not None:
                    src[qual] = seg
                start = child.lineno
                for dec in child.decorator_list or ():
                    dlo = getattr(dec, "lineno", None)
                    if dlo is not None and dlo < start:
                        start = dlo
                by_line[start] = qual
                walk(child, f"{qual}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return by_line, src


def _enclosing(by_line: Dict[int, str], lineno: int) -> Optional[str]:
    """The qualname whose code object starts at or above ``lineno``.

    Used only to FOLD an anonymous code object (a comprehension, a lambda)
    into the function it lives in."""
    best = None
    for start, qual in by_line.items():
        if start <= lineno and (best is None or start > best[0]):
            best = (start, qual)
    return best[1] if best else None


def module_body_digest(tree: ast.AST, lines: List[str]) -> str:
    """sha256 of a module's NON-def statements, INCLUDING class bodies.

    Constants, compiled regex tables, dict dispatch tables and imports are not
    functions and never appear as a call, yet changing one changes what every
    function in that file does. r5 review finding 3 added the other half:
    statements in a CLASS body are in no function either, and were in no
    digest at all.

    The TEXT is hashed, not the positions (r5 review finding 4): each
    statement is stripped and joined, so inserting a blank line above a
    constant does not read as a change to it."""
    parts: List[str] = []

    def collect(body) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.ClassDef):
                collect(node.body)
                continue
            seg = _segment(lines, node)
            if seg is not None:
                parts.append("\n".join(x.strip() for x in seg.splitlines()
                                        if x.strip()))
    collect(getattr(tree, "body", []))
    return _sha("\n".join(parts).encode("utf-8"))


def _parse(path: Path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return None, None, f"{path} could not be read ({exc})"
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return None, None, f"{path} could not be parsed ({exc})"
    return tree, text.splitlines(keepends=True), None


def stamp_digests(path: Path, entries) -> Tuple[Dict[str, str],
                                                Optional[str]]:
    """``({qualname: sha}, error)`` for code objects observed RUNNING.

    An entry that cannot be resolved to a definition in the file it came from
    is a REFUSAL, not a stored value (r5 review finding 3): storing ABSENT at
    stamp time made it compare equal to the ABSENT the check re-derives, so
    the function's body could change for ever without being noticed."""
    tree, lines, err = _parse(path)
    if err:
        return {}, f"{path} ran but {err}"
    by_line, src = _qualnames(tree, lines)
    out: Dict[str, str] = {MODULE_BODY: module_body_digest(tree, lines)}
    for name, lineno in sorted(entries):
        qual = by_line.get(lineno)
        if qual is None and name in _ANON:
            qual = _enclosing(by_line, lineno)
        if qual is None or qual not in src:
            return {}, (f"{path}: a code object that RAN ({name} at line "
                        f"{lineno}) has no definition in this file, so what "
                        f"ran cannot be named")
        out[qual] = _sha(src[qual].encode("utf-8"))
    return out, None


def check_digests(path: Path, qualnames) -> Tuple[Dict[str, str],
                                                  Optional[str]]:
    """``({qualname: sha}, error)`` for the SAME names, from CURRENT source.

    A name that has GONE gets the value ``ABSENT``; here that is an honest
    answer and it differs from any hash, so the step re-runs."""
    tree, lines, err = _parse(path)
    if err:
        return {}, err
    _by_line, src = _qualnames(tree, lines)
    out: Dict[str, str] = {MODULE_BODY: module_body_digest(tree, lines)}
    for qual in sorted(qualnames):
        seg = src.get(qual)
        out[qual] = "ABSENT" if seg is None else _sha(seg.encode("utf-8"))
    return out, None


def rederive(record: Dict[str, Dict[str, str]], root: Path
             ) -> Tuple[Optional[Dict[str, Dict[str, str]]], str]:
    """Recompute a recording's keys against the CURRENT source."""
    root = Path(root)
    out: Dict[str, Dict[str, str]] = {}
    for rel, entries in record.items():
        if rel == "__engine_env__":
            # The environment that PICKS the engine is a fact about now, and
            # the caller re-supplies it; carried forward unchanged so a
            # comparison is against the recorded value.
            out[rel] = dict(entries)
            continue
        if rel.startswith("launched:"):
            q = root / rel[len("launched:"):]
            d = _sha256_file(q)
            out[rel] = {"__whole__": d if d is not None else "ABSENT"}
            continue
        quals = [k for k in entries if k != MODULE_BODY]
        digests, err = check_digests(root / rel, quals)
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
