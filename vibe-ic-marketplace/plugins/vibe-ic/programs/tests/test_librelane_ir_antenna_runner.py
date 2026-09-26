"""Steps 24, 26 and 26.5ic at their runner call sites, and the seal-ring
program's `--sealed-by` (lane mig101).

The runner's helpers and `die_finishing_gen` run for real.  Faked: the tool
arms' LibreLane runs (their records are written the way `librelane_ir_antenna`
writes them) and KLayout (a runner that writes what the PDK generator and the
seal-ring verifier write).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import die_finishing_gen as DFG  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path, modes: dict) -> Path:
    project = tmp_path / "proj"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    (pnr / "top.def").write_text("VERSION 5.8 ;\nDESIGN top ;\nEND DESIGN\n")
    (pnr / "top.gds").write_bytes(b"shipped-stream")
    (project / "reports/phase3").mkdir(parents=True)
    (project / "phase3/librelane_switch.json").write_text(json.dumps({"steps": modes}))
    return project


def _write(project, name, doc):
    (project / "reports/phase3" / name).write_text(json.dumps(doc))


# --- the refusals, re-read on every call ------------------------------------------

def _ir_doc(project, verdict, reasons=(), def_sha=None):
    return {"step": "24", "def_sha256": def_sha or _sha(project / "phase3/stage3/pnr/top.def"),
            "judgment": {"verdict": verdict, "reasons": list(reasons)}}


def test_direct_everywhere_refuses_nothing(tmp_path):
    project = _project(tmp_path, {})
    assert R._librelane_step_refusals(project, "top", final=True) == []


def test_step24_librelane_takes_the_tools_verdict(tmp_path):
    project = _project(tmp_path, {"24": "librelane"})
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "PASS"))
    assert R._librelane_step_refusals(project, "top", final=False) == []
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "NOT_MEASURED", ["refused: x"]))
    assert R._librelane_step_refusals(project, "top", final=False)


def test_step24_dual_notes_a_tool_fail_but_refuses_disagreeing_arms(tmp_path):
    project = _project(tmp_path, {"24": "dual"})
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "FAIL", ["IR rule: over budget"]))
    assert R._librelane_step_refusals(project, "top", final=False) == []
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "FAIL", ["LL_IR_ARMS_DISAGREE: ['VSS']"]))
    assert R._librelane_step_refusals(project, "top", final=False)
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "FAIL", ["LL_IR_RC_MODEL_INCONSISTENT: x"]))
    assert R._librelane_step_refusals(project, "top", final=False)


def test_a_record_about_another_route_is_refused(tmp_path):
    project = _project(tmp_path, {"24": "dual", "26": "dual"})
    _write(project, R._LL_IR_RECORD, _ir_doc(project, "PASS", def_sha="0" * 64))
    _write(project, R._LL_ANTENNA_RECORD, {"step": "26", "def_sha256": "0" * 64})
    out = R._librelane_step_refusals(project, "top", final=False)
    assert len(out) == 2 and all("another routed DEF" in r for r in out)


def test_step26_final_needs_the_gds_model(tmp_path):
    project = _project(tmp_path, {"26": "librelane"})
    doc = {"step": "26", "def_sha256": _sha(project / "phase3/stage3/pnr/top.def"),
           "router": {"counts": {}}}
    _write(project, R._LL_ANTENNA_RECORD, doc)
    assert R._librelane_step_refusals(project, "top", final=False) == []
    assert R._librelane_step_refusals(project, "top", final=True)
    doc["judgment"] = {"verdict": "PASS", "reasons": []}
    _write(project, R._LL_ANTENNA_RECORD, doc)
    assert R._librelane_step_refusals(project, "top", final=True) == []


def test_step26_refuses_a_count_on_an_unfinished_route(tmp_path):
    project = _project(tmp_path, {"26": "dual"})
    base = {"step": "26", "def_sha256": _sha(project / "phase3/stage3/pnr/top.def"),
            "judgment": {"verdict": "PASS", "reasons": []}}
    _write(project, R._LL_ANTENNA_RECORD, dict(base, routing_incomplete=False))
    assert R._librelane_step_refusals(project, "top", final=True) == []
    _write(project, R._LL_ANTENNA_RECORD, dict(base, routing_incomplete=True))
    assert R._librelane_step_refusals(project, "top", final=True)
    _write(project, R._LL_ANTENNA_RECORD, dict(base, route_modified_after_last_verification=True))
    assert R._librelane_step_refusals(project, "top", final=True)


def test_the_router_record_reads_the_shipping_sessions_abort_markers(tmp_path, monkeypatch):
    import librelane_ir_antenna as la
    project = _project(tmp_path, {"26": "dual"})
    pnr = project / "phase3/stage3/pnr"
    # A route abort the router logged (DRT-0305), and no post-route verification.
    (pnr / "openroad.log").write_text("[ERROR DRT-0305] Net VDD of signal type POWER is dangling.\n")
    monkeypatch.setattr(R, "_librelane_step_ctx", lambda *a: ("img", tmp_path))
    monkeypatch.setattr(la, "run_antenna_router", lambda *a, **k: {
        "counts": {"antenna__violating__nets": 0, "antenna__violating__pins": 0},
        "state": str(pnr / "x"), "state_sha256": "s", "subject_sha256": "d"})
    written = []
    R._librelane_antenna_router(project, "top", SimpleNamespace(name="gf"), "dual", written)
    doc = json.loads((project / "reports/phase3" / R._LL_ANTENNA_RECORD).read_text())
    assert doc["routing_incomplete"] is True


def test_step26_dual_refuses_model_disagreement_and_router_arms(tmp_path):
    project = _project(tmp_path, {"26": "dual"})
    base = {"step": "26", "def_sha256": _sha(project / "phase3/stage3/pnr/top.def")}
    _write(project, R._LL_ANTENNA_RECORD, dict(base, judgment={
        "verdict": "FAIL", "reasons": ["router model: 3 antenna violation(s)"]}))
    assert R._librelane_step_refusals(project, "top", final=True) == []
    _write(project, R._LL_ANTENNA_RECORD, dict(base, judgment={
        "verdict": "FAIL", "reasons": ["LL_ANTENNA_MODELS_DISAGREE: router 0 vs GDS deck 7"]}))
    assert R._librelane_step_refusals(project, "top", final=True)
    _write(project, R._LL_ANTENNA_RECORD, dict(base, router_arms="DISAGREE: direct 0 vs tool 2",
                                               judgment={"verdict": "PASS", "reasons": []}))
    assert R._librelane_step_refusals(project, "top", final=True)


def test_step265ic_dual_refuses_different_sealed_streams(tmp_path):
    project = _project(tmp_path, {"26.5ic": "dual"})
    _write(project, R._LL_SEAL_RECORD, {"xor": {"verdict": "AGREE"}})
    assert R._librelane_step_refusals(project, "top", final=True) == []
    _write(project, R._LL_SEAL_RECORD, {"xor": {"verdict": "DISAGREE", "xor_difference_count": 12}})
    assert R._librelane_step_refusals(project, "top", final=True)


# --- step 24 librelane publishes what the gates read --------------------------------

def test_the_published_ir_report_passes_the_step24_gate(tmp_path):
    project = _project(tmp_path, {"24": "librelane"})
    tool = project / "phase3/librelane/24/01-openroad-irdropreport"
    tool.mkdir(parents=True)
    (tool / "state_out.json").write_text("{}")
    # LibreLane's own irdrop.rpt from the spm run (debug lines removed).
    (tool / "irdrop.rpt").write_text(
        (_PROGRAMS / "tests/fixtures/librelane_irdrop/irdrop.rpt").read_text())
    rule = {"analysed_nets": ["VDD", "VSS"], "supply_v": 5.0, "worst_drop_v": 0.00824424,
            "worst_drop_pct": 0.1648848, "budget_v": 0.5, "budget_pct": 10.0,
            "per_net_worst_drop_v": {"VDD": 0.0072882, "VSS": 0.00824424}}
    doc = {"producer": "librelane:OpenROAD.IRDropReport", "def_sha256": "d",
           "record": {"tool_state": str(tool / "state_out.json"), "tool_state_sha256": "s",
                      "vdd_nets": ["VDD"], "gnd_nets": ["VSS"], "rc_source": "tech LEF",
                      "source_model": "declared supply pads", "budget_source": "default"},
           "judgment": {"verdict": "PASS", "reasons": [], "rule": rule,
                        "psm_coverage": {"analysis_failed": [], "unconnected_instances": []}}}
    R._librelane_step24_publish(project, doc)
    published = json.loads((project / "reports/phase3/ir_drop.json").read_text())
    assert published["worst_ir_uv"] == pytest.approx(8244.24)
    assert published["nets_analysed"] == ["VDD", "VSS"]
    assert published["verdict"] == "PASS"
    out = project / "reports/phase3/ir_drop_signoff.json"
    rc = subprocess.run([sys.executable, str(_PROGRAMS / "ir_drop_report_check.py"),
                         str(project), "--json", str(out)], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout[-2000:]


# --- step 26.5ic: _die_finishing hands the tool's output to die_finishing_gen --------

def _seal_call(tmp_path, monkeypatch, mode, tool):
    project = _project(tmp_path, {"26.5ic": mode})
    gds = project / "phase3/stage3/pnr/top.gds"
    calls = []
    monkeypatch.setattr(R, "_pdk_dir_of", lambda pdk: str(tmp_path / "pdk/gf"))
    # raising=False: in `direct` mode these must not be reached, and a tree
    # without them (the pre-change runner) must still answer that control.
    monkeypatch.setattr(R, "_librelane_sealring", lambda *a, **k: tool, raising=False)
    monkeypatch.setattr(R, "_librelane_sealring_xor", lambda *a, **k: calls.append("xor"),
                        raising=False)
    monkeypatch.setattr(R._pr, "run_best_effort", lambda argv, **k: calls.append(list(argv))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    ok, note = R._die_finishing(project, "top", SimpleNamespace(name="gf"), gds, None)
    return ok, note, calls


def test_librelane_mode_verifies_the_tools_ring_not_our_own(tmp_path, monkeypatch):
    ok, note, calls = _seal_call(tmp_path, monkeypatch, "librelane",
                                 {"tool": {"sealed_gds": "/t/sealed.gds"}})
    argv = calls[0]
    assert argv[argv.index("--sealed-by") + 1] == "/t/sealed.gds"
    assert "--sealed-by-record" in argv and "--sealed-by-error" not in argv
    assert ok and "LibreLane" in note


def test_a_tool_refusal_reaches_the_report_and_nothing_falls_back(tmp_path, monkeypatch):
    ok, note, calls = _seal_call(tmp_path, monkeypatch, "librelane",
                                 {"refusal": "LL_SEALRING_ORIGIN_UNSUPPORTED: [10, 10, 90, 90]"})
    argv = calls[0]
    assert argv[argv.index("--sealed-by-error") + 1].startswith("LL_SEALRING_ORIGIN_UNSUPPORTED")
    assert "--sealed-by" not in argv


def test_dual_seals_directly_then_compares(tmp_path, monkeypatch):
    ok, note, calls = _seal_call(tmp_path, monkeypatch, "dual",
                                 {"tool": {"sealed_gds": "/t/sealed.gds"}})
    assert "--sealed-by" not in calls[0] and calls[-1] == "xor"


def test_direct_mode_is_unchanged(tmp_path, monkeypatch):
    ok, note, calls = _seal_call(tmp_path, monkeypatch, "direct",
                                 {"tool": {"sealed_gds": "/t/sealed.gds"}})
    assert len(calls) == 1 and not any(a.startswith("--sealed-by") for a in calls[0])


def test_a_die_that_owes_no_ring_runs_no_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(DFG, "_declaration",
                        lambda project: ({DFG._DECL_REQUIRED: False}, None))
    ok, note, calls = _seal_call(tmp_path, monkeypatch, "librelane",
                                 {"tool": {"sealed_gds": "/t/sealed.gds"}})
    assert not any(a.startswith("--sealed-by") for a in calls[0])


# --- die_finishing_gen --sealed-by: the tool's output judged like ours -------------

class _KLayout:
    """Writes what the PDK generator and the ring verifier write."""
    kind, detail = "test", "fake"

    def __init__(self):
        self.generator_calls = []

    def cpath(self, p):
        return str(p)

    def covers(self, p):
        return True

    def exists(self, p):
        return Path(str(p)).is_file()

    def klayout_bin(self):
        return "klayout"

    def run_argv(self, argv, env, timeout=1800):
        argv = [str(a) for a in argv]
        if argv[0] == "head":
            return 0, Path(argv[-1]).read_text()[:2000], ""
        if argv[0] == "sh":
            return 1, "", ""
        self.generator_calls.append(argv)
        out = argv[argv.index("--output") + 1]
        Path(out).write_bytes(Path(argv[argv.index("--input") + 1]).read_bytes() + b"+ring")
        return 0, "", ""

    def run(self, script, env, path_keys=(), timeout=1800):
        ok = Path(env["SEAL_OUT"]).is_file()
        Path(env["SEAL_REPORT"]).write_text(json.dumps(
            {"check": "pdk_seal_ring_present", "verdict": "PASS" if ok else "FAIL",
             "added_layers": ["81/0"], "ring": {"horizontal_crossings": 2,
                                                "vertical_crossings": 2,
                                                "centre_covered": False},
             "die_box_dbu": [0, 0, 100000, 100000],
             "ring_extent": {"outer": {"dbu": [0, 0, 100000, 100000], "um": [0, 0, 100, 100]},
                             "inner": {"dbu": [5000, 5000, 95000, 95000],
                                       "um": [5, 5, 95, 95]}}}))
        return 0, "", ""


def _dfg(tmp_path, monkeypatch, **kw):
    project = tmp_path / "p"
    (project / "phase3/stage3/pnr").mkdir(parents=True)
    gds = project / "phase3/stage3/pnr/top.gds"
    gds.write_bytes(b"die")
    (project / "phase3/stage3/pnr/routed.def").write_text(
        "VERSION 5.8 ;\nDESIGN top ;\nUNITS DISTANCE MICRONS 1000 ;\n"
        "DIEAREA ( 0 0 ) ( 100000 100000 ) ;\nEND DESIGN\n")
    script = tmp_path / "sealring.py"
    script.write_text("# --die-width\n")
    fake = _KLayout()
    monkeypatch.setattr(DFG._kl, "find_runner", lambda *a, **k: fake)
    res = DFG.run(project, str(gds), str(script), None, None, None, None, sys.executable,
                  None, 100.0, 100.0, None, True, None, **kw)
    return res, fake, gds


def test_a_sealed_by_stream_is_verified_and_shipped_without_our_generator(tmp_path, monkeypatch):
    tool = tmp_path / "tool_sealed.gds"
    tool.write_bytes(b"die+tool-ring")
    record = tmp_path / "record.json"
    record.write_text("{}")
    res, fake, gds = _dfg(tmp_path, monkeypatch, sealed_by=str(tool),
                          sealed_by_record=str(record))
    seal = res["seal_ring"]
    assert fake.generator_calls == []
    assert seal["state"] == "PASS", seal.get("reason")
    assert seal["producer"] == "librelane:KLayout.SealRing"
    assert seal["sealed_by"]["sha256"] == _sha(tool)
    assert seal["sealed_by"]["record_sha256"] == _sha(record)
    assert gds.read_bytes() == b"die+tool-ring"


def test_a_producer_refusal_fails_the_ring_by_name(tmp_path, monkeypatch):
    res, fake, gds = _dfg(tmp_path, monkeypatch,
                          sealed_by_error="LL_SEALRING_ORIGIN_UNSUPPORTED: [10, 10, 90, 90]")
    seal = res["seal_ring"]
    assert fake.generator_calls == []
    assert seal["state"] == "FAIL"
    assert "LL_SEALRING_ORIGIN_UNSUPPORTED" in seal["reason"]
    assert gds.read_bytes() == b"die"


def test_a_missing_sealed_by_stream_fails(tmp_path, monkeypatch):
    res, fake, gds = _dfg(tmp_path, monkeypatch, sealed_by=str(tmp_path / "absent.gds"))
    assert res["seal_ring"]["state"] == "FAIL" and fake.generator_calls == []


def test_without_sealed_by_our_generator_runs_as_before(tmp_path, monkeypatch):
    res, fake, gds = _dfg(tmp_path, monkeypatch)
    assert len(fake.generator_calls) == 1
    assert res["seal_ring"]["state"] == "PASS"
    assert "producer" not in res["seal_ring"]


def _publish_with_transient(project, transient):
    tool = project / "phase3/librelane/24/01-openroad-irdropreport"
    tool.mkdir(parents=True, exist_ok=True)
    (tool / "state_out.json").write_text("{}")
    rule = {"analysed_nets": ["VDD", "VSS"], "supply_v": 5.0, "worst_drop_v": 0.00824424,
            "worst_drop_pct": 0.1648848, "budget_v": 0.5, "budget_pct": 10.0,
            "per_net_worst_drop_v": {"VDD": 0.0072882, "VSS": 0.00824424}}
    R._librelane_step24_publish(project, {
        "producer": "librelane:OpenROAD.IRDropReport", "def_sha256": "d",
        "record": {"tool_state": str(tool / "state_out.json"), "tool_state_sha256": "s",
                   "vdd_nets": ["VDD"], "gnd_nets": ["VSS"], "transient": transient},
        "judgment": {"verdict": "PASS", "reasons": [], "rule": rule,
                     "psm_coverage": {"analysis_failed": [], "unconnected_instances": []}}})
    out = project / "reports/phase3/dynamic_ir_gate.json"
    return subprocess.run([sys.executable, str(_PROGRAMS / "dynamic_ir_drop_check.py"),
                           str(project / "reports/phase3/dynamic_ir.json"), "--budget-pct", "10",
                           "--json", str(out)], capture_output=True, text=True)


def test_the_tool_transient_is_published_for_the_dynamic_gate(tmp_path):
    import librelane_ir_antenna as la
    transient_findings = la.transient_findings
    project = _project(tmp_path, {"24": "librelane"})
    transient = transient_findings(
        (_PROGRAMS / "tests/fixtures/librelane_irdrop/transient_quasi_static.rpt").read_text(),
        ["VDD", "VSS"], 5.0, 24.0, "sdc_create_clock")
    rc = _publish_with_transient(project, transient)
    doc = json.loads((project / "reports/phase3/dynamic_ir.json").read_text())
    assert doc["producer"] == "librelane:Vibeic.TransientIR"
    assert doc["scaled_static_bound"] is True and doc["max_dynamic_drop_mv"] == pytest.approx(16.5)
    assert rc.returncode == 0, rc.stdout[-1500:]


def test_a_transient_the_tool_could_not_solve_blocks_the_dynamic_gate(tmp_path):
    project = _project(tmp_path, {"24": "librelane"})
    rc = _publish_with_transient(project, {"verdict": "NOT_MEASURED",
                                           "reason": "no dynamic IR line for net(s) ['VSS']"})
    doc = json.loads((project / "reports/phase3/dynamic_ir.json").read_text())
    assert doc["status"] == "ERROR_NO_PSM_IR"
    assert rc.returncode == 1
