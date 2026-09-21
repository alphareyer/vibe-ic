"""A net stripped to a stub still HAS a wire, and the census must see that.

MEASURED on spm run17 (main 41a1613e6 + released 0.3.70). The run reported
`0 unrouted at every boundary`, antenna repair APPLIED, scoped_reroute=1 --
and its routed.def carries 33,557 wire lines against run15's 92,576.

Measured again on run17's own two surviving checkpoints, which is what makes
the case airtight:

    sdr_transaction/pre_repair.odb             655 nets, 0 with no wire,
                                               32634 wire units
    sdr_transaction_reconverge/pre_repair.odb  655 nets, 0 with no wire,
                                               17857 wire units
    nets whose wire CONTENT moved: 655 -- 654 shrank, 1 grew
    the one that GREW is p__core, 30 -> 50: the scoped route's own target,
    doing exactly what it should
    biggest losses: u_core/net12 1680->846, u_core/net8 550->278,
                    u_core/yr 415->216

-45.3 % of the wire across one transaction, every net still "routed", and the
instrument said zero. R-0915-121's boundary probe asks whether a net HAS a
wire; that question cannot see this. The antenna census in the same ruling
DOES compare `[$wire length]`, which is why it caught its own damage -- the
two were written to different standards and only one of them was right.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

_H = r"""
set ::nets {%(nets)s}
array set ::len0 {%(len0)s}
array set ::len1 {%(len1)s}
set ::phase 0
namespace eval ord { proc get_db_block {} { return ::BLK } }
proc ::BLK {method args} {
  switch -- $method { getNets { set r {} ; foreach n $::nets { lappend r ::NET_$n } ; return $r } }
  return NULL
}
foreach _n $::nets {
  proc ::NET_$_n {method args} [string map [list @N $_n] {
    switch -- $method {
      getName   { return @N }
      isSpecial { return 0 }
      getWire   {
        set l [expr {$::phase ? $::len1(@N) : $::len0(@N)}]
        if {$l == 0} { return NULL }
        return ::WIRE_@N
      }
    }
    return 0
  }]
  proc ::WIRE_$_n {method args} [string map [list @N $_n] {
    switch -- $method { length { return [expr {$::phase ? $::len1(@N) : $::len0(@N)}] } }
    return 0
  }]
}
"""


def _drive(nets, before, after, scope=None):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    body = (R._wire_content_census_tcl("_b")
            + "set ::phase 1\n"
            + (f"set _scope {{{' '.join(scope)}}}\n" if scope else "")
            + R._wire_content_compare_tcl("_b", "DRIFT",
                                          "_scope" if scope else ""))
    script = (_H % {"nets": " ".join(nets),
                    "len0": " ".join(f"{n} {before[n]}" for n in nets),
                    "len1": " ".join(f"{n} {after[n]}" for n in nets)}
              + body + '\nputs "OK=$DRIFT_OK"\n')
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "c.tcl"
        f.write_text(script)
        return subprocess.run([tclsh, str(f)], capture_output=True, text=True,
                              cwd=td)


@needs_tclsh
def test_run17s_shape_is_seen_a_net_that_keeps_a_wire_and_loses_most_of_it():
    """THE CASE THE OLD PROBE MISSED: nothing reaches zero, so nothing was
    reported, while the wire halved."""
    nets = ["a", "b", "c"]
    r = _drive(nets, {"a": 1680, "b": 550, "c": 30},
               {"a": 846, "b": 278, "c": 50})
    assert r.returncode == 0, r.stderr
    m = re.search(r"DRIFT: nets_changed=(\d+) shrank=(\d+) grew=(\d+) "
                  r"wire_before=(\d+) wire_after=(\d+)", r.stdout)
    assert m, r.stdout
    assert m.group(1) == "3" and m.group(2) == "2" and m.group(3) == "1"
    assert m.group(4) == "2260" and m.group(5) == "1174"


@needs_tclsh
def test_a_net_that_did_not_move_is_not_reported():
    """OVER-BREADTH CONTROL: an untouched design must read zero drift."""
    nets = ["a", "b"]
    r = _drive(nets, {"a": 100, "b": 200}, {"a": 100, "b": 200})
    assert "nets_changed=0 shrank=0 grew=0" in r.stdout


@needs_tclsh
def test_a_change_outside_a_declared_scope_fails_the_check():
    """The scoped-route acceptance, as a predicate: icord1 measured 654 of
    654 held byte-identical, and anything less is a refusal."""
    nets = ["p__core", "other"]
    r = _drive(nets, {"p__core": 30, "other": 500},
               {"p__core": 50, "other": 250}, scope=["p__core"])
    assert "outside_declared_scope=1" in r.stdout
    assert "OK=0" in r.stdout


@needs_tclsh
def test_a_change_inside_the_declared_scope_passes():
    nets = ["p__core", "other"]
    r = _drive(nets, {"p__core": 30, "other": 500},
               {"p__core": 50, "other": 500}, scope=["p__core"])
    assert "outside_declared_scope=0" in r.stdout
    assert "OK=1" in r.stdout


@needs_tclsh
def test_with_no_declared_scope_the_drift_is_disclosed_not_forgiven():
    """A pass that declares no scope cannot have anything 'out of scope', and
    saying so is honest. What it must NOT do is stay quiet: run17's numbers
    existed and nothing printed them."""
    nets = ["a"]
    r = _drive(nets, {"a": 1000}, {"a": 400})
    assert "nets_changed=1 shrank=1" in r.stdout
    assert "no scope declared" in r.stdout
    assert "this is a disclosure, not a pass" in r.stdout
    assert "OK=1" in r.stdout


@needs_tclsh
def test_an_unreadable_length_is_never_called_a_change():
    """UNMEASURED IS NOT ZERO, and it is not guilt either."""
    nets = ["a"]
    r = _drive(nets, {"a": -1}, {"a": -1})
    assert "nets_changed=0" in r.stdout


def test_the_transaction_censuses_before_and_compares_at_the_decision():
    b = R._postroute_sdr_transaction_begin_tcl("/w/pnr", "postroute_drv_repair")
    f = R._postroute_sdr_transaction_finish_tcl("postroute_drv_repair")
    assert "_sdr_wire0" in b
    assert "SDR_TRANSACTION_WIRE_DRIFT" in f
    assert f.index("SDR_TRANSACTION_WIRE_DRIFT") < f.index(
        "SDR_TRANSACTION_DECISION")


def test_every_boundary_keeps_a_def_so_the_claim_is_falsifiable():
    """`antenna_pass_pre.odb` is deleted on the success path -- I added that
    delete -- so on a FINISHED run "the scoped route held 654 nets" cannot be
    checked at all. A DEF is the artefact the byte-comparison acceptance uses
    and is a third the size."""
    src = Path(R.__file__).read_text()
    for b in ("after_postroute_spef_extract", "after_postroute_antenna_repair",
              "after_postroute_drv_reconverge",
              "after_postroute_antenna_reconverge",
              "after_postroute_fill_before_pg_reconnect"):
        assert f'_boundary_def_tcl("{b}", out_dir_c)' in src, b
    t = R._boundary_def_tcl("after_postroute_antenna_repair", "/w/pnr")
    assert "/w/pnr/boundary_after_postroute_antenna_repair.def" in t
    assert "BOUNDARY_DEF_UNWRITTEN" in t


def test_the_emitted_fragments_are_balanced_tcl():
    for t in (R._wire_content_census_tcl("_x"),
              R._wire_content_compare_tcl("_x", "M"),
              R._wire_content_compare_tcl("_x", "M", "_s"),
              R._boundary_def_tcl("t", "/w/pnr")):
        assert sum(l.count("{") - l.count("}") for l in t.splitlines()) == 0
        assert sum(l.count("[") - l.count("]") for l in t.splitlines()) == 0


# ── extraction is a DB-mutating step and is bracketed like one ─────────────
#
# It reads like a measurement and is not: OpenRCX's `orderWires` re-encodes
# every net through `tmg_conn`, and on spm run17 that is what took the wire
# (fork PR #23, merged 9e331799; the fork's RoutingPreserver restores
# 655/655). The flow cannot rely on a fork fix to know whether its own
# database survived a step -- it measures.

def _rendered_decks():
    """Every deck that invokes `extract_parasitics` after `detailed_route`,
    rendered with the arguments the repo's own tests use."""
    return {
        "postroute_spef_extract": R._post_route_spef_repair_tcl(
            "/pnr", "/t.tlef", "/c.lef"),
        "postroute_timing_repair": R._build_postroute_timing_repair_tcl(
            "top", "/t.tlef", "/c.lef", "/l.lib", "/pnr", "/rep", "M",
            post_route_start=True, captables_c={"nom": "/cap.rules"}),
        "signoff_drv_repair": R._v1_8_100_signoff_drv_repair_tcl("/pnr"),
        "ship_signoff_spef_repair": R._ship_signoff_spef_repair_tcl(
            "top", "/t.tlef", "/c.lef", "/ss.lib", "/pnr", "/cap.rules", "M",
            8),
        "ship_cvg_restore": R._ship_cvg_restore_tcl(
            "top", "/t.tlef", "/c.lef", "/ss.lib", "/pnr", "/cap.rules", "M",
            8, "/pnr/ship_cvg_pass1.def"),
        "ship_wire_length_escalation": R._ship_wire_length_escalation_tcl(
            "top", "/t.tlef", "/c.lef", "/ss.lib", "/pnr", "/cap.rules", "M",
            8),
    }


def test_every_db_mutating_post_route_step_is_bracketed_not_only_the_antenna():
    """R-0915-125(1): EVERY post-route step whose result ships is bracketed.

    The set is derived, not asserted by hand: every deck that invokes
    `extract_parasitics` after `detailed_route` must appear in
    `_EXTRACTION_BRACKET_SITES` or be safe BY ORDERING (its artefact written
    before it extracts), and the ordering case is pinned separately below."""
    decks = _rendered_decks()
    assert set(R._EXTRACTION_BRACKET_SITES) == set(decks), (
        "a deck that extracts on a routed database is neither bracketed nor "
        "ordering-safe")
    for site, tcl in decks.items():
        lines = tcl.splitlines()
        i_cen = next(i for i, l in enumerate(lines)
                     if l.startswith(f"array unset _xb_{site}"))
        i_ext = next(i for i, l in enumerate(lines)
                     if "extract_parasitics" in l and i > i_cen)
        i_cmp = next(i for i, l in enumerate(lines)
                     if f"EXTRACT_WIRE_DRIFT_{site.upper()}:" in l)
        assert i_cen < i_ext < i_cmp, site
        assert f"EXTRACT_MUTATED_THE_ROUTE: site={site} " in tcl, site
        assert f"/boundary_before_extract_{site}.def" in tcl, site


def test_the_bracket_names_no_scope_because_extraction_names_no_net():
    """Extraction NAMES NO NET, so every net it moved is one it did not name.
    Passing a scope here would silently forgive exactly the failure."""
    t = R._extraction_bracket_tcl("s", "puts x\n", out_dir_c="/pnr")
    assert "outside_declared_scope" not in t
    assert "no scope declared" in t


def test_every_bracketed_deck_refuses_to_ship_a_mutated_candidate():
    """R-0915-125(1) -- refuse AND restore. The restore is the artefact
    already on disk: not writing the candidate leaves the verified route
    exactly where it was."""
    candidates = {"ship_signoff_spef_repair": "/pnr/routed_repaired.def",
                  "ship_cvg_restore": "/pnr/routed_cvg_restored.def",
                  "ship_wire_length_escalation": "/pnr/routed_escalated.def"}
    decks = _rendered_decks()
    for site, cand in candidates.items():
        tcl = decks[site]
        i_guard = tcl.index("ROUTE_MUTATION_REFUSES_CANDIDATE")
        i_write = tcl.index(f"write_def {cand}")
        assert i_guard < i_write, (
            f"{site}: the candidate is written before anything asks whether "
            "the database it was built on is still the verified one")
        assert tcl.count(f"write_def {cand}") == 1, site


def test_the_drv_repair_pass_is_unselectable_when_the_route_was_mutated():
    tcl = _rendered_decks()["signoff_drv_repair"]
    i_guard = tcl.index("ROUTE_MUTATION_REFUSES_CANDIDATE")
    i_write = tcl.index("write_db $_sdr_pass_odb")
    assert i_guard < i_write
    assert "set _sdr_pass_saved 0" in tcl, (
        "the refused pass must stay unselectable, which is the state the "
        "loop's own write-failure branch already produces")


def test_the_two_decks_that_are_safe_by_ordering_stay_that_way():
    """`_si_mcf_repair_child_tcl` and the post-route timing repair write their
    artefacts BEFORE they extract. That is a property a later edit can take
    away silently, so it is asserted rather than trusted."""
    child = R._si_mcf_repair_child_tcl(
        "top", tech_lef_c="/t.tlef", cell_lef_c="/c.lef", liberty_c="/l.lib",
        pnr_dir_c="/pnr", txn_dir_c="/pnr/txn", sdc_c="/c.sdc",
        max_captable_c="/cap.rules", metal_prefix="M", thread_count=8,
        repair_body="repair_design\n")
    assert child.index("write_def /pnr/txn/") < child.index(
        "extract_parasitics")
    assert child.index("write_db /pnr/txn/") < child.index(
        "extract_parasitics")
    rep = _rendered_decks()["postroute_timing_repair"]
    assert rep.index("write_def /rep/timing_repaired.def") < rep.index(
        "extract_parasitics")


def test_extraction_changing_a_wire_is_named_as_mutation_not_measurement():
    src = Path(R.__file__).read_text()
    assert "SPEF_EXTRACT_MUTATED_THE_ROUTE" in src
    assert "supposed to READ this database" in src
    assert "RoutingPreserver" in src


def test_the_transaction_census_does_not_hide_behind_a_missing_candidate():
    """MEASURED on spm run17 (2026-09-22, read-only, on its own artefacts):
    both SDR children came back `mutated=0`, so the comparison against the
    checkpoint this very session had just written was inside a branch that
    never ran -- while the session's wire fell from 6503 DEF wire records at
    `sdr_transaction/pre_repair.def` to 1069 at
    `sdr_transaction_reconverge/pre_repair.def`, 655 of 693 nets changed
    (u_core/net12 336 -> 50).  "No candidate" is a statement about the CHILD."""
    f = R._postroute_sdr_transaction_finish_tcl("postroute_drv_repair")
    i_drift = f.index("SDR_TRANSACTION_WIRE_DRIFT:")
    i_mut = f.index("$_sdr_tx_mutated")
    assert i_drift < i_mut, (
        "the drift comparison is gated on there being a candidate; run17 had "
        "none and lost 84 % of its wire records in the same window")


def _drive_guard(mutated):
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    script = ((f"set {R._ROUTE_MUTATION_VAR} {{rcx}}\n" if mutated else "")
              + R._route_mutation_guard_tcl('puts "WROTE"\n', "site_x"))
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "g.tcl"
        f.write_text(script)
        return subprocess.run([tclsh, str(f)], capture_output=True, text=True,
                              cwd=td)


@needs_tclsh
def test_driven_the_guard_writes_when_nothing_moved_the_route():
    r = _drive_guard(False)
    assert r.returncode == 0, r.stderr
    assert "WROTE" in r.stdout
    assert "ROUTE_MUTATION_REFUSES_CANDIDATE" not in r.stdout


@needs_tclsh
def test_driven_the_guard_refuses_and_says_who_moved_it():
    r = _drive_guard(True)
    assert r.returncode == 0, r.stderr
    assert "WROTE" not in r.stdout
    assert "ROUTE_MUTATION_REFUSES_CANDIDATE: site=site_x mutated_by=rcx" \
        in r.stdout
    assert "route already on disk is kept" in r.stdout


def _drive_bracket(before, after):
    """Run a real bracket against a fake db whose wire lengths move."""
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    nets = sorted(before)
    body = (R._extraction_bracket_tcl(
        "sitex", "set ::phase 1\n", out_dir_c="/pnr")
        + f'if {{![info exists {R._ROUTE_MUTATION_VAR}]}} '
          f'{{ set {R._ROUTE_MUTATION_VAR} {{}} }}\n'
          f'puts "MUTBY=[join ${R._ROUTE_MUTATION_VAR} {{,}}]"\n')
    script = (_H % {"nets": " ".join(nets),
                    "len0": " ".join(f"{n} {before[n]}" for n in nets),
                    "len1": " ".join(f"{n} {after[n]}" for n in nets)}
              + "proc write_def {args} { return }\n"
              + body)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "b.tcl"
        f.write_text(script)
        return subprocess.run([tclsh, str(f)], capture_output=True, text=True,
                              cwd=td)


@needs_tclsh
def test_driven_a_step_that_moved_nothing_arms_no_refusal():
    """BOTH DIRECTIONS. A healthy build -- the fork with RoutingPreserver --
    must pass through the bracket without refusing anything."""
    r = _drive_bracket({"a": 100, "b": 200}, {"a": 100, "b": 200})
    assert r.returncode == 0, r.stderr
    assert "nets_changed=0" in r.stdout
    assert "EXTRACT_MUTATED_THE_ROUTE" not in r.stdout
    assert "MUTBY=" in r.stdout and "MUTBY=sitex" not in r.stdout


@needs_tclsh
def test_driven_run17s_own_numbers_arm_the_refusal():
    """The three biggest losses run17 actually took, as the fixture."""
    r = _drive_bracket({"u_core/net12": 336, "u_core/net8": 110,
                        "u_core/yr": 83},
                       {"u_core/net12": 50, "u_core/net8": 16,
                        "u_core/yr": 14})
    assert r.returncode == 0, r.stderr
    assert "EXTRACT_MUTATED_THE_ROUTE: site=sitex nets_changed=3 shorter=3" \
        in r.stdout
    assert "restore_from=/pnr/boundary_before_extract_sitex.def" in r.stdout
    assert "MUTBY=sitex" in r.stdout, (
        "the refusal must NAME the step that moved the route; a bare flag "
        "leaves the next candidate's refusal unable to say who did it")


def test_the_new_fragments_are_balanced_tcl():
    for t in (R._extraction_bracket_tcl("s", "puts x\n", out_dir_c="/pnr"),
              R._route_mutation_guard_tcl("puts y\n", "s")):
        assert sum(l.count("{") - l.count("}") for l in t.splitlines()) == 0
        assert sum(l.count("[") - l.count("]") for l in t.splitlines()) == 0
