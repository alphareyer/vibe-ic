"""vibe-ic#2220 — a retiming dWNS decides nothing until it is READABLE.

WHAT WAS WRONG, AND WHERE THE PROGRAM SAID IT ITSELF
====================================================
`iterative_recurrence_timing_diagnosis.py` carried

    # 0.10 ns is well inside routing/characterisation noise for any sign-off
    # corner and is not PDK-specific.
    RETIMING_EPS_NS = 0.10

and then decided `retiming_effective` / `retiming_ineffective` from it. The
comment is the refutation: a quantity documented as lying INSIDE the noise
cannot be the thing that separates two verdicts drawn from that noise.

MEASURED by the filer (sky130A x sha256, 25.9 ns SDC, ss_100C_1v60 + extracted
max SPEF, propagated clock, derate 0.95/1.05, one sign-off script per row): with
the netlist held BYTE-IDENTICAL and only `set_thread_count` changed 32 -> 10,
global WNS moved **1.569724 ns** — 15.7x the constant.

REPRODUCED at the program level before anything was changed (lane cy2183,
8HD-4, 2026-09-10, main 9c653d47f, pinned image content id da2314d4100c...,
one self-loop STA fixture, only `--retiming-wns-delta` varied):

    +0.09  -> LOOP_BOUND_RECURRENCE   "... (< 0.1 ns -> ineffective
                                       (loop-bound corroborated))"
    +0.11  -> RETIMING_EFFECTIVE_APPLY "... (>= 0.1 ns -> effective)"
    +1.5697-> RETIMING_EFFECTIVE_APPLY  <-- the filer's BYTE-IDENTICAL noise
                                            number, answered "apply retiming"
    -0.50  -> LOOP_BOUND_RECURRENCE   "... (>= 0.1 ns -> effective)"

Two root causes, both inside the same four lines:

  RC1  an unqualified `--retiming-wns-delta` is compared against a constant that
       is only defensible at one measurement granularity, and nothing states or
       enforces which. The architectural verdict, and the remedy behind it,
       turned on 0.02 ns of a quantity whose own noise is 1.57 ns.
  RC2  the evidence SENTENCE is rendered from `retiming_ineffective` (a
       two-sided test) while the VERDICT is driven by `retiming_effective` (a
       one-sided one), so the band `delta <= -EPS` printed
       "(>= 0.1 ns -> effective)" on a run that took neither branch. Both halves
       of that sentence are false. This one was not filed; it is fixed here
       because it lives in the lines RC1 rewrites.

THE REPAIR IS NOT A BIGGER CONSTANT
===================================
The right floor is flow- and granularity-dependent, which is the whole point, so
the constant was WITHDRAWN rather than raised. A delta must now arrive with the
thing that makes it readable — a MEASURED control-vs-control floor from the same
flow, or the byte-identical-netlist fact, which needs no threshold at all. A
delta with neither is NOT_MEASURED and decides nothing.

NEVER GREENER: no exit code moves (LOOP_BOUND / EFFECTIVE / INCONCLUSIVE are all
0; the only 1 is CLOCK_RELAX_FORBIDDEN and it is untouched). Nothing that used
to fail now passes. What changes is that a claim the program could not support
is no longer made, and `retiming_ineffective` is None where it used to be a
defaulted verdict.

chip-AGNOSTIC: generic register names and slack numbers; no vendor, SKU, PDK or
design literal drives any assertion.
"""
import json
import sys
from pathlib import Path

import pytest

PROG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG_DIR))

import iterative_recurrence_timing_diagnosis as mod  # noqa: E402

#: the filer's measured full-flow spread, with the netlist held byte-identical
NOISE_NS = 1.569724

SELF_LOOP_RPT = """\
Startpoint: a_reg[5] (rising edge-triggered flip-flop clocked by clk)
Endpoint: a_reg[27] (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

  Delay    Time   Description
  0.00     0.00   clock clk (rise edge)
  ...
  slack (VIOLATED)                  -3.85
"""

ANON_RPT = """\
Startpoint: _1234_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _5678_ (rising edge-triggered flip-flop clocked by clk)
Path Type: max
  slack (VIOLATED)                  -2.40
"""


def _run(tmp_path, *extra, rpt=SELF_LOOP_RPT):
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "s.rpt"
    src.write_text(rpt)
    out = tmp_path / "o.json"
    rc = mod.main(["--sta-report", str(src), "--json", str(out), *extra])
    return rc, (json.loads(out.read_text()) if out.is_file() else None)


# ── RC1: a bare delta decides nothing ──────────────────────────────────────

def test_a_bare_delta_is_NOT_MEASURED_and_establishes_nothing(tmp_path):
    """The headline. A number with no declared provenance is not a reading."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "0.09",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["signals"]["retiming_ineffective"] is None, (
        "an unqualified dWNS was allowed to establish ineffectiveness")
    assert r["signals"]["retiming_datapoint_state"] == "NOT_MEASURED_NO_NOISE_FLOOR"


def test_the_verdict_does_not_turn_on_two_hundredths_of_a_nanosecond(tmp_path):
    """0.09 and 0.11 straddled the withdrawn constant and gave OPPOSITE
    remedies — one sending the author to RTL surgery, the other to retiming."""
    _, lo = _run(tmp_path / "lo", "--retiming-wns-delta", "0.09",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    _, hi = _run(tmp_path / "hi", "--retiming-wns-delta", "0.11",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert lo["verdict"] == hi["verdict"], (lo["verdict"], hi["verdict"])
    assert lo["remedy"] == hi["remedy"], (lo["remedy"], hi["remedy"])
    assert (lo["signals"]["retiming_datapoint_state"]
            == hi["signals"]["retiming_datapoint_state"])


def test_the_measured_noise_number_does_not_route_to_apply_retiming(tmp_path):
    """The filer's 1.569724 ns was produced with the netlist BYTE-IDENTICAL, so
    it is the measurement's own spread. Unqualified, it must not become an
    'improvement' that overrides the self-loop evidence."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", str(NOISE_NS),
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["verdict"] != "RETIMING_EFFECTIVE_APPLY", (
        f"pure measurement noise was read as a retiming improvement: {r}")
    assert r["signals"]["retiming_ineffective"] is None


# ── RC2: the sentence and the verdict must agree ───────────────────────────

def test_the_evidence_never_claims_effective_on_a_run_that_was_not(tmp_path):
    """`delta <= -EPS` fell into neither verdict branch and printed the other
    branch's sentence: "-0.500 ns (>= 0.1 ns -> effective)" under a
    LOOP_BOUND_RECURRENCE verdict. Both halves were false."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "-0.50",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["verdict"] != "RETIMING_EFFECTIVE_APPLY"
    for line in r["evidence"]:
        if "retiming" not in line:
            continue
        assert not line.rstrip(") ").endswith("-> effective"), (
            f"the verdict is {r['verdict']} and the evidence says effective: "
            f"{line}")


# ── the readable cases, once the caller says what the number is ────────────

def test_below_a_measured_floor_is_NOT_MEASURED_never_ineffective(tmp_path):
    """The standing rule: a gate that could not tell says NOT_MEASURED. It does
    not say zero, and it does not say the design is loop-bound."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "0.09",
                 "--retiming-wns-noise-floor-ns", str(NOISE_NS),
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["signals"]["retiming_datapoint_state"] == "NOT_MEASURED_BELOW_NOISE_FLOOR"
    assert r["signals"]["retiming_ineffective"] is None


def test_an_above_noise_regression_is_its_own_state(tmp_path):
    """A pass that made WNS WORSE by more than the noise is a measured result.
    It is not 'effective' and it is not loop-bound corroboration."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "-2.0",
                 "--retiming-wns-noise-floor-ns", "1.60",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["signals"]["retiming_datapoint_state"] == "REGRESSED"
    assert r["signals"]["retiming_ineffective"] is False
    assert r["verdict"] != "RETIMING_EFFECTIVE_APPLY"


def test_byte_identical_needs_no_threshold_at_all(tmp_path):
    """The loop-bound signature the docstring already named. Nothing changed,
    so nothing improved — there is no reading to be inside anything."""
    rc, r = _run(tmp_path, "--retiming-netlist-byte-identical",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["signals"]["retiming_datapoint_state"] == "INEFFECTIVE_BYTE_IDENTICAL"
    assert r["signals"]["retiming_ineffective"] is True


def test_an_anonymised_path_with_an_unreadable_delta_is_INCONCLUSIVE(tmp_path):
    """The gate used to ask whether a NUMBER arrived. A number the program has
    just called NOT_MEASURED is not a datapoint, and letting it suppress this
    branch is how a noise reading stood in for the bank identity nobody could
    see."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "0.05", rpt=ANON_RPT)
    assert rc == 0
    assert r["verdict"] == "INCONCLUSIVE_NAMES_ANONYMIZED", r


def test_a_zero_noise_floor_is_refused(tmp_path):
    """A floor of zero puts every reading above it and readmits the defect."""
    src = tmp_path / "s.rpt"
    src.write_text(SELF_LOOP_RPT)
    assert mod.main(["--sta-report", str(src),
                     "--retiming-wns-delta", "0.09",
                     "--retiming-wns-noise-floor-ns", "0"]) == 2


# ── CONTROLS: what must NOT have moved ─────────────────────────────────────

def test_CONTROL_a_readable_improvement_still_overrides_the_name(tmp_path):
    """Without this, 'never let a delta override' would satisfy every test
    above and destroy the capability they are protecting."""
    rc, r = _run(tmp_path, "--retiming-wns-delta", "2.5",
                 "--retiming-wns-noise-floor-ns", "0.35",
                 "--spec-microarch-free", "--spec-latency-unconstrained")
    assert rc == 0
    assert r["verdict"] == "RETIMING_EFFECTIVE_APPLY"
    assert r["signals"]["retiming_datapoint_state"] == "EFFECTIVE"
    assert r["signals"]["retiming_ineffective"] is False


def test_CONTROL_no_datapoint_at_all_is_unchanged(tmp_path):
    """Green on BOTH trees by construction: the no-delta path never touched the
    constant, so a failure here would mean the repair reached further than the
    defect did."""
    rc, r = _run(tmp_path, "--spec-microarch-free",
                 "--spec-latency-unconstrained")
    assert rc == 0
    assert r["verdict"] == "LOOP_BOUND_RECURRENCE"
    assert r["remedy"] == "RECOMMEND_MULTICYCLE_SPLIT"
    assert r["signals"]["retiming_ineffective"] is None


def test_CONTROL_the_clock_relax_tripwire_still_fails(tmp_path):
    """The only non-zero exit in the program. If the repair had moved an exit
    code, this is where it would show."""
    src = tmp_path / "s.rpt"
    src.write_text(SELF_LOOP_RPT)
    assert mod.main(["--sta-report", str(src), "--relax-clock-proposed"]) == 1
