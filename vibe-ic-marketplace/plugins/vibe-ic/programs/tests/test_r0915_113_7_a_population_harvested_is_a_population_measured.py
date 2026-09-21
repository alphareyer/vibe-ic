#!/usr/bin/env python3
"""R-0915-113(7) — a gate that harvests a population must MEASURE it.

THE DEFECT, in the gate's own words, on sha256 x sky130A, FRONT DOOR, run24 at
main 8337cc81f:

    NOT_MEASURED ... documentation staged under input/ DOES declare a register
    map: 7 table row(s) in input/docs/L5_register_map.md naming 29 register(s).
    The rule was not applied to them, so this gate states no coverage over
    that population. L4_REGMAP.registers[] does carry all 29 of those name(s)
    — but by NAME only, which is not the rule this gate applies.

A gate that harvests a population and then states no coverage over it measures
nothing about it, and NOT_MEASURED at P0 makes `final_audit` NOT_MEASURED
under --strict-structural whatever the design does. The repair is to APPLY the
rule, not to narrow the population.

THE RULE IS ADDRESS-BOUND IDENTITY, never name-only presence: covered means L4
carries the register under the declared NAME, at the declared ADDRESS, and —
when both sides state one — at the declared WIDTH.

MEASURED, run24's own L5 table against run24's own L4, all four directions:

    untouched                      rc=0  PASS, 29 of 29 covered
    drop CTRL from L4              rc=1  [ABSENT_FROM_L4] CTRL is declared at
                                         0x8 and L4 carries no such name
    strip one document address     rc=2  [NOT_COVERED_UNADDRESSED] VERSION,
                                         the other 28 still measured
    STATUS moved to 0x33 in L4     rc=1  [ADDRESS_MISMATCH] declared at 0x9,
                                         carried at 0x33
    NAME0 width 32 -> 16 in L4     rc=1  [WIDTH_MISMATCH]

and run24 itself moves BASE rc=2 NOT_MEASURED -> HEAD rc=0 PASS.

A SECOND DROP WAS FOUND ON THE WAY. `_column_roles` required EVERY cell of a
column to parse as an address before it would call it the address column, so
ONE unaddressed row disqualified the column and the gate reported "no
documentation this gate opened declares a register row" — one blank cell
dropped all 29 registers. Two passes now, STRICT FIRST, so no table that
resolves today resolves differently; the tolerant pass keeps distinctness and
demands a strong majority.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import l4_regmap_declared_register_coverage_check as G      # noqa: E402

#: run24's own L5 summary table, transcribed (the corpus is not on main).
L5 = """\
## Register Definitions

| 位址(hex) | 名稱 | R/W | 寬度 | 描述 |
|---|---|---|---|---|
| `0x00` | `NAME0` | R | 32 | Chip identifier word 0 |
| `0x01` | `NAME1` | R | 32 | Chip identifier word 1 |
| `0x02` | `VERSION` | R | 32 | Version string |
| `0x08` | `CTRL` | R/W | 32 | 控制 register |
| `0x09` | `STATUS` | R | 32 | 狀態 register |
| `0x10-0x1F` | `BLOCK0` ~ `BLOCK15` | W | 32 each | 512-bit message block |
| `0x20-0x27` | `DIGEST0` ~ `DIGEST7` | R | 32 each | 256-bit digest |
"""

NAMED = ([("NAME0", 0x00), ("NAME1", 0x01), ("VERSION", 0x02),
          ("CTRL", 0x08), ("STATUS", 0x09)]
         + [(f"BLOCK{i}", 0x10 + i) for i in range(16)]
         + [(f"DIGEST{i}", 0x20 + i) for i in range(8)])


def _project(tmp_path: Path, l5: str = L5, regs=None, width=32) -> Path:
    p = tmp_path / "proj"
    (p / "input" / "docs").mkdir(parents=True)
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "input" / "docs" / "L5_register_map.md").write_text(l5)
    rows = NAMED if regs is None else regs
    (p / "phase1" / "generated_docs" / "L4_REGMAP.json").write_text(
        json.dumps({"schema_version": 2, "doc_class": "regmap",
                    "ic_name": "sha256",
                    "registers": [{"name": n, "address": f"0x{a:02x}",
                                   "width_bits": width, "fields": []}
                                  for n, a in rows]}))
    return p


def _verdict(p: Path):
    rc, summary = G.evaluate(p)
    return rc, summary, "\n".join(G.render(summary))


# ── 1. the four directions the rule must answer ───────────────────────────
def test_every_documented_register_covered_is_a_pass(tmp_path):
    rc, summary, text = _verdict(_project(tmp_path))
    assert rc == 0 and summary["verdict"] == "PASS"
    assert summary["documentary_registers_measured"] == 29
    assert summary["documentary_registers_covered"] == 29
    assert "29 of 29 register(s) covered" in text


def test_a_declared_register_absent_from_l4_blocks_and_names_it(tmp_path):
    p = _project(tmp_path, regs=[r for r in NAMED if r[0] != "CTRL"])
    rc, summary, text = _verdict(p)
    assert rc == 1 and summary["verdict"] == "FAIL"
    assert G.ABSENT_FROM_L4 in text and "CTRL" in text and "0x8" in text


def test_a_name_only_match_at_a_wrong_address_is_a_fail(tmp_path):
    """The case a name-only rule reported as covered. BOTH numbers print."""
    p = _project(tmp_path, regs=[(n, 0x33 if n == "STATUS" else a)
                                 for n, a in NAMED])
    rc, summary, text = _verdict(p)
    assert rc == 1 and summary["verdict"] == "FAIL"
    assert G.ADDRESS_MISMATCH in text
    assert "0x9" in text and "0x33" in text


def test_a_document_row_with_no_address_is_unaddressed_by_name(tmp_path):
    l5 = L5.replace("| `0x02` | `VERSION`", "| (unassigned) | `VERSION`")
    rc, summary, text = _verdict(_project(tmp_path, l5=l5))
    assert rc == 2 and summary["verdict"] == "NOT_MEASURED"
    assert G.NOT_COVERED_UNADDRESSED in text and "VERSION" in text
    # never assumed covered, never dropped, and the REST is still measured
    outcomes = summary["documentary_outcome_counts"]
    assert outcomes[G.NOT_COVERED_UNADDRESSED] == 1
    assert outcomes[G.COVERED] == 28
    assert summary["documentary_registers_measured"] == 29


def test_a_width_the_document_states_and_l4_contradicts_is_a_fail(tmp_path):
    p = _project(tmp_path)
    f = p / "phase1" / "generated_docs" / "L4_REGMAP.json"
    doc = json.loads(f.read_text())
    for r in doc["registers"]:
        if r["name"] == "NAME0":
            r["width_bits"] = 16
    f.write_text(json.dumps(doc))
    rc, _summary, text = _verdict(p)
    assert rc == 1 and G.WIDTH_MISMATCH in text
    assert "32 bit(s) wide" in text and "16" in text


def test_a_width_only_one_side_states_does_not_decide(tmp_path):
    """Fail-closed in the other direction: an unstated width invents nothing."""
    p = _project(tmp_path)
    f = p / "phase1" / "generated_docs" / "L4_REGMAP.json"
    doc = json.loads(f.read_text())
    for r in doc["registers"]:
        r.pop("width_bits", None)
    f.write_text(json.dumps(doc))
    rc, summary, _text = _verdict(p)
    assert rc == 0 and summary["documentary_registers_covered"] == 29


# ── 2. the sentence that must become impossible ───────────────────────────
@pytest.mark.parametrize("mutate", [
    pytest.param(lambda p: None, id="covered"),
    pytest.param(lambda p: (p / "phase1" / "generated_docs"
                            / "L4_REGMAP.json").write_text(
        json.dumps({"registers": [{"name": "NAME0", "address": "0x00"}]})),
        id="mostly-absent"),
])
def test_no_verdict_can_say_it_states_no_coverage(tmp_path, mutate):
    """Asserted over the EMITTED verdict, never over the file's own text: the
    post-mortem in the source quotes the defect, and a guard that reads its
    own citation reddens on the fix."""
    p = _project(tmp_path)
    mutate(p)
    _rc, _summary, text = _verdict(p)
    assert "states no coverage" not in text
    assert "the rule was not applied" not in text.lower()


def test_the_verdict_names_both_populations_and_the_rule(tmp_path):
    _rc, _summary, text = _verdict(_project(tmp_path))
    assert "HDL population is NOT_MEASURED" in text
    assert "that population WAS measured" in text
    assert "ADDRESS-BOUND IDENTITY" in text
    assert "declared width" in text


def test_the_record_carries_the_rule_and_the_per_register_outcomes(tmp_path):
    _rc, summary, _text = _verdict(_project(tmp_path))
    assert "address-bound identity" in summary["documentary_coverage_rule"]
    rows = summary["documentary_coverage"]
    assert {r["outcome"] for r in rows} == {G.COVERED}
    assert {r["name"] for r in rows} >= {"NAME0", "BLOCK15", "DIGEST7"}
    den = summary["denominator"]
    assert den["examined"] == 29          # the rule RAN over this population


# ── 3. the range row's own arithmetic ─────────────────────────────────────
def test_a_range_row_binds_its_ith_name_to_lo_plus_i(tmp_path):
    _rc, summary, _text = _verdict(_project(tmp_path))
    by_name = {r["name"]: r for r in summary["documentary_coverage"]}
    assert by_name["BLOCK0"]["address"] == 0x10
    assert by_name["BLOCK15"]["address"] == 0x1f
    assert by_name["DIGEST7"]["address"] == 0x27


def test_a_range_whose_span_and_count_disagree_states_no_address():
    rows = [{"names": [f"R{i}" for i in range(4)], "addr_lo": 0x10,
             "addr_hi": 0x1f, "width_bits": 32, "source_file": "d.md",
             "line": ""}]
    got = G.documentary_bindings(rows)
    assert len(got) == 4
    assert all(b["address"] is None for b in got)
    assert "4 name(s)" in got[0]["address_basis"]


def test_one_address_for_several_names_states_no_address():
    rows = [{"names": ["A", "B"], "addr_lo": 0x10, "addr_hi": None,
             "width_bits": None, "source_file": "d.md", "line": ""}]
    got = G.documentary_bindings(rows)
    assert all(b["address"] is None for b in got)
    assert "which name it binds is not stated" in got[0]["address_basis"]


# ── 4. the column-role repair, in both directions ─────────────────────────
def test_a_table_that_resolves_today_resolves_the_same_way():
    """The strict pass runs first, so nothing that already worked changes."""
    rows = [["`0x00`", "`A`", "R", "32"], ["`0x01`", "`B`", "R", "32"]]
    assert G._column_roles(rows) == (0, 1)


def test_one_unparseable_address_cell_no_longer_drops_the_table():
    rows = [["`0x00`", "`A`", "R", "32"], ["(unassigned)", "`B`", "R", "32"],
            ["`0x02`", "`C`", "R", "32"], ["`0x03`", "`D`", "R", "32"],
            ["`0x04`", "`E`", "R", "32"]]
    assert G._column_roles(rows) == (0, 1)


def test_a_column_of_repeated_values_is_still_refused():
    """Distinctness survives the tolerant pass — a `Reset` column of 0x0
    cannot become an address column."""
    rows = [["`0x0`", "`A`", "R"], ["`0x0`", "`B`", "R"],
            ["`0x0`", "`C`", "R"]]
    assert G._column_roles(rows) is None


def test_a_column_that_is_mostly_prose_is_refused():
    rows = [["note one", "`A`", "R"], ["note two", "`B`", "R"],
            ["`0x02`", "`C`", "R"], ["note four", "`D`", "R"],
            ["note five", "`E`", "R"]]
    assert G._column_roles(rows) is None


# ── 5. the width column, measured per table ───────────────────────────────
def test_the_width_column_is_measured_not_assumed(tmp_path):
    _rc, summary, _text = _verdict(_project(tmp_path))
    by_name = {r["name"]: r for r in summary["documentary_coverage"]}
    assert by_name["NAME0"]["width_bits"] == 32
    assert by_name["BLOCK0"]["width_bits"] == 32     # "32 each"


def test_a_description_cell_is_never_a_width():
    assert G._parse_width_cell("512-bit message block input(16 word)") is None
    assert G._parse_width_cell("32") == 32
    assert G._parse_width_cell("32 each") == 32
    assert G._parse_width_cell("0") is None


def test_a_table_with_no_width_column_measures_addresses_alone(tmp_path):
    l5 = "\n".join(line.rsplit("|", 3)[0] + "|" if line.startswith("|") else line
                   for line in L5.splitlines())
    rc, summary, _text = _verdict(_project(tmp_path, l5=l5))
    assert rc == 0
    assert all(r["width_bits"] is None
               for r in summary["documentary_coverage"])


# ── 6. the deck's own hygiene ─────────────────────────────────────────────
def test_no_two_tests_in_this_file_share_a_name():
    tree = ast.parse(Path(__file__).read_text())
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(names) == len(set(names)), \
        sorted({n for n in names if names.count(n) > 1})
