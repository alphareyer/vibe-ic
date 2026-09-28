"""Post-layout LEC: the `liberty` NameError, and the failure DISCLOSURE.

WHY THIS FILE WAS REWRITTEN
==========================
The first cut of these tests defined their OWN inline `_pre_fix_snippet` /
`_fixed_snippet` closures and asserted things about *those*. An independent
skeptic reverted BOTH source hunks (`str(pdk.liberty)` -> bare `liberty`,
and the caller's re-raise -> `notes.append`) and 7 of the 9 tests still
passed: they never imported, never called, and therefore never constrained
the shipped code. A test that passes with the defect fully restored is not
evidence.

Every test below drives the REAL `phase3_one_shot_runner` functions. The
mutations each one is built to catch are named in its docstring, and each
was run against the fix to confirm it FAILS with the defect present.

THE DEFECTS
-----------
(a) `_emit_lec_post_layout` contained `Path(liberty)` where `liberty` was
    never bound in that scope -> NameError on every post-layout LEC. The
    caller's broad `except Exception` buried it in a JSON note, so the
    sign-off proof never ran and nothing said so.
(b) The fix for (a) must use the LIBERTY (`pdk.liberty`), not some other
    PdkConfig path: the value feeds the functional-`read_liberty`
    capability decision, and pointing it at a file that merely EXISTS
    (e.g. `pdk.cell_lef`) buys the UNSOUND `-lib` blackbox recipe on an
    input the tool never read.
(c) A sign-off emit that fails must become a FAIL status ROW — and must
    NOT abort the runner. `step_canonicalize_artefacts` is called
    unguarded from `main()`, so raising out of it destroys the
    orchestrator report the failure was supposed to appear in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGS))

import phase3_one_shot_runner as R  # noqa: E402
import lec_post_layout_check as LEC  # noqa: E402


TOP = "widget"


def _pdk(liberty: str, cell_lef: str) -> R.PdkConfig:
    return R.PdkConfig(
        name="testpdk", liberty=liberty, tech_lef=cell_lef,
        cell_lef=cell_lef, cell_gds=None, site="unit", drc_deck=None,
    )


def _routed_project(tmp_path: Path) -> Path:
    """A project with the minimum `_emit_lec_post_layout` needs to reach the
    liberty-capability probe: a routed gate netlist and a synth gold."""
    pnr = R._pl.pnr_dir(tmp_path)
    synth = R._pl.synth_dir(tmp_path)
    for d in (pnr, synth):
        d.mkdir(parents=True, exist_ok=True)
    (pnr / f"{TOP}_pnr.v").write_text(f"module {TOP}(input a, output y);\n"
                                      f"endmodule\n")
    (synth / f"{TOP}_synth.v").write_text(f"module {TOP}(input a, output y);\n"
                                          f"endmodule\n")
    (pnr / f"{TOP}.def").write_text("DIEAREA ( 0 0 ) ( 100 100 ) ;\n")
    return tmp_path


@pytest.fixture()
def stub_container(monkeypatch):
    """Neutralise every container round-trip so the pure-python decision
    path under test runs on any host (with or without docker)."""
    calls = []

    def _fake_exec(container, cmd, timeout=1800, **kw):
        calls.append(cmd)
        return (_fake_exec.rc, "", "")

    _fake_exec.rc = 0
    monkeypatch.setattr(R, "_docker_exec", _fake_exec)
    monkeypatch.setattr(R, "_docker_exec_raw", _fake_exec)
    monkeypatch.setattr(R, "_discover_blackbox_verilog", lambda *a, **k: [])
    monkeypatch.setattr(R, "_synthesize_physical_cell_stubs",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    return _fake_exec


# ---------------------------------------------------------------------------
# (a) The NameError itself — executed, not paraphrased.
#
# MUTATION THIS CATCHES: restore the pre-fix line
#     _lib_host = Path(liberty) if liberty else None
# in `_emit_lec_post_layout`. -> NameError: name 'liberty' is not defined,
# and this test fails.
# ---------------------------------------------------------------------------

def test_emit_lec_post_layout_reaches_the_liberty_probe(tmp_path,
                                                        stub_container):
    """The REAL `_emit_lec_post_layout` runs to a verdict on a routed
    project. Before the fix this raised NameError at the liberty-capability
    probe, so no verdict was ever produced."""
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"
    notes: list = []

    verdict = R._emit_lec_post_layout(
        project, TOP, _pdk(str(lib), str(tmp_path / "cells.lef")),
        "nocontainer", out_json, out_json.with_suffix(".rpt"), notes)

    assert isinstance(verdict, str) and verdict, \
        "the emit must return a verdict string, not blow up"
    assert out_json.is_file(), "lec_post_layout.json must be written"
    doc = json.loads(out_json.read_text())
    assert doc["skipped"] is False, (
        "the compare must have been ATTEMPTED, not skipped: "
        f"{doc.get('skip_reason')}")
    assert doc.get("lec_recipe") == "functional", (
        f"a healthy liberty + passing probe selects the SOUND recipe; "
        f"got {doc.get('lec_recipe')!r}, notes={notes}")


def test_postlayout_producer_retains_decided_failure_when_sat_cannot_run(
        tmp_path, stub_container, monkeypatch):
    """A later missing terminal IL cannot turn the first pass's failure into
    NOT_PROVEN. Exercise the actual Phase 3 artefact writer and gate."""
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"
    monkeypatch.setattr(LEC, "parse_equiv_log", lambda text: {
        "verdict": "NON_EQUIVALENT", "proven": 2, "unproven": 1,
        "total": 3, "non_equivalent": 1, "equivalent": False,
        "sat_unsupported_cells": [],
    })
    verdict = R._emit_lec_post_layout(
        project, TOP, _pdk(str(lib), str(lib)), "nocontainer",
        out_json, out_json.with_suffix(".rpt"), [])
    doc = json.loads(out_json.read_text())
    assert doc["counterexample_search"]["result"] == "NOT_RUN"
    assert doc["verdict"] == "NON_EQUIVALENT"
    assert verdict == "NON_EQUIVALENT"


def test_emit_lec_post_layout_does_not_raise_nameerror(tmp_path,
                                                       stub_container):
    """Explicit: no NameError escapes for ANY caller. (The caller's broad
    except is what buried it, so the assertion is made here at the source.)"""
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"
    try:
        R._emit_lec_post_layout(
            project, TOP, _pdk(str(lib), str(tmp_path / "cells.lef")),
            "nocontainer", out_json, out_json.with_suffix(".rpt"), [])
    except NameError as exc:  # pragma: no cover - the defect
        pytest.fail(f"pre-fix NameError is back: {exc}")


# ---------------------------------------------------------------------------
# (b) It must be the LIBERTY, not merely *a* file that exists.
#
# MUTATION THIS CATCHES: `str(pdk.liberty)` -> `str(pdk.cell_lef)` (the
# skeptic's own plausible copy-paste). Below, the liberty is ABSENT and the
# cell LEF is present + non-empty, and the functional probe FAILS (rc=1):
#   * correct  -> liberty is an INPUT defect -> refuse the unsound `-lib`
#                 fallback, keep the SOUND functional recipe.
#   * mutated  -> the LEF "is" the liberty -> a capability gap -> select
#                 `blackbox_lib_fallback`, an UNSOUND compare on a Liberty
#                 that was never read.
# ---------------------------------------------------------------------------

def test_liberty_capability_decision_reads_the_liberty_not_the_lef(
        tmp_path, stub_container):
    """Absent liberty + present LEF + failing probe must keep the SOUND
    functional recipe (input defect), never the unsound -lib fallback."""
    stub_container.rc = 1                      # functional read_liberty fails
    missing_lib = tmp_path / "does_not_exist.lib"
    lef = tmp_path / "cells.lef"
    lef.write_text("MACRO x END x\n")          # exists AND non-empty
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"
    notes: list = []

    R._emit_lec_post_layout(project, TOP, _pdk(str(missing_lib), str(lef)),
                            "nocontainer", out_json,
                            out_json.with_suffix(".rpt"), notes)

    doc = json.loads(out_json.read_text())
    assert doc.get("lec_recipe") != "blackbox_lib_fallback", (
        "an ABSENT liberty is an INPUT defect and must NOT buy the unsound "
        "-lib recipe — the capability decision is reading the wrong file "
        f"(recipe={doc.get('lec_recipe')!r})")
    assert doc.get("lec_recipe") == "functional"
    assert any("SOUND" in n for n in notes), (
        f"the input defect must be disclosed; notes={notes}")


def test_liberty_capability_decision_honours_a_real_capability_gap(
        tmp_path, stub_container):
    """Control for the test above (so it cannot pass by always asserting
    'functional'): a REAL, non-empty liberty plus a failing probe IS a
    capability gap, and the unsound fallback is selected + recorded."""
    stub_container.rc = 1
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"

    R._emit_lec_post_layout(project, TOP, _pdk(str(lib), str(lib)),
                            "nocontainer", out_json,
                            out_json.with_suffix(".rpt"), [])

    doc = json.loads(out_json.read_text())
    assert doc.get("lec_recipe") == "blackbox_lib_fallback"


def test_liberty_probe_script_is_built_from_the_liberty_path(tmp_path,
                                                             stub_container):
    """Belt-and-braces on the same mutation: the probe script yosys runs must
    name the LIBERTY file."""
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    lef = tmp_path / "cells.lef"
    lef.write_text("MACRO x END x\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"

    R._emit_lec_post_layout(project, TOP, _pdk(str(lib), str(lef)),
                            "nocontainer", out_json,
                            out_json.with_suffix(".rpt"), [])

    probe = (out_json.parent / f"lec_post_{TOP}_libprobe.ys").read_text()
    assert str(lib) in probe
    assert str(lef) not in probe


def test_functional_read_liberty_supported_contract():
    """The decision helper itself, pinned: an absent/empty liberty is an
    INPUT defect (no fallback); a failing probe on a real liberty is a
    CAPABILITY gap (fallback)."""
    assert LEC.functional_read_liberty_supported(0, True, True)[0] is True
    assert LEC.functional_read_liberty_supported(1, True, True)[0] is False
    assert LEC.liberty_input_is_usable(False, False) is False
    assert LEC.liberty_input_is_usable(True, False) is False
    assert LEC.liberty_input_is_usable(True, True) is True


# ---------------------------------------------------------------------------
# (c) A failed sign-off emit must be a FAIL ROW, and must not kill the run.
#
# MUTATIONS THESE CATCH:
#   * revert the caller to `notes.append(...)`  -> step still returns PASS
#     -> test_signoff_emit_failure_is_a_fail_row fails.
#   * `raise RuntimeError(...) from exc`        -> step_canonicalize_artefacts
#     propagates -> both tests below fail (the second by ERROR, which is the
#     point: the runner dies and there is no report at all).
# ---------------------------------------------------------------------------

def _canonicalize_project(tmp_path: Path) -> Path:
    project = _routed_project(tmp_path)
    R._pl.reports_phase3_dir(project).mkdir(parents=True, exist_ok=True)
    return project


def _quiet_canonicalize(monkeypatch):
    """Stub every OTHER emitter in step_canonicalize_artefacts so the test
    isolates the post-layout-LEC branch."""
    for name in ("_emit_power_report",
                 "_emit_antenna_report", "_emit_si_crosstalk_report",
                 "_emit_metal_fill", "_emit_erc_report",
                 "_emit_metal_density_report"):
        if hasattr(R, name):
            monkeypatch.setattr(R, name, lambda *a, **k: False)
    monkeypatch.setattr(R, "_emit_ir_em_reports", lambda *a, **k: (False, False))
    monkeypatch.setattr(R, "_docker_exec", lambda *a, **k: (1, "", ""))
    monkeypatch.setattr(R, "_docker_exec_raw", lambda *a, **k: (1, "", ""))


def test_signoff_emit_failure_is_a_fail_row(tmp_path, monkeypatch):
    """A post-layout LEC emit that raises makes canonicalize_artefacts FAIL
    and NAMES the failure in the status row — not a buried note."""
    _quiet_canonicalize(monkeypatch)
    project = _canonicalize_project(tmp_path)

    def _boom(*a, **k):
        raise FileNotFoundError("docker: command not found")

    monkeypatch.setattr(R, "_emit_lec_post_layout", _boom)

    res = R.step_canonicalize_artefacts(
        project, TOP, _pdk(str(tmp_path / "x.lib"), str(tmp_path / "x.lef")),
        "nocontainer")

    assert res.status == "FAIL", (
        f"a sign-off proof that could not be produced must FAIL the step; "
        f"got {res.status} / {res.detail[:200]}")
    assert "post-layout LEC emit FAILED" in res.detail


def test_signoff_emit_failure_does_not_abort_the_runner(tmp_path, monkeypatch):
    """The failure must not be raised: `step_canonicalize_artefacts` is
    called unguarded from main(), so raising loses the orchestrator report
    AND every artefact emitted after this point. The step must still return
    normally and still have emitted its other artefacts."""
    _quiet_canonicalize(monkeypatch)
    project = _canonicalize_project(tmp_path)

    def _boom(*a, **k):
        raise FileNotFoundError("docker: command not found")

    monkeypatch.setattr(R, "_emit_lec_post_layout", _boom)

    # Must NOT raise.
    res = R.step_canonicalize_artefacts(
        project, TOP, _pdk(str(tmp_path / "x.lib"), str(tmp_path / "x.lef")),
        "nocontainer")

    assert res.name == "canonicalize_artefacts"
    assert res.output_files, (
        "the remaining canonical artefacts must still have been emitted; "
        "aborting the step would have lost them")
    # The canonical GDS alias dir and the clock plan live AFTER the LEC block.
    assert R._pl.cts_dir(project).is_dir()


def test_healthy_canonicalize_still_passes(tmp_path, monkeypatch):
    """Control: with the LEC emit healthy the step is PASS, so the FAIL row
    above cannot be an artefact of the harness."""
    _quiet_canonicalize(monkeypatch)
    project = _canonicalize_project(tmp_path)
    monkeypatch.setattr(R, "_emit_lec_post_layout", lambda *a, **k: "SKIP")

    res = R.step_canonicalize_artefacts(
        project, TOP, _pdk(str(tmp_path / "x.lib"), str(tmp_path / "x.lef")),
        "nocontainer")

    assert res.status == "PASS", res.detail[:300]


# ---------------------------------------------------------------------------
# Source-shape guards. These are NOT the evidence — the behavioural tests
# above are. They are kept only to pin the two exact strings the defect and
# the rejected crash-fix consisted of.
# ---------------------------------------------------------------------------

def test_source_no_bare_liberty_in_lec_post_layout():
    import inspect
    from _source_pin import code_only
    src = code_only(inspect.getsource(R._emit_lec_post_layout))
    assert "Path(liberty)" not in src


def test_source_signoff_emit_does_not_raise_out_of_the_step():
    """The caller must record the failure, not raise it: raising kills the
    runner mid-step (no orchestrator report, no waivers.json, no
    final_summary.md)."""
    import inspect
    src = inspect.getsource(R.step_canonicalize_artefacts)
    call = src.find("_emit_lec_post_layout(")
    assert call != -1
    window = src[call:call + 1600]
    assert "signoff_failures.append" in window
    assert "raise RuntimeError" not in window


# ---------------------------------------------------------------------------
# The post-layout producer's residual decision, driven through the REAL
# `_emit_lec_post_layout` with every yosys run faked at `_docker_exec`. The
# fakes write the files the real runs would: the terminal equivalence IL, the
# flattened search miter, the WaveJSON dump.
# ---------------------------------------------------------------------------

def _post_fake(monkeypatch, proof_logs, search_log="", flat="", trace_doc=None):
    """`proof_logs` is consumed one per equivalence run, the last repeating."""
    import re
    runs = []

    def fake_exec(_container, cmd, timeout=1800, **_kw):
        if "yosys -V" in cmd:
            return 0, "Yosys 0.69", ""
        if "libprobe" in cmd or "yosys -s" not in cmd:
            return 0, "", ""
        script = Path(re.search(r"yosys -s (\S+)", cmd).group(1)).read_text()
        for path in re.findall(r"write_rtlil (\S+)", script):
            Path(path).write_text(flat if "lec_post_cex_flat" in path
                                  else "module \\equiv\nend\n")
        for path in re.findall(r"-dump_json (\S+)", script):
            Path(path).write_text(json.dumps(trace_doc or {}))
        for path in re.findall(r"write_json (\S+)", script):
            Path(path).write_text(json.dumps(
                {"modules": {TOP: {"netnames": {"u1.A": {}, "u1.B": {}}}}}))
        if "equiv_miter" in script:
            return 0, search_log, ""
        runs.append(cmd)
        return 0, proof_logs[min(len(runs), len(proof_logs)) - 1], ""

    monkeypatch.setattr(R, "_docker_exec", fake_exec)
    return runs


def _unproven(total=3, unproven=1):
    def parse(_text):
        return {"verdict": "UNPROVEN", "proven": total - unproven,
                "unproven": unproven, "total": total, "non_equivalent": None,
                "equivalent": False, "sat_unsupported_cells": []}
    return parse


def _emit_post(tmp_path):
    lib = tmp_path / "corner_tt.lib"
    lib.write_text("library(t){}\n")
    project = _routed_project(tmp_path)
    out_json = R._pl.reports_phase3_dir(project) / "lec_post_layout.json"
    verdict = R._emit_lec_post_layout(
        project, TOP, _pdk(str(lib), str(lib)), "nocontainer",
        out_json, out_json.with_suffix(".rpt"), [])
    return verdict, json.loads(out_json.read_text())


def test_postlayout_producer_keeps_a_stateful_model_not_proven(
        tmp_path, stub_container, monkeypatch):
    """The up/down-counter model (verbatim SAT output, see
    test_lec_counterexample_candidate) reaches the post-layout producer."""
    import test_lec_counterexample_candidate as F
    monkeypatch.delattr(LEC, "classify_pin_permutation_points")
    monkeypatch.setattr(LEC, "parse_equiv_log", _unproven(total=1))
    _post_fake(monkeypatch,
               ["  Unproven $equiv $auto$x: \\y_gold \\y_gate\n"],
               F.COUNTER_LOG, F.COUNTER_FLAT, F.COUNTER_JSON)
    verdict, doc = _emit_post(tmp_path)
    assert doc["counterexample_search"]["result"] != "COUNTEREXAMPLE"
    assert doc["counterexample_search"]["result"] == "CANDIDATE_COUNTEREXAMPLE"
    assert verdict == "NOT_PROVEN"
    assert doc["verdict_explanation"].startswith("NOT_PROVEN")
    assert LEC.evaluate_report(doc)["result"] == "NOT_PROVEN"


def test_postlayout_producer_names_the_bits_of_a_multi_bit_residual(
        tmp_path, stub_container, monkeypatch):
    monkeypatch.delattr(LEC, "classify_pin_permutation_points")
    monkeypatch.setattr(LEC, "parse_equiv_log", _unproven(total=4, unproven=2))
    _post_fake(monkeypatch, [
        "  Unproven $equiv $auto$x1: \\q_gold [0] \\q_gate [0]\n"
        "  Unproven $equiv $auto$x2: \\q_gold [1] \\q_gate [1]\n"])
    # No terminal IL: the search cannot run, and says so.
    monkeypatch.setattr(R, "_to_container_path",
                        lambda p, c: str(p).replace("lec_post_layout_final.il",
                                                    "elsewhere.il"))
    verdict, doc = _emit_post(tmp_path)
    assert doc["unproven_point_names"] == ["q[0]", "q[1]"]
    assert doc["counterexample_search"]["point_names"] == ["q[0]", "q[1]"]
    assert verdict == "NOT_PROVEN"
    assert LEC.evaluate_report(doc)["result"] == "NOT_PROVEN"


def test_postlayout_producer_says_why_it_cannot_name_the_residual(
        tmp_path, stub_container, monkeypatch):
    monkeypatch.delattr(LEC, "classify_pin_permutation_points")
    monkeypatch.setattr(LEC, "parse_equiv_log", _unproven(total=4, unproven=2))
    _post_fake(monkeypatch, [
        "  Unproven $equiv $auto$x1: \\q_gold [0] \\q_gate [0]\n"
        "  Unproven $equiv $auto$x2: 1'0 \\q_gate [1]\n"])
    verdict, doc = _emit_post(tmp_path)
    assert verdict == "RUN_ERROR"
    assert "named 1" in doc["counterexample_search_error"]
    result = LEC.evaluate_report(doc)
    assert result["verdict"] == "RUN_ERROR"
    assert "named 1" in result["findings"][0]


def test_postlayout_discarded_reproof_does_not_name_the_residual(
        tmp_path, stub_container, monkeypatch):
    """A pin-rename re-proof that moves the denominator is DISCARDED and the
    first pass's counts stand, so the names and the state evidence published
    beside those counts must be the first pass's too."""
    first = ("=== equiv ===\n     1   $equiv\n     1   $and\n"
             "  Unproven $equiv $auto$x: \\u1.A_gold \\u1.A_gate\n")
    second = ("SECOND PASS\n=== equiv ===\n     1   $dff\n"
              "  Unproven $equiv $auto$y: \\u9.B_gold \\u9.B_gate\n")
    monkeypatch.setattr(LEC, "parse_equiv_log", lambda text: {
        "verdict": "UNPROVEN", "proven": 1 if "SECOND" in text else 2,
        "unproven": 1, "total": 2 if "SECOND" in text else 3,
        "non_equivalent": None, "equivalent": False,
        "sat_unsupported_cells": []})
    calls = {"n": 0}

    def classify(names, *_a, **_k):
        calls["n"] += 1
        return {"accepted": ([{"instance": "u1", "point": n} for n in names]
                             if calls["n"] > 1 else []), "rejected": []}

    monkeypatch.setattr(LEC, "classify_pin_permutation_points", classify)
    monkeypatch.setattr(LEC, "build_pin_correspondence_renames",
                        lambda accepted, *_a, **_k: (
                            ([("u1.A", "u1.B")],
                             [{"instance": "u1", "renames": ["u1.A"]}])
                            if accepted else ([], [])))
    _post_fake(monkeypatch, [first, first, second])
    _verdict, doc = _emit_post(tmp_path)
    assert doc["pin_permutation_reproof"]["denominator_moved"] == {
        "first_pass": 3, "second_pass": 2}
    assert doc["total_points"] == 3
    assert doc["unproven_point_names"] == ["u1.A"]
    assert doc["counterexample_search"]["point_names"] == ["u1.A"]
    assert doc["miter_stateless"] is True
