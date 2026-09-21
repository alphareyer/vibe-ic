#!/usr/bin/env python3
"""R-0915-113(2) — a vector both halves of which the design STATES is drivable.

THE MEASUREMENT this deck is written from (sha256 x sky130A, front door, the
design's own Phase-1 documents, run20):

    BASE (main cf26dc789)   emit_unit_tbs -> 11 TBs, 0 oracles, 11 substance
                            floors stamped `VIBEIC_TB_ORACLE: NONE`, so Step 4
                            reads a ZERO functional denominator.
    HEAD                    emit_unit_tbs -> 11 TBs, 6 ORACLES, 5 floors.

and, against the design's OWN RTL under `iverilog`:

    clean DUT               6 of 6 PASS, rc=0 (including the 15626-block
                            `long_message_1m_bytes_of_a`)
    K[0] 428a2f98->428a2f99 6 of 6 FAIL, rc=1

The 5 that stay on the floor are refused BY NAME, and that is the honest
answer, not a gap: four state an acceptance PERCENTAGE over a coverage scope
("100% PASS") rather than a value, and one states a message LENGTH (448 bits)
with no message. Grounding any of them would mean COMPUTING the answer, which
is the one thing this family must never do.

Both directions are asserted here: every derivation that must succeed on the
design's own documents, and every derivation that must REFUSE — by name, with
the case named in the reason, so the run says which case it did not ground.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parents[1]
if str(PROG) not in sys.path:
    sys.path.insert(0, str(PROG))

import stated_vector_bus_oracle_gen as G          # noqa: E402
import testbench_gen as T                          # noqa: E402

# ── the design's own surface, as its documents state it ────────────────────
# Written out rather than read from a corpus: the corpus is not on main (see
# `main-no-longer-carries-benchmark-data`), and a record-bound test is silent
# on a clean clone. These are the DOCUMENTED rows, transcribed.
PORTS = [
    ("input", "", "clk"),
    ("input", "", "reset_n"),
    ("input", "", "cs"),
    ("input", "", "we"),
    ("input", "[7:0]", "address"),
    ("input", "[31:0]", "write_data"),
    ("output", "[31:0]", "read_data"),
    ("output", "", "error"),
]


def _reg(name, addr, access, fields=None, desc=""):
    return {"name": name, "address": addr, "access": access,
            "description": desc, "fields": fields or []}


def _fld(name, bit, desc=""):
    return {"field_name": name, "bits": str(bit), "lsb": bit,
            "description": desc}


MODE_DESC = "1 = SHA-256(256-bit digest)、0 = SHA-224(224-bit truncated digest)"

L4 = {"registers": [
    _reg("NAME0", "0x00", "R"), _reg("VERSION", "0x02", "R"),
    _reg("CTRL", "0x08", "R/W", [
        _fld("INIT", 0, "寫 1 啟動新 hash"),
        _fld("NEXT", 1, "寫 1 從上一次 H[] 繼續"),
        _fld("MODE", 2, MODE_DESC)]),
    _reg("STATUS", "0x09", "R", [
        _fld("READY", 0, "1 = chip idle"),
        _fld("VALID", 1, "1 = digest output valid")]),
] + [_reg(f"BLOCK{i}", f"0x{0x10 + i:02x}", "W",
          desc="512-bit message block input") for i in range(16)]
    + [_reg(f"DIGEST{i}", f"0x{0x20 + i:02x}", "R",
            desc="256-bit digest output") for i in range(8)]}

ABC = {"name": "fips1804_sha256_abc",
       "kind": "known_answer_vector",
       "citation": "FIPS-180-4 / NIST SHA-256 example, one-block message",
       "parameters": {"digest_len": 256},
       "inputs": {"message": "616263"},
       "expected_outputs": {"digest": "ba7816bf8f01cfea414140de5dae2223"
                                      "b00361a396177a9cb410ff61f20015ad"}}

PCT = {"name": "message_length", "kind": "coverage_goal",
       "stimulus": "1 byte / 55 bytes / 64 bytes", "expected": "100% PASS"}

LEN_ONLY = {"name": "single_block_nist_appa_abcdbcde_mnopqr",
            "kind": "functional_vector",
            "stimulus": "448-bit message,padded",
            "expected": "248d6a61 d20638b8 e5c02693 0c3e6039 "
                        "a33ce459 64ff2167 f6ecedd4 19db06c1"}

REPEAT = {"name": "long_message_1m_bytes_of_a", "kind": "functional_vector",
          "stimulus": "1,000,000 × 0x61 bytes",
          "expected": "cdc76e5c 9914fb92 81a1c7e2 84d73e67 "
                      "f1809a48 a497200e 046d39cc c7112cd0"}

SHA224 = {"name": "empty_sha_224_mode", "kind": "functional_vector",
          "stimulus": "\"abc\" with MODE=0 (the core enters SHA-224 mode "
                      "when INIT is written with MODE=0)",
          "expected": "23097d22 3405d822 8642a477 bda255b3 "
                      "2aadbce4 bda0b3f7 e36c9da7(224-bit)"}


# ── 1. the bus the design declares ─────────────────────────────────────────
def test_the_declared_port_table_resolves_to_a_memory_mapped_bus():
    bus, why = G.bus_contract(PORTS)
    assert bus is not None, why
    assert (bus["cs"], bus["we"], bus["address"]) == ("cs", "we", "address")
    assert (bus["write_data"], bus["read_data"]) == ("write_data", "read_data")
    assert bus["word_bits"] == 32 and bus["address_bits"] == 8
    assert bus["clk"] == "clk" and bus["rst"] == "reset_n"
    assert bus["rst_active_low"] is True
    assert bus["error"] == "error"


@pytest.mark.parametrize("drop", ["cs", "we", "address", "write_data",
                                  "read_data"])
def test_a_partial_bus_refuses_and_names_the_missing_signal(drop):
    bus, why = G.bus_contract([p for p in PORTS if p[2] != drop])
    assert bus is None
    assert drop in why


def test_a_reset_without_an_n_suffix_is_read_as_active_high():
    ports = [(d, w, ("reset" if n == "reset_n" else n)) for d, w, n in PORTS]
    bus, why = G.bus_contract(ports)
    assert bus is not None, why
    assert bus["rst_active_low"] is False


def test_a_design_with_no_clock_refuses():
    bus, why = G.bus_contract([p for p in PORTS if p[2] != "clk"])
    assert bus is None and "clock" in why


def test_mismatched_write_and_read_widths_refuse():
    ports = [(d, ("[15:0]" if n == "read_data" else w), n)
             for d, w, n in PORTS]
    bus, why = G.bus_contract(ports)
    assert bus is None and "differ in width" in why


# ── 2. the register windows ────────────────────────────────────────────────
def test_the_indexed_write_window_is_the_block_family():
    win, why = G.indexed_window(L4["registers"], G._IN_WINDOW_ROLES, "W")
    assert win is not None, why
    assert win["base"] == "block"
    assert win["addresses"] == list(range(0x10, 0x20))


def test_the_indexed_read_window_is_the_digest_family():
    win, why = G.indexed_window(L4["registers"], G._OUT_WINDOW_ROLES, "R")
    assert win is not None, why
    assert win["base"] == "digest"
    assert win["addresses"] == list(range(0x20, 0x28))


def test_a_window_stated_at_two_addresses_is_a_refusal_not_a_choice():
    regs = list(L4["registers"]) + [_reg("BLOCK3", "0x33", "W")]
    win, why = G.indexed_window(regs, G._IN_WINDOW_ROLES, "W")
    assert win is None
    assert "0x13" in why and "0x33" in why


def test_a_window_with_a_hole_refuses():
    regs = [r for r in L4["registers"] if r["name"] != "BLOCK7"]
    win, why = G.indexed_window(regs, G._IN_WINDOW_ROLES, "W")
    assert win is None and "contiguous" in why


def test_two_candidate_windows_refuse_rather_than_pick_one():
    regs = list(L4["registers"]) + [
        _reg(f"DIN{i}", f"0x{0x40 + i:02x}", "W") for i in range(4)]
    win, why = G.indexed_window(regs, G._IN_WINDOW_ROLES, "W")
    assert win is None and "more than one candidate" in why


def test_the_command_contract_reads_the_field_table_by_role():
    cmd, why = G.command_contract(L4["registers"])
    assert cmd is not None, why
    assert (cmd["ctrl_addr"], cmd["status_addr"]) == (0x08, 0x09)
    assert (cmd["init_bit"], cmd["next_bit"]) == (0, 1)
    assert (cmd["ready_bit"], cmd["valid_bit"]) == (0, 1)
    assert cmd["mode_bit"] == 2


def test_a_status_register_with_no_valid_bit_refuses():
    regs = [r if r["name"] != "STATUS"
            else _reg("STATUS", "0x09", "R", [_fld("READY", 0)])
            for r in L4["registers"]]
    cmd, why = G.command_contract(regs)
    assert cmd is None and "valid" in why


# ── 3. the mode encoding comes from the field's OWN description ────────────
def test_the_mode_value_is_read_out_of_the_fields_own_table():
    assert G.mode_value_for_width(MODE_DESC, 256)[0] == 1
    assert G.mode_value_for_width(MODE_DESC, 224)[0] == 0


def test_a_width_the_mode_table_does_not_state_refuses():
    val, why = G.mode_value_for_width(MODE_DESC, 512)
    assert val is None and "512" in why


def test_a_mode_field_with_no_table_refuses_rather_than_defaulting():
    val, why = G.mode_value_for_width("selects the algorithm variant", 256)
    assert val is None and "states no value=width table" in why


# ── 4. the vector the case STATES, and the five it does not ────────────────
def test_a_typed_vector_states_both_halves():
    msg, _ = G.stated_message(ABC)
    ans, _ = G.stated_answer(ABC)
    assert msg["kind"] == "hex" and msg["hex"] == "616263"
    assert ans == ABC["expected_outputs"]["digest"]


def test_an_acceptance_percentage_is_refused_by_the_cases_own_name():
    ans, why = G.stated_answer(PCT)
    assert ans is None
    assert "message_length" in why and "COMPUTING" in why


def test_a_stated_length_with_no_message_is_refused_by_name():
    msg, why = G.stated_message(LEN_ONLY)
    assert msg is None
    assert LEN_ONLY["name"] in why and "448 bits" in why


def test_an_elided_hex_run_with_a_stated_length_is_the_stated_prefix():
    case = {"name": "empty_nist_appa_single_block_abc",
            "stimulus": "0x6162638000... + length 24 bit", "expected": "00" * 32}
    msg, _ = G.stated_message(case)
    assert msg is not None and msg["hex"] == "616263"


def test_an_elided_hex_run_with_no_stated_length_refuses():
    case = {"name": "nameless", "stimulus": "0x616263...", "expected": "00"}
    msg, why = G.stated_message(case)
    assert msg is None and "ELIDED" in why


def test_a_repeat_spec_is_a_stated_message():
    msg, _ = G.stated_message(REPEAT)
    assert msg == {"kind": "repeat", "byte": 0x61, "count": 1000000,
                   "bytes": 1000000, "evidence": "stimulus"}


def test_a_quoted_ascii_literal_is_a_stated_message():
    msg, _ = G.stated_message(SHA224)
    assert msg is not None and msg["hex"] == b"abc".hex()


def test_the_case_may_state_the_mode_itself():
    cmd, _ = G.command_contract(L4["registers"])
    assert G.stated_mode_override(SHA224, cmd["mode_field"]) == 0
    assert G.stated_mode_override(ABC, cmd["mode_field"]) is None


# ── 5. the padding is framing, and the answer is never computed ────────────
def test_the_padding_is_the_cited_standards_own_framing():
    # 3 bytes + 0x80 + zeros + a 64-bit length closes ONE 512-bit block.
    assert G.pad_blocks(3, 512, 64) == (1, 3, 24)
    # 56 bytes no longer fits the length field: two blocks.
    assert G.pad_blocks(56, 512, 64)[0] == 2
    # a whole number of blocks always gains one more for the padding.
    assert G.pad_blocks(64, 512, 64)[0] == 2


def test_this_module_computes_no_reference_answer():
    src = (PROG / "stated_vector_bus_oracle_gen.py").read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # A reference model is how a "stated vector" quietly becomes a computed
    # one. None of these may be reachable from this module.
    assert not (imported & {"hashlib", "hmac", "zlib", "binascii", "crcmod",
                            "Crypto", "cryptography", "pycryptodome"}), imported


def test_the_stated_literal_is_what_the_tb_compares_against():
    plan, why = G.resolve_stated_vector(ABC, L4, PORTS)
    assert plan is not None, why
    text, why = G.emit_stated_vector_bus_tb(plan, "sha256", ABC)
    assert text, why
    want = ABC["expected_outputs"]["digest"]
    got = re.findall(r"expect_w\[\d+\] = 32'h([0-9a-f]{8});", text)
    assert "".join(got) == want


def test_changing_the_stated_answer_changes_the_tb():
    other = dict(ABC, expected_outputs={"digest": "de" * 32})
    a, _ = G.emit_case_stated_vector_bus(ABC, L4, "sha256", PORTS)
    b, _ = G.emit_case_stated_vector_bus(other, L4, "sha256", PORTS)
    assert a and b and a != b
    assert "32'hdedededede"[:12] not in a
    assert re.search(r"expect_w\[0\] = 32'hdededede;", b)


# ── 6. the testbench can fail, and cannot pass without checking ────────────
def test_the_emitted_tb_fatals_on_a_mismatch():
    text, why = G.emit_case_stated_vector_bus(ABC, L4, "sha256", PORTS)
    assert text, why
    assert "$fatal(1);" in text
    assert "errors = errors + 1;" in text
    # the PASS line is reachable only after `errors != 0` has been tested
    fatal = text.index("$fatal(1);")
    ok = text.index("] PASS:")
    assert fatal < ok


def test_the_poll_budget_is_bounded_and_fails():
    text, _ = G.emit_case_stated_vector_bus(ABC, L4, "sha256", PORTS)
    assert "polls >= POLL_BUDGET" in text
    assert re.search(r"localparam integer POLL_BUDGET = \d+;", text)
    budget = text[text.index("if (polls >= POLL_BUDGET)"):]
    assert budget[:400].count("errors = errors + 1;") == 1
    assert "never asserted within" in text


def test_the_error_flag_the_design_publishes_is_checked():
    text, _ = G.emit_case_stated_vector_bus(ABC, L4, "sha256", PORTS)
    assert "raised on a documented address" in text


def test_the_tb_drives_the_designs_own_signal_names():
    text, _ = G.emit_case_stated_vector_bus(ABC, L4, "sha256", PORTS)
    assert ".cs(cs), .we(we), .address(address)" in text
    assert "reg reset_n = 1'b0;" in text        # active-low, asserted at t=0
    assert "in_addr[0] = 8'h10;" in text
    assert "out_addr[0] = 8'h20;" in text


def test_a_two_block_message_uses_the_continue_bit():
    case = dict(ABC, name="two_block",
                inputs={"message": "61" * 56})
    text, why = G.emit_case_stated_vector_bus(case, L4, "sha256", PORTS)
    assert text, why
    # CTRL: INIT=bit0 with MODE=1<<2 -> 0x5, then NEXT=bit1 -> 0x6
    assert "bus_write(8'h8, 32'h5);" in text
    assert "bus_write(8'h8, 32'h6);" in text


def test_a_two_block_message_refuses_when_no_continue_bit_is_stated():
    regs = [r if r["name"] != "CTRL"
            else _reg("CTRL", "0x08", "R/W",
                      [_fld("INIT", 0), _fld("MODE", 2, MODE_DESC)])
            for r in L4["registers"]]
    case = dict(ABC, name="two_block", inputs={"message": "61" * 56})
    text, why = G.emit_case_stated_vector_bus(case, {"registers": regs},
                                              "sha256", PORTS)
    assert text is None and "continue bit" in why


def test_a_million_stated_bytes_do_not_become_a_million_literals():
    text, why = G.emit_case_stated_vector_bus(REPEAT, L4, "sha256", PORTS)
    assert text, why
    assert "for (blk = 0; blk < 15625; blk = blk + 1)" in text
    assert len(text.splitlines()) < 400


def test_the_224_bit_answer_reads_only_the_words_the_map_states():
    text, why = G.emit_case_stated_vector_bus(SHA224, L4, "sha256", PORTS)
    assert text, why
    assert "out_addr [0:6];" in text            # 7 of the 8 digest words
    assert "out_addr[7]" not in text
    assert "bus_write(8'h8, 32'h1);" in text    # MODE=0 -> INIT alone


def test_an_answer_wider_than_the_designs_window_refuses():
    case = dict(ABC, expected_outputs={"digest": "ab" * 64})
    text, why = G.emit_case_stated_vector_bus(case, L4, "sha256", PORTS)
    assert text is None and "cannot carry it" in why


# ── 7. the flow runs it: the denominator, both directions ──────────────────
def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "phase1" / "generated_docs").mkdir(parents=True)
    (p / "phase2" / "stage1" / "rtl").mkdir(parents=True)
    (p / "phase1" / "generated_docs" / "L4_REGMAP.json").write_text(
        json.dumps(L4))
    (p / "phase1" / "generated_docs" / "L10_TEST_CASES.json").write_text(
        json.dumps({"schema_version": 2, "doc_class": "test_cases",
                    "ic_name": "sha256", "test_cases": CASES}))
    ports = "".join(
        f"  {'input' if d.startswith('input') else 'output'} "
        f"{w + ' ' if w else ''}{n}{',' if i < len(PORTS) - 1 else ''}\n"
        for i, (d, w, n) in enumerate(PORTS))
    (p / "phase2" / "stage1" / "rtl" / "sha256.v").write_text(
        f"module sha256 (\n{ports});\nendmodule\n")
    return p


CASES = [ABC, SHA224, REPEAT, PCT, LEN_ONLY]


def test_the_emitter_ladder_grounds_the_stated_ones_and_floors_the_rest(
        tmp_path):
    p = _project(tmp_path)
    T._L4_CACHE.clear()
    report = {}
    n = T.emit_unit_tbs(p, CASES, report=report)
    assert n == len(CASES), report.get("reason")
    grounded = {c["case"] for c in report.get("stated_vector_cases", [])}
    assert grounded == {ABC["name"], SHA224["name"], REPEAT["name"]}
    refused = {c["case"] for c in report.get("stated_vector_unbound", [])}
    assert refused == {PCT["name"], LEN_ONLY["name"]}
    tb = p / "phase2" / "stage1" / "sim" / "tb"
    for c in CASES:
        text = (tb / f"{c['name']}.v").read_text()
        floor = T.ORACLE_NONE_MARKER in text
        assert floor is (c["name"] in refused), c["name"]


def test_every_grounded_case_carries_its_provenance(tmp_path):
    p = _project(tmp_path)
    T._L4_CACHE.clear()
    report = {}
    T.emit_unit_tbs(p, CASES, report=report)
    assert report["stated_vector_cases"]
    for row in report["stated_vector_cases"]:
        assert row["provenance"] == "GENERATED_FROM_STATED_VECTOR"


def test_a_project_with_no_register_map_refuses_by_name(tmp_path):
    p = _project(tmp_path)
    (p / "phase1" / "generated_docs" / "L4_REGMAP.json").unlink()
    T._L4_CACHE.clear()
    report = {}
    T.emit_unit_tbs(p, CASES, report=report)
    assert not report.get("stated_vector_cases")
    reasons = {c["reason"] for c in report.get("stated_vector_unbound", [])}
    assert reasons == {"the project stages no L4 register map"}
    tb = p / "phase2" / "stage1" / "sim" / "tb"
    for c in CASES:
        assert T.ORACLE_NONE_MARKER in (tb / f"{c['name']}.v").read_text()


def test_the_emitter_is_reachable_from_the_ladder_not_merely_defined():
    """A helper with tests is not a fix until the runner runs it."""
    src = (PROG / "testbench_gen.py").read_text()
    tree = ast.parse(src)
    loop = next(f for f in ast.walk(tree)
                if isinstance(f, ast.FunctionDef) and f.name == "emit_unit_tbs")
    called = {n.func.id for n in ast.walk(loop)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_emit_case_stated_vector_bus" in called


# ── 8. the deck's own hygiene ──────────────────────────────────────────────
def test_no_two_tests_in_this_file_share_a_name():
    """A duplicated name silently shadows the first, so a guard never runs."""
    tree = ast.parse(Path(__file__).read_text())
    names = [n.name for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(names) == len(set(names)), \
        sorted({n for n in names if names.count(n) > 1})
