#!/usr/bin/env python3
"""#2087 — ``no_fsm_in_input`` is a claim about the INPUT, not about the design.

THE DEFECT, measured on a ``processor_cpu`` project (``rtl_gen=null``, so
``design_one_shot_runner.step_rtl_gen`` WAIVES to ``spec-to-rtl`` and the author
writes the RTL):

  Run 1, before the author writes anything — correct, and it says the right
  thing::

      [SKIP] L6 positively declares no FSM in the input and declares no
             reject_rules[] — nothing this gate can hold it to

  Run 2, same L6, same input, the only change being the RTL the runner asked
  for::

      [FAIL] BLOCKING EXTRACTION_APPLICABILITY_CONTRADICTION:
             L6_CONTROL_LOGIC.json:no-FSM=true vs <authored>.v:FSM-next=3

The field's name is the contract. ``no_fsm_in_input`` says the INPUT DOCUMENTS
carry no FSM; an input-level claim can only be contradicted by input-level
evidence. RTL the FLOW authored is not evidence about the input — for these
classes the input deliberately leaves the control structure free, so an authored
FSM is the CONFORMING outcome.

WHAT MUST NOT MOVE, and is asserted here in the same file so the two halves
cannot drift apart: #1977's finding. When the RTL under ``phase2/stage1/rtl``
came FROM the input — a reused-IP design, keystone
``SOURCE_MANIFEST.json{reused_ip:true}``, or RTL staged in
``input/vendor_rtl/`` — a no-FSM declaration over it is still a BLOCKING
contradiction at rc 1. The discriminator is the tree's PROVENANCE and nothing
else, read through the one shared predicate (``_reused_ip_predicate``).

Synthesized neutral data throughout: no benchmark name, no chip name, no vendor
literal.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
PROG = PROGRAMS / "l6_fsm_scaffold_actionable_check.py"

#: An FSM the flow AUTHORS: an integer state register with real movement.
#: Verilog-2001 shape, which is what ``spec-to-rtl`` writes for these classes.
AUTHORED_FSM = """\
module core_ctrl (input wire clk, input wire rst_n, output reg done);
  reg [2:0] state;
  always @(posedge clk) begin
    if (!rst_n) state <= 3'd0;
    else case (state)
      3'd0: state <= 3'd1;
      3'd1: state <= 3'd2;
      3'd2: state <= 3'd0;
      default: state <= 3'd0;
    endcase
  end
  always @(posedge clk) done <= (state == 3'd2);
endmodule
"""

#: L1+L2 that make ``ic_class_profile`` resolve ``processor_cpu`` — the class
#: the issue was measured on. Structural ISA prose only; no core name.
L1_CPU = {
    "ic_name": "synth_core",
    "description": ("A 32-bit RV32I soft-core processor. The instruction set "
                    "is fixed by the input; the program counter and the "
                    "register file are architectural state."),
    "interface": "wishbone",
}
L2_CPU = {"architecture": ("bit-serial datapath; instruction fetch over the "
                           "memory bus. The micro-architecture, including any "
                           "control structure, is left to the implementer.")}

#: L6 as the producer honestly writes it for such an input.
L6_SILENT_INPUT = {
    "fsm_states": [], "fsm_machines": [], "fsm_states_source": [],
    "no_fsm_in_input": True, "no_fsm_states_in_input": True,
}


def _run(project: Path, json_out: Path | None = None):
    argv = [sys.executable, str(PROG), str(project)]
    if json_out is not None:
        argv.extend(["--json", str(json_out)])
    return subprocess.run(argv, capture_output=True, text=True)


def _cpu_project(tmp_path: Path, name: str, l6: dict | None = None) -> Path:
    proj = tmp_path / name
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L1_DATASHEET.json").write_text(json.dumps(L1_CPU), encoding="utf-8")
    (gd / "L2_ARCHITECTURE.json").write_text(json.dumps(L2_CPU),
                                             encoding="utf-8")
    (gd / "L6_CONTROL_LOGIC.json").write_text(
        json.dumps(L6_SILENT_INPUT if l6 is None else l6), encoding="utf-8")
    return proj


def _author_rtl(project: Path, filename: str = "core_ctrl.v") -> Path:
    """Write RTL the way the FLOW does: into phase2, with no staging record."""
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    path = rtl / filename
    path.write_text(AUTHORED_FSM, encoding="utf-8")
    return path


def _stage_rtl_from_input(project: Path,
                          filename: str = "core_ctrl.v") -> Path:
    """Write RTL the way a REUSED-IP design does: it arrives in the input."""
    vdir = project / "input" / "vendor_rtl"
    vdir.mkdir(parents=True, exist_ok=True)
    path = vdir / filename
    path.write_text(AUTHORED_FSM, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# THE DEFECT — the two runs of the issue, in one test.
# ---------------------------------------------------------------------------

def test_the_two_runs_of_the_issue_give_the_same_verdict_class(tmp_path):
    """Authoring the RTL the runner asked for must not change the verdict.

    This is the whole issue: run 1 and run 2 differ ONLY in whether the author
    has written the RTL yet. Before #2087 run 1 was rc 2 SKIP and run 2 was
    rc 1 BLOCKING."""
    before = _cpu_project(tmp_path, "before")
    r1 = _run(before)

    after = _cpu_project(tmp_path, "after")
    _author_rtl(after)
    r2 = _run(after)

    assert r1.returncode == 2, r1.stdout + r1.stderr
    assert r2.returncode == r1.returncode, r2.stdout + r2.stderr
    assert "[SKIP]" in r1.stdout
    assert "[SKIP]" in r2.stdout
    assert "nothing this gate can hold it to" in r2.stdout
    assert "EXTRACTION_APPLICABILITY_CONTRADICTION" not in r2.stdout


def test_an_authored_fsm_is_reported_as_an_advisory_not_a_contradiction(
        tmp_path):
    """The observation is kept — it is just not called a contradiction."""
    proj = _cpu_project(tmp_path, "advisory")
    _author_rtl(proj)
    report = tmp_path / "advisory.json"
    r = _run(proj, report)

    assert r.returncode == 2, r.stdout + r.stderr
    assert "AUTHORED_FSM_NO_INPUT_SCAFFOLD" in r.stdout
    assert "[WARN]" in r.stdout
    assert "authored FSM with no input scaffold" in r.stdout

    res = json.loads(report.read_text(encoding="utf-8"))
    assert res["applicability_findings"] == []
    assert res["failures"] == []
    advisory = res["authored_fsm_advisories"][0]
    assert advisory["name"] == "AUTHORED_FSM_NO_INPUT_SCAFFOLD"
    assert advisory["severity"] == "ADVISORY"
    assert advisory["declaration"]["fields"] == {
        "no_fsm_in_input": True, "no_fsm_states_in_input": True}
    evidence = advisory["authored_rtl_evidence"]
    assert [e["provenance"] for e in evidence] == ["authored"]
    assert evidence[0]["rtl_path"] == "phase2/stage1/rtl/core_ctrl.v"


def test_the_written_report_carries_no_failing_verdict_for_step_36(tmp_path):
    """THE DOWNSTREAM HALF, PINNED BY THE FIELD ITS CONSUMER ACTUALLY READS.

    `phase1_doc_one_shot_runner:62878` runs this gate WITH `--json`, so the
    verdict does not stop at stdout — it is WRITTEN to
    `reports/phase1/l6_fsm_scaffold_actionable.json`. Flow Step 36
    (`step_internal_fail_bubble_up_check`) then scans `reports/**/*.json` and
    treats `verdict` in `_FAIL_VERDICTS = {"FAIL", "MISSING"}` as an
    unacknowledged step-internal failure. MEASURED on the #2087 shape: with the
    pre-fix gate that file said `FAIL` and Step 36 returned rc 1
    `[STEP_FAIL_NOT_BUBBLED]` — the false contradiction manufactured a SECOND
    red in a DIFFERENT gate.

    Asserting rc alone would not hold that closed: the report's `verdict` is a
    separate field and a later edit could restore `FAIL` there while keeping
    rc 2. So this names the field and the exact value set its consumer uses."""
    proj = _cpu_project(tmp_path, "report_verdict")
    _author_rtl(proj)
    report = tmp_path / "report_verdict.json"
    r = _run(proj, report)

    assert r.returncode == 2, r.stdout + r.stderr
    res = json.loads(report.read_text(encoding="utf-8"))
    # the literal set step_internal_fail_bubble_up_check._FAIL_VERDICTS holds
    assert res["verdict"] not in {"FAIL", "MISSING"}, res["verdict"]
    assert res["verdict"] == "SKIP", res["verdict"]


def test_a_real_input_side_contradiction_still_writes_a_failing_verdict(
        tmp_path):
    """The other direction: Step 36 must still SEE #1977's finding."""
    proj = _cpu_project(tmp_path, "report_verdict_fail")
    _stage_rtl_from_input(proj)
    report = tmp_path / "report_verdict_fail.json"
    r = _run(proj, report)

    assert r.returncode == 1, r.stdout + r.stderr
    res = json.loads(report.read_text(encoding="utf-8"))
    assert res["verdict"] in {"FAIL", "MISSING"}, res["verdict"]


def test_the_verdict_is_the_first_stdout_line_not_the_advisory(tmp_path):
    """THE RUN LOG GETS ONE LINE, AND IT MUST BE THE VERDICT.

    `phase1_doc_one_shot_runner` echoes `_gate_cp.stdout.strip().splitlines()[0]`
    — exactly one line — into the phase-1 run log. When #2087's advisory was
    first written it printed BEFORE the verdict, so a reader of that log saw the
    advisory and never learned the gate had skipped. The advisory is still
    published; it just does not stand in front of the verdict."""
    proj = _cpu_project(tmp_path, "firstline")
    _author_rtl(proj)
    r = _run(proj)

    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("[SKIP] l6_fsm_scaffold_actionable_check:"), (
        r.stdout)
    assert "AUTHORED_FSM_NO_INPUT_SCAFFOLD" not in lines[0]
    # published, just not first
    assert any(line.startswith("[WARN]")
               and "AUTHORED_FSM_NO_INPUT_SCAFFOLD" in line
               for line in lines[1:]), r.stdout


def test_the_verdict_leads_on_the_fail_tier_too(tmp_path):
    """Same rule on the blocking tier: a warning must never displace a FAIL."""
    proj = _cpu_project(tmp_path, "firstline_fail")
    _stage_rtl_from_input(proj)
    r = _run(proj)

    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("[FAIL] l6_fsm_scaffold_actionable_check:"), (
        r.stdout)


# ---------------------------------------------------------------------------
# #1977 MUST NOT MOVE — the SAME RTL, arriving from the INPUT, still blocks.
# This pair is the negative control: only the provenance differs.
# ---------------------------------------------------------------------------

def test_the_same_fsm_staged_from_the_input_still_blocks(tmp_path):
    """Byte-identical RTL, staged in input/vendor_rtl instead of authored."""
    proj = _cpu_project(tmp_path, "staged")
    _stage_rtl_from_input(proj)
    report = tmp_path / "staged.json"
    r = _run(proj, report)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "BLOCKING EXTRACTION_APPLICABILITY_CONTRADICTION" in r.stdout
    assert "input/vendor_rtl/core_ctrl.v" in r.stdout
    finding = json.loads(report.read_text(
        encoding="utf-8"))["applicability_findings"][0]
    assert finding["severity"] == "BLOCKING"
    assert [e["provenance"]
            for e in finding["staged_rtl_evidence"]] == ["input"]


def test_a_reused_ip_manifest_makes_the_phase2_tree_input_side(tmp_path):
    """The other real staging path: the tree is populated FROM the input and
    the keystone manifest records it. Same file, same place as the authored
    case — only the manifest differs, and it flips the verdict back to red."""
    proj = _cpu_project(tmp_path, "consumed")
    _author_rtl(proj)                       # same bytes, same path
    (proj / "phase2" / "stage1" / "rtl" / "SOURCE_MANIFEST.json").write_text(
        json.dumps({"reused_ip": True}), encoding="utf-8")
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "BLOCKING EXTRACTION_APPLICABILITY_CONTRADICTION" in r.stdout
    assert "phase2/stage1/rtl/core_ctrl.v" in r.stdout


def test_a_design_that_stages_rtl_keeps_its_whole_phase2_tree_input_side(
        tmp_path):
    """THE COARSENESS, ASSERTED SO IT IS A DECISION AND NOT AN ACCIDENT.

    Provenance is answered PER TREE, not per file, because on the catalog-pull
    staging path the input-side files are copied straight into
    ``phase2/stage1/rtl`` and never appear under ``input/vendor_rtl`` at all —
    there is no per-file record to read. So once a design has staged ANY reused
    RTL, every FSM in its phase-2 tree stays input-side and stays BLOCKING,
    including the glue the author wrote beside it. That is the fail-closed
    direction: a design that brings its own implementation keeps #1977's
    finding whole. Only a design that stages NOTHING — the ``rtl_gen=null`` +
    ``spec-to-rtl`` shape #2087 is about — gets the advisory."""
    proj = _cpu_project(tmp_path, "both")
    _stage_rtl_from_input(proj, "staged_ctrl.v")
    _author_rtl(proj, "authored_ctrl.v")
    report = tmp_path / "both.json"
    r = _run(proj, report)

    assert r.returncode == 1, r.stdout + r.stderr
    res = json.loads(report.read_text(encoding="utf-8"))
    finding = res["applicability_findings"][0]
    assert [e["rtl_path"] for e in finding["staged_rtl_evidence"]] == [
        "input/vendor_rtl/staged_ctrl.v",
        "phase2/stage1/rtl/authored_ctrl.v"]
    assert {e["provenance"] for e in finding["staged_rtl_evidence"]} == {
        "input"}
    assert res["authored_fsm_advisories"] == []


# ---------------------------------------------------------------------------
# The advisory disarms NOTHING that was a real finding.
# ---------------------------------------------------------------------------

def test_an_unscaffoldable_declared_fsm_still_fails_beside_authored_rtl(
        tmp_path):
    """L6 DOES declare an FSM, and it is not actionable. Authored RTL sitting
    next to it must not turn that into a skip."""
    l6 = {
        "fsm_states": [{"name": "ST_A", "transitions": [{"to": "ST_GHOST"}]},
                       {"name": "ST_B", "transitions": [{"to": "ST_A"}]}],
        "no_fsm_in_input": False, "no_fsm_states_in_input": False,
    }
    proj = _cpu_project(tmp_path, "dangling", l6=l6)
    _author_rtl(proj)
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "not in the derived state set" in r.stdout


def test_a_single_state_declaration_still_fails_beside_authored_rtl(tmp_path):
    l6 = {
        "fsm_states": [{"name": "ST_ONLY", "transitions": []}],
        "no_fsm_in_input": False, "no_fsm_states_in_input": False,
    }
    proj = _cpu_project(tmp_path, "single", l6=l6)
    _author_rtl(proj)
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 FSM state" in r.stdout


# ---------------------------------------------------------------------------
# The provenance reader itself, in both directions, and fail-closed.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The keystone manifest: ABSENT, READABLE and UNREADABLE are three answers.
# ---------------------------------------------------------------------------

#: (label, manifest bytes or None, expected rc). Table-driven ON PURPOSE: a
#: single-case test would not catch a change that fixes one row by breaking its
#: neighbour, and the whole content of this fix is which row maps to which rc.
_MANIFEST_TABLE = [
    ("absent — the spec-to-rtl shape", None, 2),
    ("readable, reused_ip: true", '{"reused_ip": true}', 1),
    ("readable, reused_ip: false — an explicit statement", '{"reused_ip": false}', 2),
    ("PRESENT but truncated mid-write", '{"reused_ip": tru', 1),
    ("PRESENT but an empty file", "", 1),
    ("PRESENT but a JSON list, not an object", "[1, 2, 3]", 1),
]


def test_the_keystone_manifest_table_holds_in_every_row(tmp_path):
    """ABSENT is not UNREADABLE, and the difference decides the verdict.

    `_reused_ip_predicate`'s fail-closed direction is False = "not reused IP",
    which is the OPPOSITE of this gate's (INPUT = keep the blocking finding).
    Delegating to it without care let a truncated / empty / wrongly-typed
    manifest downgrade a #1977 contradiction to an advisory — a fail-OPEN hole
    inside a fail-closed design, MEASURED on this exact table. A manifest that
    EXISTS is the keystone artefact the staging paths leave behind, so failing
    to read one is a failure to measure, not licence to assume the flow wrote
    the RTL. An absent manifest still means spec-to-rtl, and an explicit
    readable `reused_ip: false` is still honoured."""
    seen = {}
    for label, manifest, expected_rc in _MANIFEST_TABLE:
        proj = _cpu_project(tmp_path, "mf_" + str(abs(hash(label))))
        _author_rtl(proj)
        if manifest is not None:
            (proj / "phase2" / "stage1" / "rtl"
             / "SOURCE_MANIFEST.json").write_text(manifest, encoding="utf-8")
        r = _run(proj)
        seen[label] = r.returncode
        assert r.returncode == expected_rc, (
            f"{label}: expected rc {expected_rc}, got {r.returncode}\n"
            + r.stdout + r.stderr)
    # membership, not count: every row was actually exercised
    assert set(seen) == {row[0] for row in _MANIFEST_TABLE}


def test_an_unreadable_manifest_says_so_rather_than_blocking_silently(
        tmp_path):
    """Fail-closed is only honest when it says which measurement it lost."""
    proj = _cpu_project(tmp_path, "mf_unreadable_warn")
    _author_rtl(proj)
    (proj / "phase2" / "stage1" / "rtl"
     / "SOURCE_MANIFEST.json").write_text('{"reused_ip": tru', encoding="utf-8")
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "SOURCE_MANIFEST.json is PRESENT but cannot be read" in r.stdout
    assert "fail-closed" in r.stdout
    assert "could not be measured" in r.stdout
    # and the verdict still leads
    assert r.stdout.strip().splitlines()[0].startswith("[FAIL]"), r.stdout


def test_an_unreadable_staged_tree_is_not_reported_as_an_absence(tmp_path):
    """PRESENT-but-unreadable is a failure to measure, not an absence.

    Second instance of the fail-open species behind the manifest table: an
    `input/vendor_rtl/` that EXISTS but cannot be enumerated made
    `staged_vendor_rtl_files` return `[]`, which reads identically to "this
    design staged nothing" — and a real #1977 contradiction vanished into an
    advisory. MEASURED with mode 000, not reasoned about.

    The gate cannot raise a contradiction it has no readable evidence for, so
    the honest outcome is not a manufactured FAIL: it is a verdict that SAYS it
    examined nothing on that side. What must never happen is silence."""
    if os.geteuid() == 0:
        import pytest
        pytest.skip("running as root: mode 000 does not deny access, so this "
                    "test cannot make the tree unreadable")
    proj = _cpu_project(tmp_path, "unreadable_vendor")
    vdir = proj / "input" / "vendor_rtl"
    vdir.mkdir(parents=True)
    (vdir / "staged_ctrl.v").write_text(AUTHORED_FSM, encoding="utf-8")
    os.chmod(vdir, 0o000)
    try:
        r = _run(proj)
    finally:
        os.chmod(vdir, 0o755)          # always restore, even on failure

    assert "input/vendor_rtl/ is PRESENT but cannot be enumerated" in r.stdout
    assert "could not be measured" in r.stdout
    # and it must not claim a block that did not happen
    assert "NOT a clean bill for that tree" in r.stdout
    assert "keeps the finding below blocking" not in r.stdout
    assert r.stdout.strip().splitlines()[0].startswith("[SKIP]"), r.stdout


def test_an_unreadable_manifest_states_the_block_it_actually_caused(tmp_path):
    """The OTHER consequence, and the reason the message is computed rather
    than fixed: here there IS readable RTL, so fail-closed really does block,
    and the warning is entitled to say so. An earlier version printed the
    blocking sentence in BOTH cases — asserting an action that had not
    occurred."""
    proj = _cpu_project(tmp_path, "unreadable_manifest_blocks")
    _author_rtl(proj)
    (proj / "phase2" / "stage1" / "rtl"
     / "SOURCE_MANIFEST.json").write_text('{"reused_ip": tru', encoding="utf-8")
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "keeps the finding below blocking" in r.stdout
    assert "NOT a clean bill for that tree" not in r.stdout


def test_an_unreadable_rtl_file_is_named_rather_than_dropped_in_silence(
        tmp_path):
    """THIRD instance of the species, and the only wholly silent one.

    PRE-EXISTING on the frozen base: the evidence scanner dropped an unreadable
    file with `except: continue`, so a staged FSM behind a permission error was
    indistinguishable from a tree containing no FSM — no finding, no warning,
    nothing. This does NOT manufacture a verdict: with no text to cite the gate
    cannot raise a contradiction, and inventing one would be fabricating
    evidence. It makes the silence audible, which changes no rc."""
    if os.geteuid() == 0:
        import pytest
        pytest.skip("running as root: mode 000 does not deny access")
    proj = _cpu_project(tmp_path, "unreadable_file")
    vdir = proj / "input" / "vendor_rtl"
    vdir.mkdir(parents=True)
    staged = vdir / "staged_ctrl.v"
    staged.write_text(AUTHORED_FSM, encoding="utf-8")
    os.chmod(staged, 0o000)
    try:
        r = _run(proj)
    finally:
        os.chmod(staged, 0o644)

    assert "could NOT be read" in r.stdout, r.stdout
    assert "staged_ctrl.v" in r.stdout            # named, not counted
    assert "NOT a clean bill for those files" in r.stdout
    # no fabricated finding
    assert "EXTRACTION_APPLICABILITY_CONTRADICTION" not in r.stdout
    assert r.returncode == 2, r.stdout + r.stderr


def test_a_readable_staged_tree_raises_no_unreadable_warning(tmp_path):
    """NEGATIVE CONTROL for the row above: the warning must not fire when every
    file was read. A warning that is always on is not a warning."""
    proj = _cpu_project(tmp_path, "readable_file")
    _stage_rtl_from_input(proj)
    r = _run(proj)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "could NOT be read" not in r.stdout


def test_phase2_tree_provenance_answers_both_ways(tmp_path):
    sys.path.insert(0, str(PROGRAMS))
    import l6_fsm_scaffold_actionable_check as gate

    authored = _cpu_project(tmp_path, "prov_authored")
    _author_rtl(authored)
    assert gate._phase2_tree_provenance(authored) == gate.PROV_AUTHORED

    consumed = _cpu_project(tmp_path, "prov_input")
    _author_rtl(consumed)
    (consumed / "phase2" / "stage1" / "rtl"
     / "SOURCE_MANIFEST.json").write_text(json.dumps({"reused_ip": True}),
                                          encoding="utf-8")
    assert gate._phase2_tree_provenance(consumed) == gate.PROV_INPUT


def test_an_unreadable_provenance_predicate_keeps_the_finding_blocking(
        tmp_path, monkeypatch):
    """FAIL-CLOSED, in the direction the rest of this gate already fails in: a
    provenance we cannot read is treated as INPUT-side, so #1977's finding
    survives a broken probe rather than evaporating into an advisory."""
    sys.path.insert(0, str(PROGRAMS))
    import l6_fsm_scaffold_actionable_check as gate

    proj = _cpu_project(tmp_path, "failclosed")
    _author_rtl(proj)
    assert gate._phase2_tree_provenance(proj) == gate.PROV_AUTHORED

    monkeypatch.setattr(gate, "_reused_ip", None)
    assert gate._phase2_tree_provenance(proj) == gate.PROV_INPUT

    class _Raises:
        @staticmethod
        def staged_rtl_is_reused_ip(_project):
            raise RuntimeError("probe is broken")

        @staticmethod
        def staged_vendor_rtl_files(_project):
            raise RuntimeError("probe is broken")

    monkeypatch.setattr(gate, "_reused_ip", _Raises)
    assert gate._phase2_tree_provenance(proj) == gate.PROV_INPUT
    groups = gate._rtl_files_by_provenance(proj)
    assert [g[0] for g in groups] == [gate.PROV_INPUT]


def test_a_design_that_stages_nothing_and_authors_nothing_is_untouched(
        tmp_path):
    """The plain honest case stays exactly what it was."""
    r = _run(_cpu_project(tmp_path, "bare"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "[SKIP]" in r.stdout
    assert "AUTHORED_FSM_NO_INPUT_SCAFFOLD" not in r.stdout


def test_authored_rtl_with_no_fsm_raises_no_advisory(tmp_path):
    """The advisory is about a STRUCTURAL FSM, not about the presence of RTL."""
    proj = _cpu_project(tmp_path, "comb")
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "comb.v").write_text(
        "module comb(input wire a, b, output wire y); assign y = a ^ b; "
        "endmodule\n", encoding="utf-8")
    r = _run(proj)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "AUTHORED_FSM_NO_INPUT_SCAFFOLD" not in r.stdout
