"""icslot52 — a gate whose recorded verdict and recorded message disagreed.

THE INPUT, from run21's own `reports/audit/phase23_completion_audit.json`:

    {"name": "project_outputs_in_tree_check",
     "verdict": "FAIL",
     "message": "[INFO] project_outputs_in_tree_check: 1 reference(s) naming THIS
                 run through another mount of the same tree — non-blocking,
                 because the artefact is present in the run root at the same
                 run-relative pat"}

Exactly 200 characters: the HEAD of the gate's stdout. `project_outputs_in_tree_check`
prints its non-blocking disclosures FIRST and its `[FAIL]` line after them, so the
record paired a refusal with a sentence stating that nothing was wrong, and the
one line that explained the verdict was thrown away. That is the whole of the
INFO-vs-FAIL disagreement: the verdict is right, the message describes something
else, and no reader can reconcile them.

THIS IS THE SAME DEFECT `_p0_first_line` ALREADY EXISTS TO FIX, in the other
output shape. Its docstring: `testbench_exists_check` FAILed and the audit
recorded `"message": "{"` — "a refusal whose reason the artefact could not state,
in the field a reader goes to for exactly that. The gate was reporting fine; the
reader was taking `.split("\\n")[0]`." That repair covered JSON stdout and left
plain text still taking the literal first line.

So the plain-text branch now picks the line that CARRIES the refusal, ordered
most-decisive-first, exactly as the JSON branch picks the first ERROR-class
finding. It can only ever narrow to a MORE decisive line of the gate's own
output: a gate that leads with its verdict, or tags nothing, publishes precisely
what it published before.

MEASURED on a copy of run21, same gate, rc=1 both times:
  before  [INFO] … 2 in-tree self-reference(s) … non-blocking, and NOT this
          gate's question …
  after   [FAIL] … 1 blocking external-storage reference(s) … — this is what the
          gate exits 1 on:
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
FCC = PROGRAMS / "flow_compliance_check.py"
sys.path.insert(0, str(PROGRAMS))


def _fcc():
    spec = importlib.util.spec_from_file_location("fcc_r130", FCC)
    m = importlib.util.module_from_spec(spec)
    sys.modules["fcc_r130"] = m
    spec.loader.exec_module(m)
    return m


#: run21's shape: two non-blocking disclosures, then the line that decided it.
_RUN21_SHAPE = (
    "[INFO] project_outputs_in_tree_check: 2 in-tree self-reference(s) whose "
    "file is not on disk — non-blocking, and NOT this gate's question\n"
    "[FAIL] project_outputs_in_tree_check: 1 blocking external-storage "
    "reference(s) — this is what the gate exits 1 on:\n"
    "[INFO] project_outputs_in_tree_check: 8 in-tree self-reference(s) — "
    "in-tree by definition, non-blocking\n")


def test_a_refusal_publishes_the_line_that_refused():
    """THE DEFECT. The record must not pair FAIL with "non-blocking"."""
    m = _fcc()
    line = m._p0_first_line(_RUN21_SHAPE)
    assert line.startswith("[FAIL]"), line
    assert "blocking external-storage" in line
    assert "non-blocking" not in line


def test_a_gate_that_leads_with_its_verdict_is_unchanged():
    """CONTROL. Narrowing must be a no-op where the first line already decided."""
    m = _fcc()
    text = ("[FAIL] alpha_check: 3 finding(s)\n"
            "[INFO] alpha_check: scanned 12 file(s)\n")
    assert m._p0_first_line(text) == "[FAIL] alpha_check: 3 finding(s)"


def test_an_untagged_gate_keeps_its_historical_first_line():
    """CONTROL. No tag anywhere means no more decisive line exists, so the
    historical behaviour stands — this change adds no opinion about such gates."""
    m = _fcc()
    text = "beta_check: something went wrong\nmore detail here\n"
    assert m._p0_first_line(text) == "beta_check: something went wrong"


def test_an_error_tagged_finding_is_taken_when_there_is_no_fail_line():
    """`pad_ring_check`'s shape: its findings are `[ERROR]` lines and it prints no
    `[FAIL]`. The first of those is the line that refused."""
    m = _fcc()
    text = ("[INFO] padring: 15 IO LEF view(s) consulted\n"
            "[ERROR] PADRING_MASTERS_UNCORROBORATED: no PDK IO cell library "
            "resolved\n")
    line = m._p0_first_line(text)
    assert line.startswith("[ERROR] PADRING_MASTERS_UNCORROBORATED")


def test_a_cannot_determine_gate_publishes_that_line():
    """`macro_obs_geometry_intersect_check`'s shape — a refusal that is neither
    FAIL nor ERROR but is certainly not the INFO line above it."""
    m = _fcc()
    text = ("[INFO] macro_obs: inventory has 32 entries\n"
            "[CANNOT DETERMINE] macro_obs_geometry_intersect: physical view "
            "inventory is incomplete. NOT a pass.\n")
    assert m._p0_first_line(text).startswith("[CANNOT DETERMINE]")


def test_a_fail_line_outranks_an_earlier_error_line():
    """Ordering is deliberate: in this repo `[FAIL]` is the gate's VERDICT tag
    while `[ERROR]` is usually one of its findings, so the verdict line wins even
    when a finding was printed first."""
    m = _fcc()
    text = ("[ERROR] gamma_check: finding one\n"
            "[FAIL] gamma_check: 2 blocking finding(s) — the verdict\n")
    assert m._p0_first_line(text).startswith("[FAIL]")


def test_the_json_shape_is_untouched():
    """REGRESSION GUARD for the repair this one is modelled on (#497): a JSON
    report still renders its deciding finding, not `{`."""
    m = _fcc()
    text = json.dumps({"findings": [
        {"severity": "WARN", "category": "COSMETIC", "message": "minor"},
        {"severity": "ERROR", "category": "TB_ABSENT", "message": "no testbench"},
    ]})
    assert m._p0_first_line(text) == "TB_ABSENT: no testbench"


def test_empty_output_is_empty():
    m = _fcc()
    assert m._p0_first_line("") == ""
    assert m._p0_first_line("   \n  ") == ""


def test_the_published_line_is_capped():
    """The 200-char cap is what truncated run21's message; it stays, so nothing
    about record size changes — only WHICH line is cut to fit."""
    m = _fcc()
    long_fail = "[FAIL] delta_check: " + ("x" * 500)
    assert len(m._p0_first_line("[INFO] a\n" + long_fail)) == 200
