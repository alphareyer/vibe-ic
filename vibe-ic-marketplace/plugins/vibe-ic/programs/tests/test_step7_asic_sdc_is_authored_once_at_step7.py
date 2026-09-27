"""FX_STEP7_ASIC_SDC: the ASIC SDC is authored ONCE, at step 7, and step 15
reads it.

MEASURED (lane lls W23: spm x gf180mcuD, HARDMACRO, main 76a277544): step 7
FAILed on its own because its declared output `phase2/stage2/constraints/*.sdc`
was never produced. The ASIC SDC was authored inside `step_pnr` (and a second
copy of the same chain inside `step_prelayout_signoff`), and step 7's file was
written only when the design staged an SDC.

The split, with its evidence: every line of the SDC is design intent known at
step 7 (clock, I/O delays, the design's exceptions, the fanout ladder, liberty
units / DRV limits). ONE input is PnR-time -- the supply ports the pad-ring
producer (15.5ic) proves, excluded from the signal-only DRV scope -- and that
is a NAMED derivation `step_pnr` records, not a second author.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R                       # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fx_step7_prelayout_fixtures", TESTS / "test_prelayout_signoff_before_pnr.py")
PL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PL)

TOP = "chip_top"
CONS = Path("phase2/stage2/constraints")


def _project(tmp_path: Path) -> Path:
    """A design with L8 clock data (25 ns on `clk`) and NO staged SDC."""
    proj = tmp_path / "proj"
    docs = proj / "phase1" / "generated_docs"
    docs.mkdir(parents=True)
    (docs / "L8_TIMING_WAVEFORM.json").write_text(json.dumps({
        "clock_domains": [{"name": "clk", "source_pin": "clk",
                           "role": "primary", "period_ns": 25.0}]}))
    return proj


def _pdk(monkeypatch, drv=None):
    pdk = PL._corner_pdk(monkeypatch, "/foss/pdks/x/nom.lib")
    if drv is not None:
        monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: dict(drv))
    return pdk


def _step7(proj, pdk):
    res = R.step_prelayout_signoff(proj, TOP, pdk, "some-container")
    assert res.status == "PASS", res.detail
    return res


def _record(proj):
    return json.loads((proj / CONS / R.ASIC_SDC_RECORD).read_text())


_DRV = {"max_transition_ns": 1.5, "max_capacitance_pf": 0.2}


def _pad_record(proj):
    rec = proj / "reports" / "phase3" / "io_pad_chip_top.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({
        "verdict": "WROTE",
        "power_pad_plan": {"domain_topology": "single_domain",
                           "power_net": "VDD", "ground_net": "VSS"}}))
    return rec


# --------------------------------------------------------------------------- #
# RED on main: step 7's declared output is absent after step 7
# --------------------------------------------------------------------------- #
def test_step7_writes_its_declared_sdc_when_the_design_stages_none(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    res = _step7(proj, _pdk(monkeypatch))
    sdcs = sorted((proj / CONS).glob("*.sdc"))
    assert [p.name for p in sdcs] == [f"{TOP}.asic.sdc"], sdcs
    text = sdcs[0].read_text()
    assert "-period 25" in text, text[:500]
    rec = _record(proj)
    assert rec["step"] == 7 and rec["path"] == str(CONS / f"{TOP}.asic.sdc")
    assert rec["sha256"] == R._sha256_text(text)
    assert str(proj / rec["path"]) in res.output_files
    # never laundered into "design-staged"
    assert R._resolve_staged_silicon_sdc(proj) is None
    # the pre-layout STA reads the very deck step 7 wrote
    deck = (proj / "phase3/stage3/pnr/constraint.sdc").read_text()
    assert R._sha256_text(deck) == rec["deck_sha256"]


def test_a_design_staged_sdc_keeps_its_canonical_name(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    (proj / "input/constraints").mkdir(parents=True)
    (proj / "input/constraints/clock.sdc").write_text(PL._EDGE_LLM_ACCEL_SDC)
    _step7(proj, _pdk(monkeypatch))
    rec = _record(proj)
    assert rec["design_staged"] is True
    assert rec["path"] == str(CONS / f"{TOP}.sdc")
    assert rec["staged_sdc"] == "input/constraints/clock.sdc"
    assert not (proj / CONS / f"{TOP}.asic.sdc").exists()


# --------------------------------------------------------------------------- #
# step 15 READS step 7's file; it does not author
# --------------------------------------------------------------------------- #
def test_pnr_loads_step7s_file_and_never_calls_the_author(tmp_path,
                                                         monkeypatch):
    proj = _project(tmp_path)
    pdk = _pdk(monkeypatch)
    _step7(proj, pdk)
    rec = _record(proj)

    def _no_second_author(*a, **k):
        raise AssertionError("step_pnr re-authored the SDC")
    monkeypatch.setattr(R, "_author_asic_sdc", _no_second_author)
    got = R.asic_sdc_for_pnr(proj, TOP, pdk, "some-container")
    assert got["regenerated"] is None and got["derivation"] is None
    assert got["step7_sha256"] == rec["sha256"]
    assert R._sha256_text(got["text"]) == rec["deck_sha256"]


def test_step_pnr_itself_contains_no_sdc_author():
    """The inline author is gone from `step_pnr`: it calls
    `asic_sdc_for_pnr`, and none of the authoring builders."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "step_pnr")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "asic_sdc_for_pnr" in called
    assert not called & {"_build_auto_silicon_sdc", "_ensure_staged_sdc_drv",
                         "_scale_sdc_to_liberty_units", "_author_asic_sdc"}


# --------------------------------------------------------------------------- #
# windows / old projects: regenerate through the SAME producer, and say so
# --------------------------------------------------------------------------- #
def test_an_old_project_without_step7_sdc_is_regenerated_by_its_producer(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    pdk = _pdk(monkeypatch)
    got = R.asic_sdc_for_pnr(proj, TOP, pdk, "some-container")
    assert got["regenerated"] and "no step-7 record" in got["regenerated"]
    assert (proj / CONS / f"{TOP}.asic.sdc").is_file()
    assert _record(proj)["sha256"] == got["step7_sha256"]


def test_a_step7_file_changed_after_step7_is_regenerated_not_trusted(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    pdk = _pdk(monkeypatch)
    _step7(proj, pdk)
    f = proj / CONS / f"{TOP}.asic.sdc"
    f.write_text(f.read_text() + "set_false_path -from [get_ports x]\n")
    got = R.asic_sdc_for_pnr(proj, TOP, pdk, "some-container")
    assert "changed after step 7" in got["regenerated"]
    assert "set_false_path -from [get_ports x]" not in got["text"]


# --------------------------------------------------------------------------- #
# the honest split: design intent at step 7, the pad-ring supply ports at PnR
# --------------------------------------------------------------------------- #
def test_step7_is_design_intent_even_with_a_stale_pad_record(tmp_path,
                                                             monkeypatch):
    proj = _project(tmp_path)
    _pad_record(proj)                      # a previous run's 15.5ic record
    _step7(proj, _pdk(monkeypatch, drv=_DRV))
    text = (proj / CONS / f"{TOP}.asic.sdc").read_text()
    assert "set_max_transition" in text
    assert "producer-proven supply ports excluded" not in text


def test_the_pad_ring_supply_ports_are_a_named_pnr_time_derivation(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    pdk = _pdk(monkeypatch, drv=_DRV)
    _step7(proj, pdk)
    step7_text = (proj / CONS / f"{TOP}.asic.sdc").read_text()
    rec = _pad_record(proj)
    got = R.asic_sdc_for_pnr(proj, TOP, pdk, "some-container")
    d = got["derivation"]
    assert d["name"] == R.ASIC_SDC_DERIVATION
    assert d["supply_ports"] == ["VDD", "VSS"]
    assert d["from_record"] == "reports/phase3/io_pad_chip_top.json"
    assert d["from_record_sha256"] == R._sha256_text(rec.read_text())
    assert d["base_deck_sha256"] == _record(proj)["deck_sha256"]
    assert "producer-proven supply ports excluded" in got["text"]
    assert d["deck_sha256"] == R._sha256_text(got["text"])
    # step 7's file is not rewritten by the PnR-time derivation
    assert (proj / CONS / f"{TOP}.asic.sdc").read_text() == step7_text


# --------------------------------------------------------------------------- #
# the step-7 deck never feeds itself back, and it binds PnR's cache identity
# --------------------------------------------------------------------------- #
def test_the_clock_collector_never_reads_step7s_own_deck(tmp_path):
    proj = _project(tmp_path)
    (proj / CONS).mkdir(parents=True)
    (proj / CONS / f"{TOP}.asic.sdc").write_text(
        "create_clock -name clk -period 7.0 [get_ports clk]\n")
    assert R._phase2_emitted_period_ns(proj, top=TOP) is None


def test_the_step7_record_is_part_of_pnr_identity(tmp_path):
    class _Args:
        spare_density = 0.02
        container = ""
    proj = _project(tmp_path)
    inputs, _k, _b = R._step_inputs(proj, "pnr", TOP, _Args())
    assert ("step7_asic_sdc", proj / CONS / R.ASIC_SDC_RECORD) in [
        (label, path) for label, path, _r in inputs]


# --------------------------------------------------------------------------- #
# the fanout ladder reads the run's OWN standard-cell library at step 7
# --------------------------------------------------------------------------- #
_LIB = ("/foss/pdks/ciel/gf180mcu/gf180mcuD/libs.ref/"
        "gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib")
_L9_FANOUT_TABLE = """# L9 constraints

## Synthesis Fanout Limit

| library | `MAX_FANOUT_CONSTRAINT` |
|---|---|
| `sky130_fd_sc_ls` | 5 |
| `gf180mcu_*` | 4 |
| 其他 | 工具預設 |
"""


def test_the_std_cell_library_comes_from_the_resolved_liberty(tmp_path):
    """MEASURED on spm x gf180mcuD (DIE): with no liberty, the resolver read
    the pad-ring record's IO-library paths and answered `gf180mcu_fd_io`."""
    proj = _project(tmp_path)
    rec = _pad_record(proj)
    rec.write_text(json.dumps(dict(json.loads(rec.read_text()), io_lefs=[
        "/foss/pdks/x/libs.ref/gf180mcu_fd_io/lib/gf180mcu_fd_io__tt.lib"])))
    assert R._active_std_cell_library(proj, "gf180mcuD") == "gf180mcu_fd_io"
    assert R._active_std_cell_library(proj, "gf180mcuD", _LIB) == \
        "gf180mcu_fd_sc_mcu7t5v0"


def test_step7_applies_l9s_per_library_fanout_cap(tmp_path, monkeypatch):
    """The cap the design declares for its library reaches step 7's SDC
    (before: missed at step 7, found at PnR only through the IO library)."""
    proj = _project(tmp_path)
    (proj / "input/docs").mkdir(parents=True)
    (proj / "input/docs/L9_constraints_floorplan.md").write_text(_L9_FANOUT_TABLE)
    pdk = PL._corner_pdk(monkeypatch, _LIB)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: dict(_DRV))
    _step7(proj, pdk)
    text = (proj / CONS / f"{TOP}.asic.sdc").read_text()
    assert "set_max_fanout 4 [current_design]" in text, text[-1200:]


def test_the_pnr_derivation_changes_only_the_drv_scope(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    (proj / "input/docs").mkdir(parents=True)
    (proj / "input/docs/L9_constraints_floorplan.md").write_text(_L9_FANOUT_TABLE)
    pdk = PL._corner_pdk(monkeypatch, _LIB)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: dict(_DRV))
    _step7(proj, pdk)
    base = R._read_step7_asic_sdc(proj, TOP, pdk)[0]["text"]
    _pad_record(proj)
    got = R.asic_sdc_for_pnr(proj, TOP, pdk, "some-container")
    assert got["derivation"]["applied"] is True
    removed = [l for l in base.splitlines() if l not in got["text"].splitlines()]
    assert all("[current_design]" in l or l.startswith("#") for l in removed), \
        removed
    assert "set_max_fanout 4 $_vibeic_drv_signal_in_ports" in got["text"]
    assert "set_max_fanout 10" not in got["text"]
