"""A GDS digest that answers "same LAYOUT", not "same write time"
(vibe-ic#2221).

THE MEASUREMENT THIS EXISTS FOR. Every GDS BGNLIB record carries 12 int16
fields -- modification date then last-access date -- stamped from the writing
clock, and BGNSTR carries the same per structure. Read off the published
corpus (benchmark-data 146d6656), first BGNLIB of six files:

    ic/spm/v1.10.18_sky130A/.../spm.gds          mod=(2026,8,9,13,10,28)  acc=same
    ic/spm/v1.5.58_ihp-sg13g2/.../spm.gds        mod=(2026,7,23,5,18,52)  acc=same
    ic/spm/v1.9.96_gf180mcuD/.../chip_top.gds    mod=(2026,8,6,21,16,38)  acc=same
    ic/u_hawaii_adc/.../delta_sigma.gds          mod=(126,8,4,2,40,23)  acc=(126,8,4,2,51,19)
    ic/u_hawaii_adc/.../ldo.gds                  mod=(126,8,4,2,39,29)  acc=(126,8,4,2,51,20)
    ic/u_hawaii_adc/.../stage4/gds/ldo.gds       mod=(126,8,4,2,39,29)  acc=(126,8,4,2,51,20)

Wall clock, to the second; the Magic-written three carry mod and access ~11
minutes apart, i.e. two distinct clock reads inside one file. So two
stream-outs of ONE layout can never be byte-equal, and `sha256` cannot answer
"did these two runs produce the same layout" -- which is the question every
physical A/B in this repo asks. A lane comparing two arms by `md5` reported
"the stream-out is not reproducible run to run"; it would report that on any
design, forever.

WHAT IS NOT CLAIMED, because #2221 measured it: no SHIPPED gate is wrong. The
only GDS hash EQUALITY in the tree is `_pad_ring_route_cache_valid`, which
compares a recorded hash to the current hash of the SAME file. Every other GDS
hash is single-artefact attestation. This is an instrument for A/B work, not a
repair of a broken gate, and it changes no verdict.

BOTH DIRECTIONS ARE PINNED BELOW, and the second is the one that matters: a
digest that ignored MORE than the clock would be useless, so the same-geometry
arm is paired with arms that mutate geometry, a structure NAME, and record
order, each of which must still MOVE the digest.
"""
import struct
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gds_substance_check as G  # noqa: E402


def _rec(rtype: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HH", len(payload) + 4, rtype) + payload


def _dates(*vals) -> bytes:
    return struct.pack(">12h", *vals)


def _gds(mod=(2026, 1, 2, 3, 4, 5), acc=(2026, 1, 2, 3, 4, 5),
         strname=b"TOP\x00", layer=7, xy=(0, 0, 10, 0, 10, 10, 0, 10, 0, 0),
         extra_struct=False) -> bytes:
    """A minimal but WELL-FORMED GDS stream, ENDLIB and all."""
    body = (
        _rec(G.RT_HEADER, struct.pack(">h", 600))
        + _rec(G.RT_BGNLIB, _dates(*mod, *acc))
        + _rec(G.RT_LIBNAME, b"LIB\x00")
        + _rec(G.RT_UNITS, b"\x00" * 16)
        + _rec(G.RT_BGNSTR, _dates(*mod, *acc))
        + _rec(G.RT_STRNAME, strname)
        + _rec(0x0800)                                   # BOUNDARY
        + _rec(0x0D02, struct.pack(">h", layer))         # LAYER
        + _rec(0x1003, struct.pack(">%di" % len(xy), *xy))  # XY
        + _rec(0x1100)                                   # ENDEL
        + _rec(0x0700)                                   # ENDSTR
    )
    if extra_struct:
        body += (_rec(G.RT_BGNSTR, _dates(*mod, *acc))
                 + _rec(G.RT_STRNAME, b"SECOND\x00") + _rec(0x0700))
    return body + _rec(G.RT_ENDLIB)


# ---------------------------------------------------------------------------
# The direction the digest exists for
# ---------------------------------------------------------------------------
def test_two_writes_of_one_layout_agree_although_their_bytes_do_not():
    """THE POINT. Same geometry, two different clocks."""
    import hashlib
    a = _gds(mod=(2026, 8, 6, 21, 16, 38), acc=(2026, 8, 6, 21, 16, 38))
    b = _gds(mod=(2026, 9, 10, 1, 2, 3), acc=(2026, 9, 10, 1, 9, 59))
    assert a != b, "the fixture must differ in bytes or it proves nothing"
    assert hashlib.sha256(a).hexdigest() != hashlib.sha256(b).hexdigest()
    assert G.canonical_digest(a) == G.canonical_digest(b)
    assert G.canonical_digest(a) is not None


# ---------------------------------------------------------------------------
# The directions a too-forgiving digest would break, one per property
# ---------------------------------------------------------------------------
def test_a_different_polygon_still_moves_the_digest():
    base = _gds()
    moved = _gds(xy=(0, 0, 11, 0, 11, 10, 0, 10, 0, 0))
    assert G.canonical_digest(base) != G.canonical_digest(moved)


def test_a_different_layer_still_moves_the_digest():
    assert G.canonical_digest(_gds(layer=7)) != G.canonical_digest(_gds(layer=8))


def test_a_different_structure_NAME_still_moves_the_digest():
    """STRNAME is not a date record, and a renamed cell is a different layout
    to every consumer downstream of the stream-out.

    THE TWO NAMES ARE THE SAME, EVEN LENGTH ON PURPOSE, and both halves of
    that were paid for. SAME length: With `TOP` against `OTHER`
    this test passed for the wrong reason: the record LENGTHS differ, so the
    digest moved even when the NAME payload was ignored entirely -- MEASURED,
    a mutation blanking every STRNAME payload left all 8 tests green. Equal
    lengths make the content the only variable, and that mutation then reddens
    exactly here. EVEN length: a GDS record length must be even, so a 5-byte
    name payload makes the whole stream malformed, `iter_records` stops at it
    and the digest is `None` for BOTH arms -- which fails this assertion for a
    reason that has nothing to do with names. Measured that too, on the way.
    """
    assert (G.canonical_digest(_gds(strname=b"AAA\x00"))
            != G.canonical_digest(_gds(strname=b"BBB\x00")))


def test_an_extra_structure_still_moves_the_digest():
    assert G.canonical_digest(_gds()) != G.canonical_digest(_gds(extra_struct=True))


# ---------------------------------------------------------------------------
# A digest is not a validity verdict, and an unmeasurable file is not a zero
# ---------------------------------------------------------------------------
def test_a_stream_with_no_ENDLIB_is_refused_rather_than_digested():
    """A truncated file must not get a confident answer. `iter_records` stops
    silently at the first impossible record, so without this the digest of a
    half-written GDS would look exactly like the digest of a whole one."""
    whole = _gds()
    assert G.canonical_digest(whole) is not None
    truncated = whole[:len(whole) - 4]          # drop ENDLIB
    assert G.canonical_digest(truncated) is None


def test_a_file_that_is_not_a_gds_at_all_is_refused():
    assert G.canonical_digest(b"this is not a GDS stream") is None
    assert G.canonical_digest(b"") is None


# ---------------------------------------------------------------------------
# The real corpus, so the fixture above is not the only thing measured
# ---------------------------------------------------------------------------
_BENCHMARK_DATA = os.environ.get("VIBEIC_BENCHMARK_DATA")
_CORPUS = (Path(_BENCHMARK_DATA) / "ic/spm/v1.9.96_gf180mcuD"
           / "phase3/stage4/gds/chip_top.gds"
           if _BENCHMARK_DATA else Path("/__vibeic_benchmark_data_unset__"))


@pytest.mark.skipif(not _CORPUS.is_file(),
                    reason="published corpus not checked out on this host")
def test_a_real_corpus_gds_digests_and_its_clock_is_what_moves():
    """Re-stamping ONLY the dates of a real
    published GDS must not move the digest, while flipping one geometry byte must."""
    data = _CORPUS.read_bytes()
    base = G.canonical_digest(data)
    assert base is not None, "the corpus file must be a complete GDS"

    # Re-stamp every date record in place; nothing else is touched.
    out, pos, stamped = bytearray(), 0, 0
    while pos + 4 <= len(data):
        rec_len, rec_type = struct.unpack_from(">HH", data, pos)
        if rec_len < 4 or rec_len % 2 or pos + rec_len > len(data):
            break
        chunk = bytearray(data[pos:pos + rec_len])
        if rec_type in G._DATE_RECORDS and rec_len >= 4 + G._DATE_BYTES:
            chunk[4:4 + G._DATE_BYTES] = _dates(1999, 12, 31, 23, 59, 58,
                                                1999, 12, 31, 23, 59, 59)
            stamped += 1
        out += chunk
        pos += rec_len
    out += data[pos:]
    assert stamped >= 1, "no date record was re-stamped, so nothing was tested"
    assert bytes(out) != data
    assert G.canonical_digest(bytes(out)) == base
