"""`no stored runtime-image digest` — the runtime image's identity is resolved
from the host, never stored in tracked Python.

The known-good subject resolves the digest from the environment and REFUSES
when it is absent, which is the shape the three removed copies were changed
into. The mutation restores the coupling in the exact form the gate exists to
catch: a module-level binding whose NAME carries a whole `IMAGE` segment and
whose concatenated string literals spell `sha256:` + 64 hex.

THE DIGEST IS SYNTHESISED AT RUN TIME, never stored here. A fixture file that
carried a real 64-hex image digest would be the very artefact the gate keeps
out of the tree — exempt by venue, and still wrong. `sha256` of a fixture
sentence is a 64-hex run this fixture computes and never commits.

ALL FOUR DECLARED VENUES ARE BUILT, not just the one the digest lands in: the
gate REFUSES (rc 2) when ANY declared venue is absent — "a venue that is not
there was not searched" — and an arm that reached that refusal would prove the
gate notices a missing directory, not that it notices a stored digest. Measured:
with `tools/` alone, the KNOWN-GOOD arm came back rc 2.
"""
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_mutation_fixtures as F  # noqa: E402

GATE = "no stored runtime-image digest"

_RESOLVED = '''import os


def resolved_image_digest():
    value = os.environ.get("FIXTURE_IMAGE_DIGEST")
    if not value:
        raise RuntimeError("no image digest on this host; resolve it, never store it")
    return value
'''


def _stored() -> str:
    hexes = hashlib.sha256(b"gate fixture subject").hexdigest()
    return f'\nEDA_IMAGE_DIGEST = "sha256:" + "{hexes}"\n'


_VENUES = ("vibe-ic-marketplace/plugins/vibe-ic/programs",
           "vibe-ic-marketplace/plugins/vibe-ic/tools",
           "vibe-ic-marketplace/plugins/vibe-ic/mcp-eda",
           "tools")


def _tree(work: Path, stored: str) -> Path:
    root = F.git_init(work / "subject")
    for venue in _VENUES:
        d = root / venue
        d.mkdir(parents=True)
        (d / "occupied.py").write_text("VERSION = 1\n")
    (root / "tools" / "runtime_pin.py").write_text(_RESOLVED + stored)
    F.git_commit(root)
    return root


def can_pass(work: Path) -> Path:
    return _tree(work, "")


def can_fail(work: Path):
    return _tree(work, _stored()), "EDA_IMAGE_DIGEST"
