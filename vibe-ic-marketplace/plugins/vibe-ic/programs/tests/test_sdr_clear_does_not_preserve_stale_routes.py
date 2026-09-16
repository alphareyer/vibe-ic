#!/usr/bin/env python3
"""R-0915-71 — the SDR clear preserved wires it was about to invalidate.

MEASURED (opentitan_aes x sky130A, 2026-09-16). Pass 4 of the post-route DRV
loop died on

    Error: spare_tielo_spare_inverter_107 1 pin not visited #guides = 7
    Error: checkConnectivity break, net spare_tielo_spare_inverter_107
    [ERROR DRT-0206] checkConnectivity error.

ONE net -- `checkConnectivity` stops at the first failure -- and it cost a
5.4-hour route, `route_ok=0`, and the whole repaired candidate.

v1.8.43 had already diagnosed this exact failure (spm x sky130A) and removed
the `*spare*` NAME filter from the routing clear, because TritonRoute cannot
reconcile a stale detailed route against fresh global-route guides. It left the
`isDoNotTouch` filter alone -- and on a design with spare cells the two
predicates select THE SAME NETS. From this run's own `pre_repair.odb`:

    PG=2  PROTECTED=604 (spare-named=604  other=0)  UNROUTED=703  CLEARABLE=39780

All 604 are `spare_tielo_*` and all 604 are WIRED, so the deleted filter was
still in force under another name, at 604x the scale of the single net that
produced the v1.8.42 failure.

The site-specific fact that decides it: the SDR clear is followed by
`global_route` + `detailed_route`, which regenerates every guide. A preserved
wire there has nothing left to be reconciled against. A caller that does NOT
re-route must still keep those wires, which is why the choice is an argument
and not a new rule inside the proc.

These tests read the EMITTED Tcl, because that is what OpenROAD executes.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tcl_walk                    # noqa: E402
from _pnr_tcl_stub import STUB as _STUB          # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

_CALL = "_vibeic_spare_safe_clear_net $_net"


def _sdr_child(tmp_path: Path) -> str:
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / "ckpt.def"
    ckpt.write_text("CHECKPOINT\n")
    return R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage="postroute_drv_repair")


# ───────────────── the argument exists and both ways are real ──────────────

def _clear(prefix, **kw):
    """Call the emitter tolerantly.

    A RED arm must measure the ANSWER, not a TypeError: without the shim the
    reverted tree fails on the keyword's absence, which would be true of any
    signature change and proves nothing about the behaviour being pinned."""
    try:
        return R._spare_safe_routing_clear_tcl(prefix, **kw)
    except TypeError:
        return R._spare_safe_routing_clear_tcl(prefix)


def test_the_clear_emitter_can_be_told_to_drop_protected_wires():
    keep = _clear("X")
    drop = _clear("X", drop_protected=True)
    assert f"{_CALL} 1]" in drop, (
        "the emitter cannot be told to drop protected wires: " + drop[:400])
    assert f"{_CALL} 0]" in keep, keep[:400]
    assert keep != drop, "both ways emit the same Tcl"


def test_the_default_is_unchanged_so_no_other_site_moves():
    """NEGATIVE CONTROL. Every other clear site must emit exactly what it did
    before; a fix that quietly changed all of them would be a different change
    from the one argued for."""
    ship = R._spare_safe_routing_clear_tcl("SHIP")
    assert f"{_CALL} 1]" not in ship, (
        "the SHIP clear now drops protected wires too -- that is a different "
        "change from the one argued for, and it is not followed by a full "
        "reroute at every SHIP site")
    assert _clear("X") == _clear("X", drop_protected=False)


def test_power_and_ground_are_never_reachable_through_the_argument():
    """The PG skip is not optional. `drop_protected` must not be a way to
    destroy supply wiring -- the proc returns PG before it ever looks at the
    dont_touch flag, and this pins that ordering in the emitted text."""
    proc = R._spare_safe_clear_net_proc_tcl()
    pg = proc.index('return PG')
    # The GUARD, not the parameter declaration -- the parameter is named in the
    # proc's signature on line 1 and would make this comparison meaningless.
    guard = proc.index('if {!$_drop_protected}')
    assert pg < guard, (
        "the POWER/GROUND skip no longer precedes the drop_protected branch; "
        "a caller could reach supply wiring through it")
    assert proc.index('odb::dbWire_destroy') > pg, (
        "a wire can now be destroyed before the POWER/GROUND check runs")


# ─────────────────── the SDR site is the one that drops ────────────────────

def test_the_sdr_child_drops_protected_wires_before_it_reroutes(tmp_path):
    child = _sdr_child(tmp_path)
    assert f"{_CALL} 1]" in child, (
        "the SDR clear still preserves protected wires, and the very next "
        "commands regenerate every guide -- this is the DRT-0206 that was "
        "measured on spare_tielo_spare_inverter_107")


def test_the_drop_is_followed_by_a_full_reroute_which_is_what_justifies_it(
        tmp_path):
    """The argument is site-specific: dropping is safe HERE because the guides
    are rebuilt immediately after. If that order ever changes, the
    justification is gone and this test says so."""
    child = _sdr_child(tmp_path)
    i = child.index(f"{_CALL} 1]")
    tail = child[i:]
    g = tail.index("global_route")
    d = tail.index("detailed_route")
    assert g < d, "global_route no longer precedes detailed_route after the clear"


def test_the_replacement_protection_is_emitted_at_this_site(tmp_path):
    """v1.8.43 replaced the preservation with a MEASUREMENT, and this site
    never emitted it. Dropping the wires without it would remove a protection
    and add nothing."""
    child = _sdr_child(tmp_path)
    assert "SDR_UNROUTED_NETS" in child, (
        "the post-reroute routing-integrity check is not emitted in the SDR "
        "child; the v1.5.65 hazard (a multi-terminal net that comes back with "
        "no wire, whose pins extraction then merges) would be unobserved")
    assert child.index("SDR_UNROUTED_NETS") > child.index(f"{_CALL} 1]"), (
        "the integrity check is emitted BEFORE the clear it is meant to check")


# ─────────────────────────── it still runs ─────────────────────────────────

def test_the_child_deck_is_still_valid_tcl_and_runs_to_its_end(tmp_path):
    """NEGATIVE CONTROL for the whole change: the parent `exec`s this deck and
    cannot debug it."""
    out, err, route = _tcl_walk.walk(
        "source [lindex $argv 0]\n", _STUB + _sdr_child(tmp_path), tmp_path)
    assert "missing close-bracket" not in err, err
    assert "SDR_CHILD_DONE" in out, f"[{route}] {out[-1500:]}"
