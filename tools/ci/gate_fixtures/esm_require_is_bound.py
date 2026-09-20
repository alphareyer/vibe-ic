"""`ESM require is bound` — an ES module that CALLS `require` must BIND it.

The mutation deletes the two BINDING lines and leaves the CALL, which is the
pre-fix shape of `mcp-eda/src/index.js` exactly: the file is still an ES
module, still read, still holds a use of `require` — only the binding is gone.
The corpus does not shrink; the answer inside it changes.

`.mjs` rather than `"type": "module"` + `.js`, so the subject states its own
module kind and the gate never has to resolve a manifest to know what it is
reading.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "ESM require is bound"

_BINDING = (
    'import { createRequire } from "module";\n'
    "const require = createRequire(import.meta.url);\n"
)

_USE = (
    'export function readManifest(p) {\n'
    '  const fs = require("fs");\n'
    '  return fs.readFileSync(p, "utf8");\n'
    "}\n"
)


def _tree(work: Path, binding: str) -> Path:
    root = F.git_init(work / "subject")
    src = root / "mcp-eda" / "src"
    src.mkdir(parents=True)
    (src / "index.mjs").write_text(binding + _USE)
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    return _tree(work, _BINDING)


def can_fail(work: Path):
    return _tree(work, ""), "unbound use"
