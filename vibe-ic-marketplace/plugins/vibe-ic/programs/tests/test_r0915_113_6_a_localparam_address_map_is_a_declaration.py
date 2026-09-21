#!/usr/bin/env python3
"""R-0915-113(6) — Verilog-2005 declares its register map too.

THE MEASUREMENT (sha256 x sky130A, FRONT DOOR, run24 on main 8337cc81f):

    final_audit Overall: NOT_MEASURED (strict=True)
      <- Step P0 NOT_MEASURED (partial_population)
      <- l4_regmap_declared_register_coverage_check reason_class=EXECUTION_ERROR
         "This gate's declared side reads ADDRESS-VALUED HDL ENUMS ONLY and no
          staged input declares one"

`l4_regmap_declared_register_coverage_check.py` mentions `localparam` zero
times; its declared side is `_hdl_enum` alone. The design's own addresses are
declared as

    localparam ADDR_NAME0    = 8'h00;
    ...                        (9 bindings, 0 enums)

and its register-map document calls `localparam ADDR_*` its documented API.
Verilog-2005 has no `typedef enum`, so an entire language generation declares
its map in a form this gate could not read — and NOT_MEASURED at P0 makes
`final_audit` NOT_MEASURED under --strict-structural.

END-TO-END, on a project staging that RTL as a reused-IP input:

    BASE (main 3e0ec4d27)   rc=2  NOT_MEASURED
    HEAD                    rc=0  PASS: the input declares 9 register address
                                  binding(s) and L4 carries all 9
    HEAD, 2 dropped from L4 rc=1  FAIL naming ADDR_CTRL = 0x8 and
                                  ADDR_DIGEST7 = 0x27 and their provenance

THE RULE IS NOT WIDENED, ONLY THE FORM IT CAN READ. Every block goes through
`route_enum` -> `address_map_verdict` unchanged, with the same four
thresholds, and carries `enum_role=None`, so the name-vocabulary tier is
UNREACHABLE for it — stricter than an enum gets. Grouping is by source
adjacency, the structural principle the coverage gate's documentary harvester
already uses for a table. Nothing reads a name, a prefix or a spelling.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import _hdl_enum as H                                        # noqa: E402
import l4_regmap_declared_register_coverage_check as GATE    # noqa: E402

#: The design's own declaration block, transcribed (the corpus is not on main).
ADDR_BLOCK = """\
module sha256 (input clk);
   localparam ADDR_NAME0    = 8'h00;
   localparam ADDR_NAME1    = 8'h01;
   localparam ADDR_VERSION  = 8'h02;
   localparam ADDR_CTRL     = 8'h08;
   localparam ADDR_STATUS   = 8'h09;
   localparam ADDR_BLOCK0   = 8'h10;
   localparam ADDR_BLOCK15  = 8'h1f;
   localparam ADDR_DIGEST0  = 8'h20;
   localparam ADDR_DIGEST7  = 8'h27;

   localparam CTRL_INIT_BIT = 0;
   localparam CTRL_NEXT_BIT = 1;
   localparam CTRL_MODE_BIT = 2;

   localparam CORE_NAME0 = 32'h73686132; // "sha2"
   localparam CORE_NAME1 = 32'h35362020;
   localparam CORE_VERSION = 32'h302e3830;

   localparam CTR_LOAD  = 7'd0;
   localparam CTR_FINAL = 7'd65;

   localparam MODE_SHA_224 = 1'b0;
   localparam MODE_SHA_256 = 1'b1;
endmodule
"""

DECLARED = [("ADDR_NAME0", 0x00), ("ADDR_NAME1", 0x01), ("ADDR_VERSION", 0x02),
            ("ADDR_CTRL", 0x08), ("ADDR_STATUS", 0x09), ("ADDR_BLOCK0", 0x10),
            ("ADDR_BLOCK15", 0x1f), ("ADDR_DIGEST0", 0x20),
            ("ADDR_DIGEST7", 0x27)]


# ── 1. the blocks a source declares ───────────────────────────────────────
def test_the_address_block_is_found_with_its_own_bindings():
    blocks = H.parse_localparam_address_blocks(ADDR_BLOCK)
    addr = [b for b in blocks if b["declared_width"] == 8]
    assert len(addr) == 1
    got = [(m["name"], m["value"]) for m in addr[0]["members"]]
    assert got == DECLARED


def test_every_non_map_block_is_refused_and_says_which_condition_failed():
    refusals = {}
    for b in H.parse_localparam_address_blocks(ADDR_BLOCK):
        ok, why = H.address_map_verdict(b)
        if not ok:
            refusals[b["declared_width"]] = why
    assert set(refusals) == {32, 7, 1}
    assert "< 8" in refusals[32]          # too few bindings
    assert "7 bit(s) wide" in refusals[7]   # too narrow a code space
    assert "1 bit(s) wide" in refusals[1]


def test_exactly_one_block_routes_to_the_register_map():
    maps = H.address_map_enums({"sha256.v": ADDR_BLOCK})
    assert len(maps) == 1
    assert maps[0]["routing"]["destination"] == H.DEST_L4_REGISTERS
    assert [(b["name"], b["value"]) for b in maps[0]["bindings"]] == DECLARED


def test_the_decision_can_only_be_made_by_the_member_sets_shape():
    """A block has no type name to route by, so the name-vocabulary tier is
    unreachable for it. That is STRICTER than an enum gets, not looser."""
    for b in H.parse_localparam_address_blocks(ADDR_BLOCK):
        assert b["enum_role"] is None
    maps = H.address_map_enums({"sha256.v": ADDR_BLOCK})
    assert maps[0]["routing"]["rule"] == H.RULE_ADDRESS_MAP_SHAPE


# ── 2. what breaks a block, and what does not ─────────────────────────────
def test_a_blank_line_does_not_break_a_block():
    src = ("localparam A = 8'h00;\n\n\nlocalparam B = 8'h01;\n"
           + "".join(f"localparam R{i} = 8'h{i + 2:02x};\n" for i in range(7)))
    blocks = H.parse_localparam_address_blocks(src)
    assert len(blocks) == 1 and len(blocks[0]["members"]) == 9


def test_a_comment_line_does_not_break_a_block():
    src = ("localparam A = 8'h00; // the base\n"
           "// a heading between the two halves\n"
           "localparam B = 8'h01;\n")
    blocks = H.parse_localparam_address_blocks(src)
    assert len(blocks) == 1 and len(blocks[0]["members"]) == 2


def test_any_other_non_blank_line_breaks_a_block():
    src = ("localparam A = 8'h00;\n"
           "reg [7:0] something;\n"
           "localparam B = 8'h01;\n")
    assert len(H.parse_localparam_address_blocks(src)) == 2


def test_a_width_change_breaks_a_block():
    src = "localparam A = 8'h00;\nlocalparam B = 16'h0001;\n"
    blocks = H.parse_localparam_address_blocks(src)
    assert [b["declared_width"] for b in blocks] == [8, 16]


def test_an_unsized_literal_is_not_a_code_binding_and_breaks_the_block():
    """A bare integer names no code space, and guessing one is the invention
    this refuses. It is also what keeps `CTRL_INIT_BIT = 0` out."""
    src = "localparam A = 8'h00;\nlocalparam BIT = 0;\nlocalparam B = 8'h01;\n"
    blocks = H.parse_localparam_address_blocks(src)
    assert len(blocks) == 2
    assert all(m["name"] != "BIT" for b in blocks for m in b["members"])


def test_an_x_or_z_literal_states_no_code():
    src = "localparam A = 8'hxx;\nlocalparam B = 8'h01;\n"
    blocks = H.parse_localparam_address_blocks(src)
    assert all(m["name"] != "A" for b in blocks for m in b["members"])


def test_a_parameter_is_not_a_localparam():
    src = "".join(f"parameter P{i} = 8'h{i:02x};\n" for i in range(9))
    assert H.parse_localparam_address_blocks(src) == []


def test_a_localparam_quoted_in_a_comment_is_not_a_declaration():
    src = "// localparam ADDR_X = 8'h00;\n/* localparam ADDR_Y = 8'h01; */\n"
    assert H.parse_localparam_address_blocks(src) == []


def test_a_source_with_no_localparam_costs_nothing_and_yields_nothing():
    assert H.parse_localparam_address_blocks("module m; endmodule") == []
    assert H.parse_localparam_address_blocks("") == []
    assert H.parse_localparam_address_blocks(None) == []


# ── 3. the thresholds are the enum's own, unchanged ───────────────────────
def test_a_block_too_small_is_refused_at_the_same_floor():
    src = "".join(f"localparam A{i} = 8'h{i:02x};\n"
                  for i in range(H.MIN_ADDRESS_BINDINGS - 1))
    blk = H.parse_localparam_address_blocks(src)[0]
    ok, why = H.address_map_verdict(blk)
    assert not ok and f"< {H.MIN_ADDRESS_BINDINGS}" in why
    src += f"localparam A{H.MIN_ADDRESS_BINDINGS} = 8'hfe;\n"
    assert H.address_map_verdict(
        H.parse_localparam_address_blocks(src)[0])[0] is True


def test_a_block_that_fills_its_space_is_an_encoding_not_a_map():
    src = "".join(f"localparam E{i} = 8'h{i:02x};\n" for i in range(64))
    ok, why = H.address_map_verdict(H.parse_localparam_address_blocks(src)[0])
    assert not ok and "being enumerated, not addressed" in why


def test_a_narrow_code_space_is_refused_at_the_same_floor():
    src = "".join(f"localparam S{i} = 4'h{i:x};\n" for i in range(9))
    ok, why = H.address_map_verdict(H.parse_localparam_address_blocks(src)[0])
    assert not ok and "not an address space" in why


def test_two_bindings_on_one_code_refuse_as_a_misparse():
    src = "".join(f"localparam A{i} = 8'h{i:02x};\n" for i in range(9))
    src += "localparam DUP = 8'h00;\n"
    ok, why = H.address_map_verdict(H.parse_localparam_address_blocks(src)[0])
    assert not ok and "same code" in why


def test_an_enum_still_routes_exactly_as_before():
    """The second form must not disturb the first."""
    src = ("typedef enum logic [11:0] {\n"
           + ",\n".join(f"  CSR_{i} = 12'h{i * 3:03x}" for i in range(12))
           + "\n} csr_e;\n")
    maps = H.address_map_enums({"pkg.sv": src})
    assert len(maps) == 1 and maps[0]["type_name"] == "csr_e"
    assert maps[0]["routing"]["destination"] == H.DEST_L4_REGISTERS


def test_both_forms_in_one_tree_are_both_harvested():
    enum_src = ("typedef enum logic [11:0] {\n"
                + ",\n".join(f"  CSR_{i} = 12'h{i * 3:03x}" for i in range(12))
                + "\n} csr_e;\n")
    maps = H.address_map_enums({"pkg.sv": enum_src, "sha256.v": ADDR_BLOCK})
    assert len(maps) == 2
    assert {m["declaration_form"] if "declaration_form" in m else "enum"
            for m in maps} == {"enum", H.LOCALPARAM_BLOCK_FORM}


# ── 4. the gate, end to end, in both directions ───────────────────────────
def _project(tmp_path: Path, l4_names) -> Path:
    p = tmp_path / "proj"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "sha256.v").write_text(ADDR_BLOCK)
    (p / "phase1" / "generated_docs" / "L4_REGMAP.json").write_text(
        json.dumps({"schema_version": 2, "doc_class": "regmap",
                    "ic_name": "sha256",
                    "registers": [{"name": n, "address": f"0x{a:02x}",
                                   "access": "R/W", "fields": []}
                                  for n, a in l4_names]}))
    return p


def test_the_gate_measures_a_localparam_declared_map(tmp_path):
    p = _project(tmp_path, DECLARED)
    assert GATE.main([str(p)]) == 0


def test_the_gate_blocks_when_l4_drops_a_declared_binding(tmp_path):
    p = _project(tmp_path, [r for r in DECLARED
                            if r[0] not in ("ADDR_CTRL", "ADDR_DIGEST7")])
    assert GATE.main([str(p)]) == 1


def test_the_blocking_verdict_names_every_missing_binding(tmp_path, capsys):
    p = _project(tmp_path, [r for r in DECLARED if r[0] != "ADDR_CTRL"])
    assert GATE.main([str(p)]) == 1
    out = capsys.readouterr().out
    assert "ADDR_CTRL" in out and "0x8" in out
    assert H.LOCALPARAM_BLOCK_FORM in out        # provenance, not just a name


def test_a_project_with_no_declaration_form_is_untouched(tmp_path):
    """Fail-closed: the second form adds no verdict where there is no map."""
    p = _project(tmp_path, DECLARED)
    (p / "input" / "docs" / "sha256.v").write_text(
        "module m; localparam BIT = 0; endmodule")
    assert GATE.main([str(p)]) == 2


def test_the_gate_reads_the_declared_bindings_through_the_shared_door():
    """A helper with tests is not a fix until the gate calls it."""
    tree = ast.parse((PROG / "_hdl_enum.py").read_text())
    fn = next(f for f in ast.walk(tree)
              if isinstance(f, ast.FunctionDef) and f.name == "address_map_enums")
    called = {ast.unparse(n.func) for n in ast.walk(fn)
              if isinstance(n, ast.Call)}
    assert "harvest_declared_address_maps" in called


# ── 5. the deck's own hygiene ─────────────────────────────────────────────
def test_no_two_tests_in_this_file_share_a_name():
    tree = ast.parse(Path(__file__).read_text())
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(names) == len(set(names)), \
        sorted({n for n in names if names.count(n) > 1})
