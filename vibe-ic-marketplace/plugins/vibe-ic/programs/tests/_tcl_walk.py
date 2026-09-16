"""Run a Tcl walker the way the PROGRAM runs Tcl — host first, container second.

WHY THIS EXISTS. Both SI test files needed `tclsh`, and both reached for
`pytest.mark.skipif(shutil.which("tclsh") is None, ...)`. That is the pattern
the repo already used, and it is still wrong for the same reason an env-gated
control is wrong: a tool-absent skip turns a test that COULD NOT RUN into a
green line on the host that is doing the measuring. Nothing downstream can tell
that apart from a test that ran and passed.

The resolution is the one the flow itself uses. `phase3_one_shot_runner` does
not skip when a tool is missing from the host; it dispatches into the pinned
EDA container, which is where the tool it needs actually lives. So:

  1. host `tclsh` if `shutil.which` finds one;
  2. otherwise `docker exec` into the container the runner itself names —
     through the runner's OWN `_docker_exec`, and the container name from the
     runner's own resolver, because a second copy of either is a second thing
     to keep in step;
  3. and if neither route answers, the caller FAILS with NOT_MEASURED naming
     both routes — never a skip, and never a pass.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))


class TclNotMeasured(AssertionError):
    """Neither route to `tclsh` answered — the walk was NOT MEASURED."""


def container_name() -> str:
    """The container the RUNNER would use, resolved the runner's way."""
    try:
        import _eda_pin as _pin                                # noqa: PLC0415
        return os.environ.get("VIBEIC_EDA_CONTAINER") \
            or os.environ.get("EDA_CONTAINER") \
            or _pin.default_container_name()
    except Exception:                                          # noqa: BLE001
        return (os.environ.get("VIBEIC_EDA_CONTAINER")
                or os.environ.get("EDA_CONTAINER") or "")


def walk(walker_text: str, deck_text: str, tmp_path: Path):
    """Run `walker_text` over `deck_text` and return (stdout, stderr, route).

    Raises `TclNotMeasured` — an AssertionError, so pytest reports a FAILURE
    and not a skip — when neither route can run tclsh."""
    walker = tmp_path / "walk.tcl"
    deck = tmp_path / "deck.tcl"
    walker.write_text(walker_text)
    deck.write_text(deck_text)

    host = shutil.which("tclsh")
    if host:
        r = subprocess.run([host, str(walker), str(deck)],
                           capture_output=True, text=True, timeout=300)
        return r.stdout, r.stderr, f"host:{host}"

    container = container_name()
    tried = ["host PATH (shutil.which('tclsh') -> None)"]
    if container:
        try:
            import design_one_shot_runner as _dosr             # noqa: PLC0415
            # THE SCRIPTS TRAVEL IN THE COMMAND, NOT ON A MOUNT. A test writes
            # under `tmp_path`, which the EDA container has no reason to mount,
            # so handing it a host path would fail for a reason that has
            # nothing to do with what is being measured. Quoted heredocs: the
            # container writes both files into its own /tmp and reads them
            # there, and nothing about the host filesystem has to be true.
            cmd = (
                "set -e; d=$(mktemp -d); "
                "cat > \"$d/walk.tcl\" <<'__VIBEIC_W__'\n"
                + walker_text.rstrip("\n") + "\n__VIBEIC_W__\n"
                "cat > \"$d/deck.tcl\" <<'__VIBEIC_D__'\n"
                + deck_text.rstrip("\n") + "\n__VIBEIC_D__\n"
                "tclsh \"$d/walk.tcl\" \"$d/deck.tcl\""
            )
            rc, out, err = _dosr._docker_exec(container, cmd, timeout=300)
            if "tclsh: not found" in (err or "") or rc == 127:
                tried.append(f"container {container!r} (no tclsh, rc={rc})")
            else:
                # The label says WHERE IT WAS DISPATCHED, not where it
                # landed: `_docker_exec` is the runner's own seam and it
                # decides between the container and this filesystem exactly as
                # the flow would. Claiming "container" for a call the seam ran
                # locally would be a small lie in the one field a reader uses
                # to know what was measured.
                return out, err, f"runner-exec:{container}"
        except Exception as exc:                               # noqa: BLE001
            tried.append(f"container {container!r} ({type(exc).__name__}: {exc})")
    else:
        tried.append("container (the runner resolved no container name)")
    raise TclNotMeasured(
        "NOT_MEASURED: this walk needs `tclsh` and neither route answered — "
        + "; ".join(tried)
        + ". The deck's syntax was therefore NOT checked, which is a missing "
          "measurement and not a passing one.")
