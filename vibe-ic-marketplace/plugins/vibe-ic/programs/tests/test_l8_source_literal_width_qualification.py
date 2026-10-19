"""Public source declarations, normal L8 producer and unchanged strict consumer.

The document is the issue's public snippet, not the unavailable field corpus.
The review receipt below is a validator fixture, not an actual expert review.
"""
import json
from pathlib import Path

import pytest

import ai_signed_judgement as judgement
import flow_compliance_check as compliance
import phase1_doc_one_shot_runner as runner


PUBLIC = (Path(__file__).parent / "fixtures/phase1_local/"
          "source_literal_widths/design.md").read_text().rstrip("\n")


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _emit(root, source=PUBLIC, *, extracted=None):
    if extracted is None:
        _write(root, "input/docs/design.md", source)
        extracted = {"design.md": source}
    result = runner.gen_l8_timing_waveform(root, extracted)
    data = json.loads(result.path.read_text())
    rows = {r["name"]: r for r in data["timing_parameters"]
            if "width_bits" in r}
    step = compliance.StepResult(id="D1", name="public L8 evidence",
                                 stage="phase1", status="PASS",
                                 evidence=[str(result.path.relative_to(root))])
    compliance._evidence_integrity_scan(root, step)
    return result, rows, step


def test_public_literal_digest_is_qualified_without_changing_its_evidence(tmp_path):
    _, rows, step = _emit(tmp_path)
    assert set(rows) == {"digest"}  # Bare address is not emitted by this owner.
    row = rows["digest"]
    assert row["width_bits"] == 256
    assert row["low_confidence"] is False
    assert row["source"] == "input/docs/design.md"
    assert row["literal"] == "256"
    assert row["evidence"]["source"] == row["source"]
    assert row["evidence"]["line"] == 1
    assert row["evidence"]["extraction_strategy"] == "width_role_bigram_match"
    assert "The output is a 256-bit digest." in row["evidence"]["quote"]
    assert step.status == "PASS", step.reasons


def test_existing_address_candidate_can_be_qualified_by_its_own_declaration(tmp_path):
    # The existing nearby parameter anchor enables the old address emitter.
    # It is not itself evidence that the register address has a given width.
    source = "parameter INDEX_WIDTH = 8;\n\n" + PUBLIC
    _, rows, step = _emit(tmp_path, source)
    assert set(rows) == {"digest", "address"}
    assert rows["address"]["width_bits"] == 8
    assert rows["address"]["low_confidence"] is False
    assert rows["digest"]["low_confidence"] is False
    assert rows["address"]["evidence"]["line"] == 3
    assert rows["address"]["evidence"]["extraction_strategy"] == "width_role_bigram_match"
    assert "register interface uses an 8-bit address space" in rows["address"]["evidence"]["quote"]
    assert step.status == "PASS", step.reasons


def test_ordinary_role_mentions_do_not_create_a_second_width(tmp_path):
    _, rows, step = _emit(tmp_path, PUBLIC + "\n\nRead the digest after completion.")
    assert rows["digest"]["width_bits"] == 256
    assert rows["digest"]["low_confidence"] is False
    assert step.status == "PASS", step.reasons


@pytest.mark.parametrize("case", [
    "missing", "source_drift", "later_conflict", "omitted_conflict",
    "ambiguous", "negated", "example_prefix", "example_heading", "fenced",
    "nested_example_heading", "long_role_first_conflict", "variable_length",
    "wrapped_conflict",
])
def test_unqualified_source_keeps_the_original_strict_refusal(tmp_path, case):
    source = PUBLIC
    extracted = None
    if case == "missing":
        extracted = {"design.md": source}
    elif case == "source_drift":
        _write(tmp_path, "input/docs/design.md", source.replace("256", "512"))
        extracted = {"design.md": source}
    elif case == "later_conflict":
        source += " The output is a 512-bit digest."
    elif case == "omitted_conflict":
        _write(tmp_path, "input/docs/design.md", source)
        # A role-first declaration must not be hidden by the extractor mapping.
        _write(tmp_path, "input/docs/other.md", "The digest width is 512 bits.")
        extracted = {"design.md": source}
    elif case == "ambiguous":
        source = "The output is a 256-bit digest or a 512-bit digest."
    elif case == "negated":
        source = "The output is not a 256-bit digest."
    elif case == "example_prefix":
        source = "Example. " + source
    elif case == "example_heading":
        source = "# Example\n\n" + source
    elif case == "fenced":
        source = "```text\n" + source + "\n```"
    elif case == "nested_example_heading":
        source = "## Examples\n\n### Output\n\n" + source
    elif case == "long_role_first_conflict":
        source += "\n\nThe digest " + "under the documented operating conditions " * 3 + "has a width of 512 bits."
    elif case == "variable_length":
        source += " The digest has variable length."
    elif case == "wrapped_conflict":
        source += "\n\nThe digest\nmay be 512 bits."
    _, rows, step = _emit(tmp_path, source, extracted=extracted)
    assert rows["digest"]["width_bits"] == 256
    assert rows["digest"]["low_confidence"] is True
    assert rows["digest"]["evidence"]["extraction_strategy"] == "width_role_bigram_match"
    assert step.status == "PASS_WITH_WAIVERS", step.reasons
    assert any("review_required" in reason for reason in step.reasons)


def test_unrelated_low_confidence_row_still_blocks_the_same_artifact(tmp_path):
    _, rows, step = _emit(tmp_path, PUBLIC + "\n\nA 64-bit timestamp is recorded.")
    assert rows["digest"]["low_confidence"] is False
    assert rows["timestamp"]["low_confidence"] is True
    assert step.status == "PASS_WITH_WAIVERS", step.reasons


def test_a_previous_d1_review_cannot_authorize_changed_l8_output(tmp_path):
    result, _, _ = _emit(tmp_path)
    old_digest = judgement.evidence_sha256(tmp_path, "D1")
    _write(tmp_path, "reports/audit/ai_judgements/D1.json", json.dumps({
        "schema": judgement.SCHEMA, "step_id": "D1",
        "evidence_sha256": old_digest, "verdict": "PASS",
        "signed_by": "test-only-validator-fixture",
        "judgement": "Synthetic receipt for freshness rejection, not a real review.",
    }))
    assert judgement.check(tmp_path, "D1")[0] is True
    data = json.loads(result.path.read_text())
    data["timing_parameters"][0]["low_confidence"] = not data["timing_parameters"][0]["low_confidence"]
    result.path.write_text(json.dumps(data))
    assert judgement.evidence_sha256(tmp_path, "D1") != old_digest
    valid, reason = judgement.check(tmp_path, "D1")
    assert valid is False
    assert "awaiting_signed_judgement" in reason
