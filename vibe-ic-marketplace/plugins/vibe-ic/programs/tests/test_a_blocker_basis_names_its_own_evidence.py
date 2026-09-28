#!/usr/bin/env python3
"""A blocker's basis names the evidence the step recorded, not a neighbour's.

MEASURED on lane llb's opentitan_aes run, `reports/audit/flow_compliance_check.log`:

    Step 2  FAIL(missing_artefact)  basis: no-rule-matched
    Step 4  FAIL                    basis: derived-from-upstream (2)
    Step 5  FAIL                    basis: derived-from-upstream (2)

Step 2's record carries two things.
  * Its natural verdict was NOT_MEASURED(awaiting_agent_pass): the nested phase-1
    audit waits on D1's hand-off, `phase1_expert_parse_track . --check-report`.
  * It was then made FAIL(missing_artefact) by "AUDIT-CREATED OUTPUT REFUSED:
    reports/phase2/lint/rom_init_lint.json". The file is present, but only the
    audit wrote it.
Why only the audit: the pre-audit producer (`flow_declared_producer_run`) DID
run `rom_init_lint`, and it exited 2 with "missing file:
phase2/stage1/rtl/*.sv". Its argv was `command.split()`, so the glob the flow
command names reached the program unexpanded. The audit's own executor expands
it (`_expand_globs`), which is why the audit's run of the same command
succeeded.

Steps 4 and 5 each FAILed on their OWN program: verilator_coverage_measure
LINE_UNION_DISAGREES, and formal_proof_evidence_check BINDING_OUTSTANDING.
Rule 7 still booked them "derived-from-upstream". The reason: step 2 counts as
"did not deliver" (missing_artefact), although what it did not deliver is a
lint report that is ON DISK and that neither step reads. The flow declares
step 4's inputs from steps 1 and D1 and step 5's from step 1; step 2 is only in
their `blocks_on`.

Both directions are tested. An ABSENT predecessor output still blocks, whether
or not the flow lists it as an input: step 29's SDF comes from step 22, which
it does not declare. So does a present-but-refused output of a step that owns
one of this step's declared inputs, and one of a step that declares no inputs
at all. Only a present-but-refused output of a step this one reads nothing
from stops blocking.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

_BC = importlib.import_module("_blocker_classification")
_FLOW_YAML = _PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

AWAIT = ("AWAITING an agent pass: the gate completed pass one of a two-pass "
         "protocol and pass two is not a program's to make: flow_compliance_check "
         ". --stage-id stage_phase1 --strict --json reports/phase1/gates/"
         "stage_phase1_compliance.json --skip-analog — nested step(s) awaiting "
         "an agent pass: D1 (Phase 1 Doc Extraction); hand-off emitted by: "
         "phase1_expert_parse_track . --check-report")
REFUSED = ("AUDIT-CREATED OUTPUT REFUSED: ['reports/phase2/lint/rom_init_lint.json']"
           " — present, but written by this step's own gate rather than by the "
           "run, so the step has no run evidence for it.")


def _step(sid, status, **kw):
    rec = {"id": sid, "name": kw.pop("name", f"step {sid}"),
           "stage": "stage1", "status": status,
           "reasons": kw.pop("reasons", []), "evidence": kw.pop("evidence", []),
           "gate_output": "", "cascade_note": "", "self_skip_disclosed": False,
           "structure_only_disclosed": False, "gate_records": None}
    rec.update(kw)
    return rec


def _real_flow(*ids):
    doc = yaml.safe_load(_FLOW_YAML.read_text())
    want = {str(i) for i in ids}
    return [s for s in doc["steps"] if str(s.get("id")) in want]


def _aes_steps():
    """The five records of the AES run, in the shapes the audit publishes."""
    return [
        _step("D1", "NOT_MEASURED", reason_class="awaiting_agent_pass",
              reasons=[AWAIT.split(" — ")[0]],
              evidence=["phase1/generated_docs/L10_TEST_CASES.json"]),
        _step(1, "NOT_MEASURED", reason_class="partial_population",
              reasons=["PARTIALLY-VACUOUS (1 of 2 gate clause(s) examined "
                       "nothing): catalog_synth_safe_params_check"],
              evidence=["phase2/stage1/rtl/aes_core.sv"]),
        _step(2, "FAIL", reason_class="missing_artefact",
              reasons=[AWAIT, "INCOMPLETE: the gate reports its input was "
                       "applicable and was NOT examined: flow_compliance_check",
                       REFUSED],
              evidence=["reports/phase2/lint/rtl_hygiene.json",
                        "reports/phase2/lint/rom_init_lint.json",
                        "reports/crosslayer/rewrite_equivalence_check.json"]),
        _step(4, "FAIL", reasons=[
            "program failed: verilator_coverage_measure check --coverage-json "
            "reports/phase2/coverage/coverage_verilator.json",
            "output: [check] LINE_UNION_DISAGREES: verilator_coverage "
            "--write-info and the h-stripped suite union name different lines"],
            evidence=["phase2/stage1/sim/results.xml"]),
        _step(5, "FAIL", reasons=[
            "program failed: formal_proof_evidence_check . --json "
            "reports/phase2/gates/formal_evidence.json",
            "output: BINDING_OUTSTANDING (R-0924-2): L8.clock_and_reset_waveform"
            ".resets.0.name — the covering property is not proven"],
            evidence=["phase2/stage1/formal/results.json"]),
    ]


def _by_id(blockers):
    return {str(b["step_id"]): b for b in blockers}


# ── (b) steps 4 and 5 are booked on their own program failure ───────────────
@pytest.mark.parametrize("sid", ["4", "5"])
def test_a_step_that_failed_on_its_own_program_is_not_derived(sid):
    """RED on 500b3a42a (and main a7597e349): derived-from-upstream (2)."""
    got = _by_id(_BC.build_blockers(
        _aes_steps(), flow_steps=_real_flow("D1", 1, 2, 4, 5)))[sid]
    assert (got["classification"], got["basis"]) == (
        "DESIGN_FACT", "gate-reached-verdict"), got
    # which step this came AFTER is still said; what it IS is its own failure
    assert got["derived_from"] == ["2"]
    # and the note does not claim what the record contradicts: step 2 FAILed
    assert "passed" not in got["why"], got["why"]


# ── (a) step 2 is booked on its refused artefact, with the hand-off named ───
def test_step2_is_booked_on_the_artefact_the_audit_refused():
    """RED on 500b3a42a: no-rule-matched."""
    got = _by_id(_BC.build_blockers(
        _aes_steps(), flow_steps=_real_flow("D1", 1, 2, 4, 5)))["2"]
    assert got["classification"] == "UNCLASSIFIED"
    assert got["basis"] == "declared-artefact-refused", got
    assert "phase1_expert_parse_track . --check-report" in got["why"], got["why"]


def test_a_missing_artefact_fail_is_never_no_rule_matched():
    """The typed half of rule 10: FAIL(missing_artefact) with no absence line
    the prose rule knows still names an absent/refused artefact."""
    step = _step("X", "FAIL", reason_class="missing_artefact",
                 reasons=["required_outputs missing: ['a.json'] (satisfied: "
                          "0/1 — the gate passed, but every declared output "
                          "must be produced, not just one)"])
    cls, basis, _ = _BC.classify(step)
    assert (cls, basis) == ("UNCLASSIFIED", "declared-artefact-absent")


def test_an_unattributed_output_fail_is_booked_refused():
    """The producer's other provenance refusal types its FAIL with an EMPTY
    reason_class ("a gate defect, not an absent artefact"), so only the line
    says what it is. RED on 500b3a42a: no-rule-matched."""
    step = _step("X", "FAIL", reason_class="",
                 reasons=["UNATTRIBUTED OUTPUT: 1 of 1 declared output(s) were "
                          "discharged by a project-wide glob with nothing tying "
                          "the file to THIS step (codes=['no_step_record'])."],
                 evidence=["reports/x.json"])
    assert _BC.classify(step)[1] == "declared-artefact-refused"


def test_the_awaiting_step_itself_is_still_booked_awaiting():
    """What step 2 becomes once its producer writes the file: its natural
    NOT_MEASURED(awaiting_agent_pass). Green on both trees: rule 10b."""
    step = _step(2, "NOT_MEASURED", reason_class="awaiting_agent_pass",
                 reasons=[AWAIT])
    assert _BC.classify(step)[1] == "awaiting-agent-pass"


# ── what must NOT move ───────────────────────────────────────────────────────
def test_an_absent_output_of_an_undeclared_input_owner_still_blocks():
    """Step 29 (post-layout sim + SDF) blocks_on 22 and declares its inputs
    from 12 only. The SDF comes from 22 all the same, so an ABSENT step-22
    output keeps 29 derived."""
    steps = [
        _step(22, "FAIL", reason_class="missing_artefact",
              reasons=["no required_outputs found (expected: "
                       "['phase3/stage3/extracted/*.spef'])"]),
        _step(29, "FAIL", reasons=["program failed: post_layout_sim_check . "
                                   "--json reports/phase2/gates/post_layout_sim.json"]),
    ]
    got = _by_id(_BC.build_blockers(steps, flow_steps=_real_flow(22, 29)))["29"]
    assert got["basis"] == "derived-from-upstream", got


def test_a_refused_output_of_a_declared_input_owner_still_blocks():
    """Step 5 reads step 1's RTL. If step 1's own output is only
    audit-authored, step 5 did measure a tree the run did not produce."""
    steps = _aes_steps()
    steps[1] = _step(1, "FAIL", reason_class="missing_artefact",
                     reasons=[REFUSED.replace("rom_init_lint.json", "rtl/x.sv")],
                     evidence=["phase2/stage1/rtl/x.sv"])
    flow = _real_flow("D1", 1, 2, 4, 5)
    for s in flow:
        if str(s["id"]) == "5":
            s["blocks_on"] = [1, 2]
    got = _by_id(_BC.build_blockers(steps, flow_steps=flow))["5"]
    assert got["basis"] == "derived-from-upstream", got


@pytest.mark.parametrize("shape", ["an_output_also_missing", "no_evidence"])
def test_a_refusal_that_is_not_wholly_present_still_blocks(shape):
    """The producer appends `required_outputs missing` after the refusal when
    part of the declared set is ABSENT; and a refusal with nothing resolved
    in `evidence` establishes no presence. Either keeps 4 derived."""
    steps = _aes_steps()
    if shape == "an_output_also_missing":
        steps[2]["reasons"].append(
            "required_outputs missing: ['reports/phase2/lint/rtl_hygiene.json'] "
            "(satisfied: 2/3 — the gate passed, but every declared output "
            "must be produced, not just one)")
    else:
        steps[2]["evidence"] = []
    got = _by_id(_BC.build_blockers(
        steps, flow_steps=_real_flow("D1", 1, 2, 4, 5)))["4"]
    assert got["basis"] == "derived-from-upstream", got


def test_a_step_that_declares_no_inputs_keeps_the_conservative_rule():
    """No declared inputs, nothing to tell sequencing from data: unchanged."""
    steps = _aes_steps()
    flow = _real_flow("D1", 1, 2, 4, 5)
    for s in flow:
        s.pop("required_inputs", None)
    got = _by_id(_BC.build_blockers(steps, flow_steps=flow))["4"]
    assert got["basis"] == "derived-from-upstream", got


def test_the_markers_are_the_producers_own_words():
    src = (_PROGRAMS / "flow_compliance_check.py").read_text()
    assert _BC.PROVENANCE_REFUSED_MARKERS
    for marker in (*_BC.PROVENANCE_REFUSED_MARKERS,
                   _BC.OUTPUTS_MISSING_PREFIX, _BC.AWAITING_PREFIX):
        assert f'f"{marker}' in src, marker
    assert "hand-off emitted by: " in src


# ── (a) the root of step 2's FAIL: the pre-audit producer never expanded ────
def test_the_pre_audit_producer_expands_the_globs_its_command_names(tmp_path):
    """RED on 500b3a42a: rc 2, "rom_init_lint: missing file:
    phase2/stage1/rtl/*.sv", no report written. The audit's own executor
    expands the same command (nullglob), so the producer now does too, with
    the audit's own `_expand_globs`."""
    import flow_declared_producer_run as P
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "aes_core.sv").write_text("module aes_core(input clk); endmodule\n")
    row = {"step": "2", "program": "rom_init_lint",
           "command": "rom_init_lint phase2/stage1/rtl/*.sv phase2/stage1/rtl/*.v "
                      "--json reports/phase2/lint/rom_init_lint.json",
           "target": "reports/phase2/lint/rom_init_lint.json"}
    out = P._run_one(tmp_path, row, timeout=300)
    assert out["executed"] and out["rc"] == 0, out
    assert out["target_exists_after"], out


def _globbing_clauses():
    import flow_declared_producer_run as P
    return [c for c in P.declared_producer_clauses()
            if any(ch in c["command"] for ch in "*?[")]


def test_the_glob_sweep_has_a_denominator():
    """Measured at authoring: step 2's rtl_hygiene_lint and rom_init_lint.
    The sweep below is only a check while this list is not empty."""
    progs = {c["program"] for c in _globbing_clauses()}
    assert {"rom_init_lint", "rtl_hygiene_lint"} <= progs, progs


@pytest.mark.parametrize("program", ["rom_init_lint", "rtl_hygiene_lint"])
def test_every_globbing_producer_clause_writes_its_document(tmp_path, program):
    """Every declared producer clause whose command names a glob, driven
    through the pass on a project whose RTL the glob matches."""
    import flow_declared_producer_run as P
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.sv").write_text("module top(input clk); endmodule\n")
    rows = [c for c in _globbing_clauses() if c["program"] == program]
    assert rows, program
    for row in rows:
        out = P._run_one(tmp_path, dict(row), timeout=300)
        assert out["executed"] and out["rc"] == 0, out
        assert out["target_exists_after"], out
