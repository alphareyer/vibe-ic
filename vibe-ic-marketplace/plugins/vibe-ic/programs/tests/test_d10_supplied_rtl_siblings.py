#!/usr/bin/env python3
"""D10: the two siblings D6 left — the #732 vendor branch, and the registry
class generators — read "supplied RTL" the way consume does.

(1) MEASURED at v1.25.64: `input/vendor_rtl/{spm.v, tb_spm.v}` made the
    rtl_gen WAIVE say "2 file(s)" and the #732 manifest publish
    `ip_list: ['spm', 'tb_spm']`, while consume staged only `spm.v`. Both now
    use `discover_provided_build_rtl` restricted to vendor_rtl: a testbench or
    an oracle segment is never IP.

(2) MEASURED on the mixed_signal_otp fixture at v1.25.64: with the design's
    own `chip_top.v` supplied, `aid_class_rtl_gen` still wrote `chip_top.sv`,
    and consume then staged `chip_top.v` beside it (two definitions of the
    top). The class generators (aid_class_half_duplex_single_wire,
    mixed_signal_otp -> aid_class_rtl_gen; data_converter ->
    data_converter_rtl_gen) now decline when the supplied RTL defines the
    design's declared top.

    What stays: a generated design that INSTANTIATES supplied IP. MEASURED on
    the same fixture: the generated `otp_mem` instantiates `otp_macro_wrapper`,
    the project supplies it, and consume's closure path stages it around the
    generated tree. That still happens, pinned below.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(TESTS))

import design_one_shot_runner as R                       # noqa: E402
import reused_ip_rtl_consume as C                        # noqa: E402
import staged_rtl_reused_ip_manifest_emit as SRM         # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(f"d10_{name}", TESTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OTP_FX = _load("test_aid_class_rtl_gen_mixed_signal_otp_skeleton")
DC_FX = _load("test_issue2197_data_converter_datapath_generator")

OTP_MACRO = ("module otp_macro_wrapper(input clk, input [6:0] addr,\n"
             "  output [7:0] rdata);\n  assign rdata = 8'h0;\nendmodule\n")


def _rtl_files(p):
    d = p / "phase2/stage1/rtl"
    return sorted(f.name for f in d.glob("*") if f.suffix in (".v", ".sv")) \
        if d.is_dir() else []


def _defs(p, module):
    d = p / "phase2/stage1/rtl"
    rx = re.compile(r"^\s*module\s+" + re.escape(module) + r"\b", re.M)
    return sum(len(rx.findall(f.read_text(errors="replace")))
               for f in d.glob("*") if f.suffix in (".v", ".sv"))


# --------------------------------------------------------------------------- #
# (1) the #732 vendor branch
# --------------------------------------------------------------------------- #
def _vendor(tmp_path, files):
    vd = tmp_path / "input/vendor_rtl"
    for rel, text in files.items():
        (vd / rel).parent.mkdir(parents=True, exist_ok=True)
        (vd / rel).write_text(text)
    return tmp_path


SPM = "module spm(input clk, output p);\n  assign p = clk;\nendmodule\n"
TB = "module tb_spm;\n  spm u ();\nendmodule\n"


def test_the_manifest_lists_no_testbench_as_ip(tmp_path):
    p = _vendor(tmp_path, {"spm.v": SPM, "tb_spm.v": TB,
                           "tb/harness.v": "module harness; endmodule\n"})
    mf = json.loads(SRM.emit_prestaged_reused_ip_manifest(p).read_text())
    assert mf["ip_list"] == ["spm"]
    assert [f.name for f in SRM.vendor_build_rtl(p)] == ["spm.v"]


def test_the_waive_counts_what_consume_stages(tmp_path):
    p = _vendor(tmp_path, {"spm.v": SPM, "tb_spm.v": TB})
    res = R.step_rtl_gen(p, "digital_cmd_driven")
    assert res.extras.get("fallback_skill") == "catalog-glue-author"
    assert "1 file(s)" in res.detail and "tb_spm" not in res.detail, \
        res.detail[:400]


def test_a_vendor_tree_of_testbenches_only_is_not_reused_ip(tmp_path):
    p = _vendor(tmp_path, {"tb_spm.v": TB})
    assert SRM.emit_prestaged_reused_ip_manifest(p) is None
    res = R.step_rtl_gen(p, "digital_cmd_driven")
    assert "source_manifest_emitted" not in (res.extras or {})


# --------------------------------------------------------------------------- #
# (2) the registry class generators
# --------------------------------------------------------------------------- #
def _otp_project(root):
    root.mkdir(parents=True, exist_ok=True)
    OTP_FX._seed_fixture(root)
    return root


def _dc_project(root):
    p = DC_FX._mk(root, DC_FX.FULL)
    (p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps({"top_module": "cic_decimator", "top_ports": []}))
    return p


CLASSES = [("aid_class_half_duplex_single_wire", _otp_project, "chip_top"),
           ("mixed_signal_otp", _otp_project, "chip_top"),
           ("data_converter", _dc_project, "cic_decimator")]


@pytest.mark.parametrize("cls,mk,top", CLASSES, ids=[c[0] for c in CLASSES])
def test_the_generator_runs_without_supplied_rtl(tmp_path, cls, mk, top):
    """The control: each class really reaches its generator here, so the
    decline below is the supplied RTL's doing."""
    p = mk(tmp_path / "p")
    res = R.step_rtl_gen(p, cls)
    assert res.status == "PASS", res.detail[:300]
    assert _defs(p, top) >= 1


@pytest.mark.parametrize("cls,mk,top", CLASSES, ids=[c[0] for c in CLASSES])
def test_a_supplied_top_declines_the_class_generator(tmp_path, cls, mk, top):
    p = mk(tmp_path / "p")
    _vendor(p, {f"{top}.v": f"module {top}(input clk, output q);\n"
                            f"  assign q = clk;\nendmodule\n",
                f"tb_{top}.v": f"module tb_{top};\n  {top} u ();\nendmodule\n"})
    res = R.step_rtl_gen(p, cls)
    assert res.status == "PASS_WITH_WAIVERS", res.detail[:300]
    assert res.extras["fallback_skill"] == "catalog-glue-author"
    assert res.extras["declined_generator"] == R._lookup_class(cls)["rtl_gen"]
    assert res.extras["declared_top"] == top
    assert res.extras["design_rtl_sample"] == [f"input/vendor_rtl/{top}.v"]
    assert _rtl_files(p) == []
    staged = C.consume_reused_ip_rtl(p)["staged"]
    assert staged == [f"{top}.v"] and _defs(p, top) == 1


def test_a_generated_design_still_wraps_supplied_ip(tmp_path):
    """The cooperation case, through consume's closure path: supplied IP that
    does not define the top leaves the generator running, and consume stages
    the IP the generated tree instantiates."""
    p = _otp_project(tmp_path / "p")
    _vendor(p, {"otp_macro_wrapper.v": OTP_MACRO})
    res = R.step_rtl_gen(p, "mixed_signal_otp")
    assert res.status == "PASS", res.detail[:300]
    gen = [f for f in (p / "phase2/stage1/rtl").glob("*")
           if f.suffix in (".v", ".sv")]
    assert "otp_macro_wrapper" in C._unresolved_module_refs(gen)
    assert "supplied_rtl_blocked_by" not in res.extras
    assert "will NOT be staged" not in res.detail
    out = C.consume_reused_ip_rtl(p)
    assert out["staged"] == ["otp_macro_wrapper.v"]
    assert "otp_macro_wrapper" in out["unresolved_module_refs"]
    assert _defs(p, "chip_top") == 1 and _defs(p, "otp_macro_wrapper") == 1


@pytest.mark.parametrize("files", [
    {"tb/chip_top.v": "module chip_top; endmodule\n"},
    {"tb_chip_top.v": "module chip_top; endmodule\n"},
], ids=["oracle-segment", "testbench-stem"])
def test_a_testbench_that_defines_the_top_is_not_the_design(tmp_path, files):
    p = _otp_project(tmp_path / "p")
    _vendor(p, files)
    assert R.step_rtl_gen(p, "mixed_signal_otp").status == "PASS"


def test_no_declared_top_never_stops_the_generator(tmp_path):
    p = _otp_project(tmp_path / "p")
    spec = p / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    doc = json.loads(spec.read_text())
    doc.pop("top_module", None)
    spec.write_text(json.dumps(doc))
    _vendor(p, {"chip_top.v": "module chip_top(input clk); endmodule\n"})
    assert R._supplied_rtl_defining_declared_top(p) == (None, [])
    assert R.step_rtl_gen(p, "mixed_signal_otp").status == "PASS"


# =========================================================================== #
# review of D6, follow-ups (a)-(d)
# =========================================================================== #
import hashlib                                                  # noqa: E402

D6 = _load("test_d6_supplied_rtl_outranks_every_generator")
ARITH = D6.ARITH


@pytest.fixture
def _session(monkeypatch):
    monkeypatch.setattr(R, "_RTL_SESSION_OWNED", False)
    monkeypatch.setattr(R, "_RTL_SESSION_PROJECT", None)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# (a) supplied VHDL never disappears silently -------------------------------
VHDL = "entity top is end entity;\narchitecture rtl of top is begin end;\n"


def test_a_vhdl_only_design_still_declines_the_rtl_spec_dispatcher(tmp_path):
    """#403 refused on *.vhd/*.vhdl before D6; D6's .v/.sv helper let the
    generator replace a VHDL design with a PASS that never named it."""
    stage = tmp_path / "phase2/stage1"
    stage.mkdir(parents=True)
    (stage / "rtl_spec.json").write_text(json.dumps(D6.TRUTH_TABLE_SPEC))
    D6._supply(tmp_path, "input/vendor_rtl", "top.vhd", VHDL)
    res = R._try_deterministic_rtl_dispatch(tmp_path, 0.0)
    assert res is not None and res.status == "NOT_APPLICABLE", res
    assert res.extras["design_rtl_sample"] == ["input/vendor_rtl/top.vhd"]
    assert _rtl_files(tmp_path) == []


def test_a_supplied_vhdl_is_named_in_the_rtl_gen_result(tmp_path, _session):
    p = D6._spm_project(tmp_path)
    D6._supply(p, "input/rtl", "spm.vhd", VHDL)
    res = R.step_rtl_gen(p, ARITH)
    assert res.extras["unconsumed_supplied_hdl"] == ["input/rtl/spm.vhd"]
    assert "input/rtl/spm.vhd" in res.detail and "VHDL" in res.detail


# (b) a deferral is recorded, and every input root gets the reused-IP hand-off
@pytest.mark.parametrize("route", ["input/rtl", "input/design_src/impl/rtl",
                                   "input/vendor_rtl"])
def test_b_a_deferral_is_recorded_and_hands_off_as_reused_ip(
        tmp_path, _session, route):
    p = D6._spm_project(tmp_path)
    D6._supply(p, route, "spm.v", D6.SUPPLIED_SPM)
    res = R.step_rtl_gen(p, ARITH)
    assert res.status == "PASS_WITH_WAIVERS", res.detail[:300]
    assert res.extras["fallback_skill"] == "catalog-glue-author", res.extras
    assert res.extras["supplied_build_rtl"] == [f"{route}/spm.v"]
    assert "serial_parallel_mul_synth" in [
        d["generator"] for d in res.extras["deferred_generators"]]
    assert "Do NOT author the design itself" in res.detail


def test_b_force_regen_overridden_by_supplied_rtl_is_said(tmp_path, _session):
    p = D6._prose_project(tmp_path, "bf", D6.BEHAVIORAL_PROSE)
    D6._supply(p, "input/rtl", "own_top.v", D6._supplied("own_top"))
    res = R.step_rtl_gen(p, ARITH, force_regen=True)
    bf = [d for d in res.extras["deferred_generators"]
          if d["generator"] == "behavioral_fsm"]
    assert bf and bf[0]["force_rtl_regen_overridden"] is True
    assert "--force-rtl-regen overridden" in res.detail


# (c) a module-less .v is not a design ---------------------------------------
DEFINES = "// spm widths\n`define SPM_BITS 8\n`define SPM_SIGNED 0\n"


def test_c_a_define_only_v_file_does_not_stop_the_generator(tmp_path, _session):
    p = D6._spm_project(tmp_path)
    D6._supply(p, "input/rtl", "spm_defines.v", DEFINES)
    res = R.step_rtl_gen(p, ARITH)
    assert res.status == "PASS", res.detail[:300]
    assert res.extras.get("deterministic_generator") == \
        "serial_parallel_mul_synth", res.extras
    assert "deferred_generators" not in res.extras
    assert C.discover_supplied_design_sources(p) == []


def test_c_a_define_only_vendor_file_is_not_ip(tmp_path):
    p = _vendor(tmp_path, {"spm.v": SPM, "spm_defines.v": DEFINES})
    assert [f.name for f in SRM.vendor_build_rtl(p)] == ["spm.v"]


# (d) a previous run's generated rtl/ does not block the supplied design ----
def test_d_a_stale_generated_tree_is_reclaimed_for_the_supplied_design(
        tmp_path, _session, monkeypatch):
    """A previous run's REGISTRY-generated tree (ledger-stamped GENERATED)
    is moved to the backup, and consume stages the supplied design."""
    p = _otp_project(tmp_path / "p")
    first = R.step_rtl_gen(p, "mixed_signal_otp")
    assert first.status == "PASS" and _defs(p, "chip_top") == 1
    monkeypatch.setattr(R, "_RTL_SESSION_OWNED", False)   # a new run
    monkeypatch.setattr(R, "_RTL_SESSION_PROJECT", None)
    own = _vendor(p, {"chip_top.v": "// the supplied design\n"
                                    "module chip_top(input clk, output q);\n"
                                    "  assign q = clk;\nendmodule\n"})
    own = p / "input/vendor_rtl/chip_top.v"
    res = R.step_rtl_gen(p, "mixed_signal_otp")
    assert res.extras["reclaimed_generated_rtl"] == {
        "backup": "rtl.pre_gen_backup", "published": True}, res.extras
    assert _rtl_files(p) == []
    assert (p / "phase2/stage1/rtl.pre_gen_backup/chip_top.sv").is_file()
    out = C.consume_reused_ip_rtl(p)
    assert out["staged"] == ["chip_top.v"]
    assert _sha(p / "phase2/stage1/rtl/chip_top.v") == _sha(own)


def test_d_an_unprovable_tree_is_kept_and_both_steps_say_so(tmp_path, _session):
    """The serial-parallel generator writes no provenance ledger, so its tree
    is `unknown` and is NEVER moved; rtl_gen and consume both say the supplied
    RTL was not staged."""
    p = D6._spm_project(tmp_path)
    R.step_rtl_gen(p, ARITH)
    assert _rtl_files(p) == ["spm.v"]
    R._RTL_SESSION_OWNED = False
    D6._supply(p, "input/rtl", "spm.v", D6.SUPPLIED_SPM)
    res = R.step_rtl_gen(p, ARITH)
    assert "reclaimed_generated_rtl" not in res.extras
    assert res.extras["supplied_rtl_blocked_by"] == {
        "rtl_files": ["spm.v"], "provenance": "unknown"}
    assert "will NOT be staged" in res.detail
    out = C.consume_reused_ip_rtl(p)
    assert out["supplied_rtl_not_staged"] == ["input/rtl/spm.v"]

    assert "already holds 1 RTL file(s)" in out["reason"]
    assert "were NOT staged" in out["reason"]
