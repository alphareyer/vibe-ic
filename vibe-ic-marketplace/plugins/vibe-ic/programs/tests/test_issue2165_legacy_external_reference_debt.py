#!/usr/bin/env python3
"""vibe-ic#2165 — the legacy external-reference debt #2158's blocking gate made
visible: decided per family, recorded where it cannot be re-run, and MEASURED.

THE ARM THAT MATTERS MOST IS `test_annotating_does_not_make_the_gate_pass`.
The #2158 ruling recorded 659 roots as DEBT rather than absorbing them, and
refused to soften the gate to fit the count it had just found. A remediation
tool is the obvious back door to that decision: write a waiver per path and the
number goes to zero without a single reference becoming resolvable. So the
annotation is pinned as a RECORD — the gate's verdict and its blocking COUNT are
byte-identical before and after — and a second arm asserts the waiver key is
never written anywhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import legacy_external_reference_debt as D  # noqa: E402
import project_outputs_in_tree_check as GATE  # noqa: E402

GATE_PROG = _PROGRAMS / "project_outputs_in_tree_check.py"
DEBT_PROG = _PROGRAMS / "legacy_external_reference_debt.py"


def _gate(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE_PROG), str(project)],
                          capture_output=True, text=True)


def _debt(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DEBT_PROG), *map(str, args)],
                          capture_output=True, text=True)


def _gone(project: Path, tail: str = "phase2/stage1/rtl/top.sv") -> str:
    """A reference in #2158's population: a relocated copy of THIS run root,
    under a directory that does not exist. Never created."""
    p = f"/mnt/vanished_2165/producer_root/{project.name}/{tail}"
    assert not Path(p).exists()
    return p


def _root_citing_reports_only(tmp_path: Path) -> Path:
    project = tmp_path / "runroot"
    (project / "reports" / "orchestrator").mkdir(parents=True)
    (project / "phase1" / "generated_docs").mkdir(parents=True)
    (project / "reports" / "orchestrator" / "phase2_one_shot.json").write_text(
        json.dumps({"steps": [{"name": "rtl_gen",
                               "detail": f"see {_gone(project)}"}]}))
    return project


# ── family derivation ───────────────────────────────────────────────────────

def test_the_family_is_the_parent_of_the_relocated_run_root():
    family = Path.home() / "AI_IC_design" / "_bench6_v100_r1"
    assert D.family_of(str(family / "ibex" / "phase2" / "x.json"),
                       "ibex") == str(family)


def test_the_shallowest_occurrence_of_the_name_wins():
    """A run root named `ibex` under a directory also named `ibex` must not
    resolve to the deeper one — the relocated root is the outer copy."""
    assert D.family_of("/a/ibex/ibex/phase2/x.json", "ibex") == "/a"


def test_a_path_without_the_run_root_name_falls_back_and_says_nothing_false():
    """The fallback is the first absent ancestor. It is only reachable for a
    reference handed in from outside the gate's population."""
    got = D.family_of("/mnt/vanished_2165/elsewhere/out.gds", "notpresent")
    assert got == "/mnt/vanished_2165", got


# ── the decision ────────────────────────────────────────────────────────────

def test_reports_only_citations_with_input_present_are_RE_RUN(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    row = D.classify_root(project, {_gone(project): ["reports/o/p.json"]})
    assert row["decision"] == D.RE_RUN, row


def test_one_authored_citation_flips_the_same_root_to_ARCHIVED(tmp_path):
    """ONE VARIABLE: the identical root and reference, cited from a file the
    flow does not regenerate."""
    project = _root_citing_reports_only(tmp_path)
    ref = _gone(project)
    assert D.classify_root(project, {ref: ["reports/o/p.json"]})["decision"] \
        == D.RE_RUN
    row = D.classify_root(project, {ref: ["reports/o/p.json", "RESULT.md"]})
    assert row["decision"] == D.ARCHIVED, row
    assert "RESULT.md" in row["reason"], row["reason"]


def test_a_missing_design_input_is_ARCHIVED_even_with_only_reports(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    (project / "phase1" / "generated_docs").rmdir()
    row = D.classify_root(project, {_gone(project): ["reports/o/p.json"]})
    assert row["decision"] == D.ARCHIVED, row
    assert "nothing to re-run from" in row["reason"], row["reason"]


def test_a_family_whose_roots_disagree_is_SPLIT_and_is_not_rounded():
    rows = [
        {"project": "/a/x", "decision": D.RE_RUN, "families": ["/fam"]},
        {"project": "/a/y", "decision": D.ARCHIVED, "families": ["/fam"]},
    ]
    fams = D.decide_families(rows)
    assert fams["/fam"]["decision"] == D.SPLIT
    assert fams["/fam"]["root_count"] == 2
    assert set(fams["/fam"]["roots"]) == {"/a/x", "/a/y"}


def test_a_uniform_family_takes_its_roots_decision():
    for d in (D.RE_RUN, D.ARCHIVED):
        rows = [{"project": "/a/x", "decision": d, "families": ["/f"]},
                {"project": "/a/y", "decision": d, "families": ["/f"]}]
        assert D.decide_families(rows)["/f"]["decision"] == d


# ── the annotation is a RECORD, not an exemption ────────────────────────────

def test_annotating_does_not_make_the_gate_pass(tmp_path):
    """THE ANTI-SOFTENING ARM. Verdict AND blocking count byte-identical."""
    project = _root_citing_reports_only(tmp_path)
    before = _gate(project)
    assert before.returncode == 1, before.stdout + before.stderr
    head_before = before.stdout.splitlines()[0]

    r = _debt("--annotate", project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (project / D.ANNOTATION_REL).is_file()

    after = _gate(project)
    assert after.returncode == 1, after.stdout + after.stderr
    assert after.stdout.splitlines()[0] == head_before, (
        f"the record changed the gate's deciding line:\n"
        f"  before {head_before}\n  after  {after.stdout.splitlines()[0]}")


def test_no_waiver_key_is_ever_written(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    _debt("--annotate", project)
    for f in project.rglob("*"):
        if f.is_file():
            assert GATE.WAIVER_KEY not in f.read_text(errors="ignore"), f
    assert not (project / "waivers.json").exists()


def test_the_annotation_preserves_the_path_verbatim_and_edits_nothing(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    ref = _gone(project)
    before = {str(f.relative_to(project)): f.read_bytes()
              for f in project.rglob("*") if f.is_file()}

    _debt("--annotate", project)

    after = {str(f.relative_to(project)): f.read_bytes()
             for f in project.rglob("*") if f.is_file()}
    # MEMBERSHIP: exactly one new file, and every prior file byte-identical.
    assert set(after) - set(before) == {D.ANNOTATION_REL}, \
        set(after) ^ set(before)
    for rel, blob in before.items():
        assert after[rel] == blob, f"{rel} was rewritten"

    rec = json.loads((project / D.ANNOTATION_REL).read_text())
    paths = [e["path"] for e in rec["references"]]
    assert paths == [ref], paths          # verbatim, not normalised
    assert rec["references"][0]["status"] == "UNRESOLVABLE"
    assert "does not exist on this host" in rec["references"][0]["reason"]
    assert "NOT A WAIVER" in rec["note"]


def test_the_record_is_append_only_and_never_shrinks(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    _debt("--annotate", project)
    rec = json.loads((project / D.ANNOTATION_REL).read_text())
    rec["references"].append({"path": "/mnt/vanished_2165/older/entry.gds",
                              "status": "UNRESOLVABLE", "cited_in": ["x.md"]})
    (project / D.ANNOTATION_REL).write_text(json.dumps(rec, indent=2))

    _debt("--annotate", project)
    again = json.loads((project / D.ANNOTATION_REL).read_text())
    names = {e["path"] for e in again["references"]}
    assert "/mnt/vanished_2165/older/entry.gds" in names, (
        "a second annotate dropped a prior entry — a debt ledger that shrinks "
        "silently is the failure this arm exists for")
    assert _gone(project) in names


def test_a_root_with_nothing_to_record_writes_nothing(tmp_path):
    project = tmp_path / "clean"
    (project / "reports").mkdir(parents=True)
    (project / "reports" / "x.json").write_text('{"ok": true}')
    r = _debt("--annotate", project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[SKIP]" in r.stdout
    assert not (project / D.ANNOTATION_REL).exists()


# ── refusals: never a default ───────────────────────────────────────────────

def test_an_unreadable_census_is_NOT_MEASURED(tmp_path):
    bad = tmp_path / "nope.json"
    for mode in ("--classify", "--sweep"):
        r = _debt(mode, "--census", bad)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "NOT_MEASURED" in r.stderr, r.stderr
        assert str(bad) in r.stderr, r.stderr


def test_annotate_refuses_a_non_directory(tmp_path):
    r = _debt("--annotate", tmp_path / "missing")
    assert r.returncode == 2 and "NOT_MEASURED" in r.stderr, r.stderr


# ── the population is the roots whose verdict MOVED ─────────────────────────

def test_the_population_is_the_moved_roots_not_every_root_naming_a_path(
        tmp_path):
    """A census's `new_paths` also names roots that ALREADY failed on the
    volatile-prefix class. Over the shipped #2158 census that is 700 roots
    where 659 moved; taking `new_paths` wholesale inflates the debt by 41."""
    census = tmp_path / "c.json"
    census.write_text(json.dumps({
        "moved": [["/a/moved", 0, 1]],
        "new_paths": {"/gone/moved/x": ["/a/moved"],
                      "/gone/already/y": ["/a/already_failing"]},
    }))
    loaded = D.load_census(census)
    assert set(loaded) == {"/a/moved"}, loaded


def test_a_census_with_no_moved_list_keeps_every_root(tmp_path):
    census = tmp_path / "c.json"
    census.write_text(json.dumps({"new_paths": {"/g/a/x": ["/r1", "/r2"]}}))
    assert set(D.load_census(census)) == {"/r1", "/r2"}


# ── the sweep measures; it does not forgive ─────────────────────────────────

def test_the_sweep_counts_an_annotated_root_as_still_blocking(tmp_path):
    project = _root_citing_reports_only(tmp_path)
    _debt("--annotate", project)
    census = tmp_path / "c.json"
    census.write_text(json.dumps({
        "moved": [[str(project), 0, 1]],
        "new_paths": {_gone(project): [str(project)]},
    }))
    out = tmp_path / "sweep.json"
    r = _debt("--sweep", "--census", census, "--json", out)
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(out.read_text())
    assert res["still_blocking_family_roots"] == 1, res
    assert res["still_blocking_distinct_roots"] == 1, res
    fam = next(iter(res["families"].values()))
    assert fam["annotated"] == 1 and fam["still_blocking"] == 1, fam
    assert "annotated roots are COUNTED" in r.stdout
    assert "DISTINCT root(s) still blocking" in r.stdout, r.stdout


def test_the_sweep_separates_family_root_pairs_from_distinct_roots(tmp_path):
    """A root citing TWO vanished producers appears in two family rows. The
    column therefore sums to pairs, not roots, and over the shipped census 246
    of 659 roots cite more than one — so both numbers are printed."""
    project = _root_citing_reports_only(tmp_path)
    second = f"/mnt/other_vanished_2165/{project.name}/phase3/out.gds"
    rep = project / "reports" / "orchestrator" / "phase2_one_shot.json"
    rep.write_text(rep.read_text().replace(
        "\"}]}", f" and {second}\"}}]}}"))
    census = tmp_path / "c.json"
    census.write_text(json.dumps({
        "moved": [[str(project), 0, 1]],
        "new_paths": {_gone(project): [str(project)],
                      second: [str(project)]},
    }))
    out = tmp_path / "s.json"
    r = _debt("--sweep", "--census", census, "--json", out)
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(out.read_text())
    assert len(res["families"]) == 2, res["families"]
    assert res["still_blocking_family_roots"] == 2, res
    assert res["still_blocking_distinct_roots"] == 1, res


def test_the_sweep_names_a_root_it_could_not_read(tmp_path):
    census = tmp_path / "c.json"
    census.write_text(json.dumps({
        "moved": [["/a/does_not_exist_2165", 0, 1]],
        "new_paths": {"/g/does_not_exist_2165/x": ["/a/does_not_exist_2165"]},
    }))
    r = _debt("--sweep", "--census", census)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT_MEASURED" in r.stdout, r.stdout
    assert "/a/does_not_exist_2165" in r.stdout, r.stdout


# ── the rule itself is untouched ────────────────────────────────────────────

def test_this_program_never_reimplements_the_gates_rule():
    """`references_of` must ASK the gate. A second copy of the predicate is how
    the two drift apart and the sweep starts measuring something else."""
    src = DEBT_PROG.read_text()
    for symbol in ("_derived_ephemeral", "_ANY_ABS_PATH_RE", "_PATH_RE",
                   "_inside_project", "_pinned_plugin_root", "_SCAN_GLOBS"):
        assert f"gate.{symbol}" in src, symbol
        assert f"def {symbol}" not in src, f"{symbol} is re-implemented here"
