#!/usr/bin/env python3
"""F10 — a step nobody measured is NOT_MEASURED and leaves the verdict by name.

THE DEFECT (lanes T95 and T108, independently). When the runner discloses that
no FPGA bitstream was built (`reports/phase2/fpga/quartus_map_audit.json`
verdict SKIP, sof_present false), `_synthesise_fpga_skip_waivers` wrote an
ENV_UNAVAILABLE "waiver" for every board step and `check_step` promoted the
steps' natural FAIL to PASS_WITH_WAIVERS. So a run whose board steps were never
measured read `Overall: PASS_WITH_WAIVERS` — a verdict about work nobody did.

THE OWNER RULE. A step that was not measured is NOT_MEASURED and is excluded
from the verdict. It is never a waiver: a waiver needs evidence, a ticket and
review_required, authored by someone, about a step that ran.

WHAT THIS FILE PINS, by driving the shipped `main()` over a flow fixture whose
board steps are the SHIPPED flow's own (id, name, stage, required_outputs):

  * a disclosed board skip can no longer produce PASS_WITH_WAIVERS; the board
    steps are NOT_MEASURED, listed by name in `not_measured_excluded`, and the
    verdict is decided by the steps that were measured;
  * an authored waiver (evidence + ticket + review_required) still produces
    PASS_WITH_WAIVERS, exactly as before;
  * an UNDISCLOSED missing bitstream still FAILs — exclusion is not a blanket;
  * the board set is read off the flow's declarations (the steps that owe a
    bitstream), never a list of step numbers.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F  # noqa: E402
import fpga_board_capability as _cap  # noqa: E402

_SHIPPED_FLOW = F._find_flow_def()
_MEASURED_OUT = "out/measured.txt"


def _shipped_steps():
    return yaml.safe_load(Path(_SHIPPED_FLOW).read_text())["steps"]


def _board_steps():
    """The shipped flow's board steps, as the flow declares them."""
    return [s for s in _shipped_steps() if _cap.step_owes_bitstream(s)]


def _flow(tmp_path: Path) -> Path:
    """One measured stand-in step plus the shipped board steps.

    The board steps keep their real id, name, stage and required_outputs; their
    gate is the presence of the bitstream they owe, which is the clause every
    board step's natural FAIL comes from when no board exists.
    """
    ids = {str(s["id"]) for s in _shipped_steps()}
    measured_id = next(str(n) for n in range(1000, 2000) if str(n) not in ids)
    steps = [{"id": int(measured_id), "name": "measured stand-in",
              "stage": "stage1", "required_outputs": [_MEASURED_OUT],
              "gate": {"files_exist": [_MEASURED_OUT]}}]
    for s in _board_steps():
        owed = [alt.strip() for e in s["required_outputs"]
                for alt in str(e).split(" OR ")
                if alt.strip().lower().endswith(_cap.BITSTREAM_SUFFIX)]
        steps.append({"id": s["id"], "name": s["name"], "stage": s["stage"],
                      "required_outputs": list(s["required_outputs"]),
                      "gate": {"files_exist": owed}})
    flow = tmp_path / "flow.yaml"
    flow.write_text(yaml.safe_dump({"steps": steps}, sort_keys=False))
    return flow


def _project(tmp_path: Path, *, disclosed: bool, measured: bool,
             authored_waiver_for_measured: bool = False) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    if measured:
        (proj / _MEASURED_OUT).parent.mkdir(parents=True)
        (proj / _MEASURED_OUT).write_text("measured\n")
    if disclosed:
        d = proj / "reports/phase2/fpga"
        d.mkdir(parents=True)
        (d / "quartus_map_audit.json").write_text(json.dumps(
            {"verdict": "SKIP", "sof_present": False,
             "evidence": "fpga_compile not run"}))
    if authored_waiver_for_measured:
        ev = proj / "reports/waiver_evidence.md"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text("bench log: the measurement host was down (fixture)\n")
        (proj / "waivers.json").write_text(json.dumps({"waived_steps": [{
            "id": _measured_id(tmp_path),
            "reason": ("The instrument this step reads was offline for the "
                       "whole run window, so it is deferred to re-run on "
                       "bring-up (test fixture)."),
            "approver": "f10-test-harness",
            "ticket": "TEST-F10",
            "review_required": True,
            "evidence": ["reports/waiver_evidence.md"],
            "date": "2026-09-26"}]}, indent=2))
    return proj


def _measured_id(tmp_path: Path) -> int:
    doc = yaml.safe_load((tmp_path / "flow.yaml").read_text())
    return doc["steps"][0]["id"]


def _audit(tmp_path: Path, **kw):
    flow = _flow(tmp_path)
    proj = _project(tmp_path, **kw)
    out = tmp_path / "audit.json"
    run = subprocess.run(
        [sys.executable, str(PROGRAMS / "flow_compliance_check.py"),
         str(proj), "--flow-def", str(flow), "--strict",
         "--skip-yosys-gates", "--json", str(out)],
        capture_output=True, text=True, timeout=600)
    assert out.is_file(), (run.returncode, run.stdout[-3000:],
                           run.stderr[-3000:])
    doc = json.loads(out.read_text())
    rows = {str(r["id"]): r for r in doc["steps"]}
    return run, doc, rows


def test_the_board_set_is_read_off_the_flows_declarations():
    derived = _cap.board_bound_step_ids(_shipped_steps())
    assert derived, "the shipped flow declares no bitstream-owing step"
    assert F._FPGA_BOARD_STEP_IDS == derived
    # Renumber-proof: the set follows the declaration, not an id.
    moved = [{"id": 900 + i, "required_outputs": s["required_outputs"]}
             for i, s in enumerate(_board_steps())]
    moved.append({"id": 6, "required_outputs": ["phase3/stage4/gds/*.gds"]})
    assert _cap.board_bound_step_ids(moved) == frozenset(
        900 + i for i in range(len(_board_steps())))


def test_a_disclosed_board_skip_can_no_longer_produce_pass_with_waivers(
        tmp_path):
    run, doc, rows = _audit(tmp_path, disclosed=True, measured=True)
    board = {str(i) for i in F._FPGA_BOARD_STEP_IDS}
    assert doc["overall"] != "PASS_WITH_WAIVERS", run.stdout[-3000:]
    for sid in board:
        assert rows[sid]["status"] == "NOT_MEASURED", rows[sid]
        assert rows[sid]["excluded_from_verdict"], rows[sid]
        assert rows[sid]["reason_class"], rows[sid]
    # Listed BY NAME beside the verdict, in the report and on stdout.
    listed = {str(e["step_id"]): e for e in doc["not_measured_excluded"]}
    assert set(listed) == board, doc["not_measured_excluded"]
    for sid in board:
        assert listed[sid]["step_name"] == rows[sid]["name"]
        assert rows[sid]["name"] in run.stdout
    assert "excluded from the verdict" in run.stdout
    # The verdict is the measured steps' own: here, the one measured step
    # passed, so the run is PASS with the board steps named outside it.
    assert doc["overall"] == "PASS", (doc["overall"], run.stdout[-3000:])
    assert doc["counts"]["PASS_WITH_WAIVERS"] == 0, doc["counts"]
    assert run.returncode == 0, run.stdout[-3000:]


def test_excluded_steps_do_not_decide_a_red_run_either(tmp_path):
    # The measured step FAILs: the run is FAIL because of IT, and the board
    # steps are still NOT_MEASURED and named — not folded into the red.
    run, doc, rows = _audit(tmp_path, disclosed=True, measured=False)
    assert doc["overall"] == "FAIL", doc["overall"]
    assert {str(e["step_id"]) for e in doc["not_measured_excluded"]} == {
        str(i) for i in F._FPGA_BOARD_STEP_IDS}


def test_an_authored_waiver_still_produces_pass_with_waivers(tmp_path):
    run, doc, rows = _audit(tmp_path, disclosed=True, measured=False,
                            authored_waiver_for_measured=True)
    sid = str(_measured_id(tmp_path))
    # A GUARD: green before the fix and after it (`.get` so it measures the
    # same behaviour on a report that predates the new fields).
    assert rows[sid]["status"] == "PASS_WITH_WAIVERS", rows[sid]
    assert not rows[sid].get("excluded_from_verdict"), rows[sid]
    assert doc["overall"] == "PASS_WITH_WAIVERS", (doc["overall"],
                                                   run.stdout[-3000:])
    assert sid not in {str(e["step_id"])
                       for e in doc.get("not_measured_excluded", [])}


def test_an_undisclosed_missing_bitstream_still_fails(tmp_path):
    run, doc, rows = _audit(tmp_path, disclosed=False, measured=True)
    for sid in (str(i) for i in F._FPGA_BOARD_STEP_IDS):
        assert rows[sid]["status"] == "FAIL", rows[sid]
        assert not rows[sid].get("excluded_from_verdict"), rows[sid]
    assert doc.get("not_measured_excluded", []) == []
    assert doc["overall"] == "FAIL", doc["overall"]


def test_a_step_that_owes_no_bitstream_is_never_excluded(tmp_path):
    # The audit's own board-skip row, forged onto a step the flow does NOT
    # declare as owing a bitstream: it is not a board step, so it is not
    # excluded (it keeps the ENV_UNAVAILABLE tier an ENV row always had).
    ids = {s["id"] for s in _shipped_steps()}
    sid = next(n for n in range(1000, 2000) if n not in ids)
    step = {"id": sid, "name": "not a board step", "stage": "stage1",
            "required_outputs": ["out/never.txt"],
            "gate": {"files_exist": ["out/never.txt"]}}
    w = {sid: {"id": sid, "reason": "forged", "approver": "x",
               "ticket": "T", "review_required": True,
               "verdict_tier": "ENV_UNAVAILABLE", "_env_unavailable": True,
               "_fpga_skip": True, "cause": "quartus_absent"}}
    r = F.check_step(tmp_path, step, w)
    assert not getattr(r, "excluded_from_verdict", ""), r.reasons
    assert r.status != "NOT_MEASURED", r.reasons
