"""The runner's spare-coverage attribution job comes from step 18's own
declaration, not from a path restated in the runner.

MEASURED 2026-09-15: #2257 gave `_emit_drc_attribution_reports` a producer
entry for the graded spare-cell pool, and wrote both of its paths out as
literals::

    ("spare_cell_coverage_check", "reports/spare_cell_coverage.json",
     "phase3/stage3/pnr/spare_cells.json",
     ("--json", "reports/phase2/gates/spare_cell_coverage.json")),

which tripped `test_spare_coverage_single_declaring_producer::
test_the_runner_does_not_write_the_gates_declared_output`. That guard is RIGHT
and is not changed here: this runner must not WRITE the artefact step 18
declares `spare_cell_coverage_check` the producer of. Its proxy for "does not
write it" is "does not NAME it", and the entry named it twice — once as the
output it waits for and once inside the checker's own `--json` argv. So a table
that was CORRECT tripped a guard that was also correct.

Restating either path is wrong for a second, independent reason, and it is the
one `_canonical_step_condition` in the same file already states: re-stating a
declaration in the runner gives the producer and its consumer TWO definitions
of one artefact, and a later edit to either can move them apart silently.

So both paths are READ FROM THE FLOW: the declared output is the step's own
`required_outputs` entry that the gate's `--json` clause does not name, and the
argv is the gate clause verbatim. When the declaration cannot be resolved the
job is DISCLOSED as NOT_MEASURED — never guessed.

chip-AGNOSTIC: step id and program name only.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

import pytest  # noqa: E402


def _step18():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text())

    def walk(n):
        if isinstance(n, dict):
            if str(n.get("id")) == R._SPARE_COVERAGE_STEP:
                yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    found = list(walk(doc))
    assert len(found) == 1, f"step {R._SPARE_COVERAGE_STEP} resolved {len(found)}x"
    return found[0]


# ── the job IS the declaration ────────────────────────────────────────────

def test_the_declared_output_is_the_one_step_18_declares():
    job = R._spare_coverage_job()
    assert job is not None, "step 18's declaration no longer resolves"
    _prog, declared, _trigger, _argv = job
    assert declared in (_step18().get("required_outputs") or []), (
        "the runner waits for a path step 18 does not declare")


def test_the_argv_is_the_gate_clause_verbatim():
    job = R._spare_coverage_job()
    assert job is not None
    _prog, _declared, _trigger, argv = job
    src = (PROGRAMS.parent / "flow"
           / "phase1_phase2_phase3.yaml").read_text(errors="replace")
    clause = f"{R._SPARE_COVERAGE_PROGRAM} . {' '.join(argv)}"
    assert clause in src, (
        f"the runner would invoke {clause!r}, which the flow does not declare")


def test_the_declared_output_is_not_the_gates_own_json_target():
    """They are two different artefacts and the job must not confuse them: the
    gate writes its verdict beside the flow's other gate records, and step 18
    declares the graded pool itself."""
    job = R._spare_coverage_job()
    _prog, declared, _trigger, argv = job
    gate_json = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--json"]
    assert gate_json and declared not in gate_json


# ── it is DERIVED: change the declaration and the job changes ─────────────

def test_a_renamed_declaration_renames_the_job(tmp_path, monkeypatch):
    """The proof that nothing is restated. Point the resolver at a flow whose
    step 18 declares a different path and the job follows it."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text())

    def rewrite(n):
        if isinstance(n, dict):
            if str(n.get("id")) == R._SPARE_COVERAGE_STEP:
                n["required_outputs"] = [
                    R._SPARE_COVERAGE_TRIGGER,
                    "reports/renamed_pool_record.json"]
            for v in n.values():
                rewrite(v)
        elif isinstance(n, list):
            for v in n:
                rewrite(v)
    rewrite(doc)
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    (flow_dir / "phase1_phase2_phase3.yaml").write_text(yaml.safe_dump(doc))
    (tmp_path / "programs").mkdir()
    monkeypatch.setattr(R, "PROGRAMS_DIR", tmp_path / "programs")
    job = R._spare_coverage_job()
    assert job is not None
    assert job[1] == "reports/renamed_pool_record.json"


# ── it FAILS CLOSED, and the caller discloses ─────────────────────────────

def test_an_unreadable_flow_resolves_to_nothing(tmp_path, monkeypatch):
    (tmp_path / "programs").mkdir()
    monkeypatch.setattr(R, "PROGRAMS_DIR", tmp_path / "programs")
    assert R._spare_coverage_job() is None


def test_an_ambiguous_declaration_resolves_to_nothing(tmp_path, monkeypatch):
    """Two candidate outputs and the job cannot say which is the pool. It says
    so rather than picking."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text())

    def rewrite(n):
        if isinstance(n, dict):
            if str(n.get("id")) == R._SPARE_COVERAGE_STEP:
                n["required_outputs"] = [R._SPARE_COVERAGE_TRIGGER,
                                         "reports/a.json", "reports/b.json"]
            for v in n.values():
                rewrite(v)
        elif isinstance(n, list):
            for v in n:
                rewrite(v)
    rewrite(doc)
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    (flow_dir / "phase1_phase2_phase3.yaml").write_text(yaml.safe_dump(doc))
    (tmp_path / "programs").mkdir()
    monkeypatch.setattr(R, "PROGRAMS_DIR", tmp_path / "programs")
    assert R._spare_coverage_job() is None


def test_an_unresolvable_job_is_DISCLOSED_not_dropped(tmp_path, monkeypatch):
    """Silence and 'there was nothing to do' must not be the same record."""
    (tmp_path / "programs").mkdir()
    monkeypatch.setattr(R, "PROGRAMS_DIR", tmp_path / "programs")
    rows = R._emit_drc_attribution_reports(tmp_path)
    spare = [r for r in rows if r.get("program") == R._SPARE_COVERAGE_PROGRAM]
    assert spare, "the unresolvable job vanished from the record"
    assert spare[0]["status"] == "NOT_MEASURED"
    assert "will not guess" in spare[0]["reason"]


# ── and the guard this exists to satisfy still holds ──────────────────────

def test_the_runner_still_names_no_declared_path(tmp_path):
    """The sibling guard's own question, asked here too so a future edit that
    reintroduces the literal is caught by BOTH files."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text(errors="replace")
    job = R._spare_coverage_job()
    assert job is not None
    named = [ln for ln in src.splitlines()
             if job[1] in ln and not ln.lstrip().startswith("#")]
    assert named == [], named
