"""An incremental converter's coefficients are DERIVED, and the bound that
derives them is the CLOSED-LOOP excursion, not an open-loop ramp.

MEASURED (round 20): the entry carried the FREE-RUNNING second-order set
a1 = a2 = 1/2, cited to Boser & Wooley 1988, for a converter the same entry
calls incremental in five places and whose own L5 declares
"resets/accumulates per conversion window". With that set one DAC decision
moves the loop filter by half the reference and the first integrator saturates
in TWO clocks of a 256-clock window; the bitstream carried no code at any
input.

MEASURED AGAIN (lane icadc, 2026-09-16, R-0915-61), AND THE RULER MOVED. The
replacement — `a = (swing * L! / (vref * N**L)) ** (1/L)` — bounds the ramp an
integrator would make with NO FEEDBACK. In a closed single-bit loop the DAC
removes charge every cycle, so that ramp never happens. On an ideal-element
harness of the very topology this library emits (same nets, phases, DAC
polarity, 256-clock reset and clock; ideal switches and amplifiers), the
open-loop expression overestimates the real excursion by 12.5x at the emitted
coefficient and 5700x at the working one, and the coefficient it returns
produces a NON-CONVERTER:

    ORDER 2, density at vin 0.700 / 0.600 (ideal 0.6000 / 0.5000):
    ci/cs    a         slope     vo1 swing
      2      0.5000    1.0291    1.2446 V   <- OVERFLOWS a 1.2 V rail
      4      0.2500    1.0105    0.3577 V   <- the closed-loop rule
      8      0.1250    0.9381    0.2066 V
     16      0.0625    0.9472    0.1411 V
     32      0.0313    0.8652    0.1400 V
     64      0.0156    0.6922    0.1326 V
    128      0.0078    0.5647    0.0940 V
    183.5    0.0055    0.3846    0.0797 V   <- what the OLD expression returned

    ORDER 1, same harness with stage 2 removed:
      2      0.5000    1.0018    0.5103 V
      4      0.2500    1.0200    0.2130 V   <- the closed-loop rule
      8      0.1250    0.9836    0.1304 V
     16      0.0625    0.9836    0.0648 V
     64      0.0156    0.9745    0.0134 V
    256      0.0039    1.0109    0.0040 V   <- what the OLD expression returned

The mechanism: the open-loop expression scales `a` as 1/N, so the loop product
`a**L` scales as N**-L and the gain collapses as the L-th POWER of the window.
At order 1 there is nothing to collapse and every coefficient converts; at
order 2 it is squared and the transfer goes with it. That is why a rule that
looked defensible produced a non-converter the moment `order` was 2.

SO THESE TESTS CHANGED, AND WHY. Several of them pinned the open-loop closed
form as a property. A test that pins a measured-wrong contract is a wrong
ruler; correcting a ruler with the measurement attached is not weakening it.
Each such assertion is REPLACED by the measured property it should have pinned,
and none is deleted or xfailed. `test_THE_CONTROL_...` is INVERTED, with its
reason: the set must NOT move with OSR, because the FEEDBACK and not the window
bounds the excursion — a rule that scales `a` with 1/OSR is the defect, so a
control asserting it is the defect's own test.

The old expression is kept, computed and REPORTED as `a_openloop_ramp_bound`,
never as the coefficient.
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analog_a2_topology_emit as m  # noqa: E402

CONSTS = {"integrator_swing_fraction_of_vdd": 0.833}
#: The coefficient windows the ideal-element harness MEASURED as converting,
#: quoted from the sweep in this module's docstring. Order 2: slope >= 0.93 for
#: a in [0.0625, 0.25], overflow at 0.5. Order 1: slope 0.974-1.020 for a in
#: [0.0039, 0.5], so the window is wider and the same rule sits inside it.
ORDER2_CONVERTS_MIN, ORDER2_CONVERTS_MAX = 0.0625, 0.25
ORDER1_CONVERTS_MIN, ORDER1_CONVERTS_MAX = 0.0039, 0.50
DERIVE = m.COEFFICIENT_DERIVATIONS["incremental_cifb"]


def _c(order, osr, vref=1.0, vdd=1.2):
    return DERIVE(order, {"osr": osr, "vref": vref, "vdd": vdd}, CONSTS)


def test_THE_CONTROL_INVERTED_the_set_must_NOT_change_when_osr_changes():
    """THE CONTROL, INVERTED, AND THE INVERSION IS THE FINDING (R-0915-61).

    It used to assert that `a` moves with OSR. That is the defect: scaling `a`
    as 1/N makes the loop product `a**L` fall as N**-L, and at order 2 the
    measured transfer collapses to slope 0.3846 with ideal devices. What
    bounds the integrator's excursion is the FEEDBACK — one DAC step per
    decision — not the window length, so the coefficient must not depend on
    the window at all.

    The half of the old control that still holds is kept below: the set must
    not be the tabulated free-running 1/2 either.
    """
    seen = {osr: _c(2, osr)[0] for osr in (64, 128, 256, 512, 1024)}
    assert len(set(seen.values())) == 1, seen
    assert set(seen.values()) != {0.5}, "the tabulated free-running set is back"


def test_the_set_is_ONE_RULE_across_order_and_the_length_still_follows_order():
    """Replaces `test_the_set_changes_when_order_changes`.

    MEASURED (R-0915-61 clause 2): at order 1 EVERY coefficient from 0.5 down
    to 0.0039 converts — slope 0.974 to 1.020 across a 128x range — so the
    closed-loop rule is correct there too and a per-order rule would buy
    nothing while keeping a defective expression alive for the one case where
    its defect happens not to bite. One rule; the per-stage COUNT still
    follows the order.
    """
    assert _c(1, 256)[0] == _c(2, 256)[0]
    assert len(_c(1, 256)) == 1 and len(_c(2, 256)) == 2


def test_first_order_lands_in_the_window_the_harness_MEASURED_converting():
    """Replaces `test_first_order_lands_on_the_textbook_result`.

    The old assertion pinned `a = usable_swing / (N * vref)` = 0.003905. That
    value is not WRONG at order 1 — the harness measures slope 1.0109 there —
    but it is not REQUIRED either: order 1 converts at every coefficient from
    0.5 to 0.0039. What the rule must do is land inside the measured
    converting window, which the closed-loop bound does at 0.25 (slope 1.0200,
    0.213 V of swing). The old expression stays available and reported.
    """
    a = _c(1, 256)[0]
    assert ORDER1_CONVERTS_MIN <= a <= ORDER1_CONVERTS_MAX, a
    assert a == pytest.approx(1.2 * 0.833 / (4 * 1.0), rel=1e-6)


def test_the_set_satisfies_the_CLOSED_LOOP_bound_and_the_old_one_is_reported():
    """Replaces `test_the_second_order_set_satisfies_its_own_bound`.

    The old assertion required EQUALITY with the open-loop ramp bound
    `prod(a) * vref * N**L / L! == usable_swing`. Measured, that bound
    overestimates the real excursion by 12.5x-5700x, so equality with it is
    equality with the wrong quantity. The bound that binds is the closed-loop
    excursion, `a <= swing / (4 * vref)`; the open-loop number is still
    computed and REPORTED so the two can be compared.
    """
    for order, osr in ((1, 64), (2, 64), (2, 256), (2, 512)):
        m._COEFFICIENT_BOUNDS_SEEN.clear()
        a = _c(order, osr)
        assert math.prod(a) > 0
        for x in a:
            assert x <= 1.2 * 0.833 / (4 * 1.0) + 1e-12, (order, osr, x)
        rec = m._COEFFICIENT_BOUNDS_SEEN[-1]
        openloop = (1.2 * 0.833 * math.factorial(order)
                    / (1.0 * osr ** order)) ** (1.0 / order)
        assert rec["a_openloop_ramp_bound"] == pytest.approx(openloop, rel=1e-9)
        assert rec["a_returned"] == pytest.approx(a[0], rel=1e-12)


def test_the_derived_set_is_below_the_free_running_one_and_above_the_collapse():
    """Replaces `test_the_derived_set_is_far_below_the_free_running_one`.

    BOTH edges are measured, so both are pinned. The tabulated free-running
    1/2 swings 1.2446 V on a 1.2 V rail and OVERFLOWS; the old expression's
    0.0055 gives slope 0.3846 and does not convert. The rule must sit strictly
    between, and it does: 0.25, slope 1.0105 at 30 % of the rail.
    """
    a = _c(2, 256)[0]
    assert a < 0.5, "the tabulated free-running set overflows the rail"
    assert a > 0.0055 * 10, "back in the range the harness measures collapsing"
    assert ORDER2_CONVERTS_MIN <= a <= ORDER2_CONVERTS_MAX, a


def test_coefficients_are_per_stage_and_equal():
    a = _c(2, 256)
    assert len(a) == 2 and a[0] == a[1]


@pytest.mark.parametrize("sv,missing", [
    ({"osr": 0, "vref": 1.0, "vdd": 1.2}, "osr"),
    ({"osr": 256, "vref": 0, "vdd": 1.2}, "vref"),
    ({"osr": 256, "vref": 1.0, "vdd": 0}, "vdd"),
    ({}, "everything"),
])
def test_an_underivable_set_is_refused_by_name_never_defaulted(sv, missing):
    with pytest.raises(m.LibraryEntryError) as e:
        DERIVE(2, sv, CONSTS)
    assert "ABSENT, never defaulted" in str(e.value)


def test_a_zero_or_negative_order_is_refused():
    with pytest.raises(m.LibraryEntryError):
        DERIVE(0, {"osr": 256, "vref": 1.0, "vdd": 1.2}, CONSTS)


def test_the_entry_derives_rather_than_tabulating():
    ds = None
    for name in dir(m):
        v = getattr(m, name)
        if isinstance(v, dict) and "delta_sigma" in v:
            cand = v["delta_sigma"]
            if isinstance(cand, dict) and "circuit_class_citation" in cand:
                ds = cand
                break
    assert ds is not None, "delta_sigma library entry not found"
    assert ds.get(m.COEFFICIENT_DERIVATION_KEY) == "incremental_cifb"
    assert m.COEFFICIENT_SETS_KEY not in ds, (
        "the tabulated free-running set is back")


def test_the_citation_names_the_regime_it_is_used_in():
    ds = None
    for name in dir(m):
        v = getattr(m, name)
        if isinstance(v, dict) and "delta_sigma" in v:
            cand = v["delta_sigma"]
            if isinstance(cand, dict) and "circuit_class_citation" in cand:
                ds = cand
                break
    cite = ds["circuit_class_citation"]
    assert "INCREMENTAL" in cite.upper()
    assert "different regime" in cite


# ── the bias the coefficient implies, and the liveness the window needs ────

def _entry():
    for name in dir(m):
        v = getattr(m, name)
        if isinstance(v, dict) and "delta_sigma" in v:
            cand = v["delta_sigma"]
            if isinstance(cand, dict) and "circuit_class_citation" in cand:
                return cand
    raise AssertionError("delta_sigma entry not found")


def _env(order=2, osr=256, vref=1.0, vdd=1.2, enob=14, fclk=1.0):
    e = {"order": order, "osr": osr, "vref": vref, "vdd": vdd,
         "enob": enob, "fclk": fclk}
    e.update(_entry()["constants"])
    e.update({"kt_j_300k": 4.141947e-21, "cap_area_ff_per_um2": 1.5,
              "rsheet_ohm_per_sq": 260.0, "vth_n_extracted_v": 0.5})
    return e


def _derived(name, **kw):
    for spec in _entry()["requires_derived"]:
        if spec["name"] == name:
            return eval(spec["expr"], {"__builtins__": {}}, _env(**kw)), spec
    raise AssertionError(name)


def test_the_bias_resistor_is_derived_not_a_nominal():
    assert "r_ib_l_um" not in _entry()["constants"], (
        "the bias length is back as a hand number")
    exprs = {e["device"]: e for e in _entry()["device_param_exprs"]}
    assert "r_ib" in exprs and exprs["r_ib"]["param"] == "l"


def test_the_derived_bias_meets_the_slew_by_construction():
    # slew_margin was 0.167 with the nominal bias; deriving the bias FROM the
    # slew makes it the stated design margin exactly
    val, spec = _derived("slew_margin")
    assert val == pytest.approx(_entry()["constants"]["slew_design_margin"])
    assert val >= spec["min"]


def test_the_bias_length_moves_with_every_bound_row_it_depends_on():
    # THE CONTROL. A bias that does not follow the declaration is the defect.
    base, _ = _derived("bias_resistor_l_um")
    for kw in ({"vref": 0.8}, {"fclk": 2.0}, {"enob": 12}, {"vdd": 1.1}):
        other, _ = _derived("bias_resistor_l_um", **kw)
        assert other != pytest.approx(base), kw


def test_the_bias_is_INDEPENDENT_of_osr_and_that_is_physics():
    # MEASURED while writing the control above, and it is not a missing
    # dependency: the sampling capacitor's kT/C budget scales as 1/osr while
    # the load ratio (1 + miller)/coefficient scales as osr, so the OTA's
    # LOAD — and therefore the current that slews it — is invariant. A longer
    # window needs a smaller coefficient AND a smaller sampling capacitor,
    # and the two cancel exactly. Pinned so a future edit that breaks the
    # cancellation is visible rather than silent.
    base, _ = _derived("bias_resistor_l_um")
    for osr in (64, 128, 512, 1024):
        other, _ = _derived("bias_resistor_l_um", osr=osr)
        assert other == pytest.approx(base, rel=1e-9), osr
    # The half that used to move is now deliberately still (R-0915-61): the
    # coefficient no longer scales with the window, because the feedback and
    # not the window bounds the excursion. MEASURED here as a consequence
    # worth keeping visible -- the OTA load is invariant across OSR ANYWAY,
    # because `_LOAD_OVER_CS_DERIVED_EXPR` writes both halves as explicit OSR
    # expressions rather than reading the returned coefficient. So the
    # cancellation this test names survives the rule change, and the loop
    # below is the assertion that matters.
    assert _c(2, 64)[0] == _c(2, 512)[0]


def test_the_bias_length_is_admitted_for_this_declaration():
    val, spec = _derived("bias_resistor_l_um")
    assert spec["min"] <= val <= spec["max"]
    assert val == pytest.approx(15.0889, rel=1e-3)


def test_liveness_nodes_are_declared_and_drawn():
    live = _entry()[m.LIVENESS_NODES_KEY]
    assert set(live) == {"reset", "feedback", "decision"}
    assert m.library_invariants() == []


def test_a_liveness_net_the_entry_never_draws_is_faulted():
    import copy
    lib = copy.deepcopy({k: v for k, v in m.LIBRARY.items()
                         if v.get(m.STAGE_KEY)})
    for e in lib.values():
        e[m.LIVENESS_NODES_KEY] = {"reset": "not_a_net"}
    assert any("never draws" in p for p in m.library_invariants(lib))


def test_the_derivation_states_what_it_does_NOT_claim():
    """A bound that prevents overflow is not a converter, and the docstring
    must say so. v1.16.10's landing message credited this derivation with a
    density measured on a DIFFERENT coefficient; the narrowed claim is the
    thing that stops that being inherited."""
    doc = DERIVE.__doc__
    assert "DOES NOT SET THE GAIN" in doc
    assert "NECESSARY" in doc and "SUFFICIENT" in doc
    # the measured pair that makes the point must stay with it
    assert "0.1288" in doc and "0.0325" in doc
