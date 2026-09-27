#!/usr/bin/env python3
"""D3 — reference_tb hard-FAILed clean upstream RTL because STRICT Icarus
refuses a net used before its declaration, and the run halted in phase 2.

MEASURED on subservient (8HD-4, reused serv 1.4.0, 22 pure .v files):

    FAIL reference_tb  generic full-stack TB (tb_subservient_full.v) failed to
    compile against rtl/ — real structural defect. iverilog -g2012 rc=4
    stderr=...serv_state.v:111: error: Unable to bind wire/reg/memory
    `trap_pending' ... serv_state.v:223: : A symbol with that name was declared
    here. Check for declaration after use.

In the SAME run Verilator built and ran the SAME TB against the SAME 22 files.
The closure is pure .v, so the ladder's sv2v / Verilator rungs were
unreachable; rtl_repair could not edit reused IP and reported INERT.

The rule the code follows now: on a REUSED-IP project (SOURCE_MANIFEST
reused_ip:true) a default compile that Icarus refused for declaration order
(its "Check for declaration after use" hint; not a timeout, not a dispatch
failure) is retried ONCE with Icarus's documented `-gno-strict-declaration`.
Only if THAT compiles AND Icarus warns "used before declaration" is the
refusal booked as declaration order (tag `iverilog_g2012_decl_relaxed`,
disclosed in the transcript); the TB's own completion marker still decides.
Any other refusal is returned exactly as before.

What the retry must NOT do (review of the first cut): relax PLUGIN-AUTHORED
RTL (declare-after-use there is an IEEE violation rtl_repair can fix, so it
stays a FAIL), launder a timeout / dispatch failure, or accept a relaxed
success that shows no relaxed binding. And no second site-level waive: a
reused-IP refusal the relaxed compile ALSO rejects (a genuinely undeclared
name, e.g. a typo in a plugin-written wrapper) stays a FAIL at the generic
site.

Only the EDA tool's answers are faked (`_run_iverilog_stage`); the fake's
strict/relaxed behaviour is the one measured in the image's Icarus
(s20260301-570-gcf82c3ec5): strict refuses a later-declared net, relaxed
compiles it with a warning, and an undeclared name is refused by both. The
stderr strings below are copied from that Icarus (measured 2026-09-28).

chip-AGNOSTIC: synthetic RTL and the frontend-tag vocabulary only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as dosr  # noqa: E402

_TOP = "core_top"
_FLAG = "-gno-strict-declaration"
_RELAXED = "iverilog_g2012_decl_relaxed"

#: the used-before-declared shape: legal to Verilator/Yosys, refused by strict
#: Icarus, compiled by relaxed Icarus.
_RTL_LATE = (f"module {_TOP}(input clk, input reset_n, input data_in, "
             f"output data_out);\n  assign data_out = late;\n"
             f"  wire late = data_in;\nendmodule\n")

_STRICT_LATE = (
    4, "",
    f"{_TOP}.v:2: error: Unable to bind wire/reg/memory `late' in "
    f"`tb_{_TOP}_full.u_dut'\n"
    f"{_TOP}.v:3:      : A symbol with that name was declared here. "
    f"Check for declaration after use.\n1 error(s) during elaboration.\n")

#: what that Icarus prints for _RTL_LATE with the flag (rc 0, on stderr).
_RELAXED_OK = (0, "", f"{_TOP}.v:2: warning: net/variable `late` used before "
                      f"declaration.\n{_TOP}.v:3:        : the net/variable "
                      f"is declared here.\n")

#: a genuinely undeclared identifier: refused with and without the flag.
_GHOST = (2, "",
          f"{_TOP}.v:2: error: Unable to bind wire/reg/memory `ghost' in "
          f"`tb_{_TOP}_full.u_dut'\n"
          f"{_TOP}.v:2: error: Unable to elaborate r-value: ghost\n"
          f"2 error(s) during elaboration.\n")

_MISSING_MODULE = (1, "", f"{_TOP}.v:4: error: Unknown module type: "
                          f"missing_child\n1 error(s) during elaboration.\n")


def _project(root: Path, reused_ip=True, oracle_tb: bool = False) -> Path:
    gd = root / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": _TOP,
        "top_ports": [{"name": "clk", "direction": "input"},
                      {"name": "reset_n", "direction": "input"},
                      {"name": "data_in", "direction": "input"},
                      {"name": "data_out", "direction": "output"}]}))
    rtl = root / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / f"{_TOP}.v").write_text(_RTL_LATE)
    if reused_ip is not None:
        (rtl / "SOURCE_MANIFEST.json").write_text(
            json.dumps({"reused_ip": bool(reused_ip)}))
    dosr.step_full_stack_tb_gen(root, _TOP)
    if oracle_tb:
        sim = root / "phase2" / "stage1" / "sim_full_stack"
        sim.mkdir(parents=True, exist_ok=True)
        (sim / f"tb_{_TOP}_oracle.v").write_text(
            f"module tb_{_TOP}_oracle; {_TOP} u_dut(); endmodule\n")
    return root


def _fake_icarus(monkeypatch, *, strict, relaxed, vvp):
    """Answer each `_run_iverilog_stage` call BY ITS ARGV, the way the image's
    Icarus does, and record every argv. No other simulator exists."""
    monkeypatch.setattr(dosr, "_iverilog_available", lambda *a, **k: True)
    monkeypatch.setattr(dosr, "_tool_in_container", lambda *a, **k: False)
    calls = []

    def _stage(argv, run_dir, container, timeout=120, **_k):
        calls.append(list(argv))
        if argv[0] == "vvp":
            return vvp
        return relaxed if _FLAG in argv else strict

    monkeypatch.setattr(dosr, "_run_iverilog_stage", _stage)
    return calls


def _compiles(calls):
    return [c for c in calls if c[0] == "iverilog"]


def _vvps(calls):
    return [c for c in calls if c[0] == "vvp"]


# ---------------------------------------------------------------------------
# (a) the measured case
# ---------------------------------------------------------------------------
def test_declaration_order_refusal_is_not_a_structural_fail(
        tmp_path, monkeypatch):
    calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=_RELAXED_OK,
                         vvp=(0, "FULL_STACK_TB_DONE bytes=0 bits=0\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    assert sr.status == "NOT_MEASURED", (sr.status, sr.detail)
    assert "real structural defect" not in sr.detail
    assert sr.extras.get("tb_frontend") == _RELAXED, sr.extras
    assert sr.extras.get("sim_executed") is True
    # the compile vvp ran was the relaxed one, and the flag is an OPTION:
    # before the first source path, not after it.
    relaxed = [c for c in _compiles(calls) if _FLAG in c]
    assert relaxed, calls
    first_src = min(i for i, t in enumerate(relaxed[-1]) if t.endswith(".v"))
    assert relaxed[-1].index(_FLAG) < first_src, relaxed[-1]
    assert _vvps(calls), calls
    assert calls.index(relaxed[-1]) < calls.index(_vvps(calls)[0])


def test_the_relaxation_is_disclosed_in_the_transcript(tmp_path, monkeypatch):
    """A retry that nobody can see is a silent waiver. The transcript every
    site writes must name the flag AND quote the strict refusal it overrode."""
    _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=_RELAXED_OK,
                 vvp=(0, "FULL_STACK_TB_DONE bytes=0 bits=0\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    logs = [Path(e) for e in sr.output_files if str(e).endswith(".log")]
    assert logs, sr.output_files
    text = logs[0].read_text()
    assert _FLAG in text
    assert "Check for declaration after use" in text
    assert dosr._TB_FRONTEND_NAMES.get(_RELAXED, "").find(_FLAG) >= 0


# ---------------------------------------------------------------------------
# (b) a relaxed COMPILE buys nothing about FUNCTION — the run still judges
# ---------------------------------------------------------------------------
def test_relaxed_compile_still_judged_by_the_run(tmp_path, monkeypatch):
    calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=_RELAXED_OK,
                         vvp=(0, "no marker\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    assert sr.status == "FAIL", (sr.status, sr.detail)
    assert "did not reach FULL_STACK_TB_DONE" in sr.detail
    assert _vvps(calls), "the relaxed compile must be RUN, not reused"
    assert sr.extras.get("tb_frontend") == _RELAXED, sr.extras


def test_relaxed_compile_oracle_mismatch_is_still_a_functional_fail(
        tmp_path, monkeypatch):
    calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=_RELAXED_OK,
                         vvp=(0, "ORACLE_TB_DONE pass=0/1\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path, oracle_tb=True), _TOP,
                                "processor_cpu")
    assert sr.status == "FAIL", (sr.status, sr.detail)
    assert "functional mismatch" in sr.detail
    assert _vvps(calls)
    assert sr.extras.get("tb_frontend") == _RELAXED, sr.extras


def test_relaxed_compile_oracle_pass_names_its_frontend(tmp_path, monkeypatch):
    _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=_RELAXED_OK,
                 vvp=(0, "ORACLE_TB_DONE pass=1/1\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path, oracle_tb=True), _TOP,
                                "processor_cpu")
    assert sr.status == "PASS", (sr.status, sr.detail)
    assert sr.extras.get("tb_frontend") == _RELAXED, sr.extras


# ---------------------------------------------------------------------------
# (c) TEETH — what the retry must NOT rescue
# ---------------------------------------------------------------------------
def test_genuinely_undeclared_identifier_still_fails(tmp_path, monkeypatch):
    calls = _fake_icarus(monkeypatch, strict=_GHOST, relaxed=_GHOST,
                         vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    assert sr.status == "FAIL", (sr.status, sr.detail)
    assert sr.extras.get("tb_frontend") == "iverilog_g2012", sr.extras
    assert "ghost" in sr.detail
    assert not _vvps(calls)


def test_an_icarus_without_the_flag_keeps_the_original_refusal(
        tmp_path, monkeypatch):
    """An older Icarus rejects the unknown -g option (rc 255, measured). The
    step must report the STRICT refusal, not the option error."""
    _fake_icarus(monkeypatch, strict=_STRICT_LATE,
                 relaxed=(255, "", "Unknown/Unsupported Language generation "
                                   "no-strict-declaration\n"),
                 vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    assert sr.status == "FAIL", (sr.status, sr.detail)
    assert "Check for declaration after use" in sr.detail
    assert "Unsupported Language generation" not in sr.detail


def test_an_absent_compiler_is_not_retried(tmp_path, monkeypatch):
    """#1394: a compiler that was not FOUND did not refuse anything."""
    absent = (127, "", "COMMAND_NOT_FOUND: 'iverilog'")
    calls = _fake_icarus(monkeypatch, strict=absent, relaxed=_RELAXED_OK,
                         vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    assert sr.status != "FAIL", (sr.status, sr.detail)
    assert not [c for c in calls if _FLAG in c], calls
    assert sr.extras.get("sim_executed") is False


# ---------------------------------------------------------------------------
# (d) no site-level waive beyond the rung: what relaxed Icarus also refuses
# ---------------------------------------------------------------------------
def _site_status(tmp_path, monkeypatch, *, reused_ip, oracle_tb, refusal):
    _fake_icarus(monkeypatch, strict=refusal, relaxed=refusal,
                 vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    return dosr.step_reference_tb(
        _project(tmp_path, reused_ip=reused_ip, oracle_tb=oracle_tb),
        _TOP, "processor_cpu")


def test_reused_ip_refusal_the_relaxed_compile_also_rejects_stays_a_fail(
        tmp_path, monkeypatch):
    """A genuinely undeclared name on a reused-IP project -- e.g. a typo in
    the plugin-written chip_top wrapper -- is refused by strict AND relaxed
    Icarus. The generic site keeps main's FAIL ("real structural defect"),
    so rtl_repair can edit the wrapper; it is NOT booked as a tool-subset
    limit. (The oracle site's landed GAP-E2E-5 waive is unchanged here.)"""
    generic = _site_status(tmp_path / "g", monkeypatch, reused_ip=True,
                           oracle_tb=False, refusal=_GHOST)
    assert generic.status == "FAIL", (generic.status, generic.detail)
    assert "ghost" in generic.detail
    assert not generic.extras.get("sv_subset_waived"), generic.extras
    assert generic.extras.get("tb_frontend") == "iverilog_g2012"


def test_authored_rtl_refusal_is_a_defect_at_both_sites(tmp_path, monkeypatch):
    """§4.05 NO-LEAK: the waive belongs to upstream-validated reused IP only."""
    for label, oracle_tb in (("o", True), ("g", False)):
        sr = _site_status(tmp_path / label, monkeypatch, reused_ip=False,
                          oracle_tb=oracle_tb, refusal=_GHOST)
        assert sr.status == "FAIL", (label, sr.status, sr.detail)


def test_missing_module_on_reused_ip_is_a_defect_at_both_sites(
        tmp_path, monkeypatch):
    """A missing module is not a language-subset signature: FAIL everywhere."""
    for label, oracle_tb in (("o", True), ("g", False)):
        sr = _site_status(tmp_path / label, monkeypatch, reused_ip=True,
                          oracle_tb=oracle_tb, refusal=_MISSING_MODULE)
        assert sr.status == "FAIL", (label, sr.status, sr.detail)


# ---------------------------------------------------------------------------
# (e) the relaxation is for REUSED IP only
# ---------------------------------------------------------------------------
def test_authored_rtl_declare_after_use_is_not_relaxed(tmp_path, monkeypatch):
    """Plugin-authored RTL (no manifest, or reused_ip:false) that uses a name
    before declaring it is an IEEE 1800 violation commercial simulators
    refuse. The strict compile is the one place the flow catches it: FAIL,
    to rtl_repair, and the relaxed flag is never even tried."""
    for label, manifest in (("none", None), ("false", False)):
        for site, oracle_tb in (("o", True), ("g", False)):
            calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE,
                                 relaxed=_RELAXED_OK,
                                 vvp=(0, "ORACLE_TB_DONE pass=1/1\n"
                                         "FULL_STACK_TB_DONE\n", ""))
            sr = dosr.step_reference_tb(
                _project(tmp_path / f"{label}_{site}", reused_ip=manifest,
                         oracle_tb=oracle_tb), _TOP, "processor_cpu")
            assert sr.status == "FAIL", (label, site, sr.status, sr.detail)
            assert "Check for declaration after use" in sr.detail
            assert not [c for c in calls if _FLAG in c], (label, site, calls)
            assert sr.extras.get("tb_frontend") != _RELAXED, sr.extras
            assert not _vvps(calls), (label, site, calls)


def test_reused_ip_declare_after_use_is_relaxed_at_both_sites(
        tmp_path, monkeypatch):
    """The other direction: the same RTL on reused IP compiles through the
    rung at the oracle AND the generic site."""
    for site, oracle_tb in (("o", True), ("g", False)):
        calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE,
                             relaxed=_RELAXED_OK,
                             vvp=(0, "ORACLE_TB_DONE pass=1/1\n"
                                     "FULL_STACK_TB_DONE bytes=0 bits=0\n",
                                  ""))
        sr = dosr.step_reference_tb(
            _project(tmp_path / site, reused_ip=True, oracle_tb=oracle_tb),
            _TOP, "processor_cpu")
        assert sr.status != "FAIL", (site, sr.status, sr.detail)
        assert sr.extras.get("tb_frontend") == _RELAXED, (site, sr.extras)
        assert [c for c in calls if _FLAG in c], (site, calls)


# ---------------------------------------------------------------------------
# (f) a retry is accepted only on Icarus's own evidence
# ---------------------------------------------------------------------------
def _assert_original_refusal_kept(sr, calls, *, retried):
    assert sr.status == "FAIL", (sr.status, sr.detail)
    assert sr.extras.get("tb_frontend") != _RELAXED, sr.extras
    assert bool([c for c in calls if _FLAG in c]) is retried, calls
    assert not _vvps(calls), calls


def test_a_strict_timeout_is_not_retried(tmp_path, monkeypatch):
    """One run is the verdict: a deadline kill (`_run` rc 124) is not a
    declaration-order refusal, even if the partial output it captured
    already shows the hint -- a later relaxed success must not launder it."""
    timeout = (124, "", _STRICT_LATE[2] + "TIMEOUT after 120s\n")
    calls = _fake_icarus(monkeypatch, strict=timeout, relaxed=_RELAXED_OK,
                         vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    _assert_original_refusal_kept(sr, calls, retried=False)


def test_a_dispatch_failure_is_not_retried(tmp_path, monkeypatch):
    """A docker/daemon failure (rc 125) never ran Icarus at all."""
    for rc in (125, 126, 137, 143):
        failed = (rc, "", _STRICT_LATE[2]
                  + "docker: Error response from daemon: container is "
                    "restarting\n")
        calls = _fake_icarus(monkeypatch, strict=failed, relaxed=_RELAXED_OK,
                             vvp=(0, "FULL_STACK_TB_DONE\n", ""))
        sr = dosr.step_reference_tb(_project(tmp_path / str(rc)), _TOP,
                                    "processor_cpu")
        _assert_original_refusal_kept(sr, calls, retried=False)


def test_a_refusal_without_the_declaration_hint_is_not_retried(
        tmp_path, monkeypatch):
    """Only Icarus's declaration-after-use refusal is what the flag relaxes;
    a syntax error on reused IP is not retried with it."""
    syntax = (1, "", f"{_TOP}.v:2: syntax error\nI give up.\n")
    calls = _fake_icarus(monkeypatch, strict=syntax, relaxed=_RELAXED_OK,
                         vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    _assert_original_refusal_kept(sr, calls, retried=False)


def test_a_relaxed_success_without_the_warning_proves_nothing(
        tmp_path, monkeypatch):
    """rc 0 from the relaxed compile with NO "used before declaration"
    warning means the flag moved no binding (the strict failure was
    something else that did not recur): keep the ORIGINAL result."""
    calls = _fake_icarus(monkeypatch, strict=_STRICT_LATE, relaxed=(0, "", ""),
                         vvp=(0, "FULL_STACK_TB_DONE\n", ""))
    sr = dosr.step_reference_tb(_project(tmp_path), _TOP, "processor_cpu")
    _assert_original_refusal_kept(sr, calls, retried=True)
    assert "Check for declaration after use" in sr.detail
