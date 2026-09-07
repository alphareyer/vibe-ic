"""Issue #2074 — "where Step-4 evidence lives" has ONE declaration.

`flow/phase1_phase2_phase3.yaml` declares step 4's evidence as one requirement
in the shapes the supported TB paths emit::

    phase2/stage1/sim/*.log OR phase2/stage1/sim/results.xml
    OR phase2/stage1/sim/pass.flag
    OR phase2/stage1/sim_professional/**/results.xml

`design_one_shot_runner.step_step4_functional_evidence` looked only at
`phase2/stage1/sim/results.xml`. A JUnit written to the professional path (the
analog-acceptance producer's, #2064) therefore satisfied the gate and not the
inline step, so the inline verdict could not move and the reason a reader was
given named a file the flow does not require.

Both consumers now resolve the SAME entry, read from the YAML, through the same
`_glob_first`. Structural and chip-AGNOSTIC; no simulator.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import design_one_shot_runner as RUNNER        # noqa: E402
import flow_compliance_check as FLOW           # noqa: E402


def _shim_the_module_the_runner_imports(monkeypatch, **overrides):
    """Replace the MODULE OBJECT the runner will import, not this file's binding.

    `step_step4_functional_evidence` imports `flow_compliance_check` LAZILY,
    inside the step, so it resolves `sys.modules` at call time.
    `monkeypatch.setattr(FLOW, …)` reaches that object only while the two happen
    to be identical. MEASURED 2026-09-07, in the pinned image, in a 110-file
    session: they were NOT identical, both tests below went green on the host
    and red in the image, and the "narrowing" had silently done nothing while
    the assertion read as a real verdict. Patch `sys.modules` and the runner's
    own `import` statement can only resolve to this shim.

    Every override RECORDS that it was called, and the callers assert on that:
    a mutation nobody executed is not a mutation, and it must fail by name
    rather than pass by silence."""
    real = sys.modules["flow_compliance_check"]
    shim = types.ModuleType("flow_compliance_check")
    shim.__dict__.update(real.__dict__)
    for name, fn in overrides.items():
        setattr(shim, name, fn)
    monkeypatch.setitem(sys.modules, "flow_compliance_check", shim)
    return shim

_JUNIT = ("<testsuites><testsuite name='functional' tests='1' failures='0' "
          "errors='0' skipped='0'><testcase name='declared_behavior'/>"
          "</testsuite></testsuites>")

#: The gate's own resolution of one `required_outputs` entry, with no step
#: ledger — exactly what `check_step`'s probe does on a fresh run root.
_NO_BINDING = {"available": False, "reason": "no step ledger"}


def _project(tmp_path: Path) -> Path:
    (tmp_path / "phase2/stage1/rtl").mkdir(parents=True)
    (tmp_path / "phase2/stage1/rtl/dut_core.v").write_text(
        "module dut_core(input clk, output q); assign q = clk; endmodule\n")
    return tmp_path


def _plant(project: Path, rel: str, body: str = _JUNIT) -> Path:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _gate_entry() -> str:
    """The flow YAML's step-4 evidence entry, read here INDEPENDENTLY of the
    program so the two cannot drift into agreement by construction."""
    doc = yaml.safe_load(Path(FLOW.DEFAULT_FLOW_DEF).read_text())
    for step in doc["steps"]:
        if str(step.get("id")) != "4":
            continue
        for entry in step.get("required_outputs") or []:
            if "phase2/stage1/sim/results.xml" in str(entry):
                return str(entry)
    raise AssertionError("the flow declares no step-4 sim-evidence entry")


def _gate_satisfied(project: Path) -> bool:
    satisfied, _ev, _mode, _note, _detail = FLOW._resolve_required_output(
        project, 4, _gate_entry(), _NO_BINDING)
    return bool(satisfied)


# --------------------------------------------------------------------------
# 1. ONE declaration, read from the flow YAML.
# --------------------------------------------------------------------------
def test_the_alternatives_are_the_flow_yamls_own_entry():
    assert FLOW.step4_sim_evidence_alternatives() == [
        a.strip() for a in _gate_entry().split(" OR ") if a.strip()]


def test_the_declaration_still_names_both_homes():
    alts = FLOW.step4_sim_evidence_alternatives()
    assert "phase2/stage1/sim/results.xml" in alts
    assert "phase2/stage1/sim_professional/**/results.xml" in alts


def test_an_unreadable_declaration_raises_instead_of_defaulting(tmp_path):
    """Degrade LOUDLY. An empty alternative list would read as "the run
    produced nothing" and charge the design for a broken flow definition."""
    broken = tmp_path / "flow.yaml"
    broken.write_text("steps:\n  - id: 4\n    required_outputs: []\n")
    with pytest.raises(FLOW.Step4EvidenceUndeclared):
        FLOW.step4_sim_evidence_alternatives(broken)
    absent = tmp_path / "nowhere.yaml"
    with pytest.raises(FLOW.Step4EvidenceUndeclared):
        FLOW.step4_sim_evidence_alternatives(absent)


# --------------------------------------------------------------------------
# 2. A results.xml at EITHER declared path satisfies BOTH consumers.
# --------------------------------------------------------------------------
def test_the_canonical_path_satisfies_both(tmp_path):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim/results.xml")
    assert _gate_satisfied(project)
    assert FLOW.step4_sim_evidence(project) == [
        "phase2/stage1/sim/results.xml"]
    assert RUNNER.step_step4_functional_evidence(
        project, "digital").status == "PASS"


def test_the_professional_path_satisfies_both(tmp_path):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_professional/dut_core/results.xml")
    assert _gate_satisfied(project)
    assert FLOW.step4_sim_evidence(project) == [
        "phase2/stage1/sim_professional/dut_core/results.xml"]
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert inline.status == "PASS", inline.detail
    # …and the PASS cites the transcript it was earned by.
    assert "sim_professional/dut_core/results.xml" in inline.detail


# --------------------------------------------------------------------------
# 3. A results.xml ELSEWHERE satisfies NEITHER.
# --------------------------------------------------------------------------
def test_a_results_xml_elsewhere_satisfies_neither(tmp_path):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_elsewhere/results.xml")
    _plant(project, "reports/results.xml")
    assert not _gate_satisfied(project)
    assert FLOW.step4_sim_evidence(project) == []
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert inline.status == "FAIL", inline.detail
    # the reason names the DECLARATION, not one hand-picked path
    assert "no step-4 simulation evidence at any path the flow declares" \
        in inline.detail
    assert "phase2/stage1/sim_professional/**/results.xml" in inline.detail
    # PROVE-BY-RUN that this BLOCKING step actually stops the run: the step's
    # docstring declares `ENFORCEMENT: blocking`, and a declaration is not a
    # measurement until the runner's own aggregation is shown to carry it.
    assert RUNNER._aggregate_verdict([inline]) == "FAIL"


def test_an_empty_tree_still_fails(tmp_path):
    project = _project(tmp_path)
    assert not _gate_satisfied(project)
    assert RUNNER.step_step4_functional_evidence(
        project, "digital").status == "FAIL"


# --------------------------------------------------------------------------
# 4. MUTATION ARM — narrow the inline path again and the professional-only
#    tree goes red, which is exactly the state #2074 reported.
# --------------------------------------------------------------------------
def test_narrowing_the_inline_path_again_is_red(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_professional/dut_core/results.xml")
    assert RUNNER.step_step4_functional_evidence(
        project, "digital").status == "PASS"

    canonical = "phase2/stage1/sim/results.xml"
    called = []

    def _narrow_alternatives(*_a, **_k):
        called.append("alternatives")
        return [canonical]

    def _narrow_resolve(proj, *_a, **_k):
        called.append("resolve")
        return FLOW._glob_first(proj, canonical)

    _shim_the_module_the_runner_imports(
        monkeypatch,
        step4_sim_evidence_alternatives=_narrow_alternatives,
        step4_sim_evidence=_narrow_resolve)
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert called, (
        "the narrowed derivation was never called — the patch did not reach the "
        "module the runner imports, so this arm proved nothing")
    assert inline.status == "FAIL", (
        "narrowing the inline step back to the canonical path must reproduce "
        "#2074 — if it does not, the inline step is not reading the shared "
        "declaration at all")


def test_a_broken_declaration_is_not_charged_to_the_design(tmp_path,
                                                           monkeypatch):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_professional/dut_core/results.xml")

    called = []

    def _boom(*_a, **_k):
        called.append("boom")
        raise FLOW.Step4EvidenceUndeclared("flow definition unreadable")

    _shim_the_module_the_runner_imports(
        monkeypatch, step4_sim_evidence_alternatives=_boom)
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert called, (
        "the broken declaration was never consulted — the patch did not reach "
        "the module the runner imports")
    assert inline.status == "FAIL"
    assert inline.detail.startswith("NOT_MEASURED:"), inline.detail


# --------------------------------------------------------------------------
# 5. #2073 holds at the inline step too: the professional verdict is the union.
# --------------------------------------------------------------------------
def test_a_failing_professional_sibling_denies_the_inline_pass(tmp_path):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_professional/a_green/results.xml")
    _plant(project, "phase2/stage1/sim_professional/z_red/results.xml",
           _JUNIT.replace("failures='0'", "failures='1'"))
    assert _gate_satisfied(project)          # the artefact is declared-present
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert inline.status == "FAIL", inline.detail
    assert "sim_professional/z_red/results.xml" in inline.detail
    assert RUNNER._aggregate_verdict([inline]) == "FAIL"
    assert inline.extras.get("fallback_skill") == "testbench-gen"


def test_a_sibling_that_produced_nothing_is_named_in_the_inline_pass(tmp_path):
    project = _project(tmp_path)
    _plant(project, "phase2/stage1/sim_professional/a_green/results.xml")
    (project / "phase2/stage1/sim_professional/never_ran").mkdir(parents=True)
    inline = RUNNER.step_step4_functional_evidence(project, "digital")
    assert inline.status == "PASS", inline.detail
    assert "NOT_MEASURED" in inline.detail
    assert "sim_professional/never_ran" in inline.detail
    assert RUNNER._aggregate_verdict([inline]) == "PASS"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
