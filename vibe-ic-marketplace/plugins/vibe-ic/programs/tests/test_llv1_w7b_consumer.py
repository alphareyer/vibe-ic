"""llv1 W7b: the runners' consumer mode for an external flow (`--librelane`).

Piece 1 -- Phase 2. Under the flag, `design_one_shot_runner` runs steps 1-8
unchanged and does not dispatch step 9 (`step_yosys_synth`) or the DFT/LEC chain
(11-13): LibreLane segment 1 synthesizes in phase 3 and vibe-ic runs 11-14
between the segments. The two sites are pruned by the SAME exit machinery a
declared `--exit-step 8` uses, each booked NOT_APPLICABLE with the flag that
pruned it and the phase that runs it. Without the flag nothing changes.
"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402
import step_preflight as SPF  # noqa: E402


def _d():
    import design_one_shot_runner as D
    return D


def test_phase2_under_the_flag_is_the_window_steps_1_to_8():
    D = _d()
    sites = SPF.RUNNER_PLANS["design_one_shot_runner"].sites
    pruned = D._exit_pruned_sites(sites, IF.CONSUMER_PHASE2_LAST_STEP)
    assert pruned == ["yosys_synth", "dft_lec_chain"]
    kept = [n for n, _ in sites if n not in pruned]
    assert kept == ["rtl_gen", "rtl_validate", "sim"]
    assert D.run_is_bounded(None, pruned, [n for n, _ in sites])


def test_the_design_runner_is_wired_and_the_mode_comes_from_the_flag():
    assert "design_one_shot_runner" in IF.WIRED_RUNNERS
    assert IF.consumer_mode(SimpleNamespace(librelane=True, orfs=False)) == \
        IF.IMPL_LIBRELANE
    assert IF.consumer_mode(SimpleNamespace(librelane=False, orfs=False)) is None


def test_phase3_boundary_names_one_owner_for_every_moved_step():
    boundary = IF.consumer_phase3_boundary()
    assert boundary["phase2_last_step"] == "8"
    assert boundary["segment1"] == ("9",)
    assert boundary["between_segments"] == ("11", "12", "13", "14", "prepnr")
    assert boundary["segment2"] == ("15", "16", "17", "18", "19", "20", "21", "22")
    assert boundary["importer"] == "librelane_import.import_segments"
    assert boundary["stage2_advisory"] == ("7", "8", "10")
    assert boundary["admission_digest_roots"] == ("phase2", "stage2", "synth")


def test_phase3_windows_are_refused_as_atomic_external_spans():
    disposition, reason = IF.KNOBS["phase3_one_shot_runner"]["entry_step"]
    assert disposition == IF.REFUSED
    assert "atomic manifest/import span" in reason


def test_the_sentinel_names_the_flag_and_the_phase():
    d = IF.consumer_sentinel_detail(IF.IMPL_LIBRELANE, "yosys_synth", ("9",))
    assert "--librelane" in d and "phase 3" in d and "9" in d
    assert "not missing" in d


class _Captured(Exception):
    def __init__(self, plan):
        super().__init__("captured")
        self.plan = list(plan)


def _project(p: Path) -> Path:
    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    for i in range(1, 14):
        (gd / f"L{i}_X.json").write_text("{}")
    (p / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    (p / "phase2" / "stage1" / "rtl" / "top.v").write_text(
        "module top(input clk); endmodule\n")
    return p


def _drive(monkeypatch, project: Path, *argv):
    """The real design main, every step stubbed PASS, captured at the step
    right after the synthesis site (the synth-log audit reads its result)."""
    D = _d()
    for name in dir(D):
        if name.startswith("step_") and callable(getattr(D, name)):
            monkeypatch.setattr(D, name, (lambda n: lambda *a, **k:
                                          D.StepResult(n, "PASS", 0.0, "stub"))(name))

    def _gate(project, runner, site, refusal, fn, *a, **k):
        return D.StepResult(site, "PASS", 0.0, "stub-gate")

    monkeypatch.setattr(D._spf, "gate", _gate)

    monkeypatch.setattr(D, "step_synth_log_audit",
                        lambda project, last: (_ for _ in ()).throw(
                            _Captured([last])))
    monkeypatch.setattr(D._canonical_admission, "admit_span",
                        lambda *a, **k: SimpleNamespace(
                            admitted=True, reason="ADMITTED", detail=""))
    monkeypatch.setattr(sys, "argv", ["design_one_shot_runner", str(project),
                                      *argv])
    with pytest.raises(_Captured) as ei:
        D.main()
    return ei.value.plan[0]


def test_the_real_phase2_main_does_not_dispatch_synthesis_under_the_flag(
        monkeypatch, tmp_path):
    flagged = _drive(monkeypatch, _project(tmp_path / "f"), "--librelane")
    assert flagged.name == "yosys_synth"
    assert flagged.status == "NOT_APPLICABLE"
    assert flagged.declared_by == "--librelane"
    assert "phase 3" in flagged.detail


def test_without_the_flag_synthesis_is_dispatched_as_before(monkeypatch,
                                                            tmp_path):
    default = _drive(monkeypatch, _project(tmp_path / "d"))
    assert (default.name, default.status, default.detail) == (
        "yosys_synth", "PASS", "stub-gate")


def test_the_flag_is_one_of_the_windows_declared_flags():
    D = _d()
    assert D.declared_window_flags(None, None, "--librelane") == ("--librelane",)
    assert D.declared_window_flags(None, "4", "--librelane") == (
        "--exit-step 4", "--librelane")
    assert D.declared_window_flags(None, None) == ()


def test_refresh_only_under_the_flag_stays_honoured(monkeypatch, tmp_path):
    """KNOBS declares --refresh-only HONOURED under the flag: it runs no step.
    The flag is a window flag, but it must not trip the refusal that an
    operator-declared --entry-step/--exit-step earns."""
    D = _d()
    assert IF.KNOBS["design_one_shot_runner"]["refresh_only"][0] == IF.HONOURED
    project = _project(tmp_path / "r")
    monkeypatch.setattr(D, "_run_refresh_only", lambda *a, **k: 17)
    monkeypatch.setattr(sys, "argv", ["design_one_shot_runner", str(project),
                                      "--librelane", "--refresh-only"])
    assert D.main() == 17


def _consumer_stub(monkeypatch, tmp_path, *, prepnr_status="PASS",
                   lec_verdict="PASS", lec_digest=None):
    import phase3_one_shot_runner as R
    import librelane_contract as LC
    import librelane_whole_flow as W
    import _rtl_include_hub as HUB

    project = _project(tmp_path / "consumer")
    trace = []
    pdk_root = tmp_path / "pdk"
    pdk_root.mkdir()
    liberty = pdk_root / "libA__tt.lib"
    liberty.write_text("library (libA) {}\n")
    pdk = SimpleNamespace(name="processA", liberty=str(liberty),
                          macro_libs=[], macro_lefs=[], macro_v=[])
    sdc = project / "phase2/stage2/constraints/top.sdc"
    sdc.parent.mkdir(parents=True)
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    monkeypatch.setattr(LC, "resolve_image", lambda project: "img")
    monkeypatch.setattr(LC, "pdk_root_resolution",
                        lambda *a, **k: {"path": str(pdk_root)})
    monkeypatch.setattr(LC, "emit_pdn_cfg", lambda *a, **k: None)
    monkeypatch.setattr(HUB, "silicon_rtl_selection",
                        lambda root: [root / "top.v"])
    monkeypatch.setattr(R._sf, "read_text_blob", lambda files: "")
    monkeypatch.setattr(R._sf, "decide_macro_aware_sim_define",
                        lambda *a: {"define_sim": False})
    monkeypatch.setattr(R, "_write_synth_inputs_sidecar", lambda *a: None)
    monkeypatch.setattr(R, "run_step11_dft_after_synth",
                        lambda *a: trace.append("11-12") or [])
    monkeypatch.setattr(R, "run_step13_lec_on_pnr_input",
                        lambda *a: trace.append("13") or [])
    monkeypatch.setattr(R, "step_prelayout_signoff",
                        lambda *a: trace.append("7-8-10") or
                        R.StepResult("prelayout", "PASS", 0.0, "advisory"))
    monkeypatch.setattr(R, "step_prepnr",
                        lambda *a: trace.append("prepnr") or
                        R.StepResult("prepnr", prepnr_status, 0.0,
                                     "measured" if prepnr_status == "PASS"
                                     else "SDC_SEAM_PENDING",
                                     extras={"sdc": {"path": str(sdc),
                                                     "basis": "step 7"}},
                                     reason_class=("" if prepnr_status == "PASS"
                                                   else "input_absent")))
    monkeypatch.setattr(R, "_chip_path_requests_pad_ring", lambda *a: False)
    monkeypatch.setattr(R, "pnr_input_netlist",
                        lambda subject, top: (
                            R._pl.synth_dir(subject) / f"{top}_synth.v", "", False))

    def config(subject, pdk_name, out, **kw):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}\n")
        return out

    monkeypatch.setattr(W, "segment1_config", config)

    def two_segments(subject, image, **kw):
        trace.append("segment1")
        nl = subject / "runs/segment1/01-yosys/core.nl.v"
        nl.parent.mkdir(parents=True)
        nl.write_text("module top(input clk); endmodule\n")
        proof_sha = lec_digest or hashlib.sha256(nl.read_bytes()).hexdigest()
        lec = subject / "reports/lec.json"
        lec.parent.mkdir(parents=True, exist_ok=True)
        lec.write_text(json.dumps({
            "verdict": lec_verdict,
            "proof_identity": {"top": "top", "gate_netlist": {
                "path": "phase2/stage2/synth/top_synth.v",
                "sha256": f"sha256:{proof_sha}"}}}) + "\n")
        state = nl.parent / "state_out.json"
        state.write_text(json.dumps({"nl": str(nl)}))
        kw["between"](subject, state)
        trace.extend(("segment2", "import"))
        return {"netlist_identity": {"verdict": "PASS"}, "import": {}}

    monkeypatch.setattr(W, "run_two_segments", two_segments)
    args = SimpleNamespace(container="eda", die_um="auto", util=.5)
    return R, project, pdk, args, trace


def test_phase3_consumer_orders_segments_checks_and_import(monkeypatch, tmp_path):
    R, project, pdk, args, trace = _consumer_stub(monkeypatch, tmp_path)
    assert "phase3_one_shot_runner" in IF.WIRED_RUNNERS
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert trace == ["segment1", "11-12", "13", "7-8-10", "prepnr",
                     "segment2", "import"]
    record = json.loads((project / "reports/orchestrator/phase3_one_shot.json").read_text())
    assert record["verdict"] == "NOT_MEASURED"
    assert [r["name"] for r in record["steps"]][-2:] == [
        "librelane_import", "post_import_signoff"]


def test_nonmeasured_prepnr_cannot_start_segment2(monkeypatch, tmp_path):
    R, project, pdk, args, trace = _consumer_stub(
        monkeypatch, tmp_path, prepnr_status="NOT_MEASURED")
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert len(trace) == 5  # no segment 2 or importer may follow this refusal
    assert trace == ["segment1", "11-12", "13", "7-8-10", "prepnr"]
    record = json.loads((project / "reports/orchestrator/phase3_one_shot.json").read_text())
    assert record["verdict"] == "NOT_MEASURED"
    assert record["steps"][-1]["name"] == "prepnr"


def test_inconclusive_recorded_lec_cannot_start_segment2(monkeypatch, tmp_path):
    R, project, pdk, args, trace = _consumer_stub(
        monkeypatch, tmp_path, lec_verdict="INCONCLUSIVE")
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert len(trace) == 3
    assert trace == ["segment1", "11-12", "13"]
    record = json.loads((project / "reports/orchestrator/phase3_one_shot.json").read_text())
    row = record["steps"][-1]
    assert (record["verdict"], row["name"], row["status"],
            row["reason_class"]) == (
                "NOT_MEASURED", "lec_proof_binding", "NOT_MEASURED",
                "inconclusive")


def test_stale_lec_binding_cannot_start_segment2(monkeypatch, tmp_path):
    R, project, pdk, args, trace = _consumer_stub(
        monkeypatch, tmp_path, lec_digest="0" * 64)
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert len(trace) == 3
    assert trace == ["segment1", "11-12", "13"]
    record = json.loads((project / "reports/orchestrator/phase3_one_shot.json").read_text())
    row = record["steps"][-1]
    assert (record["verdict"], row["name"], row["status"],
            row["reason_class"]) == (
                "NOT_MEASURED", "lec_proof_binding", "NOT_MEASURED",
                "not_executed")


def test_chip_segment_uses_fxports_typed_pnr_sdc_derivation(monkeypatch,
                                                            tmp_path):
    R, project, pdk, args, trace = _consumer_stub(monkeypatch, tmp_path)
    import _ppa.timing as timing
    from _atomic_artefact import write_json
    monkeypatch.setattr(R, "_chip_path_requests_pad_ring", lambda *a: True)
    wrapper = project / "phase3/stage3/pnr/chip_top_io.v"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("module chip_top(input clk); top u(clk); endmodule\n")
    write_json(project / "reports/phase3/io_pad_chip_top.json", {
        "chip_top_module": "chip_top",
        "chip_top_verilog": str(wrapper.relative_to(project))})
    calls = []

    def derive(rt, subject, top, selected_pdk, container):
        calls.append((rt, subject, top, selected_pdk, container))
        return {"text": "create_clock -period 11 [get_ports clk]\n",
                "path": "phase2/stage2/constraints/top.asic.sdc",
                "step7_sha256": "sha256:source", "derivation": {"applied": True}}

    monkeypatch.setattr(timing, "asic_sdc_for_pnr", derive, raising=False)
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert trace[-2:] == ["segment2", "import"]
    assert len(calls) == 1 and calls[0][1:4] == (project, "top", pdk)
    derived = project / "phase3/librelane/whole/pnr_derived.sdc"
    assert derived.read_text() == "create_clock -period 11 [get_ports clk]\n"
    record = json.loads(derived.with_suffix(".provenance.json").read_text())
    assert record["pnr_time_derivation"] == {"applied": True}
    assert record["step7_sha256"] == "sha256:source"


def test_missing_chip_sdc_derivation_is_unmeasured(monkeypatch, tmp_path):
    R, project, pdk, args, trace = _consumer_stub(monkeypatch, tmp_path)
    import _ppa.timing as timing
    from _atomic_artefact import write_json
    monkeypatch.setattr(R, "_chip_path_requests_pad_ring", lambda *a: True)
    wrapper = project / "phase3/stage3/pnr/chip_top_io.v"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("module chip_top(input clk); top u(clk); endmodule\n")
    write_json(project / "reports/phase3/io_pad_chip_top.json", {
        "chip_top_module": "chip_top",
        "chip_top_verilog": str(wrapper.relative_to(project))})
    monkeypatch.delattr(timing, "asic_sdc_for_pnr", raising=False)
    assert R._run_librelane_consumer_phase3(project, "top", pdk, args) == 2
    assert trace == ["segment1", "11-12", "13", "7-8-10", "prepnr"]
    record = json.loads((project / "reports/orchestrator/phase3_one_shot.json").read_text())
    row = record["steps"][-1]
    assert (record["verdict"], row["name"], row["status"],
            row["reason_class"]) == (
                "NOT_MEASURED", "librelane_segment", "NOT_MEASURED",
                "tool_absent")
    assert "SDC_PNR_DERIVATION_PENDING" in row["detail"]


def test_phase3_main_dispatches_the_flag_before_the_direct_flow(monkeypatch,
                                                                 tmp_path):
    import phase3_one_shot_runner as R
    import _chip_synth_read as CSR
    project = _project(tmp_path / "main")
    calls = []
    monkeypatch.setattr(R._impl_flow, "gate_or_exit", lambda *a, **k: None)
    monkeypatch.setattr(R._impl_flow, "record_after_lock", lambda *a, **k: None)
    monkeypatch.setattr(R._runner_lock, "acquire_or_reenter",
                        lambda *a, **k: object())
    monkeypatch.setattr(R, "_delivery_admission_refusal", lambda *a: None)
    monkeypatch.setattr(R._canonical_admission, "admit_span",
                        lambda *a, **k: SimpleNamespace(admitted=True))
    monkeypatch.setattr(R, "_detect_pdk",
                        lambda *a: SimpleNamespace(name="gf180mcuD",
                                                    tech_lef="", cell_lef="",
                                                    macro_lefs=[]))
    monkeypatch.setattr(R._impl_flow, "scope_refusal_after_pdk",
                        lambda *a: None)
    monkeypatch.setattr(R, "commercial_pdk_fallback_guard",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "declared_pdk_target_guard",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "macro_lef_layer_compat_guard",
                        lambda *a, **k: None)
    monkeypatch.setattr(CSR, "effective_top", lambda *a: "top")
    monkeypatch.setattr(R, "_run_librelane_consumer_phase3",
                        lambda *a: calls.append("consumer") or 37,
                        raising=False)
    monkeypatch.setattr(sys, "argv", ["phase3_one_shot_runner", str(project),
                                      "--librelane", "--pdk", "gf180mcuD",
                                      "--top-name", "top"])
    assert R.main() == 37
    assert calls == ["consumer"]
