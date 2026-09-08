"""Issue2154: the existing landing delta consumes affected #1082 subjects.

The atomic/plain fixtures and range controls are recovered from 6494f3f6.
They assert the scanner and actual consumer behavior, not an import spelling.
No benchmark corpus or evaluation inputs are used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import atomic_artifact_write_check as AW
import landing_hygiene_ratchet_check as D

REL = D.PROGRAMS_REL
REPO = PROGRAMS.parents[3]
ATOMIC_PROGRAM = '''\
import argparse
from pathlib import Path
import _atomic_artefact as _atomic

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)
    result = {"ok": True}
    if a.json_out:
        _atomic.write_json(Path(a.json_out), result)
    return 0
'''
PLAIN_PROGRAM = ATOMIC_PROGRAM.replace(
    "_atomic.write_json(Path(a.json_out), result)",
    "Path(a.json_out).write_text(str(result))")


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _commit(root, message):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "subject"
    (root / REL).mkdir(parents=True)
    (root / REL / "p.py").write_text(ATOMIC_PROGRAM)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    _git(root, "config", "user.name", "synthetic")
    return root, _commit(root, "atomic base")


def _run(argv, evidence, *, env=None):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    record = {"argv": argv, "rc": proc.returncode,
              "stdout": proc.stdout, "stderr": proc.stderr}
    evidence.write_text(json.dumps(record, indent=2))
    print(json.dumps(record, sort_keys=True))
    return proc


def _cli(root, base, evidence):
    return _run([sys.executable, "-B", str(PROGRAMS / "landing_hygiene_ratchet_check.py"),
                 "--repo", str(root), "--plugin-root", str(PROGRAMS.parent),
                 "--base", base, "--json", str(evidence.with_suffix(".report.json"))],
                evidence)


def test_a_range_that_adds_a_plain_write_is_refused_by_name(repo, tmp_path):
    root, base = repo
    path = root / REL / "p.py"
    assert AW.scan_program(path) == []  # positive fixture, before mutation
    path.write_text(PLAIN_PROGRAM)
    assert AW.scan_program(path)       # actual #1082 predicate sees the defect
    _commit(root, "restore plain write")
    rec = D.atomic_added_offenders(root, base)
    assert rec["not_measured"] == {}
    assert len(rec["added"]) == 1
    hit = rec["added"][0]
    assert hit["file"] == f"{REL}/p.py"
    assert hit["line"] > 0
    assert hit["form"] == ".write_text(...)"
    proc = _cli(root, base, tmp_path / "plain-cli.json")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"{hit['file']}:{hit['line']}" in proc.stdout


def test_a_range_that_changes_nothing_relevant_passes(repo, tmp_path):
    root, base = repo
    (root / REL / "p.py").write_text(ATOMIC_PROGRAM.replace(
        '{"ok": True}', '{"ok": True, "n": 1}'))
    _commit(root, "harmless data change")
    rec = D.atomic_added_offenders(root, base)
    assert rec["not_measured"] == {}
    assert rec["added"] == []
    assert rec["programs_examined"] == 1
    proc = _cli(root, base, tmp_path / "atomic-cli.json")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_moving_an_existing_offender_is_not_adding_one(repo):
    root, _ = repo
    path = root / REL / "p.py"
    path.write_text(PLAIN_PROGRAM)
    base = _commit(root, "existing plain writer")
    path.write_text("# unrelated new comment\n\n" + PLAIN_PROGRAM)
    _commit(root, "move existing line")
    rec = D.atomic_added_offenders(root, base)
    assert rec["not_measured"] == {}
    assert rec["added"] == []
    assert rec["programs_examined"] == 1


def test_an_unreadable_range_is_not_a_pass(tmp_path):
    root = tmp_path / "not-a-repository"
    (root / REL).mkdir(parents=True)
    rec = D.atomic_added_offenders(root, "HEAD")
    assert rec["added"] == []
    assert "range" in rec["not_measured"]
    proc = _cli(root, "HEAD", tmp_path / "refused-cli.json")
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_flow_only_declaration_change_is_measured_on_each_arm(repo, tmp_path):
    root, _ = repo
    path = root / REL / "p.py"
    path.write_text('from pathlib import Path\nout = Path("reports/new.json")\n'
                    'out.write_text("{}")\n')
    flow = root / D._FLOW_REL
    flow.parent.mkdir(parents=True)
    flow.write_text('steps:\n- programs: [p]\n  required_outputs: [reports/old.json]\n')
    base = _commit(root, "plain literal is not yet declared")
    assert AW.scan_program(path, {"reports/old.json"}) == []
    assert AW.scan_program(path, {"reports/new.json"})
    flow.write_text('steps:\n- programs: [p]\n  required_outputs: [reports/new.json]\n')
    head = _commit(root, "declare the existing plain destination")
    assert _git(root, "diff", "--name-only", base, head) == D._FLOW_REL
    added = D.atomic_added_offenders(root, base, head)
    assert added["not_measured"] == {}
    assert added["programs_examined"] == 1
    assert [r["file"] for r in added["added"]] == [f"{REL}/p.py"]
    removed = D.atomic_added_offenders(root, head, base)
    assert removed["not_measured"] == {}
    assert removed["programs_examined"] == 1
    assert removed["added"] == []
    # A worktree rewrite cannot change what the named candidate COMMIT said.
    path.write_text(ATOMIC_PROGRAM)
    assert D.atomic_added_offenders(root, base, head) == added
    proc = _cli(root, base, tmp_path / "flow-only-cli.json")
    assert proc.returncode == 1, proc.stdout + proc.stderr


@pytest.mark.parametrize("plain,expected_rc", [(False, 0), (True, 1)],
                         ids=["atomic", "plain"])
def test_existing_cheap_unit_propagates_the_real_consumer_verdict(repo, tmp_path,
                                                                plain, expected_rc):
    root, base = repo
    (root / REL / "p.py").write_text(PLAIN_PROGRAM if plain else
        ATOMIC_PROGRAM.replace('{"ok": True}', '{"ok": True, "n": 1}'))
    _commit(root, "plain mutation" if plain else "atomic control")
    script = (REPO / "tools/gatekeeper-land.sh").read_text()
    start = script.index("landing_unit_cheap_hygiene_ratchet() {")
    end = script.index("\n}\n", start) + len("\n}\n")
    # Execute the shipped unit body verbatim. Only its generic recorder is
    # replaced by direct argv execution; no checker or verdict is stubbed.
    unit = tmp_path / "actual-unit.sh"
    unit.write_text('run() { shift 2; "$@"; }\n' + script[start:end]
                    + '\nlanding_unit_cheap_hygiene_ratchet cheap:hygiene-ratchet\n')
    env = dict(os.environ, ROOT=str(root), PLUGIN=str(PROGRAMS.parent),
               PROGRAMS=str(PROGRAMS), BASE=base, GK_RANGE_N="1")
    proc = _run(["bash", str(unit)], tmp_path / "unit-child.json", env=env)
    assert proc.returncode == expected_rc, proc.stdout + proc.stderr
    if plain:
        assert f"{REL}/p.py:" in proc.stdout
