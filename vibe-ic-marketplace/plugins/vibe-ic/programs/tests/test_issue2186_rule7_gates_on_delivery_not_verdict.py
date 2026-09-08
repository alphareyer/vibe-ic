"""Rule 7 must gate on the predecessor's DELIVERY, not on its verdict word.

THE MEASUREMENT THIS IS WRITTEN FROM (vibe-ic#2186, lane rbadc3, 8HD-8,
2026-09-07, design `u_hawaii_adc`, PDK `ihp-sg13g2`, through the front door).
The analog stage published:

    Blocker list (classified) — 6 non-PASS step(s).
      PLUGIN_DEFECT=0  DESIGN_FACT=0  MISSING_CAPABILITY=0  UNCLASSIFIED=6

Two of those six had run. Step A5 drew two layouts (`layout.mag` + `.gds` for
`delta_sigma` and `ldo`) and failed on a rule it evaluated against a drawn
device; step A6 ran per-block physical verification to completion — 560 KLayout
sign-off rules, zero violations, on BOTH blocks — extracted a netlist per block
and returned `ldo: match`, `delta_sigma: mismatch`. Both were published under
`basis: derived-from-upstream — nothing here measures this design until that is
closed`, which is false of both.

The cause is an ORDERING that is correct and a PREDICATE that is not. Rule 7 is
ordered before the design-fact rule deliberately, and its own comment says why:

    a gate that ran, and failed, on a step whose declared input NEVER ARRIVED
    produced a number about an incomplete tree

The rule tested `did the predecessor PASS`. Those are different questions. A5's
predecessor A4 is INCOMPLETE because ONE sub-gate
(`analog_adc_enob_corner_check`) returned `UNMEASURED / ZERO_DENOMINATOR`; its
declared output arrived complete for both blocks, nine real ngspice corners
each. So the run reported that it had established no fact about the design
while holding several, including the one that should be fixed.

WHAT THESE TESTS ARE WRITTEN AGAINST: the observable classification of step
records, in both directions. A different correct implementation passes them.
The calibration case the ordering exists for — `si_mcf_sta_check` reporting
`NO_SPEF` under a MISSING parasitic-extraction step — is asserted here too, and
must NOT move; a change that promotes it is exactly the over-correction.

ANTI-TAUTOLOGY, measured rather than asserted. This module imports only
`_blocker_classification`, which exists on the base tree, so it COLLECTS there
and every failure is a real one. Measured by swapping the base file back in
(`git show <base>:<path> > <path>`, never `git stash`) with these tests
unchanged: **4 failed, 2 passed**, and the four split honestly —

    CONTENT   test_delivered_predecessor_leaves_the_consumer_a_design_fact
    CONTENT   test_the_hawaii_adc_analog_chain_publishes_two_design_facts
    CONTENT   test_every_blocker_class_stays_reachable
    SIGNATURE test_undetermined_delivery_is_not_read_as_delivered  (TypeError on
              the new keyword; it proves the parameter exists, not a behaviour)

    PASS both trees:
              test_absent_predecessor_output_keeps_the_calibration_case_derived
              test_a_predecessor_whose_own_absence_line_fired_still_blocks

The two that pass on BOTH trees are the point of the other direction: this
change must not move them.
"""
from __future__ import annotations

import importlib

_BC = importlib.import_module("_blocker_classification")


def _step(sid, status, **kw):
    rec = {
        "id": sid,
        "name": kw.pop("name", f"step {sid}"),
        "stage": kw.pop("stage", "analog"),
        "status": status,
        "reasons": kw.pop("reasons", []),
        "evidence": kw.pop("evidence", []),
        "gate_output": "",
        "cascade_note": kw.pop("cascade_note", ""),
        "self_skip_disclosed": kw.pop("self_skip_disclosed", False),
        "structure_only_disclosed": kw.pop("structure_only_disclosed", False),
        "gate_records": kw.pop("gate_records", None),
    }
    rec.update(kw)
    return rec


def _by_id(blockers):
    return {b["step_id"]: b for b in blockers}


# ── direction 1: a predecessor that DELIVERED does not disqualify ───────────
def test_delivered_predecessor_leaves_the_consumer_a_design_fact():
    """A4 INCOMPLETE with both declared outputs present; A5 ran and failed.

    A5's class must be DESIGN_FACT, and the `derived_from` attribution must
    still name A4 — which step this is a consequence OF and what this step IS
    are different questions and stay in different fields.
    """
    flow = [
        {"id": "A4", "name": "corner sweep", "blocks_on": [],
         "required_outputs": ["phase3/analog/*/corner_results.json"]},
        {"id": "A5", "name": "analog layout", "blocks_on": ["A4"],
         "required_outputs": ["phase3/analog/*/layout.mag"],
         "gate": {"program_exit_zero": "analog_a5_layout_check ."}},
    ]
    steps = [
        _step("A4", "INCOMPLETE",
              reasons=["INCOMPLETE: the gate reports its input was applicable "
                       "and was NOT examined: analog_adc_enob_corner_check"],
              evidence=["phase3/analog/delta_sigma/corner_results.json",
                        "phase3/analog/ldo/corner_results.json"]),
        _step("A5", "FAIL",
              reasons=["program failed: analog_a5_layout_check . --json "
                       "reports/analog/a5_layout.json",
                       "output: FAIL — 1 finding(s): [delta_sigma] "
                       "A5_DEVICE_ABOVE_PDK_MAXIMUM"],
              evidence=["phase3/analog/delta_sigma/layout.mag",
                        "phase3/analog/ldo/layout.mag"]),
    ]
    a5 = _by_id(_BC.build_blockers(steps, flow_steps=flow))["A5"]
    assert a5["classification"] == "DESIGN_FACT"
    assert a5["basis"] == "gate-reached-verdict"
    assert a5["derived_from"] == ["A4"]


# ── direction 2: the calibration case must not move ─────────────────────────
def test_absent_predecessor_output_keeps_the_calibration_case_derived():
    """`si_mcf_sta_check`/NO_SPEF under a MISSING step 22.

    The predecessor produced nothing, so the downstream number is about an
    incomplete tree. This is the case rule 7's ordering exists for and the one
    a fix to it is most likely to break.
    """
    flow = [
        {"id": 22, "name": "parasitic extraction", "blocks_on": [],
         "required_outputs": ["phase3/stage3/spef/*.spef"]},
        {"id": 23, "name": "post-route STA", "blocks_on": [22],
         "gate": {"program_exit_zero": "si_mcf_sta_check ."}},
    ]
    steps = [
        _step(22, "MISSING",
              reasons=["no required_outputs found (expected: "
                       "['phase3/stage3/spef/*.spef'])"]),
        _step(23, "FAIL",
              reasons=["program failed: si_mcf_sta_check . --json "
                       "reports/sta.json",
                       "output: NO_SPEF — setup worst-slack -8.830 ns"]),
    ]
    s23 = _by_id(_BC.build_blockers(steps, flow_steps=flow))[23]
    assert s23["classification"] == "UNCLASSIFIED"
    assert s23["basis"] == "derived-from-upstream"
    # the observation is not thrown away, only refused promotion
    assert "-8.830 ns" in s23["observed"]


def test_a_predecessor_whose_own_absence_line_fired_still_blocks():
    """A predecessor can reach a verdict word and STILL not have delivered.

    A step that FAILed on a `files_exist` clause says so in the producer's own
    absence line. Reading only its status word would promote its consumers.
    """
    flow = [
        {"id": "A7", "name": "post-layout resim", "blocks_on": [],
         "required_outputs": ["phase3/analog/*/pre_vs_post.json"]},
        {"id": "A8", "name": "hardmacro", "blocks_on": ["A7"],
         "gate": {"program_exit_zero": "analog_hardmacro_check ."}},
    ]
    steps = [
        _step("A7", "FAIL",
              reasons=["missing files (any_of=['phase3/analog/*/"
                       "pre_vs_post.json']): ['phase3/analog/*/"
                       "pre_vs_post.json']"],
              evidence=["phase3/analog/ldo/notes.txt"]),
        _step("A8", "FAIL",
              reasons=["program failed: analog_hardmacro_check ."]),
    ]
    a8 = _by_id(_BC.build_blockers(steps, flow_steps=flow))["A8"]
    assert a8["classification"] == "UNCLASSIFIED"
    assert a8["basis"] == "derived-from-upstream"


def test_undetermined_delivery_is_not_read_as_delivered():
    """`predecessors_missing_outputs` is three-state.

    THE ONE TEST HERE THAT NAMES THE NEW API. Against the base tree it fails on
    `TypeError: unexpected keyword argument`, not on content — said plainly
    because a signature failure proves the parameter exists and nothing about
    behaviour. The other five are content tests and collect on either tree.

    A caller that did not determine delivery gets the conservative answer —
    every non-PASS predecessor blocks. `None` must never be read as "[] i.e.
    they all delivered", which is the direction that manufactures a class.
    """
    step = _step("A6", "FAIL",
                 reasons=["program failed: analog_a6_block_pv_check ."])
    undetermined = _BC.classify(step, non_pass_predecessors=["A5"])
    determined = _BC.classify(step, non_pass_predecessors=["A5"],
                              predecessors_missing_outputs=[])
    assert undetermined[0] == "UNCLASSIFIED"
    assert undetermined[1] == "derived-from-upstream"
    assert determined[0] == "DESIGN_FACT"


# ── the run this was measured from, end to end ──────────────────────────────
def test_the_hawaii_adc_analog_chain_publishes_two_design_facts():
    """The A4..A9 chain from the run in #2186, in the shapes it published.

    Reproduced from the read-only stage report at
    `/home/<your-user>/_lane_rbadc3/proj/reports/analog/stage_analog_compliance.json`
    (8HD-8). Predicted before running: DESIGN_FACT=2 (A5, A6) and UNCLASSIFIED=4
    (A4 on its own disclosure tier, A7 and A9 on their cascade notes, A8 because
    its predecessor A7 is MISSING and delivered nothing). Measured: exactly that.
    """
    flow = [{"id": f"A{i}", "name": f"A{i}",
             "blocks_on": [] if i == 4 else [f"A{i - 1}"],
             "required_outputs": [f"phase3/analog/*/a{i}.json"]}
            for i in range(4, 10)]
    cascade = ("blocked-by-upstream(step A5): cascade of the first mid-chain "
               "FAIL — the chain stops at step A5")
    steps = [
        _step("A4", "INCOMPLETE",
              reasons=["INCOMPLETE: the gate reports its input was applicable "
                       "and was NOT examined: analog_adc_enob_corner_check"],
              evidence=["phase3/analog/delta_sigma/corner_results.json",
                        "phase3/analog/ldo/corner_results.json"]),
        _step("A5", "FAIL",
              reasons=["program failed: analog_a5_layout_check .",
                       "output: FAIL — 1 finding(s): [delta_sigma] "
                       "A5_DEVICE_ABOVE_PDK_MAXIMUM"],
              evidence=["phase3/analog/delta_sigma/layout.mag",
                        "phase3/analog/ldo/ldo.gds"]),
        _step("A6", "FAIL",
              reasons=["program failed: analog_a6_block_pv_check .",
                       "output: verdict: FAIL (1/2 block(s) DRC-0 + "
                       "LVS-match)"],
              evidence=["phase3/analog/delta_sigma/drc.report",
                        "phase3/analog/ldo/comp.json"]),
        _step("A7", "MISSING",
              reasons=["no required_outputs found (expected: "
                       "['phase3/analog/*/pre_vs_post.json'])", cascade],
              cascade_note=cascade),
        _step("A8", "FAIL",
              reasons=["program failed: analog_hardmacro_check ."],
              evidence=["phase3/analog/hardmacro/ldo/ldo.gds"]),
        _step("A9", "MISSING",
              reasons=["no required_outputs found (expected: "
                       "['phase3/mixed_signal/cosim/*.json'])", cascade],
              cascade_note=cascade),
    ]
    blockers = _BC.build_blockers(steps, flow_steps=flow)
    got = _by_id(blockers)
    assert _BC.class_counts(blockers) == {
        "PLUGIN_DEFECT": 0, "DESIGN_FACT": 2,
        "MISSING_CAPABILITY": 0, "UNCLASSIFIED": 4}
    assert got["A5"]["classification"] == "DESIGN_FACT"
    assert got["A6"]["classification"] == "DESIGN_FACT"
    for sid in ("A4", "A7", "A8", "A9"):
        assert got[sid]["classification"] == "UNCLASSIFIED", sid


# ── the standing guard: no class may become unreachable ─────────────────────
def test_every_blocker_class_stays_reachable():
    """One corpus, one step per class, membership compared as a SET.

    A classifier that returns one word for everything is not classifying — the
    run in #2186 published UNCLASSIFIED for all six of its blockers. This test
    fails if ANY class in the closed vocabulary stops being reachable, in
    either direction: a rule that stops firing and a rule that swallows its
    neighbours both show up as a set that is not equal.
    """
    flow = [
        {"id": "P", "name": "crashed gate", "blocks_on": []},
        {"id": "D0", "name": "delivered predecessor", "blocks_on": [],
         "required_outputs": ["phase3/analog/*/corner_results.json"]},
        {"id": "D", "name": "gate reached a verdict", "blocks_on": ["D0"],
         "gate": {"program_exit_zero": "a_check ."}},
        {"id": "M", "name": "named capability gap", "blocks_on": []},
        {"id": "U", "name": "killed gate", "blocks_on": []},
    ]
    steps = [
        _step("P", "FAIL", reasons=[f"{_BC.CRASH_MARKER} Traceback"]),
        _step("D0", "INCOMPLETE", reasons=["INCOMPLETE: one sub-gate"],
              evidence=["phase3/analog/ldo/corner_results.json"]),
        _step("D", "FAIL", reasons=["program failed: a_check ."]),
        _step("M", "SKIPPED-SETUP-REQUIRED", reasons=["setup absent"]),
        _step("U", "FAIL", reasons=[f"{_BC.TIMEOUT_MARKER} 0 progress"]),
    ]
    reached = {b["classification"]
               for b in _BC.build_blockers(steps, flow_steps=flow)}
    assert reached == set(_BC.BLOCKER_CLASSES)
