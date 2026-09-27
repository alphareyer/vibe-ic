"""llv1 W19: rows imported from LibreLane's own step logs are WITNESSED runs.

Owner decision 4a (2026-09-28): a row imported from LibreLane's step log counts
as a witnessed run attributed to LibreLane, and it cites its source log by path
and sha256. Rule #365 stays: a runner back-fill is still `reconstructed: true`.

Before this, `provenance_check` had no notion of a witness at all, so a row
could claim one and cite anything — a log since rewritten, a log from a step
the flow never started, a back-fill wearing a witness — and bind its artefact
exactly as a real run does. These tests hold the difference.

The flow logs are the two real calibration samples (a whole CMP3 LibreLane
flow.log, and the same log cut before detailed routing).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
_PLUGIN = PROGRAMS.parent
for _p in (str(PROGRAMS), str(_PLUGIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CHECK = PROGRAMS / "provenance_check.py"
CAL = PROGRAMS / "calibration"
WHOLE = CAL / "librelane_flow_log_complete_negative.log"
ABORTED = CAL / "librelane_flow_log_aborted_positive.log"

RUN = "phase3/librelane/runs/seg2"
STEP = "44-openroad-detailedrouting"
STEP_ID = "OpenROAD.DetailedRouting"
OUT = "phase3/stage3/pnr/routed.def"


def _project(tmp_path: Path, flow_log: Path = WHOLE) -> Path:
    proj = tmp_path / "proj"
    run = proj / RUN
    (run / STEP).mkdir(parents=True)
    shutil.copyfile(flow_log, run / "flow.log")
    (run / STEP / "openroad-detailedrouting.log").write_text(
        "[INFO DRT-0198] Complete detail routing.\n")
    (proj / OUT).parent.mkdir(parents=True)
    (proj / OUT).write_text("VERSION 5.8 ;\nEND DESIGN\n")
    return proj


def _row(proj: Path, **kw):
    import _tool_log_provenance as T
    args = dict(flow="librelane", step_id=STEP_ID, run_dir=proj / RUN,
                step_dir=STEP,
                logs=[proj / RUN / STEP / "openroad-detailedrouting.log"],
                outputs={OUT: proj / OUT}, exit_code=0,
                timestamp="2026-09-28T00:00:00Z")
    args.update(kw)
    return T.witnessed_row(proj, **args)


def _ledger(proj: Path, *rows) -> None:
    (proj / "provenance.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def _check(proj: Path, tool: str = "openroad"):
    return subprocess.run(
        [sys.executable, str(CHECK), str(proj), "--output", OUT,
         "--tool", tool], capture_output=True, text=True, timeout=120)


# ── the witnessed row ────────────────────────────────────────────────────

def test_a_witnessed_row_cites_its_log_and_flow_log_by_sha(tmp_path):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    row = _row(proj)
    assert row["reconstructed"] is False
    assert row["attributed_to"] == "librelane"
    assert row["tool"] == "openroad"          # the allow-lists judge it as is
    w = row["witness"]
    assert w["step_id"] == STEP_ID and w["step_dir"] == STEP
    log = proj / RUN / STEP / "openroad-detailedrouting.log"
    assert w["logs"] == [{"path": f"{RUN}/{STEP}/openroad-detailedrouting.log",
                          "sha256": T._sha256(log)}]
    assert w["flow_log"] == {"path": f"{RUN}/flow.log",
                             "sha256": T._sha256(proj / RUN / "flow.log")}
    assert row["outputs"] == {OUT: T._sha256(proj / OUT)}
    assert T.is_witnessed(row, proj)
    _ledger(proj, row)
    r = _check(proj)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_attributed_tool_comes_from_the_step_not_the_caller():
    import _tool_log_provenance as T
    assert T.underlying_tool("OpenROAD.STAMidPNR-3") == "openroad"
    assert T.underlying_tool("Odb.ReportWireLength") == "openroad"
    assert T.underlying_tool("Magic.StreamOut") == "magic"
    assert T.underlying_tool("KLayout.DRC") == "klayout"
    assert T.underlying_tool("Netgen.LVS") == "netgen"
    assert T.underlying_tool("Yosys.Synthesis") == "yosys"
    assert T.underlying_tool("Checker.TrDRC") is None
    assert T.underlying_tool("Misc.ReportManufacturability") is None


# ── a claimed witness that does not hold binds nothing ────────────────────

def test_a_rewritten_step_log_unbinds_the_row(tmp_path):
    proj = _project(tmp_path)
    _ledger(proj, _row(proj))
    (proj / RUN / STEP / "openroad-detailedrouting.log").write_text("edited\n")
    r = _check(proj)
    assert r.returncode == 1, r.stdout
    assert "claims a witness that does not hold" in r.stdout + r.stderr


def test_a_step_the_flow_never_started_is_not_witnessed(tmp_path):
    """The run ended before detailed routing: its own flow.log never started
    the step, so a row citing that step is fabricated, whatever the hashes."""
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    row = _row(proj)
    shutil.copyfile(ABORTED, proj / RUN / "flow.log")
    row["witness"]["flow_log"]["sha256"] = T._sha256(proj / RUN / "flow.log")
    _ledger(proj, row)
    ok, why = T.verify_witness(row, proj)
    assert ok is False and "never started" in why
    assert _check(proj).returncode == 1


def test_a_back_fill_wearing_a_witness_is_refused(tmp_path):
    proj = _project(tmp_path)
    row = _row(proj)
    row["reconstructed"] = True
    _ledger(proj, row)
    r = _check(proj)
    assert r.returncode == 1 and "#365" in r.stdout


def test_attribution_without_a_witness_is_refused(tmp_path):
    proj = _project(tmp_path)
    row = _row(proj)
    del row["witness"]
    _ledger(proj, row)
    assert _check(proj).returncode == 1


def test_a_tool_the_step_does_not_run_is_refused(tmp_path):
    proj = _project(tmp_path)
    row = _row(proj)
    row["tool"] = "klayout"
    _ledger(proj, row)
    r = _check(proj, tool="klayout,openroad")
    assert r.returncode == 1 and "is not the tool step" in r.stdout


def test_a_symlinked_log_is_not_evidence(tmp_path):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    row = _row(proj)
    log = proj / RUN / STEP / "openroad-detailedrouting.log"
    real = tmp_path / "elsewhere.log"
    shutil.move(log, real)
    log.symlink_to(real)
    with pytest.raises(T.WitnessRefused, match="symlink"):
        _row(proj)
    _ledger(proj, row)
    assert _check(proj).returncode == 1


# ── witnessed_row refuses what it cannot support ─────────────────────────

@pytest.mark.parametrize("kw,match", [
    (dict(step_id="Checker.TrDRC"), "runs no tool"),
    (dict(flow="orfs"), "no witness rule"),
    (dict(step_id="OpenROAD.GlobalRouting"), "never started"),
    (dict(exit_code="0"), "must be an int"),
    (dict(logs=[]), "no tool log"),
    (dict(outputs={"other/name.def": None}), "is not the path"),
])
def test_witnessed_row_refuses_unsupported_claims(tmp_path, kw, match):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    if "outputs" in kw:
        kw = dict(outputs={"other/name.def": proj / OUT})
    with pytest.raises(T.WitnessRefused, match=match):
        _row(proj, **kw)


def test_a_log_outside_the_step_folder_is_refused(tmp_path):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    other = proj / RUN / "43-openroad-stamidpnr-3"
    other.mkdir()
    (other / "x.log").write_text("x\n")
    with pytest.raises(T.WitnessRefused, match="not inside the step"):
        _row(proj, logs=[other / "x.log"])


def test_a_run_without_its_flow_log_has_no_witness(tmp_path):
    import _tool_log_provenance as T
    proj = _project(tmp_path)
    (proj / RUN / "flow.log").unlink()
    with pytest.raises(T.WitnessRefused, match="nothing names the step"):
        _row(proj)


# ── rule #365 is untouched ────────────────────────────────────────────────

def test_a_runner_back_fill_is_still_reconstructed_and_never_witnessed(
        tmp_path):
    import _tool_log_provenance as T
    import phase3_one_shot_runner as R
    proj = _project(tmp_path)
    (proj / "provenance.jsonl").write_text("")
    R._restamp_provenance_output(proj, OUT, proj / OUT, "openroad", "import")
    rows = [json.loads(l) for l in
            (proj / "provenance.jsonl").read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    assert rows[0]["reconstructed"] is True
    assert rows[0]["duration_ms"] is None
    assert not T.claims_witness(rows[0])
    assert T.verify_witness(rows[0], proj) == (None, "")
    assert not T.is_witnessed(rows[0], proj)
    # and the checker judges it exactly as before
    assert _check(proj).returncode == 0


def test_the_flow_log_reader_is_calibrated():
    import instrument_calibration as I
    cal = I.check("_tool_log_provenance::flow_log_names_step")
    assert cal.state == I.CALIBRATED, cal.as_dict()
    assert cal.positive_outcome == "UNWITNESSED"
    assert cal.negative_outcome is None
