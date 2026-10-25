"""phase1_port_extract — deterministic PORT/PARAM/RESET extraction for Phase-1 NL.

Field measurement (CVDP 12-prompt AI-vs-program audit): the phase1 NL ingester
captured only ~17% of an AI's ports because it (a) only recognised inline Verilog
declarations, (b) `_parse_md_table_ports` did not recognise the common `In/Out`
direction header or `Length` width header, and (c) returned only the single
largest interface table when a spec splits clock/reset, input and output into
SEPARATE tables. This validates the three structural fixes — and asserts the
gates' single-best contract is preserved (union is opt-in).
"""
import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PLUGIN / "programs"))
import _specrtl_common as S   # noqa: E402
import phase1_port_extract as PX  # noqa: E402

# A spec that splits its interface across THREE tables with `In/Out` + `Length`
# headers and `in`/`out` values — the dominant CVDP form.
_MULTI_TABLE = """\
### Clock / Reset
| Name | In/Out | Length | Description |
|------|--------|--------|-------------|
| Clk  | in     | 1      | clock |
| Rst  | in     | 1      | async reset |

### Input Data
| Name     | In/Out | Length      | Description |
|----------|--------|-------------|-------------|
| In_Data  | in     | `WIDTH`     | data in |
| In_Valid | in     | 1           | valid |

### Output Data
| Name      | In/Out | Length | Description |
|-----------|--------|--------|-------------|
| Out_Data  | out    | 8      | data out |
| Out_Valid | out    | 1      | valid |
"""


def test_in_out_header_recognised():
    assert S._DIR_HDR.match("In/Out")
    assert S._DIR_HDR.match("I/O")          # back-compat
    assert S._DIR_HDR.match("Direction")


def test_length_width_header_recognised():
    assert S._WIDTH_HDR.match("Length")
    assert S._WIDTH_HDR.match("Len")
    assert S._WIDTH_HDR.match("Width")      # back-compat


def test_union_collects_every_interface_table():
    names = {p.name for p in S._parse_md_table_ports(_MULTI_TABLE, union=True)[0]}
    assert names == {"Clk", "Rst", "In_Data", "In_Valid", "Out_Data", "Out_Valid"}


def test_default_is_single_best_table_gate_contract_preserved():
    # §4.05: the conformance gates rely on the single-best contract — default
    # (union=False) must still return ONLY the largest qualifying table.
    best, _ = S._parse_md_table_ports(_MULTI_TABLE)   # union defaults to False
    # the largest table here has 2 ports; best must be one 2-port table, not all 6
    assert len(best) == 2


def test_phase1_extractor_unions_all_ports():
    ports = {p["name"] for p in PX.extract_ports(_MULTI_TABLE)}
    assert ports == {"Clk", "Rst", "In_Data", "In_Valid", "Out_Data", "Out_Valid"}
    # direction + width carried
    by = {p["name"]: p for p in PX.extract_ports(_MULTI_TABLE)}
    assert by["Out_Data"]["dir"] == "output" and by["Out_Data"]["width"] == 8


def test_param_table_and_decl_extracted():
    txt = ("| Name | Default | Description |\n|---|---|---|\n"
           "| WIDTH | 32 | data width |\n| DEPTH | 8 | fifo depth |\n"
           "Also `parameter N = 4;` inline.\n")
    params = {p["name"]: p["default"] for p in PX.extract_params(txt)}
    assert params.get("WIDTH") == "32" and params.get("DEPTH") == "8"
    assert params.get("N") == "4"


def test_reset_polarity_inferred():
    r = PX.extract("input rst_n; // active-low asynchronous reset")
    assert r["reset"]["name"].lower() == "rst_n"
    assert r["reset"]["polarity"] == "active_low"


def test_prose_does_not_inject_phantom_ports():
    """PRECISION: parse_verilog_ports is run ONLY inside real Verilog code regions
    (fenced blocks / module…endmodule), so a prose sentence that merely contains
    `input`/`output` words never scrapes a phantom port (the #27/#28 prose-scrape
    class). A pure-prose spec with no table/code yields ZERO ports — not garbage."""
    prose = ("The module takes input data and produces an output result. The "
             "input is latched and the output drives the bus. Inputs include a "
             "clock and a reset signal.")
    assert PX.extract_ports(prose) == []


def test_code_block_ports_extracted_but_its_comments_are_not():
    code = ("```verilog\nmodule m (\n"
            "  input  wire clk,        // the system clock\n"
            "  output reg  done\n);\nendmodule\n```\n")
    names = {p["name"] for p in PX.extract_ports(code)}
    assert names == {"clk", "done"}, names  # 'the' (comment) must NOT appear


def test_regmap_table_extracted_with_offset_access_width():
    """A register-map table (distinguished by an Offset/Address column, headers
    possibly **bold**) is parsed into {name, offset, access, width}."""
    txt = ("| **Register** | **Offset** | **Access** | **Bit Width** |\n"
           "|---|---|---|---|\n"
           "| Beat  | 0x100 | Read/Write | 20 |\n"
           "| ID    | 0x500 | Read-Only  | 32 |\n")
    regs = {r["name"]: r for r in PX.extract_regmap(txt)}
    assert set(regs) == {"Beat", "ID"}
    assert regs["Beat"]["offset"] == "0x100" and regs["Beat"]["width"] == 20
    assert regs["ID"]["access"] == "Read-Only"


def test_port_table_not_mistaken_for_regmap():
    """§4.05: a port table (direction column, NO offset column) must NOT be
    harvested as a register map."""
    txt = ("| Name | In/Out | Length |\n|---|---|---|\n"
           "| clk | in | 1 |\n| dout | out | 8 |\n")
    assert PX.extract_regmap(txt) == []


# ── structured-prose signal-definition list (no table / no code) ──────────────

_PROSE_SIGLIST = """\
- **Inputs**:
  - `clk`: Clock signal, positive edge.
  - `rst`: Active high synchronous reset signal.
  - `go`: Start signal. Active high.
  - `A [WIDTH-1:0]`: input value A.
- **Outputs**:
  - `done`: Signal indicating completion.
  - `OUT [WIDTH-1:0]`: the GCD result.
"""


def test_prose_signal_definition_list_extracted_with_direction():
    by = {p["name"]: p for p in PX.extract_prose_ports(_PROSE_SIGLIST)}
    assert set(by) == {"clk", "rst", "go", "A", "done", "OUT"}
    assert by["clk"]["dir"] == "input" and by["done"]["dir"] == "output"


def test_prose_reference_bullet_not_a_port():
    """PRECISION: a bullet that REFERENCES signals in prose (name NOT immediately
    followed by `:`) must not be harvested — only definition bullets are."""
    txt = ("- `item_button` and `cancel` are treated as toggle signals.\n"
           "- This prevents continuous-signal registration.\n")
    assert PX.extract_prose_ports(txt) == []


def test_prose_section_descriptor_label_not_a_port():
    """PRECISION: a TitleCase descriptor bullet ('- **Clock:** the `clk` signal …')
    is a section label, not the port (the real port is the backtick token)."""
    txt = ("- **Clock:** The `clk` signal is the rising edge of the clock.\n"
           "- **Reset:** Active-low asynchronous reset.\n")
    names = {p["name"] for p in PX.extract_prose_ports(txt)}
    assert "Clock" not in names and "Reset" not in names


def test_prose_fallback_only_when_no_table_or_code():
    """The prose list is a FALLBACK — a design with a real port table uses the
    table (higher confidence), not the prose path."""
    txt = ("| Name | In/Out | Length |\n|---|---|---|\n"
           "| clk | in | 1 |\n| dout | out | 8 |\n"
           "- `something`: described in prose elsewhere\n")
    names = {p["name"] for p in PX.extract_ports(txt)}
    assert names == {"clk", "dout"}


def test_enums_verilog_multi_decl_and_table():
    code = ("```\nlocalparam IDLE = 2'b00, RUN = 2'b01, DONE = 2'b10;\n"
            "parameter MODE_ADD = 8'h01;\n```\n")
    by = {e["name"]: e["value"] for e in PX.extract_enums(code)}
    assert by == {"IDLE": "2'b00", "RUN": "2'b01", "DONE": "2'b10",
                  "MODE_ADD": "8'h01"}
    tbl = "| State | Encoding |\n|---|---|\n| S_IDLE | 2'b00 |\n| S_GO | 2'b01 |\n"
    names = {e["name"] for e in PX.extract_enums(tbl)}
    assert names == {"S_IDLE", "S_GO"}


def test_enums_prose_no_false_positive():
    assert PX.extract_enums("The state word appears in prose; clk = the clock.") == []


# ── Step-2.7 §4.05: region gate must validate Verilog CONTENT, not just "in a
# fence / module-span" — else a non-Verilog fence or a prose module…endmodule
# span scrapes phantom ports into an otherwise-empty L-doc. ───────────────────

def test_no_phantom_from_non_verilog_fence():
    """A bare/pseudo-code fence (logs, pseudo-code) must not be scraped: a line
    `input message byte stream` is prose, not a Verilog port decl."""
    crc = ("# CRC8 Generator\n```\n"
           "input message byte stream\noutput crc value after last byte\n```\n")
    assert PX.extract_ports(crc) == []


def test_no_phantom_from_prose_module_span():
    """The bare words module…endmodule in spec prose must not form a parseable
    Verilog region."""
    prose = ("Each module accepts an input signal stream and produces an output "
             "result. You must declare every endmodule explicitly.")
    assert PX.extract_ports(prose) == []


def test_no_phantom_from_fenced_python():
    """A python fence using input()/`output =` must yield no ports."""
    py = "```python\nx = input('go: ')\noutput = compute(x)\nfor input in items:\n    pass\n```"
    assert PX.extract_ports(py) == []


def test_genuine_verilog_regions_still_extracted():
    """Regression: real Verilog (module header, bare `,`-terminated port snippet,
    `;`-terminated decls) must STILL be extracted after the content gate."""
    mod = "```verilog\nmodule m(input clk, input [7:0] a, output reg q); endmodule\n```"
    assert {p["name"] for p in PX.extract_ports(mod)} == {"clk", "a", "q"}
    snip = "```\ninput clk,\noutput q\n```"
    assert {p["name"] for p in PX.extract_ports(snip)} >= {"clk", "q"}


def _selected_header_prompt(kind, fenced=False):
    """Neutral context declarations differ from the requested public header."""
    if kind == "width":
        example = ("module legacy_stage(input CLK, input [11:0] DataIn, "
                   "output DataOut);\nendmodule")
        target = ("module PacketStage (\n  input CLK,\n"
                  "  input [11:0] DataIn,\n  output [11:0] DataOut\n);")
    else:
        example = ("module parent_stage(input [2:0] ParentBus, "
                   "output [2:0] ParentResult);\nendmodule")
        target = ("module PacketStage (\n  input CLK,\n"
                  "  input DataIn,\n  output DataOut\n);")
    if fenced:
        example = "```verilog\n" + example + "\n```"
        target = "```verilog\n" + target + "\n```"
    return ("Implement a module named PacketStage with the following interface.\n"
            "An earlier module is shown only as context:\n" + example +
            "\nThe requested module header is:\n" + target + "\n")


@pytest.mark.parametrize("kind", ["width", "parent"])
@pytest.mark.parametrize("fenced", [False, True])
def test_selected_header_excludes_context_and_preserves_declared_case(kind, fenced):
    text = _selected_header_prompt(kind, fenced)
    rows = PX.extract_code_block_ports(text, target="PacketStage")
    width = 12 if kind == "width" else 1
    assert [(p["name"], p["dir"], p["width"]) for p in rows] == [
        ("CLK", "input", 1), ("DataIn", "input", width),
        ("DataOut", "output", width)]
    assert all(p["rtl_name"] == p["name"] for p in rows)
    assert all(p["source_line"] in text for p in rows)


def test_selected_header_uses_only_its_own_parameter_scope():
    text = ("module example #(parameter WORD_BITS=2) "
            "(output [WORD_BITS-1:0] Result); endmodule\n"
            "module Requested #(parameter WORD_BITS=13) "
            "(input [WORD_BITS-1:0] Value, output [WORD_BITS-1:0] Result);\n")
    got = PX.extract_ports(text, target="Requested")
    assert got == [{"name": "Value", "dir": "input", "width": 13},
                   {"name": "Result", "dir": "output", "width": 13}]
    assert PX.extract(text, target="Requested")["ports"] == got


def test_selected_target_does_not_fall_back_to_an_unrelated_code_module():
    text = "module context_only(input Other); endmodule\n"
    assert PX.extract_code_block_ports(text, target="Absent") == []
    assert PX.extract_ports(text, target="Absent") == []


def test_selected_target_preserves_prose_only_interface_support():
    text = ("Implement a module named prose_stage.\n"
            "- input CLK\n- output Result (6 bits)\n")
    assert PX.extract_ports(text, target="prose_stage") == [
        {"name": "CLK", "dir": "input", "width": 1},
        {"name": "Result", "dir": "output", "width": 6}]


def test_selected_target_preserves_headerless_strict_code_fragments():
    text = ("Module Name: fragment_core\n"
            "// module commented_example(input Ghost); endmodule\n"
            "```verilog\ninput CLK,\noutput [5:0] Result;\n```\n")
    assert PX.selected_module_header(text, "fragment_core") is None
    assert PX.selected_module_code(text, "fragment_core") == text
    assert PX.extract_ports(text, target="fragment_core") == [
        {"name": "CLK", "dir": "input", "width": 1},
        {"name": "Result", "dir": "output", "width": 6}]


def test_selected_header_ignores_commented_decoy_and_unstated_width_claim():
    text = ("// module Selected(input Ghost, output [29:0] Result); endmodule\n"
            "A 30-bit internal datapath is not a port-width declaration.\n"
            "module Selected(input CLK, output Result);\n")
    rows = PX.extract_code_block_ports(text, target="Selected")
    assert [(p["name"], p["width"]) for p in rows] == [("CLK", 1), ("Result", 1)]


def test_selected_header_reads_existing_neutral_rtl_artifact():
    from _hostpaths import require_repo

    rtl = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
                       "tests", "fixtures", "iface_table_roles", "neutral_reg.v")
    text = ("module unrelated(input Ghost); endmodule\n" + rtl.read_text())
    rows = PX.extract_code_block_ports(text, target="neutral_reg")
    assert [(p["name"], p["dir"], p["width"]) for p in rows] == [
        ("clk", "input", 1), ("reset_n", "input", 1),
        ("cs", "input", 1), ("we", "input", 1),
        ("address", "input", 8), ("write_data", "input", 32),
        ("read_data", "output", 32), ("error", "output", 1)]


@pytest.mark.parametrize("kind", ["width", "parent"])
@pytest.mark.parametrize("fenced", [False, True])
def test_selected_header_reaches_actual_l1_and_l9_consumers(tmp_path, kind, fenced):
    import phase1_doc_one_shot_runner as R

    text = _selected_header_prompt(kind, fenced)
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "design.md").write_text(text)
    (tmp_path / "input" / "phase1_prompt.md").write_text(text)
    R.gen_l1_datasheet(tmp_path, {"design.md": text})
    R.gen_l9_integration_spec(tmp_path, {"design.md": text}, {})
    R._post_emit_crosswalk_l9_ports_to_l1_pin_table_v1_6_555(tmp_path)
    generated = tmp_path / "phase1" / "generated_docs"
    l1 = json.loads((generated / "L1_DATASHEET.json").read_text())
    l9 = json.loads((generated / "L9_INTEGRATION_SPEC.json").read_text())
    width = 12 if kind == "width" else 1
    expected = {"CLK": ("input", 1), "DataIn": ("input", width),
                "DataOut": ("output", width)}
    assert {p.get("rtl_name") or p["name"]: (
        p.get("direction") or p.get("mode"), p.get("width"))
        for p in l1["pin_table"]} == expected
    for key in ("ports", "top_ports", "top_module_pins"):
        # The ordinary JSON contract consumer dereferences name itself.
        # A source-cased annotation must not mask a lowercased public port.
        assert {p["name"]: (
            p.get("direction") or p.get("mode"), p.get("width"))
            for p in l9[key]} == expected
