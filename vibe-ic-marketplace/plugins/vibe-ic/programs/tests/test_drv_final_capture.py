"""R-0929-DRV-IDENTITY — the FINAL post-stream DRV capture, and only it,
decides the IC's DRV(tran/cap/fanout) verdict.

What this file pins, on top of `test_drv_post_stream_capture.py`:

* The GDS link is GEOMETRIC.  The admission record only NAMES the DEF the
  gds step streamed; step 37.3's XOR is the flow's evidence that the shipped
  GDS's design layers are that stream.  No XOR, an XOR of another GDS, or one
  that compared nothing is NOT_MEASURED; an XOR against another DEF, or any
  design-layer difference, is FAIL.
* Every link value is read from the re-hashed flow record, never from a value
  the capture plan copied beside it.
* The LVS verdict names the inputs it compared (step 31 writes them into its
  own verdict), so a verdict left by an earlier LVS is not this compare's.
* The runner: the final DRV capture waits for the XOR it binds; the LVS input
  record is cleared on every producer mode; the in-flow capture writes its own
  receipt; and Step 23's clause judges the post-stream capture only.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import yaml

_PROGRAMS = Path(__file__).resolve().parent.parent
_TESTS = Path(__file__).resolve().parent
for _p in (str(_PROGRAMS), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drv_signoff_judge as drv  # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402
from test_drv_post_stream_capture import _streamed  # noqa: E402
from test_drv_signoff_judge import _synthetic_scene_profile  # noqa: E402,F401 (autouse)

_FLOW = _PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"
_RECEIPT = "reports/phase3/sta/drv_signoff.json"


def _verdict(bundle):
    result = drv.judge(bundle)
    return result["verdict"], result["failures"], result["not_measured"]


# --- the GDS link is geometric -----------------------------------------------

def test_the_full_chain_passes(tmp_path):
    verdict, fails, missing = _verdict(_streamed(tmp_path)[1])
    assert verdict == "PASS", fails + missing


def test_an_admission_without_the_stream_out_xor_is_not_measured(tmp_path):
    verdict, fails, missing = _verdict(_streamed(tmp_path, xor=False)[1])
    assert verdict == "NOT_MEASURED" and not fails
    assert "GDS stream-out XOR record absent" in missing


def test_an_xor_against_another_def_fails(tmp_path):
    verdict, fails, _ = _verdict(_streamed(tmp_path, xor_def="c" * 64)[1])
    assert verdict == "FAIL"
    assert ("shipped GDS's reference stream derives from a DEF other than the "
            "judged DEF") in fails


def test_a_design_layer_difference_fails(tmp_path):
    diff = [{"layer": 34, "datatype": 0, "differences": 3}]
    verdict, fails, _ = _verdict(_streamed(tmp_path, xor_differences=diff,
                                           xor_verdict="FAIL")[1])
    assert verdict == "FAIL"
    assert ("shipped GDS differs from the judged DEF's stream-out on 1 design "
            "layer(s)") in fails


def test_an_xor_of_another_gds_is_not_evidence(tmp_path):
    verdict, fails, missing = _verdict(_streamed(tmp_path, xor_gds="b" * 64)[1])
    assert verdict == "NOT_MEASURED" and not fails
    assert "GDS stream-out XOR measured another GDS" in missing


def test_an_xor_that_compared_no_layer_is_not_measured(tmp_path):
    verdict, _, missing = _verdict(_streamed(tmp_path, xor_layers=0)[1])
    assert verdict == "NOT_MEASURED"
    assert "GDS stream-out XOR compared no design layer" in missing


def test_an_xor_that_did_not_pass_without_a_layer_difference_is_not_measured(tmp_path):
    verdict, _, missing = _verdict(_streamed(tmp_path, xor_verdict="NOT_MEASURED")[1])
    assert verdict == "NOT_MEASURED"
    assert "GDS stream-out XOR did not pass (verdict NOT_MEASURED)" in missing


# --- values come from the records, not from the plan -----------------------------

def test_a_plan_copy_cannot_overrule_the_admission_record(tmp_path):
    _, bundle = _streamed(tmp_path, gds_def="f" * 64)
    gds = bundle["identity"]["derivation"]["gds"]
    gds["streamed_from_def_sha256"] = bundle["identity"]["artifacts"]["def"]["sha256"]
    verdict, fails, _ = _verdict(bundle)
    assert verdict == "FAIL"
    assert "streamed GDS derives from a DEF other than the judged DEF" in fails


def test_a_plan_copy_cannot_overrule_the_lvs_verdict_record(tmp_path):
    _, bundle = _streamed(tmp_path, lvs_status="FAIL")
    lvs = bundle["identity"]["derivation"]["lvs"]
    lvs["verdict"], lvs["compare_performed"] = "PASS", True
    verdict, _, missing = _verdict(bundle)
    assert verdict == "NOT_MEASURED"
    assert "LVS did not prove the layout matches the netlist (verdict FAIL)" in missing


# --- the LVS verdict is this compare's ------------------------------------------

def test_a_verdict_that_names_no_compared_inputs_is_not_measured(tmp_path):
    verdict, fails, missing = _verdict(_streamed(tmp_path, verdict_inputs=None)[1])
    assert verdict == "NOT_MEASURED" and not fails
    assert "LVS verdict is not bound to the inputs it compared" in missing


def test_a_verdict_of_another_compare_is_not_measured(tmp_path):
    verdict, _, missing = _verdict(_streamed(tmp_path, verdict_inputs="other")[1])
    assert verdict == "NOT_MEASURED"
    assert ("LVS verdict belongs to another compare than the recorded LVS "
            "inputs") in missing


def test_step31_writes_the_inputs_it_compared_into_its_verdict(tmp_path):
    compared = {"layout_def": {"path": "/l.def", "sha256": "1" * 64},
                "schematic_netlist": {"path": "/n.v", "sha256": "2" * 64}}
    record = tmp_path / "reports/phase3/lvs_inputs.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({**compared, "selected_because": "x"}))
    R._write_lvs_verdict(tmp_path, "PASS", "LVS_MATCH", "fixture compare")
    doc = json.loads((tmp_path / "reports/phase3/lvs_verdict.json").read_text())
    assert doc["compared_inputs"] == compared
    record.unlink()                         # a verdict with no compare record
    R._write_lvs_verdict(tmp_path, "PASS", "LVS_MATCH", "fixture compare")
    doc = json.loads((tmp_path / "reports/phase3/lvs_verdict.json").read_text())
    assert "compared_inputs" not in doc


def test_every_lvs_producer_mode_starts_without_an_earlier_input_record(
        tmp_path, monkeypatch):
    """A LibreLane-produced LVS (step 31 switch) records no inputs; the
    direct run's record must not survive into its verdict."""
    stale = tmp_path / "reports/phase3/lvs_inputs.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"layout_def": {"sha256": "old"}}')
    produced = R.StepResult("lvs", "PASS", 0.0, "LibreLane step 31 (lvs): PASS")
    monkeypatch.setattr(R, "_step31_dispatch", lambda *a, **k: produced)
    assert R.step_lvs(tmp_path, "top", object(), "c") is produced
    assert not stale.exists()


# --- the runner -----------------------------------------------------------------

def test_the_final_drv_capture_waits_for_the_gds_xor(tmp_path, monkeypatch):
    order = []

    def gate(project, name, program, out_rel, extra_argv=()):
        order.append(name)
        return R.StepResult(name, "PASS", 0.0, "")

    monkeypatch.setattr(R, "_run_declared_signoff_gate", gate)
    R.step_declared_signoff_gates(tmp_path, drv_after=lambda: order.append("gds_xor"))
    assert "drv_signoff" in order
    assert order.index("gds_xor") == order.index("drv_signoff") - 1
    assert order.count("gds_xor") == 1


def test_the_runner_hands_the_xor_job_to_the_final_gates():
    source = Path(R.__file__).read_text()
    call = source[source.index("plan.extend(step_declared_signoff_gates("):]
    call = call[:call.index("))") + 2]
    assert "drv_after=(xor_job.result if xor_job is not None else None)" in call


def _step23_drv_clause() -> list[str]:
    flow = yaml.safe_load(_FLOW.read_text())
    step = next(s for s in flow["steps"] if str(s.get("id")) == "23")
    found = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.startswith("drv_signoff_judge "):
            found.append(node)

    walk(step)
    assert len(found) == 1, found
    return shlex.split(found[0])


def test_step23_judges_only_the_final_capture():
    argv = _step23_drv_clause()
    assert argv[argv.index("--capture-point") + 1] == "post_stream"
    assert argv[argv.index("--json") + 1] == _RECEIPT
    post = [g for g in R._DECLARED_SIGNOFF_GATES if g[0] == "drv_signoff"]
    pre = [g for g in R._PRESTREAM_GATES if g[0] == "drv_signoff"]
    assert [g[2] for g in post] == [_RECEIPT]
    assert [g[2] for g in pre] and all(g[2] != _RECEIPT for g in pre)


def test_an_in_flow_pass_is_never_step23s_verdict(tmp_path, monkeypatch):
    """The in-flow capture can PASS (it binds what exists before stream-out);
    judged by Step 23's own clause it is NOT_MEASURED."""
    from test_drv_wave57_identity_and_step32 import (_plan_identity,
                                                     _with_plan_identity)
    _, plan = _plan_identity(tmp_path, monkeypatch)
    bundle = _with_plan_identity(tmp_path, plan)
    assert drv.judge(bundle)["verdict"] == "PASS"          # the in-flow verdict
    source = tmp_path / "in_flow_bundle.json"
    source.write_text(json.dumps(bundle))
    argv = _step23_drv_clause()[1:]
    argv[argv.index(".")] = str(source)
    argv[argv.index("--json") + 1] = str(tmp_path / "step23.json")
    assert drv.main(argv) == 1
    doc = json.loads((tmp_path / "step23.json").read_text())
    assert doc["verdict"] != "PASS"
    assert ("gate judges the post_stream capture but the bundle is in_flow"
            in doc["not_measured"])
