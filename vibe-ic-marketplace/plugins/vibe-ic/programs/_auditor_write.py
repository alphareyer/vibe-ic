#!/usr/bin/env python3
"""THE AUDITOR'S ONE WRITER. R-0915-168.

Every file `flow_compliance_check` creates inside the project it is auditing goes through here:
the canonical completion audit, its compliance receipt, the authorship notes and their locks, the
per-step output records, and the superseded copies the republish keeps aside.

WHY ONE WRITER, AND WHY NOW
===========================
Three separate reviews found the same defect in three places, and each was patched on its own:

    R-0915-151  the canonical audit's temp   `<audit>.json.<pid>.tmp`
    R-0915-152  the note's lock and temp     `audit_created/<hex>.json.lock`, `....<pid>.<tid>.tmp`
    R-0915-165  the per-step record's temp   `step_outputs/<sid>.json.<pid>.tmp`

The defect is always the same: a temp or a lock is the AUDITOR'S file, it carries no content a
reader can identify it by, and `design_input_digest` therefore hashed the leftover as a DESIGN
INPUT -- so an interrupted pass moved the design hash permanently on an unchanged design. Four
shapes for one idea, and a fifth mechanism would have added a fifth shape and waited for a fourth
review to find it.

So the shape is DECLARED ONCE, in `_path_layout` (`auditor_temp_name`, `auditor_lock_name`,
`is_auditor_inprogress_name`), this module is the only thing that mints it, and the digest
recognises it by importing that declaration. A new auditor mechanism is covered the day it is
written rather than the day someone notices.

WHAT IT GUARANTEES, AND WHAT IT DELIBERATELY DOES NOT
=====================================================
It guarantees that the final name never refers to a partial document. It does NOT make a write
succeed, and it never changes what is written: this module is not the place to alter a payload --
the same boundary `_atomic_artefact.write_text` draws for itself.

AND IT DOES NOT SERIALISE TWO WRITERS UNLESS ASKED. An earlier cut of this paragraph said it did.
That was wrong twice over: `publish` is handed a finished document, so a reader of the final name
sees one complete document with or without a lock, and every lock taken leaves a file in the run
tree for the life of the tree -- 94 of them per full pass, four of them where a freshness judge
dated the design round by one. `lock=True` is the opt-in for the one READ-MODIFY-WRITE the auditor
has, and `holding_lock` is the door for a sequence wider than a single write.

THE CLEANUP NEVER MASKS THE ORIGINAL ERROR. The temp is removed in a `finally`, its own failure is
swallowed, and nothing is raised from the cleanup -- so the caller sees the write's error, not the
tidy-up's. `finally` and not `except OSError` because `SystemExit` and `KeyboardInterrupt` are two
of the ways a pass actually dies, and both leave a leftover the narrower form misses.

chip-AGNOSTIC: nothing here reasons about any IC, vendor, SKU or process.
"""
from __future__ import annotations

import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _path_layout as _pl  # noqa: E402

__all__ = ["publish", "copy_aside", "holding_lock", "temp_name", "lock_name"]


def temp_name(final: Path) -> Path:
    """The temp this module would publish `final` through. Declared in `_path_layout`."""
    return _pl.auditor_temp_name(Path(final))


def lock_name(final: Path) -> Path:
    """The lock this module would hold while publishing `final`."""
    return _pl.auditor_lock_name(Path(final))


@contextmanager
def holding_lock(final: Union[str, Path]) -> Iterator[None]:
    """Hold the per-document lock across a read-modify-write, or proceed without it.

    ACQUIRED BEFORE A SINGLE `yield`, and exactly one yield. An earlier cut of this shape in
    `flow_compliance_check` had a second `yield` inside an `except OSError:` arm, so a body that
    raised -- an `unlink` of an already-removed note, which is the ORDINARY drop under
    concurrency -- threw into the generator, hit that arm and yielded again: "generator didn't
    stop after throw()". A lock we cannot take must never stop the audit, so failure to lock is
    handled here, before the body runs.
    """
    dest = Path(final)
    fh = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_name(dest), "a+")                    # noqa: SIM115
        try:
            import fcntl                                    # noqa: PLC0415
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:                                   # pragma: no cover
            pass
    except OSError:                                         # pragma: no cover
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                import fcntl                                # noqa: PLC0415
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:                               # pragma: no cover
                pass
            try:
                fh.close()
            except OSError:                                 # pragma: no cover
                pass


def publish(final: Union[str, Path], payload: str, *,
            encoding: str = "utf-8", lock: bool = False) -> Path:
    """Write `payload` so that `final` never refers to a partial document.

    temp -> (lock) -> write -> `os.replace` -> `finally` remove the temp. Returns `final`.

    The temp is a SIBLING so the rename cannot cross a filesystem, where `os.replace` degrades to
    a copy and the window reopens.

    `lock` DEFAULTS TO FALSE, and the default is the whole point of the parameter.
    ===============================================================================
    A lock is for a READ-MODIFY-WRITE. This function composes nothing: it is handed a finished
    document and hands it over in one `os.replace`, so two publishers write differently-named
    temps and a reader of `final` sees one complete document either way. The lock would only
    decide WHICH complete document wins, and neither order is more correct when both come from the
    same pass.

    What it DOES buy, always, is a file that stays in the run tree for the life of the tree:
    `flock` lives on the inode, so releasing a lock cannot unlink it without a race (between one
    pass's release and its `unlink`, a second pass can already hold the same inode; the unlink
    then lets a third create a NEW inode and take a lock that serialises against nobody).
    Measured on two consecutive `--lenient` passes over a copy of a completed `ic/subservient` run
    tree with `lock=True` everywhere: 94 lock files, 69 of them one-per-step-output-record, four
    of them beside stage receipts OUTSIDE `reports/audit/` where `result_md_audit_provenance_check`
    read them as the newest DESIGN artefact and failed a tree nobody had touched. The same two
    passes with this default: 24 -- 20 authorship-note locks and the four the one read-modify-write
    still takes. The 91 gate verdicts, the step tally and the blocker classification are IDENTICAL
    between the two arms, in both passes, so the 70 that went bought nothing that was measured.

    So the whole-document callers pass nothing and leave no lock; the ONE read-modify-write the
    auditor has (`flow_compliance_check._stamp_publication`) passes `lock=True` and says why.
    `holding_lock` remains the door for a caller that must serialise a sequence WIDER than one
    write -- the authorship note's, which is why the note itself publishes with `lock=False`.
    """
    dest = Path(final)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(dest)

    def _write() -> None:
        try:
            tmp.write_text(payload, encoding=encoding)
            os.replace(tmp, dest)
        finally:
            _discard(tmp)

    if lock:
        with holding_lock(dest):
            _write()
    else:
        _write()
    return dest


def copy_aside(src: Union[str, Path], final: Union[str, Path]) -> Path:
    """Copy `src` to `final` so that `final` never refers to a partial copy.

    Used where the auditor keeps a document aside rather than composing one -- the republish's
    `*.superseded-<n>.json`. `copy2` straight onto the final name leaves a short file under it if
    the copy dies, which is the same lie one layer along.
    """
    dest = Path(final)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_name(dest)
    try:
        shutil.copy2(str(src), str(tmp))
        os.replace(tmp, dest)
    finally:
        _discard(tmp)
    return dest


def _discard(tmp: Path) -> None:
    """Remove a temp without ever masking the failure that left it.

    `missing_ok` rather than an `exists()` probe: on the happy path `os.replace` has already
    consumed the temp, and a peer sweeping the same prefix between the two is legitimate.
    """
    try:
        tmp.unlink(missing_ok=True)
    except OSError:                                         # pragma: no cover
        pass


if __name__ == "__main__":  # pragma: no cover - a smoke entry, not a gate
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
