"""A fixture helper: declare planted L docs as written by the CURRENT phase-1
producer (FX_STALE_LDOCS).

Since FX_STALE_LDOCS the front door reuses generated L docs only when their
recorded producer identity matches the current one; docs with no identity are
regenerated (orchestrator ruling, item 4). A test whose SUBJECT is something
else -- the expert hand-off, the L-count skip -- and which plants L docs to
stand for "phase 1 already ran", must therefore also plant the identity phase
1 stamps. This does that through the real stamp (`_phase1_producer_identity.
stamp`) over a real recording made by `_step_recorder.Recorder`; the recorded
code is the identity module's own, which re-derives to itself, so the stamp
reads as current. It changes nothing the calling test asserts.
"""
from __future__ import annotations

from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]


def stamp_current(project: Path) -> None:
    import _phase1_producer_identity as PID
    import _step_recorder as SR
    rec = SR.Recorder(PROGRAMS)
    with rec:
        PID.inputs_digest(Path(project))
    PID.stamp(Path(project), rec)
