#!/usr/bin/env python3
"""A refusal that names no reference cannot be attributed to a writer.

MEASURED on spm (lane icspm5, 2026-09-23). Stage 1's P0 umbrella recorded

    FAIL: project_outputs_in_tree_check — [FAIL] project_outputs_in_tree_check:
    1 blocking external-storage reference(s) in this project's declaration
    file(s) (1 live, 0 dangling, 0 outside-root) — this is what the gate exits
    1 on:

and that is the WHOLE record. The path, and the declaration file citing it,
are on the NEXT lines of the gate's output, and the umbrella's reader
(`flow_compliance_check._p0_first_line`) keeps exactly one line. So the P0 row
names a refusal it cannot attribute, and the volatile path it refused is swept
minutes later — after which the gate exits 0 and the evidence is gone for good.

I could not attribute that occurrence afterwards, and this is why: the only
durable record of it carries no reference. The gate is RIGHT to refuse; the
refusal is simply unusable by the one consumer that stores it.

The header line now names the first offending reference and its citing file.
No classification changes, nothing is weakened, and the detail lines below are
untouched — this only moves what a one-line reader gets.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

CHECK = PROGRAMS / "project_outputs_in_tree_check.py"


def _run(project):
    return subprocess.run([sys.executable, str(CHECK), str(project)],
                          capture_output=True, text=True)


def _project(tmp_path, cited=None):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    doc = {"verdict": "PASS"}
    if cited:
        doc["artefact"] = str(cited)
    (tmp_path / "reports" / "a_declared_output.json").write_text(json.dumps(doc))
    return tmp_path


def test_the_refusal_line_names_the_reference_and_its_file(tmp_path):
    """THE FIX. A one-line reader must come away able to act."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    live = Path("/tmp") / f"icspm5_p0tmp_{tmp_path.name}.gds"
    live.write_text("x")
    try:
        r = _run(_project(tmp_path, live))
        assert r.returncode == 1, r.stdout
        head = [ln for ln in r.stdout.splitlines()
                if ln.startswith("[FAIL]")][0]
        assert str(live) in head, head
        assert "reports/a_declared_output.json" in head, head
    finally:
        live.unlink(missing_ok=True)


def test_a_clean_project_still_says_nothing_of_the_kind(tmp_path):
    """The negative arm: no reference, no refusal, no name."""
    r = _run(_project(tmp_path))
    assert r.returncode == 0, r.stdout
    assert "blocking external-storage" not in r.stdout


def test_the_detail_block_is_unchanged(tmp_path):
    """And the fix must not cost the fuller listing a reader already had."""
    live = Path("/tmp") / f"icspm5_p0tmp2_{tmp_path.name}.gds"
    live.write_text("x")
    try:
        r = _run(_project(tmp_path, live))
        assert "live external-storage artifact(s)" in r.stdout, r.stdout
        assert f"referenced in reports/a_declared_output.json → {live}" \
            in r.stdout, r.stdout
    finally:
        live.unlink(missing_ok=True)
