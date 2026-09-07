"""`programs load when loaded by path` — a bare sibling import with no guard.

THE MUTATION IS ONE LINE, AND IT IS THE DEFECT AT ITS SMALLEST. Both arms ship
the SAME two programs at the same paths: a leaf `widget_table.py` and a
`widget_check.py` that imports it by BARE NAME. The only difference is the
`sys.path` line above that import.

  can_pass   widget_check puts its own directory on sys.path first  -> 0 offenders
  can_fail   the same import with that line removed                 -> 1 offender

BOTH ARMS HAVE THE SAME DENOMINATOR — two programs, loaded in both, and the
gate prints it (`loaded 2 program(s) by path from ...`) on each side so a reader
can check that the population did not move. That matters more than usual here:
this gate's failure mode when handed a SMALLER subject is a PASS, because a
directory with nothing in it has nothing that fails to import. Taking the
sibling away instead of taking the GUARD away would exit 0 over an empty sweep
and prove the vacuity path rather than the predicate — which is what
`gate_mutation_fixtures` means by "the mutation must leave the gate a corpus to
look at and change the ANSWER inside it".

WHY THE MUTATED IMPORT IS `from widget_table import ...` AND NOT A PACKAGE THAT
IS SIMPLY ABSENT. The gate classifies a `ModuleNotFoundError` naming a SIBLING
of the directory under test as the offence, and everything else — an absent
third-party package, a syntax error — as "not this gate's subject", reported by
name and deciding nothing. `widget_table.py` sits right beside the importer in
both arms, so the red arm is red for the gate's own subject and the refusal
names the offending file.

THE SUBJECT IS TINY ON PURPOSE. Against the real tree this gate starts 1395
child interpreters (~6 s at `--jobs 8`); a fixture that copied `programs/` would
make the mutation suite pay that twice per run and would couple this gate's arms
to every future import in the tree.

THE SUBJECT PATH IS ASKED OF THE ROW, never spelled here — `declared_subject_path`
returns whatever `--programs` on the declaration is anchored at, so the fixture
and the gate cannot come to disagree about where the corpus lives (vibe-ic#2019).
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "programs load when loaded by path"

_TABLE = '"""A leaf sibling. Imported by name, imports nothing itself."""\nWIDTH = 8\n'

#: The guard every program in this tree is required to carry before a bare
#: sibling import: `spec_from_file_location` does not add the file's directory
#: to `sys.path`, and running the same file as `__main__` does.
_GUARDED = '''"""A checker that resolves its sibling however it is loaded."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from widget_table import WIDTH  # noqa: E402

LIMIT = WIDTH
'''

_BARE = '''"""The same checker with the guard removed. Nothing else differs."""
import os
import sys

from widget_table import WIDTH

LIMIT = WIDTH
'''


def _tree(work: Path, checker_body: str) -> Path:
    root = F.git_init(work / "subject")
    progs = root / F.declared_subject_path(GATE, "programs")
    progs.mkdir(parents=True)
    (progs / "widget_table.py").write_text(_TABLE)
    (progs / "widget_check.py").write_text(checker_body)
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    """Both programs load by path: the importer guards its own `sys.path`."""
    return _tree(work, _GUARDED)


def can_fail(work: Path):
    """The guard alone is removed; the same two programs are swept."""
    return _tree(work, _BARE), "widget_check.py"
