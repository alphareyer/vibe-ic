"""vibe-ic#2116(2) — the global-route-only marker is read from `openroad.log`.

`def_stage_progression_check` demotes a `no-routing-geometry` ERROR to a
WARNING when it finds `DETAILED_ROUTE_NONFATAL:` under the PnR directory. The
comment block that introduces the marker has always said the marker is read
from "any openroad.log under `phase3/stage3/pnr/`"; `_is_global_route_only`
swept `pnr_dir.rglob("*.log")` — every `.log` in that tree, whoever wrote it.

That gap is reachable, not theoretical. `step_pnr` retries the route from a
bounded ladder and archives each SUPERSEDED approach's transcript next to the
canonical one (#2108). An approach that was thrown away because it could not
route emits exactly this marker; under a `*.log` sweep it then vouches for the
approach that actually shipped. #2108 could only dodge that from its own side,
by naming the archives `openroad.approach<N>.log.txt`, which leaves any OTHER
writer of a `.log` under the PnR directory able to launder the same downgrade.
(That is the name #2108 gave them. `step_pnr` writes
`openroad.inv<K>.approach<N>.log.txt` today: vibe-ic#2133 added the invocation
key so a second `step_pnr` into one out_dir cannot overwrite the first one's
archives. The narrowing this module pins is what makes the sweep independent of
either spelling.)

Both directions, and the two properties the narrowing must NOT cost:

  1. a foreign `.log` carrying the marker no longer demotes      (reproducer)
  2. the canonical `openroad.log` still demotes                (positive control)
  3. a canonical log in a NESTED run directory still demotes  (`rglob` kept)
  4. the explicit `routing_mode.json` marker is untouched
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

dsp = importlib.import_module("def_stage_progression_check")

_MARKER = "DETAILED_ROUTE_NONFATAL: no via-cut rules\n"
_PLAIN = "[INFO DRT-0199] Number of violations = 0.\n"


def _pnr(project: Path) -> Path:
    d = project / "phase3" / "stage3" / "pnr"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 1. THE REPRODUCER — a foreign `.log` must not demote
# ---------------------------------------------------------------------------
def test_a_foreign_log_carrying_the_marker_does_not_demote(tmp_path):
    """The shipped run routed; a file that is not this run's log says it did
    not. Before #2116 the sweep believed the second one."""
    pnr = _pnr(tmp_path)
    (pnr / "openroad.log").write_text(_PLAIN)
    (pnr / "openroad.approach0.log").write_text(_MARKER)
    assert dsp._is_global_route_only(tmp_path) is False


def test_an_arbitrary_log_name_carrying_the_marker_does_not_demote(tmp_path):
    """Not only the approach archives: ANY `.log` under the PnR directory
    reached the sweep, so the population was 'whatever anyone wrote there'."""
    pnr = _pnr(tmp_path)
    (pnr / "openroad.log").write_text(_PLAIN)
    (pnr / "some_other_tool.log").write_text(_MARKER)
    assert dsp._is_global_route_only(tmp_path) is False


def test_a_foreign_log_does_not_demote_even_with_no_canonical_log(tmp_path):
    """The absence of `openroad.log` is not permission to read a different
    file in its place — that would restore the defect whenever the canonical
    log is missing, which is precisely the damaged-run case."""
    pnr = _pnr(tmp_path)
    (pnr / "openroad.approach0.log").write_text(_MARKER)
    assert dsp._is_global_route_only(tmp_path) is False


# ---------------------------------------------------------------------------
# 2. POSITIVE CONTROLS — the narrowing must cost nothing it was not asked for
# ---------------------------------------------------------------------------
def test_the_canonical_log_still_demotes(tmp_path):
    pnr = _pnr(tmp_path)
    (pnr / "openroad.log").write_text(_PLAIN + _MARKER)
    assert dsp._is_global_route_only(tmp_path) is True


def test_a_canonical_log_in_a_nested_run_directory_still_demotes(tmp_path):
    """`rglob` is retained on purpose: the file name is narrowed, the depth of
    the search is not."""
    pnr = _pnr(tmp_path)
    nested = pnr / "run2"
    nested.mkdir()
    (nested / "openroad.log").write_text(_MARKER)
    assert dsp._is_global_route_only(tmp_path) is True


def test_the_explicit_routing_mode_marker_is_untouched(tmp_path):
    fh = tmp_path / "phase3" / "stage4" / "foundry_handoff"
    fh.mkdir(parents=True)
    (fh / "routing_mode.json").write_text(json.dumps({"mode": "global_only"}))
    assert dsp._is_global_route_only(tmp_path) is True


def test_a_pnr_directory_with_no_marker_anywhere_does_not_demote(tmp_path):
    """The pair to every case above: a classifier that answered False always
    would satisfy the reproducers, and one that answered True always would
    satisfy the controls. Only both together say anything."""
    pnr = _pnr(tmp_path)
    (pnr / "openroad.log").write_text(_PLAIN)
    assert dsp._is_global_route_only(tmp_path) is False


# ---------------------------------------------------------------------------
# 3. THE NAME IS DECLARED, NOT SPELLED INLINE
# ---------------------------------------------------------------------------
def test_the_swept_file_name_is_a_named_constant(tmp_path):
    """A future reader changing the runner's log name has ONE place to look,
    and this test names the coupling so the change cannot be silent."""
    assert dsp._GLOBAL_ROUTE_LOG_NAME == "openroad.log"
