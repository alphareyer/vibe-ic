"""Steps 9 and 14 judge the netlist PnR routes, not the generic pre-map arm (U11).

MEASURED on spm IC run v5c (192.168.1.120, run_v5c, 2026-09-29):

  * `phase2/stage2/synth/netlist.v` -- what Step 9's clauses name -- holds
    449 yosys primitives and no standard cell: `$_NAND_` 221, `$_NOR_` 128,
    `$_DFF_P_` 65, `$_NOT_` 35 (written by a `-DSIMULATION ... abc -g cmos2`
    command with no Liberty).
  * `reports/phase2/synth_netlist.json` read PASS with `sequential_cells: 0`
    over those 65 `$_DFF_P_` -- the count was filled in only on the
    tiny-design branch.
  * PnR routed `phase2/stage2/synth/spm_synth.v` (65 `dffq_1` + mapped
    logic), named by the `synth_inputs.json` sidecar phase 3's step_synth
    writes, and produced by the `synth.log` command (dfflibmap/abc -liberty,
    hilomap). No Step-9 clause looked at it, and Step 14's yosys_* gates
    reported VACUOUS_PASS against the simulation-only command.

Each case runs the EXACT clause argv the flow declares for Step 9 / Step 14
(read from the YAML), in the project directory, as flow_compliance_check does.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parents[1]
FLOW = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
SYNTH = "phase2/stage2/synth"


def _clause(step_id: str, program: str) -> str:
    doc = yaml.safe_load(FLOW.read_text())
    step = next(s for s in doc["steps"] if str(s.get("id")) == step_id)
    for pred in step["gate"]["all_of"]:
        for key in ("program_exit_zero", "optional_program_exit_zero",
                    "advisory_program_exit_zero"):
            v = pred.get(key) if isinstance(pred, dict) else None
            cmd = v.get("command") if isinstance(v, dict) else v
            if isinstance(cmd, str) and cmd.split()[0] == program:
                return cmd
    raise AssertionError(f"step {step_id} declares no {program} clause")


def _run_clause(project: Path, cmd: str):
    argv = shlex.split(cmd)
    return subprocess.run([sys.executable, str(PROGRAMS / f"{argv[0]}.py"),
                           *argv[1:]], cwd=project, capture_output=True,
                          text=True)


def _generic(n_nand=221, n_nor=128, n_dff=65, n_not=35) -> str:
    lines = ["module spm(clk, rst, x, y, p);", "  input clk;", "  wire clk;"]
    k = 0
    for cell, n in (("$_NAND_", n_nand), ("$_NOR_", n_nor),
                    ("$_DFF_P_", n_dff), ("$_NOT_", n_not)):
        for _ in range(n):
            lines.append(f"  \\{cell} _{k}_ (.A(a), .Y(y{k}));")
            k += 1
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _mapped(n_dff=65) -> str:
    lib = "gf180mcu_fd_sc_mcu7t5v0__"
    lines = ["module spm(clk, rst, x, y, p);", "  input clk;", "  wire clk;"]
    k = 0
    for cell, n in (("dffq_1", n_dff), ("nor2_1", 50), ("nand2_1", 32),
                    ("xor2_4", 15), ("tiel", 1)):
        for _ in range(n):
            lines.append(f"  {lib}{cell} _{k}_ (.I(a), .ZN(y{k}));")
            k += 1
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


_LIB = "/pdk/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/tt.lib"
_SIM_CMD = (f"read_verilog -sv -DSIMULATION rtl/spm.v; synth -top spm -flatten; "
            f"techmap; opt; dffunmap; abc -g cmos2; write_verilog -noexpr "
            f"-nostr -noattr /p/{SYNTH}/netlist_yosys.v; stat")
_MAP_CMD = (f"read_verilog -sv -DSIMULATION rtl/spm.v; hierarchy -check -top spm; "
            f"proc; flatten; synth -top spm -flatten; dfflibmap -liberty {_LIB}; "
            f"abc -liberty {_LIB}; setundef -zero; hilomap -hicell "
            f"gf180mcu_fd_sc_mcu7t5v0__tieh Z -locell gf180mcu_fd_sc_mcu7t5v0__tiel ZN; "
            f"clean; stat -liberty {_LIB}; write_verilog -noattr /p/{SYNTH}/spm_synth.v")


def _sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _project(tmp_path: Path, *, mapped_text=None, sidecar=True,
             map_cmd=_MAP_CMD, synth_log=True, mapped_provenance=True) -> Path:
    proj = tmp_path / "spm"
    d = proj / SYNTH
    d.mkdir(parents=True)
    (d / "netlist.v").write_text(_generic())
    (d / "netlist_yosys.v").write_text(_generic())
    (d / "yosys.log").write_text(f"-- Running command `{_SIM_CMD}' --\n")
    prov = [{"timestamp": "2026-09-29T04:17:44Z", "tool": "yosys",
             "exit_code": 0, "measured": True,
             "measurement": {"schema": "mcp-eda/measurement/1",
                             "measured": True, "tool": "yosys",
                             "operation": "logic synthesis",
                             "stated_by": "runner-derived"},
             "outputs": {f"{SYNTH}/netlist.v": _sha(d / "netlist.v"),
                         f"{SYNTH}/netlist_yosys.v": _sha(d / "netlist_yosys.v")}}]
    if mapped_text is not None:
        (d / "spm_synth.v").write_text(mapped_text)
        if sidecar:
            (d / "synth_inputs.json").write_text(json.dumps(
                {"netlist": "spm_synth.v", "rtl_sha256": {}}))
        if synth_log:
            (d / "synth.log").write_text(f"-- Running command `{map_cmd}' --\n")
        if mapped_provenance:
            prov.append({"record": "artefact",
                         "produced_by": "phase3_one_shot_runner.step_synth",
                         "timestamp": "2026-09-29T04:21:39Z", "tool": "yosys",
                         "exit_code": 0, "measured": True,
                         "measurement": {"schema": "mcp-eda/measurement/1",
                                         "measured": True, "tool": "yosys",
                                         "operation": "logic synthesis",
                                         "stated_by": "runner-derived"},
                         "outputs": {f"{SYNTH}/spm_synth.v":
                                     _sha(d / "spm_synth.v")}})
    (proj / "provenance.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in prov))
    return proj


def _json_arg(cmd: str) -> str:
    argv = shlex.split(cmd)
    return argv[argv.index("--json") + 1]


# ------------------------------------------------------------------ step 9 --

def test_step9_counts_the_generic_flops_as_sequential(tmp_path):
    proj = _project(tmp_path, mapped_text=_mapped())
    cmd = _clause("9", "synth_netlist_check")
    cp = _run_clause(proj, cmd)
    rep = json.loads((proj / _json_arg(cmd)).read_text())
    assert rep["stats"]["cell_type_counts"]["$_DFF_P_"] == 65
    assert rep["stats"]["sequential_cells"] == 65, rep["stats"]
    assert cp.returncode == 0, cp.stdout[-800:]


def test_step9_audits_the_mapped_netlist_pnr_routes(tmp_path):
    proj = _project(tmp_path, mapped_text=_mapped())
    cmd = _clause("9", "synth_netlist_check")
    cp = _run_clause(proj, cmd)
    rep = json.loads((proj / _json_arg(cmd)).read_text())
    pnr = rep.get("pnr_netlist")
    assert pnr is not None, "Step 9 never looked at the netlist PnR routes"
    assert pnr["netlist"].endswith(f"{SYNTH}/spm_synth.v")
    assert pnr["sequential_cells"] == 65 and pnr["generic_only"] is False
    assert cp.returncode == 0


def test_step9_fails_a_generic_only_netlist_handed_to_pnr(tmp_path):
    proj = _project(tmp_path, mapped_text=_generic())
    cmd = _clause("9", "synth_netlist_check")
    cp = _run_clause(proj, cmd)
    rep = json.loads((proj / _json_arg(cmd)).read_text())
    assert cp.returncode == 1, rep
    assert "GENERIC_UNMAPPED_NETLIST" in {f["category"] for f in rep["findings"]}


def test_step9_finds_the_mapped_arm_without_its_sidecar(tmp_path):
    proj = _project(tmp_path, mapped_text=_generic(), sidecar=False)
    cmd = _clause("9", "synth_netlist_check")
    assert _run_clause(proj, cmd).returncode == 1


def test_phase2_only_run_is_judged_as_before(tmp_path):
    """No mapped arm yet: the generic arm alone, and it still passes."""
    proj = _project(tmp_path, mapped_text=None)
    cmd = _clause("9", "synth_netlist_check")
    cp = _run_clause(proj, cmd)
    rep = json.loads((proj / _json_arg(cmd)).read_text())
    assert cp.returncode == 0 and "pnr_netlist" not in rep


def test_a_stale_mapped_arm_does_not_block_a_phase2_rerun(tmp_path):
    """design_one_shot_runner calls the checker on the generic arm with --rtl
    mid-phase-2; a previous run's mapped arm must not refuse it on age."""
    import os
    proj = _project(tmp_path, mapped_text=_mapped())
    rtl = proj / "rtl.v"
    rtl.write_text("module spm; endmodule\n")
    nl, mp = proj / SYNTH / "netlist.v", proj / SYNTH / "spm_synth.v"
    os.utime(mp, (1000, 1000))
    os.utime(rtl, (2000, 2000))
    os.utime(nl, (3000, 3000))
    cp = subprocess.run([sys.executable, str(PROGRAMS / "synth_netlist_check.py"),
                         "--netlist", str(nl), "--rtl", str(rtl)],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout[-800:]


def test_pnr_mode_before_phase3_is_asked_before_producer(tmp_path):
    proj = _project(tmp_path, mapped_text=None)
    out = tmp_path / "r.json"
    cp = subprocess.run([sys.executable, str(PROGRAMS / "synth_netlist_check.py"),
                         "--pnr-netlist", str(proj), "--json", str(out)],
                        capture_output=True, text=True)
    assert cp.returncode == 2
    rep = json.loads(out.read_text())
    assert rep["reason_class"] == "ASKED_BEFORE_PRODUCER"
    assert rep["verdict"] == "NOT_MEASURED"


def test_step9_provenance_requires_the_mapped_netlists_record(tmp_path):
    cmd = _clause("9", "provenance_check")
    bad = _project(tmp_path / "a", mapped_text=_mapped(), mapped_provenance=False)
    assert _run_clause(bad, cmd).returncode == 1, \
        "the netlist PnR routes has no provenance record and Step 9 passed"
    good = _project(tmp_path / "b", mapped_text=_mapped())
    cp = _run_clause(good, cmd)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]


# ----------------------------------------------------------------- step 14 --

@pytest.mark.parametrize("program", ["yosys_hilomap_required_check",
                                     "yosys_script_template_check"])
def test_step14_judges_the_command_that_wrote_the_pnr_netlist(tmp_path, program):
    cmd = _clause("14", program)
    ok = _project(tmp_path / "ok", mapped_text=_mapped())
    cp = _run_clause(ok, cmd)
    assert cp.returncode == 0, cp.stdout[-600:] + cp.stderr[-600:]
    rep = json.loads((ok / _json_arg(cmd)).read_text())
    assert rep["inline_evidence"] == [f"{SYNTH}/synth.log"], rep


@pytest.mark.parametrize("program", ["yosys_hilomap_required_check",
                                     "yosys_script_template_check"])
def test_step14_refuses_a_pnr_netlist_with_no_recipe(tmp_path, program):
    """v5c's shape at audit time minus the log: only the simulation-only
    command is echoed, and it did not write the netlist PnR routes."""
    cmd = _clause("14", program)
    proj = _project(tmp_path, mapped_text=_mapped(), synth_log=False)
    cp = _run_clause(proj, cmd)
    assert cp.returncode == 1, cp.stdout[-600:] + cp.stderr[-600:]


@pytest.mark.parametrize("program", ["yosys_hilomap_required_check",
                                     "yosys_script_template_check"])
def test_step14_refuses_a_pnr_netlist_written_without_a_library(tmp_path, program):
    cmd = _clause("14", program)
    no_lib = (f"read_verilog rtl/spm.v; synth -top spm -flatten; "
              f"write_verilog -noattr /p/{SYNTH}/spm_synth.v")
    proj = _project(tmp_path, mapped_text=_mapped(), map_cmd=no_lib)
    assert _run_clause(proj, cmd).returncode == 1


def test_step14_handoff_audit_names_the_pnr_netlist(tmp_path):
    cmd = _clause("14", "yosys_script_template_check")
    proj = _project(tmp_path, mapped_text=_mapped())
    _run_clause(proj, cmd)
    rep = json.loads((proj / _json_arg(cmd)).read_text())
    assert rep["handoff_netlist_audit"]["handoff_netlist"] == f"{SYNTH}/spm_synth.v"
    assert rep["handoff_netlist_audit"]["role"] == "pnr_consumed_mapped"
