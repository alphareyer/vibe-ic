#!/usr/bin/env python3
"""A netlist left by an EARLIER build is not this build's output.

MEASURED (opentitan_aes x sky130A, 2026-09-15). `step_synth` asks four times
whether the build produced a netlist, and answered with `netlist.is_file()`.
Phase 3 synthesises into the directory phase 2 already wrote `<top>_synth.v`
to, so that predicate is true before the build even starts.

The consequence was not cosmetic. `synth_frontend_should_retry_under_synthesis`
takes `produced_output` as its anti-verdict-shopping guard — "a real result
must not be re-read under -DSYNTHESIS". Handed a STALE file it refused the
retry, and the retry it refused is the one the runner ships for exactly this
failure (its own comment: "a vendor primitive whose `ifdef SIMULATION DV-only
arm ($urandom / std::randomize / $value$plusargs-in-a-constant-context) ...
FAIL - even though the IDENTICAL closure elaborates under -DSYNTHESIS").
The run failed `synth rc=1 ... synth_frontend: "none"` with a 5,276,402-byte
netlist from four hours earlier sitting beside it.

The same predicate also gated the three SUCCESS checks, where a stale netlist
beside an rc=0 build that wrote nothing would CERTIFY a frontend that produced
nothing - the same defect with a false PASS instead of a false FAIL.

These tests are about the OBSERVABLE, not about any chip: they use a synthetic
`fixture_top_synth.v` and assert only on the stamp helper's answers.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as P3      # noqa: E402
import synth_frontend as SF              # noqa: E402

#: Resolved lazily and tolerantly ON PURPOSE. A module-level attribute
#: access would turn the pre-fix tree into a COLLECTION ERROR, which takes the
#: whole file down — including the structural test below, the one that has to
#: keep working against pre-fix source to be worth anything. Absent, every test
#: here fails on its own assertion with the reason named.
_STAMP = getattr(P3, "_synth_artifact_stamp", None)


def _stamp(path):
    assert _STAMP is not None, (
        "phase3_one_shot_runner has no per-build artefact stamp, so "
        "'did this build produce a netlist?' can only be answered by a "
        "presence test — which an earlier build's netlist satisfies")
    return _STAMP(path)


# ───────────────────────────── the stamp itself ──────────────────────────────

def test_absent_artifact_has_no_stamp(tmp_path):
    assert _stamp(tmp_path / "fixture_top_synth.v") is None


def test_an_unchanged_file_keeps_its_stamp(tmp_path):
    """The stale case: the file is there, and the build did not touch it."""
    nl = tmp_path / "fixture_top_synth.v"
    nl.write_text("module fixture_top(); endmodule\n")
    before = _stamp(nl)
    assert before is not None
    assert _stamp(nl) == before, (
        "an untouched artefact reported itself as a new result")


def test_a_rewritten_file_gets_a_new_stamp(tmp_path):
    """The real case: this build wrote something different."""
    nl = tmp_path / "fixture_top_synth.v"
    nl.write_text("module fixture_top(); endmodule\n")
    before = _stamp(nl)
    time.sleep(0.01)
    nl.write_text("module fixture_top(input a); endmodule\n")
    assert _stamp(nl) != before


def test_byte_identical_rewrite_is_not_a_new_result(tmp_path):
    """A rebuild that lands the SAME bytes is not new evidence.

    mtime alone would call this a fresh result; the digest is why it does not.
    """
    nl = tmp_path / "fixture_top_synth.v"
    text = "module fixture_top(); endmodule\n"
    nl.write_text(text)
    before = _stamp(nl)
    time.sleep(0.01)
    nl.write_text(text)          # same bytes, new mtime
    after = _stamp(nl)
    assert after is not None and before is not None
    assert after[2] == before[2], "the digest is not being compared"


def test_an_appearing_file_is_this_builds_output(tmp_path):
    """Absent before, present after — unambiguously produced."""
    nl = tmp_path / "fixture_top_synth.v"
    before = _stamp(nl)
    assert before is None
    nl.write_text("module fixture_top(); endmodule\n")
    assert _stamp(nl) != before


# ───────────── the consequence the defect actually had (the retry) ───────────
# A design-property blob that genuinely branches on the define set, so the
# retry's OTHER precondition is satisfied and `produced_output` is the only
# variable under test.

_ARMED_RTL = """
module fixture_prim ();
`ifdef SIMULATION
  initial begin
    if (!$value$plusargs("fixture_seed=%0d", seed)) begin end
  end
`elsif SYNTHESIS
  // synthesizable passthrough
`endif
endmodule
"""


def test_stale_artifact_would_suppress_the_retry(tmp_path):
    """NEGATIVE CONTROL for the defect: this is what went wrong.

    Not a test of new code — a statement of the mechanism, so a future change
    that re-introduces `is_file()` semantics fails here with the reason named.
    """
    suppressed, why = SF.synth_frontend_should_retry_under_synthesis(
        "$value$plusargs", rtl_text_blob=_ARMED_RTL, produced_output=True)
    assert suppressed is False
    assert "no retry" in why


def test_the_same_closure_retries_when_nothing_was_produced(tmp_path):
    """And this is what should happen once `produced_output` means produced."""
    allowed, why = SF.synth_frontend_should_retry_under_synthesis(
        "$value$plusargs", rtl_text_blob=_ARMED_RTL, produced_output=False)
    assert allowed is True
    assert "SYNTHESIS" in why


def test_step_synth_asks_the_stamp_not_the_filesystem():
    """The four call sites read this build's output, not any file's presence.

    Asserted on the SOURCE of `step_synth` rather than by driving a container:
    the defect was one predicate spelled four times, and the property that
    matters is that none of those spellings survives.
    """
    import inspect
    src = inspect.getsource(P3.step_synth)
    assert "_netlist_is_from_this_build()" in src, (
        "step_synth no longer consults the per-build stamp")
    assert "netlist.is_file()" not in src, (
        "a presence test came back into step_synth — a netlist from an "
        "earlier build will be mistaken for this build's output again")
