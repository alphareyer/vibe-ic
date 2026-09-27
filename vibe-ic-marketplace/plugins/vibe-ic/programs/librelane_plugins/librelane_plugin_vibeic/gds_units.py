"""The GDSII UNITS record, read from a stream's header (lane mig105).

Pure Python and free of LibreLane imports, so the host-side tests and
`Vibeic.DatabaseUnit` run the same function.
"""
from __future__ import annotations

import struct
from typing import Optional

__all__ = ["gds_database_unit_um", "real8"]


def real8(raw: bytes) -> float:
    """A GDSII 8-byte excess-64 base-16 real (not IEEE 754)."""
    sign = -1.0 if raw[0] & 0x80 else 1.0
    exponent = (raw[0] & 0x7F) - 64
    mantissa = int.from_bytes(raw[1:8], "big") / float(1 << 56)
    return sign * mantissa * (16.0 ** exponent)


def gds_database_unit_um(path: str) -> Optional[float]:
    """The stream's database unit in um, read from its UNITS record.

    GDSII records are ``<u16 length><u8 type><u8 datatype><payload>``; UNITS
    is record type 0x03 with two 8-byte GDSII reals (user units per database
    unit, metres per database unit).  Only the header is read.  None when the
    stream ends, or the first structure begins (BGNSTR, 0x05), before any
    UNITS record.
    """
    with open(path, "rb") as stream:
        while True:
            head = stream.read(4)
            if len(head) < 4:
                return None
            length, rtype = struct.unpack(">HB", head[:3])
            if length < 4:
                return None
            payload = stream.read(length - 4)
            if rtype == 0x05:
                return None
            if rtype == 0x03 and len(payload) == 16:
                return real8(payload[8:16]) * 1e6
