"""T91 (mig-rtlver) step 3: the CDC/RDC rules read a Yosys JSON netlist.

Review row 3 keeps the rules (no CDC engine exists; Verilator 5.053 rejects
`--cdc`) and moves their input from regex over RTL text onto the netlist
LibreLane's `Yosys.JsonHeader` writes, where a flop's clock, its reset and
whether a name is a PORT are structural facts.

Every netlist below is REAL yosys output, not hand-written JSON: vibeic-eda
0.3.77 (yosys 0.69+ 4d572059c), the Yosys.JsonHeader passes
(`hierarchy -check -top T -nokeep_prints -nokeep_asserts; rename -top T;
proc; flatten; opt_clean -purge; json`), run on the `.v` beside each `.json`
in `tests/fixtures/cdc_netlist/` and on the calibration structures in
`calibration/cdc_netlist_*.v`.

Each test drives the shipped CLI, so on a tree without the netlist front end
it answers wrongly (argparse refuses `--netlist`, or the switch is ignored and
the regex arm answers) rather than raising inside the test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
FIX = PROG / "tests" / "fixtures" / "cdc_netlist"
CAL = PROG / "calibration"


def _run(gate: str, *args: str) -> tuple[int, dict]:
    out = Path(args[0]) / f"{gate}.json"
    cp = subprocess.run([sys.executable, str(PROG / f"{gate}.py"), *args,
                         "--json", str(out)], capture_output=True, text=True)
    doc = json.loads(out.read_text()) if out.is_file() else {}
    return cp.returncode, doc


def _project(tmp: Path, name: str, rtl: Path, mode: str | None,
             netlist: Path | None) -> Path:
    pj = tmp / name
    (pj / "phase2/stage1/rtl").mkdir(parents=True)
    shutil.copy(rtl, pj / "phase2/stage1/rtl" / rtl.name)
    if mode:
        (pj / "phase3").mkdir()
        (pj / "phase3/librelane_switch.json").write_text(
            json.dumps({"steps": {"3": mode}}))
    if netlist is not None:
        dest = pj / "reports/phase2/cdc/netlist.json"
        dest.parent.mkdir(parents=True)
        shutil.copy(netlist, dest)
    return pj


def _rules(doc: dict) -> list[str]:
    return sorted({f["rule"] for f in doc.get("findings", [])
                   if f.get("severity") == "ERROR"})


def test_the_netlist_sees_a_crossing_the_regex_arm_misses(tmp_path):
    """A clk_a flop captured by a clk_b flop INSIDE a submodule.

    The regex arm excludes whatever is handed to a submodule (it cannot see
    inside); the flattened netlist can, and the capture is one flop deep.
    """
    pj = _project(tmp_path, "via_sub", FIX / "cdc_via_submodule.v", None, None)
    rc, doc = _run("clock_domain_reg_crossing_check", str(pj))
    assert (rc, _rules(doc)) == (0, [])      # the regex arm's known blind spot
    rc, doc = _run("clock_domain_reg_crossing_check", str(pj),
                   "--netlist", str(FIX / "cdc_via_submodule.json"))
    assert (rc, _rules(doc)) == (1, ["CDC_REG_NO_SYNC"])
    assert doc["summary"]["netlist_clock_domains"] == ["clk_a", "clk_b"]


def test_dual_mode_names_the_disagreement(tmp_path):
    pj = _project(tmp_path, "via_sub", FIX / "cdc_via_submodule.v", "dual",
                  FIX / "cdc_via_submodule.json")
    rc, doc = _run("clock_domain_reg_crossing_check", str(pj))
    cmp = doc["summary"]["front_end_comparison"]
    assert rc == 1
    assert (cmp["regex_verdict"], cmp["netlist_verdict"], cmp["agree"]) == \
        ("PASS", "FAIL", False)
    assert cmp["netlist_blocking_rules"] == ["CDC_REG_NO_SYNC"]


def test_a_synchronised_multi_clock_design_passes_on_the_netlist(tmp_path):
    pj = _project(tmp_path, "mc", FIX / "multi_clock_sync.v", "librelane",
                  FIX / "multi_clock_sync.json")
    rc, doc = _run("clock_domain_reg_crossing_check", str(pj))
    assert (rc, _rules(doc)) == (0, [])
    assert doc["summary"]["front_end"] == "netlist"
    assert doc["summary"]["multi_clock_modules_examined"] == 1


def test_a_suffix_named_internal_wire_is_not_a_port_on_the_netlist(tmp_path):
    """#2063 (fix 3341a0d32), kept as a regression on the netlist front end.

    `b_raw` survives `opt_clean -purge` in the logic-driven fixture (it is in
    the netlist's netnames) and is not in its ports; in the alias fixture it
    is purged altogether. Neither may be read as an async input.
    """
    for name in ("internal_raw_logic", "internal_raw_alias"):
        doc = json.loads((FIX / f"{name}.json").read_text())["modules"][name]
        assert "b_raw" not in doc["ports"]
        pj = _project(tmp_path, name, FIX / f"{name}.v", "librelane",
                      FIX / f"{name}.json")
        rc, rep = _run("cdc_async_input_check", str(pj))
        assert (rc, _rules(rep), rep["summary"]["front_end"]) == (0, [], "netlist")
    names = json.loads((FIX / "internal_raw_logic.json").read_text())
    assert "b_raw" in names["modules"]["internal_raw_logic"]["netnames"]


def test_a_real_async_port_named_raw_still_needs_a_synchroniser(tmp_path):
    """The negative control of #2063: port-ness tightens the premise only."""
    for src, js, want in (("cdc_netlist_async_raw_unsync.v", "cdc_netlist_async_positive.json",
                           (1, ["ASYNC_INPUT_NO_SYNC"])),
                          ("cdc_netlist_async_raw_sync.v", "cdc_netlist_async_negative.json",
                           (0, []))):
        pj = _project(tmp_path, js, CAL / src, None, CAL / js)
        rc, doc = _run("cdc_async_input_check", str(pj), "--front-end", "netlist")
        assert (rc, _rules(doc), doc["summary"]["front_end"]) == (*want, "netlist")


def test_reset_cycle_on_the_netlist(tmp_path):
    for js, want in (("cdc_netlist_reset_positive.json", (1, ["CIRCULAR_RESET_DEPENDENCY"])),
                     ("cdc_netlist_reset_negative.json", (0, []))):
        pj = tmp_path / js
        pj.mkdir()
        rc, doc = _run("reset_dependency_check", str(pj), "--netlist", str(CAL / js))
        assert (rc, _rules(doc)) == want


def test_a_selected_netlist_front_end_never_falls_back_to_regex(tmp_path):
    """Switch says netlist, no netlist on disk: a blocking refusal, not PASS."""
    for gate in ("cdc_async_input_check", "clock_domain_reg_crossing_check",
                 "reset_dependency_check"):
        pj = _project(tmp_path, gate, FIX / "multi_clock_sync.v", "librelane", None)
        rc, doc = _run(gate, str(pj))
        assert (rc, _rules(doc)) == (1, ["NETLIST_MISSING"]), gate


def test_no_switch_keeps_the_regex_default(tmp_path):
    pj = _project(tmp_path, "plain", FIX / "cdc_via_submodule.v", None,
                  FIX / "cdc_via_submodule.json")
    rc, doc = _run("clock_domain_reg_crossing_check", str(pj))
    assert (rc, _rules(doc)) == (0, [])
    assert "front_end" not in doc["summary"]


def test_the_front_end_runs_the_json_header_passes():
    """The netlist is the one Yosys.JsonHeader writes, pass for pass."""
    import importlib
    sys.path.insert(0, str(PROG))
    cn = importlib.import_module("_cdc_netlist")
    script = cn.yosys_script([Path("/d/a.v")], "top", Path("/d/o.json"))
    assert script.split("; ")[1:] == [
        "hierarchy -check -top top -nokeep_prints -nokeep_asserts",
        "rename -top top", "proc", "flatten", "opt_clean -purge",
        "json -o /d/o.json"]


def test_the_runner_writes_the_netlist_manifest_for_a_multi_clock_design(tmp_path, monkeypatch):
    """Step-3 call site: librelane mode reads domains from CLK pins.

    Only the EDA tool's file write is faked: `build` copies the real yosys
    JSON where yosys would have written it.
    """
    sys.path.insert(0, str(PROG))
    import design_one_shot_runner as runner
    import _cdc_netlist as cn

    def fake_build(project, rtl_files, top, image=None, docker="docker"):
        out = Path(project) / cn.NETLIST_REL
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "multi_clock_sync.json", out)
        return out

    monkeypatch.setattr(cn, "build", fake_build)
    pj = _project(tmp_path, "mc", FIX / "multi_clock_sync.v", "librelane", None)
    runner.step_emit_phase2_manifests(pj, [], "multi_clock_sync")
    rec = json.loads((pj / "reports/phase2/cdc/netlist_front_end.json").read_text())
    crossing = json.loads((pj / "reports/phase2/cdc/crossing.json").read_text())
    assert (rec["verdict"], rec["clock_domains"], rec["multi_clock_agrees"]) == \
        ("PASS", ["clk_a", "clk_b"], True)
    assert (crossing["verdict"], crossing["front_end"]) == ("SKIPPED-CONDITION", "netlist")
    assert crossing["clocks_found"] == ["clk_a", "clk_b"]
