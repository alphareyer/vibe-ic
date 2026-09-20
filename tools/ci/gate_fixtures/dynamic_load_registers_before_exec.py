"""`dynamic load registers before exec` — a by-path load must reach the import
table BEFORE `exec_module` when the loaded file defines a `@dataclass`.

The subject is the four-clause shape the gate decides on: one function holding
`spec_from_file_location` -> `module_from_spec` -> `exec_module` by line order,
a literal path, and a target module whose top level carries a `@dataclass`
with a string annotation that has no module prefix.

The mutation deletes ONE line — the registration between the last two calls —
which is the whole defect and the whole fix. Both files survive it; the loader
still loads, the dataclass is still there, and the gate is still handed the
same two files to read.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "dynamic load registers before exec"

_REGISTER = "    sys.modules[spec.name] = mod\n"

_LOADER = '''import importlib.util
import sys
from pathlib import Path


def load_target():
    path = Path(__file__).resolve().parent / "target.py"
    spec = importlib.util.spec_from_file_location("target", path)
    mod = importlib.util.module_from_spec(spec)
%s    spec.loader.exec_module(mod)
    return mod
'''

# `from __future__ import annotations` + an UNQUOTED, unprefixed annotation:
# that pair is what makes the compiler store the bare source text `str`, which
# is the one shape that reaches the branch of `dataclasses._is_type` that
# dereferences `sys.modules.get(cls.__module__)`. A quoted `"str"` stores
# `"'str'"` and never gets there, so it would build a target the gate is right
# to ignore and a can-fail arm that proved nothing.
_TARGET = '''from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Row:
    name: str
    count: int = 0
'''


def _tree(work: Path, register: str) -> Path:
    root = F.git_init(work / "subject")
    pkg = root / "tools"
    pkg.mkdir()
    (pkg / "loader.py").write_text(_LOADER % register)
    (pkg / "target.py").write_text(_TARGET)
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    return _tree(work, _REGISTER)


def can_fail(work: Path):
    return _tree(work, ""), "no sys.modules registration"
