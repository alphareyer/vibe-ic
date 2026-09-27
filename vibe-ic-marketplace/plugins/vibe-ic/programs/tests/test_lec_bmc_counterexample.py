"""An unclosed LEC searches for a counterexample from reset (FX_LEC_BMC_CEX).

THE DEFECT. `lec_run.build_report` wrote `"non_equivalent_points": 0` as a
constant and no rung of the equiv ladder prints a counterexample, so a LEC
that stopped with unproven points could not be told apart from a real
sequential mismatch. When the ladder leaves points unproven, lec_run now runs
a bounded model check FROM RESET on the port miter of the same gold/gate
pairing, and the count is the miter outputs it finds differing.

The artefacts are real: yosys 0.69+ in vibeic-eda 0.3.83, on the calibration
pair in `programs/calibration/cal_bmc_*` (see the `lec_run::parse_bmc_log`
entry in instrument_calibration). Only the in-container tool's file writes
are replayed on a host without yosys; the end-to-end test runs yosys itself.
"""
import glob
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

import lec_run
from not_verified_tier import skip_not_verified

CAL = Path(lec_run.__file__).resolve().parent / "calibration"
CEX_LOG = CAL / "lec_bmc_cex_positive.log"
NONE_LOG = CAL / "lec_bmc_none_negative.log"
XDC_LOG = CAL / "lec_bmc_xdc_negative.log"
PARTIAL_LOG = CAL / "lec_bmc_deadline_partial.log"
RESET = {"resets": [{"port": "rst", "polarity": "active_high",
                     "asserted": 1}],
         "clock": "clk", "source": "test"}
L8 = {"clock_and_reset_waveform": {
    "clocks": [{"name": "clk"}],
    "resets": [{"name": "rst", "polarity": "active_high",
                "sync": "synchronous"}]}}
UNCLOSED = {"proven": 3, "unproven": 2, "total": 5, "equivalent": False,
            "verdict": "INCONCLUSIVE", "sat_model_unsupported_cells": [],
            "unproven_cells": ["\\hit"], "verdict_explanation": "x"}


def _replay(monkeypatch, log: Path, rc: int, oom=(0, 0)):
    """The container run, reduced to what the tool writes: its log, at the
    path the command names (`-l`, also handed to the supervisor), and the
    container's OOM counter before and after."""
    seen = []
    counts = iter(oom)
    monkeypatch.setattr(lec_run, "probe_cgroup_memory", lambda c, *a: {
        "oom_kills": next(counts), "memory_max_bytes": 4 << 30})

    def fake(container, cmd, timeout=120, *, marker=None, log_path=None, **_kw):
        seen.append({"cmd": cmd, "timeout": timeout, "marker": marker})
        assert str(Path(log_path).resolve()) in cmd
        shutil.copyfile(log, log_path)
        return lec_run.subprocess.CompletedProcess(cmd, rc, "", "")
    monkeypatch.setattr(lec_run, "_docker", fake)
    return seen


# ── red on main ──────────────────────────────────────────────────────────────

def test_a_planted_mismatch_is_a_counterexample_with_its_output_and_cycle():
    got = lec_run.parse_bmc_log(CEX_LOG.read_text(), 16)
    assert got["result"] == lec_run.BMC_COUNTEREXAMPLE, got
    # Predicted from the edit alone: reset in step 1, en in step 2, `hit`
    # differs in step 3 (2 cycles after reset) and q never does.
    assert got["counterexample"]["cycle"] == 3
    assert got["counterexample"]["cycles_after_reset"] == 2
    assert got["counterexample"]["differing_outputs"] == ["hit"]


def test_a_synthesised_dont_care_is_not_a_counterexample():
    """The RTL assigns 1'bx for sel == 2'b11 and the gate chose 1 there:
    equivalent under Verilog x semantics, so no model within the bound."""
    got = lec_run.parse_bmc_log(XDC_LOG.read_text(), 16)
    assert got["result"] == lec_run.BMC_NONE_WITHIN_BOUND, got
    assert got["depth_reached"] == 16


def test_the_search_is_x_aware():
    ys = lec_run.bmc_script("P\n", RESET, [1], "t.vcd")
    assert "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate" in ys
    assert "sat -verify -enable_undef -set-def-inputs -seq 2" in ys


def test_a_gold_x_bit_is_not_a_differing_output():
    log = (f"{lec_run._BMC_DEPTH_MARK} 1\n"
           "SAT proof finished - model found: FAIL!\n"
           "     2 \\gate_y        1   1   1\n"
           "     2 \\gold_y        -   -   x\n"
           "     2 \\gate_z        1   1   1\n"
           "     2 \\gold_z        0   0   0\n"
           "     2 \\trigger       1   1   1\n")
    got = lec_run.parse_bmc_log(log, 1)
    assert got["counterexample"]["differing_outputs"] == ["z"]


def test_non_equivalent_points_come_from_the_search_not_a_constant(
        tmp_path, monkeypatch):
    _replay(monkeypatch, CEX_LOG, 1)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=16)
    report = lec_run.build_report(UNCLOSED, "cal_bmc", "netlist.v", None,
                                  bmc=bmc)
    assert report["non_equivalent_points"] == 1
    assert report["bmc"]["result"] == "COUNTEREXAMPLE"
    assert report["bmc"]["counterexample"]["differing_outputs"] == ["hit"]
    # Evidence only: the verdict words are the ladder's, untouched.
    assert report["verdict"] == "INCONCLUSIVE"


def test_a_search_to_its_bound_that_finds_nothing_is_none_within_bound(
        tmp_path, monkeypatch):
    _replay(monkeypatch, NONE_LOG, 0)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=16)
    assert bmc["result"] == "NONE_WITHIN_BOUND", bmc
    assert bmc["depth_reached"] == bmc["depth_target"] == 16
    assert lec_run.bmc_non_equivalent_points(bmc) == 0


def test_a_deadline_stop_is_not_run_with_the_depth_it_reached(
        tmp_path, monkeypatch):
    """A real in-container `timeout` kill (rc 124) mid-rung: the completed
    depth is kept as a measured bound and the result is NOT_RUN, never
    NONE_WITHIN_BOUND."""
    seen = _replay(monkeypatch, PARTIAL_LOG, 124)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=4, depth_target=65536)
    # The deadline is in the command (an in-container `timeout`), and the
    # container-tree supervisor is asked for, not the host-client monitor.
    assert "timeout" in seen[0]["cmd"] and seen[0]["timeout"] == 4
    assert seen[0]["marker"]
    assert bmc["result"] == "NOT_RUN", bmc
    assert bmc["depth_reached"] == 64
    assert "4s deadline" in bmc["reason"] and "depth 64 of 65536" in bmc["reason"]
    assert lec_run.bmc_non_equivalent_points(bmc) is None


def test_a_search_that_stopped_short_of_its_target_is_not_run(tmp_path,
                                                                monkeypatch):
    _replay(monkeypatch, NONE_LOG, 0)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=64)
    assert bmc["result"] == "NOT_RUN", bmc
    assert bmc["depth_reached"] == 16


def test_a_crash_with_no_log_is_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(lec_run, "probe_cgroup_memory", lambda c, *a: {
        "oom_kills": 0, "memory_max_bytes": None})
    monkeypatch.setattr(lec_run, "_docker", lambda *a, **k:
                        lec_run.subprocess.CompletedProcess("x", 1, "", ""))
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=16)
    assert bmc["result"] == "NOT_RUN" and "no log" in bmc["reason"]


@pytest.mark.parametrize("rc, oom, deadline, needle", [
    (137, (3, 4), 900, "OOM killer stopped the search"),
    (137, (None, None), 900, "OOM counter could not be read"),
    (137, (5, 5), 900, "before the 900s deadline and with no OOM kill counted"),
    (137, (5, 5), 4, "4s deadline stopped the search"),
    (124, (5, 5), 900, "900s deadline stopped the search"),
])
def test_a_killed_search_names_what_killed_it(tmp_path, monkeypatch, rc, oom,
                                             deadline, needle):
    """rc 137 is a SIGKILL from `timeout --kill-after` OR the cgroup OOM
    killer; the container's oom_kill counter across the run tells which."""
    _replay(monkeypatch, PARTIAL_LOG, rc, oom=oom)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=deadline, depth_target=65536)
    assert bmc["result"] == "NOT_RUN", bmc
    assert needle in bmc["reason"], bmc["reason"]
    if oom[0] is not None:
        assert bmc["oom_kill_delta"] == oom[1] - oom[0]


def test_the_script_asserts_the_declared_reset_then_compares(tmp_path):
    ys = lec_run.bmc_script("PREFIX\n", RESET, [1, 2], "t.vcd")
    assert ys.startswith("PREFIX\nmiter -equiv -flatten -make_outputs "
                         "-ignore_gold_x gold gate")
    assert "-seq 2 -set-init-zero -set-at 1 in_rst 1 -prove-skip 1" in ys
    assert "-seq 3 " in ys
    low = dict(RESET, resets=[{"port": "rst_n", "polarity": "active_low",
                               "asserted": 0}])
    assert "-set-at 1 in_rst_n 0" in lec_run.bmc_script("P\n", low, [1], "t")


def test_the_not_prose_claim_for_the_bmc_reader_is_falsifiable():
    """`_NOT_PROSE["lec_run::parse_bmc_log"]`: a rung with neither result
    line is read as neither -- the search is NOT_RUN at the last completed
    depth, never a counterexample and never 'none within bound'."""
    cex = CEX_LOG.read_text().replace(
        "SAT proof finished - model found: FAIL!", "")
    got = lec_run.parse_bmc_log(cex, 16)
    assert got["result"] == "NOT_RUN" and got["depth_reached"] == 1, got
    none = NONE_LOG.read_text().replace(
        "SAT proof finished - no model found: SUCCESS!", "")
    got = lec_run.parse_bmc_log(none, 16)
    assert got["result"] == "NOT_RUN" and got["depth_reached"] == 0, got


# ── the search is its own run: it never spends or re-arms the step budget ────

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _budget_project():
    root = Path(tempfile.mkdtemp(prefix="lecbmcb_"))
    (root / "phase2/stage1/rtl").mkdir(parents=True)
    (root / "phase2/stage2/synth").mkdir(parents=True)
    (root / "phase2/stage1/rtl/dut.v").write_text(
        "module dut(input clk, input rst, input a, output reg y);\n"
        "  always @(posedge clk) y <= rst ? 1'b0 : a;\nendmodule\n")
    (root / "phase2/stage2/synth/netlist.v").write_text(
        "module dut(clk, rst, a, y);\n  input clk;\n  input rst;\n"
        "  input a;\n  output y;\n"
        "  cell_dff _0_ (.CLK(clk), .D(a), .Q(y));\nendmodule\n")
    _l8(root, L8)
    return root


def _drive_with_clock(monkeypatch, bmc_seconds):
    from test_issue2194_ladder_runs_one_rung_per_process import _FakeYosys
    clock = _Clock()
    real_budget = lec_run.StepBudget
    monkeypatch.setattr(lec_run, "StepBudget", lambda total, **kw:
                        real_budget(total, clock=clock, **kw))
    fake = _FakeYosys()

    def ladder(container, ys, *a, **k):   # five legs end at 6500 s of 7200 s
        clock.t += 1300
        ok, raw = fake(container, ys, *a, **k)
        if "equiv_induct -seq 64" in Path(ys).read_text():
            # The deepest rung proves nothing: a flat induction wall, so the
            # ladder ends INCONCLUSIVE with points unproven (the R-82 case).
            raw = raw.replace(
                "Executing EQUIV_INDUCT pass.",
                "equiv_induct: Proving $equiv cells in module equiv (-seq 64)."
                "\nProved 0 previously unproven $equiv cells.")
        return ok, raw

    def search(container, cmd, timeout=120, *, marker=None, log_path=None, **_k):
        clock.t += bmc_seconds       # the search's own run: up to its deadline
        shutil.copyfile(PARTIAL_LOG, log_path)
        return lec_run.subprocess.CompletedProcess(cmd, 124, "", "")

    for name, value in (("_container_available", lambda c: True),
                        ("_container_file_exists", lambda c, p: True),
                        ("_container_dir_writable", lambda c, p: (True, "")),
                        ("_container_file_sha256", lambda c, p: "0" * 64),
                        ("_yosys_version", lambda c: "Yosys 0.69+ fake"),
                        ("_container_image_digest", lambda c: "sha256:" + "1" * 64),
                        ("run_yosys_equiv", ladder), ("_docker", search),
                        ("probe_cgroup_memory", lambda c, *a: {
                            "oom_kills": 0, "memory_max_bytes": None})):
        monkeypatch.setattr(lec_run, name, value)
    root = _budget_project()
    try:
        lec_run.main([str(root), "--top", "dut", "--container", "fake",
                      "--liberty", "/pdk/x.lib", "--timeout", "7200"])
        return json.loads((root / "reports/lec.json").read_text())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_search_time_is_not_the_steps_budget(monkeypatch):
    """The ladder finishes INCONCLUSIVE at 6500 s of a 7200 s budget; the
    search then runs 895 s to its own deadline. The step did NOT exhaust its
    budget, and the step-13 disposition is what the ladder alone earns."""
    import design_one_shot_runner as dosr
    report = _drive_with_clock(monkeypatch, bmc_seconds=895)
    assert report["verdict"] == "INCONCLUSIVE" and report["unproven_points"] > 0
    assert report["bmc"]["result"] == "NOT_RUN"
    assert "deadline" in report["bmc"]["reason"]
    assert report["step_budget_exhausted"] is False
    assert report["step_elapsed_sec"] == 6500
    alone = dict(report, bmc=None, non_equivalent_points=0)
    assert (dosr.lec_inconclusive_disposition(report)[0]
            == dosr.lec_inconclusive_disposition(alone)[0] == "FAIL")


def test_no_search_starts_on_a_stopped_bounded_or_spent_step(tmp_path):
    clock = _Clock()
    budget = lec_run.StepBudget(100, clock=clock)
    budget.record("verilog", "", 100, 0.0, True, False)
    assert lec_run.bmc_step_stop_reason(budget, False, False) == ""
    assert "completed-rung policy" in lec_run.bmc_step_stop_reason(budget, False, True)
    assert "was stopped" in lec_run.bmc_step_stop_reason(budget, True, False)
    clock.t = 99
    assert "step budget is spent" in lec_run.bmc_step_stop_reason(budget, False, False)
    bmc = lec_run._bmc_after_ladder(
        UNCLOSED, tmp_path, "c", "g.v", "t", tmp_path, None,
        lambda: pytest.fail("no search is launched on a spent step"),
        step_stop="the step budget is spent")
    assert bmc["result"] == "NOT_RUN" and bmc["reason"] == "the step budget is spent"


# ── the runner's sentence says what the search found (text only) ─────────────

def test_the_runner_sentence_reads_the_search_and_its_verdict_does_not():
    import design_one_shot_runner as dosr
    base = {"verdict": "INCONCLUSIVE", "compared_points": 3,
            "unproven_points": 2, "miter_points": 5,
            "non_equivalent_points": 0, "budget_exhausted": False}
    cex = dict(base, non_equivalent_points=1, bmc={
        "result": "COUNTEREXAMPLE", "counterexample": {
            "cycles_after_reset": 2, "differing_outputs": ["hit"],
            "trace_path": "reports/lec_bmc_cex.vcd"}})
    none = dict(base, bmc={"result": "NONE_WITHIN_BOUND", "depth_reached": 16})
    status, why = dosr.lec_inconclusive_disposition(cex)
    assert status == dosr.lec_inconclusive_disposition(base)[0] == "FAIL"
    assert "FOUND a counterexample: output(s) hit differ 2 cycle(s)" in why
    assert "no counterexample was recorded" not in why
    assert "within 16 cycle(s)" in dosr.lec_inconclusive_disposition(none)[1]
    assert "no counterexample was recorded" in dosr.lec_inconclusive_disposition(base)[1]


# ── the reset comes from the declaration, or the search does not run ─────────

def _l8(tmp_path, doc):
    d = tmp_path / "phase1" / "generated_docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "L8_TIMING_WAVEFORM.json").write_text(json.dumps(doc))
    return tmp_path


def test_the_declared_reset_and_polarity_are_read(tmp_path):
    reset, why = lec_run.bmc_reset_from_declaration(_l8(tmp_path, L8),
                                                    ["clk", "rst", "q"])
    assert why == "" and reset["resets"] == [
        {"port": "rst", "polarity": "active_high", "asserted": 1}]


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.pop("clock_and_reset_waveform"), "no clock_and_reset_waveform"),
    (lambda d: d["clock_and_reset_waveform"].update(resets=[]), "declares no reset"),
    (lambda d: d["clock_and_reset_waveform"]["resets"][0].update(polarity="?"),
     "not active_high/active_low"),
    (lambda d: d["clock_and_reset_waveform"].update(
        clocks=[{"name": "a"}, {"name": "b"}]), "2 clock(s)"),
    (lambda d: d["clock_and_reset_waveform"]["resets"][0].update(name="nrst"),
     "not a port of the compared top"),
])
def test_an_underivable_reset_is_named_not_guessed(tmp_path, mutate, needle):
    doc = json.loads(json.dumps(L8))
    mutate(doc)
    reset, why = lec_run.bmc_reset_from_declaration(_l8(tmp_path, doc),
                                                    ["clk", "rst", "q"])
    assert reset is None and needle in why, why


def test_no_declaration_file_is_named(tmp_path):
    reset, why = lec_run.bmc_reset_from_declaration(tmp_path, ["rst"])
    assert reset is None and "no readable reset declaration" in why


def test_a_proved_ladder_searches_nothing():
    bmc = lec_run._bmc_after_ladder(
        dict(UNCLOSED, unproven=0, verdict="PASS", equivalent=True),
        Path("/nonexistent"), "c", "g.v", "t", Path("."), None,
        lambda: pytest.fail("no script is built for a proved ladder"))
    assert bmc["result"] == "NOT_RUN" and "proved every point" in bmc["reason"]


# ── end to end, the real tool ────────────────────────────────────────────────

def _liberty():
    root = os.environ.get("PDK_ROOT") or "/foss/pdks"
    hits = sorted(glob.glob(f"{root}/**/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib",
                            recursive=True))
    return hits[0] if hits else None


@pytest.mark.parametrize("rtl, top, gate, expect", [
    ("cal_bmc_rtl.v", "cal_bmc", "cal_bmc_gate_planted.v", 1),
    ("cal_bmc_rtl.v", "cal_bmc", "cal_bmc_gate.v", 0),
    ("cal_bmc_xdc_rtl.v", "cal_bmc_xdc", "cal_bmc_xdc_gate.v", 0)])
def test_lec_run_end_to_end_on_the_calibration_pair(rtl, top, gate, expect):
    lib = _liberty()
    if shutil.which("yosys") is None or lib is None:
        skip_not_verified(
            "yosys or the gf180mcu standard-cell Liberty is not on this host",
            "tools/ci/run_suite_in_eda_image.sh -- "
            "programs/tests/test_lec_bmc_counterexample.py")
    root = Path(tempfile.mkdtemp(prefix="lecbmc_"))
    try:
        (root / "phase2/stage1/rtl").mkdir(parents=True)
        (root / "phase2/stage2/synth").mkdir(parents=True)
        shutil.copy(CAL / rtl, root / "phase2/stage1/rtl" / rtl)
        shutil.copy(CAL / gate, root / "phase2/stage2/synth/netlist.v")
        _l8(root, L8)
        rc = lec_run.main([str(root), "--top", top, "--container", "",
                           "--liberty", lib])
        report = json.loads((root / "reports/lec.json").read_text())
        assert rc == 0
        assert report["non_equivalent_points"] == expect, report.get("bmc")
        if expect:
            assert report["unproven_points"] > 0
            assert report["bmc"]["result"] == "COUNTEREXAMPLE"
            cex = report["bmc"]["counterexample"]
            assert (cex["cycle"], cex["differing_outputs"]) == (3, ["hit"])
            assert Path(cex["trace_path"]).is_file()
        elif report["verdict"] == "PASS":
            assert report["bmc"]["result"] == "NOT_RUN"
        else:
            # The ladder could not close the don't-care; the search, x-aware,
            # finds no difference to its bound.
            assert report["unproven_points"] > 0
            assert report["bmc"]["result"] == "NONE_WITHIN_BOUND", report["bmc"]
    finally:
        shutil.rmtree(root, ignore_errors=True)
