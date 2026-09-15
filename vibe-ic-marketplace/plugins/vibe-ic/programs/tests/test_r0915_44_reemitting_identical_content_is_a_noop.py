"""R-0915-44 — a netlist that has not changed must not get a new digest.

THE DEFECT, MEASURED ON A REAL RUN (lane icadc, 2026-09-15/16). Line 5 of the
shipped `delta_sigma.sp` read

    * _provenance: produced_at=2026-09-14T18:59:58Z

and `analog_a3_netlist_emit` had no skip-if-unchanged path — its only
`exists()` guards staging the model libraries. A4 records the WHOLE-FILE
sha256 as `netlist_sha256` (verified: the `ldo`'s recorded
`c6fe9b5468bdb153…` is exactly `sha256sum ldo.sp`) and
`analog_a4_corner_sweep_check` recomputes it. So re-running the flow on
unchanged inputs re-stamped the clock, moved the digest, and fired
`A4_SWEEP_STALE_VS_NETLIST` on a circuit nobody had touched — discarding a
completed 13-hour PVT sweep over a timestamp.

DROPPING THE CLOCK IS NOT ENOUGH, AND THAT IS THE INTERESTING PART. `run_ref`
is a NONCE by design (`_analog_producer_common.new_run_ref`): it is checked by
AGREEMENT between the artefact and the record beside it, never re-derived, so
it cannot simply be made deterministic. It lands in the same header. A second
emission of identical content therefore still rewrote the file with a new nonce
and a new whole-file digest.

So the fix is two-sided:
  * the digested artefact carries NO wall clock — `produced_at` lives in
    `netlist_provenance.json`, which is where a time belongs; and
  * re-emitting byte-identical CONTENT is a no-op: the file on disk is kept
    verbatim, nonce and all, and the sidecar is written against THAT nonce so
    the agreement check still holds.

`content_digest` already ignores every `* _provenance:` line, so "unchanged"
means the CIRCUIT is unchanged, not the stamps.

BOTH DIRECTIONS. A real netlist change must still rewrite and still move the
digest, or the staleness gate has been disabled rather than fixed.

No chip / SKU / foundry literal.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_a3_netlist_emit as A3          # noqa: E402
import _analog_producer_common as _pc        # noqa: E402


def _tmp() -> Path:
    # mkdtemp, not tmp_path: a pytest tmp path can carry a newline on the
    # pinned image and that breaks tools that take it as an argument.
    return Path(tempfile.mkdtemp(prefix="r091544_"))


def _deck(produced_at: str, run_ref: str, rvalue: str = "100k") -> str:
    return (
        "* block — analog block netlist\n"
        f"* _provenance: producer=analog_a3_netlist_emit schema=1\n"
        f"* _provenance: produced_at={produced_at}\n"
        f"* _provenance: run_ref={run_ref}\n"
        "* _provenance: ai_handoff=none\n"
        ".subckt blk vdd vss vin vout\n"
        f"r1 vin vout {rvalue}\n"
        ".ends blk\n"
    )


# ── the artefact must not carry a wall clock ───────────────────────────────
def test_the_netlist_header_no_longer_stamps_produced_at():
    """THE DEFECT ARM. The header line is emitted as an f-string
    `f"_provenance: produced_at={stamp}"`; that spelling is what wrote a wall
    clock into the digested artefact and it must be gone. Matching the EMITTING
    EXPRESSION, not the bare word, so the explanatory comment beside it — which
    necessarily names the field — cannot make this test pass or fail on prose.
    """
    src = Path(A3.__file__).read_text(encoding="utf-8")
    assert 'f"_provenance: produced_at=' not in src
    assert "_provenance: produced_at={stamp}" not in src


def test_the_sidecar_still_carries_produced_at():
    """The stamp is MOVED, not deleted — a run that cannot say when it ran is
    a different defect."""
    src = Path(A3.__file__).read_text(encoding="utf-8")
    assert '"produced_at": stamp,' in src


# ── re-emission of identical content is a no-op ────────────────────────────
def test_identical_content_with_different_stamps_is_reused():
    d = _tmp()
    try:
        f = d / "blk.sp"
        f.write_text(_deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa"), encoding="utf-8")
        before = hashlib.sha256(f.read_bytes()).hexdigest()
        again = _deck("2026-06-30T12:34:56Z", "bbbbbbbbbbbb")   # new clock, new nonce
        reused = A3._reuse_unchanged(f, again)
        assert reused is not None
        assert reused == f.read_text(encoding="utf-8")          # the OLD text
        assert hashlib.sha256(f.read_bytes()).hexdigest() == before
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_reused_text_keeps_the_nonce_the_sidecar_must_agree_with():
    """The whole reason to return the EXISTING text: `run_ref` is checked by
    agreement, so the sidecar has to be written against the nonce that is
    actually in the file."""
    d = _tmp()
    try:
        f = d / "blk.sp"
        f.write_text(_deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa"), encoding="utf-8")
        reused = A3._reuse_unchanged(f, _deck("2026-06-30T12:34:56Z", "bbbbbbbbbbbb"))
        assert A3._run_ref_in(reused) == "aaaaaaaaaaaa"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unchanged_content_keeps_the_whole_file_digest_A4_pinned():
    """The property the gate actually reads: `netlist_sha256` is the WHOLE-FILE
    sha256, and it must not move when the circuit has not."""
    d = _tmp()
    try:
        f = d / "blk.sp"
        f.write_text(_deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa"), encoding="utf-8")
        pinned = hashlib.sha256(f.read_bytes()).hexdigest()
        for stamp, nonce in (("2026-02-02T02:02:02Z", "cccccccccccc"),
                             ("2026-03-03T03:03:03Z", "dddddddddddd")):
            reused = A3._reuse_unchanged(f, _deck(stamp, nonce))
            assert reused is not None
            if reused is None:                                  # pragma: no cover
                f.write_text(_deck(stamp, nonce), encoding="utf-8")
        assert hashlib.sha256(f.read_bytes()).hexdigest() == pinned
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── the negative control: a REAL change must still move the digest ─────────
def test_a_real_netlist_change_is_not_reused_so_staleness_still_fires():
    """THE ARM THAT KEEPS THE GATE ALIVE. One device value moves; the emitter
    must write, and the whole-file digest must move with it."""
    d = _tmp()
    try:
        f = d / "blk.sp"
        f.write_text(_deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa", "100k"), encoding="utf-8")
        pinned = hashlib.sha256(f.read_bytes()).hexdigest()
        changed = _deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa", "101k")
        assert A3._reuse_unchanged(f, changed) is None
        f.write_text(changed, encoding="utf-8")
        assert hashlib.sha256(f.read_bytes()).hexdigest() != pinned
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_absent_file_is_not_a_reuse():
    d = _tmp()
    try:
        assert A3._reuse_unchanged(d / "never-written.sp", _deck("t", "r")) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reuse_compares_the_circuit_not_the_stamps():
    """`content_digest` strips `* _provenance:` lines — state it as a property
    rather than trusting the two callers to agree by accident."""
    a = _deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa")
    b = _deck("2027-12-31T23:59:59Z", "zzzzzzzzzzzz")
    assert a != b
    assert _pc.content_digest(a) == _pc.content_digest(b)
    assert _pc.content_digest(a) != _pc.content_digest(
        _deck("2026-01-01T00:00:00Z", "aaaaaaaaaaaa", "101k"))


def test_the_emitter_routes_both_artefacts_through_the_reuse_path():
    """The netlist AND its testbench: A4 digests both
    (`netlist_sha256`, `netlist_testbench_sha256`)."""
    src = Path(A3.__file__).read_text(encoding="utf-8")
    assert "reused = _reuse_unchanged(sp_path, sp_text)" in src
    assert "tb_reused = _reuse_unchanged(tb_path, tb_text)" in src
