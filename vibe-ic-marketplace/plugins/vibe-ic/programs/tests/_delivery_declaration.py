"""Declare a fixture project's delivery through the REAL admission contract.

WHY IT IS SHARED. v1.22.13 (#2376) added `_delivery_admission_refusal` to
`phase3_one_shot_runner`, which stops Phase 3 before any step when the project
carries no tapeout declaration:

    REFUSED: DELIVERY_AUTHORITY_STALE_OR_UNDECLARED:
    input/submission_template/tapeout_declaration.json: cannot read ...;
    refresh step 0.5ic before starting Phase 3

`main()` then returns WITHOUT writing `reports/orchestrator/phase3_one_shot.json`,
so every test that drives `main()` and reads that report dies on a missing file
rather than on anything it asserts. The landing updated ONE of the three fixture
families that drive `main()` this way and left the other two red.

Writing the same five lines a third time is how this tree grows a drift it then
spends a version removing, so the declaration lives here once. The two callers
that were red now use it; the one that already had its own copy delegates to it.

IT SUPPLIES AN INPUT; IT DOES NOT DISABLE A GATE. Admission still runs, and a
project this function was never called on is still refused -- which is that
gate's own suite's subject.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def declare_delivery(project: Path, deliverable: str = "DIE") -> None:
    """Stage an owner-attested route and the matching operator marker."""
    import _owner_declared as OD
    import _submission_template as ST
    import _tapeout_declaration as TD

    answers = {"deliverable": deliverable, "top_cell": "top"}
    declaration, _ = TD.merge_answers(TD.blank_declaration(), answers)
    declaration = OD.attest(declaration)
    assert not TD.validate(declaration)
    raw = OD.attest({
        "schema": "vibe-ic/step_0_5ic_answers/1",
        "answers": answers,
        "operator_template": {"path": None, "slot": None,
                              "absent_reason": "no operator slot"},
    })
    for rel, doc in ((ST.DESIGN_ANSWERS_REL, raw),
                     (TD.DECLARATION_REL, declaration)):
        path = Path(project) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))
    marker_rel, marker = (
        (ST.NO_TEMPLATE_REL, ST.NO_TEMPLATE_MARKER)
        if deliverable == TD.DELIVERABLE_HARDMACRO
        else (TD.SELF_TAPEOUT_REL, TD.SELF_TAPEOUT_MARKER))
    (Path(project) / marker_rel).write_text(marker + "\n")
