"""A fixture helper: declare planted L docs as written by the CURRENT phase-1
producer (FX_STALE_LDOCS).

Since FX_STALE_LDOCS the front door reuses generated L docs only when their
recorded producer identity matches the current one; docs with no identity are
regenerated (orchestrator ruling, item 4). A test whose SUBJECT is something
else -- the expert hand-off, the L-count skip -- and which plants L docs to
stand for "phase 1 already ran", must therefore also plant the identity phase
1 stamps. This does that through the real stamp (`_phase1_producer_identity.
stamp`) over a real recording made by `ProducerRecorder` (the modules loaded in
this process, as they are on disk), which re-derives to itself, so the stamp
reads as current. The planted docs are re-written byte for byte under the
recorder, because a stamp vouches only for docs its own run wrote. It changes nothing the calling test asserts.
"""
from __future__ import annotations

from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]


def stamp_current(project: Path) -> None:
    import _phase1_producer_identity as PID
    project = Path(project)
    before = PID.input_tree_snapshot(project)
    rec = PID.ProducerRecorder()
    with rec:
        # "phase 1 wrote these": the planted docs are re-written, byte for
        # byte, under the recorder, so the stamp covers them as THIS run's
        # outputs (a stamp only vouches for docs its run wrote).
        for doc in PID.generated_docs(project):
            doc.write_bytes(doc.read_bytes())
    PID.stamp(project, rec, input_before=before)
