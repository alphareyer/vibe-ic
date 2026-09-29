"""R-0929-TOOL-DEFAULT, wave 3 item 1 (audit §3.2): step 31 is the tool's
physical verification of the SHIPPED GDS.

* The LVS half extracts the stream itself (`MAGIC_EXT_USE_GDS=True`,
  `MAGIC_EXT_ABSTRACT=False`): LibreLane's default extracts the routed DEF with
  LEF abstracts, so the GDS that ships was never extracted (spm: a PASS on a
  stream whose std cells carried no pin label).
* KLayout.LVS (the PDK's declared runset, via OpenROAD.WriteCDL) is the second
  LVS arm; only its own `klayout__lvs_error__count` credits it -- the shared
  `design__lvs_error__count` it would carry from Netgen is never read as
  KLayout's.  Both arms must be clean.
* On the chip path (pad ring, IC route) step 31 defaults to the tool.
"""
import importlib
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))
contract = importlib.import_module("librelane_contract")
pv = importlib.import_module("librelane_pv_signoff")
from test_librelane_pv_signoff import _fake_tool, _folder, _half_project  # noqa: E402
from test_librelane_contract_production_defaults import _chip  # noqa: E402

_NETGEN_CLEAN = {k: 0 for k in pv.PRODUCED["Netgen.LVS"]}


def _run(tmp_path, monkeypatch, half, writes):
    chain = pv.HALVES[half]
    project, pnr, configs, pdk_root = _half_project(tmp_path, chain)
    seen = {}

    def resolve(*args, **kwargs):
        seen["overlay"] = kwargs.get("overlay") or {}
        return configs

    monkeypatch.setattr(pv, "resolve_step_configs", resolve)
    calls = _fake_tool(monkeypatch, writes)
    record = pv.run_half(project, "img", pdk_root, "procA", half, gds=pnr / "chip.gds",
                         routed_def=pnr / "chip.def", netlist=pnr / "chip_pnr.v",
                         sdc=pnr / "constraint.sdc")
    ran = [c[c.index("--id") + 1] for c in calls if "--id" in c]
    return record, seen["overlay"], ran


def test_the_lvs_half_extracts_the_shipped_gds(tmp_path, monkeypatch):
    _, overlay, _ = _run(tmp_path, monkeypatch, "lvs", {
        "Magic.SpiceExtraction": {"magic__illegal_overlap__count": 0},
        "Netgen.LVS": _NETGEN_CLEAN, "KLayout.LVS": {"klayout__lvs_error__count": 0}})
    assert overlay["MAGIC_EXT_USE_GDS"][0] is True
    assert overlay["MAGIC_EXT_ABSTRACT"][0] is False


def test_the_drc_half_is_not_given_the_extraction_knobs(tmp_path, monkeypatch):
    _, overlay, _ = _run(tmp_path, monkeypatch, "drc", {
        "Magic.DRC": {"magic__drc_error__count": 0},
        "KLayout.DRC": {"klayout__drc_error__count": 0},
        "KLayout.Density": {"klayout__density_error__count": 0}})
    assert "MAGIC_EXT_USE_GDS" not in overlay


def test_klayout_lvs_is_the_second_arm_and_both_must_be_clean(tmp_path, monkeypatch):
    record, _, ran = _run(tmp_path, monkeypatch, "lvs", {
        "Magic.SpiceExtraction": {"magic__illegal_overlap__count": 0},
        "Netgen.LVS": _NETGEN_CLEAN, "KLayout.LVS": {"klayout__lvs_error__count": 1}})
    assert ran == ["Magic.SpiceExtraction", "Netgen.LVS", "OpenROAD.WriteCDL", "KLayout.LVS"]
    assert record["verdict"] == "FAIL"
    assert record["metrics"]["klayout__lvs_error__count"]["value"] == 1


def test_a_skipped_klayout_runset_is_not_a_klayout_pass(tmp_path):
    """KLayout.LVS that ran no runset carries Netgen's shared count only."""
    folders = [
        _folder(tmp_path, "01-ext", "Magic.SpiceExtraction", {},
                {"magic__illegal_overlap__count": 0}),
        _folder(tmp_path, "02-netgen", "Netgen.LVS", {"magic__illegal_overlap__count": 0},
                dict(_NETGEN_CLEAN, magic__illegal_overlap__count=0)),
        _folder(tmp_path, "04-klayout-lvs", "KLayout.LVS",
                dict(_NETGEN_CLEAN, magic__illegal_overlap__count=0),
                dict(_NETGEN_CLEAN, magic__illegal_overlap__count=0)),
    ]
    required = tuple(s for s in pv.LVS_CHAIN if s in pv.PRODUCED)
    record = pv.judge_pv(folders, required, tmp_path / "r.json")
    assert record["verdict"] == "NOT_MEASURED"
    assert record["metrics"]["klayout__lvs_error__count"]["status"] == "NOT_MEASURED"


def test_the_chip_path_defaults_step_31_to_the_tool(tmp_path):
    project = _chip(tmp_path)
    assert contract.selected_mode(project, "31") == "librelane"


def test_a_step_outside_the_chip_flow_is_resolved_under_its_own_variables():
    """KLayout.LVS is not a Chip-flow step, so the flow's config never loads
    its PDK variables: KLAYOUT_LVS_SCRIPT came back None and the runset was
    skipped ("PDK declares no KLAYOUT_LVS_SCRIPT") although gf180mcuD's
    config.tcl declares it (measured on spm, 2026-09-29).  The resolver
    loads such a step under its own variables (the container's LibreLane is
    exercised in the lane report; here the resolution script is pinned)."""
    import inspect
    src = inspect.getsource(contract.resolve_step_configs)
    assert "step_id not in chip_steps" in src
    assert "Config.load(design, target.get_all_config_variables()" in src
