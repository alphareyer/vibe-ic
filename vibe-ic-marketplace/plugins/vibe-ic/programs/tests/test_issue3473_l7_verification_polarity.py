"""Regression proof for issue #3473 L7 verification-plan polarity."""

import json

import pytest


def test_l7_suite_references_preserve_positive_and_reject_denied(tmp_path):
    from programs import phase1_doc_one_shot_runner as producer
    from programs.tests._hostpaths import require_repo

    fixture = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "phase1_local", "l7_verification_polarity",
        "verification_plan.md",
    )
    text = fixture.read_text()
    result = producer.gen_l7_test_debug(
        tmp_path, {"verification_plan.md": text})
    emitted = json.loads(result.path.read_text())

    actual = [
        {key: row.get(key) for key in
         ("phase", "method", "evidence", "extraction_strategy")}
        for row in emitted["verification_strategy"]
    ]
    assert actual == [{
        "phase": "cocotb",
        "method": (
            "The verification plan must use cocotb for module-level "
            "regression."
        ),
        "evidence": "input/docs/verification_plan.md",
        "extraction_strategy": "test_suite_reference",
    }]
    assert emitted["no_verification_strategy_in_input"] is False


@pytest.mark.parametrize("text", [
    "The verification plan must use verilator. Cocotb is not required.",
    "Cocotb is not required. The verification plan must use verilator.",
])
def test_l7_positive_sentence_survives_neighbor_exclusion(tmp_path, text):
    from programs import phase1_doc_one_shot_runner as producer

    result = producer.gen_l7_test_debug(tmp_path, {"verification_plan.md": text})
    emitted = json.loads(result.path.read_text())
    assert [row["phase"] for row in emitted["verification_strategy"]] == ["verilator"]
    assert emitted["no_verification_strategy_in_input"] is False
