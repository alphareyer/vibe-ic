"""vibe-ic#2172 — `clock_tree_synthesis -buf_list` gets the WHOLE buffer
family the library ships, not one pinned master.

MEASURED (subservient x gf180mcuD, lane cz2160, image 0.3.48
``sha256:1463dac58116…``): the clock tree's insertion delay at the slow corner
was 6.53 ns against a DECLARED 20 ns period, and 4.56 ns of it was the tree
itself -- root ``clkbuf_16`` then four levels of ``clkbuf_4``, because the deck
handed CTS ``-buf_list {clkbuf_4}``: ONE master for every level. The same
library ships that family in eight drive strengths (1/2/3/4/8/12/16/20).

What these pin, all without OpenROAD and without a PDK on the host:

  1. the family is DERIVED from the Liberty's own pin models, using the two
     tests #1958 established (a buffer is one input + one output whose function
     IS that input; the family key is the name minus its trailing drive
     integer). No cell, vendor or PDK name is matched anywhere.
  2. the choice MOVES when the library moves -- the acceptance control the
     issue asks for. Take drive strengths out of the library and the emitted
     list shrinks to exactly what is left.
  3. it REFUSES rather than guesses. A leaf master that is not a structural
     buffer of the Liberty being read yields no list at all, and the caller
     keeps the single master it already had.
  4. a library shipping ONE drive strength produces the pre-fix deck, so the
     change cannot perturb a PDK it has nothing to offer.
  5. the emitted `pnr.tcl` carries every derived master inside `-buf_list {}`,
     and carries exactly the single master when nothing was derived.
"""
import importlib
import re
from pathlib import Path

R = importlib.import_module("phase3_one_shot_runner")

#: A minimal Liberty with a real pin model. `_p` is the family prefix; the
#: names are deliberately meaningless -- the derivation must never read them.
_CELL = """
  cell({name}) {{
    area : {area};
    pin(A) {{
      direction : input;
    }}
    pin(Z) {{
      direction : output;
      function : "A";
    }}
  }}
"""
#: an INVERTER and a 2-input gate: neither is a buffer, and both live in the
#: same name family, so anything that grouped by NAME would swallow them.
_NOT_BUFFERS = """
  cell(fam_inv_4) {
    pin(A) {
      direction : input;
    }
    pin(Z) {
      direction : output;
      function : "!A";
    }
  }
  cell(fam_2) {
    pin(A) {
      direction : input;
    }
    pin(B) {
      direction : input;
    }
    pin(Z) {
      direction : output;
      function : "A B";
    }
  }
"""


def _liberty(drives, prefix="fam_xbuf_"):
    body = "".join(_CELL.format(name=f"{prefix}{d}", area=float(d))
                   for d in drives)
    return "library(t) {\n" + body + _NOT_BUFFERS + "\n}\n"


def test_family_is_every_drive_strength_the_library_ships():
    names, how = R._i2172_cts_buf_family(
        _liberty([1, 2, 4, 8, 16]), "fam_xbuf_4", "fam_xbuf_16")
    assert names == ["fam_xbuf_1", "fam_xbuf_2", "fam_xbuf_4",
                     "fam_xbuf_8", "fam_xbuf_16"], names
    # the inverter and the gate share the name space and MUST NOT appear.
    assert "fam_inv_4" not in names and "fam_2" not in names
    assert "5 drive strength" in how and "1/2/4/8/16" in how


def test_the_choice_moves_with_the_library():
    """The acceptance control: mutate the list of masters the library ships
    and the derived list must move with it -- proving the selection is read
    from the library and not written into the flow."""
    wide = R._i2172_cts_buf_family(_liberty([1, 2, 4, 8, 16]), "fam_xbuf_4")[0]
    narrow = R._i2172_cts_buf_family(_liberty([1, 4]), "fam_xbuf_4")[0]
    assert wide == ["fam_xbuf_1", "fam_xbuf_2", "fam_xbuf_4",
                    "fam_xbuf_8", "fam_xbuf_16"]
    assert narrow == ["fam_xbuf_1", "fam_xbuf_4"]
    assert set(narrow) < set(wide)
    # and the family PREFIX is not a literal either
    other = R._i2172_cts_buf_family(
        _liberty([1, 4, 8], prefix="zz_q_"), "zz_q_4")[0]
    assert other == ["zz_q_1", "zz_q_4", "zz_q_8"]


def test_it_refuses_rather_than_guesses():
    """No list at all when the leaf master is not a structural buffer of THIS
    Liberty -- an unreadable library, a stub with no pin model, or a registry
    cell the active library does not contain. The caller then keeps the single
    master, which is the honest pre-fix behaviour, not a guessed family."""
    lib = _liberty([1, 2, 4])
    assert R._i2172_cts_buf_family(lib, "not_in_this_library_4") == ([], "")
    assert R._i2172_cts_buf_family(lib, "fam_inv_4") == ([], "")
    assert R._i2172_cts_buf_family("", "fam_xbuf_4") == ([], "")
    assert R._i2172_cts_buf_family(lib, None) == ([], "")


def test_single_drive_library_keeps_the_pre_fix_deck():
    assert R._i2172_cts_buf_family(_liberty([4]), "fam_xbuf_4") == ([], "")


# ---------------------------------------------------------------- emission --
def _pnr_tcl(tmp_path, cts_buf_list):
    pdk = R.PdkConfig(
        name="fixture_pdk",
        liberty="/pdk/lib.lib", tech_lef="/pdk/tech.lef",
        cell_lef="/pdk/cells.lef", cell_gds=None,
        site="unithd", drc_deck=None, metal_prefix="met")
    out_dir_c = str(tmp_path / "out")
    (tmp_path / "out").mkdir(exist_ok=True)
    plan = R._build_spare_cells_plan(
        2000, 0.02, (10, 10, 290, 290), liberty_path="", container="")
    return R._build_pnr_tcl_text(
        tech_lef_c="/pdk/tech.lef", cell_lef_c="/pdk/cells.lef",
        macro_lefs_tcl="", liberty_c="/pdk/lib.lib",
        macro_libs_tcl="", netlist_c="/work/netlist.v", top="chip_top",
        sdc_c="/work/chip_top.sdc",
        dont_use_block=R._dont_use_tcl(pdk),
        metal_prefix=pdk.metal_prefix, die_w=300, die_h=300,
        core_pad=10, core_w=280, core_h=280, site=pdk.site,
        out_dir_c=out_dir_c,
        tapcell_block=R._build_tapcell_tcl(pdk),
        pdn_block=R._build_pdn_tcl(pdk), util=0.45,
        spare_protection_tcl=R._build_spare_protection_tcl(plan, out_dir_c),
        spare_postfix_tcl=R._build_spare_postfix_tcl(
            plan, tie_lo_cell="sky130_fd_sc_hd__conb_1", tie_lo_pin="LO"),
        clk_buf="sky130_fd_sc_hd__clkbuf_4",
        clk_buf_root="sky130_fd_sc_hd__clkbuf_16",
        cts_buf_list=cts_buf_list,
        routing_constraint_tcl="",
        pg_cleanup_block=R._pg_net_cleanup_tcl(),
        spef_repair_block="",
        antenna_repair_block=R._antenna_repair_tcl(pdk),
        filler_block="")


def _emitted_buf_list(tcl):
    m = re.search(r"clock_tree_synthesis -buf_list \{([^}]*)\}", tcl)
    assert m, "no clock_tree_synthesis -buf_list in the emitted deck"
    return m.group(1).split()


def test_emitted_deck_carries_every_derived_master(tmp_path):
    derived = ["sky130_fd_sc_hd__clkbuf_%d" % d for d in (1, 2, 4, 8, 16)]
    assert _emitted_buf_list(_pnr_tcl(tmp_path, derived)) == derived


def test_emitted_deck_is_the_single_master_when_nothing_was_derived(tmp_path):
    """`[]` and `None` both mean "keep the pre-fix deck", and the deck they
    produce is byte-identical to each other."""
    a = _pnr_tcl(tmp_path, None)
    b = _pnr_tcl(tmp_path, [])
    assert _emitted_buf_list(a) == ["sky130_fd_sc_hd__clkbuf_4"]
    assert a == b


def test_no_cell_or_pdk_literal_in_the_derivation():
    """The derivation is chip-AGNOSTIC: its source names no cell, vendor,
    library or PDK. Read the function's own source, not a summary of it."""
    src = Path(R.__file__).read_text()
    body = src[src.index("def _i2172_cts_buf_family("):]
    body = body[:body.index("\ndef ", 1)]
    low = body.lower()
    for literal in ("sky130", "gf180", "sg13g2", "nangate", "asap7",
                    "clkbuf", "bufx", "__clk"):
        assert literal not in low, f"{literal!r} is a literal in the derivation"
