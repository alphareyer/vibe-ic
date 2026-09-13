#!/usr/bin/env python3
"""Tests for warn_acceptance_policy_check.py"""
from __future__ import annotations
import json
import subprocess, sys
from pathlib import Path
import pytest
PROG = Path(__file__).resolve().parent.parent / "warn_acceptance_policy_check.py"
def _run(args, **kw): return subprocess.run([sys.executable, str(PROG)] + args, capture_output=True, text=True, **kw)
def test_help():
    r = _run(["--help"]); assert r.returncode == 0
def test_empty_project(tmp_path):
    # #521 — a project with no reports directory is VACUOUS (rc 2): not a
    # single gate report was read, so "every WARN is addressed" is true only
    # because no WARN was ever loaded.
    r = _run(["--project-dir", str(tmp_path)]); assert r.returncode == 2


def test_policy_does_not_reconsume_its_own_prior_warning(tmp_path):
    """A policy report is a derived verdict, not another source-gate WARN."""
    reports = tmp_path / "reports" / "phase2" / "gates"
    reports.mkdir(parents=True)
    (reports / "source_gate.json").write_text(json.dumps({
        "program": "source_gate",
        "findings": [{
            "severity": "WARN", "category": "documented", "file": "rtl.v",
            "line": 7, "message": "source warning",
        }],
    }))
    # This is exactly the shape this program wrote on its preceding invocation.
    (reports / "warn_acceptance_policy.json").write_text(json.dumps({
        "program": "warn_acceptance_policy_check",
        "findings": [{
            "severity": "WARN", "category": "UNADDRESSED_WARN", "file": "rtl.v",
            "line": 7, "message": "derived policy warning",
        }],
    }))
    (tmp_path / "WARN_ACCEPTANCE_LOG.md").write_text(
        "| gate | file | rule | resolution |\n"
        "| source_gate | rtl.v:7 | documented | input-grounded acceptance |\n"
    )

    r = _run(["--project-dir", str(tmp_path)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["summary"]["total_warns"] == 1
