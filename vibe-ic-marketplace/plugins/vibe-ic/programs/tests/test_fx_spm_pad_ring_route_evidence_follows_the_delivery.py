"""FX_SPM_GATES_2 (2): the pad-ring route attestation is owed by a die only.

MEASURED on the same-RTL spm x gf180mcuD run (lane cmpb N5; deliverable
HARDMACRO, a core-only design with no pad ring): step 37 FAILED
`required_outputs missing: ['reports/phase3/pad_ring_route_evidence.json']`
(missing_artefact). Its only producer, `step_pad_ring_final_evidence`, runs
only under `_chip_path_requests_pad_ring(project)` -- step 15.5ic's condition
-- so the flow owed a HARDMACRO an artefact its producer correctly never
writes.

The rule: step 37 keeps declaring the attestation (every other reader --
matrix pins, the publisher, d4 grounding -- sees the step unchanged), and
`output_conditions` makes that ONE entry NOT_APPLICABLE when, and only when,
the design's own delivery declaration says there is no die (the #2277
`delivery_declares` clause, cited in the reason). A DIE stays held to it.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(HERE))

import pytest                                            # noqa: E402

import _tapeout_declaration as TD                        # noqa: E402
import flow_compliance_check as F                        # noqa: E402
import test_issue2277_hardmacro_owes_no_die_steps as H   # noqa: E402

EVIDENCE = "reports/phase3/pad_ring_route_evidence.json"
GDS = "phase3/stage4/gds/top.gds"


def _step37():
    return H._steps()["37"]


def _mini_step():
    """Step 37's own declaration of the attestation and its output condition,
    with a gate that only asks for the GDS -- so the verdict is decided by
    the required_outputs rule under test, not by the real step's programs."""
    real = _step37()
    return {"id": "37", "name": real["name"], "stage": real["stage"],
            "required_outputs": ["phase3/stage4/gds/*.gds", EVIDENCE],
            "output_conditions": real.get("output_conditions"),
            "gate": {"all_of": [{"files_exist": ["phase3/stage4/gds/*.gds"]}]},
            "blocks_on": []}


def _run(tmp_path, *, with_evidence=False, **kw):
    project = H._project(tmp_path, **kw)
    gds = project / GDS
    gds.parent.mkdir(parents=True, exist_ok=True)
    gds.write_bytes(b"\x00\x06\x00\x02\x02\x58" * 64)
    if with_evidence:
        ev = project / EVIDENCE
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text('{"verdict": "PASS"}\n')
    return F.check_step(project, _mini_step(), {})


def test_step_37_still_declares_it_with_the_die_steps_own_clause():
    step = _step37()
    assert EVIDENCE in step["required_outputs"]
    cond = (step.get("output_conditions") or {}).get(EVIDENCE)
    assert cond, "step 37 declares no condition for the attestation"
    assert cond["delivery_declares"] == \
        H._steps()["15.5ic"]["condition"]["delivery_declares"]


def test_a_declared_hardmacro_does_not_owe_the_route_attestation(tmp_path):
    """RED on main: `required_outputs missing: [...pad_ring_route_evidence...]`."""
    r = _run(tmp_path)                                   # HARDMACRO, no slot
    blob = " ".join(r.reasons)
    assert r.status != "FAIL", blob
    assert f"required_outputs missing" not in blob
    assert f"declared output {EVIDENCE!r} NOT_APPLICABLE for this delivery" in blob
    assert "tapeout_declaration.json" in blob and "HARDMACRO" in blob


@pytest.mark.parametrize("kw", [
    {"deliverable": "DIE"},
    {"deliverable": "DIE", "self_tapeout": True},
    {"declaration": False},
], ids=["die", "die-self-tapeout", "unreadable-declaration"])
def test_a_die_still_owes_it(tmp_path, kw):
    r = _run(tmp_path, **kw)
    assert r.status == "FAIL" and r.reason_class == "missing_artefact", r.reasons
    assert any(f"required_outputs missing: ['{EVIDENCE}']" in x for x in r.reasons)


def test_a_die_that_has_it_passes(tmp_path):
    r = _run(tmp_path, with_evidence=True, deliverable="DIE")
    assert r.status != "FAIL", r.reasons


@pytest.mark.parametrize("kw", [
    {}, {"deliverable": "DIE"}, {"deliverable": "DIE", "self_tapeout": True},
], ids=["hardmacro", "die", "die-self-tapeout"])
def test_the_requirement_asks_the_producers_own_question(tmp_path, kw):
    """Owed exactly when the runner writes the attestation.

    NOT PINNED HERE, a pre-existing split for the owner (FX_SPM_GATES_2
    report): a HARDMACRO that BOUGHT a slot. #2277's `delivery_declares`
    keeps die outputs owed for a purchase, while the producer's
    `_tapeout_declaration.requests_pad_ring` builds no ring for any declared
    HARDMACRO. That disagreement predates this change and already applies to
    15.5ic's own outputs; which side is right is a ruling, not a test."""
    project = H._project(tmp_path, **kw)
    owed = F._output_not_owed(project, _step37(), EVIDENCE) is None
    assert owed is bool(TD.requests_pad_ring(project))


def test_an_entry_without_a_condition_is_always_owed(tmp_path):
    project = H._project(tmp_path)                       # HARDMACRO
    assert F._output_not_owed(project, _step37(),
                              "phase3/stage4/gds/*.gds") is None
