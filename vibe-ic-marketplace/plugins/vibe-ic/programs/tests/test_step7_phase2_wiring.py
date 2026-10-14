"""Regression tests for the Phase-2 Step-7 producer seam."""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as D  # noqa: E402
import step_preflight as S  # noqa: E402
import execution_policy as EP  # noqa: E402
from _ppa import timing as T  # noqa: E402


def test_phase2_declares_and_wires_canonical_step7_site():
    sites = dict(S.RUNNER_PLANS["design_one_shot_runner"].sites)
    assert sites["asic_sdc"] == ("7",)
    source = (PROGRAMS / "design_one_shot_runner.py").read_text()
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
    assert "step_asic_sdc" in names
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [node for node in ast.walk(main)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "gate"]
    assert any(any(isinstance(arg, ast.Constant)
                   and arg.value == "asic_sdc" for arg in call.args)
               for call in calls)
    step7 = next(call for call in calls
                 if any(isinstance(arg, ast.Constant)
                        and arg.value == "asic_sdc" for arg in call.args))
    assert any(kw.arg == "_use_controller"
               and isinstance(kw.value, ast.Constant)
               and kw.value.value is False
               for kw in step7.keywords)


def test_step7_direct_path_uses_single_canonical_emitter(tmp_path, monkeypatch):
    calls = []
    pdk_calls = []
    pdk = SimpleNamespace(name="test", liberty="/pdk/nom.lib")
    monkeypatch.setattr(
        EP, "dispatch_fixed_step",
        lambda project, step, *, parameters: None,
    )

    def resolve_pdk(project, pdk_name):
        pdk_calls.append((project, pdk_name))
        return pdk

    monkeypatch.setattr(D, "_phase2_pdk_config", resolve_pdk)
    monkeypatch.setattr(D._pl, "constraints_dir",
                        lambda project: Path(project) / "constraints")
    fake = {
        "path": "constraints/chip_top.asic.sdc",
        "sha256": "a" * 64,
        "design_staged": False,
    }
    def emit(*args, **kwargs):
        calls.append((args, kwargs))
        path = Path(args[1]) / fake["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("create_clock -period 10 [get_ports clk]\n")
        return fake
    monkeypatch.setattr(T, "emit_step7_asic_sdc", emit)
    def pvt(project, _pdk, _container):
        path = Path(project) / "constraints/pvt_matrix.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"corners": [{"label": "TT"}, {"label": "SS"}]}\n')
        return path, 2
    monkeypatch.setattr(D, "_emit_step7_pvt_matrix", pvt)
    result = D.step_asic_sdc(tmp_path, "chip_top", "container")
    assert result.status == "PASS", result.detail
    assert pdk_calls == [(tmp_path, None)]
    assert len(calls) == 1
    assert calls[0][0][1:] == (tmp_path, "chip_top", pdk, "container")
    assert calls[0][1] == {}
    assert (tmp_path / fake["path"]).is_file()
    assert (tmp_path / "constraints/pvt_matrix.json").is_file()


def test_default_not_measured_frontend_result_falls_through_to_emitter(
        tmp_path, monkeypatch):
    """A source-bound placeholder cannot strand the canonical default path."""
    dispatch_calls = []
    pdk_calls = []
    pdk = SimpleNamespace(name="test", liberty="/pdk/nom.lib")

    def dispatch_fixed_step(project, step, *, parameters):
        dispatch_calls.append((project, step, parameters))
        return {"status": "NOT_MEASURED", "reason": "NO_RUNNABLE_ADAPTER"}

    monkeypatch.setattr(EP, "dispatch_fixed_step", dispatch_fixed_step)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default",
                                     "request_receipt": {"request_digest": "d" * 64}},
                         "project": tmp_path.resolve()})
    def resolve_pdk(project, pdk_name):
        pdk_calls.append((project, pdk_name))
        return pdk

    monkeypatch.setattr(D, "_phase2_pdk_config", resolve_pdk)
    monkeypatch.setattr(D._pl, "constraints_dir",
                        lambda project: Path(project) / "constraints")
    called = []
    from _ppa import timing as T
    def emit(*args, **kwargs):
        called.append((args, kwargs))
        path = Path(args[1]) / "constraints/chip_top.asic.sdc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("create_clock -period 10 [get_ports clk]\n")
        return {"path": "constraints/chip_top.asic.sdc", "sha256": "a" * 64}
    monkeypatch.setattr(T, "emit_step7_asic_sdc", emit)
    monkeypatch.setattr(D, "_emit_step7_pvt_matrix", lambda project, pdk, container: (
        (Path(project) / "constraints/pvt_matrix.json").write_text("{}\n") and
        (Path(project) / "constraints/pvt_matrix.json", 2)))
    result = D.step_asic_sdc(tmp_path, "chip_top", "container")
    assert result.status == "PASS"
    assert dispatch_calls == [(tmp_path, "7", {
        "top": "chip_top", "container": "container", "ic_class": None,
    })]
    assert pdk_calls == [(tmp_path, None)]
    assert len(called) == 1
    assert called[0][0][1:] == (tmp_path, "chip_top", pdk, "container")
    assert called[0][1] == {}


def test_default_ordinary_step7_placeholder_releases_preflight_gate(
        tmp_path, monkeypatch):
    """The live Controller seam must let Default reach the canonical fn.

    ``step_preflight.gate`` asks ``dispatch_ordinary_site`` first.  A
    source-bound Step-7 adapter is intentionally NOT_MEASURED, so returning
    that row would strand the direct producer forever.  Ultra and measured
    failures remain owned by the Controller.
    """
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default"}})
    runtime = {"policy": {"mode": "default",
                           "request_receipt": {"request_digest": "d" * 64}},
               "project": tmp_path.resolve()}
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("Default Step 7 must use canonical producer")))
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "asic_sdc", lambda *a: None) is None

    placeholder = SimpleNamespace(
        status="NOT_MEASURED",
        extras={"execution_results": [{
            "status": "NOT_MEASURED", "reason": "NO_RUNNABLE_ADAPTER"}]})
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: placeholder)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "ultra"}})
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "asic_sdc", lambda *a: None) is placeholder

    measured_fail = SimpleNamespace(status="FAIL", extras={})
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: measured_fail)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default",
                                     "request_receipt": {"request_digest": "d" * 64}},
                         "project": tmp_path.resolve()})
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "asic_sdc", lambda *a: None) is None


def test_default_ordinary_step9_placeholder_releases_preflight_gate(
        tmp_path, monkeypatch):
    """Default Step 9 goes straight to the caller-owned Yosys producer."""
    placeholder = SimpleNamespace(
        status="NOT_MEASURED",
        extras={"execution_results": [{
            "status": "NOT_MEASURED", "reason": "NO_RUNNABLE_ADAPTER"}]})
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: placeholder)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default",
                                     "request_receipt": {"request_digest": "d" * 64}},
                         "project": tmp_path.resolve()})
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is None

    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "ultra"}})
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is placeholder

    measured_fail = SimpleNamespace(status="FAIL", extras={})
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: measured_fail)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default"}})
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is None


def test_default_step9_does_not_create_a_controller_fallback_token(
        tmp_path, monkeypatch):
    """Default must not create a per-row Controller fallback token."""
    placeholder = SimpleNamespace(
        status="NOT_MEASURED",
        extras={"execution_results": [{
            "status": "NOT_MEASURED", "reason": "NO_RUNNABLE_ADAPTER"}]})
    runtime = {"policy": {"mode": "default",
                           "request_receipt": {"request_digest": "d" * 64}},
               "project": tmp_path.resolve()}
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: placeholder)
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is None
    assert "program_first_fallback_sites" not in runtime
    assert EP.consume_program_first_fallback(tmp_path, "yosys_synth") is None


def test_default_step9_fallback_marker_is_not_consumed_by_ultra(
        tmp_path, monkeypatch):
    runtime = {"policy": {"mode": "ultra"}, "project": tmp_path.resolve(),
               "program_first_fallback_sites": {
                   ("yosys_synth", str(tmp_path.resolve()))}}
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    assert EP.consume_program_first_fallback(tmp_path, "yosys_synth") is None
    assert runtime["program_first_fallback_sites"]


def test_default_step9_worker_error_does_not_release_to_native_producer(
        tmp_path, monkeypatch):
    worker_error = SimpleNamespace(
        status="NOT_MEASURED",
        extras={"execution_results": [{
            "status": "NOT_MEASURED", "reason": "ADAPTER_ERROR"}]})
    runtime = {"policy": {"mode": "default",
                           "request_receipt": {"request_digest": "d" * 64}},
               "project": tmp_path.resolve()}
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: worker_error)
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is None
    assert "program_first_fallback_sites" not in runtime


def test_default_step9_fallback_requires_placeholder_reason(tmp_path, monkeypatch):
    for reason in ("ADAPTER_ERROR", "PROCESS_ERROR", "GATE_FAIL", "worker error"):
        worker_error = SimpleNamespace(
            status="NOT_MEASURED",
            extras={"execution_results": [{
                "status": "NOT_MEASURED", "reason": reason}]})
        runtime = {"policy": {"mode": "default",
                               "request_receipt": {"request_digest": "d" * 64}},
                   "project": tmp_path.resolve()}
        monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                            lambda *args, _row=worker_error, **kwargs: _row)
        monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
        assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                         "yosys_synth", lambda *a: None) is None
        assert "program_first_fallback_sites" not in runtime


def test_default_step9_fallback_requires_request_digest(tmp_path, monkeypatch):
    placeholder = SimpleNamespace(
        status="NOT_MEASURED",
        extras={"execution_results": [{
            "status": "NOT_MEASURED", "reason": "NO_RUNNABLE_ADAPTER"}]})
    runtime = {"policy": {"mode": "default"}, "project": tmp_path.resolve()}
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: placeholder)
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    assert EP.dispatch_ordinary_site(tmp_path, "design_one_shot_runner",
                                     "yosys_synth", lambda *a: None) is None
    assert "program_first_fallback_sites" not in runtime


def test_phase3_step9_consumes_default_fallback_marker():
    """Phase-3 synth must have an explicit one-shot producer bypass."""
    import phase3_one_shot_runner as P3
    source = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    tree = ast.parse(source)
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "step_synth")
    segment = ast.get_source_segment(source, fn)
    assert segment is not None
    assert "_fallback_handoff" in segment
    assert P3.step_synth.__name__ == "step_synth"
    retry = [node for node in ast.walk(fn)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "step_synth"]
    assert any(any(kw.arg == "_fallback_handoff" for kw in call.keywords)
               for call in retry)


def test_phase3_step9_rejects_forged_fallback_handoff(tmp_path, monkeypatch):
    """An arbitrary private-looking value must keep Controller dispatch on."""
    import execution_production as production
    import phase3_one_shot_runner as P3
    runtime = {"policy": {"mode": "default",
                           "request_receipt": {"request_digest": "d" * 64}},
               "project": tmp_path.resolve()}
    monkeypatch.setattr(EP, "_ordinary_runtime", runtime)
    calls = []
    monkeypatch.setattr(production, "dispatch_site",
                        lambda *args, **kwargs: (
                            calls.append((args, kwargs)) or
                            {"status": "ADOPTED", "reason": "controller"}))
    result = P3.step_synth(tmp_path, "top", None, "container",
                           _fallback_handoff=True)
    assert result.status == "PASS"
    assert calls, "forged handoff must not bypass Controller"


def test_live_preflight_gate_calls_step7_fn_after_default_placeholder(
        tmp_path, monkeypatch):
    """Exercise the actual gate seam, not only its dispatch helper."""
    decision = S.Decision("design_one_shot_runner", "asic_sdc", ["7"],
                          verdict="READY", allow=True, detail="ready")
    monkeypatch.setattr(S, "decide", lambda *args, **kwargs: decision)
    monkeypatch.setattr(S, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(EP, "_ordinary_runtime",
                        {"policy": {"mode": "default",
                                     "request_receipt": {"request_digest": "d" * 64}},
                         "project": tmp_path.resolve()})
    monkeypatch.setattr(EP, "dispatch_ordinary_rows",
                        lambda *args, **kwargs: SimpleNamespace(
                            status="NOT_MEASURED",
                            extras={"execution_results": [{
                                "status": "NOT_MEASURED",
                                "reason": "NO_RUNNABLE_ADAPTER"}]}))
    called = []
    sentinel = SimpleNamespace(status="NOT_MEASURED")

    def direct(*args, **kwargs):
        called.append((args, kwargs))
        return sentinel

    got = S.gate(tmp_path, "design_one_shot_runner", "asic_sdc",
                 lambda *args: None, direct, tmp_path)
    assert got is sentinel
    assert called and called[0][0] == (tmp_path,)


def _release_runtime(project, monkeypatch, *, caller_token=True):
    """SOURCE_CONTROL ordinary context; real opaque-token policy APIs."""
    digest = 'c' * 64
    markers = {('asic_sdc', str(project.resolve()), digest)}
    if caller_token:
        markers.add(('yosys_synth', str(project.resolve()), digest))
    runtime = {'project': project.resolve(), 'policy': {
        'mode': 'default', 'request_receipt': {'request_digest': digest}},
        'program_first_fallback_sites': markers}
    monkeypatch.setattr(EP, '_ordinary_runtime', runtime)
    other = EP.consume_program_first_fallback(project, 'asic_sdc')
    assert other is not None and EP.is_program_first_fallback_handoff(other, project, 'asic_sdc')
    return runtime, other


def test_total_release_none_preserves_other_active_handoff(tmp_path, monkeypatch):
    runtime, other = _release_runtime(tmp_path, monkeypatch)
    before = set(runtime['_active_program_first_fallback_handoffs'])
    EP.release_program_first_fallback(None)
    assert runtime['_active_program_first_fallback_handoffs'] == before
    assert EP.is_program_first_fallback_handoff(other, tmp_path, 'asic_sdc')
    monkeypatch.setattr(EP, '_ordinary_runtime', None)
    assert EP.release_program_first_fallback(None) is None


def test_total_release_opaque_removes_only_itself_and_cannot_be_reused(tmp_path, monkeypatch):
    runtime, other = _release_runtime(tmp_path, monkeypatch)
    owned = EP.consume_program_first_fallback(tmp_path, 'yosys_synth')
    assert owned is not None and EP.is_program_first_fallback_handoff(owned, tmp_path, 'yosys_synth')
    EP.release_program_first_fallback(owned)
    assert runtime['_active_program_first_fallback_handoffs'] == {other}
    assert not EP.is_program_first_fallback_handoff(owned, tmp_path, 'yosys_synth')
    assert EP.consume_program_first_fallback(tmp_path, 'yosys_synth') is None
    EP.release_program_first_fallback(owned)
    assert EP.is_program_first_fallback_handoff(other, tmp_path, 'asic_sdc')


@pytest.mark.parametrize('caller_token', [False, True], ids=['none', 'opaque'])
@pytest.mark.parametrize('exit_path', ['return', 'refusal', 'unexpected'])
def test_phase2_caller_finally_delegates_total_release_and_preserves_scope(
        tmp_path, monkeypatch, caller_token, exit_path):
    """Actual Phase2 caller/consume/release; the native producer is NOT_RUN."""
    import json
    import os
    import librelane_contract as LC
    import phase3_one_shot_runner as P3

    project = tmp_path / 'neutral_cleanup'
    rtl = project / 'phase2/stage1/rtl'; rtl.mkdir(parents=True)
    rtl.joinpath('neutral_cleanup_top.v').write_text(
        'module neutral_cleanup_top(input a, output y); assign y=a; endmodule\n')
    runtime, other = _release_runtime(project, monkeypatch, caller_token=caller_token)
    monkeypatch.setattr(LC, 'selected_mode', lambda *a: 'librelane')
    monkeypatch.setattr(D, '_phase2_pdk_config', lambda *a: SimpleNamespace(name='source-control'))
    producer_calls = []
    def producer(*args, **kwargs):
        handoff = kwargs['_fallback_handoff']
        producer_calls.append(handoff)
        assert (handoff is not None) is caller_token
        if handoff is not None:
            assert EP.is_program_first_fallback_handoff(handoff, project, 'yosys_synth')
        if exit_path == 'refusal':
            raise LC.Refusal('SOURCE_CONTROL_REFUSAL', 'no native execution')
        if exit_path == 'unexpected':
            raise RuntimeError('SOURCE_CONTROL_UNEXPECTED')
        return D.StepResult('source_control_producer', 'NOT_MEASURED', 0, 'native producer NOT_RUN',
                            reason_class=D._V.ReasonClass.NOT_EXECUTED)
    monkeypatch.setattr(P3, 'step_synth', producer)
    actual_release = EP.release_program_first_fallback
    release_calls = []
    def release(handoff):
        release_calls.append(handoff)
        return actual_release(handoff)
    monkeypatch.setattr(EP, 'release_program_first_fallback', release)
    if exit_path == 'unexpected':
        with pytest.raises(RuntimeError, match='SOURCE_CONTROL_UNEXPECTED'):
            D.step_yosys_synth(project, 'neutral_cleanup_top', 'unused')
        status = 'PROPAGATED_RUNTIME_ERROR'
    else:
        row = D.step_yosys_synth(project, 'neutral_cleanup_top', 'unused')
        status = row.status
        assert status == ('FAIL' if exit_path == 'refusal' else 'NOT_MEASURED'), row
    assert len(producer_calls) == 1
    owned = producer_calls[0]
    other_valid = EP.is_program_first_fallback_handoff(other, project, 'asic_sdc')
    stale_valid = EP.is_program_first_fallback_handoff(owned, project, 'yosys_synth')
    reuse = EP.consume_program_first_fallback(project, 'yosys_synth')
    target = os.environ.get('ISSUE3101_VALUES_DIR')
    if target:
        folder = Path(target); folder.mkdir(parents=True, exist_ok=True)
        name = os.environ['PYTEST_CURRENT_TEST'].rsplit(' (', 1)[0].split('::', 1)[1]
        (folder / (name + '.json')).write_text(json.dumps({
            'caller_token': caller_token, 'exit_path': exit_path, 'status': status,
            'producer_calls': len(producer_calls), 'release_calls': len(release_calls),
            'released_own_value': bool(release_calls and release_calls[0] is owned),
            'other_handoff_still_active': other_valid, 'owned_still_active': stale_valid,
            'token_reused': reuse is not None,
            'remaining_active_count': len(runtime['_active_program_first_fallback_handoffs']),
            'native_execution': False}, indent=2) + '\n')
    assert release_calls == [owned], release_calls
    assert runtime['_active_program_first_fallback_handoffs'] == {other}
    assert other_valid and not stale_valid and reuse is None
