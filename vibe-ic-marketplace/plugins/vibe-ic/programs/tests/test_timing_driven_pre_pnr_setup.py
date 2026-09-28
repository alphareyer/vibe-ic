"""Pre-PnR setup must stop a measured SS violation before placement."""
import json
import shutil
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import librelane_prelayout as prelayout
from _hostpaths import require_repo
from test_phase3_cache_producer_identity import _drive, _plan, _project
import phase3_one_shot_runner as runner


def _path(slack: float) -> str:
    result = "VIOLATED" if slack < 0 else "MET"
    return ("Startpoint: in_a (input port clocked by clk)\n"
            "Endpoint: out_z (output port clocked by clk)\n"
            "Path Group: clk\nPath Type: max\n\n"
            f"          {slack:.2f}   slack ({result})\n\n"
            "STA_BASIS: PRE_LAYOUT_ESTIMATE\n")


def _fixture(tmp_path: Path, slack: float) -> tuple[Path, Path, Path]:
    matrix = tmp_path / "pvt_matrix.json"
    matrix.write_text(json.dumps({"corners": [
        {"label": "SS", "name": "slow", "liberty": "slow.lib"}]}))
    reports = tmp_path / "per_corner"
    reports.mkdir()
    (reports / "sta_SS.rpt").write_text(_path(slack))
    return matrix, reports, tmp_path / "gate.json"


def test_negative_ss_path_blocks_with_observed_class(tmp_path):
    matrix, reports, output = _fixture(tmp_path, -0.21)
    result = prelayout.pre_pnr_setup_gate(matrix, reports, output)
    assert result["verdict"] == "FAIL"
    assert result["setup_slack_ns"] == -0.21
    assert result["path_classes"] == ["in-to-out"]
    assert json.loads(output.read_text())["reason"] == "NEGATIVE_PRE_PNR_SETUP_SLACK"


def test_positive_ss_path_proceeds(tmp_path):
    matrix, reports, output = _fixture(tmp_path, 0.43)
    result = prelayout.pre_pnr_setup_gate(matrix, reports, output)
    assert result["verdict"] == "PASS"
    assert result["setup_slack_ns"] == 0.43


def test_missing_ss_report_is_not_a_pass(tmp_path):
    matrix, reports, output = _fixture(tmp_path, 0.43)
    (reports / "sta_SS.rpt").unlink()
    assert prelayout.pre_pnr_setup_gate(matrix, reports, output)["verdict"] == "NOT_MEASURED"


def test_existing_step10_judge_refuses_negative_slack(tmp_path):
    """This is RED on main: judge_slack returned PASS for -0.21 ns."""
    step = tmp_path / "step10"
    corner = step / "slow"
    corner.mkdir(parents=True)
    (corner / "sta.log").write_text("OpenSTA linked design\n")
    (corner / "max.rpt").write_text(_path(-0.21))
    (step / "state_out.json").write_text(json.dumps({"metrics": {
        "timing__setup__ws__corner:slow": -0.21,
        "timing__hold__ws__corner:slow": 0.18}}))
    result = prelayout.judge_slack(step, tmp_path / "step10_gate.json")
    assert result["verdict"] == "FAIL"
    assert result["corners"]["slow"]["negative_setup_path_classes"] == ["in-to-out"]


def test_checked_in_opensta_ss_report_blocks(tmp_path):
    source = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "sta_mcorner_r27", "phase3", "stage3", "sta",
        "per_corner", "sta_SS.rpt")
    matrix, reports, output = _fixture(tmp_path, 0.43)
    shutil.copyfile(source, reports / "sta_SS.rpt")
    result = prelayout.pre_pnr_setup_gate(matrix, reports, output)
    assert result["verdict"] == "FAIL"
    assert result["setup_slack_ns"] == -6.10
    assert result["path_classes"] == ["reg-to-reg"]


def test_report_must_bind_current_netlist_and_sdc(tmp_path):
    matrix, reports, output = _fixture(tmp_path, 0.43)
    netlist = tmp_path / "netlist.v"
    sdc = tmp_path / "constraints.sdc"
    netlist.write_text("module t; endmodule\n")
    sdc.write_text("create_clock -period 20 clk\n")
    assert prelayout.pre_pnr_setup_gate(
        matrix, reports, output, netlist=netlist, sdc=sdc
    )["reason"] == "SS_REPORT_NETLIST_IDENTITY_STALE"
    with (reports / "sta_SS.rpt").open("a") as report:
        report.write(
            f"STA_BASIS_NETLIST_SHA256: sha256:{prelayout.digest(netlist)}\n"
            f"STA_BASIS_SDC_SHA256: sha256:{prelayout.digest(sdc)}\n")
    assert prelayout.pre_pnr_setup_gate(
        matrix, reports, output, netlist=netlist, sdc=sdc
    )["verdict"] == "PASS"
    netlist.write_text("module t(input a); endmodule\n")
    assert prelayout.pre_pnr_setup_gate(
        matrix, reports, output, netlist=netlist, sdc=sdc
    )["reason"] == "SS_REPORT_NETLIST_IDENTITY_STALE"


@pytest.mark.parametrize("cached", [False, True])
def test_step10_black_box_blocks_both_pnr_paths(tmp_path, monkeypatch, cached):
    """A positive, current SS path cannot override Step 10's failed link check."""
    project = _project(tmp_path, stamp=cached)
    called = _drive(monkeypatch, project)
    netlist = runner._pl.synth_dir(project) / "chip_top_synth.v"
    sdc = runner._pl.pnr_dir(project) / "constraint.sdc"
    matrix = runner._pl.constraints_dir(project) / "pvt_matrix.json"
    matrix.write_text(json.dumps({"corners": [
        {"label": "SS", "name": "slow", "liberty": "slow.lib"}]}))
    reports = runner._pl.sta_dir(project) / "per_corner"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "sta_SS.rpt").write_text(
        _path(0.43)
        + f"STA_BASIS_NETLIST_SHA256: sha256:{prelayout.digest(netlist)}\n"
        + f"STA_BASIS_SDC_SHA256: sha256:{prelayout.digest(sdc)}\n")

    # Replay a typed Step 10 producer row judged from a checked-in OpenSTA
    # black-box log. The consumer under test is the real runner main().
    from test_mig_sdcsta import sta_folder
    folder, _ = sta_folder(tmp_path / "tool", log="sta_prepnr_black_box_positive.log")
    step10 = prelayout.judge_slack(folder, tmp_path / "step10.json")
    assert step10["verdict"] == "FAIL"
    assert "LL_STA_BLACK_BOX" in " ".join(step10["findings"])
    monkeypatch.setattr(runner, "step_prelayout_signoff", lambda *a, **k:
        runner.StepResult("prelayout_signoff", "FAIL", 0.0,
                          "step 10 gate FAIL: LL_STA_BLACK_BOX",
                          [str(tmp_path / "step10.json")]))

    runner.main()
    plan = _plan(project)
    assert plan["pre_pnr_setup"]["status"] == "PASS", plan["pre_pnr_setup"]
    assert plan["prelayout_signoff"]["status"] == "FAIL"
    assert plan["pnr"]["status"] == "NOT_MEASURED", plan["pnr"]
    assert "LL_STA_BLACK_BOX" in plan["pnr"]["detail"]
    assert "pnr" not in called, "a failed Step 10 must block a new route"
    assert "skipped re-run" not in plan["pnr"]["detail"], (
        "a failed Step 10 must also block cached route admission")


def test_unmeasured_step10_cannot_admit_cached_pnr(tmp_path, monkeypatch):
    """A typed Step 10 gap blocks reuse even when the SS path itself is green."""
    project = _project(tmp_path, stamp=True)
    called = _drive(monkeypatch, project)
    from test_mig_sdcsta import sta_folder
    folder, _ = sta_folder(tmp_path / "unmeasured_tool", setup=None)
    step10 = prelayout.judge_slack(folder, tmp_path / "step10_unmeasured.json")
    assert step10["verdict"] == "NOT_MEASURED"
    monkeypatch.setattr(runner, "step_prelayout_signoff", lambda *a, **k:
        runner.StepResult("prelayout_signoff", "NOT_MEASURED", 0.0,
                          "step 10 setup metric NOT_MEASURED",
                          [str(tmp_path / "step10_unmeasured.json")],
                          reason_class=runner._V.ReasonClass.INCONCLUSIVE))

    runner.main()
    plan = _plan(project)
    assert plan["pre_pnr_setup"]["status"] == "PASS", plan["pre_pnr_setup"]
    assert plan["prelayout_signoff"]["status"] == "NOT_MEASURED"
    assert plan["pnr"]["status"] == "NOT_MEASURED", plan["pnr"]
    assert "step 10 setup metric NOT_MEASURED" in plan["pnr"]["detail"]
    assert "skipped re-run" not in plan["pnr"]["detail"]
    assert "pnr" not in called


def test_unreadable_setup_input_publishes_typed_block(tmp_path, monkeypatch):
    """An absent matrix yields a plan row and never crashes StepResult validation."""
    project = _project(tmp_path, stamp=True)
    called = _drive(monkeypatch, project)
    (runner._pl.constraints_dir(project) / "pvt_matrix.json").unlink()

    runner.main()
    plan = _plan(project)
    assert plan["prelayout_signoff"]["status"] == "PASS"
    assert plan["pre_pnr_setup"]["status"] == "NOT_MEASURED"
    assert plan["pre_pnr_setup"]["reason_class"] == "execution_error"
    assert "PRE_PNR_GATE_INPUT_UNREADABLE:FileNotFoundError" in plan["pnr"]["detail"]
    assert plan["pnr"]["status"] == "NOT_MEASURED"
    assert "pnr" not in called
