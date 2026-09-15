"""The 13 declared outputs SPM's own run was not writing, and the rule that
keeps the fix from becoming self-certification.

MEASURED 2026-09-15 (lane icspm3) on `spm` x gf180mcuD, a fresh front-door run
at main b47917a47. The completion audit refused 13 declared outputs over 11
steps::

    step 10  MISSING
      SELF-CERTIFIED EVIDENCE EXCLUDED (audit_created)
        ['reports/phase3/sta/pre_pnr_summary.json']
      ... PRODUCER GAP: no pre-audit producer supplied these paths; wire them
      into the owning runner before claiming this step complete.

and `stage2_compliance` / `stage3_compliance` / `stage4_compliance` then failed
on the ordering violations that follow from steps 10 and 11 reading MISSING.

THE FLOW ALREADY SAID IT TWICE. For every one of the 13, the step's own gate
clause invokes a program with ``--json <path>`` and ``<path>`` is one of that
step's ``required_outputs``. What was missing is the THIRD declaration,
``programs:`` — the one `flow_declared_producer_run.declared_producer_clauses`
reads — so nothing in the run ran the producer, the AUDITOR's evaluation of the
gate wrote the document, and the audit correctly refused its own output.

BOTH DIRECTIONS ARE CASES HERE, because only one of them is about the fix:

  * the document IS produced by the run — every one of the 13 is now a
    declared producer clause, is owed when absent on a step the run performed,
    and the runner (never the auditor) executes it;

  * a gate-written document is STILL refused where the producer is not
    declared — the fix is 13 named declarations, not a rule that credits any
    ``--json`` target to whoever wrote it. A step that declares an output and
    names no producer yields NO clause, so nothing in the run writes it and
    the audit's refusal stands.

chip-AGNOSTIC: the population is recomputed from the shipped yaml on every run
of this file, never tabulated from a chip.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

#: The 13 the spm audit flagged `audit_created`, as (step, program, target).
#: Each row is one line of that run's
#: `output_binding.specs[].code == "audit_created"`.
SPM_THIRTEEN = (
    ("10", "sta_report_check", "reports/phase3/sta/pre_pnr_summary.json"),
    ("15", "stage2_compliance", "reports/phase2/gates/stage2_compliance.json"),
    ("21", "drc_report_check", "reports/phase3/drc_router.json"),
    ("23", "sta_report_check", "reports/phase3/sta/post_route_summary.json"),
    ("24", "ir_drop_report_check", "reports/phase3/ir_drop_signoff.json"),
    ("25", "em_report_check", "reports/phase3/em_signoff.json"),
    ("25", "em_peak_current_authority_check",
     "reports/phase3/em_current_authority.json"),
    ("26.5ic", "die_finishing_check", "reports/phase3/die_finishing.json"),
    ("31", "drc_report_check", "reports/phase3/drc_signoff.json"),
    ("31", "lvs_report_check", "reports/phase3/lvs.json"),
    ("37", "stage3_compliance", "reports/phase3/gates/stage3_compliance.json"),
    ("37.5ip", "digital_hardmacro_check",
     "reports/phase3/digital_hardmacro.json"),
    ("37.5ic", "tapeout_precheck", "reports/phase3/tapeout_precheck.json"),
)


def _P():
    import flow_declared_producer_run as P  # noqa: PLC0415
    return P


def _steps():
    yaml = pytest.importorskip("yaml")
    P = _P()
    doc = yaml.safe_load(FLOW_YAML.read_text(errors="replace"))
    return {str(s.get("id")): s for s in P._iter_steps(doc)}


# ── direction 1: the RUN produces the document ────────────────────────────

@pytest.mark.parametrize("sid,program,target", SPM_THIRTEEN,
                         ids=[f"{a}:{b}" for a, b, _ in SPM_THIRTEEN])
def test_the_flow_names_this_step_s_own_producer(sid, program, target):
    """All THREE declarations agree: `programs:` lists the program, the gate
    clause invokes it with `--json <target>`, and `<target>` is one of the
    step's `required_outputs`. Any one of the three missing is the producer
    gap."""
    P = _P()
    step = _steps().get(sid)
    assert step is not None, f"step {sid} is no longer in the shipped flow"
    programs = [str(p).strip() for p in (step.get("programs") or [])]
    outs = [str(o) for o in (step.get("required_outputs") or [])]
    assert program in programs, (
        f"step {sid} does not list {program} under programs:, so "
        f"flow_declared_producer_run cannot run it and {target} stays "
        f"audit_created")
    assert target in outs, f"step {sid} no longer declares {target}"
    cmds = P._iter_commands(step.get("gate") or {}, [])
    assert any(c.split()[:1] == [program] and P._JSON_RE.search(c)
               and P._JSON_RE.search(c).group(1) == target for c in cmds), (
        f"no gate clause of step {sid} invokes {program} --json {target}")


def test_every_one_of_the_13_is_a_declared_producer_clause():
    P = _P()
    got = {(c["step"], c["program"], c["target"])
           for c in P.declared_producer_clauses()}
    missing = [row for row in SPM_THIRTEEN if row not in got]
    assert not missing, f"not picked up by the mechanism: {missing}"


def test_an_absent_document_of_one_of_the_13_is_OWED(tmp_path):
    """Direction 1, executable: on a step the run performed (a sibling
    declared output is on disk) an absent target is owed, so the RUN writes
    it before the audit ever evaluates the gate."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if (c["step"], c["program"], c["target"]) == SPM_THIRTEEN[0]]
    assert clause, "step 10's clause is gone"
    sib = clause[0]["siblings"][0]
    (tmp_path / sib).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sib).write_text("the run performed step 10\n")
    to_run, skipped = P.owed(tmp_path, clause)
    assert [r["target"] for r in to_run] == [clause[0]["target"]], skipped


def test_a_document_the_run_already_wrote_at_one_of_the_13_is_left_alone(
        tmp_path):
    """The fix adds work, it never overwrites the run's own evidence."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if (c["step"], c["program"], c["target"]) == SPM_THIRTEEN[0]]
    sib = clause[0]["siblings"][0]
    (tmp_path / sib).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sib).write_text("x\n")
    tgt = tmp_path / clause[0]["target"]
    tgt.parent.mkdir(parents=True, exist_ok=True)
    tgt.write_text('{"program": "the run wrote this"}')
    to_run, skipped = P.owed(tmp_path, clause)
    assert not to_run
    assert tgt.read_text() == '{"program": "the run wrote this"}'


def test_a_step_this_delivery_never_performed_is_still_not_owed(tmp_path):
    """The guard that keeps 13 more clauses from manufacturing work: step
    26.5ic (die finishing) is a CHIP-path step, and a HARDMACRO delivery
    leaves none of its declared outputs on disk."""
    P = _P()
    clause = [c for c in P.declared_producer_clauses()
              if c["step"] == "26.5ic" and c["program"] == "die_finishing_check"]
    assert clause, "step 26.5ic's clause is gone"
    to_run, skipped = P.owed(tmp_path, clause)
    assert not to_run
    assert skipped and "did not perform this step" in skipped[0]["why"]


# ── direction 2: a gate-written document is STILL refused ─────────────────

def test_a_declared_output_with_NO_named_producer_yields_no_clause(tmp_path):
    """The other direction, and the reason the fix is 13 NAMED declarations
    rather than a rule. A step that declares its gate's `--json` target as a
    required_output but lists no `programs:` produces NO clause — nothing in
    the run writes it, and the audit's `audit_created` refusal is exactly
    right about it."""
    P = _P()
    y = tmp_path / "flow.yaml"
    y.write_text(
        "steps:\n"
        "  - id: Z1\n"
        "    required_outputs:\n"
        "      - \"reports/z1.json\"\n"
        "      - \"out/evidence.txt\"\n"
        "    gate:\n"
        "      all_of:\n"
        "        - program_exit_zero: \"z1_check . --json reports/z1.json\"\n")
    assert P.declared_producer_clauses(y) == []


def test_a_named_producer_whose_target_is_not_declared_yields_no_clause(
        tmp_path):
    """And the symmetric one: naming a program under `programs:` does not make
    every document it writes run evidence. Only a declared `required_output`
    is owed — 64 clauses in the shipped flow have this shape."""
    P = _P()
    y = tmp_path / "flow.yaml"
    y.write_text(
        "steps:\n"
        "  - id: Z2\n"
        "    programs: [z2_check]\n"
        "    required_outputs:\n"
        "      - \"out/evidence.txt\"\n"
        "    gate:\n"
        "      all_of:\n"
        "        - program_exit_zero: \"z2_check . --json reports/z2.json\"\n")
    assert P.declared_producer_clauses(y) == []


def test_the_13_are_derived_from_the_yaml_not_asserted_by_this_file():
    """The population must be RE-DERIVABLE: every pair above is a clause whose
    target the step declares and whose program the step lists, computed from
    the yaml. A row that stopped being true in the flow fails here rather than
    living on as a table."""
    P = _P()
    steps = _steps()
    derived = set()
    for sid, step in steps.items():
        outs = set(str(o) for o in (step.get("required_outputs") or []))
        progs = {str(p).strip() for p in (step.get("programs") or [])}
        for c in P._iter_commands(step.get("gate") or {}, []):
            parts = c.split()
            m = P._JSON_RE.search(c)
            if parts and m and parts[0] in progs and m.group(1) in outs:
                derived.add((sid, parts[0], m.group(1)))
    assert set(SPM_THIRTEEN) <= derived


def test_no_program_was_invented_for_this(tmp_path):
    """Every producer named is a program the tree already ships — the fix adds
    a declaration, never a file."""
    for _sid, program, _t in SPM_THIRTEEN:
        assert (PROGRAMS / f"{program}.py").is_file(), program
