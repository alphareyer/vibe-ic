"""A HARDMACRO delivery still had its die pinned by a pad ring nothing builds.

`_chip_path_requests_pad_ring` is the canonical condition for step 15.5ic and
a6a45babe taught it that a HARDMACRO delivery never requests a ring. Every
other consumer obeys it -- `_install_route_deck`, the PnR cache validity, and
the final pad-ring evidence step. The producer dispatch inside `step_pnr` did
NOT, and `_padring_required_die_um` reads the record that producer writes, so a
delivery that builds no ring still had its floorplan sized to one.

MEASURED on subservient x gf180mcuD (plugin 1.21.6, main ed3965cc6). The design
declares `deliverable: HARDMACRO` in
`input/submission_template/tapeout_declaration.json`; the sibling `slots/`
directory holds the operator's CATALOGUE of four slot sizes (0p5x0p5, 0p5x1,
1x0p5, 1x1), which is not a choice; `_chip_path_requests_pad_ring` returned
False. The producer ran anyway and wrote `die_required_um.die_side_um = 1962`:

  * `die-um=auto -> 221x221` was DISCARDED for `1962x1962` um,
  * `core inset := 393 um` -- the ring's own measured depth,
  * measured core utilization **3.979 %** against L9's declared 30-40 %,
  * `pad_side_constraint` FAILED on i_clk/i_rst "measured against the run's
    declared die rectangle ... 1962x1962 um".

And no pad was ever placed: the routed DEF is `DESIGN subservient` with ZERO
`gf180mcu_fd_io__` instances. 78x the die area, and 393 um of margin on every
side, reserved for a ring nothing in the flow builds.

Fix: `_padring_producer_dispatch()` consults the same predicate before
dispatching the producer, and SKIPs with the delivery named otherwise.

BOTH DIRECTIONS ARE ASSERTED HERE. A delivery that genuinely gets a ring -- a
self-tape-out, or a slot taken by a DIE delivery -- must STILL dispatch the
producer, or the fix would simply have deleted the pad ring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import _owner_declared as _OD                              # noqa: E402
import phase3_one_shot_runner as R  # noqa: E402


_SLOT_YAML = "name: 1x1\ndie_w_um: 1000\ndie_h_um: 1000\n"


def _project(tmp_path: Path, *, deliverable=None, slots=False,
             self_tapeout=False) -> Path:
    p = tmp_path / "proj"
    st = p / "input" / "submission_template"
    st.mkdir(parents=True, exist_ok=True)
    if slots:
        (st / "slots").mkdir(exist_ok=True)
        for name in ("0p5x0p5", "0p5x1", "1x0p5", "1x1"):
            (st / "slots" / f"{name}.yaml").write_text(_SLOT_YAML,
                                                       encoding="utf-8")
    if self_tapeout:
        (st / "SELF_TAPEOUT.txt").write_text("self tape-out\n",
                                             encoding="utf-8")
    if deliverable is not None:
        (st / "tapeout_declaration.json").write_text(
            json.dumps(_OD.attest(
                {"schema": "vibe-ic/tapeout_declaration/1",
                 "answers": {"deliverable": deliverable}})),
            encoding="utf-8")
    return p


@pytest.fixture
def spy(monkeypatch):
    """Record whether the real producer was dispatched."""
    calls = []

    def _fake(project, container=None, pdk=None):
        calls.append(str(project))
        return R.StepResult("io_pad_chip_top_gen", "PASS", 0.0, "WROTE")

    monkeypatch.setattr(R, "step_io_pad_chip_top_gen", _fake)
    return calls


# ── the defect: a HARDMACRO must not reach the producer ──────────────────────
def test_hardmacro_with_a_slot_catalogue_does_not_dispatch_the_producer(
        tmp_path, spy):
    """The measured subservient shape: a slot CATALOGUE beside a HARDMACRO."""
    p = _project(tmp_path, deliverable="HARDMACRO", slots=True)
    res = R._padring_producer_dispatch(p)
    assert spy == [], (
        "step 15.5ic's pad-ring producer was dispatched for a HARDMACRO "
        "delivery; its die_required_um then pins the floorplan to a ring "
        "nothing places")
    assert res.status == "SKIP"
    assert "HARDMACRO" in res.detail


def test_the_skip_names_the_delivery_it_decided_on(tmp_path, spy):
    p = _project(tmp_path, deliverable="HARDMACRO", slots=True)
    res = R._padring_producer_dispatch(p)
    assert "tapeout_declaration.json" in res.detail, (
        "the SKIP must name WHERE the delivery was declared, so the decision "
        "is readable from the run log alone: %r" % res.detail)


def test_a_delivery_with_no_submission_template_does_not_dispatch_it(
        tmp_path, spy):
    """No slot taken and no self-tape-out is not a chip path either."""
    p = _project(tmp_path)
    res = R._padring_producer_dispatch(p)
    assert spy == []
    assert res.status == "SKIP"


# ── the other direction: a DIE delivery must STILL get its ring ──────────────
def test_self_tapeout_still_dispatches_the_producer(tmp_path, spy):
    """The control. A self-tape-out IS a die and owns its pads."""
    p = _project(tmp_path, self_tapeout=True)
    res = R._padring_producer_dispatch(p)
    assert spy == [str(p)], (
        "a SELF_TAPEOUT delivery must still dispatch the pad-ring producer -- "
        "a fix that simply stopped dispatching it would delete the pad ring")
    assert res.status == "PASS"


def test_a_die_delivery_that_took_a_slot_still_dispatches_the_producer(
        tmp_path, spy):
    """The control, second shape: a slot taken by a DIE delivery."""
    p = _project(tmp_path, deliverable="DIE", slots=True)
    res = R._padring_producer_dispatch(p)
    assert spy == [str(p)]
    assert res.status == "PASS"


def test_an_undeclared_slot_delivery_still_dispatches_the_producer(
        tmp_path, spy):
    """NOT_DETERMINED is not an answer, so behaviour is unchanged: the slot
    presence still requests a ring. The fix narrows ONLY on a declared
    HARDMACRO."""
    p = _project(tmp_path, deliverable="NOT_DETERMINED", slots=True)
    res = R._padring_producer_dispatch(p)
    assert spy == [str(p)]
    assert res.status == "PASS"


# ── the seam agrees with the predicate every other consumer uses ─────────────
@pytest.mark.parametrize("kwargs", [
    dict(deliverable="HARDMACRO", slots=True),
    dict(),
    dict(self_tapeout=True),
    dict(deliverable="DIE", slots=True),
    dict(deliverable="NOT_DETERMINED", slots=True),
])
def test_dispatch_follows_chip_path_requests_pad_ring_exactly(
        tmp_path, spy, kwargs):
    p = _project(tmp_path, **kwargs)
    wants_ring = R._chip_path_requests_pad_ring(p)
    R._padring_producer_dispatch(p)
    assert bool(spy) is bool(wants_ring), (
        "the producer dispatch and the ring decision must be ONE decision; "
        "_chip_path_requests_pad_ring=%r but dispatched=%r"
        % (wants_ring, bool(spy)))


# ── the seam must actually be the one step_pnr uses ──────────────────────────
def test_step_pnr_dispatches_the_producer_through_the_seam():
    """A seam nothing calls is decoration. `step_pnr` must reach the producer
    ONLY through `_padring_producer_dispatch`, or the gate is bypassed again
    exactly the way it was before this fix."""
    import inspect
    src = inspect.getsource(R.step_pnr)
    assert "_padring_producer_dispatch(" in src, (
        "step_pnr no longer dispatches the pad-ring producer through the "
        "gated seam")
    assert "step_io_pad_chip_top_gen(project" not in src, (
        "step_pnr still calls step_io_pad_chip_top_gen directly, so the "
        "HARDMACRO gate is bypassed")


# ── a STALE record from an earlier run must not re-pin the floorplan ─────────
def _write_stale_record(project: Path) -> None:
    """A producer record exactly as an earlier run left it on disk."""
    rep = project / "reports" / "phase3"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "io_pad_chip_top.json").write_text(json.dumps({
        "verdict": "WROTE",
        "die_required_um": {"die_side_um": 1962.0,
                            "basis": "an earlier run's pad ring",
                            "ring_depth_um": 381.0,
                            "ring_depth_basis": "an earlier run's pad ring"},
    }), encoding="utf-8")


def test_a_stale_producer_record_does_not_pin_a_hardmacro_die(tmp_path):
    """Not dispatching the producer is not enough on its own: a record written
    by an EARLIER run -- before the delivery was declared, or under an older
    plugin -- is still on disk, and reading it re-pins the floorplan to a ring
    this run does not place."""
    p = _project(tmp_path, deliverable="HARDMACRO", slots=True)
    _write_stale_record(p)
    side, basis = R._padring_required_die_um(p)
    assert side is None, (
        "a stale pad-ring record pinned a HARDMACRO die to %r um" % side)
    assert "builds no pad ring" in basis
    inset, _why = R._padring_core_inset_um(p)
    assert inset is None, (
        "a stale pad-ring record inset a HARDMACRO core by %r um" % inset)


def test_a_die_delivery_still_reads_its_producer_record(tmp_path):
    """The control. A delivery that DOES build a ring must still be sized by
    it -- the ring's two corner cells plus the pads on the longest side are a
    hard geometric minimum, and a die below it makes every side compute
    negative."""
    p = _project(tmp_path, self_tapeout=True)
    _write_stale_record(p)
    side, _basis = R._padring_required_die_um(p)
    assert side == 1962, (
        "a self-tape-out delivery must still be sized by its own pad ring; "
        "got %r" % side)
    inset, _why = R._padring_core_inset_um(p)
    assert inset is not None and inset > 0, (
        "a self-tape-out delivery must still inset its core behind the ring; "
        "got %r" % inset)
