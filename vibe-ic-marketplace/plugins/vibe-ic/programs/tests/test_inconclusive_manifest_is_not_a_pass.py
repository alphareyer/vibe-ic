#!/usr/bin/env python3
"""No consumer of the MCP result manifest may read `INCONCLUSIVE` as a pass.

The manifest `status` field became three-state — PASS / FAIL / INCONCLUSIVE —
when `writeManifest` started refusing to record a PASS whose proving metrics are
absent (mcp-eda/src/lib/manifest_metrics.mjs). Introducing a third state moves
the defect rather than fixing it unless every reader is updated: a reader that
tests `status != "FAIL"` to mean "passed" now silently accepts a run that
measured nothing, which is exactly the bug the third state was added to stop.

Two programs read that manifest. This file pins both, at both poles, plus a
source-level guard that a future reader cannot reintroduce the `!= "FAIL"`
shape in either of them.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

PROGRAMS = Path(__file__).resolve().parent.parent
VERIFY = PROGRAMS / "mcp_execution_verify.py"
ATTEST = PROGRAMS / "fpga_program_chain_attest_check.py"


def _ts(hours_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _manifest(tmp_path: Path, entries: list) -> Path:
    p = tmp_path / "latest_results.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return p


def _verify(manifest: Path, steps: str):
    r = _pr.run(
        [sys.executable, str(VERIFY), "--manifest", str(manifest),
         "--require-steps", steps],
        capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


def _attest(manifest: Path):
    r = _pr.run(
        [sys.executable, str(ATTEST), "--manifest", str(manifest)],
        capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── consumer 1: mcp_execution_verify.py ────────────────────────────────────

def test_verify_inconclusive_is_not_a_pass(tmp_path: Path) -> None:
    """RED POLE. The STA step ran, reported success, and recorded no wns/tns.
    That must not earn a pass."""
    m = _manifest(tmp_path, [
        {"timestamp": _ts(1), "step": "sta", "status": "INCONCLUSIVE",
         "tool": "OpenSTA", "wns": None, "tns": None,
         "missing_metrics": ["wns", "tns"]},
    ])
    rc, rep = _verify(m, "sta")
    assert rc == 1
    assert rep["summary"]["verdict"] != "PASS"
    assert rep["summary"]["found_pass"] == 0
    assert rep["summary"]["found_inconclusive"] == 1
    assert rep["results"][0]["status"] == "FOUND_INCONCLUSIVE"


def test_verify_inconclusive_is_not_a_failure_either(tmp_path: Path) -> None:
    """...and it must not be reported as a failing design. Unmeasured is its
    own answer; calling it FAIL is the same lie pointed the other way."""
    m = _manifest(tmp_path, [
        {"timestamp": _ts(1), "step": "sta", "status": "INCONCLUSIVE"},
    ])
    rc, rep = _verify(m, "sta")
    assert rep["summary"]["verdict"] == "INCONCLUSIVE"
    assert rep["summary"]["found_fail"] == 0
    assert rc == 1  # still not permission to proceed


def test_verify_a_clean_run_is_still_a_pass(tmp_path: Path) -> None:
    """GREEN POLE — the control that proves this is not a refusal machine."""
    m = _manifest(tmp_path, [
        {"timestamp": _ts(2), "step": "synthesis", "status": "PASS",
         "tool": "Yosys", "cells": 2827},
        {"timestamp": _ts(1), "step": "sta", "status": "PASS",
         "tool": "OpenSTA", "wns": 0.0, "tns": 0.0},
    ])
    rc, rep = _verify(m, "synthesis,sta")
    assert rc == 0
    assert rep["summary"]["verdict"] == "PASS"
    assert rep["summary"]["found_inconclusive"] == 0


def test_verify_a_real_failure_is_still_a_failure(tmp_path: Path) -> None:
    """CONTROL — adding a third state must not blind the second one."""
    m = _manifest(tmp_path, [
        {"timestamp": _ts(1), "step": "drc", "status": "FAIL",
         "tool": "KLayout", "violations": 41},
    ])
    rc, rep = _verify(m, "drc")
    assert rc == 1
    assert rep["summary"]["verdict"] == "FAIL"
    assert rep["results"][0]["status"] == "FOUND_FAIL"


def test_verify_a_mixed_run_reports_the_failure(tmp_path: Path) -> None:
    """A FAIL anywhere outranks an INCONCLUSIVE: the run is known-bad."""
    m = _manifest(tmp_path, [
        {"timestamp": _ts(2), "step": "sta", "status": "INCONCLUSIVE"},
        {"timestamp": _ts(1), "step": "drc", "status": "FAIL", "violations": 3},
    ])
    rc, rep = _verify(m, "sta,drc")
    assert rc == 1
    assert rep["summary"]["verdict"] == "FAIL"
    assert rep["summary"]["found_inconclusive"] == 1


# ── consumer 2: fpga_program_chain_attest_check.py ─────────────────────────

def _chain(compile_status: str, program_status: str) -> list:
    sid = "sess-1"
    return [
        {"timestamp": _ts(3), "step": "fpga_compile", "status": compile_status,
         "session_id": sid, "compiled_artifact_sha256": "a" * 64},
        {"timestamp": _ts(2), "step": "fpga_program", "status": program_status,
         "session_id": sid, "programmed_artifact_sha256": "a" * 64,
         "compile_artifact_sha256": "a" * 64, "program_matches_compile": True},
    ]


def test_attest_inconclusive_compile_does_not_link_the_chain(
        tmp_path: Path) -> None:
    """RED POLE. An unproven compile is not a compile: it may not carry a
    hardware-attestation claim."""
    m = _manifest(tmp_path, _chain("INCONCLUSIVE", "PASS"))
    rc, out = _attest(m)
    assert rc != 0
    assert "INCONCLUSIVE" in out


def test_attest_a_real_chain_still_passes(tmp_path: Path) -> None:
    """GREEN POLE — the control."""
    m = _manifest(tmp_path, _chain("PASS", "PASS"))
    rc, out = _attest(m)
    assert rc == 0, out


# ── the guard: neither reader may use the two-state shape ──────────────────

FORBIDDEN = ('!= "FAIL"', "!= 'FAIL'", '!== "FAIL"', "not in (\"FAIL\"",
             "not in ('FAIL'")


def _executable_lines(path: Path):
    """(lineno, source) for lines that are executable code — docstrings and
    comments stripped, so prose ABOUT the forbidden shape does not trip the
    guard while code USING it does."""
    src = path.read_text()
    tree = ast.parse(src)
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            prose.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    for n, line in enumerate(src.splitlines(), 1):
        if n in prose:
            continue
        yield n, line.split("#", 1)[0]


def test_no_manifest_reader_treats_non_fail_as_a_pass() -> None:
    """Source guard on every program that reads latest_results.jsonl. With a
    three-state status, `!= "FAIL"` means "PASS or INCONCLUSIVE", i.e. it reads
    an unmeasured run as a passing one. Test `== "PASS"` instead.

    Scope note: this guard covers the MCP result-manifest readers only. Other
    verdict systems in this repo carry their own vocabularies and never receive
    a value written by writeManifest."""
    readers = [VERIFY, ATTEST]
    for reader in readers:
        assert reader.is_file(), reader
        for n, line in _executable_lines(reader):
            for bad in FORBIDDEN:
                assert bad not in line, (
                    f"{reader.name}:{n} tests {bad} — with a three-state "
                    f"manifest that reads INCONCLUSIVE as a pass: {line!r}")


#: Ways a module can take the manifest's CONTENT, as they appear in an AST.
_READ_METHODS = frozenset({"read_text", "read_bytes", "open", "readlines",
                           "readline", "read"})
_PARSE_FUNCS = frozenset({"load", "loads"})
#: The CLI option through which a manifest reader is HANDED the path. Both
#: shipped readers take it; see the second arm of `_manifest_readers_in`.
_MANIFEST_OPTION = "--manifest"


def _manifest_readers_in(path: Path) -> list:
    """Where this module OPENS or PARSES `latest_results.jsonl`, by line.

    NAMING THE FILE IS NOT READING IT, and the difference is the whole point of
    this predicate. A module that builds the path and asks `.is_file()` learns
    PRESENCE and never CONTENT: it cannot read a `status` field at all, so it
    cannot read `INCONCLUSIVE` as a pass, which is the only thing the guard
    above forbids. MEASURED 2026-09-16 (lane icsub2): `flow_compliance_check.py`
    is exactly that shape at :8453 --
    `str(project / "latest_results.jsonl") if (project /
    "latest_results.jsonl").is_file() else None` -- handed to the P0 roster so
    the MCP gate is N/A on a run that never drove the MCP server (R-0915-15).
    It has named the file since 3ee62f3ae and has never opened it.

    AND IT MUST NOT SIMPLY BE ADDED TO `known` EITHER, which is why this is a
    predicate and not a longer list. It carries three genuine `!= "FAIL"` tests
    -- over `result.status`, `r["verdict"]` and `overall` -- all in ITS OWN
    verdict vocabulary, none of which is a value `writeManifest` ever wrote.
    Listing it as a reader would make the source guard fire on three correct
    lines, and the only ways out of that are a per-file exception list or a
    weaker guard. Both are worse than asking the real question.

    THE REAL QUESTION, asked of the AST rather than of the text, in TWO arms
    because one was measurably not enough:

      (a) does a manifest path expression reach a read? Taint starts at a
          string literal containing `latest_results`, propagates through
          assignment to a name, and is consumed by `open(x)`,
          `json.load/loads(x)`, or `x.read_text()` and its siblings.

      (b) does the module DECLARE `--manifest`? Arm (a) alone was WRONG, and
          `test_the_two_known_readers_are_still_readers` is what said so:
          `mcp_execution_verify.py` is a reader by trade and arm (a) could not
          see it, because it is HANDED the path on the command line and reads
          it in `parse_manifest(manifest_path)` -- the string `latest_results`
          appears in that file only in its module docstring and in `--manifest`
          help text. A predicate keyed on the literal is blind to every reader
          that takes the path as an argument, which is how a reader is normally
          written. `fpga_program_chain_attest_check.py` is the same shape.
          `flow_compliance_check.py` declares no such option.

    Narrow on purpose, and the residue is DISCLOSED rather than papered over: a
    reader that neither names the file nor declares `--manifest` -- one handed
    the path through some third channel -- is not caught here. The positive
    control keeps arm (a) honest, the blindness control keeps both arms from
    narrowing to nothing, and the source guard above is the second line of
    defence in any case.
    """
    try:
        src = path.read_text(errors="ignore")
    except OSError:
        return []
    # SCOPE FIRST, and this is where the original test's predicate belongs: a
    # module that never mentions `latest_results` is not about THIS manifest,
    # so neither arm may speak about it. MEASURED: `--manifest` is a generic
    # option name -- cross_layer_reference_check, handoff_bundle_check and
    # content_pinned_authority_verified_only_at_merge each declare one for a
    # manifest of their own, none of which writeManifest ever wrote. Arm (b)
    # without this scope named all three.
    if "latest_results" not in src:
        return []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []

    def literal(node) -> bool:
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and "latest_results" in node.value)

    tainted_names = set()

    def tainted(node) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name) and node.id in tainted_names:
            return True
        return any(literal(n) or (isinstance(n, ast.Name)
                                  and n.id in tainted_names)
                   for n in ast.walk(node))

    # Bindings first, and repeated until they settle, so a name assigned above
    # its use -- or a chain of assignments -- is tainted by the time the read
    # is examined.
    for _ in range(4):
        before = set(tainted_names)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and tainted(node.value):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            tainted_names.add(n.id)
        if tainted_names == before:
            break

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # Arm (b): the module is handed the path on its own command line.
        if isinstance(fn, ast.Attribute) and fn.attr == "add_argument":
            for a in node.args:
                if (isinstance(a, ast.Constant)
                        and a.value == _MANIFEST_OPTION):
                    hits.append("%s:%d declares %s"
                                % (path.name, node.lineno, _MANIFEST_OPTION))
        if isinstance(fn, ast.Name) and fn.id == "open":
            if any(tainted(a) for a in node.args):
                hits.append("%s:%d open(...)" % (path.name, node.lineno))
        elif isinstance(fn, ast.Attribute):
            if fn.attr in _READ_METHODS and tainted(fn.value):
                hits.append("%s:%d .%s()" % (path.name, node.lineno, fn.attr))
            elif fn.attr in _PARSE_FUNCS and any(tainted(a) for a in node.args):
                hits.append("%s:%d json.%s()" % (path.name, node.lineno, fn.attr))
    return sorted(set(hits))


def test_the_reader_set_is_the_whole_set() -> None:
    """The guard above is only as good as its list of readers. Every program
    that READS the manifest must be one of them, so adding a real new reader
    without adding it here goes red.

    The membership test is `_manifest_readers_in`, not "mentions the filename":
    the guard forbids a status comparison, and only a module that takes the
    manifest's CONTENT can make one. There is deliberately NO exception list --
    a module is in or out by what its own code does."""
    root = PROGRAMS.parent
    known = {VERIFY.resolve(), ATTEST.resolve()}
    tests_dir = (PROGRAMS / "tests").resolve()
    extra = {}
    for path in root.rglob("*.py"):
        p = path.resolve()
        if p in known or tests_dir in p.parents or p.parent.name == "test":
            continue
        where = _manifest_readers_in(p)
        if where:
            extra[str(p)] = where
    assert not extra, (
        "new reader(s) of latest_results.jsonl are not covered by the "
        "non-FAIL guard: " + json.dumps(extra, indent=2))


def test_a_planted_reader_is_detected(tmp_path: Path) -> None:
    """POSITIVE CONTROL. A ratchet nobody can see fire is a ratchet nobody can
    trust: the tightening above only narrows honestly if a REAL new reader
    still trips it. Each body below is a shape a genuine consumer would use."""
    shapes = {
        "direct_open": 'open("latest_results.jsonl")\n',
        "path_read_text": (
            'from pathlib import Path\n'
            'Path("x/latest_results.jsonl").read_text()\n'),
        "bound_then_read": (
            'from pathlib import Path\n'
            'm = Path(root) / "latest_results.jsonl"\n'
            'text = m.read_text()\n'),
        "json_load": (
            'import json\n'
            'p = "latest_results.jsonl"\n'
            'json.load(open(p))\n'),
        "handed_the_path_on_argv": (
            'import argparse, json\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--manifest", required=True,\n'
            '               help="Path to latest_results.jsonl")\n'
            'a = p.parse_args()\n'
            'json.loads(open(a.manifest).read())\n'),
        "chained_binding": (
            'from pathlib import Path\n'
            'name = "latest_results.jsonl"\n'
            'p = Path(d) / name\n'
            'q = p\n'
            'q.open()\n'),
    }
    for label, body in shapes.items():
        f = tmp_path / (label + ".py")
        f.write_text(body)
        assert _manifest_readers_in(f), (
            label + ": a real reader was NOT detected")


def test_a_manifest_of_its_own_is_not_this_manifest(tmp_path: Path) -> None:
    """NEGATIVE CONTROL for arm (b). `--manifest` is a generic option name:
    three shipped programs declare one for a manifest of their own. Only a
    module that is about THIS manifest may be judged by this ratchet."""
    f = tmp_path / "other.py"
    f.write_text(
        'import argparse\n'
        'p = argparse.ArgumentParser()\n'
        'p.add_argument("--manifest", help="the handoff bundle manifest")\n'
        'a = p.parse_args()\n'
        'open(a.manifest).read()\n')
    assert _manifest_readers_in(f) == []


def test_a_presence_test_is_not_a_reader(tmp_path: Path) -> None:
    """NEGATIVE CONTROL, and it is `flow_compliance_check.py`'s own shape,
    transcribed. Asking whether the file EXISTS reaches no `status` field."""
    f = tmp_path / "presence.py"
    f.write_text(
        'from pathlib import Path\n'
        'def ctx(project):\n'
        '    return {"mcp_manifest": (str(project / "latest_results.jsonl")\n'
        '            if (project / "latest_results.jsonl").is_file()\n'
        '            else None)}\n')
    assert _manifest_readers_in(f) == []


def test_the_two_known_readers_are_still_readers() -> None:
    """The detector must not have narrowed so far that it sees nobody. If
    either shipped reader stops being detected, this predicate has gone blind
    and the ratchet above would be passing over an empty population."""
    for reader in (VERIFY, ATTEST):
        assert _manifest_readers_in(reader.resolve()), (
            reader.name + " reads the manifest but the detector missed it — "
            "the ratchet is measuring nothing")
