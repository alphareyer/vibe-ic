"""Sign a synthetic project's exact evidence for unrelated flow tests."""

import json
from pathlib import Path

import ai_signed_judgement


def sign(project: Path, step_id: str) -> None:
    digest = ai_signed_judgement.evidence_sha256(project, step_id)
    assert digest, f"fixture has no Step {step_id} evidence to review"
    receipt = project / "reports/audit/ai_judgements" / f"{step_id}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({
        "schema": ai_signed_judgement.SCHEMA,
        "step_id": step_id,
        "evidence_sha256": digest,
        "verdict": "PASS",
        "signed_by": "synthetic-fixture-reviewer",
        "judgement": "Reviewed the synthetic evidence staged by this test fixture.",
    }) + "\n")


def run_phase1_with_expert_answer(project: Path, argv: list[str], run,
                                  *, expected_tokens: list[str],
                                  field_path: str = "top_ports",
                                  layer: str = "L9_INTERFACE",
                                  requirement: str = "the design input's named facts appear in final L9",
                                  **kwargs):
    """Finish a synthetic Phase-1 fixture through its real two-pass handoff.

    The answer and receipt are written only after pass 1 produced the L docs;
    the receipt therefore binds the input, generated docs, and expert answer.
    ``run`` is the calling test's supervised subprocess runner.
    """
    first = run(argv, **kwargs)
    return consume_phase1_expert_answer(
        project, argv, run, expected_tokens=expected_tokens,
        field_path=field_path, layer=layer, requirement=requirement,
        first=first, **kwargs)


def consume_phase1_expert_answer(project: Path, argv: list[str], run,
                                 *, expected_tokens: list[str],
                                 field_path: str = "top_ports",
                                 layer: str = "L9_INTERFACE",
                                 requirement: str = "the design input's named facts appear in final L9",
                                 first=None,
                                 **kwargs):
    """Deliver a fixture answer after an already executed Phase-1 first pass."""
    from _path_layout import report_path
    report = report_path(project, "phase1/expert_parse_track.json")
    assert report.is_file(), ((first.stdout or "") + (first.stderr or "")
                              if first else "first pass did not emit a report")
    assert json.loads(report.read_text())["ai_subtrack"]["status"] == "HANDOFF_EMITTED"
    answer = (report.parent / "expert_parse_track_pack"
              / "l_doc_expectations.json")
    assert answer.parent.is_dir(), "first pass did not emit the expert pack"
    answer.write_text(json.dumps({"expectations": [{
        "id": "fixture-declared-layer-fact",
        "layer": layer,
        "field_path": field_path,
        "requirement": requirement,
        "expected_tokens": expected_tokens,
        "evidence": ["the synthetic design input names these facts"],
    }]}))
    sign(project, "D1")
    return run([*argv, "--second-track-only"], **kwargs)
