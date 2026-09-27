"""`--die-um auto` must size from the SITE definition, never from a macro.

`_parse_site_area_um2` fed `_resolve_auto_die_um` (`avg_cell = site_area x
6.0`) until N4 (2026-09-28) replaced that per-PDK constant with the netlist's
own measured cell area; the site now sets only the PDN floor's row step. A
wrong site still propagates into geometry, so the parser's contract stands.

Two defects, both MEASURED on sky130A (plugin 1.9.76, vibeic-eda image 0.2.58 —
written WITHOUT the fully-qualified pull form on purpose: this records which
image a past measurement used, and `sync_image_version.py --check` reads any
`ghcr.io/vibeic/vibeic-eda:X.Y.Z` in a tracked file as a LIVE pointer that must
track the current VERSION. Bumping it would falsify the record; this states the
same fact without claiming to be an install pointer):

  1. WRONG TOKEN. A cell LEF carries two kinds of `SITE` token and only one is
     a definition:
         definition   `SITE unithd`      (bare; then SYMMETRY/CLASS/SIZE/END)
         reference    `SITE unithd ;`    (one inside EVERY macro)
     The old pattern `SITE .*? SIZE w BY h ;` (DOTALL) matched the first
     `SITE` token anywhere — a macro's REFERENCE — and then captured THAT
     MACRO's footprint. On `sky130_fd_sc_hd.lef` it returned 4.14 x 2.72 =
     11.26 um2 instead of the real `SITE unithd` 0.46 x 2.72 = 1.2512 um2.
     9x high on area, i.e. a 9x die: measured end-to-end, `--die-um auto` for
     subservient went 433x433 -> 1299x1299.

  2. WRONG FILE. The cell LEF holds NO site definition at all (measured: 0
     bare-`SITE` lines in `lef/sky130_fd_sc_hd.lef`, 2 in
     `techlef/sky130_fd_sc_hd__nom.tlef`) — so it must fall through to the
     TECH lef rather than accept whatever the cell LEF yields.

NEGATIVE CONTROL: `test_macro_site_reference_is_not_mistaken_for_the_site`
asserts 1.2512 and FAILS (returns 11.2608) against the pre-fix body.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402

# Shaped exactly like sky130_fd_sc_hd.lef: macros FIRST, each carrying a SITE
# REFERENCE and its own SIZE; the site DEFINITION (if any) comes later.
_CELL_LEF_MACROS_FIRST = """\
VERSION 5.7 ;
MACRO sky130_fd_sc_hd__a211oi_1
  CLASS CORE ;
  SITE unithd ;
  SIZE 4.14 BY 2.72 ;
END sky130_fd_sc_hd__a211oi_1
MACRO sky130_fd_sc_hd__inv_1
  CLASS CORE ;
  SITE unithd ;
  SIZE 1.38 BY 2.72 ;
END sky130_fd_sc_hd__inv_1
END LIBRARY
"""

_TECH_LEF = """\
VERSION 5.7 ;
LAYER met1
  TYPE ROUTING ;
END met1
SITE unithd
  SYMMETRY Y ;
  CLASS CORE ;
  SIZE 0.46 BY 2.72 ;
END unithd
SITE unithddbl
  SYMMETRY Y ;
  CLASS CORE ;
  SIZE 0.46 BY 5.44 ;
END unithddbl
"""


def test_macro_site_reference_is_not_mistaken_for_the_site():
    """NEGATIVE CONTROL — pre-fix this returns 11.2608 (a macro) and FAILS.

    The cell LEF declares no site, only references, so the correct answer is
    "no site here" — NOT the first macro's footprint.
    """
    got = R._parse_site_area_um2(_CELL_LEF_MACROS_FIRST)
    assert got != 4.14 * 2.72, (
        "parsed a MACRO's SIZE as the site area — a macro's `SITE x ;` is a "
        "reference, not a definition")
    assert got is None, got


def test_site_definition_in_tech_lef_is_parsed():
    got = R._parse_site_area_um2(_TECH_LEF)
    assert got is not None
    assert abs(got - 0.46 * 2.72) < 1e-9, got


def test_class_core_site_is_preferred_over_a_non_core_site():
    """When a LEF declares several sites, the placement row site (CLASS CORE)
    is the one the die model means — not a pad/corner site that happens to be
    declared first."""
    lef = """\
SITE ioSite
  CLASS PAD ;
  SIZE 60.0 BY 180.0 ;
END ioSite
SITE unithd
  CLASS CORE ;
  SIZE 0.46 BY 2.72 ;
END unithd
"""
    got = R._parse_site_area_um2(lef)
    assert abs(got - 0.46 * 2.72) < 1e-9, got


def test_no_site_anywhere_returns_none_so_the_caller_can_degrade():
    assert R._parse_site_area_um2("VERSION 5.7 ;\nEND LIBRARY\n") is None
    assert R._parse_site_area_um2("") is None
    assert R._parse_site_area_um2(None) is None


# ── gatekeeper Step-2.7 additions ────────────────────────────────────────────
# Three holes found by adversarially attacking the fix above. Each FAILS
# against the fix as first submitted (measured, this worktree):
#
#   CRLF LEF                 -> None      REGRESSION — the pre-fix DOTALL
#                                         pattern parsed a CRLF LEF correctly
#   `SITE unithd;` reference -> 11.2608   the SAME macro-footprint defect this
#                                         file exists to close, in a different
#                                         whitespace style
#   the container read       -> UNTESTED  the half of the change the title is
#                                         named after had no test at all


def test_a_crlf_lef_still_yields_the_site():
    r"""A vendor LEF written with CRLF must not silently lose its site.

    `^…[ \t]*$` cannot cross the `\r` that sits before the `\n`, so the SITE
    anchor and its `END <name>` both miss and the file parses as "no site" —
    routing the die to the fallback CONSTANT. The pre-fix pattern did NOT have
    this problem, so it is a regression, not a pre-existing gap."""
    got = R._parse_site_area_um2(_TECH_LEF.replace("\n", "\r\n"))
    assert got is not None, "CRLF LEF parsed as if it declared no site"
    assert abs(got - 0.46 * 2.72) < 1e-9, got


def test_a_reference_written_without_a_space_before_the_semicolon():
    r"""`SITE unithd;` is still a macro's REFERENCE, not a definition.

    `(\S+)` swallows the `;` into the site NAME, the line then looks like a
    bare definition, no `END unithd;` exists to bound it, and the block runs on
    into the next MACRO — handing back that macro's footprint."""
    lef = """\
MACRO cellA
  CLASS CORE ;
  SITE unithd;
  SIZE 4.14 BY 2.72 ;
END cellA
SITE unithd
  CLASS CORE ;
  SIZE 0.46 BY 2.72 ;
END unithd
"""
    got = R._parse_site_area_um2(lef)
    assert got != 4.14 * 2.72, (
        "`SITE unithd;` was read as a definition and captured the macro's SIZE")
    assert abs(got - 0.46 * 2.72) < 1e-9, got


def test_the_named_site_wins_over_declaration_order():
    """`initialize_floorplan -site <pdk.site>` builds the rows, so the die
    model must measure THAT site. A PDK declaring a double-height CLASS CORE
    site alongside the unit site (sky130A declares both) would otherwise be
    sized for rows the floorplan never builds, purely on declaration order."""
    lef = """\
SITE unithddbl
  CLASS CORE ;
  SIZE 0.46 BY 5.44 ;
END unithddbl
SITE unithd
  CLASS CORE ;
  SIZE 0.46 BY 2.72 ;
END unithd
"""
    assert abs(R._parse_site_area_um2(lef, "unithd") - 0.46 * 2.72) < 1e-9
    # unknown / absent name → the CLASS CORE rule, unchanged
    assert abs(R._parse_site_area_um2(lef, "") - 0.46 * 5.44) < 1e-9
    assert abs(R._parse_site_area_um2(lef, "nosuchsite") - 0.46 * 5.44) < 1e-9


# ── the container read must actually RUN ─────────────────────────────────────
#
# N4 (FX_N4_autodie_sizing, 2026-09-28): the die is no longer sized from the
# SITE (`site area x 6.0 sites/cell` was a sky130-hd constant that sized a
# gf180mcu core 2.46x too small). The auto-sizer now reads each instance's own
# MACRO SIZE from the CELL lef — which lives only inside the EDA image, so the
# container read is still the thing that must actually RUN. The site parser
# above is unchanged and still tested; it now feeds only the PDN row step.
#
# The netlist below instantiates `sky130_fd_sc_hd__inv_1` (1.38 x 2.72 um in
# the fixture), so the measured core is sqrt(10000 x 3.7536 / 0.25) = 387.5 ->
# 388 um and the die 388 + 2 x 10 = 408 — a number the 7.5 fallback constant
# (core 548, die 568) cannot produce.

# Landed fixture (restored verbatim): a site of 1.00 x 2.00.
_TECH_LEF_DISTINCT = """\
VERSION 5.7 ;
LAYER met1
  TYPE ROUTING ;
  PITCH 0.34 ;
END met1
SITE unithd
  CLASS CORE ;
  SIZE 1.00 BY 2.00 ;
END unithd
"""


class _Pdk:
    """In-container PDK paths — neither exists on the host, which is the whole
    point: this is the topology in which the LEF read used to be dead."""
    name = "openpdk"
    site = "unithd"
    cell_lef = "/in-container-only/lef/cells.lef"
    tech_lef = "/in-container-only/techlef/tech.tlef"


def _netlist(tmp_path, n=10000, master="sky130_fd_sc_hd__inv_1"):
    p = tmp_path / "netlist.v"
    p.write_text("module top ();\n" + "".join(
        f"  {master} u{i} (.A(a), .Y(y{i}));\n" for i in range(n))
        + "endmodule\n")
    return p


def test_the_container_read_is_reached_and_the_die_comes_from_it(
        tmp_path, monkeypatch):
    """OBSERVED, not asserted on source: the fake `_docker_exec` RECORDS the
    commands auto-die sizing issues. The cell LEF must be `cat`-ed through the
    container and the die must be the one its MACRO SIZEs imply."""
    calls = []

    def fake_exec(container, cmd, timeout=1800, **kw):
        calls.append(cmd)
        if _Pdk.cell_lef in cmd:
            return 0, _CELL_LEF_MACROS_FIRST, ""
        return 1, "", "cat: no such file or directory"

    monkeypatch.setattr(R, "_docker_exec", fake_exec)
    monkeypatch.setattr(R, "_container_file_text",
                        lambda c, path: fake_exec(c, f"cat {path}")[1] or None)
    assert not Path(_Pdk.cell_lef).is_file()

    die, note = R._resolve_auto_die_um(
        "auto", _netlist(tmp_path), 0.30, _Pdk(), project=None, top="",
        container="an-eda-container")

    assert any(_Pdk.cell_lef in c for c in calls), (
        f"the cell LEF read never reached the container; commands: {calls}")
    assert die == "408x408", (die, note)
    assert "LEF SIZE" in note, note
    assert "FALLBACK CONSTANT" not in note, note


def _deck_stub(pdk, container=None, plan_out=None, **kw):
    """A PDN deck that draws two pitched strap layers and reports nothing to
    `plan_out` -- the tuned-grid deck shape. Its text is what pdngen gets."""
    return ("  add_pdn_stripe -grid grid -layer met4 -width 1.6 -pitch 40.0 "
            "-offset 8.0\n"
            "  add_pdn_stripe -grid grid -layer met5 -width 1.6 -pitch 40.0 "
            "-offset 8.0\n")


def test_the_cell_lef_is_tried_when_the_tech_lef_declares_no_site(
        tmp_path, monkeypatch):
    """Two of the four PDKs shipped in the EDA image keep the site definition
    in the CELL lef, not the tech lef. The fall-through must reach it.

    RESTORED (review wave 3, N4). The landed body asserted `die == "693x693"`
    and `"[site-LEF]" in note`: 693 = sqrt(10000 x 1.00 x 2.00 x 6.0 / 0.25),
    i.e. the removed sites-per-cell constant, which N4 deletes by design, so
    those two lines cannot hold on any tree carrying N4 (measured: the
    verbatim body returns 568x568 there). The SUBJECT -- the tech-LEF-first,
    cell-LEF-second site fall-through -- still exists: the row site now sets
    the PDN floor's snapping step. The name, docstring, fixture and fake exec
    are the landed ones; the assertion is made on the site's one consumer."""
    def fake_exec(container, cmd, timeout=1800, **kw):
        if _Pdk.tech_lef in cmd:
            return 0, "VERSION 5.7 ;\nEND LIBRARY\n", ""
        return 0, _TECH_LEF_DISTINCT, ""

    monkeypatch.setattr(R, "_docker_exec", fake_exec)
    monkeypatch.setattr(R, "_container_file_text",
                        lambda c, path: fake_exec(c, f"cat {path}")[1] or None)
    monkeypatch.setattr(R, "_build_pdn_tcl", _deck_stub)
    detail = {}
    side, basis = R._strap_plan_core_floor(_Pdk(), "an-eda-container",
                                           detail=detail)
    assert detail["site_dims_um"] == [1.0, 2.0], detail
    assert detail["site_dims_from"] == _Pdk.cell_lef, detail
    assert "site/row steps 1/2" in basis, basis


def test_no_site_anywhere_degrades_loudly_instead_of_inventing_one(
        tmp_path, monkeypatch):
    """A PDK that declares no site must NOT get a constant dressed up as a
    measurement. The line has to SAY it is a fallback.

    RESTORED (review wave 3, N4). The landed body asserted the auto die
    `548x548` from the fallback average cell; N4 adds the 2 x 10 um core inset
    (568) and keeps that label in the die note -- covered beside this by
    `test_no_lef_anywhere_degrades_loudly_instead_of_inventing_one`. The site
    itself, where no LEF declares one, still degrades: the PDN floor's basis
    must SAY the row step was unknown rather than invent one."""
    def fake_exec(container, cmd, timeout=1800, **kw):
        return 0, "VERSION 5.7 ;\nEND LIBRARY\n", ""

    monkeypatch.setattr(R, "_docker_exec", fake_exec)
    monkeypatch.setattr(R, "_container_file_text",
                        lambda c, path: fake_exec(c, f"cat {path}")[1])
    monkeypatch.setattr(R, "_build_pdn_tcl", _deck_stub)
    detail = {}
    _side, basis = R._strap_plan_core_floor(_Pdk(), "an-eda-container",
                                            detail=detail)
    assert detail["site_dims_um"] is None
    assert "site size unknown" in basis, basis


def test_a_master_the_lef_does_not_size_is_named_not_guessed(
        tmp_path, monkeypatch):
    """A sum over only the masters one LEF happens to carry is an under-count
    of unknown size. It must degrade to the LABELLED fallback and NAME the
    master it could not size."""
    monkeypatch.setattr(R, "_container_file_text",
                        lambda c, path: _CELL_LEF_MACROS_FIRST)
    die, note = R._resolve_auto_die_um(
        "auto", _netlist(tmp_path, master="unknown_cell_x1"), 0.30, _Pdk(),
        project=None, top="", container="an-eda-container")
    assert "FALLBACK CONSTANT" in note, note
    assert "unknown_cell_x1" in note, note
    assert die == "568x568", (die, note)  # core sqrt(10000*7.5/0.25)=548 +2x10


def test_no_lef_anywhere_degrades_loudly_instead_of_inventing_one(
        tmp_path, monkeypatch):
    """A PDK whose cell LEF cannot be read must NOT get a constant dressed up
    as a measurement. The line has to SAY it is a fallback."""
    monkeypatch.setattr(R, "_container_file_text", lambda c, path: None)
    die, note = R._resolve_auto_die_um(
        "auto", _netlist(tmp_path), 0.30, _Pdk(), project=None, top="",
        container="an-eda-container")
    assert "FALLBACK CONSTANT" in note, note
    assert die == "568x568", (die, note)


def test_a_container_read_that_fails_does_not_break_the_flow(
        tmp_path, monkeypatch):
    """The container read raising must degrade to the disclosed constant, not
    propagate out of die sizing."""
    def boom(container, path):
        raise RuntimeError("docker daemon is not reachable")

    monkeypatch.setattr(R, "_container_file_text", boom)
    die, note = R._resolve_auto_die_um(
        "auto", _netlist(tmp_path), 0.30, _Pdk(), project=None, top="",
        container="an-eda-container")
    assert "FALLBACK CONSTANT" in note, note
    assert die == "568x568", (die, note)
