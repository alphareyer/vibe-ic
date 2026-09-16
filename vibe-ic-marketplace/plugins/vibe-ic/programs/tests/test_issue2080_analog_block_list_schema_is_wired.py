#!/usr/bin/env python3
"""vibe-ic#2080 — `analog_block_list_emit_check` was credited by advice prose.

WHAT WAS MEASURED ON THE PRE-FIX TIP (94617408759e)
===================================================
Its only credit anywhere in the tree was a sentence inside a string in another
program:

    programs/analog_flow_compliance_check.py:451
        "analog_block_list_emit_check for whether a list SHOULD "

`gate_is_wired_check`'s invocation.v1 rule reads that for what it is — advice,
not a call — and named the gate unwired. So the ONE file every A1-A9 gate reads
(`_analog_a_check_common.load_block_list` -> `analog/analog_block_list.json`)
had its schema checked by nothing.

THE HOLE IS A SPECIFIC ONE. `analog_one_shot_runner.main` already refuses an
ABSENT list by name (rc 2, FAIL_NO_BLOCK_LIST, #50 Fix 1) and SKIPs an
explicitly-empty one. PRESENT-BUT-BROKEN is the state neither covers, and it is
exactly this gate's subject.

AND THE RUNNER WAS ITSELF PRODUCING ONE. Its #50 Fix 3 fallback materialised
the canonical file as `{"blocks": blocks}` — with no `block_count`. The
`analog-spec-extract` master-list schema this gate was extracted from requires
`block_count` and requires it to equal `len(blocks)`, so the runner wrote a
list its own schema gate rejects, and nothing noticed because nothing ran the
gate. Both halves are repaired here, and both are measured below: the gate is
dispatched, and the producer emits the field.

`spec_file` is deliberately NOT invented by that materialisation. The writer
has not seen a `spec.json` — A1 is what emits them — and a record promising a
path it never looked for would be a fabrication. The gate reports the absence
instead, which is what
`test_the_gate_reports_a_promised_spec_file_that_is_not_on_disk` pins.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import analog_one_shot_runner as AOSR                  # noqa: E402
import analog_block_list_emit_check as ABLE            # noqa: E402
import _path_layout as _pl                             # noqa: E402

_DISPATCH = "_dispatched(step_block_list_schema(project))"
_GATE = "analog_block_list_emit_check"

_ONE_BLOCK = {"name": "ldo_1v8", "type": "LDO",
              "spec_file": "analog/ldo_1v8/spec.json"}


#: The canonical analog root is `phase3/analog` (`_path_layout.analog_dir`),
#: read from that function rather than spelled here so a move breaks the fixture
#: instead of silently building a tree the runner would call "missing".
def _project(tmp: Path, block_list=None, spec_on_disk=False) -> Path:
    p = tmp / "proj"
    adir = _pl.analog_dir(p)
    adir.mkdir(parents=True)
    if block_list is not None:
        (adir / "analog_block_list.json").write_text(
            json.dumps(block_list, indent=2))
    if spec_on_disk:
        (adir / "ldo_1v8").mkdir(parents=True, exist_ok=True)
        (adir / "ldo_1v8/spec.json").write_text("{}\n")
    return p


def _row(res):
    assert res.extras and "block_list_schema" in res.extras, res.extras
    return res.extras["block_list_schema"]


# ---------------------------------------------------------------------------
# THE WIRING
# ---------------------------------------------------------------------------
def test_the_analog_runner_dispatches_the_gate_at_a_real_call_site() -> None:
    src = Path(AOSR.__file__).read_text(errors="replace")
    assert _DISPATCH in src, (
        f"analog_one_shot_runner no longer dispatches {_GATE} ({_DISPATCH!r} "
        f"absent). Its only other credit in this tree is a sentence inside a "
        f"string in analog_flow_compliance_check — vibe-ic#2080.")


def test_the_dispatch_is_after_the_A_loop_not_before_it() -> None:
    """A1 is what emits each block's `spec.json`.

    Asked before the loop, the gate's `spec_file`-resolution arm would be
    answering about a tree this run had not built yet, and every project would
    report the same finding for a reason that has nothing to do with the list.
    """
    src = Path(AOSR.__file__).read_text(errors="replace")
    a1 = src.index('"A1",')
    a9 = src.index('"A9",')
    here = src.index(_DISPATCH)
    assert a1 < a9 < here, (
        "the block-list schema gate moved ahead of the A1-A9 loop; it must be "
        "asked after A1 has had its chance to emit the spec files the list "
        "names.")


def test_the_gate_left_the_unwired_register() -> None:
    reg = json.loads((_PROGRAMS / "gate_is_wired_baseline.json").read_text())
    assert _GATE not in reg["unwired"], (
        "gate_is_wired_baseline.json still records this gate as unwired; the "
        "register shrinks in the commit that wires the gate, or it becomes "
        "standing permission.")
    assert _GATE not in reg.get("skill_only", []), (
        "the skill_only row outlived the unwired row it qualifies; a skill "
        "mention is recorded only for a gate that is otherwise unreached.")


def test_the_row_can_never_move_the_analog_verdict() -> None:
    """ADVISORY is named by NO tier of `_aggregate_verdict`, and that is
    asserted by driving the real function rather than by reading the ladder."""
    src = Path(AOSR.__file__).read_text(errors="replace")
    step = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef)
                and n.name == "step_block_list_schema")
    statuses = {c.args[2].value for c in ast.walk(step)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "StepResult" and len(c.args) >= 3
                and isinstance(c.args[2], ast.Constant)}
    assert statuses == {"ADVISORY"}, (
        f"step_block_list_schema returns {sorted(statuses)}; vibe-ic#2080 "
        f"wires a recorded-unwired gate ADVISORY unless its docstring declares "
        f"it BLOCKING, and this gate's does not.")
    advisory = AOSR.StepResult("block_list_schema", "", "ADVISORY", 0.0, "")
    for base in ([AOSR.StepResult("A1", "x", "PASS", 0.0, "")],
                 [AOSR.StepResult("A1", "x", "NOT_MEASURED", 0.0, "", reason_class="not_executed")],
                 [AOSR.StepResult("A1", "x", "PASS_WITH_WAIVERS", 0.0, "")],
                 [AOSR.StepResult("A1", "x", "FAIL", 0.0, "")]):
        assert (AOSR._aggregate_verdict(base)
                == AOSR._aggregate_verdict(base + [advisory])), (
            f"adding the ADVISORY row changed the analog verdict for "
            f"{base[0].status}; it may not move it in either direction.")


# ---------------------------------------------------------------------------
# THE VERDICT MOVES ON THE SUBJECT — BOTH DIRECTIONS
# ---------------------------------------------------------------------------
def test_a_conforming_list_with_its_spec_on_disk_is_a_PASS(
        tmp_path: Path) -> None:
    p = _project(tmp_path, {"blocks": [_ONE_BLOCK], "block_count": 1},
                 spec_on_disk=True)
    row = _row(AOSR.step_block_list_schema(p))
    assert row["verdict"] == "PASS", row


def test_a_block_count_that_disagrees_with_the_list_is_a_FINDING(
        tmp_path: Path) -> None:
    """The consistency arm, which is the whole reason `block_count` is in the
    schema: a list and a count that disagree cannot both be right, and every
    A-gate below reads the list."""
    p = _project(tmp_path, {"blocks": [_ONE_BLOCK], "block_count": 9},
                 spec_on_disk=True)
    row = _row(AOSR.step_block_list_schema(p))
    assert row["verdict"] == "FINDING", row
    assert any("BLOCK_COUNT_MISMATCH" in str(f) for f in row["findings"]), row


def test_the_gate_reports_a_promised_spec_file_that_is_not_on_disk(
        tmp_path: Path) -> None:
    p = _project(tmp_path, {"blocks": [_ONE_BLOCK], "block_count": 1},
                 spec_on_disk=False)
    row = _row(AOSR.step_block_list_schema(p))
    assert row["verdict"] == "FINDING", row
    assert any("does not resolve to a file" in str(f)
               for f in row["findings"]), row


def test_an_absent_list_is_VACUOUS_and_never_a_silent_PASS(
        tmp_path: Path) -> None:
    p = _project(tmp_path, None)
    res = AOSR.step_block_list_schema(p)
    assert res.status == "PASS"
    assert _row(res)["verdict"] == "VACUOUS", _row(res)


def test_an_unparsable_list_is_a_FINDING_never_a_SKIP(tmp_path: Path) -> None:
    """`_load_block_list_with_status` deliberately treats corrupted JSON as
    "empty" so the runner does not escalate to FAIL_NO_BLOCK_LIST. That leaves
    the corruption itself judged by nobody; this row is what judges it."""
    p = tmp_path / "proj"
    _pl.analog_dir(p).mkdir(parents=True)
    (_pl.analog_dir(p) / "analog_block_list.json").write_text("{ not json")
    assert AOSR._load_block_list_with_status(p) == ([], "empty"), (
        "the runner no longer swallows a corrupted list as empty; re-base the "
        "claim on what it does now rather than deleting the measurement")
    row = _row(AOSR.step_block_list_schema(p))
    assert row["verdict"] == "FINDING", row
    assert "UNPARSABLE_JSON" in row["findings"], row


# ---------------------------------------------------------------------------
# THE PRODUCER THE WIRING FOUND
# ---------------------------------------------------------------------------
def test_the_runners_own_fallback_list_satisfies_the_schema_it_writes(
        tmp_path: Path) -> None:
    """#50 Fix 3 materialised `{"blocks": blocks}` with no `block_count`.

    Driven through the runner's own literal, so a future edit to that writer is
    measured rather than assumed: the emitted document is handed straight to
    the gate's validator. Reverting the producer line turns this red while
    every wiring test above still passes — which is the state the tree was in.
    """
    src = Path(AOSR.__file__).read_text(errors="replace")
    assert '{"blocks": blocks, "block_count": len(blocks)}' in src, (
        "the fallback materialisation no longer emits `block_count`; the "
        "master-list schema requires it and this runner is the writer.")
    blocks = [{"name": "ldo_1v8", "type": "LDO",
               "spec_file": "analog/ldo_1v8/spec.json"}]
    p = tmp_path / "proj"
    adir = _pl.analog_dir(p)
    (adir / "ldo_1v8").mkdir(parents=True)
    (adir / "ldo_1v8/spec.json").write_text("{}\n")
    (adir / "analog_block_list.json").write_text(
        json.dumps({"blocks": blocks, "block_count": len(blocks)}, indent=2)
        + "\n")
    rc, rep = ABLE._validate(adir / "analog_block_list.json", p)
    assert rc == 0 and rep["status"] == "PASS", rep


def test_the_verdict_report_is_published_under_reports_phase3_analog(
        tmp_path: Path) -> None:
    p = _project(tmp_path, {"blocks": [_ONE_BLOCK], "block_count": 1},
                 spec_on_disk=True)
    res = AOSR.step_block_list_schema(p)
    out = p / "reports/phase3/analog/block_list_schema.json"
    assert out.is_file(), sorted(str(x) for x in p.rglob("*.json"))
    assert res.output_files == [str(out)]
    assert json.loads(out.read_text())["status"] == "PASS"
