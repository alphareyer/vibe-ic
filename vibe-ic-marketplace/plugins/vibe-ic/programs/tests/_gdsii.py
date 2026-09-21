#!/usr/bin/env python3
"""Minimal REAL GDSII stream bytes, for tests that need a tape-out artefact.

Why this exists
---------------
The tape-out checklist's GDS slot (`signoff_audit._check_tapeout`) credits only
the flow's DECLARED stream-out artefact (`phase3/stage4/gds/*.gds`) and only
when that file carries actual GDSII substance — a non-empty file whose first
record is a HEADER. Fixtures that used to write `"GDS"` or `"binary gds data"`
into an arbitrary `.gds` were asserting on a shape the gate no longer accepts,
and rightly so: that is precisely the "a file nothing verified" defect the slot
was tightened to refuse.

So a test that needs the GDS slot CREDITED must hand the gate a real stream.
This module emits the smallest structurally honest one: HEADER, BGNLIB,
LIBNAME, UNITS, one empty structure, ENDLIB. It is bytes, not text, and it is
chip-AGNOSTIC (no design, PDK or vendor name anywhere).

It is deliberately NOT a substitute for a laid-out GDS: `gds_substance_check`
demands geometry proportional to the placed-instance count, and this stream has
none. Tests of THAT gate build their own richer streams.
"""
from __future__ import annotations

import struct
from pathlib import Path

#: `phase3/stage4/gds/*.gds` — what the flow yaml declares as Step 37's
#: stream-out artefact, and the only location the tape-out GDS slot credits.
DECLARED_GDS_DIR = "phase3/stage4/gds"


def _record(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    """One GDSII record: big-endian u16 total length, u8 type, u8 data-type."""
    return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload


#: The structure name the stream carries when a caller does not choose one.
#: Kept as the historical literal so every existing caller is byte-for-byte
#: unchanged by the `cell` parameter below.
DEFAULT_CELL = "TOP"


def _strname(cell: str) -> bytes:
    """STRNAME payload: ASCII, NUL-padded to an even length, as GDSII requires."""
    raw = cell.encode("ascii")
    return raw + (b"\x00" if len(raw) % 2 else b"")


def minimal_gdsii_bytes(structures: int = 1, *,
                        cell: str = DEFAULT_CELL) -> bytes:
    """The smallest byte string that IS a GDSII stream (HEADER .. ENDLIB).

    `cell` NAMES THE STRUCTURE, and a caller needs it whenever the gate under
    test compares the stream's TOP CELL against something. Keyword-only with the
    historical default, so no existing caller changes.

    MEASURED (lane icslot6): `drc_report_check` binds a sign-off report to its
    layout by CONTENT — it parses the bound stream's top cells and refuses a
    binding whose tops do not equal the report's declared top. A fixture writing
    six bytes of non-GDS therefore yielded `gds_top_cells(...) == []` and the
    witness `recorded layout top cells [] differ from report top 'top'`, so the
    two POSITIVE cases of `test_signoff_drc_producer_is_a_signoff_deck` could
    not pass. The stub was adequate while only the filename mattered; it stopped
    being a layout the moment the gate started reading one.

    HONEST LIMIT, measured rather than assumed: this stream contains no SREF /
    AREF records, so NOTHING references anything and EVERY structure is a top.
    With `structures=3, cell="top"` the tops are `['top', 'top_1', 'top_2']`, not
    `['top']`. A caller whose gate compares against a SINGLE top must therefore
    use `structures=1`; the suffixes exist only to keep the names distinct, not
    to build a hierarchy.
    """
    out = _record(0x00, 0x02, struct.pack(">h", 600))               # HEADER
    out += _record(0x01, 0x02, struct.pack(">12h", *([0] * 12)))    # BGNLIB
    out += _record(0x02, 0x06, b"LIB\x00")                          # LIBNAME
    out += _record(0x03, 0x05, b"\x00" * 16)                        # UNITS
    for index in range(structures):
        name = cell if index == 0 else f"{cell}_{index}"
        out += _record(0x05, 0x02, struct.pack(">12h", *([0] * 12)))  # BGNSTR
        out += _record(0x06, 0x06, _strname(name))                    # STRNAME
        out += _record(0x07, 0x00)                                    # ENDSTR
    out += _record(0x04, 0x00)                                        # ENDLIB
    return out


def write_gdsii(path: Path, structures: int = 1, *,
                cell: str = DEFAULT_CELL) -> Path:
    """Write a minimal real GDSII stream at `path` (parents created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(minimal_gdsii_bytes(structures, cell=cell))
    return path


def write_declared_streamout(project: Path, name: str = "chip_top.gds") -> Path:
    """Write a creditable Step-37 stream-out into `project`.

    The one call a fixture needs when its subject is NOT the GDS slot and it
    simply requires that slot satisfied.
    """
    return write_gdsii(project / DECLARED_GDS_DIR / name)
