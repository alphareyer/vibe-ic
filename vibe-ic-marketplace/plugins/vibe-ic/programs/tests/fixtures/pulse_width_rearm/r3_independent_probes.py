#!/usr/bin/env python3
"""Independent production-path probes for LAND6 Package C R3.

This file lives outside the reviewed checkout.  It imports the exact candidate
and creates only temporary projects.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(os.environ.get("REVIEW_CLONE", "/workspace")).resolve()
PROGRAMS = ROOT / "vibe-ic-marketplace/plugins/vibe-ic/programs"
FIXTURES = PROGRAMS / "tests/fixtures/pulse_width_rearm"
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as runner  # noqa: E402
import pulse_width_rearm_conformance_check as checker  # noqa: E402


TOP = "pulse_width_rearm_fixture"
GOOD = FIXTURES / "compliant.sv"
BAD = FIXTURES / "level_reacquire_bad.sv"
CONTRACT = FIXTURES / "contract.json"

results = []
vulnerabilities = []


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record(name: str, ok: bool, **details) -> None:
    results.append({"name": name, "ok": bool(ok), **details})
    if not ok:
        raise AssertionError(f"{name}: {details}")


def make_project(root: Path, rtl_bytes: bytes, *, contract: bool = True):
    project = root / "project"
    src_dir = project / "phase1/input_prompt"
    rtl_dir = project / "phase2/stage1/rtl"
    src_dir.mkdir(parents=True)
    rtl_dir.mkdir(parents=True)
    source = src_dir / ("contract.json" if contract else "prompt.txt")
    source.write_bytes(CONTRACT.read_bytes() if contract else
                       b"Implement a simple synchronous pulse detector.\n")
    rtl = rtl_dir / "dut.sv"
    rtl.write_bytes(rtl_bytes)
    return project, source, rtl


def ordinary_pass_counter(counter):
    def ordinary(*_args, **_kwargs):
        counter["calls"] += 1
        return runner.StepResult("reference_tb", "PASS", detail="ordinary TB PASS")
    return ordinary


def probe_normal_state(name, native_rc, native_report, expected_status,
                       expected_ordinary_calls):
    with tempfile.TemporaryDirectory(prefix="c_r3_state_") as td:
        receipt = Path(td) / "native.json"
        receipt.write_text(json.dumps(native_report))
        counter = {"calls": 0}
        with patch.object(runner, "_run_pulse_width_rearm_normal_gate",
                          return_value=(native_rc, native_report, receipt)), \
             patch.object(runner, "_step_reference_tb_without_pulse_gate",
                          side_effect=ordinary_pass_counter(counter)):
            got = runner.step_reference_tb(Path(td) / "project", TOP, None,
                                           "pinned-image")
        record(name,
               got.status == expected_status and
               counter["calls"] == expected_ordinary_calls,
               status=got.status, ordinary_calls=counter["calls"],
               native_rc=native_rc, native_report=native_report)


def normal_gate_matrix():
    probe_normal_state("native_skip_cannot_be_hidden", 0,
                       {"verdict": "SKIP", "measured": False,
                        "reason": "tools unavailable"}, "NOT_MEASURED", 0)
    probe_normal_state("unmeasured_pass_cannot_be_hidden", 0,
                       {"verdict": "PASS", "measured": False},
                       "NOT_MEASURED", 0)
    probe_normal_state("unknown_verdict_cannot_be_hidden", 0,
                       {"verdict": "MYSTERY", "measured": False},
                       "NOT_MEASURED", 0)
    probe_normal_state("nonzero_rc_pass_cannot_be_hidden", 9,
                       {"verdict": "PASS", "measured": True},
                       "NOT_MEASURED", 0)
    probe_normal_state("unmeasured_fail_cannot_be_hidden", 1,
                       {"verdict": "FAIL", "measured": False},
                       "NOT_MEASURED", 0)
    probe_normal_state("measured_fail_precedes_ordinary", 7,
                       {"verdict": "FAIL", "measured": True},
                       "FAIL", 0)
    probe_normal_state("explicit_not_applicable_allows_ordinary", 0,
                       {"verdict": "NOT_APPLICABLE", "measured": False,
                        "declared_by": "source-bound contract discovery"},
                       "PASS", 1)


def actual_missing_tool_and_na():
    with tempfile.TemporaryDirectory(prefix="c_r3_actual_state_") as td:
        project, _source, _rtl = make_project(Path(td), GOOD.read_bytes())
        calls = {"calls": 0}
        real_which = checker.shutil.which

        def missing_sim(name):
            if name in {"iverilog", "vvp"}:
                return None
            return real_which(name)

        with patch.object(checker.shutil, "which", side_effect=missing_sim), \
             patch.object(runner, "_step_reference_tb_without_pulse_gate",
                          side_effect=ordinary_pass_counter(calls)):
            got = runner.step_reference_tb(project, TOP, None, "pinned-image")
        receipt = json.loads((project / "reports/phase2/gates/"
                              "pulse_width_rearm_conformance.json").read_text())
        record("actual_missing_tools_stops_before_ordinary",
               got.status == "NOT_MEASURED" and calls["calls"] == 0 and
               receipt["verdict"] == "SKIP" and receipt["measured"] is False,
               status=got.status, ordinary_calls=calls["calls"], receipt=receipt)

    with tempfile.TemporaryDirectory(prefix="c_r3_actual_na_") as td:
        project, _source, _rtl = make_project(Path(td), GOOD.read_bytes(),
                                               contract=False)
        calls = {"calls": 0}
        with patch.object(runner, "_step_reference_tb_without_pulse_gate",
                          side_effect=ordinary_pass_counter(calls)):
            got = runner.step_reference_tb(project, TOP, None, "pinned-image")
        receipt = json.loads((project / "reports/phase2/gates/"
                              "pulse_width_rearm_conformance.json").read_text())
        record("actual_explicit_not_applicable_semantics",
               got.status == "PASS" and calls["calls"] == 1 and
               receipt["verdict"] == "NOT_APPLICABLE" and
               receipt["measured"] is False and
               receipt["declared_by"] == "source-bound contract discovery",
               status=got.status, ordinary_calls=calls["calls"], receipt=receipt)


def bound_pass_and_atomic_publication():
    with tempfile.TemporaryDirectory(prefix="c_r3_bound_") as td:
        project, source, rtl = make_project(Path(td), GOOD.read_bytes())
        helper = rtl.parent / "helper.sv"
        helper.write_text("module helper; endmodule\n")
        report_path = project / "reports/gate.json"
        initial = {str(p): p.read_bytes() for p in (source, rtl, helper)}
        real_run_check = checker.run_check
        guarded = {str(source), str(rtl), str(helper)}
        guard_calls = []

        def frozen_only(*args, **kwargs):
            supplied = kwargs.get("_input_bytes")
            assert supplied is not None
            assert all(supplied[k] == initial[k] for k in guarded)
            real_read_bytes = Path.read_bytes

            def no_live_read(path_obj):
                if str(path_obj) in guarded:
                    guard_calls.append(str(path_obj))
                    raise AssertionError("run_check reread a live admitted input")
                return real_read_bytes(path_obj)

            with patch.object(Path, "read_bytes", no_live_read):
                return real_run_check(*args, **kwargs)

        with patch.object(checker, "run_check", side_effect=frozen_only):
            rc, report = checker.run_project(project, [rtl, helper], TOP,
                                             report_path=report_path)
        expected_subject = sha(initial[str(rtl)] + b"\n" + initial[str(helper)])
        disk = json.loads(report_path.read_text())
        temp_left = sorted(p.name for p in report_path.parent.iterdir()
                           if p.name.startswith(f".{report_path.name}.") and
                           p.name.endswith(".tmp"))
        record("frozen_one_read_ordered_binding_and_publish",
               rc == 0 and report["verdict"] == "PASS" and
               report["measured"] is True and not guard_calls and
               report["source_file_sha256"] == sha(initial[str(source)]) and
               report["rtl_sha256"] == sha(initial[str(rtl)]) and
               report["context_sha256"] == {str(helper): sha(initial[str(helper)])} and
               report["rtl_subject_sha256"] == expected_subject and
               report["rtl_file_sha256"] == {
                   str(rtl): sha(initial[str(rtl)]),
                   str(helper): sha(initial[str(helper)])} and
               report["input_stability"]["stable"] is True and
               disk == report and not temp_left and
               stat.S_IMODE(report_path.stat().st_mode) == 0o600,
               rc=rc, verdict=report["verdict"], measured=report["measured"],
               source_sha=report["source_file_sha256"],
               rtl_sha=report["rtl_sha256"],
               context_sha=report["context_sha256"],
               subject_sha=report["rtl_subject_sha256"],
               guard_calls=guard_calls, temp_left=temp_left,
               mode=oct(stat.S_IMODE(report_path.stat().st_mode)))

    with tempfile.TemporaryDirectory(prefix="c_r3_atomic_") as td:
        report_path = Path(td) / "receipt.json"
        report_path.write_text("OLD_RECEIPT\n")
        visibility = []
        payload = {"verdict": "PASS", "measured": True}

        def before_commit():
            visibility.append(report_path.read_text())
            payload["commit_check"] = "complete"

        checker._publish_report(report_path, payload, before_commit)
        committed = json.loads(report_path.read_text())
        success_temp = list(Path(td).glob(".receipt.json.*.tmp"))
        success_mode = stat.S_IMODE(report_path.stat().st_mode)
        report_path.write_text("OLD_RECEIPT_2\n")

        def refuse_commit():
            assert report_path.read_text() == "OLD_RECEIPT_2\n"
            raise OSError("injected final validation refusal")

        refused = False
        try:
            checker._publish_report(report_path, {"verdict": "PASS"}, refuse_commit)
        except OSError:
            refused = True
        failure_temp = list(Path(td).glob(".receipt.json.*.tmp"))
        record("atomic_receipt_success_and_refusal",
               visibility == ["OLD_RECEIPT\n"] and
               committed == {"verdict": "PASS", "measured": True,
                             "commit_check": "complete"} and
               not success_temp and success_mode == 0o600 and refused and
               report_path.read_text() == "OLD_RECEIPT_2\n" and not failure_temp,
               old_visible_during_validation=visibility,
               committed=committed, success_mode=oct(success_mode),
               refused=refused, success_temp=[str(p) for p in success_temp],
               failure_temp=[str(p) for p in failure_temp])


def drift_after_measurement(measured_bad: bool):
    label = "fail" if measured_bad else "pass"
    with tempfile.TemporaryDirectory(prefix=f"c_r3_drift_{label}_") as td:
        base_rtl = BAD.read_bytes() if measured_bad else GOOD.read_bytes()
        project, source, rtl = make_project(Path(td), base_rtl)
        report_path = project / "reports/gate.json"
        frozen_source = source.read_bytes()
        frozen_rtl = rtl.read_bytes()
        real_run_check = checker.run_check

        def mutate_after_measurement(*args, **kwargs):
            rc, report = real_run_check(*args, **kwargs)
            source.write_bytes(frozen_source + b"\n ")
            rtl.write_bytes(frozen_rtl + b"\n// live replacement\n")
            return rc, report

        with patch.object(checker, "run_check", side_effect=mutate_after_measurement):
            rc, report = checker.run_project(project, [rtl], TOP,
                                             report_path=report_path)
        disk = json.loads(report_path.read_text())
        expected_rc = 1 if measured_bad else 3
        expected_verdict = "FAIL" if measured_bad else "NOT_MEASURED"
        record(f"live_drift_after_{label}_is_fail_closed",
               rc == expected_rc and report["verdict"] == expected_verdict and
               report["measured"] is measured_bad and
               report["input_stability"]["stable"] is False and
               report["source_file_sha256"] == sha(frozen_source) and
               report["rtl_sha256"] == sha(frozen_rtl) and
               report["source_file_sha256"] != sha(source.read_bytes()) and
               report["rtl_sha256"] != sha(rtl.read_bytes()) and
               disk == report and
               (not measured_bad or "measured FAIL takes precedence" in report["reason"]),
               rc=rc, verdict=report["verdict"], measured=report["measured"],
               stability=report["input_stability"], reason=report["reason"],
               published_source_sha=report["source_file_sha256"],
               live_source_sha=sha(source.read_bytes()),
               published_rtl_sha=report["rtl_sha256"],
               live_rtl_sha=sha(rtl.read_bytes()))


def race_at_atomic_replace_probe():
    with tempfile.TemporaryDirectory(prefix="c_r3_replace_race_") as td:
        project, source, rtl = make_project(Path(td), GOOD.read_bytes())
        report_path = project / "reports/gate.json"
        frozen_source = source.read_bytes()
        frozen_rtl = rtl.read_bytes()
        real_replace = os.replace
        fired = {"value": False}

        def replace_after_last_validation(src, dst):
            if Path(dst) == report_path:
                fired["value"] = True
                source.write_bytes(frozen_source + b"\n ")
                rtl.write_bytes(frozen_rtl + b"\n// raced before receipt commit\n")
            return real_replace(src, dst)

        with patch.object(checker.os, "replace", side_effect=replace_after_last_validation):
            rc, report = checker.run_project(project, [rtl], TOP,
                                             report_path=report_path)
        disk = json.loads(report_path.read_text())
        bypass = bool(
            fired["value"] and rc == 0 and report["verdict"] == "PASS" and
            report["measured"] is True and
            report["input_stability"]["stable"] is True and disk == report and
            report["source_file_sha256"] != sha(source.read_bytes()) and
            report["rtl_sha256"] != sha(rtl.read_bytes()))
        results.append({
            "name": "atomic_replace_race_counterexample",
            "ok": not bypass,
            "bypass_observed": bypass,
            "rc": rc,
            "verdict": report["verdict"],
            "measured": report["measured"],
            "stability": report["input_stability"],
            "published_source_sha": report["source_file_sha256"],
            "live_source_sha": sha(source.read_bytes()),
            "published_rtl_sha": report["rtl_sha256"],
            "live_rtl_sha": sha(rtl.read_bytes()),
        })
        if bypass:
            vulnerabilities.append("atomic_replace_race_counterexample")


def result_channel_forgery_probe():
    bad = BAD.read_text()
    injection = r'''
  integer attack_fd;
  initial begin
    #0;
    attack_fd = $fopen("pwr_result_channel.txt", "w");
    $fdisplay(attack_fd, "PWR_CHANNEL_BEGIN");
    $fdisplay(attack_fd, "PWR_COUNTS legal=1 boundary_bad=0 bad=0 final=1 max=4");
    $fdisplay(attack_fd, "PWR_VERDICT PASS");
    $fdisplay(attack_fd, "PWR_CHANNEL_END");
    $fclose(attack_fd);
    $display("DUT_FORGED_RESULT_CHANNEL");
    $finish;
  end
'''
    malicious = bad.rsplit("endmodule", 1)[0] + injection + "endmodule\n"
    with tempfile.TemporaryDirectory(prefix="c_r3_channel_attack_") as td:
        project, _source, rtl = make_project(Path(td), malicious.encode())
        calls = {"calls": 0}
        with patch.object(runner, "_step_reference_tb_without_pulse_gate",
                          side_effect=ordinary_pass_counter(calls)):
            got = runner.step_reference_tb(project, TOP, None, "pinned-image")
        receipt_path = project / "reports/phase2/gates/pulse_width_rearm_conformance.json"
        receipt = json.loads(receipt_path.read_text())
        bypass = bool(
            got.status == "PASS" and calls["calls"] == 1 and
            receipt["verdict"] == "PASS" and receipt["measured"] is True and
            receipt.get("counts") == {"legal": 1, "boundary_bad": 0,
                                      "bad": 0, "final": 1, "max": 4} and
            any("DUT_FORGED_RESULT_CHANNEL" in row
                for row in receipt.get("raw_simulation_tail", [])))
        results.append({
            "name": "dut_can_forge_measurement_owned_result_channel",
            "ok": not bypass,
            "bypass_observed": bypass,
            "overall_status": got.status,
            "ordinary_calls": calls["calls"],
            "native_rc_implied": 0 if receipt["verdict"] == "PASS" else None,
            "native_verdict": receipt["verdict"],
            "native_measured": receipt["measured"],
            "counts": receipt.get("counts"),
            "raw_simulation_tail": receipt.get("raw_simulation_tail"),
            "rtl_sha256": receipt.get("rtl_sha256"),
        })
        if bypass:
            vulnerabilities.append("dut_can_forge_measurement_owned_result_channel")


def main():
    normal_gate_matrix()
    actual_missing_tool_and_na()
    bound_pass_and_atomic_publication()
    drift_after_measurement(False)
    drift_after_measurement(True)
    race_at_atomic_replace_probe()
    result_channel_forgery_probe()
    print(json.dumps({"schema": "land6-c-r3-adversarial-probes.v1",
                      "candidate": "98f3e8d15e883c575c55f87f88b258b1a7bb2751",
                      "results": results,
                      "vulnerabilities": vulnerabilities,
                      "verdict": "REJECT" if vulnerabilities else "ACCEPT"},
                     indent=2, sort_keys=True))
    return 1 if vulnerabilities else 0


if __name__ == "__main__":
    raise SystemExit(main())
