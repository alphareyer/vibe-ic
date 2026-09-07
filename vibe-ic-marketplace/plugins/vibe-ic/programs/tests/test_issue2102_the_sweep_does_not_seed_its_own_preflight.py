#!/usr/bin/env python3
"""The hygiene sweep wrote the residue its own FIRST gate refuses.

THE DEFECT, MEASURED (8HD-9, 2026-09-07, pinned image label 0.3.48, over a
pristine clone of 94617408759e)
==========================================================================
`tools/ci/repo_hygiene_gates.sh` opens with `attestation_preflight_check`, and
that gate is FIRST on purpose: "a single `.pyc` from any gate below it lands in
the snapshot path set, and the preflight would then be reporting residue this
sweep had just written." The position keeps the answer honest for the run that
asks it. It does nothing for the NEXT run in the same checkout::

    before the sweep   preflight rc=0  no residue, no tracked drift [8058 files]
    after  the sweep   preflight rc=1  1 bytecode/cache artefact(s): .pytest_cache

`git status --porcelain` was EMPTY at both ends — `.pytest_cache` is gitignored
— which is the same cleanliness-instrument-cannot-see-it asymmetry the preflight
was written from, one directory kind over.

The writer is the sweep's OWN pytest. `repo_hygiene_gates.sh` runs exactly one:
`python3 -m pytest -q "$ROOT/tools/test_liar_census.py"`, with cwd `$ROOT`. Run
alone against a clean tree it produced `.pytest_cache/v/cache/nodeids` holding
130 node ids, every one of them `tools/test_liar_census.py::…` — the same
directory, the same contents, as the full sweep left behind. With
`-p no:cacheprovider` the same command reported the same `128 passed,
2 skipped` and left nothing.

So the gate was not wrong and the checkout was not dirty by anyone's neglect:
the sweep seeded it. THIS FILE IS THE PIN. It drives the mechanism in both
directions in a temporary directory, drives the preflight over a temporary git
repository in both directions, and then asserts the flag is present at the one
site in the sweep that needs it — so deleting the flag turns this file red.

AND THE REMEDY LINE WAS WRONG FOR THIS CAUSE. With `PYTHONDONTWRITEBYTECODE`
set, `remedy_for` answered a `.pytest_cache` by telling the operator to pass
`-B` to an isolated child. No `PYTHON*` variable and no `-B` suppresses a tool's
own cache directory, because the interpreter is not what wrote it. The last two
tests hold the two branches apart.

chip-AGNOSTIC: shell grammar, pytest CLI and filesystem only. No design, PDK,
vendor, process or part literal appears here or can affect the result.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PROGRAMS.parents[3]
_SWEEP = _REPO_ROOT / "tools" / "ci" / "repo_hygiene_gates.sh"
_PREFLIGHT = _PROGRAMS / "attestation_preflight_check.py"

sys.path.insert(0, str(_PROGRAMS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


APC = _load("_apc_under_test", _PREFLIGHT)

#: A test module with nothing in it but two passing cases. The subject here is
#: what pytest WRITES beside the run, so the run itself must be trivial.
_TRIVIAL = """
def test_one():
    assert True


def test_two():
    assert 1 + 1 == 2
"""


def _run_pytest(cwd: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *extra, "t_probe.py"],
        cwd=str(cwd), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)


def _counts(output: str) -> str:
    """pytest's own summary line, so the two arms can be compared on WHAT RAN."""
    m = re.findall(r"(\d+) passed", output)
    return m[-1] if m else "NO SUMMARY"


# ─────────────────── 1. the mechanism, both directions ────────────────────

def test_pytest_seeds_a_cache_directory_in_its_cwd(tmp_path):
    """THE NEGATIVE CONTROL. It must observe the residue, or its sibling below
    asserting the absence of residue proves nothing at all."""
    (tmp_path / "t_probe.py").write_text(_TRIVIAL, encoding="utf-8")
    proc = _run_pytest(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert (tmp_path / ".pytest_cache").is_dir(), (
        "pytest did not write its cache directory beside the run, so this "
        "control cannot observe the defect it exists to observe:\n" + proc.stdout)
    assert _counts(proc.stdout) == "2"


def test_the_flag_suppresses_the_cache_and_changes_nothing_else(tmp_path):
    """The fix, and the proof that it is not a change to what runs."""
    (tmp_path / "t_probe.py").write_text(_TRIVIAL, encoding="utf-8")
    without = _run_pytest(tmp_path)
    assert (tmp_path / ".pytest_cache").is_dir()

    other = tmp_path / "arm2"
    other.mkdir()
    (other / "t_probe.py").write_text(_TRIVIAL, encoding="utf-8")
    with_flag = _run_pytest(other, "-p", "no:cacheprovider")

    assert with_flag.returncode == 0, with_flag.stdout
    assert not (other / ".pytest_cache").exists(), (
        "`-p no:cacheprovider` did not suppress the cache directory:\n"
        + with_flag.stdout)
    assert _counts(without.stdout) == _counts(with_flag.stdout), (
        "the flag changed WHAT RAN, not only what was written:\n"
        f"without={without.stdout}\nwith={with_flag.stdout}")


# ─────────── 2. the preflight really refuses that directory ──────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_the_preflight_refuses_a_tool_cache_and_passes_without_one(tmp_path):
    """Both directions, over the REAL checker, in a repository of our own.

    A tool cache is what the sweep leaves, and the whole finding rests on the
    preflight counting it. Asserting that here means a future narrowing of
    `RESIDUE_DIRS` reddens this file instead of silently making the sweep's
    residue invisible.
    """
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "a.txt").write_text("x\n", encoding="utf-8")
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")

    env_flag = os.environ.get(APC.ENV_FLAG)
    os.environ[APC.ENV_FLAG] = "1"
    try:
        assert APC.main([str(repo), "--repo", str(repo)]) == 0

        (repo / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
        (repo / ".pytest_cache" / "v" / "cache" / "nodeids").write_text(
            json.dumps(["t_probe.py::test_one"]), encoding="utf-8")
        assert APC.main([str(repo), "--repo", str(repo)]) == 1
    finally:
        if env_flag is None:
            os.environ.pop(APC.ENV_FLAG, None)
        else:
            os.environ[APC.ENV_FLAG] = env_flag


# ───────────────── 3. the site pin, on the sweep itself ──────────────────

def _sweep_pytest_commands(text: str) -> list:
    """Every `python3 -m pytest` `repo_hygiene_gates.sh` runs ITSELF.

    Comment lines are dropped FIRST: this script argues at length in prose and
    several of those sentences quote a pytest command line, so a presence test
    over the raw text would match an argument about a command instead of a
    command. Backslash continuations are joined, so a flag written on the next
    line still counts as part of the invocation it belongs to.
    """
    body = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    joined, buf = [], ""
    for ln in body:
        if ln.rstrip().endswith("\\"):
            buf += ln.rstrip()[:-1] + " "
            continue
        joined.append(buf + ln)
        buf = ""
    if buf:
        joined.append(buf)
    return [c for c in joined if re.search(r"\bpython3?\s+-m\s+pytest\b", c)]


def test_every_pytest_the_sweep_runs_itself_suppresses_its_cache():
    """THE PIN. Deleting the flag from the sweep turns this red.

    The denominator is asserted before the finding: a parser that matched
    nothing would pass this test while the sweep went on seeding its own
    preflight, which is the shape the repository refuses everywhere else.
    """
    cmds = _sweep_pytest_commands(_SWEEP.read_text(encoding="utf-8"))
    assert cmds, (
        f"no `python3 -m pytest` invocation was found in {_SWEEP} — either the "
        f"sweep stopped running one (then delete this test with it) or this "
        f"parser stopped matching, and an empty denominator is not a pass")
    bad = [c.strip() for c in cmds
           if "-p no:cacheprovider" not in c and "cache_dir=" not in c]
    assert not bad, (
        "a pytest the hygiene sweep runs itself writes a cache directory into "
        "the tree its own FIRST gate then refuses:\n  " + "\n  ".join(bad))


# ─────────────── 4. the remedy names the writer, not the reader ──────────

def test_the_remedy_for_a_tool_cache_does_not_send_the_operator_to_B():
    """MEASURED: with the flag set, a `.pytest_cache` was answered with `-B`.

    `-B` and `PYTHONDONTWRITEBYTECODE` are properties of the interpreter and a
    tool cache is not written by the interpreter, so that sentence sent the
    operator somewhere the fix could not be. Both branches are asserted here
    because the bytecode one is still right and must not be lost to the repair.
    """
    only_cache = APC.remedy_for(residues=["/r/.pytest_cache"], drift=[],
                                untracked_paths=[], env_value="1")
    assert any("no:cacheprovider" in line for line in only_cache), only_cache
    assert not any("`-B` FLAG" in line for line in only_cache), only_cache

    only_bytecode = APC.remedy_for(residues=["/r/pkg/__pycache__", "/r/a.pyc"],
                                   drift=[], untracked_paths=[], env_value="1")
    assert any("`-B` FLAG" in line for line in only_bytecode), only_bytecode
    assert not any("no:cacheprovider" in line for line in only_bytecode), \
        only_bytecode

    both = APC.remedy_for(residues=["/r/.pytest_cache", "/r/pkg/__pycache__"],
                          drift=[], untracked_paths=[], env_value="1")
    assert any("`-B` FLAG" in line for line in both), both
    assert any("no:cacheprovider" in line for line in both), both


if __name__ == "__main__":  # pragma: no cover
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
