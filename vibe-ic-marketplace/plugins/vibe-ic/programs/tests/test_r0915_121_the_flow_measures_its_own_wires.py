"""The flow measures its own wires at every post-route boundary.

R-0915-121(b). THE MISSING INSTRUMENT.

R-0915-110(b) put a `pin_access` probe between every post-route stage so a pin
that loses its last access point can be attributed to the stage that took it.
There was no equivalent for WIRES, and that is why two ICs each lost a run to
a stage that destroyed routing it never named:

  spm x gf180mcuD as a DIE (lane icspm5, run13, main 287e8a7b0): the antenna
  loop's native `repair_antennas -reroute` raised DRT-0206 with
  checkConnectivity naming 86 DISTINCT nets; 450 lines later the sign-off
  integrity check found 84 signal nets with two or more terminals and no wire.
  The 12 names the Tcl emitted were NOT among the 86, so whether they were the
  same nets could not be answered from the run's own output AT ALL -- the
  emission was capped at twelve and nothing wrote the rest down.

  subservient x gf180mcuD as a DIE (int9/int10, this lane): 77 nets, and the
  bracket had to be reconstructed by hand afterwards, from a text parse of
  `routed_preantenna.def` (5319 net records, 33 wireless multi-terminal, all of
  them the I/O nets the abutment clause excuses, ZERO `u_core/*`) against a
  measure-only probe on `antenna_pre_repair.odb` (ODB_NOWIRE_MULTITERM 0).
  Both numbers were available to the run and the run never took them.

So: the same geometric rule the sign-off integrity check already uses -- two or
more terminals, no wire, and terminals that do not already touch -- runs at
every post-route boundary beside the pin_access probe, and the membership goes
to a FILE, uncapped, named for the boundary. "Intact here, gone there" becomes
a measurement the flow makes itself.

MEASURE-ONLY at every boundary: it counts, names and writes. It never routes,
never repairs and never refuses -- the one site that refuses is the sign-off
check that already did, and all that changed there is that its refusal now
says where the full list is.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402
import pad_signal_route_repair as PS  # noqa: E402

BOUNDARIES = (
    "after_postroute_spef_extract",
    "after_postroute_antenna_repair",
    "after_postroute_drv_reconverge",
    "after_postroute_antenna_reconverge",
    "after_postroute_fill_before_pg_reconnect",
)


def _pnr_src():
    return Path(R.__file__).read_text()


def test_every_pin_access_boundary_also_measures_wires():
    """The two probes answer the two halves of the same question, so they sit
    at the same five places."""
    src = _pnr_src()
    for b in BOUNDARIES:
        assert f'_pin_access_probe_tcl("{b}")' in src, b
        # the unrouted probe now also carries the run's own directory, so the
        # membership file cannot land wherever the process was standing --
        # see test_every_boundary_writes_into_the_runs_own_directory.
        assert f'_unrouted_probe_tcl("{b}", out_dir_c)' in src, b


def test_the_probe_is_measure_only():
    """It must never route, repair or refuse: a probe that can change the
    design is not a probe."""
    for b in BOUNDARIES:
        t = R._unrouted_probe_tcl(b)
        cmds = "\n".join(l for l in t.splitlines()
                         if not l.lstrip().startswith("#"))
        for forbidden in ("detailed_route", "global_route", "repair_antennas",
                          "write_db", "read_db", "error "):
            assert forbidden not in cmds, (b, forbidden)


def test_the_probe_names_its_boundary_in_a_field_not_in_the_marker():
    """WAS `test_the_probe_names_its_boundary_and_its_file`, which pinned the
    stage INTO the marker name (`UNROUTED_PROBE_AFTER_POSTROUTE_ANTENNA_
    REPAIR_...`). HANDBACK on 5b5bd02af: that put a SECOND per-pass token,
    differently spelled, outside the one normalisation
    `test_splitting_the_reconverge_block_did_not_drop_the_second_antenna_pass`
    applies -- so the two antenna passes stopped being the re-emission of each
    other and the byte-equality this lane wrote in #2423 broke. The marker is
    now stage-free and the stage is a FIELD, spelled exactly as the pin-access
    probe spells it, so one normalisation covers both probes."""
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    assert "UNROUTED_PROBE_UNROUTED_NETS:" in t
    assert "stage=after_postroute_antenna_repair" in t
    assert "unrouted_after_postroute_antenna_repair.txt" in t
    # the stage must NOT be baked into any marker NAME
    assert "UNROUTED_PROBE_AFTER_POSTROUTE" not in t


def test_the_stage_token_is_spelled_as_the_pin_access_probe_spells_it():
    """THE WHOLE POINT: one token, one normalisation, both probes. If these
    two ever spell the same boundary differently, the antenna passes differ in
    two places again and only one of them gets normalised."""
    for b in BOUNDARIES:
        pin = R._pin_access_probe_tcl(b)
        unr = R._unrouted_probe_tcl(b)
        assert b in pin, b
        assert b in unr, b
        # and nothing in the unrouted probe carries a DIFFERENT spelling of it
        assert b.upper() not in unr, b


def test_a_caller_with_no_stage_emits_exactly_what_it_did_before():
    """SHIP's line is parsed by the promotion gate (`SHIP_UNROUTED_NETS:`),
    so the stage field must be absent, not empty, when nobody named a stage."""
    t = R._routing_integrity_check_tcl("SHIP")
    assert 'puts "SHIP_UNROUTED_NETS: $_unr [join $_unrn ,]"' in t
    assert "stage=" not in t


def test_the_probe_writes_the_membership_uncapped():
    """The stdout line keeps its 12-name cap on purpose -- stdout is a summary.
    The FILE is the answer, and it holds every name."""
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    assert "foreach _unnm $_unra { puts $_unfh $_unnm }" in t
    assert "lappend _unra [$_net getName]" in t
    # the capped stdout list is unchanged
    assert "if {[llength $_unrn] < 12} { lappend _unrn [$_net getName] }" in t


def test_an_unwritable_membership_file_is_disclosed_not_swallowed():
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    assert "_MEMBERSHIP_UNWRITTEN: $_unfe" in t


def test_a_caller_that_asks_for_no_file_gets_the_emission_it_always_got():
    """The existing SHIP and SDR sites are byte-unchanged: the membership file
    is opt-in, so nothing that did not ask for it acquires a new failure
    mode."""
    t = R._routing_integrity_check_tcl("SHIP")
    assert "MEMBERSHIP" not in t
    assert "_unfh" not in t
    assert "SHIP_UNROUTED_NETS" in t


def test_the_refusal_that_ends_the_run_names_its_membership_file():
    """R-0915-121(b): NAMED_VIOL_REROUTE_INCOMPLETE is the marker that ended
    both ICs' runs with twelve names and no way to get the rest."""
    t = PS.strict_integrity_tcl("NAMED_VIOL_REROUTE")
    assert "unrouted_named_viol_reroute.txt" in t
    m = re.search(r'error "NAMED_VIOL_REROUTE_INCOMPLETE: (.*?)"\}', t, re.S)
    assert m, t[-400:]
    assert "the full membership of all $_unr net(s) is in" in m.group(1)
    assert "capped at 12" in m.group(1)


def test_the_refusal_still_refuses():
    """The instrument was added to a REFUSAL; it must still refuse."""
    t = PS.strict_integrity_tcl("NAMED_VIOL_REROUTE")
    assert 'if {$_unr != 0} {error "NAMED_VIOL_REROUTE_INCOMPLETE' in t
    assert 'error "NAMED_VIOL_REROUTE_UNROUTED_CHECK_FAILED: $e"' in t
    assert 'puts "NAMED_VIOL_REROUTE_UNROUTED_CHECK_NONFATAL' not in t


def test_the_abutment_clause_survives_at_every_boundary():
    """The probe must excuse the I/O nets the sign-off check excuses, or every
    padded design reads 31 unrouted nets at every boundary and the instrument
    is noise. int9 measured 31 abutted, 0 shape-blind."""
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    assert "_ABUTTED_NETS" in t
    assert "_UNROUTED_SHAPE_BLIND" in t
    assert "_vibeic_shapes_touch" in t


def test_every_emitted_probe_is_balanced_tcl():
    for b in BOUNDARIES:
        t = R._unrouted_probe_tcl(b)
        assert sum(l.count("{") - l.count("}") for l in t.splitlines()) == 0, b
        assert sum(l.count("[") - l.count("]") for l in t.splitlines()) == 0, b
    t = PS.strict_integrity_tcl("NAMED_VIOL_REROUTE")
    assert sum(l.count("{") - l.count("}") for l in t.splitlines()) == 0


def test_the_membership_file_is_written_only_when_it_has_names_in_it():
    """CAUGHT BY THE REPO'S OWN suite_write_guard, not by me. The path is
    relative -- correct for the flow, whose cwd IS the pnr directory, the same
    convention `antenna_iter_*.rpt` already uses -- but several tests execute
    the emitted deck with the cwd inside the checkout, and an unconditional
    write dropped SEVEN `unrouted_*.txt` files into the source tree on the
    first full run of the impacted selection.

    Gating the write on `$_unr > 0` fixes that and is better behaviour anyway:
    a file per boundary per run on a healthy design is clutter, the stdout
    counts are the summary, and the file is only needed when it has names in
    it. Tcl's `&&` short-circuits, so a clean boundary does not even open the
    file."""
    for b in BOUNDARIES:
        t = R._unrouted_probe_tcl(b)
        assert "if {$_unr > 0 && [catch {" in t, b
        assert "} elseif {$_unr > 0} {" in t, b
    t = PS.strict_integrity_tcl("NAMED_VIOL_REROUTE")
    assert "if {$_unr > 0 && [catch {" in t


def test_a_boundary_with_nothing_unrouted_still_reports_its_counts():
    """The instrument must not go silent when it has good news -- "no probe
    line" and "a clean boundary" have to stay distinguishable."""
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    i_counts = t.index("UNROUTED_PROBE_UNROUTED_NETS:")
    i_gate = t.index("if {$_unr > 0 && [catch {")
    assert i_counts < i_gate, "the counts are printed before the file is gated"


# ── the membership file belongs to the RUN, not to the process cwd ──────────
#
# MEASURED on spm run16L: the spef_extract boundary wrote
#   /home/reyerchu/_lane_icspm5/unrouted_after_postroute_spef_extract.txt
# -- 734 bytes, 6 nets, on the SHARED LANE ROOT at 03:07:20, where the next
# run of anything overwrites it; icspm5 had to preserve a copy under
# run16L/lane_root_strays/ to keep the evidence.
#
# I argued when this landed that a relative name was safe because "the flow's
# cwd IS the pnr directory, the same convention `antenna_iter_*.rpt` has
# always used". That was wrong: it is true at SOME boundaries and not at
# others, and a file whose whole purpose is to say WHICH nets, in THIS run,
# cannot depend on where the process happened to be standing.

def test_every_boundary_writes_into_the_runs_own_directory():
    for b in BOUNDARIES:
        t = R._unrouted_probe_tcl(b, "/w/proj/phase3/stage3/pnr")
        assert f"/w/proj/phase3/stage3/pnr/unrouted_{b}.txt" in t, b
        # and nothing writes a bare relative name
        assert f"open unrouted_{b}.txt" not in t, b


def test_every_call_site_passes_the_run_directory():
    """A probe that CAN take an absolute path but is called without one is
    the same defect with an extra step."""
    src = Path(R.__file__).read_text()
    for b in BOUNDARIES:
        assert f'_unrouted_probe_tcl("{b}", out_dir_c)' in src, b
    import re
    bare = [m.group(0) for m in
            re.finditer(r'\{_unrouted_probe_tcl\("[^"]+"\)\}', src)]
    assert not bare, f"call site(s) omit the run directory: {bare}"


def test_the_refusal_site_also_writes_into_the_runs_own_directory():
    t = PS.strict_integrity_tcl("NAMED_VIOL_REROUTE", "/w/proj/phase3/stage3/pnr")
    assert "/w/proj/phase3/stage3/pnr/unrouted_named_viol_reroute.txt" in t


def test_a_caller_that_gives_no_directory_still_emits_something_readable():
    """OVER-BREADTH CONTROL: the parameter is optional so the helper stays
    unit-testable, and the relative fallback must still be a valid path --
    it is the CALL SITES that must supply the directory, and the test above
    is what enforces that."""
    t = R._unrouted_probe_tcl("after_postroute_antenna_repair")
    assert "unrouted_after_postroute_antenna_repair.txt" in t
