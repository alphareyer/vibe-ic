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


def _replay(monkeypatch, log: Path, rc: int):
    """The container run, reduced to what the tool writes: its log."""
    def fake(container, cmd, timeout=120, **_kw):
        target = cmd.split("-l ", 1)[1].split(" ", 1)[0].strip("'")
        shutil.copyfile(log, target)
        return lec_run.subprocess.CompletedProcess(cmd, rc, "", "")
    monkeypatch.setattr(lec_run, "_docker", fake)


# ── red on main ──────────────────────────────────────────────────────────────

def test_a_planted_mismatch_is_a_counterexample_with_its_output_and_cycle():
    got = lec_run.parse_bmc_log(CEX_LOG.read_text(), 16)
    assert got["result"] == lec_run.BMC_COUNTEREXAMPLE, got
    # Predicted from the edit alone: reset in step 1, en in step 2, `hit`
    # differs in step 3 (2 cycles after reset) and q never does.
    assert got["counterexample"]["cycle"] == 3
    assert got["counterexample"]["cycles_after_reset"] == 2
    assert got["counterexample"]["differing_outputs"] == ["hit"]


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
    _replay(monkeypatch, PARTIAL_LOG, 124)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=4, depth_target=65536)
    assert bmc["result"] == "NOT_RUN", bmc
    assert bmc["depth_reached"] == 128
    assert "4s deadline" in bmc["reason"] and "depth 128 of 65536" in bmc["reason"]
    assert lec_run.bmc_non_equivalent_points(bmc) is None


def test_a_search_that_stopped_short_of_its_target_is_not_run(tmp_path,
                                                                monkeypatch):
    _replay(monkeypatch, NONE_LOG, 0)
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=64)
    assert bmc["result"] == "NOT_RUN", bmc
    assert bmc["depth_reached"] == 16


def test_a_crash_with_no_log_is_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(lec_run, "_docker", lambda *a, **k:
                        lec_run.subprocess.CompletedProcess("x", 137, "", ""))
    bmc = lec_run.run_bmc("c", "PREFIX\n", RESET, tmp_path, None,
                          deadline_s=60, depth_target=16)
    assert bmc["result"] == "NOT_RUN"
    assert "deadline" in bmc["reason"] or "no log" in bmc["reason"]


def test_the_script_asserts_the_declared_reset_then_compares(tmp_path):
    ys = lec_run.bmc_script("PREFIX\n", RESET, [1, 2], "t.vcd")
    assert ys.startswith("PREFIX\nmiter -equiv -flatten -make_outputs gold gate")
    assert "-seq 2 -set-init-zero -set-at 1 in_rst 1 -prove-skip 1" in ys
    assert "-seq 3 " in ys
    low = dict(RESET, resets=[{"port": "rst_n", "polarity": "active_low",
                               "asserted": 0}])
    assert "-set-at 1 in_rst_n 0" in lec_run.bmc_script("P\n", low, [1], "t")


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


@pytest.mark.parametrize("gate, expect", [
    ("cal_bmc_gate_planted.v", 1), ("cal_bmc_gate.v", 0)])
def test_lec_run_end_to_end_on_the_calibration_pair(gate, expect):
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
        shutil.copy(CAL / "cal_bmc_rtl.v", root / "phase2/stage1/rtl/cal_bmc.v")
        shutil.copy(CAL / gate, root / "phase2/stage2/synth/netlist.v")
        _l8(root, L8)
        rc = lec_run.main([str(root), "--top", "cal_bmc", "--container", "",
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
        else:
            assert report["verdict"] == "PASS"
            assert report["bmc"]["result"] == "NOT_RUN"
    finally:
        shutil.rmtree(root, ignore_errors=True)
