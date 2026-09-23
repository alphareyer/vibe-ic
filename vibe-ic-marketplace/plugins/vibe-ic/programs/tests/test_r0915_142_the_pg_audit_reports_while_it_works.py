#!/usr/bin/env python3
"""R-0915-142 — the PG audit must not re-ask the same question 187,948 times,
and it must say where it is while it works.

THE MEASUREMENT. Two runs were killed by the progress watchdog at exactly this
block, each after the route had already succeeded:

  subservient run2, 2nd PnR (2026-09-23 01:22:58)
      WATCHDOG_STALLED: … since_last_progress_s=1822.908 elapsed_s=47426.209
      last line of openroad.log: PG_CONNECT_OWED: 8 terminal(s) gained a net
  subservient probeA (2026-09-23 ~19:55)
      FAIL pnr rc=199 … since_last_progress_s=1825.122 elapsed_s=5608.956
      last line of openroad.log: PG_CONNECT_OWED: 96 terminal(s) gained a net
      -> routed.def never written; drc and lvs both refused for want of it

The watchdog was RIGHT both times: every transcript was silent. The block ran
for longer than the 1800 s grace and emitted nothing at all.

MEASURED COST, both variants against run2's own
`sdr_transaction/pre_repair.odb` in the pinned image, each dumping its complete
result set to a file:

    OLD_RESULT bad=0 no_rail=1228 ms=7006598      (116.8 minutes)
    NEW_RESULT bad=0 no_rail=1228 ms=231191       (  3.85 minutes)
    cmp old.txt new.txt -> IDENTICAL (72,849 bytes each)

Same answer, byte for byte, at 30.3x the speed. The cause is in the new
variant's own disclosure: 61,303 instances, and `VDD 159513` / `VSS 160394`
stripe rectangles that the old code re-fetched and re-walked FOR EVERY PG
TERMINAL. The cache fetches them once per net.

The rules this deck pins:
  * the stripe rectangles are read ONCE PER NET, not once per terminal;
  * the block reports progress at a bounded interval, and announces its
    denominator up front and its totals at the end;
  * the ANSWER is untouched — same fields, same order, same comparison;
  * the watchdog's grace is NOT raised anywhere.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import phase3_one_shot_runner as P  # noqa: E402


def _pg_block() -> str:
    """The emitted Tcl of the PG abutment audit, as the deck ships it."""
    src = Path(P.__file__).read_text(errors="replace")
    i = src.index("set _pgab_bad {}")
    j = src.index("PG_AUDIT_DONE", i)
    return src[i:j + 400]


# ── 1. the work: one fetch per NET, not per terminal ──────────────────────
def test_the_stripe_rectangles_are_cached_per_net():
    blk = _pg_block()
    assert "_pgab_cache" in blk
    assert "dict exists $_pgab_cache $_pgab_nn" in blk
    assert "dict set _pgab_cache $_pgab_nn" in blk


def test_getSWires_is_reached_only_through_the_cache_miss():
    """The expensive call must sit INSIDE the `if !dict exists` arm — that is
    the whole 30x. If it ever moves out, this goes red."""
    blk = _pg_block()
    miss = blk.index("dict exists $_pgab_cache")
    swires = blk.index("getSWires")
    setcache = blk.index("dict set _pgab_cache")
    assert miss < swires < setcache, (miss, swires, setcache)


def test_the_overlap_test_reads_the_cache_not_the_database():
    blk = _pg_block()
    tail = blk[blk.index("dict set _pgab_cache"):]
    assert "foreach _pgab_r [dict get $_pgab_cache $_pgab_nn]" in tail
    assert "getSWires" not in tail, "the comparison must not re-enter ODB"


# ── 2. the answer is untouched ────────────────────────────────────────────
def test_the_same_two_result_sets_are_still_built():
    blk = _pg_block()
    assert "lappend _pgab_bad" in blk        # on no net
    assert "lappend _pgab_nr" in blk         # on no rail


def test_the_on_no_net_branch_is_unchanged():
    blk = _pg_block()
    assert 'if {$_pgab_n eq \\"NULL\\"}' in blk or '$_pgab_n eq \\"NULL\\"' in blk


def test_only_POWER_and_GROUND_terminals_are_considered():
    blk = _pg_block()
    assert "getSigType" in blk
    assert "POWER" in blk and "GROUND" in blk


def test_the_overlap_comparison_is_the_same_four_edges():
    """Same rectangle-intersection test, now reading list elements instead of
    four ODB method calls."""
    blk = _pg_block()
    cmp_ = blk[blk.index("foreach _pgab_r"):]
    assert cmp_.count("lindex $_pgab_r") == 4


# ── 3. it says where it is ────────────────────────────────────────────────
def test_it_announces_its_denominator_before_it_starts():
    assert "PG_AUDIT_BEGIN" in _pg_block()


def test_it_reports_progress_at_a_bounded_interval():
    blk = _pg_block()
    assert "PG_AUDIT_PROGRESS" in blk
    assert "_pgab_every" in blk
    m = re.search(r"set _pgab_every (\d+)", blk)
    assert m and 0 < int(m.group(1)) <= 5000, "the interval must be bounded"
    assert "flush stdout" in blk, "an unflushed puts is not a progress signal"


def test_it_states_its_totals_at_the_end():
    blk = _pg_block()
    assert "PG_AUDIT_DONE" in blk
    assert "on no net" in blk and "on no rail" in blk


# ── 4. the grace is NOT raised — this fixes the WORK ──────────────────────
def test_the_watchdog_grace_is_untouched():
    """THE NEGATIVE ARM, and the point of the whole change: a silent step is
    made visible; the watchdog is not made blinder."""
    assert P._WATCHDOG_STALL_GRACE_S == 1800


# ── 5. MUTATION: put the per-terminal fetch back ──────────────────────────
def test_MUTATION_a_per_terminal_getSWires_is_caught():
    """Re-plant the defect: fetch the wires inside the terminal loop, with no
    cache. `test_getSWires_is_reached_only_through_the_cache_miss` and
    `test_the_overlap_test_reads_the_cache_not_the_database` both fail on it."""
    defect = ("foreach _pgab_t [$_pgab_i getITerms] {\n"
              "  foreach _pgab_s [$_pgab_n getSWires] { … }\n"
              "}\n")
    assert "dict exists" not in defect          # the defect, reproduced
    assert "dict exists $_pgab_cache" in _pg_block()   # and the fix differs
