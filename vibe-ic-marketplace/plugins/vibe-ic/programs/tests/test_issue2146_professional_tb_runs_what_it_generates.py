"""vibe-ic#2146 — the professional-TB producer runs what it generates, or
refuses by name.

WHAT WAS BROKEN, MEASURED
=========================
`design_one_shot_runner.step_professional_tb_gen` (flow step 4, Phase-2
"Simulation") calls `professional_tb_gen.generate()`, which writes a complete
cocotb bundle into `phase2/stage1/sim_professional/<top>/` — `tb_<top>.py`,
`Makefile`, the L28 coverage model, the L29 SVA, the verification plan. It then
ran the simulator only for `dut_kind in (serial_stream, parallel_arith,
expert_reference)`. Every other class had its bundle generated and left, and
NOTHING IN THE BUNDLE SAID SO: the only trace was the absence of a
`results.xml`, which a downstream reader cannot tell apart from "the suite ran
and produced nothing".

Measured 2026-09-07 over every project root on one host carrying a
`sim_professional` tree: 346 roots, 198 with an unmeasured sibling suite of any
kind, and 145 carrying a GENERATED professional bundle (a Makefile beside a
`tb_<top>.py`) that produced no transcript at all. Three of those 145 were PASS
at Step 4 on their sibling unit-TB suite alone — the three the issue names, and
no others. (Lane czsimbridge measured 331 / 194 four hours earlier on the same
host; the corpus is live lane trees and it grew between the two readings.)

Two further facts, both measured in the pinned image (cocotb 2.2.0.dev, Icarus
14.0) and both invisible until the bundle was actually run:

  * the generic-class scaffold declared its unfilled state by raising
    `cocotb.result.TestSkip`. cocotb 2.x keeps `cocotb.result` as an empty
    shim, so that raise is `AttributeError: module 'cocotb' has no attribute
    'result'` and the transcript reads failures=1 — a FUNCTIONAL FAILURE OF
    THE DESIGN for a testbench that never reached the design.
  * `_professional_tb_exec_site` asked `_tool_in_container`, which in LOCAL
    exec mode (no docker client on PATH — a run INSIDE the image) executes on
    the local filesystem and therefore answered True for a container nothing
    had entered. The site was recorded as "container" and the run dispatched
    through a real `docker exec` argv: rc=127, no log, no transcript.

WHAT THESE TESTS PIN
====================
  * every generated bundle is either MEASURED (a parsable JUnit) or REFUSED BY
    NAME (a `professional_tb_refusal.json` naming the suite and the reason) —
    never silent;
  * the refusal names the bundle and carries a reason;
  * a stale refusal does not outlive the transcript that answers it;
  * the emitted unfilled hook binds to a skip API the running cocotb HAS, and
    the scaffold still refuses to be a vacuous pass;
  * the exec site does not claim a container in local exec mode;
  * a run whose transcript is a SKIP is still not credited as a pass.

The bundle-running arms are pure-python: the simulator is replaced at the
runner's own dispatch seams, so these tests state the CONTROL FLOW invariant
and no test here needs a simulator. The end-to-end evidence that the bundles
really run in the pinned image is the lane's acceptance measurement.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import design_one_shot_runner as D            # noqa: E402
import professional_tb_gen as PTB             # noqa: E402


REFUSAL = "professional_tb_refusal.json"


# --------------------------------------------------------------------------
# fixtures — a project whose generator emits a bundle, with the simulator
# replaced at the runner's own seams.
# --------------------------------------------------------------------------
def _bundle_project(tmp_path: Path, dut_kind: str, monkeypatch,
                    *, out_name: str = "dut") -> Path:
    """A project + a stubbed `professional_tb_gen` that declares one bundle."""
    out = tmp_path / "phase2/stage1/sim_professional" / out_name
    out.mkdir(parents=True)
    (out / "tb_dut.py").write_text("# generated\n")
    (out / "Makefile").write_text("# generated\n")
    import types
    gen = {"status": "PASS", "ic_class": "x", "dut_kind": dut_kind,
           "reference_model_tier": "t", "out_dir": str(out),
           "files": ["tb_dut.py", "Makefile"]}
    monkeypatch.setitem(sys.modules, "professional_tb_gen",
                        types.SimpleNamespace(generate=lambda _p: gen))
    return out


def _no_simulator(monkeypatch):
    monkeypatch.setattr(D, "_professional_tb_exec_site", lambda _c: None)


def _simulator_that_writes(monkeypatch, xml: str, *, marker: str = "",
                           rc: int = 0):
    """Dispatch site that behaves like a simulator run: writes `xml` as the
    bundle's results.xml (or writes nothing when `xml` is empty)."""
    def _run(cmd, cwd=None, timeout=600, env=None):
        d = Path(cwd) if cwd is not None else Path(".")
        if xml:
            (d / "results.xml").write_text(xml)
        return rc, marker, ""
    monkeypatch.setattr(D, "_professional_tb_exec_site", lambda _c: "host")
    monkeypatch.setattr(D, "_run", _run)


_SKIPPED_XML = ('<testsuites><testsuite name="tb_dut" tests="1" failures="0" '
                'errors="0" skipped="1"><testcase name="t">'
                '<skipped message="Test was skipped"/></testcase>'
                '</testsuite></testsuites>')
_PASS_XML = ('<testsuites><testsuite name="tb_dut" tests="1" failures="0" '
             'errors="0" skipped="0"><testcase name="t"/>'
             '</testsuite></testsuites>')
_FAIL_XML = ('<testsuites><testsuite name="tb_dut" tests="2" failures="1" '
             'errors="0" skipped="0"><testcase name="t"><failure/></testcase>'
             '<testcase name="u"/></testsuite></testsuites>')


def _gate(project: Path) -> dict:
    return json.loads(
        (project / "reports/phase2/gates/professional_tb.json").read_text())


# --------------------------------------------------------------------------
# THE INVARIANT
# --------------------------------------------------------------------------
@pytest.mark.parametrize("dut_kind", ["generic", "serial_stream",
                                      "parallel_arith", "expert_reference"])
def test_DEFECT_every_generated_bundle_is_measured_or_refused(
        tmp_path, monkeypatch, dut_kind):
    """RED on origin/main for dut_kind='generic': the bundle was generated,
    never run, and the directory carried neither a transcript nor a refusal."""
    out = _bundle_project(tmp_path, dut_kind, monkeypatch)
    _no_simulator(monkeypatch)
    D.step_professional_tb_gen(tmp_path, "dut", "some-container")
    # Asserted off the TREE first, so the control at the pre-fix commit fails
    # on the INVARIANT and not on a helper that does not exist there yet.
    assert (out / "results.xml").is_file() or (out / REFUSAL).is_file(), (
        f"{dut_kind}: bundle left SILENT — generated "
        f"{sorted(q.name for q in out.iterdir())} and neither ran it nor said "
        "why not")
    ok, how = D.professional_tb_bundle_accounted(out)
    assert (ok, how) == (True, "refusal")


def test_DEFECT_the_generic_bundle_is_dispatched_to_the_simulator(
        tmp_path, monkeypatch):
    """RED on origin/main: the generic class never reached the dispatch site."""
    out = _bundle_project(tmp_path, "generic", monkeypatch)
    _simulator_that_writes(monkeypatch, _SKIPPED_XML)
    step = D.step_professional_tb_gen(tmp_path, "dut", "some-container")
    assert (out / "results.xml").is_file(), step.detail
    assert _gate(tmp_path)["ran_cocotb"] is True
    assert D.professional_tb_bundle_accounted(out) == (True, "transcript")


def test_the_refusal_names_the_suite_and_the_reason(tmp_path, monkeypatch):
    out = _bundle_project(tmp_path, "generic", monkeypatch, out_name="widget")
    _no_simulator(monkeypatch)
    step = D.step_professional_tb_gen(tmp_path, "dut", "some-container")
    rec = json.loads((out / REFUSAL).read_text())
    assert rec["suite"] == "widget"
    assert rec["reason"], rec
    assert rec["refused_by"].endswith("step_professional_tb_gen")
    assert "widget" in step.detail and "REFUSED BY NAME" in step.detail
    assert _gate(tmp_path)["run_refusal"]["suite"] == "widget"


def test_a_dispatch_that_never_started_is_refused_by_that_name(
        tmp_path, monkeypatch):
    """rc=127 is 'the simulate path did not start', not 'the suite is empty'.
    Measured in the pinned image: the supervised dispatch built a real
    `docker exec` argv inside the image and returned 127."""
    out = _bundle_project(tmp_path, "serial_stream", monkeypatch)
    _simulator_that_writes(monkeypatch, "", rc=127)
    D.step_professional_tb_gen(tmp_path, "dut", "some-container")
    rec = json.loads((out / REFUSAL).read_text())
    assert "COMMAND_NOT_FOUND" in rec["reason"], rec
    assert _gate(tmp_path)["status"] == "INCOMPLETE"


def test_a_run_that_wrote_no_junit_is_refused_and_not_called_empty(
        tmp_path, monkeypatch):
    out = _bundle_project(tmp_path, "serial_stream", monkeypatch)
    _simulator_that_writes(monkeypatch, "", rc=2)
    D.step_professional_tb_gen(tmp_path, "dut", "some-container")
    rec = json.loads((out / REFUSAL).read_text())
    assert "no readable JUnit" in rec["reason"], rec


def test_a_transcript_clears_a_previous_refusal(tmp_path, monkeypatch):
    """A stale refusal beside a live transcript is a second answer to the one
    question the file exists to answer."""
    out = _bundle_project(tmp_path, "generic", monkeypatch)
    _no_simulator(monkeypatch)
    D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert (out / REFUSAL).is_file()
    _simulator_that_writes(monkeypatch, _SKIPPED_XML)
    D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert not (out / REFUSAL).exists()
    assert D.professional_tb_bundle_accounted(out) == (True, "transcript")


# --------------------------------------------------------------------------
# THE VERDICT DOES NOT MOVE BECAUSE THE BUNDLE NOW RUNS
# --------------------------------------------------------------------------
def test_a_skipped_transcript_is_still_not_a_pass(tmp_path, monkeypatch):
    """The unfilled hook now leaves a transcript. It must still not be
    credited: 0 passed, and the handoff sentence is unchanged."""
    _bundle_project(tmp_path, "generic", monkeypatch)
    _simulator_that_writes(monkeypatch, _SKIPPED_XML)
    step = D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert step.status == "NOT_MEASURED"
    g = _gate(tmp_path)
    assert g["status"] == "INCOMPLETE"
    assert "reference-model hook is unfilled" in g["reason"]
    assert g["cocotb_test_denominator"]["passed"] == 0

    import _sim_results_bridge as SRB
    assert SRB.find_professional_tb_pass(tmp_path) is None


def test_a_real_pass_is_still_a_pass(tmp_path, monkeypatch):
    _bundle_project(tmp_path, "serial_stream", monkeypatch)
    _simulator_that_writes(monkeypatch, _PASS_XML,
                           marker="PROFESSIONAL_TB PASS 208/208")
    step = D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert step.status == "PASS", step.detail


def test_a_real_mismatch_is_still_a_fail(tmp_path, monkeypatch):
    _bundle_project(tmp_path, "serial_stream", monkeypatch)
    _simulator_that_writes(monkeypatch, _FAIL_XML)
    step = D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert step.status == "FAIL", step.detail
    assert _gate(tmp_path)["functional_mismatch"] is True


def test_a_class_the_generator_SKIPs_owes_no_bundle(tmp_path, monkeypatch):
    """Unchanged scoping, re-asserted: nothing generated, nothing owed."""
    import types
    monkeypatch.setitem(
        sys.modules, "professional_tb_gen",
        types.SimpleNamespace(generate=lambda _p: {"status": "SKIP",
                                                   "reason": "no interface"}))
    step = D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert step.status == "NOT_MEASURED"
    assert not (tmp_path / "phase2/stage1/sim_professional").exists()


# --------------------------------------------------------------------------
# THE EMITTED HOOK MUST BE EXPRESSIBLE IN THE TOOLCHAIN THAT RUNS IT
# --------------------------------------------------------------------------
def _generic_scaffold() -> str:
    shape = {
        "top": "widget", "kind": "generic",
        "ports": [{"name": "a", "dir": "input", "width": 4},
                  {"name": "y", "dir": "output", "width": 4}],
        "cr": {"clk": "clk", "rst": "rst_n", "period_ns": 10,
               "active_high": False},
    }
    return PTB.emit_generic_tb(shape)


def test_DEFECT_the_unfilled_hook_does_not_raise_a_removed_attribute():
    """RED on origin/main: the scaffold raised `cocotb.result.TestSkip`, and
    the pinned image's cocotb 2.2 has no `cocotb.result.TestSkip` — so running
    the bundle recorded failures=1 with an AttributeError, charging the DESIGN
    for a testbench that never reached it."""
    tb = _generic_scaffold()
    assert "raise cocotb.result.TestSkip(" not in tb, tb
    ast.parse(tb)


def test_the_unfilled_hook_binds_to_whichever_skip_api_exists():
    tb = _generic_scaffold()
    tree = ast.parse(tb)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_UNFILLED_HOOK" in names, tb
    # both arms present: the 1.x class and the 2.x pytest skip exception
    assert "from cocotb.result import TestSkip" in tb
    assert "pytest" in tb and "skip" in tb


def test_the_scaffold_is_still_never_a_vacuous_pass():
    """Unchanged property, re-asserted: it raises, and it never prints the
    token that would let the bridge credit it."""
    tb = _generic_scaffold()
    assert "PROFESSIONAL_TB PASS" in tb           # named as a REQUIREMENT
    assert "dut._log.info('PROFESSIONAL_TB PASS')" not in tb
    tree = ast.parse(tb)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.AsyncFunctionDef)
           and any("cocotb.test" in ast.unparse(d) for d in n.decorator_list)]
    assert fns, tb
    assert any(isinstance(c, ast.Raise) for f in fns for c in ast.walk(f))


# --------------------------------------------------------------------------
# THE EXEC SITE
# --------------------------------------------------------------------------
def test_DEFECT_exec_site_does_not_claim_a_container_in_local_mode(monkeypatch):
    """RED on origin/main: `_tool_in_container` answers through the LOCAL route
    when there is no docker client, so it said True for a container nothing had
    entered and the run went to a `docker exec` that does not exist (rc 127)."""
    monkeypatch.setattr(D, "_local_exec_mode", lambda: True)
    monkeypatch.setattr(D, "_tool_in_container", lambda _c, _t: True)
    monkeypatch.setattr(D, "_local_cocotb_toolchain_present", lambda: True)
    assert D._professional_tb_exec_site("some-container") == "host"


def test_exec_site_still_prefers_a_real_container(monkeypatch):
    monkeypatch.setattr(D, "_local_exec_mode", lambda: False)
    monkeypatch.setattr(D, "_tool_in_container", lambda _c, _t: True)
    monkeypatch.setattr(D, "_local_cocotb_toolchain_present", lambda: False)
    assert D._professional_tb_exec_site("some-container") == "container"


def test_exec_site_is_none_when_neither_holds(monkeypatch):
    monkeypatch.setattr(D, "_local_exec_mode", lambda: False)
    monkeypatch.setattr(D, "_tool_in_container", lambda _c, _t: False)
    monkeypatch.setattr(D, "_local_cocotb_toolchain_present", lambda: False)
    assert D._professional_tb_exec_site("some-container") is None


# --------------------------------------------------------------------------
# THE REASON HAS TO BE ACTIONABLE
# --------------------------------------------------------------------------
def test_a_bundle_with_no_rtl_is_refused_before_it_is_dispatched(
        tmp_path, monkeypatch):
    """MEASURED over 145 corpus roots carrying an unrun bundle: 89 are a
    professional TB generated for a project whose rtl directory holds no
    source at all. Dispatching that can only produce make's "No rule to make
    target", which names a path nobody put there."""
    out = _bundle_project(tmp_path, "generic", monkeypatch)
    import types
    gen = {"status": "PASS", "dut_kind": "generic", "out_dir": str(out),
           "files": ["tb_dut.py", "Makefile"], "rtl_files": 0}
    monkeypatch.setitem(sys.modules, "professional_tb_gen",
                        types.SimpleNamespace(generate=lambda _p: gen))
    called = []
    monkeypatch.setattr(D, "_professional_tb_exec_site",
                        lambda _c: called.append(1) or "host")
    D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert not called, "an unelaboratable bundle was still dispatched"
    rec = json.loads((out / REFUSAL).read_text())
    assert "no RTL to elaborate" in rec["reason"], rec


def test_a_generator_that_does_not_report_rtl_still_dispatches(
        tmp_path, monkeypatch):
    """Scoping control: only a MEASURED zero refuses. A record without the
    population (an older report, a stub) keeps the dispatch path it had."""
    out = _bundle_project(tmp_path, "generic", monkeypatch)   # no rtl_files key
    _simulator_that_writes(monkeypatch, _SKIPPED_XML)
    D.step_professional_tb_gen(tmp_path, "dut", "c")
    assert (out / "results.xml").is_file()


def test_the_producer_reports_how_many_sources_it_will_compile(tmp_path):
    """`generate()` computes the source list to write the Makefile; the count
    is what lets the runner refuse an unelaboratable bundle by name."""
    docs = tmp_path / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": "widget",
        "top_ports": [{"name": "a", "dir": "input", "width": 4},
                      {"name": "y", "dir": "output", "width": 4},
                      {"name": "clk", "dir": "input", "width": 1}],
        "clock_domains": [{"name": "clk", "source_pin": "clk"}]}))
    rtl = tmp_path / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "widget.v").write_text(
        "module widget(input [3:0] a, input clk, output reg [3:0] y);"
        " always @(posedge clk) y <= a; endmodule\n")
    res = PTB.generate(tmp_path)
    assert res["status"] == "PASS", res
    assert res["rtl_files"] == 1, res


def test_the_refusal_quotes_the_tools_last_word_not_the_runners(
        tmp_path, monkeypatch):
    out = _bundle_project(tmp_path, "serial_stream", monkeypatch)
    _simulator_that_writes(
        monkeypatch, "", rc=2,
        marker="[INFO] Final PATH variable: /foss\nNo rule to make target 'x.v'"
               "\n[INFO] trailing banner")
    D.step_professional_tb_gen(tmp_path, "dut", "c")
    rec = json.loads((out / REFUSAL).read_text())
    assert "No rule to make target" in rec["reason"], rec
    assert "[INFO]" not in rec["reason"], rec


def test_the_log_tail_helper_says_so_when_there_is_nothing_to_quote():
    assert D._last_meaningful_line("") == "no output"
    assert D._last_meaningful_line("[INFO] banner\n[INFO] more") == "no output"
