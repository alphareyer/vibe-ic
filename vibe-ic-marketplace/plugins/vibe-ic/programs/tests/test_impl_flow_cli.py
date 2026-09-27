"""W1 (`--librelane` v1): the flags, the knob map, the child argv, the gate.

Contracts:
  1. Every gated runner's REAL parser takes `--librelane` and the reserved
     `--orfs` (mutually exclusive), and every other option it has carries a
     disposition in `_impl_flow.KNOBS` -- a new option without one is red.
  2. The gate: the default resolves to vibe-ic and writes nothing; `--orfs`
     is refused by name; a REFUSED knob the invocation set is refused by name
     before anything else is said about the flag; a flag no runner consumes
     yet is IMPL_NOT_YET_WIRED and writes nothing.
  3. Each runner's real main(), as a subprocess: `--librelane`, `--orfs`, and
     a missing flag on a librelane project each exit 2 with the named reason
     and leave the project tree byte-identical.
  4. Every runner-to-runner spawn site forwards `child_argv(project)`, which
     is [] with no record: the default child argv is unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402

RUNNERS = sorted(IF.KNOBS)


class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def _real_parser(module_name: str, monkeypatch) -> argparse.ArgumentParser:
    """The parser `module.main()` builds, captured at its parse call."""
    mod = importlib.import_module(module_name)

    def _capture(self, *a, **k):
        raise _Captured(self)

    with monkeypatch.context() as m:
        m.setattr(argparse.ArgumentParser, "parse_args", _capture)
        m.setattr(argparse.ArgumentParser, "parse_known_args", _capture)
        m.setattr(sys, "argv", [module_name, "/nonexistent-project"])
        with pytest.raises(_Captured) as ei:
            mod.main()
    return ei.value.parser


def _tree(project: Path) -> dict:
    return {str(p.relative_to(project)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                          if p.is_file() else "dir")
            for p in sorted(project.rglob("*"))}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("a design input\n")
    return p


@pytest.mark.parametrize("runner", RUNNERS)
def test_every_knob_has_a_disposition(runner, monkeypatch):
    parser = _real_parser(runner, monkeypatch)
    dests = {a.dest for a in parser._actions if a.dest != "help"}
    assert IF.FLAG_DESTS <= dests, f"{runner} lacks the flags"
    assert dests - IF.FLAG_DESTS == set(IF.KNOBS[runner]), (
        f"{runner}: options without a disposition "
        f"{sorted(dests - IF.FLAG_DESTS - set(IF.KNOBS[runner]))}; "
        f"dispositions for no option "
        f"{sorted(set(IF.KNOBS[runner]) - dests)}")
    for dest, (disposition, why) in IF.KNOBS[runner].items():
        assert disposition in (IF.HONOURED, IF.MAPPED, IF.REFUSED), dest
        assert why.strip(), dest


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_two_flags_are_mutually_exclusive(runner, monkeypatch, capsys):
    parser = _real_parser(runner, monkeypatch)
    with pytest.raises(SystemExit):
        parser.parse_args(["/p", "--librelane", "--orfs"])
    assert "not allowed with" in capsys.readouterr().err


def test_the_gate_on_the_real_phase3_parser(project, monkeypatch):
    parser = _real_parser("phase3_one_shot_runner", monkeypatch)
    before = _tree(project)

    def gate(*argv):
        return IF.gate(project, parser.parse_args([str(project), *argv]),
                       runner="phase3_one_shot_runner", parser=parser)

    assert gate() == IF.IMPL_DEFAULT
    # A window is the parser default's opposite, and a default is no request.
    assert gate("--entry-step", "17", "--exit-step", "17") == IF.IMPL_DEFAULT
    for argv, cls in ((["--orfs"], IF.IMPL_NOT_YET_SUPPORTED),
                      (["--librelane"], IF.IMPL_NOT_YET_WIRED),
                      (["--librelane", "--die-um", "400x400", "--util", "0.5",
                        "--spare-density", "0.05"], IF.IMPL_NOT_YET_WIRED),
                      (["--librelane", "--entry-step", "17", "--exit-step",
                        "17"], IF.IMPL_KNOB_UNSUPPORTED),
                      (["--librelane", "--force-step", "pnr"],
                       IF.IMPL_KNOB_UNSUPPORTED)):
        with pytest.raises(IF.ImplRefusal) as ei:
            gate(*argv)
        assert ei.value.reason_class == cls, argv
    with pytest.raises(IF.ImplRefusal) as ei:
        gate("--librelane", "--entry-step", "17", "--exit-step", "17")
    assert "--entry-step" in str(ei.value) and "--exit-step" in str(ei.value)
    assert _tree(project) == before


def test_a_wired_runner_passes_the_gate(project, monkeypatch):
    parser = _real_parser("phase3_one_shot_runner", monkeypatch)
    monkeypatch.setattr(IF, "WIRED_RUNNERS",
                        frozenset({"phase3_one_shot_runner"}))
    args = parser.parse_args([str(project), "--librelane"])
    assert IF.gate(project, args, runner="phase3_one_shot_runner",
                   parser=parser) == IF.IMPL_LIBRELANE
    assert not IF.record_path(project).exists()   # the gate never writes


def test_child_argv_follows_the_record(project):
    assert IF.child_argv(project) == []
    IF.write_record(project, "librelane", resolved_by="t")
    assert IF.child_argv(project) == ["--librelane"]


def _run_main(runner: str, project: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(PROGRAMS / f"{runner}.py"), str(project), *argv],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
             "HOME": str(project.parent)})


@pytest.mark.parametrize("runner", RUNNERS)
def test_each_real_main_refuses_by_name_and_writes_nothing(runner, project):
    before = _tree(project)
    cp = _run_main(runner, project, "--librelane")
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_NOT_YET_WIRED}" in cp.stderr
    cp = _run_main(runner, project, "--orfs")
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_NOT_YET_SUPPORTED}" in cp.stderr
    assert _tree(project) == before

    IF.write_record(project, "librelane", resolved_by="test")
    before = _tree(project)
    cp = _run_main(runner, project)      # a child that forgot the flag
    assert cp.returncode == 2, cp.stderr[-2000:]
    assert f"REFUSED: {IF.IMPL_MODE_CONFLICT}" in cp.stderr
    assert _tree(project) == before


# Runner-to-runner spawn sites, per file: how the file names a runner it
# spawns, and which of those sites are exempt. A new site moves the count and
# reddens this test until it forwards the mode (or is exempted with a reason).
_SPAWN = re.compile(r'_phase_runner\("|"[a-z0-9_]+_one_shot_runner\.py"')
_SPAWN_SITES = {
    "vibe_ic_one_shot_runner.py": 5,
    "design_one_shot_runner.py": 3,
    "phase23_one_shot_runner.py": 2,
    # phase1_doc_one_shot_runner is Phase 1's own doc track, not a gated
    # runner: it takes no flag and Phase 1 runs unchanged under the flag.
    "phase1_one_shot_runner.py": 0,
    "analog_one_shot_runner.py": 0,
}


@pytest.mark.parametrize("fname", sorted(_SPAWN_SITES))
def test_every_spawn_site_forwards_the_mode(fname):
    src = (PROGRAMS / fname).read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    sites = len(_SPAWN.findall(code))
    exempt = code.count('"phase1_doc_one_shot_runner.py"')
    assert sites - exempt == _SPAWN_SITES[fname], (
        f"{fname}: {sites - exempt} spawn sites, pinned "
        f"{_SPAWN_SITES[fname]} -- forward _impl_flow.child_argv(project) "
        "at the new one and move the pin")
    assert code.count("_impl_flow.child_argv(project)") == _SPAWN_SITES[fname]


def test_the_phase2_argv_builder_is_unchanged_by_default():
    import vibe_ic_one_shot_runner as V
    kw = dict(top_name="t", container="c", max_rtl_repair_retries=3,
              lec_max_completed_rungs=None, skip_hardware=True,
              skip_phase3=False, skip_analog=True, entry_step=None,
              exit_step="8")
    base = V._phase2_runner_argv(Path("/p"), **kw)
    assert base == ["/p", "--top-name", "t", "--container", "c",
                    "--max-rtl-repair-retries", "3", "--skip-hardware",
                    "--skip-analog", "--exit-step", "8"]
    assert V._phase2_runner_argv(Path("/p"), **kw, impl_argv=[]) == base
    assert V._phase2_runner_argv(Path("/p"), **kw,
                                 impl_argv=["--librelane"]) == base + ["--librelane"]
