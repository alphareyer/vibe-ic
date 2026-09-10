#!/usr/bin/env python3
"""Tests for benchmark_verify_report.py — the deterministic aggregator/gate behind
the `benchmark-verify` skill. Docker-free: builds synthetic project fixtures with
the pillar evidence files + cross_check verdicts, runs the report, and asserts the
6-pillar gates, verdict-token handling, and exit code.
"""
from __future__ import annotations
import importlib.util
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "benchmark_verify_report.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("benchmark_verify_report", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _all_step_ids(mod):
    # Use the SAME flow yaml the generator resolves (plugins/vibe-ic/flow/...);
    # GEN = programs/benchmark_verify_report.py, so GEN.parent.parent == the
    # plugin root. The previous extra `.parent` pointed at plugins/flow/ which
    # does not exist, silently falling back to _load_steps' stale built-in
    # 56-step ids (missing the flow's newer lettered steps FS1/DT1/DT2/DT3) —
    # so those steps got no verdict file and showed as PENDING/unresolved.
    flow = GEN.parent.parent / "flow" / "phase1_phase2_phase3.yaml"
    return [sid for sid, _, _ in mod._load_steps(flow)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_for(project: Path) -> Path:
    return project.parent / f"{project.name}-reference"


def _make_project(tmp: Path, *, func_pct=100.0, line_pct=95.0, fpga="PASS",
                  spare_cov="PASS", spare_removed=0, spare_keep=True,
                  step_verdict="MATCH", analog=False) -> Path:
    """Build a synthetic benchmark project that should PASS all applicable gates."""
    mod = _load_mod()
    (tmp / "reports").mkdir(parents=True, exist_ok=True)
    (tmp / "cross_check" / "p").mkdir(parents=True, exist_ok=True)
    # Pillar 1
    reqs = [{"id": f"R{i}", "source": "L2", "desc": "x", "status": "PASS"} for i in range(10)]
    if func_pct < 100.0:
        reqs[0]["status"] = "FAIL"
    (tmp / "reports" / "functional_coverage.json").write_text(json.dumps({"requirements": reqs}))
    # Pillar 3
    (tmp / "reports" / "code_coverage.json").write_text(
        json.dumps({"line_pct": line_pct, "branch_pct": 90, "toggle_pct": 90}))
    # Pillar 4
    (tmp / "reports" / "hw_test.json").write_text(json.dumps({"verdict": fpga, "patterns": 10}))
    # Pillar 6 design-for-eco
    (tmp / "reports" / "spare_cell_coverage.json").write_text(
        json.dumps({"status": spare_cov, "actual_density": 0.02}))
    (tmp / "reports" / "spare_preservation.json").write_text(
        json.dumps({"all_keep_attr_intact": spare_keep, "removed": spare_removed}))
    # make it look like a place-and-route digital IC so pillar 6 is applicable
    (tmp / "phase3" / "stage4" / "gds").mkdir(parents=True, exist_ok=True)
    (tmp / "phase3" / "stage4" / "gds" / "x.gds").write_text("dummy")
    if analog:
        (tmp / "analog").mkdir(exist_ok=True)
        (tmp / "analog" / "analog_block_list.json").write_text(json.dumps({"blocks": []}))
    # SOURCE_MANIFEST
    (tmp / "SOURCE_MANIFEST.md").write_text("GENERATED x\n")
    # Pillar 2: each verdict is bound to actual subject/ref artifacts and a
    # producer log. Markdown alone is deliberately not accepted.
    ref = _reference_for(tmp)
    (ref / "artifacts").mkdir(parents=True, exist_ok=True)
    (ref / "artifacts" / "reference.bin").write_text("reference\n")
    (tmp / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp / "artifacts" / "subject.bin").write_text("subject\n")
    (tmp / "reports" / "cross-check.log").write_text("comparison completed\n")
    for sid in _all_step_ids(mod):
        (tmp / "cross_check" / "p" / f"step_{sid}.json").write_text(json.dumps({
            "schema": mod.CROSS_CHECK_RECEIPT_SCHEMA, "step_id": sid,
            "verdict": step_verdict,
            "subject": {"path": "artifacts/subject.bin",
                        "sha256": _sha256(tmp / "artifacts" / "subject.bin")},
            "reference": {"path": "artifacts/reference.bin",
                          "sha256": _sha256(ref / "artifacts" / "reference.bin")},
            "producer": {"program": "benchmark_verify_report.py", "exit_code": 0,
                         "log": "reports/cross-check.log",
                         "log_sha256": _sha256(tmp / "reports" / "cross-check.log")},
        }))
    return tmp


def _make_markdown_only_project(tmp: Path) -> Path:
    """A complete-looking legacy project whose only Pillar-2 proof is prose."""
    mod = _load_mod()
    (tmp / "reports").mkdir(parents=True, exist_ok=True)
    (tmp / "cross_check" / "p").mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "functional_coverage.json").write_text(json.dumps({
        "requirements": [{"id": "R", "source": "L2", "desc": "x", "status": "PASS"}],
    }))
    (tmp / "reports" / "code_coverage.json").write_text(json.dumps({
        "line_pct": 95.0, "branch_pct": 90, "toggle_pct": 90,
    }))
    (tmp / "reports" / "hw_test.json").write_text(json.dumps({"verdict": "PASS", "patterns": 1}))
    (tmp / "reports" / "spare_cell_coverage.json").write_text(json.dumps({"status": "PASS"}))
    (tmp / "reports" / "spare_preservation.json").write_text(json.dumps({
        "all_keep_attr_intact": True, "removed": 0, "verdict": "PASS",
    }))
    (tmp / "phase3" / "stage4" / "gds").mkdir(parents=True, exist_ok=True)
    (tmp / "phase3" / "stage4" / "gds" / "x.gds").write_text("dummy")
    (tmp / "SOURCE_MANIFEST.md").write_text("GENERATED x\n")
    _reference_for(tmp).mkdir(exist_ok=True)
    for sid in _all_step_ids(mod):
        (tmp / "cross_check" / "p" / f"step_{sid}.md").write_text(
            "**Verdict: MATCH**\n**Verdict: FAIL**\n")
    return tmp


def _run(project: Path):
    r = subprocess.run([sys.executable, str(GEN), str(project),
                        "--ref", str(_reference_for(project))],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_all_pass_is_production_ready(tmp_path):
    p = _make_project(tmp_path)
    rc, out = _run(p)
    assert "OVERALL=PRODUCTION-READY" in out, out
    assert rc == 0


def test_functional_below_100_fails(tmp_path):
    p = _make_project(tmp_path, func_pct=90.0)
    rc, out = _run(p)
    assert "OVERALL=NOT-COMPLETE" in out and rc == 1


def test_code_coverage_below_floor_fails(tmp_path):
    p = _make_project(tmp_path, line_pct=50.0)
    rc, out = _run(p)
    assert rc == 1 and "NOT-COMPLETE" in out


def test_fpga_fail_fails(tmp_path):
    p = _make_project(tmp_path, fpga="FAIL")
    rc, out = _run(p)
    assert rc == 1


def test_pillar6_removed_spare_fails(tmp_path):
    # a stripped spare cell (removed>0 / keep lost) must FAIL Design-for-ECO
    p = _make_project(tmp_path, spare_removed=3, spare_keep=False)
    rc, out = _run(p)
    assert rc == 1 and "NOT-COMPLETE" in out


def test_unresolved_step_token_fails(tmp_path):
    # a FAIL/GAP step verdict must leave pillar 2 unresolved
    p = _make_project(tmp_path, step_verdict="FAIL")
    rc, out = _run(p)
    assert rc == 1


def test_verdict_token_sets():
    mod = _load_mod()
    # honest tokens that should count as a passing comparison
    for t in ("MATCH", "EQUIVALENT", "IN-RANGE", "BOTH-CLEAN", "BETTER-THAN-REF"):
        assert t in mod.PASS_TOKENS
    # tokens that must NOT pass
    for t in ("FAIL", "GAP", "TODO", "NO-TOOL", "N/A"):
        assert t not in mod.PASS_TOKENS


def test_markdown_match_cannot_satisfy_pillar_two(tmp_path):
    p = _make_markdown_only_project(tmp_path)
    rc, out = _run(p)
    assert rc == 1 and "OVERALL=NOT-COMPLETE" in out


def test_receipt_must_bind_supplied_reference_and_producer_log(tmp_path):
    p = _make_project(tmp_path)
    one = next((p / "cross_check").rglob("*.json"))
    receipt = json.loads(one.read_text())
    receipt["reference"]["sha256"] = "0" * 64
    one.write_text(json.dumps(receipt))
    rc, out = _run(p)
    assert rc == 1 and "OVERALL=NOT-COMPLETE" in out


def test_self_reference_cannot_satisfy_pillar_two(tmp_path):
    p = _make_project(tmp_path)
    r = subprocess.run([sys.executable, str(GEN), str(p), "--ref", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "OVERALL=NOT-COMPLETE" in r.stdout + r.stderr


def test_better_than_ref_counts_as_pass(tmp_path):
    p = _make_project(tmp_path, step_verdict="BETTER-THAN-REF")
    rc, out = _run(p)
    assert "OVERALL=PRODUCTION-READY" in out and rc == 0


# ── #445: the claim carries its own scope ──────────────────────────────────
def test_the_pillar_verdict_line_states_its_scope():
    """MEASURED on a published cell: `RESULT.md` asserts
    "OVERALL: PRODUCTION-READY" — a judgement about THESE SIX PILLARS — where
    it reads as the CELL's verdict, over a flow audit reading FAIL and that
    cell's own final_summary.md saying in words "blocking; do not claim PASS".

    The producer now states the scope, so anyone quoting the line quotes what
    it covers.
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] /
           "benchmark_verify_report.py").read_text()
    assert "Benchmark-pillar verdict:" in src, "the scoped line is gone"
    m = re.search(r"Benchmark-pillar verdict[^\n]*", src)
    assert "NOT flow convergence" in m.group(0) or "NOT flow" in src, src[:0]


def test_the_scoped_line_is_NOT_adoptable_as_a_deliverable_headline():
    """The other half, and the reason the label is not a bare `Verdict:`.

    `deliverable_verdict_consistency_check` recognises
    `final|overall|headline|run|top-level verdict` and would adopt a bare one
    as the DELIVERABLE's headline. Pillar 2 reads "39/39 applicable PASS"
    while the flow audit counts 63 steps, so a bare PASS here would let a
    39-step judgement impersonate a whole-flow one — the category error in the
    other direction.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import deliverable_verdict_consistency_check as D
    assert D._LABEL_RE.match("Benchmark-pillar verdict") is None
    # ...while the canonical labels the gate DOES own still match, so this is
    # a scoping choice and not a hole in the gate.
    assert D._LABEL_RE.match("Overall verdict") is not None
    assert D._LABEL_RE.match("verdict") is not None
