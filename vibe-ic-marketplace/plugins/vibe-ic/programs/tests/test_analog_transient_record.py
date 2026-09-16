"""`analog_transient_record` — the record length is derived from EVERY declared
constraint, and the derivation REFUSES rather than return the shorter one.

vibe-ic#2200. The defect these tests are about is not "512 is too small". It is
that 512 was derived from ONE input — the conversion window the deck's own
metric needs — while the thing the block is GRADED on had no way to reach the
derivation at all. So the subject here is the shape: several constraints on one
quantity, the longest wins, and every case where they do not close is a named
refusal rather than a quiet fall-back to whichever rows happened to resolve.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analog_resolution_stimulus as st           # noqa: E402
import analog_transient_record as r               # noqa: E402


def _entry(**over):
    e = {"record_constraints": [
        {"name": "conversion_windows",
         "clocks_expr": "window_clocks * 2",
         "why": "the deck measures the second window"},
        {"name": "coherent_in_band_tone",
         "applies_when_spec_declares_any": ["enob", "sndr"],
         "needs_spec_bound": ["osr"],
         "clocks_rule": "coherent_in_band_tone",
         "why": "the graded spec set is an input to the record too"},
    ]}
    e.update(over)
    return e


_CONSUMER = "record_clocks * 1000 / fclk"


def _tone_deck(*, stop_ns):
    """A corner deck of the shape A4 writes, with ONE transient of the given
    length. Synthetic block, port and net names; no chip / PDK / vendor
    literal."""
    return "\n".join([
        "* synthetic corner deck",
        ".subckt conv vdd vss ain vrefp vrefn clk dout",
        "r1 ain dout 1k",
        "c1 dout vss 1p",
        ".ends conv",
        "v_vdd vdd 0 1.8",
        "v_vrefp vrefp 0 1.4",
        "v_vrefn vrefn 0 0.4",
        "v_clk clk 0 pulse(0 1.8 0n 1n 1n 499n 1000n)",
        "v_ain ain 0 0.9",
        "xdut vdd 0 ain vrefp vrefn clk dout conv",
        ".control",
        f"tran 5n {stop_ns}n",
        "meas tran vavg avg v(dout)",
        ".endc",
        ".end",
    ]) + "\n"


_SPEC = {"block": "conv0",
         "specs": [{"name": "enob", "min": 14.0}, {"name": "osr",
                                                   "target": 256.0}]}
_TOPOLOGY = {"ports": ["vdd", "vss", "ain", "vrefp", "vrefn", "clk", "dout"],
             "stage_expansion": {"chain": ["ain", "n1"]}}


# ── the derivation ────────────────────────────────────────────────────────
def test_the_longest_constraint_binds_and_the_artefact_names_which_one():
    """Two lower bounds on one quantity: the record that satisfies both is the
    larger, and WHICH row asked for it is reported, because a reader who is
    told only the number has to re-derive the reason."""
    out = r.derive(_entry(), {"window_clocks": 256.0},
                   {"enob": 14.0, "osr": 256.0}, _CONSUMER)
    assert out["record_clocks"] == 13824.0
    assert out["binding_constraint"] == "coherent_in_band_tone"
    got = {c["constraint"]: c.get("clocks") for c in out["constraints"]}
    assert got == {"conversion_windows": 512.0,
                   "coherent_in_band_tone": 13824.0}


def test_the_graded_spec_set_is_what_makes_the_record_longer():
    """THE DEFECT, stated as a difference. The same entry, the same window, the
    same clock — and the only thing that changes is whether the declaration
    grades the block on a resolution. Before #2200 that made no difference at
    all, which is exactly what the issue reports."""
    env, entry = {"window_clocks": 256.0}, _entry()
    graded = r.derive(entry, env, {"enob": 14.0, "osr": 256.0}, _CONSUMER)
    ungraded = r.derive(entry, env, {"osr": 256.0}, _CONSUMER)
    assert ungraded["record_clocks"] == 512.0
    assert ungraded["binding_constraint"] == "conversion_windows"
    assert graded["record_clocks"] == 27 * ungraded["record_clocks"]


def test_a_row_that_does_not_apply_is_reported_as_such_not_omitted():
    """An absent row and a row that evaluated to something short are different
    statements, and the artefact has to keep them apart."""
    out = r.derive(_entry(), {"window_clocks": 256.0}, {"osr": 256.0},
                   _CONSUMER)
    tone = [c for c in out["constraints"]
            if c["constraint"] == "coherent_in_band_tone"][0]
    assert tone["applies"] is False
    assert "clocks" not in tone
    assert "enob" in tone["not_applicable_because"]


# ── the arithmetic is imported, never restated ────────────────────────────
@pytest.mark.parametrize("osr", [1.0, 16.0, 64.0, 100.0, 256.0, 512.0])
def test_the_tone_row_IS_the_producer_s_own_floor_and_not_a_second_copy(osr):
    """The one thing that would reintroduce #2200 one file further along: a
    record sized by this module's restatement of an arithmetic the producer
    enforces from its own. So the rule must return the producer's own number
    for every ratio, not a number that happens to agree at one of them.

    R-0915-74: that floor now takes a SECOND input — an entry that declares
    it is graded in the DECODED domain is sized in conversion windows. This
    entry declares no such thing, so the number must be the raw-record one,
    unchanged; the decoded case is pinned in the test below it."""
    out = r.derive(_entry(), {"window_clocks": 4.0},
                   {"enob": 14.0, "osr": osr}, _CONSUMER)
    tone = [c for c in out["constraints"]
            if c["constraint"] == "coherent_in_band_tone"][0]
    assert tone["clocks"] == float(st.coherent_record_samples(osr)["samples"])


def test_the_derived_record_is_the_shortest_the_producer_ACCEPTS():
    """Both edges of the same number, against the real producer: the derived
    record is accepted, and one band-bin below it is refused. A record that is
    lengthened and still refused is what a paper closed form gives (#2188
    published 12288 and had to correct it), so the floor is checked against
    the refusal rather than against the algebra."""
    osr = 256.0
    n = int(r.derive(_entry(), {"window_clocks": 256.0},
                     {"enob": 14.0, "osr": osr}, _CONSUMER)["record_clocks"])
    assert st.coherent_record_samples(osr)["samples"] == n
    deck = _tone_deck(stop_ns=n * 1000)
    assert st.plan(deck, _SPEC, _TOPOLOGY)["applied"] is True
    # ONE BAND BIN SHORTER, which is the smallest step that can change the
    # producer's answer: the tone bin it would emit falls below its own cycle
    # floor and it refuses again, by that name. The guard did not stop
    # refusing; it stopped being the only thing that had a say.
    short = st.plan(_tone_deck(stop_ns=(n - 2 * int(osr)) * 1000),
                    _SPEC, _TOPOLOGY)
    assert short["applied"] is False
    assert short["reason"] == "record_too_short_for_an_in_band_tone"
    assert short["samples_required"] == n


# ── every way the constraints fail to close is a REFUSAL ──────────────────
def test_an_applicable_row_whose_spec_input_is_unbound_is_REFUSED():
    """The unsatisfiable case, and the one the whole design turns on: a block
    graded on a resolution whose signal band nobody declared. There is no
    record length that puts a tone inside a band that does not exist, so
    sizing to the rows that DID resolve would ship a record that is short for
    a reason nothing records."""
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(_entry(), {"window_clocks": 256.0}, {"enob": 14.0}, _CONSUMER)
    ref = exc.value.refusals
    assert [x["requirement"] for x in ref] == ["record_input_unbound"]
    assert ref[0]["field"] == "osr"
    assert ref[0]["constraint"] == "coherent_in_band_tone"


def test_the_same_check_is_available_at_ADMISSION_time():
    """Split out so an emitter can refuse before anything reaches disk, next to
    its other requirement refusals — and silent when the record CAN be
    derived, so it never adds a refusal of its own to a healthy block."""
    assert r.unbound_inputs(_entry(), {"enob": 14.0, "osr": 256.0}) == []
    assert r.unbound_inputs(_entry(), {"osr": 256.0}) == []
    only = r.unbound_inputs(_entry(), {"enob": 14.0})
    assert len(only) == 1 and only[0]["field"] == "osr"


def test_an_entry_that_declares_NO_constraint_is_refused_not_defaulted():
    """A record sized by one hard-coded expression is the defect. An entry that
    reaches this module with nothing declared has not opted out of the
    derivation, it has failed to declare it."""
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive({"record_constraints": []}, {}, {}, _CONSUMER)
    assert exc.value.refusals[0]["requirement"] == "record_constraints_absent"


def test_a_row_that_cannot_be_EVALUATED_names_what_was_missing():
    """Three states, and the third is not an error dressed as a verdict: a row
    that was never evaluated is neither longer nor shorter than another one."""
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(_entry(), {}, {"enob": 14.0, "osr": 256.0}, _CONSUMER)
    ref = [x for x in exc.value.refusals
           if x["requirement"] == "record_constraint_unresolvable"]
    assert ref and ref[0]["missing"] == "window_clocks"


@pytest.mark.parametrize("expr", ["window_clocks * 0", "window_clocks - 256",
                                  "0 - window_clocks"])
def test_a_row_that_is_not_a_LENGTH_is_refused(expr):
    """Zero clocks and a negative number of clocks are not short records, they
    are not records; taking them into a max() would let a real row lose to
    arithmetic nobody meant."""
    entry = {"record_constraints": [{"name": "w", "clocks_expr": expr,
                                     "why": "w"}]}
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(entry, {"window_clocks": 256.0}, {}, _CONSUMER)
    assert (exc.value.refusals[0]["requirement"]
            == "record_constraint_not_a_length")


def test_a_row_that_RAISES_is_refused_and_carries_the_exception_by_name():
    """The other half of the same rule: a row that blew up is not a row that
    came back short."""
    entry = {"record_constraints": [{"name": "w", "clocks_expr":
                                     "window_clocks / 0", "why": "w"}]}
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(entry, {"window_clocks": 256.0}, {}, _CONSUMER)
    ref = exc.value.refusals[0]
    assert ref["requirement"] == "record_constraint_unresolvable"
    assert "ZeroDivisionError" in ref["missing"]


def test_an_unknown_rule_is_a_refusal_and_never_a_skipped_row():
    """A row nobody can evaluate must not drop out of the max() and let a
    shorter row win — that is the failure this module exists to remove."""
    entry = {"record_constraints": [
        {"name": "short", "clocks_expr": "window_clocks * 2", "why": "w"},
        {"name": "long", "clocks_rule": "not_a_rule_anyone_wrote", "why": "w"}]}
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(entry, {"window_clocks": 256.0}, {}, _CONSUMER)
    assert (exc.value.refusals[0]["requirement"]
            == "record_constraint_unresolvable")


def test_a_derivation_the_TRANSIENT_does_not_spend_is_refused():
    """vibe-ic#2200 EXACTLY, one file earlier: a record derived into a JSON
    field while the deck goes on running the length it always ran. The
    connection is checked, not assumed."""
    with pytest.raises(r.RecordNotDerivable) as exc:
        r.derive(_entry(), {"window_clocks": 256.0},
                 {"enob": 14.0, "osr": 256.0}, "window_clocks * 2000 / fclk")
    ref = exc.value.refusals[0]
    assert ref["requirement"] == "record_constant_not_consumed"
    assert r.RECORD_CONSTANT in ref["detail"]


def test_the_constraint_expression_grammar_is_data_and_not_code():
    """A constraint row is read off a library, so it must not be executable."""
    entry = {"record_constraints": [
        {"name": "x", "clocks_expr": "__import__('os').getpid()", "why": "w"}]}
    with pytest.raises(r.RecordNotDerivable):
        r.derive(entry, {"window_clocks": 256.0}, {}, _CONSUMER)


# ── the exported floor itself ─────────────────────────────────────────────
def test_the_floor_refuses_an_undeclared_band_rather_than_assume_one():
    """A silently-assumed OSR of 1 grades an oversampled modulator over the
    noise it shaped out of band on purpose, and that is the flattering answer."""
    for bad in (0.0, -3.0, float("nan"), float("inf"), None, True, "256"):
        with pytest.raises(ValueError):
            st.coherent_record_samples(bad)


def test_the_floor_rounds_the_tone_bin_UP_to_odd():
    """12288 is the closed form and 13824 is the record that WORKS; a reader
    who lengthens a deck to the first is refused again (#2188)."""
    out = st.coherent_record_samples(256.0)
    assert out["min_tone_bin"] % 2 == 1
    assert out["min_tone_bin"] >= out["min_signal_cycles"]
    assert out["samples"] == 13824
    assert out["samples"] > 2 * 256 * out["harmonics_in_band"] * out[
        "min_signal_cycles"]
    assert math.isclose(out["samples"], 2 * 256 * 3 * 9)
