"""A4 corners get progress supervision; A7 compares the rails (T131).

Two defects, both measured on image vibeic-eda 0.3.83 (ngspice-47):

  * READER. `analog_real_corner_sweep._NATIVE_MEAS_RE` demanded end-of-line
    right after the value, and ngspice prints every windowed / extremum result
    with its window on the same row (`vavg = 6.45100e-01 from= ... to= ...`,
    `vmax = 1.24119e+00 at= ...`). On the delta_sigma A3 deck 243 of 245
    results were read as ABSENT, the fork's `--json-measure` sidecar was `[]`
    and was still taken as the authoritative structured record, and A7's 10 %
    rule compared density and swing only -- no rail at all.
  * SUPERVISION. A4's fresh base and PVT corner runs need a monitor of the
    exact container's CPU and output, plus identity-bound stall reaping.

The rules now: every native row is read (calibrated on real ngspice-47
transcripts in `programs/calibration/`); an empty sidecar verifies nothing; A7
compares every row with its 10 % rule and a declared row that is genuinely
absent makes the step NOT_MEASURED, never PASS; an A4 budget is recorded while
progress is supervised, and a graded A4 record keeps its declared stop by
name. A stalled simulation is NOT_MEASURED with its own progress reason.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import analog_a7_post_layout_emit as A7
import analog_a7_post_layout_resim_check as G7
import analog_real_corner_sweep as ARS

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))
CAL = PROGRAMS / "calibration"
#: Stated here, not read off the module, so the pre-fix tree runs these tests
#: and answers wrongly instead of failing on a missing name.
EX_NOT_MEASURED = 75


def _stub_docker(monkeypatch, stdout, rc=0, seen=None):
    class _CP:
        returncode = rc

    _CP.stdout = stdout

    def fake(container, cmd, timeout=120):
        if seen is not None:
            seen["timeout"] = timeout
        return _CP()
    monkeypatch.setattr(ARS, "_docker", fake)
    monkeypatch.setattr(ARS, "_resolve_ngspice", lambda c: "ngspice")
    monkeypatch.setattr(ARS, "_supports_json_measure", lambda c, b: False)


# ── the reader ─────────────────────────────────────────────────────────────
def test_every_native_meas_row_is_read_from_real_ngspice47_output(
        monkeypatch):
    """The unedited ngspice-47 transcript of the calibration deck: a windowed,
    an extremum and a `find ... at=` result -- all three are measurements."""
    _stub_docker(monkeypatch, (CAL / "ngspice47_cal_meas_rows.log").read_text())
    ok, meas, _raw, _st = ARS._run_ngspice("c", "/x.sp", deck_text="tran 10n 30u")
    assert ok
    assert meas.get("cal_avg") == pytest.approx(0.525819)     # from= to=
    assert meas.get("cal_max") == pytest.approx(0.993311)     # at=
    assert meas.get("cal_at") == pytest.approx(0.865506)      # plain row
    assert meas.get("cal_echo") == pytest.approx(0.525819)    # echo key kept


def test_a_failed_measure_is_absent_never_a_number(monkeypatch):
    _stub_docker(monkeypatch,
                 (CAL / "ngspice47_cal_meas_rows_failed.log").read_text())
    _ok, meas, _raw, _st = ARS._run_ngspice("c", "/x.sp",
                                            deck_text="tran 10n 30u")
    assert not {"cal_avg", "cal_max", "cal_at"} & {
        k for k, v in meas.items() if v is not None}


def test_an_empty_json_sidecar_verifies_nothing():
    assert ARS.parse_json_measure_sidecar("[]") is None


# ── A7 compares the rails, and never passes over an absent row ─────────────
from test_t130_a7_budget import (  # noqa: E402
    IMAGE, TB, _project, _record, stub, _stated_identity)  # noqa: F401


def test_a7_compares_every_rail_row(stub):
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", A7_IMAGE()) == 0
    doc = json.loads((project / "phase3/analog/blk/pre_vs_post.json")
                     .read_text())
    metrics = {s["metric"] for s in doc["specs"]}
    assert {"railx_max_b", "railx_min_b", "vavg", "vmax", "density"} <= metrics


def test_a_rail_that_moves_more_than_ten_percent_fails_the_a7_gate(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_RAIL_POST", "0.40")   # pre 0.60 -> post 0.40
    project = _project(stub)
    (project / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    (project / "phase3/analog/blk/corner_results.json").write_text(json.dumps(
        {"design_content": "structure_and_geometry",
         "corners": [{"simulator_run": True}]}))
    assert A7.run(project, "blk", "vibeic-eda", A7_IMAGE()) == 0
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS / "analog_a7_post_layout_resim_check.py"),
                         str(project), "--block", "blk"],
                        capture_output=True, text=True)
    assert cp.returncode != 0, cp.stdout
    assert "A7_POSTSIM_DELTA_TOO_BIG" in cp.stdout + cp.stderr


def test_a_declared_row_absent_pre_layout_is_not_measured(stub, monkeypatch):
    monkeypatch.setenv("STUB_FAIL_MEAS", "railx_min_b")
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", A7_IMAGE()) == EX_NOT_MEASURED
    rec = _record(project)
    assert (rec["result"], rec["reason_class"], rec["rule"]) == (
        "NOT_MEASURED", "partial_population", "A7_PRE_MEASUREMENT_ABSENT")
    assert rec["absent_measurements"] == ["railx_min_b"]
    assert not (project / "phase3/analog/blk/pre_vs_post.json").exists()


@pytest.mark.parametrize("pre,post", [("0", "0"), ("0.0001", "0.0002")])
def test_rail_near_ground_uses_declared_supply_margin_not_its_pre_value(
        stub, monkeypatch, pre, post):
    monkeypatch.setenv("STUB_RAIL_PRE", pre)
    monkeypatch.setenv("STUB_RAIL_POST", post)
    # A3 drives this rail in the deck. Its actual stimulus is the comparison
    # basis, including a power-on ramp, not a guessed value from a spec row.
    tb = TB.replace("* tb\n", "* tb\nv_vdd vdd 0 pwl(0n 0 100n 1.2 1u 1.2)\n", 1)
    project = _project(stub, tb=tb)
    (project / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    (project / "phase3/analog/blk/corner_results.json").write_text(
        json.dumps({"design_content": "structure_and_geometry",
                    "corners": [{"simulator_run": True}]}))
    assert A7.run(project, "blk", "vibeic-eda", A7_IMAGE()) == 0
    doc = json.loads((project / "phase3/analog/blk/pre_vs_post.json").read_text())
    rails = [s for s in doc["specs"] if s["metric"].startswith("railx_")]
    assert rails and all(s["delta_pct"] < 0.1 for s in rails)
    assert all(s["rail_supply_v"] == pytest.approx(1.2) for s in rails)
    assert all(s["rail_reference_v"] == pytest.approx(1.2) for s in rails)
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS / "analog_a7_post_layout_resim_check.py"),
                         str(project), "--block", "blk"],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_a7_reads_the_a3_driven_supply_and_the_gate_recomputes_rail_drift():
    tb = "v_vdd vdd 0 pwl(0n 0 100n 1.2 1u 1.2)\n"
    assert A7.rail_reference_voltage(tb) == pytest.approx(1.2)
    assert A7._declared_rail_reference(tb) == pytest.approx((1.2, 1.0))
    row = A7.compare({"railx_min_n": 0.0001},
                     {"railx_min_n": 0.0002}, "rcx", rail_supply_v=1.2,
                     rail_margin_fraction=1.0)[0]
    row["delta_pct"] = 0.0  # forged field must not override measured volts
    assert G7._check_specs([row])[0] == pytest.approx([100 * 0.0001 / 1.2])


def test_a7_gate_uses_the_raw_rail_voltages_over_a_declared_delta():
    row = {"name": "railx_min_n@rcx", "metric": "railx_min_n",
           "pre_value": 0.0, "post_value": 0.24,
           "rail_reference_v": 1.2, "delta_pct": 0.0}
    assert G7._check_specs([row])[0] == pytest.approx([20.0])


def test_a_missing_pre_row_cannot_hide_a_measured_post_regression(
        stub, monkeypatch):
    monkeypatch.setenv("STUB_FAIL_MEAS", "railx_min_b")
    monkeypatch.setenv("STUB_RAIL_POST", "0.40")
    project = _project(stub)
    assert A7.run(project, "blk", "vibeic-eda", A7_IMAGE()) == 1
    rec = _record(project)
    assert rec["result"] == "REFUSED"
    assert rec["rule"] == "A7_POSTSIM_DELTA_TOO_BIG"
    assert rec["absent_measurements"] == ["railx_min_b"]
    assert any(c.get("post_log") for c in rec["corners"])


def test_the_runner_reports_the_records_own_not_measured_reason(
        tmp_path, monkeypatch):
    import _eda_pin as PIN
    import analog_one_shot_runner as R
    project = _project(tmp_path)
    (project / "phase3/analog/analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    (project / "phase3/librelane_switch.json").write_text(
        json.dumps({"steps": {"A7": "librelane"}}))
    a7_record = project / "phase3/analog/blk/a7_post_layout.json"
    real_run = R._pr.run

    class _CP:
        returncode = EX_NOT_MEASURED
        stdout = ""
        stderr = "NOT_MEASURED: analog_a7_post_layout_emit A7_PRE_MEASUREMENT_ABSENT"

    def fake_run(cmd, *a, **k):
        if any(str(x).endswith("analog_a7_post_layout_emit.py") for x in cmd):
            a7_record.write_text(json.dumps({
                "producer": "analog_a7_post_layout_emit", "schema": 1,
                "block": "blk", "step": "A7", "result": "NOT_MEASURED",
                "reason_class": "partial_population",
                "rule": "A7_PRE_MEASUREMENT_ABSENT",
                "absent_measurements": ["railx_min_b"]}))
            return _CP()
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(R._pr, "run", fake_run)
    monkeypatch.setattr(R._pin, "container_image_digest",
                        lambda c: (PIN.IMAGE_DIGEST, ""))
    res = R.step_for_block(project, {"name": "blk", "type": "ldo"},
                           "A7_post_layout_resim", None)
    assert res.status == "NOT_MEASURED"
    assert str(getattr(res.reason_class, "value", res.reason_class)) == \
        "partial_population"


def A7_IMAGE():
    return IMAGE


# ── A4: progressing runs are not killed on a wall clock ───────────────────
def test_an_a4_corner_container_has_no_raw_timeout(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(ARS, "_resolve_ngspice", lambda c: "ngspice")
    monkeypatch.setattr(ARS, "_supports_json_measure", lambda c, b: False)
    monkeypatch.setattr(ARS, "_corner_reservation", lambda c: ("1g", 1 << 30))
    monkeypatch.setattr(ARS, "_corner_image", lambda c: "img@sha256:" + "a" * 64)

    def launch(ledger, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess([], 0, stdout="vout = 1.2\n",
                                           stderr="")
    monkeypatch.setattr(ARS._aca, "launch", launch)
    ARS._run_ngspice("c", "/x.sp", deck_text="tran 5n 1000n\n",
                     corner_job={"id": "b:tt:27c", "project": tmp_path,
                                 "workdir": tmp_path})
    args = seen["simulation_args"]
    assert "timeout" not in args, args


def test_a7_no_clock_transport_uses_identity_bound_progress_supervisor(
        monkeypatch):
    import _container_exec as CE
    seen = {}

    def supervised(container, command, **kwargs):
        seen.update(container=container, command=command, kwargs=kwargs)
        return subprocess.CompletedProcess([], 0, stdout="measured", stderr="")

    monkeypatch.setattr(CE, "run_in_container_supervised", supervised)
    monkeypatch.setattr(CE, "run_in_container", lambda *a, **k: pytest.fail(
        "the no-clock A7 path used the deadline transport"))
    cp = ARS._docker("c", "ngspice -b /work/circuit.sp", timeout=0)
    assert cp.returncode == 0 and cp.stdout == "measured"
    assert seen["container"] == "c"
    assert "ngspice -b /work/circuit.sp" in seen["command"]


def test_both_a4_call_sites_use_no_clock_supervision():
    src = (PROGRAMS / "analog_real_corner_sweep.py").read_text()
    assert src.count("run_to_completion=True") >= 2
    assert 'simulation_args=["--skip", "bash", "-lc", command]' in src


def _fresh_corner_docker(tmp_path, monkeypatch, *, idle=False):
    """A Docker CLI fake: only Docker's observations and file writes are faked."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cli = bindir / "docker"
    cli.write_text('''#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
a = sys.argv[1:]
state = Path(os.environ["CORNER_STATE"])
calls = Path(os.environ["CORNER_CALLS"])
cid = "a" * 64
with calls.open("a") as f: f.write(" ".join(a[:3]) + "\\n")
if a[0] == "run":
    state.write_text(cid)
    start = time.monotonic()
    while state.exists() and time.monotonic() - start < 0.7:
        time.sleep(0.02)
    if state.exists(): state.unlink()
    print("vout = 1.2")
    sys.exit(0)
if a[0] == "inspect":
    if state.exists(): print(cid); sys.exit(0)
    print(os.environ.get("CORNER_MISSING", "No such object"), file=sys.stderr)
    sys.exit(1)
if a[0] == "stats":
    print("0.00%" if os.environ.get("CORNER_IDLE") else "95.00%")
    sys.exit(0)
if a[0] == "ps":
    if state.exists(): print(cid)
    sys.exit(0)
if a[:2] == ["rm", "-f"]:
    if state.exists(): state.unlink()
    sys.exit(0)
sys.exit(2)
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CORNER_STATE", str(tmp_path / "running"))
    monkeypatch.setenv("CORNER_CALLS", str(tmp_path / "calls"))
    if idle:
        monkeypatch.setenv("CORNER_IDLE", "1")
    return tmp_path / "calls"


def _corner_ledger(tmp_path):
    import analog_corner_admission as ACA
    gib = 1024 ** 3
    return ACA.AdmissionLedger(tmp_path, ram_bytes=128 * gib,
                               headroom=16 * gib, active_reservations=lambda: {},
                               state_dir=tmp_path / "admission")


def test_unlaunched_corner_is_terminal_for_both_docker_missing_wordings(
        tmp_path, monkeypatch):
    import analog_corner_admission as ACA
    _fresh_corner_docker(tmp_path, monkeypatch)
    for wording in ("No such object", "No such container"):
        monkeypatch.setenv("CORNER_MISSING", wording)
        assert ACA._container_id("vibeic-corner-missing") is None


def test_fresh_a4_corner_watches_its_container_cpu_and_releases_on_exit(
        tmp_path, monkeypatch):
    import analog_corner_admission as ACA
    calls = _fresh_corner_docker(tmp_path, monkeypatch)
    monkeypatch.setattr(ACA, "CORNER_POLL_S", 0.05, raising=False)
    monkeypatch.setattr(ACA, "CORNER_STALL_GRACE_S", 0.25, raising=False)
    ledger = _corner_ledger(tmp_path)
    cp = ACA.launch(ledger, job_id="b:tt:27c", reservation="1g",
                    image="immutable-image", project=tmp_path,
                    workdir=tmp_path, simulation_args=["--skip", "true"])
    assert cp.returncode == 0, cp.stderr
    assert "vout = 1.2" in cp.stdout
    observed = calls.read_text().splitlines()
    assert sum(s.startswith("stats ") for s in observed) >= 2, observed
    assert ledger.reserved_bytes() == 0


def test_a_stalled_fresh_corner_reaps_only_its_cid_and_releases(
        tmp_path, monkeypatch):
    import analog_corner_admission as ACA
    calls = _fresh_corner_docker(tmp_path, monkeypatch, idle=True)
    monkeypatch.setattr(ACA, "CORNER_POLL_S", 0.05)
    monkeypatch.setattr(ACA, "CORNER_STALL_GRACE_S", 0.25)
    ledger = _corner_ledger(tmp_path)
    cp = ACA.launch(ledger, job_id="b:ss:hot", reservation="1g",
                    image="immutable-image", project=tmp_path,
                    workdir=tmp_path, simulation_args=["--skip", "true"])
    assert cp.returncode == ACA._wd.RC_STALLED, cp.stderr
    observed = calls.read_text().splitlines()
    assert f"rm -f {'a' * 64}" in observed, observed
    assert ledger.reserved_bytes() == 0


def test_a4_progress_stall_is_not_mislabeled_as_spent_budget(
        tmp_path, monkeypatch):
    import analog_corner_admission as ACA
    monkeypatch.setattr(ARS, "_resolve_ngspice", lambda c: "ngspice")
    monkeypatch.setattr(ARS, "_supports_json_measure", lambda c, b: False)
    monkeypatch.setattr(ARS, "_corner_reservation", lambda c: ("1g", 1 << 30))
    monkeypatch.setattr(ARS, "_corner_image", lambda c: "immutable-image")
    monkeypatch.setattr(ACA, "launch", lambda *a, **kw:
                        subprocess.CompletedProcess([], ACA._wd.RC_STALLED,
                                                    "", "WATCHDOG_STALLED"))
    ok, _meas, raw, status = ARS._run_ngspice(
        "c", "/x.sp", deck_text="tran 5n 1000n\n",
        run_to_completion=True,
        corner_job={"id": "b:tt:27c", "project": tmp_path,
                    "workdir": tmp_path, "budget_s": 900})
    assert not ok
    assert status["progress_stalled"] and not status["stopped"]
    reason = ARS.not_completed_record(raw, status, ok, None, "corner.log")
    assert (reason["verdict"], reason["reason_class"]) == (
        "NOT_MEASURED", "PROGRESS_STALLED")
    assert "budget_exhausted" not in reason


_GRADED = """* a4 corner deck
v_clk clk 0 pulse(0 1.2 1000n 1n 1n 499n 1000n)
.control
tran 5n 13825000n
wrdata /x/pvt.resolution.wrdata v(bit_out)
meas tran vavg avg v(bit_out) from=262120n to=513000n
meas tran railx_max_n max v(xdut.n)
.endc
.end
"""


def test_a4_keeps_a_graded_record_by_name_and_only_adds_a_deadline(tmp_path):
    deck, deadline, source, span = ARS.a4_run_budget(tmp_path, "blk", _GRADED)
    assert deck == _GRADED
    assert span["rule"] == "a_card_needs_the_declared_record"
    assert [h["card"] for h in span["holds_declared_record"]] == ["wrdata"]
    assert deadline == pytest.approx(ARS.BUDGET_FLOOR_S
                                     + 13825 * ARS.BUDGET_S_PER_CLOCK)
    assert source == "default_per_clock"


def test_a4_cuts_a_windowed_only_deck_like_a7_and_honours_the_spec(tmp_path):
    windowed = _GRADED.replace("wrdata /x/pvt.resolution.wrdata v(bit_out)\n",
                               "")
    b = tmp_path / "phase3/analog/blk"
    b.mkdir(parents=True)
    (b / "spec.json").write_text(json.dumps({"simulation_budget_s": 900}))
    deck, deadline, source, span = ARS.a4_run_budget(tmp_path, "blk", windowed)
    assert "tran 5n 514000n" in deck
    assert (deadline, source) == (900.0, "spec.json:simulation_budget_s")


def test_a4_corner_that_spends_its_budget_is_budget_exhausted():
    st = {"rc": 124, "stopped": True, "deadline_s": 600,
          "run_to_completion": False, "budget_source": "default_per_clock",
          "simulated_time_reached_s": 2.0e-4,
          "simulated_time_requested_s": 1.0e-3}
    rec = ARS.not_completed_record("Reference value : 2.0e-04\n", st, False,
                                   None, "pvt_ss_m40c.ngspice.log")
    assert rec["reason_class"] == "BUDGET_EXHAUSTED"
    facts = rec["budget_exhausted"]
    assert facts["simulated_time_reached_s"] == pytest.approx(2.0e-4)
    assert facts["simulated_time_requested_s"] == pytest.approx(1.0e-3)
    assert "simulation_budget_s" in facts["remedy"]


# ── A9: ngspice is supervised without a raw clock ─────────────────────────
def test_a9_ngspice_uses_no_clock_supervision(monkeypatch, tmp_path):
    import analog_a9_cosim_emit as A9
    seen = {}

    def fake(container, sp, **kw):
        seen.update(kw)
        return True, {"a9_0_min": 1.2}, "log", {"stopped": False}
    monkeypatch.setattr(ARS, "_run_ngspice", fake)
    deck = tmp_path / "deck.sp"
    deck.write_text(".control\ntran 5n 16000n\n.endc\n")
    A9.Engine("c").ngspice(deck)
    assert seen.get("run_to_completion") is True
    assert "deadline_s" not in seen


def test_a9_a_spent_budget_is_not_measured_not_graded(tmp_path):
    import test_a9_cosim_producer_runs_the_l22_scenarios as T9

    class Spent(T9.FakeEngine):
        last_budget_exhausted = {"simulated_time_reached_s": 1e-6,
                                 "simulated_time_requested_s": 1.6e-5,
                                 "remedy": "state `simulation_budget_s`"}

    project = T9._project(tmp_path)
    _rc, report = T9._run(project, Spent(meas=T9._IN_BOUNDS))
    s3 = {r["id"]: r for r in report["scenarios"]}["S3"]
    assert s3["verdict"] == "NOT_MEASURED"
    assert s3.get("reason_class") == "budget_exhausted"


def test_a_row_split_by_the_forks_json_notice_is_still_read():
    """Real ngspice-47 fork output (delta_sigma pre-layout, image 0.3.83): the
    `--json-measure` notice, written on stderr at shutdown, landed inside a
    stdout row. Only the sidecar path is neutralised here; every other byte is
    the tool's."""
    raw = ("railx_max_nmb9      =  1.28610e+00 at=  8.97007e-04\n"
           "railx_min_nmb9      =  -6.66084e-02 at=  1.29;;MEAS_JSON "
           "/work/tb.sp.measure.json N=0\n007e-04\n"
           "railx_max_ns9       =  1.28522e+00 at=  3.85007e-04\n")
    rows = ARS.native_meas_rows(raw)
    assert rows == {"railx_max_nmb9": pytest.approx(1.28610),
                    "railx_min_nmb9": pytest.approx(-6.66084e-02),
                    "railx_max_ns9": pytest.approx(1.28522)}
