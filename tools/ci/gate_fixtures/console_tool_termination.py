"""Two-arm fixture for ``console tool termination``.

The subject is a real Python module at precisely the programs directory the
dispatcher passes to the checker.  Both arms execute a Netgen command through
one of the checker's recognised execution helpers; only ``-batch`` changes.
That keeps a corpus present in both arms and proves the command-level
terminator predicate rather than the missing-directory refusal path.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "console tool termination"


def _programs_dir(root: Path) -> Path:
    rel = F.declared_subject_path(GATE, "programs")
    path = root / rel
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tree(work: Path, command: str) -> Path:
    root = work / "subject"
    programs = _programs_dir(root)
    # `_docker_exec` is a real recognised execution seam.  The fixture does
    # not run Netgen; it supplies the AST-visible command the gate is designed
    # to audit, just as a shipped producer does.
    (programs / "synthetic_netgen_driver.py").write_text(
        "def _docker_exec(container, command):\n"
        "    return 0, '', ''\n\n"
        f"cmd = {command!r}\n"
        "_docker_exec('fixture', cmd)\n",
        encoding="utf-8")
    return root


def can_pass(work: Path) -> Path:
    """Netgen carries its durable command-level terminator."""
    return _tree(work, "netgen -batch source fixture.tcl")


def can_fail(work: Path):
    """The same executable call loses only ``-batch`` and must be rejected."""
    # Keep a leading option: the checker deliberately classifies command-form
    # invocations (`netgen -…`) rather than prose mentioning a bare tool name.
    return (_tree(work, "netgen -noconsole source fixture.tcl"),
            "netgen is executed without -batch")
