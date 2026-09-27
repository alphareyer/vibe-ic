"""D7/D8 re-review (wave 4b): the chip-read record a layout chain trusts must
describe the synthesis ATTEMPT that produced the netlist, and only the record
THIS synthesis wrote may be bound.

Before the fix, `step_synth` wrote `chip_read_built.json` before any build,
with the FIRST attempt's define decision (`-DSIMULATION` when no macro is
staged), and bound whatever record was on disk to the netlist. When the
`-DSYNTHESIS` retry (`read_slang ... -DSYNTHESIS -DYOSYS`, frontend
`yosys_slang_dsynthesis`) built the netlist, every layout chain was handed
`VERILOG_DEFINES=['SIMULATION']`, labelled as synthesis's decision: the very
define whose arm made synthesis fail. And a swallowed write failure left a
previous run's record for the bind to stamp onto the new netlist.

These drive the real record writer, binder and the contract's check (no
tool run), plus a wiring pin on the one `step_synth` call site.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
for _p in (str(PROGRAMS), str(PROGRAMS.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _chip_synth_read as CSR  # noqa: E402
import librelane_contract as C  # noqa: E402

RTL = "phase2/stage1/rtl"
NETLIST = "phase2/stage2/synth/core_synth.v"
RETRY = "yosys_slang_dsynthesis"      # step_synth's -DSYNTHESIS retry frontend
FIRST = "read_verilog_v2005"


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / RTL).mkdir(parents=True)
    # An `ifdef SIMULATION arm with a DV-only construct: the retry's case.
    (p / RTL / "core.v").write_text(
        "module core(input a, output y);\n`ifdef SIMULATION\n"
        "  initial $display(\"%0d\", $urandom);\n`endif\n"
        "  assign y = a;\nendmodule\n")
    return p


def _write(p: Path, synthesis_id: str) -> list:
    files = [p / RTL / "core.v"]
    return CSR.write_built_record(p, files, [], "core", synthesis_id=synthesis_id)


def _netlist(p: Path, text: str = "module core(); endmodule // retry\n") -> Path:
    n = p / NETLIST
    n.parent.mkdir(parents=True, exist_ok=True)
    n.write_text(text)
    return n


def _chain(p: Path):
    config = {"VERILOG_FILES": [f"dir::{RTL}/core.v"]}
    sources = {"VERILOG_FILES": "test"}
    C._check_synthesised_read(p, config, sources)
    return config, sources


def _record(p: Path) -> dict:
    return json.loads(CSR.built_record_path(p).read_text())


# ── the MAJOR: the retry-built netlist carries the retry's defines ────────

def test_a_retry_built_netlist_is_not_handed_simulation(tmp_path):
    p = _project(tmp_path)
    _write(p, "S1")
    assert _record(p)["define"]["simulation"] is True     # the first attempt
    CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="S1",
                                  frontend=RETRY)
    rec = _record(p)
    assert rec["define"]["simulation"] is False
    assert rec["define"]["synthesis"] is True
    assert rec["define"]["decided"]["simulation"] is True  # disclosed, kept
    assert rec["frontend"] == RETRY
    config, sources = _chain(p)
    assert config["VERILOG_DEFINES"] == ["SYNTHESIS"]
    assert "SIMULATION" not in config["VERILOG_DEFINES"]
    assert RETRY in sources["VERILOG_DEFINES"]


@pytest.mark.parametrize("frontend", ["read_verilog_v2005", "yosys_slang",
                                      "sv2v_verilog2005"])
def test_a_first_attempt_netlist_keeps_the_decision(tmp_path, frontend):
    """Control: every other frontend reads with the decision's own define."""
    p = _project(tmp_path)
    _write(p, "S1")
    before = _record(p)["define"]
    CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="S1",
                                  frontend=frontend)
    assert _record(p)["define"] == before
    config, _ = _chain(p)
    assert config["VERILOG_DEFINES"] == ["SIMULATION"]


def test_the_retry_makes_a_simulation_proof_stale(tmp_path):
    """The Step-5 proof read SIMULATION; a retry-built chip did not."""
    p = _project(tmp_path)
    files = [p / RTL / "core.v"]
    import _path_layout as _pl
    formal = _pl.formal_dir(p)
    formal.mkdir(parents=True, exist_ok=True)
    (formal / "results.json").write_text(json.dumps({
        "program_discharged_obligations": ["o1"],
        "chip_read": CSR.chip_read_record(files, [], "core")}))
    assert _write(p, "S1") == []                        # same read as proved
    stale = CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="S1",
                                          frontend=RETRY)
    assert stale and stale[0].startswith("define:"), stale
    assert _record(p)["stale_against_step5"] == stale
    # and the first-attempt build is still the proved chip
    _write(p, "S2")
    assert CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="S2",
                                         frontend=FIRST) == []


# ── the MINOR: only this synthesis's record binds ─────────────────────────

def test_a_previous_runs_record_is_never_bound(tmp_path):
    p = _project(tmp_path)
    _write(p, "run-1")
    with pytest.raises(CSR.BuiltRecordNotThisSynthesis):
        CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="run-2",
                                      frontend=FIRST)
    assert "netlist" not in _record(p)
    config, sources = _chain(p)
    assert "VERILOG_DEFINES" not in config
    assert "NOT_MEASURED" in sources["VERILOG_FILES"]


def test_a_discarded_record_leaves_the_chain_not_measured(tmp_path):
    p = _project(tmp_path)
    _write(p, "run-1")
    CSR.bind_built_record_netlist(p, _netlist(p), synthesis_id="run-1",
                                  frontend=FIRST)
    CSR.discard_built_record(p)                  # this run's write then failed
    CSR.discard_built_record(p)                  # idempotent
    assert not CSR.built_record_path(p).exists()
    config, sources = _chain(p)
    assert "VERILOG_DEFINES" not in config
    assert f"no {CSR.BUILT_RECORD}" in sources["VERILOG_FILES"]


# ── the one call site in step_synth ───────────────────────────────────────

def _step_synth_calls():
    tree = ast.parse((PROGRAMS / "phase3_one_shot_runner.py").read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "step_synth")
    calls = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "_csr":
            calls.setdefault(n.func.attr, []).append(n)
    return calls


def test_step_synth_discards_writes_and_binds_its_own_record():
    calls = _step_synth_calls()
    (discard,), (write,), (bind,) = (calls["discard_built_record"],
                                     calls["write_built_record"],
                                     calls["bind_built_record_netlist"])
    assert discard.lineno < write.lineno < bind.lineno
    kw = {k.arg: k.value for k in bind.keywords}
    assert isinstance(kw["frontend"], ast.Name) and kw["frontend"].id == "synth_frontend"
    wkw = {k.arg: k.value for k in write.keywords}
    assert isinstance(kw["synthesis_id"], ast.Name) and \
        isinstance(wkw["synthesis_id"], ast.Name) and \
        kw["synthesis_id"].id == wkw["synthesis_id"].id


def test_the_retry_frontend_name_is_the_runners():
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert f'synth_frontend = "{RETRY}"' in src
    assert CSR.SYNTHESIS_RETRY_FRONTEND == RETRY
