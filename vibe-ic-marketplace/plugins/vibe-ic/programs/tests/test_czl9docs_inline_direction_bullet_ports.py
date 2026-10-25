#!/usr/bin/env python3
"""#czl9docs — a bullet that carries its OWN direction keyword IS a port.

Measured on live main 73728b9f: a docs-mode Phase-1 over an input whose first
lines are

    - input  clk
    - output cmd_out (4 bits)

emitted L1.pin_table = [] and L9.ports = [], and asserted
`no_pin_table_in_input` — a positive claim about an input that declares five
ports. Both pre-existing bullet extractors are HEADING-anchored
(`_l1_bullet_port_extract` needs a `Ports:` line, `_l1_directional_prose_port_
extract` needs an `Inputs:` line), so a plain-language description that lists
its pins one per bullet under no heading matched neither.

Pinned in BOTH directions: the declaration shapes must extract, and the
documentation-prose shapes that begin with the same word must NOT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase1_doc_one_shot_runner as R  # noqa: E402

def _F(text):
    # Resolved lazily so a build WITHOUT the extractor fails as a readable
    # per-test red ("the extractor is absent") rather than a collection error.
    fn = getattr(R, "_l1_inline_direction_bullet_port_extract", None)
    assert fn is not None, ("_l1_inline_direction_bullet_port_extract is "
                            "absent — the inline-direction bullet grammar is "
                            "not extracted at all")
    return fn(text)


def _names(text):
    return [(e["name"], e["mode"]) for e in _F(text)]


def test_no_heading_needed_the_bullet_states_its_own_direction():
    text = ("Implement a framed serial receiver.\n"
            "\n"
            " - input  clk\n"
            " - input  rst\n"
            " - input  rx\n"
            " - output cmd_out (4 bits)\n"
            " - output frame_done\n")
    assert _names(text) == [("clk", "input"), ("rst", "input"),
                            ("rx", "input"), ("cmd_out", "output"),
                            ("frame_done", "output")]


def test_width_is_read_from_a_bracket_and_from_a_bit_parenthetical():
    got = {e["name"]: e["width"] for e in _F(
        "- input [7:0] data_in\n"
        "- output cmd_out (4 bits)\n"
        "- output data_out[3:0]\n"
        "- input strobe\n")}
    assert got == {"data_in": "8", "cmd_out": "4",
                   "data_out": "4", "strobe": None}


def test_a_separated_description_is_kept_and_a_width_parenthetical_is_not():
    got = {e["name"]: e["description"] for e in _F(
        "- inout  sda : the bidirectional data pad\n"
        "- output cmd_out (4 bits)\n"
        "- output q — the decoded result\n")}
    assert got == {"sda": "the bidirectional data pad",
                   "cmd_out": None,
                   "q": "the decoded result"}


def test_backticked_forms_extract():
    assert _names("- `input` `scl`\n- `output` `sda`\n") == [
        ("scl", "input"), ("sda", "output")]


def test_documentation_prose_beginning_with_the_same_word_is_not_a_port():
    # The discriminator: after the identifier the bullet runs on into bare
    # prose words, so it is a SENTENCE. A regex that only keyed on the leading
    # direction word would harvest every one of these as a phantom pin.
    prose = ("- Input validation is performed by the host before sending.\n"
             "- Output format follows the vendor convention above.\n"
             "- Input requirements are listed in the table.\n"
             "- output the result to the console when done\n"
             "- Inputs: three\n"
             "- inout\n")
    assert _F(prose) == []


def test_a_stop_word_identifier_never_becomes_a_port():
    assert _F("- input ports\n- output signals\n- inout width\n") == []


def _produce_interface(tmp_path, text):
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "design.md").write_text(text)
    R.gen_l1_datasheet(tmp_path, {"design.md": text})
    R.gen_l9_integration_spec(tmp_path, {"design.md": text}, {})
    generated = tmp_path / "phase1" / "generated_docs"
    return tuple(json.loads((generated / name).read_text()) for name in
                 ("L1_DATASHEET.json", "L9_INTEGRATION_SPEC.json"))


@pytest.mark.parametrize("name", ["alpha", "q", "X3"])
def test_explicit_bullet_provenance_reaches_the_real_l9_producer(tmp_path, name):
    text = f"Implement a module named probe_core.\n- output {name} (5 bits)\n"
    l1, l9 = _produce_interface(tmp_path, text)
    assert [(p["name"], p["mode"], p.get("width")) for p in l1["pin_table"]] == [
        (name, "output", 5)]
    for key in ("ports", "top_ports", "top_module_pins"):
        assert [(p["name"], p["direction"], p.get("width")) for p in l9[key]] == [
            (name.lower(), "output", 5)], l9[key]
        assert l9[key][0]["evidence"] == "input/docs/design.md"


def test_the_real_producer_does_not_invent_an_unstated_bullet_width(tmp_path):
    l1, l9 = _produce_interface(
        tmp_path, "Implement a module named probe_core.\n- output alpha\n")
    assert [p["name"] for p in l1["pin_table"]] == ["alpha"]
    for key in ("ports", "top_ports", "top_module_pins"):
        assert [(p["name"], p.get("width")) for p in l9[key]] == [("alpha", None)]


def test_non_port_bullets_do_not_acquire_l9_provenance(tmp_path):
    text = ("Implement a module named probe_core.\n"
            "- Input validation is performed by the host.\n"
            "- Output format follows the tool convention.\n"
            "- output the result to the console when done\n")
    l1, l9 = _produce_interface(tmp_path, text)
    assert l1["pin_table"] == []
    assert all(l9[key] == [] for key in ("ports", "top_ports", "top_module_pins"))


@pytest.mark.parametrize("width,bullet_width", [("8", " (4 bits)"), ("[SPAN-1:0]", "")])
def test_bullet_promotion_preserves_the_richer_table_width(tmp_path, width, bullet_width):
    text = ("Module Name: probe_core\n"
            "| Signal | Width | Direction | Description |\n"
            "|---|---|---|---|\n"
            f"| alpha | {width} | output | Declared data result |\n"
            f"\n- output alpha{bullet_width}\n")
    l1, l9 = _produce_interface(tmp_path, text)
    pin = next(p for p in l1["pin_table"] if p["name"] == "alpha")
    for key in ("ports", "top_ports", "top_module_pins"):
        port = next(p for p in l9[key] if p["name"] == "alpha")
        for field in ("width", "msb", "lsb", "width_symbolic"):
            assert port.get(field) == pin.get(field)
    if width == "8":
        assert pin["width"] == 8
    else:
        assert pin.get("width_symbolic") == "SPAN-1:0"


def test_existing_repo_interface_still_reaches_the_actual_producer(tmp_path):
    from programs.tests._hostpaths import require_repo
    source = require_repo("vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
                          "tests", "fixtures", "phase1_local", "neutral_mmio_transport",
                          "design.md")
    l1, l9 = _produce_interface(tmp_path, source.read_text())
    assert {p["name"] for p in l1["pin_table"]} == {
        "clk", "reset_n", "cs", "we", "address", "write_data", "read_data", "error"}
    for key in ("ports", "top_ports", "top_module_pins"):
        ports = {p["name"]: p for p in l9[key]}
        assert ports["address"]["width"] == 8
        assert ports["read_data"]["direction"] == "output"
        assert ports["read_data"]["width"] == 32
