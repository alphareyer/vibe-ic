"""R-0915-86 (1) — the real-IC landing gate, calibrated before it judges.

`real_ic_gate` runs ONE real IC through the plugin's front door on a candidate
tree and diffs its per-step verdict table against a published cell's. Because
the gate's output decides whether a landing proceeds, R-0915-86 (3) applies to
it in full: the instrument must FIRE on a known positive and stay SILENT on a
known negative, and an instrument that has not been shown to do both may not
judge.

These tests exercise the gate through `main()` — argv in, exit code and report
JSON out — using `--reuse-run`, which skips staging and the container and reads
the table already in the work directory. That is the same code path the gate
takes after a real run, minus the eight minutes of EDA. The real thing (SPM
through the front door in the pinned image, against the published cell) is a
MEASUREMENT recorded in the lane's findings, not a unit test: a test that needs
a 31 GB image and an IC is not a test.

WHAT IS PINNED HERE, AND WHY EACH ONE IS A REAL ESCAPE
=====================================================
  * A flipped step is reported AS A REGRESSION and NAMED (the positive).
  * An identical table reports NOTHING (the negative). An instrument that fires
    on everything is as useless as one that fires on nothing.
  * A MISSING audit REFUSES — it does not pass. "The run wrote no table" and
    "the run's table is clean" are the two states a gate must never conflate,
    and defaulting to the second is how a gate goes quiet forever.
  * A STALE audit REFUSES. `flow_compliance_check`'s own #1001 docstring records
    a PUBLISHED cell carrying a wrong-root audit byte-comparably; an exit-code
    check would read that leftover as this run's result.
  * §4.05: an input carrying an oracle/golden path REFUSES before the runner is
    launched, and a project directory that already holds run output REFUSES —
    a table from a pre-seeded tree is about the seed.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

G = importlib.import_module("real_ic_gate")

_ARGV_FULL_SCOPE = ["flow_compliance_check.py", "<project>", "--strict"]


def _tmp() -> Path:
    """mkdtemp, not pytest's tmp_path: the EDA image's tmp_path carries a
    newline in this repo's containers and it has broken tool invocations
    before."""
    return Path(tempfile.mkdtemp(prefix="ricgate_"))


def _audit_doc(steps, verdict, project, when=None):
    return {
        "schema_version": 1,
        "version": "1.21.6",
        "run_at": (when or datetime.now(timezone.utc)).isoformat(),
        "verdict": verdict,
        "command_argv": ["programs/flow_compliance_check.py", str(project),
                         "--strict"],
        "step_counts": {},
        "steps": [{"id": i, "name": "step %s" % i, "stage": "stage1",
                   "status": s} for i, s in steps],
    }


def _write_audit(project: Path, doc):
    p = project / "reports" / "audit" / "phase23_completion_audit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _corpus(root: Path, ic: str, pdk: str, ref_steps, ref_verdict,
            cell="v1.21.6"):
    """A minimal benchmark-data shape: an input/ to stage and ONE published
    cell to be the reference."""
    inp = root / "ic" / ic / "input" / "docs"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "L1_product_metadata.md").write_text("# a design\n", encoding="utf-8")
    (root / "ic" / ic / "input" / "step_0_5ic_answers.json").write_text(
        '{"deliverable": "HARDMACRO"}', encoding="utf-8")
    cellroot = root / "ic" / ic / ("%s_%s" % (cell, pdk))
    _write_audit(cellroot, _audit_doc(ref_steps, ref_verdict, "/published/run"))
    return root


def _fake_tree(root: Path) -> Path:
    """A checkout shape with a front door present. `--reuse-run` never invokes
    it, but the gate refuses a tree that has none, so it must exist."""
    progs = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    progs.mkdir(parents=True, exist_ok=True)
    (progs / "vibe_ic_one_shot_runner.py").write_text("", encoding="utf-8")
    return root


def _run(ic, pdk, corpus, tree, workdir, out):
    return G.main(["--tree", str(tree), "--ic", ic, "--pdk", pdk,
                   "--benchmark-data", str(corpus), "--workdir", str(workdir),
                   "--reuse-run", "--json", str(out)])


def _scenario(cur_steps, ref_steps, cur_verdict="PASS_WITH_WAIVERS",
              ref_verdict="PASS_WITH_WAIVERS", when=None, write_audit=True):
    base = _tmp()
    ic, pdk = "anic", "anpdk"
    corpus = _corpus(base / "bdata", ic, pdk, ref_steps, ref_verdict)
    tree = _fake_tree(base / "tree")
    workdir = base / "wd"
    project = workdir / ic
    project.mkdir(parents=True, exist_ok=True)
    if write_audit:
        _write_audit(project, _audit_doc(cur_steps, cur_verdict, project,
                                         when=when))
    out = base / "real_ic_gate.json"
    rc = _run(ic, pdk, corpus, tree, workdir, out)
    return rc, json.loads(out.read_text(encoding="utf-8")), base


# ── CALIBRATION (R-0915-86 (3)) ──────────────────────────────────────────────

def test_KNOWN_NEGATIVE_an_unchanged_table_exits_0_with_an_empty_diff():
    steps = [("2", "PASS"), ("7", "PASS"), ("15", "SKIPPED-CONDITION")]
    rc, rep, _ = _scenario(steps, steps)
    assert rc == 0
    assert rep["verdict"] == "NO_REGRESSION"
    assert rep["diff"]["regressions"] == []
    assert rep["diff"]["changed"] == []
    assert rep["diff"]["unchanged_step_count"] == 3


def test_KNOWN_POSITIVE_one_flipped_step_exits_1_and_is_named():
    """THE MEASUREMENT THE GATE EXISTS FOR. A landing that turns one step red on
    a real IC must be reported as a regression, with the step id."""
    ref = [("2", "PASS"), ("7", "PASS"), ("15", "SKIPPED-CONDITION")]
    cur = [("2", "PASS"), ("7", "FAIL"), ("15", "SKIPPED-CONDITION")]
    rc, rep, _ = _scenario(cur, ref)
    assert rc == 1
    assert rep["verdict"] == "REGRESSION"
    assert [e["id"] for e in rep["diff"]["regressions"]] == ["7"]


def test_a_step_laundered_into_a_skip_exits_1():
    """R-0915-85 names this shape by name. The step still appears in the table,
    so a count of FAILs would see nothing."""
    ref = [("2", "PASS"), ("7", "PASS")]
    cur = [("2", "PASS"), ("7", "SKIPPED-CONDITION")]
    rc, rep, _ = _scenario(cur, ref)
    assert rc == 1
    assert [e["id"] for e in rep["diff"]["regressions"]] == ["7"]


def test_an_improvement_exits_0_and_is_listed():
    ref = [("2", "FAIL")]
    cur = [("2", "PASS")]
    rc, rep, _ = _scenario(cur, ref, cur_verdict="PASS", ref_verdict="FAIL")
    assert rc == 0
    assert [e["id"] for e in rep["diff"]["improvements"]] == ["2"]
    assert rep["reference_table_stale"] is True


# ── the refusals: "no table" is never "a clean table" ────────────────────────

def test_a_MISSING_audit_REFUSES_it_does_not_pass():
    rc, rep, _ = _scenario([], [("2", "PASS")], write_audit=False)
    assert rc == 2
    assert rep["verdict"] == "REFUSED"
    assert "wrote no" in rep["refusal"]
    assert rep["table"] is None, "a refusal must carry no table"


def test_a_STALE_audit_REFUSES():
    """Not reachable through `--reuse-run` (which has no run clock), so the
    freshness predicate is exercised directly with a real run record. #1001's
    escape: a PUBLISHED cell carried a wrong-root audit byte-comparably, and an
    exit-code check would have read it as this run's result."""
    base = _tmp()
    project = base / "proj"
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    _write_audit(project, _audit_doc([("2", "PASS")], "PASS", project, when=old))
    run = {"rc": 0, "elapsed_s": 1.0, "started_at": "now",
           "started_monotonic_epoch": datetime.now(timezone.utc).timestamp()}
    try:
        G.read_own_table(project, run)
    except G.Refusal as exc:
        assert "predates the run" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("a leftover table must REFUSE, not be read")


def test_an_audit_about_a_DIFFERENT_project_root_REFUSES():
    base = _tmp()
    project = base / "proj"
    _write_audit(project, _audit_doc([("2", "PASS")], "PASS", "/somewhere/else"))
    run = {"rc": 0, "elapsed_s": 1.0, "started_at": "now",
           "started_monotonic_epoch": 0.0}
    try:
        G.read_own_table(project, run)
    except G.Refusal as exc:
        assert "DIFFERENT project root" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("a wrong-root table must REFUSE")


def test_a_reference_at_a_DIFFERENT_SCOPE_refuses_rather_than_diffing():
    """69-vs-9. The gate reports NOT_COMPARABLE and exits 2 — never a
    'regression' it cannot support."""
    base = _tmp()
    ic, pdk = "anic", "anpdk"
    corpus = _corpus(base / "bdata", ic, pdk, [("2", "PASS")], "PASS")
    stage_scoped = (corpus / "ic" / ic / ("v1.21.6_%s" % pdk) / "reports"
                    / "audit" / "phase23_completion_audit.json")
    doc = json.loads(stage_scoped.read_text(encoding="utf-8"))
    doc["command_argv"] = ["stage4_compliance.py", ".", "--exclude-step", "39"]
    stage_scoped.write_text(json.dumps(doc), encoding="utf-8")

    tree = _fake_tree(base / "tree")
    workdir = base / "wd"
    project = workdir / ic
    project.mkdir(parents=True, exist_ok=True)
    _write_audit(project, _audit_doc([("2", "PASS")], "PASS", project))
    out = base / "g.json"
    rc = _run(ic, pdk, corpus, tree, workdir, out)
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 2
    assert rep["verdict"] == "REFUSED"
    assert "NOT_COMPARABLE" in rep["refusal"]


def test_no_published_cell_for_this_pdk_REFUSES():
    """A gate with no reference must refuse rather than pass: 'nothing to
    compare' read as 'nothing changed' is the zero-denominator green."""
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "onepdk", [("2", "PASS")], "PASS")
    tree = _fake_tree(base / "tree")
    out = base / "g.json"
    rc = _run("anic", "otherpdk", corpus, tree, base / "wd", out)
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 2 and "no published cell" in rep["refusal"]


# ── §4.05 staging ────────────────────────────────────────────────────────────

def test_staging_copies_the_WHOLE_input_and_precreates_the_template_dir():
    """R-0915-3: stage the FULL input directory, never a doc subset. R-0915-4:
    `input/submission_template/` is WRITTEN by flow step 0.5ic, so freezing all
    of input/ is a staging error, not a finding."""
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS")
    project = base / "proj"
    rec = G.stage_input(corpus, "anic", project)
    assert rec["file_count"] == 2
    assert "step_0_5ic_answers.json" in rec["files"]
    tmpl = project / "input" / "submission_template"
    assert tmpl.is_dir() and os.access(tmpl, os.W_OK)


def test_an_input_carrying_an_ORACLE_path_REFUSES_before_the_runner_starts():
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS")
    leak = corpus / "ic" / "anic" / "input" / "golden"
    leak.mkdir(parents=True)
    (leak / "expected.txt").write_text("the answer", encoding="utf-8")
    try:
        G.stage_input(corpus, "anic", base / "proj")
    except G.Refusal as exc:
        assert "4.05" in str(exc) and "golden" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("an oracle in the staged input must REFUSE")


def test_the_operator_answers_file_is_an_INPUT_not_an_oracle():
    """The one exemption, and it is the corpus's own: R-0915-3 names
    `step_0_5ic_answers.json` as part of the input for every IC. A guard that
    refused it would refuse every real cell."""
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS")
    rec = G.stage_input(corpus, "anic", base / "proj")
    assert "step_0_5ic_answers.json" in rec["files"]


def test_a_PRE_SEEDED_project_directory_REFUSES():
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS")
    project = base / "proj"
    (project / "phase3" / "stage3").mkdir(parents=True)
    try:
        G.stage_input(corpus, "anic", project)
    except G.Refusal as exc:
        assert "not empty" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("a pre-seeded project must REFUSE")


def test_a_missing_input_REFUSES():
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS")
    try:
        G.stage_input(corpus, "no_such_ic", base / "proj")
    except G.Refusal as exc:
        assert "no design input" in str(exc)
    else:                                                   # pragma: no cover
        raise AssertionError("a missing input must REFUSE")


# ── reference selection is stated, never silent ──────────────────────────────

def test_the_highest_published_version_wins_and_the_rejects_are_named():
    """A reference chosen by a rule nobody can see is the 'two documents are
    called contract' defect: measuring against the wrong cell proves nothing."""
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "anpdk", [("2", "PASS")], "PASS",
                     cell="v1.14.88")
    _corpus(corpus, "anic", "anpdk", [("2", "FAIL")], "FAIL", cell="v1.21.6")
    sel = G.resolve_reference(corpus, "anic", "anpdk", None)
    assert sel["cell"] == "v1.21.6_anpdk"
    assert sel["rejected"] == ["v1.14.88_anpdk"]
    assert sorted(sel["candidates"]) == ["v1.14.88_anpdk", "v1.21.6_anpdk"]


def test_a_cell_for_another_pdk_is_never_borrowed():
    """A number from another PDK is not this PDK's number."""
    base = _tmp()
    corpus = _corpus(base / "bdata", "anic", "pdkA", [("2", "PASS")], "PASS")
    _corpus(corpus, "anic", "pdkB", [("2", "FAIL")], "FAIL")
    sel = G.resolve_reference(corpus, "anic", "pdkA", None)
    assert sel["candidates"] == ["v1.21.6_pdkA"]


# ── time is recorded, never enforced ─────────────────────────────────────────

def test_no_timeout_kill_or_deadline_appears_in_this_program():
    """The owner's standing rule, pinned in the file rather than in a promise:
    `--budget-s` sets a disclosure field and nothing else."""
    src = (Path(G.__file__)).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]          # past the module docstring
    for banned in ("timeout=", "subprocess.TimeoutExpired", ".kill(",
                   ".terminate(", "signal.alarm", "SIGKILL"):
        assert banned not in body, "%r must not appear in real_ic_gate" % banned
