"""`MCP program path resolves` — a tool must reach the program it spawns.

The known-good subject spawns a program that IS in the tree, through the
rooted `dirname(import.meta.url)` + `path.resolve` shape the real server uses.
The mutation re-points that same expression through a layout segment this tree
does not carry — the retired-edition defect the gate is the regression guard
for — so both the file AND its directory are absent, which is what the gate's
clause 7 requires before it will call a site DECIDED rather than undecided.

The source file, the spawn site and the program all survive the mutation. Only
the segment between them changes.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "MCP program path resolves"

_SRC = '''import path from "path";

const here = path.dirname(new URL(import.meta.url).pathname);

export function gatePath() {
  return path.resolve(here, "..", "..", %s"programs", "fixture_gate.py");
}
'''


def _tree(work: Path, detour: str) -> Path:
    root = F.git_init(work / "subject")
    src = root / "mcp-eda" / "src"
    src.mkdir(parents=True)
    (src / "index.mjs").write_text(_SRC % detour)
    programs = root / "programs"
    programs.mkdir()
    (programs / "fixture_gate.py").write_text("print('fixture gate')\n")
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    return _tree(work, "")


def can_fail(work: Path):
    return _tree(work, '"retired_edition", '), "fixture_gate.py"
