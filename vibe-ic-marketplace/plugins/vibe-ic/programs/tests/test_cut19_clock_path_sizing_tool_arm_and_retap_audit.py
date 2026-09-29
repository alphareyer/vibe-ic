"""CUT_W4 step 19 (R-0929-TOOL-DEFAULT): the tool sizes the clock path, and the
own retap sub-step is audited.

1. The clock-path sizing is OpenROAD's `repair_timing -phases GLOBAL_SIZING`
   with the clock network included (`Vibeic.ClockNetworkGlobalSizing`), the
   default arm; vibe-ic's #2160 swapMaster search stays selectable (harvest,
   then delete). MEASURED on spm x gf180mcuD (vibeic-eda 0.3.86, lane tailb):
   unrestricted, the tool re-mastered 76 CTS clkbuf_8 into DATA buffers, so the
   step holds the CTS-built instances dont_touch for the pass (vibeic/OpenROAD
   PR #38 makes the resizer keep a clock buffer a clock buffer).
2. `retap_audit_check` re-decides every retained retap from the numbers the
   step printed and matches it to the netlist change the step made. The logs
   below are the REAL step logs the calibration pair uses; the netlists are
   the step's view files, written the way OpenROAD `write_verilog` writes
   them (the only EDA output faked here).
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import librelane_cts_hold as cts  # noqa: E402
import librelane_contract as contract  # noqa: E402
try:        # absent before CUT_W4: every audit test then fails on its own line
    import retap_audit_check as RA  # noqa: E402
except ImportError:  # pragma: no cover - the RED arm
    RA = None

PLUGIN = PROGRAMS / "librelane_plugins/librelane_plugin_vibeic"
CAL = PROGRAMS / "calibration"
KEEP_LOG = (CAL / "retap_keep_positive.log").read_text()
REJECT_LOG = (CAL / "retap_reject_negative.log").read_text()

NL_IN = """module chip_top (clk, o);
 input clk;
 output o;
 gf_clkbuf_4 clkbuf_0_clk (.I(clk),
    .Z(clknet_0_i_clk__core));
 gf_clkbuf_4 clkbuf_leaf_17_clk (.I(clknet_0_i_clk__core),
    .Z(clknet_leaf_17_i_clk__core));
 gf_dffq_1 \\u_core/_1753_  (.CLK(clknet_leaf_17_i_clk__core),
    .D(n1),
    .Q(o));
 gf_dffq_1 \\u_core/_1683_  (.CLK(clknet_leaf_17_i_clk__core),
    .D(n2),
    .Q(n3));
 gf_filltie TAP_1 ();
endmodule
"""
NL_KEPT = NL_IN.replace(
    "\\u_core/_1753_  (.CLK(clknet_leaf_17_i_clk__core)",
    "\\u_core/_1753_  (.CLK(clknet_0_i_clk__core)")


def _project(tmp_path, log, nl_out, nl_in=NL_IN):
    project = tmp_path / "proj"
    folder = project / "phase3/librelane/19-cts-hold/03-vibeic-externalcapturelaunchretap"
    folder.mkdir(parents=True)
    (folder / "vibeic-externalcapturelaunchretap.log").write_text(log)
    (folder / "in.nl.v").write_text(nl_in)
    (folder / "chip_top.nl.v").write_text(nl_out)
    (folder / "state_in.json").write_text(json.dumps({"nl": str(folder / "in.nl.v")}))
    (folder / "state_out.json").write_text(json.dumps({"nl": str(folder / "chip_top.nl.v")}))
    receipt = project / "reports/phase3/librelane_cts_hold_handoff.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"chain": {
        "Vibeic.ExternalCaptureLaunchRetap": str(folder.relative_to(project))}}))
    return project


def _run(project):
    rc = RA.main([str(project), "--json", "reports/phase3/gates/retap_audit.json"])
    doc = json.loads((project / "reports/phase3/gates/retap_audit.json").read_text())
    return rc, doc


# ------------------------------------------------ 1. the tool sizing arm ---

def test_the_default_clock_path_sizing_is_the_tools_global_sizing(tmp_path):
    assert cts.clock_path_sizing_arm(tmp_path) == "tool"
    assert cts.CLOCK_PATH_SIZING_ARMS["tool"] == "Vibeic.ClockNetworkGlobalSizing"
    (tmp_path / "phase3").mkdir()
    (tmp_path / "phase3/librelane_switch.json").write_text(
        json.dumps({"arms": {"19.clock_path_sizing": "own"}}))
    assert cts.clock_path_sizing_arm(tmp_path) == "own"
    (tmp_path / "phase3/librelane_switch.json").write_text(
        json.dumps({"arms": {"19.clock_path_sizing": "guess"}}))
    with pytest.raises(contract.Refusal):
        cts.clock_path_sizing_arm(tmp_path)


def test_the_tool_step_runs_openroad_global_sizing_on_the_clock_network():
    init = (PLUGIN / "__init__.py").read_text()
    assert 'id = "Vibeic.ClockNetworkGlobalSizing"' in init
    assert "class ClockNetworkGlobalSizing(ResizerTimingPostCTS)" in init
    assert '"ClockNetworkGlobalSizing"' in init
    tcl = (PLUGIN / "clock_network_global_sizing.tcl").read_text()
    assert "set_global_sizing_config -include_clock_network true" in tcl
    assert "-phases GLOBAL_SIZING" in tcl
    # no own sizing algorithm in the tool arm
    assert "swapMaster" not in tcl and "VIBEIC_CLKPATH_SIZING_TCL" not in tcl
    # the measured defect's interim: CTS-built instances are held for the pass
    # and released before legalization; the audit fanout metric is kept
    hold = tcl.index("setDoNotTouch 1")
    assert hold < tcl.index("log_cmd repair_timing") < tcl.index("setDoNotTouch 0") \
        < tcl.index("common/dpl.tcl")
    assert 'utl::metric_integer "vibeic__cts__max_fanout"' in tcl
    assert "write_views" in tcl


# ------------------------------------------------ 2. the retap audit -------

def test_a_retained_retap_matched_to_its_netlist_change_passes(tmp_path):
    rc, doc = _run(_project(tmp_path, KEEP_LOG, NL_KEPT))
    assert rc == 0 and doc["verdict"] == "PASS", doc
    assert [k["inst"] for k in doc["keeps"]] == ["u_core/_1753_"]
    assert doc["netlist"]["moved_connections"] == 1


def test_a_netlist_change_no_keep_row_explains_fails(tmp_path):
    # the reject-only log, but the netlist moved a clock pin anyway
    rc, doc = _run(_project(tmp_path, REJECT_LOG, NL_KEPT))
    assert rc == 1 and doc["verdict"] == "FAIL"
    assert [f["code"] for f in doc["findings"]] == ["RETAP_UNEXPLAINED_NETLIST_CHANGE"]


def test_a_keep_row_the_netlist_does_not_carry_fails(tmp_path):
    rc, doc = _run(_project(tmp_path, KEEP_LOG, NL_IN))
    assert rc == 1
    assert [f["code"] for f in doc["findings"]] == ["RETAP_KEEP_NOT_IN_NETLIST"]


def test_a_keep_its_own_numbers_do_not_support_fails(tmp_path):
    # the real keep row, with the hold it printed turned negative after the trial
    log = KEEP_LOG.replace("hold=0.3183198239541504->0.3183198239541504",
                           "hold=0.3183198239541504->-0.0100000000000000")
    assert log != KEEP_LOG
    rc, doc = _run(_project(tmp_path, log, NL_KEPT))
    assert rc == 1
    assert doc["findings"] == [{"code": "RETAP_KEEP_UNSUPPORTED",
                                "inst": "u_core/_1753_", "rule": "HOLD_REGRESSED"}]


def test_a_remastered_or_added_cell_is_not_a_retap(tmp_path):
    nl = NL_KEPT.replace("gf_dffq_1 \\u_core/_1683_", "gf_dffq_2 \\u_core/_1683_")
    rc, doc = _run(_project(tmp_path, KEEP_LOG, nl))
    assert rc == 1
    assert any("remastered u_core/_1683_" in str(f.get("change")) for f in doc["findings"])


def test_a_log_without_the_retap_grammar_is_not_measured(tmp_path):
    rc, doc = _run(_project(tmp_path, "Reading OpenROAD database...\n", NL_IN))
    assert rc == 2 and doc["verdict"] == "NOT_MEASURED"
    assert "VIC_RETAP" in doc["reason"]


def test_a_receipt_without_the_retap_step_is_not_measured(tmp_path):
    project = _project(tmp_path, KEEP_LOG, NL_KEPT)
    (project / "reports/phase3/librelane_cts_hold_handoff.json").write_text(
        json.dumps({"chain": {}}))
    rc, doc = _run(project)
    assert rc == 2 and doc["verdict"] == "NOT_MEASURED"


def test_the_redecision_mirrors_the_steps_own_policy():
    policy = (PLUGIN / "external_capture_retap_policy.tcl").read_text()
    for rule in ("LOCAL_SETUP_NOT_IMPROVED", "DESIGN_SETUP_REGRESSED", "HOLD_REGRESSED"):
        assert rule in policy
    base = {"local": (-1.0, 0.5), "setup": (-1.0, -1.0), "tns": (-5.0, -5.0),
            "hold": (0.2, 0.2)}
    assert RA.redecide(dict(base)) is None
    assert RA.redecide(dict(base, local=(-1.0, -1.0))) == "LOCAL_SETUP_NOT_IMPROVED"
    assert RA.redecide(dict(base, setup=(-1.0, -1.1))) == "DESIGN_SETUP_REGRESSED"
    assert RA.redecide(dict(base, hold=(0.2, -0.01))) == "HOLD_REGRESSED"
    # an already-negative hold may stay negative if it does not get worse
    assert RA.redecide(dict(base, hold=(-0.3, -0.3))) is None


def test_step_19_gates_on_the_retap_audit_when_the_chain_ran():
    flow = yaml.safe_load((PROGRAMS.parent / "flow/phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s["id"]) == "19")
    assert "retap_audit_check" in step["programs"]
    clauses = [c.get("optional_program_exit_zero") for c in step["gate"]["all_of"]]
    audit = [c for c in clauses if c and c["command"].startswith("retap_audit_check ")]
    assert audit and audit[0]["condition_files_exist"] == [
        "reports/phase3/librelane_cts_hold_handoff.json"]
