#!/usr/bin/env python3
"""gds_canonical_digest — compare two stream-outs of one layout honestly.

WHY THIS EXISTS (vibe-ic#2221)
==============================
A GDSII stream carries its own write time. BGNLIB and BGNSTR each hold twelve
int16 fields — a modification date and a last-access date, to the second — and
every writer stamps them from the wall clock. So two stream-outs of ONE layout
are never byte-equal, and `sha256`/`md5` on a `.gds` answers "did a clock tick",
not "is this the same layout".

MEASURED, both directions, in the pinned image (`_eda_pin.IMAGE_DIGEST`), one
KLayout `Layout` written twice 2.2 s apart with no edit in between::

    size a/b: 318 318            BYTE-EQUAL: False
    differing byte offsets:      [21, 33, 77, 89, 185, 197]
    date-field spans:            [(10, 34), (66, 90), (174, 198)]
    differing bytes INSIDE a date field: 6 of 6

Six differing bytes, six of six inside a date field, zero anywhere else. That
is the whole of the difference, and it is why zeroing exactly those fields is
sound rather than convenient: nothing else moved, so nothing else is being
hidden.

Read off the published corpus for the same reason, `spm/v1.10.18_sky130A`:
BGNLIB `mod=(2026, 8, 9, 13, 10, 28)`, and every one of that file's BGNSTR
records carries the same twelve fields. BGNSTR is not an afterthought here —
one library record would be cheap to skip past, but a file has one BGNSTR per
structure and they all carry the stamp.

WHAT THIS IS NOT
================
Not a geometry comparison and not a substitute for one. Two layouts that differ
in geometry differ in this digest — that is asserted in both directions by
`test_gds_canonical_digest.py`, because a digest that cannot go DOWN as well as
up would be a rubber stamp. But two layouts with EQUAL digests are equal in
their record stream, which is a stronger statement than "the same shapes" and a
weaker one than "the same file". Use it to ask "did this layout change", never
to ask "is this file untouched" — for the second question the raw digest is the
right instrument and this module is the wrong one.

Deliberately NOT here: any design name, any PDK name, any layer number, any
threshold. This reports a digest; every rule lives in the caller.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path
from typing import Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _gds_geometry import (  # noqa: E402
    GdsError, _iter_records, _read_bytes, _RT,
)

#: The two records that carry dates, by NAME rather than by number, so a
#: renumbering in the shared table cannot silently stop this from zeroing them.
DATE_BEARING = ("BGNLIB", "BGNSTR")

#: Twelve int16: (mod y, m, d, h, m, s) then (access y, m, d, h, m, s).
DATE_FIELD_BYTES = 24

#: What a zeroed date field is. Not b"\x00" * 24 spelled inline at two sites.
ZEROED_DATES = b"\x00" * DATE_FIELD_BYTES


def canonical_records(data: bytes) -> Iterable[bytes]:
    """Re-emit every record, with the date payloads replaced by zeros.

    The stream is rebuilt rather than patched in place. A patch needs byte
    OFFSETS, and an offset computed by a second walk is a second definition of
    where a record starts; rebuilding uses the one walker
    (`_gds_geometry._iter_records`) that already refuses a truncated or
    ill-formed stream, so this cannot digest a file that module would reject.
    """
    first, saw_endlib, consumed = True, False, 0
    for rt, dt, payload in _iter_records(data):
        name = _RT.get(rt)
        if name == "ENDLIB":
            saw_endlib = True
        if first:
            if name != "HEADER":
                raise GdsError(
                    "first record is %s, not HEADER: this is not a GDSII "
                    "stream, and a digest of it would name nothing"
                    % (name or "type 0x%02X" % rt))
            first = False
        if name in DATE_BEARING:
            if len(payload) != DATE_FIELD_BYTES:
                raise GdsError(
                    "%s carries %d payload byte(s), not %d: this file does not "
                    "spell its dates the way the format declares, so zeroing "
                    "a fixed span would corrupt it rather than canonicalise it"
                    % (name, len(payload), DATE_FIELD_BYTES))
            payload = ZEROED_DATES
        record = struct.pack(">HBB", len(payload) + 4, rt, dt) + payload
        consumed += len(record)
        yield record
    if first:
        raise GdsError("empty stream: no record to digest")
    # COMPLETENESS IS PART OF THE ANSWER, and it is checked here rather than in
    # the shared walker. `_gds_geometry._iter_records` stops when fewer than
    # four bytes remain, so a stream cut mid-record ends the loop silently and
    # a digest of it would look like a digest of a whole file. MEASURED by this
    # module's own `test_a_truncated_stream_is_refused`, which did NOT raise
    # until this clause existed.
    if not saw_endlib:
        raise GdsError(
            "stream ends without ENDLIB: this file is truncated, and a digest "
            "of a truncated stream reads exactly like a digest of a whole one")
    tail = data[consumed:]
    if tail and set(tail) != {0}:
        raise GdsError(
            "%d byte(s) follow ENDLIB and are not zero padding: this file "
            "carries content the record walk never reached" % len(tail))


def canonical_bytes(path: Path) -> bytes:
    """The file's bytes with every BGNLIB/BGNSTR date field zeroed."""
    return b"".join(canonical_records(_read_bytes(Path(path))))


def canonical_digest(path: Path) -> str:
    """sha256 of :func:`canonical_bytes`. Raises rather than falling back.

    FAIL-CLOSED ON PURPOSE. An earlier draft returned the raw digest when the
    stream would not parse, which reads as an answer and is not one: the caller
    would compare two raw digests, see them differ, and report "the layouts
    differ" about two files neither of which was canonicalised.
    """
    return hashlib.sha256(canonical_bytes(path)).hexdigest()


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="sha256 of a GDSII stream with its write times zeroed")
    ap.add_argument("gds", nargs="+", type=Path)
    ap.add_argument("--compare", action="store_true",
                    help="exit 1 unless every named file has the same digest")
    args = ap.parse_args(argv)
    digests = []
    for path in args.gds:
        try:
            digest = canonical_digest(path)
        except (GdsError, OSError) as exc:
            print("NOT_MEASURED %s: %s" % (path, exc))
            return 2
        digests.append(digest)
        print("%s  %s" % (digest, path))
    if args.compare and len(set(digests)) != 1:
        print("DIFFER: %d distinct canonical digest(s) over %d file(s)"
              % (len(set(digests)), len(digests)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
