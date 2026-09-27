"""FX_AES_L4_REGMAP: phase 1 judged a register map that was not yet final, and
named a multireg's element 0 by its family.

MEASURED on opentitan_aes x main 7fac744e1 (lane rvp2):
`l4_regmap_declared_register_coverage_check` FAILed "7 of 35 register(s)
covered". The layergate-2 loop ran right after `gen_l4_regmap`, 17 s before the
post-emit passes (register-table rows, the G19 offset backfill) grew L4 to its
final rows. And behind it: the register-table pass deduped `<FAMILY>_0` against
the collapsed family row at the same address, so L4 carried the family and
elements 1..n but no register by element 0's declared name.

The design input decides which side is wrong: its register summary names every
element `<FAMILY>_<i>`, element 0 included, at its own offset. So the emitter is
fixed (the dedupe recognises an element), and the gate order is fixed (the
layer gates run after every producer of their layers). No design name appears
here; the fixtures use a generic `FIFO` family.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
for _p in (str(PROGRAMS), str(PROGRAMS.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import phase1_doc_one_shot_runner as R  # noqa: E402

RUNNER = PROGRAMS / "phase1_doc_one_shot_runner.py"


def _family(name="FIFO", base=0x10, n=4, stride=4):
    return {"name": name, "offset": hex(base), "address": hex(base),
            "element_offsets": [{"index": i, "offset": hex(base + i * stride)}
                                for i in range(n)]}


# ── the element rule ──────────────────────────────────────────────────────

@pytest.mark.parametrize("row_name, addr, expect", [
    ("FIFO_0", "0x10", True),     # element 0 at the family's own address
    ("FIFO_2", "0x18", True),     # any element at its own listed offset
    ("FIFO_1", "0x10", False),    # an index the family places elsewhere
    ("OTHER", "0x10", False),     # a second name for the register: a duplicate
    ("FIFO_X", "0x10", False),    # not an index
    ("FIFOX_0", "0x10", False),   # not this family's name
])
def test_an_element_is_told_from_a_duplicate(row_name, addr, expect):
    assert R._multireg_element_of(_family(), {"name": row_name}, addr) is expect


def test_a_family_already_named_by_its_element_is_not_renamed_twice():
    fam = dict(_family(), name="FIFO_0", multireg_family="FIFO")
    assert R._multireg_element_of(fam, {"name": "FIFO_0"}, "0x10") is False


def test_a_row_without_element_offsets_has_no_elements():
    fam = {"name": "FIFO", "address": "0x10"}
    assert R._multireg_element_of(fam, {"name": "FIFO_0"}, "0x10") is False
    assert R._multireg_element_of(None, {"name": "FIFO_0"}, "0x10") is False


# ── the real register-table pass, on a generic multireg ───────────────────

_TABLE = """# Registers

| Name                      | Offset   |   Length | Description        |
|:--------------------------|:---------|---------:|:-------------------|
| blk.[`CTRL`](#ctrl)       | 0x0      |        4 | Control Register.  |
| blk.[`FIFO_0`](#fifo)     | 0x10     |        4 | FIFO word.         |
| blk.[`FIFO_1`](#fifo)     | 0x14     |        4 | FIFO word.         |
| blk.[`FIFO_2`](#fifo)     | 0x18     |        4 | FIFO word.         |
| blk.[`FIFO_3`](#fifo)     | 0x1c     |        4 | FIFO word.         |
| blk.[`ALIAS`](#alias)     | 0x0      |        4 | Control, aliased.  |
"""


def _project(tmp_path: Path) -> Path:
    import _path_layout as _pl
    p = tmp_path / "proj"
    gd = _pl.generated_docs_dir(p)
    gd.mkdir(parents=True)
    (gd / "L1_DATASHEET.json").write_text(json.dumps({"class_path": "x"}))
    (gd / "L4_REGMAP.json").write_text(json.dumps({"registers": [
        {"name": "CTRL", "offset": "0x0", "address": "0x0"}, _family()]}))
    ind = _pl.input_doc_dir(p)
    ind.mkdir(parents=True)
    (ind / "blk_registers.md").write_text(_TABLE)
    return p


def test_the_table_pass_names_element_zero_by_its_declared_name(tmp_path):
    import _path_layout as _pl
    p = _project(tmp_path)
    # the per-register table repeats element 0 (as register docs do)
    ind = _pl.input_doc_dir(p)
    (ind / "blk_fifo.md").write_text(
        "| Name   | Offset |\n|:-------|:-------|\n| FIFO_0 | 0x10   |\n"
        "| FIFO_1 | 0x14   |\n")
    R._post_emit_pdf_regmap_table_rows(p)
    regs = json.loads((_pl.generated_docs_dir(p) / "L4_REGMAP.json")
                      .read_text())["registers"]
    names = [r["name"] for r in regs]
    by = {r["name"]: r for r in regs}
    for i, addr in enumerate(("0x10", "0x14", "0x18", "0x1c")):
        assert names.count(f"FIFO_{i}") == 1, names
        r = by[f"FIFO_{i}"]
        assert int(str(r.get("address") or r.get("addr_hex")), 16) == int(addr, 16)
    # element 0 IS the family row: one record per address, family recorded
    assert "FIFO" not in by
    assert by["FIFO_0"]["multireg_family"] == "FIFO"
    assert len(by["FIFO_0"]["element_offsets"]) == 4
    addrs = [int(str(r.get("address") or r.get("addr_hex")), 16) for r in regs]
    assert len(addrs) == len(set(addrs)), addrs
    # a genuine second name at an occupied address is still a duplicate:
    # absorbed as an alias, not appended
    assert "ALIAS" not in by
    assert "ALIAS" in (by["CTRL"].get("also_named") or [])


def test_the_phase2_emitter_builds_the_named_map(tmp_path):
    """The phase-2 emitter emits one register per L4 row: after the pass the
    map has unique identifiers and addresses (the contract gate's rule)."""
    import _path_layout as _pl
    import phase2_scaffold_gen as G
    p = _project(tmp_path)
    R._post_emit_pdf_regmap_table_rows(p)
    l4 = json.loads((_pl.generated_docs_dir(p) / "L4_REGMAP.json").read_text())
    regs = G.derive_registers(l4, {})
    names = [r["name"] for r in regs]
    assert len(names) == len(set(names)), names
    assert {"FIFO_0", "FIFO_1", "FIFO_2", "FIFO_3", "CTRL"} <= set(names)


# ── the order: layer gates judge the final layers ─────────────────────────

_PRODUCER_PREFIXES = ("_post_emit_", "gen_l4_", "gen_l5_", "gen_l6_",
                      "_g19_post_emit_", "reconcile_register_map_claims")


def _main():
    tree = ast.parse(RUNNER.read_text())
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")


def _layergate2_loop(fn):
    for n in ast.walk(fn):
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
            names = [e.elts[0].value for e in n.iter.elts
                     if isinstance(e, ast.Tuple) and e.elts
                     and isinstance(e.elts[0], ast.Constant)]
            if "l4_regmap_declared_register_coverage_check" in names:
                return n
    raise AssertionError("the layergate-2 loop is gone")


#: A producer that may run after the gate loop ONLY because the documents
#: it writes are declared, by the runner itself, and exclude the judged ones.
_DECLARED_WRITES = {
    "_post_emit_enforce_clock_contract": R._CLOCK_CONTRACT_DOCS,
}
_JUDGED = ("L4_", "L5_", "L6_")


def test_the_layer_gates_run_after_every_producer_of_their_layers():
    """Derived, not hand-listed: every call in `main` to a layer generator
    or a post-emit producer precedes the loop that judges those layers,
    unless the runner declares that producer's documents and they are not
    L4/L5/L6."""
    for fn_name, docs in _DECLARED_WRITES.items():
        assert not any(d.startswith(_JUDGED) for d in docs), (fn_name, docs)
    fn = _main()
    loop = _layergate2_loop(fn)
    late = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if (name.startswith(_PRODUCER_PREFIXES) and n.lineno > loop.lineno
                and name not in _DECLARED_WRITES):
            late.append(f"{name}@{n.lineno}")
    assert late == [], (f"producers of the judged layers run after the gate "
                        f"loop at line {loop.lineno}: {late}")


def test_a_blocking_gate_names_the_report_where_it_was_written():
    """The FAIL line cites the path `_pl.report_path` wrote to."""
    loop = _layergate2_loop(_main())
    src = ast.get_source_segment(RUNNER.read_text(), loop)
    assert "see reports/phase1/{_report_stem}.json" not in src
    assert "_gate_report.relative_to(project)" in src
