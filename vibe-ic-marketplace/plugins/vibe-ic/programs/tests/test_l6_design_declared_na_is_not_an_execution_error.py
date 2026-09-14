"""test_l6_design_declared_na_is_not_an_execution_error.py — a design whose own
L6 declares no FSM is an N/A BY DECLARATION, not a gate that failed to examine
its input (lane icadc, 2026-09-15).

THE DEFECT THIS COVERS, MEASURED on a real front-door run of a converter whose
input declares no digital control logic.  `l6_fsm_scaffold_actionable_check`
examined the design's L6, found it POSITIVELY DECLARES no FSM, and returned its
disclosed-skip rc=2 with `verdict: SKIP`.  The flow's classifier then booked it:

    GATE_RAN l6_fsm_scaffold_actionable_check rc=2 INCOMPLETE
             reason_class=EXECUTION_ERROR
    step D1 -> INCOMPLETE: "the gate reports its input was applicable and was
             NOT examined"

Nothing had gone wrong in the execution and the input HAD been examined.  The
only reason the shared classifier reached EXECUTION_ERROR is that
`_flow_reason_taxonomy._DECLARED_NA_RE` recognises "declared no" and "do not
declare" and did not recognise "declares no" — one verb inflection.

THE RECOGNISER WAS NOT WIDENED, DELIBERATELY.  "declares no" appears in 469
files in this tree and several of them are real faults —
`flow_dependency_graph_check` says "declares no steps" about a flow definition
that is broken.  Teaching the shared fail-closed default to accept that
inflection would launder those into skips.  One gate states its own class
instead, and these tests pin the CLASSIFICATION rather than the wording, so a
future rewording cannot regress it silently.

BOTH DIRECTIONS.  The design-declared path must classify skip-eligible; the
three paths that are an ABSENT or BROKEN artefact must not, because those are
faults and the fail-closed default is right for them.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _flow_reason_taxonomy as taxonomy              # noqa: E402
import l6_fsm_scaffold_actionable_check as gate       # noqa: E402


def _project(tmp_path: Path, l6: dict | None) -> Path:
    proj = tmp_path / "p"
    gd = proj / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    if l6 is not None:
        (gd / "L6_CONTROL_LOGIC.json").write_text(json.dumps(l6),
                                                  encoding="utf-8")
    (gd / "L1_DATASHEET.json").write_text(
        json.dumps({"ic_name": "synth_part"}), encoding="utf-8")
    return proj


def _declares_no_fsm() -> dict:
    """The design's own control-logic layer stating there is no FSM."""
    return {"fsm_states": [], "fsm_machines": [],
            "no_fsm_in_input": True, "no_fsm_states_in_input": True}


# ── the declared-N/A direction ─────────────────────────────────────────────
def test_a_design_that_declares_no_fsm_publishes_the_class(tmp_path):
    res = gate.evaluate(_project(tmp_path, _declares_no_fsm()))
    assert res["verdict"] == "SKIP", res
    assert res.get("reason_class") == taxonomy.DESIGN_DECLARED_NA, res


def test_that_class_is_skip_eligible_so_the_step_is_not_INCOMPLETE(tmp_path):
    """The whole point: `record_verdict` on this class must be a skip tier.
    EXECUTION_ERROR renders the consuming step INCOMPLETE, which is what put
    D1 in the blocker list for a design that had declared the absence."""
    res = gate.evaluate(_project(tmp_path, _declares_no_fsm()))
    assert res["reason_class"] in taxonomy.SKIP_ELIGIBLE, res
    assert taxonomy.record_verdict(res["reason_class"]) == "SKIP"


def test_the_sentence_alone_classifies_correctly_on_the_bare_wiring(tmp_path):
    """THE ARM THAT MATTERS FOR THE FLOW AS WIRED. `flow/
    phase1_phase2_phase3.yaml` runs this gate BARE — no `--json` — so
    `flow_compliance_check._command_json_report` has no report to read and the
    typed field above is invisible on that path. What the classifier actually
    sees is this gate's STDOUT. Asserting the classification of the emitted
    sentence, not the sentence itself, is what keeps a reword honest."""
    res = gate.evaluate(_project(tmp_path, _declares_no_fsm()))
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP", message=res["reason"]) == taxonomy.DESIGN_DECLARED_NA


# ── the directions that must NOT be laundered ──────────────────────────────
def test_a_missing_l6_is_not_a_design_declaration(tmp_path):
    """An ABSENT layer is not a design saying "there is no FSM" — nobody said
    anything. It must not reach the skip-eligible tier on this gate's word."""
    res = gate.evaluate(_project(tmp_path, None))
    assert res["verdict"] == "SKIP"
    assert res.get("reason_class") != taxonomy.DESIGN_DECLARED_NA, res
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP", message=res["reason"]) \
        is not taxonomy.DESIGN_DECLARED_NA


def test_an_unparseable_l6_stays_an_execution_error(tmp_path):
    proj = _project(tmp_path, None)
    (proj / "phase1" / "generated_docs" / "L6_CONTROL_LOGIC.json").write_text(
        "{not json", encoding="utf-8")
    res = gate.evaluate(proj)
    assert res.get("reason_class") != taxonomy.DESIGN_DECLARED_NA, res
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP", message=res["reason"]) == taxonomy.EXECUTION_ERROR


def test_an_l6_that_is_not_an_object_stays_an_execution_error(tmp_path):
    proj = _project(tmp_path, None)
    (proj / "phase1" / "generated_docs" / "L6_CONTROL_LOGIC.json").write_text(
        "[1, 2, 3]", encoding="utf-8")
    res = gate.evaluate(proj)
    assert res.get("reason_class") != taxonomy.DESIGN_DECLARED_NA, res
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP", message=res["reason"]) == taxonomy.EXECUTION_ERROR


def test_a_design_that_DOES_declare_an_fsm_is_not_class_exempted(tmp_path):
    """THE OVER-APPLY DIRECTION. A design with a real FSM must never carry the
    design-declared-N/A class: this gate has to keep holding it to its own
    declaration."""
    res = gate.evaluate(_project(tmp_path, {
        "fsm_states": ["idle", "run", "done"],
        "no_fsm_in_input": False, "no_fsm_states_in_input": False}))
    assert res.get("reason_class") != taxonomy.DESIGN_DECLARED_NA, res


# ── the shared default must stay fail-closed ───────────────────────────────
def test_the_shared_recogniser_was_not_widened():
    """A fault sentence that happens to contain "declares no" must STILL be an
    execution error. This is the reason the fix is one gate's own statement and
    not a looser regex: `flow_dependency_graph_check` says exactly this about a
    flow definition that is broken."""
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP",
        message="the flow declares no steps — a check that scanned it read 0"
    ) != taxonomy.DESIGN_DECLARED_NA


def test_a_zero_denominator_is_still_a_zero_denominator():
    """Ordering guard: the declared-N/A recogniser runs LAST, so a counted-zero
    sentence cannot be laundered into a design N/A by the word "no"."""
    assert taxonomy.infer_nonverdict_reason(
        verdict="SKIP", message="declared no rule; examined=0"
    ) == taxonomy.ZERO_DENOMINATOR
