"""A structured spec's `ports:` reach the Phase-1 L documents, with their direction.

v1.24.57 made `gates_atomic` run the canonical Phase-1 runner on every emit, and
that runner reads the agent's Shape-C `spec.yaml` as a design document. The
spec states its interface as data (`L9: {ports: {a: {dir: input, width: 1}}}`),
which neither the code-region nor the signal-table grammar reads: the runner
published ZERO ports, the sufficiency gate (which does see ports in the input)
halted on an EXTRACTION GAP, and every Shape-C emit with ports failed its
`phase1_run_all` hard gate.

Second defect on the same path: the docs door appended the shared grammar's
entries with only `dir`, while the pin table and the L9 promoter read
`mode`/`direction`, so every port that grammar supplied was published `inout`.
"""
import json
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase1_port_extract as ppx  # noqa: E402
from _specrtl_common import WIDTH_UNKNOWN  # noqa: E402

SHAPE_C = (
    "ic_name: TopModule\nclass_path: combinational-logic\n"
    "L1: { ic_name: TopModule, description: kmap }\n"
    "L9:\n  module_name: TopModule\n  ports:\n"
    "    clk: { dir: input, width: 1 }\n"
    "    din: { dir: input, width: 8 }\n"
    "    q: { dir: output, width: 1 }\n")


def test_mapping_form_yields_every_port_with_direction_and_width():
    got = [(e["name"], e["dir"], e["width"])
           for e in ppx.extract_structured_spec_ports(SHAPE_C)]
    assert got == [("clk", "input", 1), ("din", "input", 8), ("q", "output", 1)]
    lines = {e["name"]: e["source_line"]
             for e in ppx.extract_structured_spec_ports(SHAPE_C)}
    # the quoted line is the port's own line, not a key that merely starts
    # with the same letters (`c` must not quote `class_path:`)
    assert lines["clk"] == "clk: { dir: input, width: 1 }"


def test_list_form_and_refusals():
    text = ("top:\n  ports:\n"
            "    - {name: a, direction: input, width: '4'}\n"
            "    - {name: b, dir: output, width: N}\n"
            "    - {name: c, dir: sideways, width: 1}\n"
            "    - {name: 9bad, dir: input, width: 1}\n")
    got = [(e["name"], e["dir"], e["width"])
           for e in ppx.extract_structured_spec_ports(text)]
    assert got == [("a", "input", 4), ("b", "output", WIDTH_UNKNOWN)]


def test_prose_and_portless_structures_yield_nothing():
    assert ppx.extract_structured_spec_ports(
        "The module has ports: an input a and an output q.") == []
    assert ppx.extract_structured_spec_ports("L9: {module_name: TopModule}\n") == []
    assert ppx.extract_structured_spec_ports("ports: [a, b]\n") == []


def test_the_shared_grammar_carries_the_structured_shape():
    names = [e["name"] for e in ppx.extract_code_block_ports(SHAPE_C)]
    assert names == ["clk", "din", "q"]
    assert [p["name"] for p in ppx.extract_ports(SHAPE_C)] == ["clk", "din", "q"]


def _phase1(tmp_path, text):
    proj = tmp_path / "proj"
    (proj / "input" / "docs").mkdir(parents=True)
    (proj / "input" / "docs" / "design_description.md").write_text(text)
    r = subprocess.run([sys.executable, str(PROGRAMS / "phase1_one_shot_runner.py"),
                        str(proj)], capture_output=True, text=True, timeout=600)
    l9 = proj / "phase1" / "generated_docs" / "L9_INTEGRATION_SPEC.json"
    assert l9.is_file(), r.stdout[-2000:] + r.stderr[-2000:]
    ports = json.loads(l9.read_text())["top_ports"]
    return r, [(p["name"], p.get("mode"), p.get("width")) for p in ports]


def test_docs_door_publishes_a_shape_c_spec_interface(tmp_path):
    r, ports = _phase1(tmp_path, SHAPE_C)
    assert ports == [("clk", "input", 1), ("din", "input", 8), ("q", "output", 1)]
    assert "EXTRACTION GAP" not in r.stdout + r.stderr


def test_docs_door_keeps_a_code_region_ports_direction(tmp_path):
    _r, ports = _phase1(tmp_path, (
        "Design TopModule.\n\n```verilog\nmodule TopModule(\n"
        "  input  wire [3:0] din, // data\n  output wire q\n);\nendmodule\n```\n"))
    assert ports == [("din", "input", 4), ("q", "output", 1)]
