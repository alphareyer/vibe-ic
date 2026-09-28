"""R-0915-38: phase 1 EXTRACTS L24 from the input's own sign-off statements.

MEASURED 2026-09-15 (lane icspm3) on `spm` x gf180mcuD. R-0915-36 left L24
APPLICABLE because the design's input genuinely carries its subject -- 24
word-bounded hits across five of nine documents -- and the layer was still a
skeleton, so `l24_signoff_evidence_backed_check` kept blocking and P0 stopped
at 1. The layer was the gap, not the gate.

Phase 1 now reads those same documents and fills L24 with what they STATE::

    DRC      clean          input/docs/L1_product_metadata.md:46
    LVS      clean          input/docs/L1_product_metadata.md:46
    antenna  percent_clean  input/docs/L7_verification_plan.md:82
    STA      met            input/docs/L1_product_metadata.md:46
    IR_drop / EM / tapeout_precheck  -- searched, not stated

REQUIREMENTS, NEVER STATUSES. L24's `*_status` fields are phase-3 OUTCOMES,
and this gate treats any `*_status` / `status` / `verdict` / `result` key at
any depth as a CLAIM needing an in-project evidence path. Phase 1 runs before
DRC exists, so filling them would publish a certificate for a check that has
not run -- turning the layer from inert to FAILING. The requirement rows
therefore carry none of those key names, and the outcome fields stay null.

THE CLAUSE RULE IS THE LOAD-BEARING PART, and it is a bug this file caught: an
input line reading "DRC clean, LVS clean, STA met" states THREE requirements,
and the first implementation scanned the whole line and handed the first match
to all three -- STA came back `clean`, which the input never says. The clause
runs from a check's own token to the next check's token.

chip-AGNOSTIC: synthetic projects in tmp_path; no design, PDK or vendor name.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import l24_signoff_requirements_extract as X  # noqa: E402
import phase1_post_process as P  # noqa: E402

GATE = PROGRAMS / "l24_signoff_evidence_backed_check.py"


def _project(tmp_path, **docs):
    d = tmp_path / "input" / "docs"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        stem, _, suffix = name.rpartition("_")
        (d / f"{stem}.{suffix}").write_text(text, encoding="utf-8")
    return tmp_path


def _rows(project):
    out = X.extract_signoff_requirements(project)
    return {r["check"]: r for r in out["signoff_requirements"]}


def _emit_l24(project):
    doc = P.emit_l_doc_skeleton("L24", "unknown", project_dir=project)
    doc.pop("evidence", None)
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L24_SIGNOFF.json").write_text(json.dumps(doc, indent=1))
    return doc


def _report(project, rel, payload):
    p = project / "reports" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))
    return p


def _run_gate(project):
    r = subprocess.run([sys.executable, str(GATE), str(project)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ── the extractor reads what the input states ────────────────────────────

def test_a_stated_requirement_is_extracted_with_its_citation(tmp_path):
    proj = _project(tmp_path, spec_md=(
        "# Plan\n\nThe part must sign off with DRC clean on the target PDK.\n"))
    row = _rows(proj)["DRC"]
    assert row["stated"] is True
    assert row["requirement"] == "clean"
    assert row["citation"]["document"] == "input/docs/spec.md"
    assert row["citation"]["line"] == 3
    assert "DRC" in row["citation"]["text"]


def test_one_line_stating_three_checks_gives_three_requirements(tmp_path):
    """THE REGRESSION CONTROL. Scanning the whole line made STA `clean`."""
    proj = _project(tmp_path, spec_md=(
        "Must sign off: DRC clean, LVS clean, STA met.\n"))
    rows = _rows(proj)
    assert rows["DRC"]["requirement"] == "clean"
    assert rows["LVS"]["requirement"] == "clean"
    assert rows["STA"]["requirement"] == "met", (
        "the clause must stop at the next check's token")


def test_a_check_the_input_never_mentions_is_searched_not_invented(tmp_path):
    proj = _project(tmp_path, spec_md="DRC clean is required.\n")
    row = _rows(proj)["EM"]
    assert row["stated"] is False
    assert row["requirement"] is None
    assert row["citation"] is None
    assert row["searched"]["documents"] == ["input/docs/spec.md"]
    assert row["searched"]["spellings"], "must say what it looked for"


def test_a_check_named_without_a_requirement_word_keeps_it_null(tmp_path):
    """The extractor does not guess what "DRC" alone was supposed to demand."""
    proj = _project(tmp_path, spec_md="Section 4 discusses DRC.\n")
    row = _rows(proj)["DRC"]
    assert row["stated"] is True
    assert row["requirement"] is None
    assert row["citation"]["line"] == 1


def test_a_corner_named_inside_the_clause_is_attributed(tmp_path):
    proj = _project(tmp_path, spec_md="STA met at SS and FF corners.\n")
    assert _rows(proj)["STA"]["corners"] == ["FF", "SS"]


def test_a_threshold_stated_in_the_clause_is_captured(tmp_path):
    proj = _project(tmp_path, spec_md="IR drop must stay under 50 mV.\n")
    row = _rows(proj)["IR_drop"]
    assert row["stated"] is True
    assert row["threshold"] == {"value": 50.0, "unit": "mV"}


@pytest.mark.parametrize("decoy", [
    "The state machine holds status bits.\n",
    "Installation notes follow.\n",
    "A constant is stated in the table.\n",
])
def test_the_batch4_decoys_stay_decoys(tmp_path, decoy):
    """Word-bounded throughout: `STA` must not fire inside state/stated/
    installation here either."""
    proj = _project(tmp_path, spec_md=decoy)
    assert _rows(proj)["STA"]["stated"] is False, decoy


# ── the extractor refuses rather than guessing ───────────────────────────

def test_no_project_extracts_nothing():
    assert X.extract_signoff_requirements(None) is None


def test_no_input_document_extracts_nothing(tmp_path):
    (tmp_path / "input" / "docs").mkdir(parents=True)
    assert X.extract_signoff_requirements(tmp_path) is None


def test_a_binary_only_corpus_extracts_nothing(tmp_path):
    d = tmp_path / "input" / "docs"
    d.mkdir(parents=True)
    (d / "layout.gds").write_bytes(b"\x00\x01")
    assert X.extract_signoff_requirements(tmp_path) is None


# ── the emitted layer ────────────────────────────────────────────────────

def test_the_layer_is_EXTRACTED_and_asserts_no_status(tmp_path):
    proj = _project(tmp_path, spec_md="Sign-off: DRC clean, LVS clean.\n")
    doc = _emit_l24(proj)
    assert doc["applicability"] == "APPLICABLE"
    assert doc["extraction_status"] == "EXTRACTED"
    f = doc["fields"]
    for key in ("drc_status", "lvs_status", "sta_status", "ir_drop_status",
                "antenna_status"):
        assert f[key] is None, f"{key} must stay a phase-3 outcome"
    assert f["tapeout_gates"] == []
    assert [r["check"] for r in f["signoff_requirements"] if r["stated"]] == [
        "DRC", "LVS"]


def test_no_requirement_row_carries_a_claim_key(tmp_path):
    """A `status` / `verdict` / `result` key anywhere in what phase 1 writes
    would be read by this gate as an unevidenced certificate."""
    proj = _project(tmp_path, spec_md="DRC clean, LVS clean, STA met.\n")
    doc = _emit_l24(proj)
    banned = {"status", "verdict", "result"}

    def walk(node, path="fields"):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in banned and not k.endswith("_status") or (
                    v is None), f"{path}.{k} = {v!r} would read as a claim"
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(doc["fields"])


def test_an_input_with_no_signoff_statement_is_still_declared_absent(tmp_path):
    """R-0915-36 is unchanged underneath: extraction only happens where the
    subject is there to extract."""
    proj = _project(tmp_path, spec_md="It multiplies two numbers.\n")
    doc = _emit_l24(proj)
    assert doc["applicability"] == "NOT_APPLICABLE"
    assert doc["extraction_status"] == "DECLARED_ABSENT_FROM_INPUT"
    assert "signoff_requirements" not in doc["fields"]


def test_the_declared_absent_stub_carries_a_rationale(tmp_path):
    """The gate FAILS an N/A layer with no `rationale` -- "a silent-empty
    layer is indistinguishable from a failed extraction". R-0915-36's stub
    had none; the roster answered the gate N/A before it ever ran, so the
    hole was invisible."""
    proj = _project(tmp_path, spec_md="It multiplies two numbers.\n")
    doc = _emit_l24(proj)
    assert doc["applicability"] == "NOT_APPLICABLE"
    assert doc["rationale"].strip(), "the stub must say WHY"
    rc, out = _run_gate(proj)
    assert rc == 2, out          # SKIP, an honest N/A

    # AND THE CONTROL: strip the rationale back out and the same layer FAILS,
    # which is what R-0915-36 shipped and the roster hid.
    path = proj / "phase1" / "generated_docs" / "L24_SIGNOFF.json"
    doc.pop("rationale")
    path.write_text(json.dumps(doc, indent=1))
    rc_bad, out_bad = _run_gate(proj)
    assert rc_bad == 1, out_bad
    assert "no `rationale`" in out_bad


# ── the gate judges the run against the requirements ─────────────────────

def _phase3_ran(proj):
    """Mark the run as having reached the phase that MEASURES sign-off."""
    _report(proj, "orchestrator/phase3_one_shot.json", {"verdict": "PASS"})
    return proj


def _proj_requiring_drc(tmp_path):
    proj = _project(tmp_path, spec_md="Sign-off requires DRC clean.\n")
    _emit_l24(proj)
    return _phase3_ran(proj)


def test_a_requirement_the_run_measures_is_backed(tmp_path):
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "phase3/drc_signoff.json", {"program": "drc", "passed": True})
    rc, out = _run_gate(proj)
    assert rc == 0, out
    assert "DRC clean required at input/docs/spec.md:1" in out
    assert "reports/phase3/drc_signoff.json (pass)" in out


def test_a_requirement_no_report_measures_FAILS_naming_it(tmp_path):
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "phase3/lvs_verdict.json", {"status": "PASS"})
    rc, out = _run_gate(proj)
    assert rc == 1, out
    assert "the input REQUIRES DRC clean" in out
    assert "input/docs/spec.md:1" in out


def test_a_requirement_the_run_measures_as_FAILING_FAILS_naming_it(tmp_path):
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "phase3/drc_signoff.json", {"program": "drc",
                                              "passed": False})
    rc, out = _run_gate(proj)
    assert rc == 1, out
    assert "the input REQUIRES DRC clean" in out
    assert "'fail'" in out and "drc_signoff.json" in out


def test_a_vacuous_pass_does_not_back_a_requirement(tmp_path):
    """An empty denominator is not a measurement, however green it looks."""
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "phase3/drc_signoff.json", {"program": "drc",
                                              "verdict": "VACUOUS_PASS"})
    rc, out = _run_gate(proj)
    assert rc == 1, out
    assert "no report measuring it" in out


def test_the_audit_cannot_back_a_requirement_it_is_judging(tmp_path):
    """A report under reports/audit/ is the audit's own bookkeeping; using it
    would let the judge supply its own evidence."""
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "audit/drc_signoff.json", {"program": "drc", "passed": True})
    rc, out = _run_gate(proj)
    assert rc == 1, out


@pytest.mark.parametrize("payload,spelling", [
    ({"program": "drc", "passed": True}, "passed"),
    ({"program": "drc", "status": "PASS"}, "status"),
    ({"program": "drc", "result": "PASS"}, "result"),
    ({"program": "drc", "verdict": "MEASURED"}, "verdict"),
])
def test_every_verdict_spelling_a_real_run_uses_is_read(tmp_path, payload,
                                                        spelling):
    """MEASURED across a real run tree: drc_signoff.json carries `passed`,
    lvs_verdict.json carries `status` AND `result`, em.json carries
    `verdict`. A reader knowing one spelling calls the others unmeasured."""
    proj = _proj_requiring_drc(tmp_path)
    _report(proj, "phase3/drc_signoff.json", payload)
    rc, out = _run_gate(proj)
    assert rc == 0, (spelling, out)


# ── the deadlock, and the control that holds it shut ─────────────────────

def test_a_requirement_is_not_unmet_before_the_phase_that_measures_it(
        tmp_path):
    """MEASURED: the phase-2 strict-structural audit invokes this gate, so a
    requirements arm that failed on "no DRC report" failed at a point in the
    flow where DRC CANNOT have run. Run 27 halted in phase 2 on exactly that,
    so phase 3 never executed and the evidence the gate demanded could never
    appear -- the gate made its own premise unsatisfiable.

    A requirement is UNMET only once the run completed the phase that would
    have measured it. Before that it is recorded, named, and not blocking."""
    proj = _project(tmp_path, spec_md="Sign-off requires DRC clean.\n")
    _emit_l24(proj)
    assert not (proj / "reports" / "phase3").exists()
    rc, out = _run_gate(proj)
    assert rc == 0, out
    assert "not yet measurable" in out
    assert "REQUIRES" not in out


def test_the_same_run_once_phase3_HAS_run_FAILS(tmp_path):
    """THE CONTROL. The arm is not toothless: declare that phase 3 ran, with
    no DRC report in it, and the same project FAILS naming the requirement."""
    proj = _project(tmp_path, spec_md="Sign-off requires DRC clean.\n")
    _emit_l24(proj)
    _phase3_ran(proj)
    rc, out = _run_gate(proj)
    assert rc == 1, out
    assert "the input REQUIRES DRC clean" in out


def test_a_phase3_producer_report_without_a_summary_still_counts(tmp_path):
    """A netlist-bound LVS producer result is phase-3 evidence even if the
    orchestrator aborted before publishing its summary."""
    proj = _project(tmp_path, spec_md="Sign-off requires DRC clean.\n")
    _emit_l24(proj)
    netlist = proj / "phase2/stage2/synth/netlist_yosys.v"
    netlist.parent.mkdir(parents=True, exist_ok=True)
    netlist.write_text("module chip; endmodule\n")
    import hashlib
    _report(proj, "phase3/lvs_verdict.json", {
        "status": "PASS",
        "generated_by": "phase3_one_shot_runner:_run_extraction_lvs (#477)",
        "phase2_synth": {"path": "phase2/stage2/synth/netlist_yosys.v",
                         "sha256": hashlib.sha256(netlist.read_bytes()).hexdigest()},
    })
    rc, out = _run_gate(proj)
    assert rc == 1, out
    assert "the input REQUIRES DRC clean" in out

    netlist.write_text("module chip; wire new_build; endmodule\n")
    rc, out = _run_gate(proj)
    assert rc == 0, out
    assert "not yet measurable" in out
