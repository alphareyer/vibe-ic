"""A phase-2 ``reports/lec_not_run.json`` does not outlive the bound proof that
supersedes it, and no reader takes it over that proof.

P2LECIN follow-up (landed v1.26.28). On a from-documents DIE run phase 2 reaches
Step 13 before phase 3's synthesis half has written the netlist step 15 routes,
so step 13 records ``NOT_MEASURED`` (``input_absent``) and writes
``reports/lec_not_run.json`` (``verdict: SKIPPED-CONDITION``). Phase 3 then proves
that netlist and ``reports/lec.json`` binds it (``proof_subject_binding`` MATCH,
and ``lec_equivalence_check`` passes it). The not-run record stayed on disk:

* nothing reads it by name, but the directory-level self-skip scans read every
  ``*.json`` in ``reports/`` -- ``flow_compliance_check._sibling_self_skip_for_
  missing`` (any ``files_exist`` miss whose parent is ``reports/``) and
  ``flow_dashboard_data._disclosed_skip`` (step 13's own outputs, and step 18's
  ``reports/spare_cell_coverage.json``) -- and would publish the phase-2 "not
  measured" disclosure as the LEC state beside a proof that settled it.

The binding step now retires the record (``lec_equivalence_check.
retire_superseded_not_run``: step 13's PASS row in ``step_lec_equivalence``, and
phase 3's ``run_step13_lec_on_pnr_input`` when the proof on disk is already
current), and both scans skip a not-run record a bound proof supersedes
(``lec_not_run_superseded``). "Bound proof" is the gate's own answer --
``audit(...).passed`` AND binding MATCH -- so a stale proof (another sha), a
non-equivalent result, or no proof at all supersedes nothing, and a real
not-run still reports NOT_MEASURED.

Only lec_run's process is faked (it runs yosys in a container); the fake writes
the report lec_run writes, bound to the file and top it was handed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R          # noqa: E402
import flow_compliance_check as FCC         # noqa: E402
import flow_dashboard_data as D             # noqa: E402
import lec_equivalence_check as G           # noqa: E402
import lec_gate_netlist_select as S         # noqa: E402
import phase3_one_shot_runner as P3         # noqa: E402

PLACEHOLDER = "chip_top"        # the runner's --top-name default
CORE = "core"                   # the only module the design's RTL declares
SYNTH = "phase2/stage2/synth"
ROUTED = f"{SYNTH}/{CORE}_synth.v"
NOT_RUN = "reports/lec_not_run.json"
# Step 13's declared outputs (flow step 13 `required_outputs`), and a file
# another step declares in the same directory (step 18).
STEP13_OUTPUTS = ["reports/lec.rpt", "reports/lec.json"]
OTHER_REPORTS_OUTPUT = "reports/spare_cell_coverage.json"

_CORE_RTL = ("module core(input clk, input a, input b, output reg y);\n"
             "  always @(posedge clk) y <= ~(a & b);\nendmodule\n")
_CORE_MAPPED = ("module core(clk, a, b, y);\n  input clk; input a; input b; "
                "output y;\n  wire n;\n  lib__nand2_1 g1 (.A(a), .B(b), "
                ".ZN(n));\n  lib__dffq_1 r (.CLK(clk), .D(n), .Q(y));\n"
                "endmodule\n")
_PAD_WRAPPER = ("module chip_top(input clk, input a, input b, output y);\n"
                "  core u_core(.clk(clk), .a(a), .b(b), .y(y));\nendmodule\n")


def _sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _tree(tmp_path: Path, *, mapped: bool = True) -> Path:
    p = tmp_path / "proj"
    rtl = p / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "core.v").write_text(_CORE_RTL)
    (p / SYNTH).mkdir(parents=True)
    (p / f"{SYNTH}/netlist.v").write_text("module core(); endmodule\n")
    if mapped:
        (p / ROUTED).write_text(_CORE_MAPPED)
    pnr = p / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "chip_top_io.v").write_text(_PAD_WRAPPER)
    return p


@pytest.fixture()
def lec_runs(monkeypatch):
    seen: list = []

    def _run(argv, *a, **k):
        argv = [str(x) for x in argv]
        if len(argv) > 1 and argv[1].endswith("lec_run.py"):
            seen.append(argv)
            proj = Path(argv[2])
            gate = argv[argv.index("--gate-netlist") + 1]
            top = argv[argv.index("--top") + 1]
            doc = {"verdict": "PASS", "equivalent": True,
                   "compared_points": 1, "non_equivalent_points": 0,
                   "unproven_points": 0, "gold": f"{top} (RTL)",
                   "gate": f"{Path(gate).name} (synth)",
                   "proof_identity": {"top": top, "gate_netlist": {
                       "path": gate, "sha256": _sha(proj / gate)}}}
            (proj / "reports").mkdir(exist_ok=True)
            (proj / "reports/lec.json").write_text(json.dumps(doc))
            (proj / "reports/lec.rpt").write_text(
                "Equivalence successfully proven!\nProved 1 $equiv cells.\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(R.subprocess, "run", _run)
    return seen


def _phase2_chain(proj, requested=PLACEHOLDER):
    """Steps 11-13 exactly as phase 2 main dispatches them."""
    return R.step_dft_lec_chain(proj, requested, "", "digital_cmd_driven",
                                full_chip=False, phase2_request=True)


def _phase2_not_run_record(tmp_path) -> dict:
    """The record phase 2 really writes before the routed netlist exists."""
    donor = _tree(tmp_path / "donor", mapped=False)
    _phase2_chain(donor)
    return json.loads((donor / NOT_RUN).read_text())


def _plant(proj: Path, record: dict) -> None:
    """What a run before this fix left beside its proof."""
    (proj / NOT_RUN).write_text(json.dumps(record, indent=2))


def _dashboard_step13(proj: Path):
    """The dashboard's lightweight status for step 13 with its primary output
    (lec.rpt) absent and lec.json present -- the case the disclosed-skip scan
    is consulted for."""
    n_present = sum((proj / o).is_file() for o in STEP13_OUTPUTS)
    return D._lightweight_status(
        proj, {"required_outputs": STEP13_OUTPUTS}, n_present,
        len(STEP13_OUTPUTS), primary_present=(proj / STEP13_OUTPUTS[0]).is_file(),
        live=False)


# --------------------------------------------------------------------------- #
# the binding step retires the record
# --------------------------------------------------------------------------- #
def test_phase3_proof_retires_the_phase2_not_run_record(tmp_path, lec_runs):
    proj = _tree(tmp_path, mapped=False)
    rows = _phase2_chain(proj)
    assert rows[-1].name == "lec_equivalence"
    assert rows[-1].status == "NOT_MEASURED"
    assert rows[-1].reason_class == R._V.ReasonClass.INPUT_ABSENT
    assert (proj / NOT_RUN).is_file()
    # phase 3's synthesis half writes the netlist step 15 routes ...
    (proj / ROUTED).write_text(_CORE_MAPPED)
    # ... and step 13 proves it, bound to that file
    rows = P3.run_step13_lec_on_pnr_input(proj, CORE, "")
    (row,) = [r for r in rows if r.name == "step13_lec_equivalence"]
    assert row.status == "PASS", row.detail
    assert not (proj / NOT_RUN).exists(), (
        "the phase-2 not-run record outlived the bound proof that "
        "supersedes it")
    assert G.bound_proof(proj)["consumer_path"] == ROUTED
    # the retirement is said on the row that did it, never done silently
    assert f"retired {NOT_RUN}" in row.detail, row.detail


def test_a_current_proof_retires_a_stale_record_left_beside_it(tmp_path,
                                                               lec_runs):
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    _plant(proj, _phase2_not_run_record(tmp_path))
    # the proof on disk is current: nothing is re-proved ...
    assert P3.run_step13_lec_on_pnr_input(proj, CORE, "") == []
    assert len(lec_runs) == 1
    # ... and the record it supersedes is gone
    assert not (proj / NOT_RUN).exists()


# --------------------------------------------------------------------------- #
# the readers prefer the bound proof
# --------------------------------------------------------------------------- #
def test_the_sibling_self_skip_scan_prefers_the_bound_proof(tmp_path,
                                                            lec_runs):
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    _plant(proj, _phase2_not_run_record(tmp_path))
    assert FCC._sibling_self_skip_for_missing(
        proj, [OTHER_REPORTS_OUTPUT]) is None, (
        "a stale LEC not-run record was published beside a bound proof")
    assert G.bound_proof(proj) is not None


def test_the_dashboard_skip_scan_prefers_the_bound_proof(tmp_path, lec_runs):
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    _plant(proj, _phase2_not_run_record(tmp_path))
    (proj / "reports/lec.rpt").unlink()
    assert D._disclosed_skip(proj, STEP13_OUTPUTS) == (False, ""), (
        "a stale LEC not-run record was published beside a bound proof")
    assert _dashboard_step13(proj)[0] == "partial"
    assert G.bound_proof(proj) is not None


# --------------------------------------------------------------------------- #
# nothing else supersedes a not-run record
# --------------------------------------------------------------------------- #
def test_a_real_not_run_still_reports_not_measured(tmp_path, lec_runs):
    proj = _tree(tmp_path, mapped=False)
    rows = _phase2_chain(proj)
    assert rows[-1].status == "NOT_MEASURED"
    assert rows[-1].reason_class == R._V.ReasonClass.INPUT_ABSENT
    assert lec_runs == []
    # phase 3 before its synthesis half: still nothing to prove
    rows = P3.run_step13_lec_on_pnr_input(proj, CORE, "")
    assert [(r.name, r.status) for r in rows] == [
        ("lec_pnr_input", "NOT_MEASURED")]
    assert (proj / NOT_RUN).is_file()
    hint = FCC._sibling_self_skip_for_missing(proj, [OTHER_REPORTS_OUTPUT])
    assert hint is not None and hint.startswith(NOT_RUN), hint
    assert D._disclosed_skip(proj, STEP13_OUTPUTS)[0] is True
    assert _dashboard_step13(proj)[0] == "skipped"
    assert G.bound_proof(proj) is None
    assert G.retire_superseded_not_run(proj) is None
    assert (proj / NOT_RUN).is_file()


def test_a_proof_of_another_netlist_supersedes_nothing(tmp_path, lec_runs):
    """STALE: the routed netlist changed after the proof."""
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    (proj / ROUTED).write_text(_CORE_MAPPED.replace("nand2", "and2"))
    doc = json.loads((proj / "reports/lec.json").read_text())
    assert S.proof_subject_binding(proj, doc)["state"] == S.BINDING_STALE
    assert G.audit(proj).passed is False
    _plant(proj, _phase2_not_run_record(tmp_path))
    assert G.bound_proof(proj) is None
    assert G.retire_superseded_not_run(proj) is None
    assert (proj / NOT_RUN).is_file()
    hint = FCC._sibling_self_skip_for_missing(proj, [OTHER_REPORTS_OUTPUT])
    assert hint is not None and hint.startswith(NOT_RUN), hint


def test_a_proof_before_the_routed_netlist_exists_supersedes_nothing(
        tmp_path, lec_runs):
    """NO_CONSUMER: the gate passes a proof of the generic netlist.v (the
    pre-F1 subject, measured on spm run23) while the netlist step 15 routes is
    not on disk yet -- exactly what the not-run record says, so it stands."""
    proj = _tree(tmp_path, mapped=False)
    _phase2_chain(proj)
    generic = proj / f"{SYNTH}/netlist.v"
    (proj / "reports/lec.json").write_text(json.dumps({
        "verdict": "PASS", "equivalent": True, "compared_points": 1,
        "non_equivalent_points": 0, "unproven_points": 0,
        "gold": f"{CORE} (RTL)", "gate": "netlist.v (synth)",
        "proof_identity": {"top": CORE, "gate_netlist": {
            "path": f"{SYNTH}/netlist.v", "sha256": _sha(generic)}}}))
    (proj / "reports/lec.rpt").write_text(
        "Equivalence successfully proven!\nProved 1 $equiv cells.\n")
    res = G.audit(proj)
    assert res.passed is True
    assert res.summary["subject_binding"]["state"] == S.BINDING_NO_CONSUMER
    assert G.bound_proof(proj) is None
    assert G.retire_superseded_not_run(proj) is None
    assert (proj / NOT_RUN).is_file()
    hint = FCC._sibling_self_skip_for_missing(proj, [OTHER_REPORTS_OUTPUT])
    assert hint is not None and hint.startswith(NOT_RUN), hint
    assert D._disclosed_skip(proj, STEP13_OUTPUTS)[0] is True


def test_a_bound_non_equivalent_result_supersedes_nothing(tmp_path,
                                                          lec_runs):
    """Bound to the routed file, but the gate does not pass it."""
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    lec = proj / "reports/lec.json"
    doc = json.loads(lec.read_text())
    doc.update(verdict="FAIL", equivalent=False, non_equivalent_points=1)
    lec.write_text(json.dumps(doc))
    assert S.proof_subject_binding(proj, doc)["state"] == S.BINDING_MATCH
    _plant(proj, _phase2_not_run_record(tmp_path))
    assert G.bound_proof(proj) is None
    assert G.retire_superseded_not_run(proj) is None
    assert (proj / NOT_RUN).is_file()
    hint = FCC._sibling_self_skip_for_missing(proj, [OTHER_REPORTS_OUTPUT])
    assert hint is not None and hint.startswith(NOT_RUN), hint


def test_only_step13s_own_record_is_ever_superseded(tmp_path, lec_runs):
    """Another step's skip record in reports/ is not the LEC proof's to
    excuse or retire."""
    proj = _tree(tmp_path)
    _phase2_chain(proj)
    other = proj / "reports/other_not_run.json"
    other.write_text(json.dumps({"verdict": "SKIPPED-CONDITION",
                                 "reason": "another step's disclosure"}))
    assert G.bound_proof(proj) is not None
    assert G.lec_not_run_superseded(proj, other) is None
    assert G.retire_superseded_not_run(proj) is None
    assert other.is_file()
    hint = FCC._sibling_self_skip_for_missing(proj, [OTHER_REPORTS_OUTPUT])
    assert hint is not None and hint.startswith("reports/other_not_run.json")
