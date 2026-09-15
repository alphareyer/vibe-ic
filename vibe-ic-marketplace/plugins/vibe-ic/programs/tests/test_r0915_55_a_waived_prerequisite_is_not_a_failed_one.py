"""R-0915-55: the OS-constraints promotion, and what its prerequisite means.

MEASURED 2026-09-16 on the SPM verdict run (run31 / `spm26`, main bdfb62631).
`stage2_compliance` reported `"overall": "FAIL"` with `PASS=6 FAIL=0 MISSING=0
INCOMPLETE=0` — the design failed nothing and missed nothing. The chain:

  1. step 12 (Post-DFT optimisation) is in
     `_OPEN_SOURCE_CONTAINER_BLOCKED_STEPS`, so it lands in
     `oss_blocked_skipped`, `ok` goes False and `overall` becomes FAIL;
  2. the flow's own comment says that FAIL is meant to be promoted straight
     back out to PASS_WITH_OPEN_SOURCE_CONSTRAINTS — "the gap has to travel
     THROUGH the verdict rather than around it";
  3. the promotion never fired, because it required every prerequisite step to
     be PASS and step 6 is WAIVED under the flow's OWN declared capability gap
     (ENV_UNAVAILABLE fpga-board-prototype,
     ticket=fpga-board-prototype-capgap-v1.0.18, review_required=True,
     approver="field-agent-attest (fpga-board cap-gap tier)").

SPM was refused by a promotion whose precondition was defeated by the same
class of gap the promotion exists to forgive.

PART 1 — the prerequisite must NOT HAVE FAILED, which is not the same as
having passed. A waiver counts ONLY when the flow recorded what makes it
reviewable: a ticket, an approver, and review_required. FAIL, MISSING,
INCOMPLETE, or a waiver missing any of those, still block — nobody has
undertaken to close those. The promoted verdict CARRIES the waiver rows and
is PASS_WITH_OPEN_SOURCE_CONSTRAINTS, never bare PASS.

PART 2 — the prerequisite ids are DERIVED FROM THE FLOW by role name. They
were hand-typed, and the second one was WRONG: the table said `36, # FPGA
final sign-off`, but step 36 in this flow is "Tapeout checklist" and FPGA
final sign-off is step **39**. The step was renumbered and the table was not
moved with it; the error was masked because step 36 happens to PASS, so the
condition silently measured a different step from the one it named.

chip-AGNOSTIC: synthetic flows and stub results in tmp_path.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402

TICKET = "fpga-board-prototype-capgap-v1.0.18"
APPROVER = "field-agent-attest (fpga-board cap-gap tier)"


def _result(sid, status, name="a step"):
    return SimpleNamespace(id=sid, status=status, name=name, reasons=[])


def _full_waiver(**over):
    w = {"ticket": TICKET, "approver": APPROVER, "review_required": True,
         "verdict_tier": "ENV_UNAVAILABLE",
         "evidence": ["reports/phase2/fpga/quartus_map_audit.json"]}
    w.update(over)
    return w


def _satisfied(status, waiver=None, sid=6):
    return F._os_constraints_prereq_satisfied(
        _result(sid, status), {sid: waiver} if waiver else {})


# ── part 1: what counts as "has not failed" ──────────────────────────────

def test_a_passing_prerequisite_satisfies_it():
    assert _satisfied("PASS") is True


def test_a_waived_prerequisite_with_ticket_and_approver_satisfies_it():
    """THE SPM CASE. The flow itself declared this gap, gave it a ticket, an
    approver and review_required — that is an undertaking to close it, not a
    failure."""
    assert _satisfied("WAIVED", _full_waiver()) is True


@pytest.mark.parametrize("status", ["FAIL", "MISSING", "INCOMPLETE"])
def test_a_failed_or_missing_prerequisite_still_blocks(status):
    """THE NEGATIVE CONTROL. Nothing about this ruling forgives a real
    failure."""
    assert _satisfied(status) is False
    assert _satisfied(status, _full_waiver()) is False, (
        "a waiver entry must not rescue a step the flow did not waive")


@pytest.mark.parametrize("drop", ["ticket", "approver", "review_required"])
def test_a_waiver_missing_its_accountability_still_blocks(drop):
    """THE OTHER NEGATIVE CONTROL, and the point of the rule: a waiver with no
    ticket, no approver, or no review_required is nobody's undertaking to
    close anything, so it cannot buy a promotion."""
    w = _full_waiver()
    w.pop(drop)
    assert _satisfied("WAIVED", w) is False, drop


def test_a_waived_prerequisite_with_no_waiver_entry_at_all_blocks():
    assert _satisfied("WAIVED", None) is False


def test_the_waiver_entry_the_flow_actually_writes_is_accepted():
    """Keyed on the SHIPPED shape rather than my paraphrase of it: this is the
    entry `_fpga_skip` builds, field for field."""
    entry = {
        "reason": ("ENV_UNAVAILABLE (fpga-board-prototype cap-gap): ... "
                   f"[ticket={TICKET}, review_required=True, "
                   "cap:fpga_board_prototype]"),
        "approver": APPROVER,
        "ticket": TICKET,
        "verdict_tier": "ENV_UNAVAILABLE",
        "review_required": True,
        "evidence": ["reports/phase2/fpga/quartus_map_audit.json"],
        "_env_unavailable": True,
        "_fpga_skip": True,
    }
    assert _satisfied("WAIVED", entry) is True


# ── part 2: the table is derived from the flow ───────────────────────────

def test_the_table_resolves_to_the_steps_the_flow_names_today():
    ids, unresolved = F._derive_os_constraints_prereq_steps()
    assert unresolved == (), unresolved
    assert ids == (6, 39)


def test_the_literal_36_is_not_in_the_table():
    """Step 36 is "Tapeout checklist (final sign-off confirmation)". The old
    hand-typed table named it for "FPGA final sign-off", which is step 39."""
    assert 36 not in F._OS_CONSTRAINTS_PREREQ_STEPS
    assert 39 in F._OS_CONSTRAINTS_PREREQ_STEPS


def _plant(tmp_path, pairs):
    y = tmp_path / "flow.yaml"
    body = "steps:\n" + "".join(
        f'  - id: {i}\n    name: "{n}"\n    stage: stage1\n' for i, n in pairs)
    y.write_text(textwrap.dedent(body))
    return y


def test_a_renumbered_flow_moves_the_table_with_it(tmp_path):
    """THE POINT OF DERIVING IT. Renumber both steps and the table follows;
    the hand-typed version did not, which is how 36 stayed in it."""
    y = _plant(tmp_path, [
        (101, "FPGA early prototype + verification report audit"),
        (150, "Tapeout checklist (final sign-off confirmation)"),
        (202, "FPGA final sign-off (recompile + on-board test)"),
    ])
    ids, unresolved = F._derive_os_constraints_prereq_steps(y)
    assert unresolved == ()
    assert ids == (101, 202)
    assert 150 not in ids


def test_a_role_that_names_no_step_FAILS_CLOSED(tmp_path):
    """`all(())` is True, so a derivation that quietly resolved nothing would
    promote EVERY run unconditionally. It must be reported instead."""
    y = _plant(tmp_path, [(1, "Something else entirely")])
    ids, unresolved = F._derive_os_constraints_prereq_steps(y)
    assert ids == ()
    assert set(unresolved) == set(F._OS_CONSTRAINTS_PREREQ_ROLES)


def test_an_ambiguous_rename_also_FAILS_CLOSED(tmp_path):
    """Two steps matching one role is not a licence to pick one."""
    y = _plant(tmp_path, [
        (1, "FPGA early prototype + verification report audit"),
        (2, "FPGA early prototype + verification report audit (retry)"),
        (3, "FPGA final sign-off (recompile + on-board test)"),
    ])
    ids, unresolved = F._derive_os_constraints_prereq_steps(y)
    assert "FPGA early prototype" in unresolved
    assert ids == (3,)


def test_an_unreadable_flow_FAILS_CLOSED(tmp_path):
    y = tmp_path / "nope.yaml"
    ids, unresolved = F._derive_os_constraints_prereq_steps(y)
    assert ids == ()
    assert set(unresolved) == set(F._OS_CONSTRAINTS_PREREQ_ROLES)


# ── the promoted verdict carries the waivers, and can print them ─────────

def test_a_capability_gap_row_renders_its_ticket_not_a_tool():
    """REGRESSION. The first cut rendered these rows with the commercial-tool
    format and took the whole audit down with
    `KeyError: 'commercial_tool_required'` — after the verdict had already
    promoted, so the run showed a green Overall line and then a traceback."""
    line = F._render_os_deferral({
        "kind": "capability-gap-waiver",
        "step_id": 6,
        "step_name": "FPGA early prototype + verification report audit",
        "status": "WAIVED",
        "verdict_tier": "ENV_UNAVAILABLE",
        "ticket": TICKET,
        "approver": APPROVER,
        "review_required": True,
    })
    assert "WAIVED under ENV_UNAVAILABLE capability gap" in line
    assert TICKET in line
    assert APPROVER in line
    assert "needs" not in line, "it needs a ticket closed, not a tool"


def test_a_commercial_tool_row_still_renders_its_tool():
    line = F._render_os_deferral({
        "step_id": 12,
        "step_name": "Post-DFT optimization (resynth / buffering)",
        "status": "SKIPPED-CONDITION",
        "commercial_tool_required": "Post-DFT optimisation (Design Compiler)",
        "review_required": True,
    })
    assert "needs Post-DFT optimisation (Design Compiler)" in line


def test_a_row_missing_its_tool_does_not_crash_the_audit():
    """A malformed row must degrade to a '?' rather than take the verdict
    down after it has been computed."""
    line = F._render_os_deferral({"step_id": 99, "step_name": "x",
                                  "status": "SKIPPED-CONDITION"})
    assert "?" in line


def test_a_P0_row_falls_through_to_its_own_renderer():
    assert F._render_os_deferral({
        "step_id": "P0", "step_name": "umbrella", "status": "FAIL",
        "p0_thin_input_subgates": [{"sub_gate": "x", "rationale": "y"}],
    }) is None
