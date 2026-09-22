"""A design that declares it has no package has ANSWERED the packaging question.

`die_finishing_gen.die_id_state` documents its own table:

    packaging = CoB, cells all present once   -> PRESENT
    packaging = CoB, any missing or duplicated-> ABSENT   (a real FAIL)
    packaging = CoB, no cell list declared    -> NOT_DETERMINED
    packaging declared and not CoB            -> NOT_APPLICABLE
    packaging not declared                    -> NOT_DETERMINED

and it read the packaging choice from ONE place, the PDK-bridge config. MEASURED on
spm run20, the design answers it elsewhere and positively:
`phase1/generated_docs/L1_DATASHEET.json` carries `package_info: null` AND
`no_package_in_input: true`, and the step-0.5ic / operator answers carry a typed
`absent_reason` -- "nothing in L1-L9 names an operator, a slot, a submission
template or a package" -- with an absence-of-evidence citation. That is an ANSWER.
Reading it as silence left the half NOT_DETERMINED, which tiered the whole
`die_finishing` document INCOMPLETE while its own verdict said PASS and its own
reason said the die-ID half "does not gate the seal ring" -- and that INCOMPLETE
classified step 26.5ic as `partial_population`.

Nothing is retiered here. `die_finishing_check` ALREADY routes NOT_APPLICABLE to
`tier: SUBSTANTIVE_PASS` -- "DECIDED, not silent ... which is exactly what
separates this from NOT_DETERMINED" -- so this only feeds that branch the state the
design had already declared. A declared absence is not a missing declaration; that
distinction is what #2118 cost.
"""
from __future__ import annotations

import json
import pytest
import die_finishing_gen as G

L1 = "phase1/generated_docs/L1_DATASHEET.json"


def project(tmp_path, **fields):
    p = tmp_path / "run"
    (p / "phase1/generated_docs").mkdir(parents=True)
    if fields is not None:
        (p / L1).write_text(json.dumps({"doc_name": "L1_DATASHEET",
                                        "fields": dict(fields)}))
    return p


def test_a_declared_absent_package_settles_the_half_as_not_applicable(tmp_path):
    p = project(tmp_path, no_package_in_input=True, package_info=None)
    got = G.die_id_state({}, None, project=p)
    assert got["state"] == "NOT_APPLICABLE"
    assert L1 in got["packaging_basis"]
    assert "does not gate the seal ring" in got["reason"]


def test_the_citation_names_the_document_that_answered(tmp_path):
    p = project(tmp_path, no_package_in_input=True, package_info=None)
    cite = G.design_declares_no_package(p)
    assert cite and L1 in cite and "no_package_in_input=true" in cite


@pytest.mark.parametrize("fields", [
    {},                                                    # the keys absent
    {"no_package_in_input": False, "package_info": None},   # explicitly not declared
    {"no_package_in_input": True, "package_info": {"kind": "qfn"}},  # contradicts itself
    {"no_package_in_input": "yes", "package_info": None},   # not the boolean true
])
def test_only_a_POSITIVE_declaration_counts(tmp_path, fields):
    """THE NEGATIVE ARM. Anything short of "no package, and no package_info" leaves
    the half NOT_DETERMINED, so this can never turn silence into an answer."""
    p = project(tmp_path, **fields)
    assert G.design_declares_no_package(p) is None
    assert G.die_id_state({}, None, project=p)["state"] == "NOT_DETERMINED"


def test_an_absent_or_unreadable_L1_leaves_the_half_undetermined(tmp_path):
    bare = tmp_path / "bare"
    (bare / "phase1/generated_docs").mkdir(parents=True)
    assert G.design_declares_no_package(bare) is None
    (bare / L1).write_text("{ not json")
    assert G.design_declares_no_package(bare) is None
    assert G.die_id_state({}, None, project=bare)["state"] == "NOT_DETERMINED"


def test_no_project_at_all_is_still_undetermined(tmp_path):
    """Fail closed: the old call shape keeps the old answer."""
    assert G.design_declares_no_package(None) is None
    assert G.die_id_state({}, None)["state"] == "NOT_DETERMINED"


def test_the_CONFIG_still_wins_when_it_declares_a_packaging(tmp_path):
    """The design's answer is consulted only where the config gave none -- a
    config that says chip-on-board is not overridden by an L1 that says no
    package, because that disagreement is not this function's to resolve."""
    p = project(tmp_path, no_package_in_input=True, package_info=None)
    got = G.die_id_state({"die_id": {"packaging": "cob"}}, None, project=p)
    assert got["state"] != "NOT_APPLICABLE"
    assert got["packaging"] == "cob"


def test_a_declared_non_cob_packaging_was_already_not_applicable(tmp_path):
    """Pinned AS FOUND: the table's fourth row predates this change and must keep
    working, so the new path is an ADDITION and not a replacement."""
    got = G.die_id_state({"die_id": {"packaging": "bare-die"}}, None)
    assert got["state"] == "NOT_APPLICABLE"
