"""A checkpoint-restored session must re-assert the PDN's global CONNECTION
RULES, or everything it creates has power/ground terminals owned by no net.

R-0915-12. MEASURED 2026-09-15 (lane icsub2) on `subservient` x gf180mcuD —
same RTL (sha c6378fa74a…), same PDK, same container, same host, only main
moved::

    main 2f230524b   pnr PASS, GDS 8,142,856 B, 9 of 9 sign-off gates,
                     ZERO occurrences of PG_TERMINALS_ON_NO_NET
    main 7c3bab59c   pnr FAIL
                     PG_TERMINALS_ON_NO_NET: 30150 of 41498 power/ground
                     instance terminals (72.7%) are attached to no net after
                     routing (e.g. masters gf180mcu_fd_sc_mcu7t5v0__clkbuf_16)
                     -> no GDS -> drc SKIP, lvs SKIP, and
                        sta_signoff / sta_corner / sta_record FAIL

Every named master is a CTS buffer — an instance created AFTER the PDN step's
one-shot `global_connect`. The mechanism is visible in the run's own artefacts::

    phase3/stage3/pnr/pnr.tcl                                 6 add_global_connection
                                                              + global_connect (:432-438)
    phase3/stage3/pnr/sdr_child_postroute_drv_repair.tcl       0  +  0
    phase3/stage3/pnr/sdr_child_postroute_drv_reconverge.tcl   0  +  0

and it is the SAME CLASS OF LOSS #2255 found for the spare pool, stated in
`_spare_reassert_dont_touch_tcl`'s own docstring: a checkpoint-seeded deck
elides the floorplan..detailed_route region because the checkpoint contains its
RESULT; the INSTANCES come back with the DEF, the SESSION STATE does not.
`dont_touch` was one such invariant. `add_global_connection` / `global_connect`
is the other, and it was not re-asserted.

THE RULES ARE TAKEN FROM THE DECK, NEVER REBUILT. `_build_pdn_tcl` derives them
from the PDK's own LEFs, the macro LEFs and the IO library; a second derivation
here could disagree with the one the parent session actually ran. Re-running the
parent's own lines cannot.

ALL THREE RESTORE SEAMS, and a test that says so: `_write_sdr_child_decks`,
`_pnr_adopt_sdr_candidates` and `_pnr_resume_after_fatal_signal` all restore
from a checkpoint, and a fourth added later must not be able to forget — which
is why both invariants are assembled in one `_after_restore_tcl`.

chip-AGNOSTIC: the fixture deck's net names are `PWRNET` / `GNDNET`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

import pytest  # noqa: E402


# The shape `_build_pdn_tcl` emits, reduced: the connection rules, then the
# one-shot apply, then the grid work a checkpoint DOES carry.
DECK_WITH_PDN = """\
puts "PNR_BEGIN"
if {[catch {
  add_global_connection -net PWRNET -pin_pattern "^PWRNET$" -power
  add_global_connection -net GNDNET -pin_pattern "^GNDNET$" -ground
  add_global_connection -net PWRNET -pin_pattern "^VNW$" -power
  global_connect
  set_voltage_domain -name CORE -power PWRNET -ground GNDNET
  define_pdn_grid -name grid
  pdngen
} err]} { puts "PDN_FAILED: $err" }
detailed_route
puts "PNR_END"
"""

DECK_WITHOUT_PDN = """\
puts "PNR_BEGIN"
detailed_route
puts "PNR_END"
"""


def _rules(text: str):
    return re.findall(r"add_global_connection[^\n}]*", text)


# ── the rules come back, verbatim and in order ────────────────────────────

def test_every_rule_the_deck_ran_is_re_asserted_verbatim():
    got = R._pg_global_connect_reassert_tcl(DECK_WITH_PDN)
    assert _rules(got) == [r.strip() for r in _rules(DECK_WITH_PDN)], got


def test_the_apply_follows_the_rules():
    """`global_connect` applies the rules that are declared before it. A
    re-assert that applied first would apply the empty set."""
    got = R._pg_global_connect_reassert_tcl(DECK_WITH_PDN)
    assert got.index("global_connect}") > got.rindex("add_global_connection")


def test_each_rule_is_individually_nonfatal_and_the_count_is_disclosed():
    got = R._pg_global_connect_reassert_tcl(DECK_WITH_PDN)
    assert got.count("catch") == len(_rules(DECK_WITH_PDN)) + 1  # + the apply
    assert "PDN_GLOBAL_CONNECT_REASSERTED: $_pg_rules_reasserted of 3" in got


def test_a_deck_with_no_pdn_emits_nothing():
    """A design without a PDN must get a byte-identical child deck."""
    assert R._pg_global_connect_reassert_tcl(DECK_WITHOUT_PDN) == ""


def test_the_rules_are_read_from_the_deck_not_rebuilt():
    """Rename the nets in the deck and the re-assert renames with them — it has
    no opinion of its own about what the supplies are called."""
    renamed = DECK_WITH_PDN.replace("PWRNET", "VCCQ").replace("GNDNET", "VSSQ")
    got = R._pg_global_connect_reassert_tcl(renamed)
    assert "VCCQ" in got and "VSSQ" in got
    assert "PWRNET" not in got and "GNDNET" not in got


# ── both invariants, and every seam ───────────────────────────────────────

def test_the_after_restore_block_carries_BOTH_invariants():
    plan = {"instances": [{"name": "spare_0", "cell": "FILLER"}]}
    got = R._after_restore_tcl(DECK_WITH_PDN, plan)
    assert "set_dont_touch spare_0" in got, "the #2255 invariant"
    assert "add_global_connection" in got, "the R-0915-12 invariant"


def test_a_design_with_no_spares_still_gets_the_pdn_rules():
    got = R._after_restore_tcl(DECK_WITH_PDN, None)
    assert "set_dont_touch" not in got
    assert "add_global_connection" in got


def test_every_restore_seam_goes_through_the_one_assembler():
    """THE SEAM TEST. Three functions restore from a checkpoint; a fourth added
    later must not be able to re-assert one invariant and forget the other."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text(errors="replace")
    for fn in ("_write_sdr_child_decks", "_pnr_adopt_sdr_candidates",
               "_pnr_resume_after_fatal_signal"):
        body = src[src.index(f"def {fn}("):]
        body = body[:body.index("\ndef ", 10)]
        assert "_after_restore_tcl(" in body, (
            f"{fn} restores from a checkpoint and does not go through the "
            f"one place both restore invariants are assembled")
    # and nothing calls the spare half on its own any more
    assert src.count("_spare_reassert_dont_touch_tcl(spare_plan)") == 1, (
        "the spare re-assert is reachable only through _after_restore_tcl")


# ── end to end: the derived child deck actually carries them ──────────────

def test_the_derived_child_deck_carries_the_rules(tmp_path):
    """THE CONTROL THE PRE-FIX TREE CAN EXECUTE, over the REAL deck builder.

    It derives a child deck exactly as `_write_sdr_child_decks` does and asks
    what is in it. Pre-fix the answer is ZERO `add_global_connection` lines —
    which is literally what the shipped
    `phase3/stage3/pnr/sdr_child_postroute_drv_{repair,reconverge}.tcl` of the
    failing subservient run contained, beside a `pnr.tcl` carrying six.
    """
    from test_sdr_checkpoint_and_child import _full_pnr_tcl, SITE1
    deck = _full_pnr_tcl(tmp_path)
    assert "add_global_connection" in deck, (
        "the fixture's own PDN block no longer emits connection rules; "
        "re-derive this case before trusting it")
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c="/w/ck.def", stage=SITE1,
        after_restore_tcl=R._after_restore_tcl(deck, None))
    assert "add_global_connection" in child, (
        "the child session creates instances and has no rule saying which pin "
        "patterns belong to the supplies")
    assert "PDN_GLOBAL_CONNECT_REASSERTED" in child


def test_the_derived_adopt_tail_carries_the_rules(tmp_path):
    """The second seam, through its own builder: the tail that finishes the
    sign-off from an ADOPTED candidate restores from a checkpoint too."""
    from test_sdr_checkpoint_and_child import _full_pnr_tcl, SITE1
    deck = _full_pnr_tcl(tmp_path)
    tail = R._build_pnr_resume_tcl_text(
        deck, checkpoint_def_c="/w/cand.def", omit_stages=[SITE1],
        after_restore_tcl=R._after_restore_tcl(deck, None))
    assert "add_global_connection" in tail
    assert "PDN_GLOBAL_CONNECT_REASSERTED" in tail


def test_the_child_decks_the_RUNNER_writes_carry_the_rules(tmp_path):
    """THE CONTROL THE PRE-FIX TREE CAN EXECUTE, and the one that reproduces
    the measured artefact exactly.

    It drives the shipped `_write_sdr_child_decks` — the function that wrote
    `phase3/stage3/pnr/sdr_child_postroute_drv_repair.tcl` on the failing
    subservient run — and reads what it put on disk. Measured there:

        pnr.tcl                                6 add_global_connection + global_connect
        sdr_child_postroute_drv_repair.tcl     0  +  0
        sdr_child_postroute_drv_reconverge.tcl 0  +  0

    It needs no new symbol, so the pre-fix tree answers for itself.
    """
    from test_sdr_checkpoint_and_child import _full_pnr_tcl
    deck = _full_pnr_tcl(tmp_path)
    parent = tmp_path / "pnr.tcl"
    parent.write_text(deck)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    failures = R._write_sdr_child_decks(parent, out, container="", spare_plan=None)
    written = sorted(out.glob("sdr_child_*.tcl"))
    assert written, f"no child deck was written; failures={failures}"
    for child in written:
        text = child.read_text()
        assert "add_global_connection" in text, (
            f"{child.name} restores from a checkpoint, goes on to create "
            f"instances, and carries none of the {len(_rules(deck))} global "
            f"connection rules its parent deck ran")

