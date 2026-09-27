"""llv1 W14 (LibreLane half): a step the flag handed to LibreLane and LibreLane
did not perform is its own reason class, neither red nor PASS; every step the
flag changes names its producer in the phase reports; the default is untouched.

The import is the real W6 importer on the CMP3 8HD-4 fixture (Classic flow, so
`OpenROAD.PadRing` / step 15.5ic is genuinely not performed).
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
_PLUGIN = PROGRAMS.parent
for _p in (str(PROGRAMS), str(_PLUGIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _flow_reason_taxonomy as T  # noqa: E402
import _impl_outcomes as IO  # noqa: E402
import verdict as V  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "librelane_import" \
    / "8HD-4" / "runs" / "cmp3"
RUN_REL = "phase3/librelane/runs/cmp3"


@pytest.fixture
def imported(tmp_path):
    import librelane_import as LI
    proj = tmp_path / "proj"
    (proj / RUN_REL).parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, proj / RUN_REL)
    LI.import_run(proj, proj / RUN_REL)
    return proj


# ── the reason class ──────────────────────────────────────────────────────

def test_the_class_is_in_both_vocabularies_and_is_not_a_skip():
    assert V.ReasonClass.FLOW_DOES_NOT_PERFORM.value == "flow_does_not_perform"
    assert T.FLOW_DOES_NOT_PERFORM in T.REASON_CLASSES
    assert T.FLOW_DOES_NOT_PERFORM not in T.SKIP_ELIGIBLE
    assert T.FLOW_DOES_NOT_PERFORM in T.INCOMPLETE
    assert T.record_verdict(T.FLOW_DOES_NOT_PERFORM) == "INCOMPLETE"
    assert T.p0_tier_for_reason_classes([T.FLOW_DOES_NOT_PERFORM]) == "INCOMPLETE"
    assert T.normalise("flow-does-not-perform") == T.FLOW_DOES_NOT_PERFORM
    # CAPABILITY_ABSENT, the class this replaces for flag gaps, IS a skip
    assert T.CAPABILITY_ABSENT in T.SKIP_ELIGIBLE


def test_a_gate_record_cannot_book_it_as_a_skip():
    import flow_compliance_check as F
    rec = F._p0_gate_record("flag_gap_check", "SKIP",
                            "the librelane flow did not perform this step",
                            reason_class=T.FLOW_DOES_NOT_PERFORM)
    assert rec["verdict"] == "INCOMPLETE"
    assert rec["reason_class"] == T.FLOW_DOES_NOT_PERFORM


def test_a_stated_class_maps_to_the_row_word():
    import flow_compliance_check as F
    assert F._VACUOUS_CLASS_TO_ROW_CLASS[T.FLOW_DOES_NOT_PERFORM] == \
        V.ReasonClass.FLOW_DOES_NOT_PERFORM.value


# ── the verdict it carries, and the cascade ───────────────────────────────

def _np(sid="15.5ic"):
    return V.StepVerdict.not_measured(
        sid, "pad ring", reason_class=V.ReasonClass.FLOW_DOES_NOT_PERFORM,
        reason=f"not performed; remedy: {IO.remedy(sid)}")


def test_it_is_neither_red_nor_pass():
    step = _np()
    assert step.verdict is V.Verdict.NOT_MEASURED
    assert step.verdict not in (V.Verdict.FAIL, V.Verdict.PASS,
                                V.Verdict.PASS_WITH_WAIVERS)
    assert "run the default flow (no --librelane)" in step.reason


def test_a_dependent_of_a_not_performed_step_runs_and_reports_itself():
    """Cascade: NOT_MEASURED voids nothing downstream (the dependent keeps the
    word it earns); a FAIL upstream still voids its dependents."""
    assert V.cascade_to_dependent(_np(), "37", "gds") is None
    failed = V.StepVerdict.fail("21", "route", reason="tool FAIL")
    got = V.cascade_to_dependent(failed, "37", "gds")
    assert got.reason_class is V.ReasonClass.UPSTREAM_FAILED


def test_a_run_with_a_not_performed_step_is_not_pass_and_not_fail():
    rows = [V.StepVerdict.pass_("21", "route"), _np()]
    word = V.run_verdict(rows)
    assert word not in (V.Verdict.PASS, V.Verdict.FAIL), word


# ── the per-step mapping, from the real import ────────────────────────────

def test_each_changed_step_names_its_producer(imported):
    fields = IO.report_fields(imported, IO.IMPL_LIBRELANE)
    assert fields["impl"] == "librelane"
    prod = fields["step_producers"]
    pad = prod["15.5ic"]
    assert pad["state"] == IO.NOT_PERFORMED
    assert pad["verdict"] == "NOT_MEASURED"
    assert pad["reason_class"] == "flow_does_not_perform"
    assert pad["tool_step"] == "OpenROAD.PadRing"
    assert "run the default flow" in pad["remedy"]
    route = prod["21"]
    assert route["state"] == IO.DONE_BY_TOOL
    assert route["tool_steps"] == ["OpenROAD.DetailedRouting"]
    assert route["provenance"] == {"witnessed": route["files"]}
    assert prod["31"]["state"] == IO.MEASURED_BY_VIBEIC
    assert prod["18"]["state"] == IO.VIBEIC and \
        "Vibeic.InsertSpareCells" in prod["18"]["disclosure"]
    assert prod["14"]["state"] == IO.NOT_ATTRIBUTED
    # steps the flag does not change are not listed: they are vibe-ic's
    assert "1" not in prod and "13" not in prod
    for sid, p in prod.items():
        if p["state"] == IO.DONE_BY_TOOL:
            assert sid in IO.LIBRELANE_STEPS


def test_without_an_import_no_step_is_claimed(tmp_path):
    prod = IO.step_producers(IO.IMPL_LIBRELANE, None)
    assert {p["state"] for s, p in prod.items() if s in IO.LIBRELANE_STEPS} \
        == {IO.NOT_ATTRIBUTED}


def test_the_runner_rows_for_not_performed_steps(imported):
    prod = IO.report_fields(imported, IO.IMPL_LIBRELANE)["step_producers"]
    rows = IO.not_performed_verdicts(prod, {"15.5ic": "Pad Ring"})
    assert [(r.step_id, r.name, r.verdict, r.reason_class) for r in rows] == [
        ("15.5ic", "Pad Ring", V.Verdict.NOT_MEASURED,
         V.ReasonClass.FLOW_DOES_NOT_PERFORM)]
    assert "remedy: run the default flow" in rows[0].reason


def test_an_unknown_flow_has_no_mapping():
    with pytest.raises(ValueError):
        IO.step_producers("orfs", None)


# ── the default is untouched ──────────────────────────────────────────────

def test_the_default_adds_nothing(tmp_path, imported):
    assert IO.report_fields(tmp_path) == {}            # no mode record
    assert IO.report_fields(imported, IO.IMPL_DEFAULT) == {}
    assert IO.step_producers(IO.IMPL_DEFAULT, None) == {}
    assert IO.summary_lines({"steps": [], "verdict": "PASS"}) == []


def test_the_mode_is_read_from_the_w0_record(tmp_path):
    """W0 is in the stack: the real mode record, not a stand-in module."""
    import _impl_flow as F
    assert IO.resolve_impl(tmp_path) == "vibe-ic"          # no record
    F.write_record(tmp_path, "librelane", resolved_by="test")
    assert IO.resolve_impl(tmp_path) == "librelane"
    assert IO.report_fields(tmp_path)["impl"] == "librelane"


# ── the reports ───────────────────────────────────────────────────────────

def test_final_summary_names_the_flow_and_the_gap(imported):
    import final_report_generate as G
    orch = imported / "reports" / "orchestrator"
    orch.mkdir(parents=True, exist_ok=True)
    rec = {"steps": [], "verdict": "NOT_MEASURED"}
    (orch / "phase3_one_shot.json").write_text(json.dumps(rec))
    assert G._render_impl_section(imported) == []            # default record
    rec.update(IO.report_fields(imported, IO.IMPL_LIBRELANE))
    (orch / "phase3_one_shot.json").write_text(json.dumps(rec))
    md = G._render_impl_section(imported)
    assert md[0] == "## Implementation flow"
    row = next(l for l in md if l.startswith("| 15.5ic |"))
    assert "NOT_PERFORMED" in row and "flow_does_not_perform" in row
    assert "run the default flow" in row
    assert any(l.startswith("| 21 | librelane | DONE_BY_TOOL |") for l in md)


@pytest.mark.parametrize("runner", ["phase3_one_shot_runner.py",
                                    "design_one_shot_runner.py"])
def test_each_full_phase_report_carries_the_fields(runner):
    """Wiring pin: the full phase-2/phase-3 summary dict is extended by
    `_impl_outcomes.report_fields(project)` right after it is built."""
    tree = ast.parse((PROGRAMS / runner).read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "update"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "summary"
             and n.args and isinstance(n.args[0], ast.Call)
             and isinstance(n.args[0].func, ast.Attribute)
             and n.args[0].func.attr == "report_fields"]
    assert len(calls) == 1


def test_the_whole_card_carries_the_section_only_under_a_flag(imported):
    import final_report_generate as G
    orch = imported / "reports" / "orchestrator"
    orch.mkdir(parents=True, exist_ok=True)
    rec = {"steps": [], "verdict": "NOT_MEASURED"}
    (orch / "phase3_one_shot.json").write_text(json.dumps(rec))
    default_card = G._render(imported, run_audit=False)
    assert "## Implementation flow" not in default_card
    rec.update(IO.report_fields(imported, IO.IMPL_LIBRELANE))
    (orch / "phase3_one_shot.json").write_text(json.dumps(rec))
    card = G._render(imported, run_audit=False)
    assert "## Implementation flow" in card
    # the section is ADDED; every other line of the card is unchanged
    # (italic `_..._` lines carry the render time / snapshot marker)
    extra = [l for l in card.splitlines()
             if l not in default_card.splitlines() and not l.startswith("_")]
    assert extra and all(l.startswith(("## Implementation flow", "- Flow:",
                                       "|")) for l in extra), extra


# ── the import is read through W0: load_manifest + segments_of ────────────

SEG1, SEG2 = "phase3/librelane/seg1/runs/cmp3", "phase3/librelane/seg2/runs/cmp3"


@pytest.fixture
def imported_in_segments(tmp_path):
    """The two-segment plan: segment 1 --to NetlistAssignStatements, segment
    2 from CheckSDCFiles, each flow.log the real one cut the way LibreLane
    writes such a run; ONE import writes one segmented manifest."""
    import librelane_import as LI
    proj = tmp_path / "proj"
    for rel in (SEG1, SEG2):
        (proj / rel).parent.mkdir(parents=True)
        shutil.copytree(FIXTURE, proj / rel)
    lines = (proj / SEG1 / "flow.log").read_text().splitlines(keepends=True)
    cut = next(i for i, l in enumerate(lines) if l.startswith("Running")
               and "'OpenROAD.CheckSDCFiles'" in l)
    end = [l for l in lines if l.startswith(("Saving views", "Flow complete."))]
    (proj / SEG1 / "flow.log").write_text("".join(lines[:cut] + end))
    (proj / SEG2 / "flow.log").write_text("Starting…\n" + "".join(lines[cut:]))
    # the trim kept state_out.json only where a rule imports; LibreLane
    # writes one for every step that returns, the declared end included
    (proj / SEG1 / "09-checker-netlistassignstatements/state_out.json") \
        .write_text("{}\n")
    LI.import_segments(proj, [(proj / SEG1, "Checker.NetlistAssignStatements"),
                              (proj / SEG2, None)])
    return proj


def test_a_segmented_import_is_read_per_segment(imported_in_segments):
    import _external_flow_manifest as M
    doc = M.load_manifest(imported_in_segments)
    assert "rows" not in doc and len(doc["segments"]) == 2
    prod = IO.report_fields(imported_in_segments,
                            IO.IMPL_LIBRELANE)["step_producers"]
    assert prod["9"]["state"] == IO.DONE_BY_TOOL
    assert prod["9"]["segments"] == ["segment-1"]
    assert prod["9"]["tool_steps"] == ["Yosys.Synthesis"]
    assert prod["21"]["state"] == IO.DONE_BY_TOOL
    assert prod["21"]["segments"] == ["segment-2"]
    # every row of a LibreLane-owned step, in either segment, is attributed
    # once (step 31's rows are LibreLane's own DRC/LVS reports; the step is
    # MEASURED_BY_VIBEIC, its verdict the kept vibe-ic decks')
    total = sum(1 for s in doc["segments"] for r in s["rows"]
                if r["step_id"] in IO.LIBRELANE_STEPS)
    assert total and sum(p.get("files", 0) for p in prod.values()) == total
    assert {r["step_id"] for s in doc["segments"] for r in s["rows"]} \
        - IO.LIBRELANE_STEPS == {"31"}
    # not_performed is the union over the segments
    assert [s for s, p in prod.items() if p["state"] == IO.NOT_PERFORMED] \
        == ["15.5ic"]


def test_a_one_run_import_reads_as_before(imported):
    prod = IO.report_fields(imported, IO.IMPL_LIBRELANE)["step_producers"]
    assert prod["21"]["state"] == IO.DONE_BY_TOOL
    assert not any("segments" in p for p in prod.values())


def test_an_import_manifest_that_does_not_validate_claims_nothing(imported):
    """A canonical file edited after the import: W0's load_manifest refuses
    the manifest, so no step is DONE_BY_TOOL and none NOT_PERFORMED."""
    (imported / "phase3/stage3/pnr/routed.def").write_text("edited\n")
    prod = IO.report_fields(imported, IO.IMPL_LIBRELANE)["step_producers"]
    flag = {s: p for s, p in prod.items() if s in IO.LIBRELANE_STEPS}
    assert {p["state"] for p in flag.values()} == {IO.NOT_ATTRIBUTED}
    assert all("does not validate" in p["reason"] and "routed.def" in p["reason"]
               for p in flag.values())
