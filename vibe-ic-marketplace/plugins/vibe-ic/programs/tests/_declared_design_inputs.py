"""Give a synthetic run tree the design's DECLARED input: no catalog reuse.

WHY IT IS SHARED. F9 (v1.25.36) made step 1's `catalog_synth_safe_params_check`
take its applicability from the design's declaration -- the catalog IP its
input docs name as reuse (`ip_catalog_query.declared_catalog_reuse`, read from
`phase1/generated_docs/L*.json`). A tree with no L-doc is refused NOT_MEASURED
("declaration missing"), because unread is not empty. The synthetic trees the
flow tests build carried step 1's outputs and none of this input, so step 1
refused on every one of them: d268's two step-1 cases went red, and d8's
seeded step 1 left the PASS tier.

A real run past D1 always holds these documents; the front door writes them
before any step reads them. So the declaration goes into the fixture, once,
here -- in the shape the Phase-1 front door writes (`L1_DATASHEET.json`,
schema_version 2, doc_class "datasheet", the `no_*_in_input` markers). Its
prose names no catalog IP, so the design declares no reuse, and the gate
reaches DESIGN_DECLARED_NA through its own reading.

WHICH TREES. Only a step whose OWN `required_inputs` take the declaration
from D1 -- D1's outputs whole (`outputs: all`, step 1) or the datasheet by path
-- is given it (:func:`consumes_declaration`). Read from the flow YAML, never
from which gate a step runs: seeding every tree was MEASURED to move steps 2
and 38, which declare no such input, so it would test a tree no run builds.

TWO FORMS, BOTH REAL. The gate reads either document set:
- :func:`declare_no_reuse` writes D1's datasheet. For a tree built for ONE
  step whose declared input is D1's outputs (d8, d268).
- :func:`declare_no_reuse_input` writes the user's input doc
  (`input/docs/L1_product_metadata.md`, front matter + product table, as the
  spm run carries it). For a whole-flow tree on which D1 never ran: an L-doc
  there would be a partial D1 output, and D1's own gates -- which are not
  the subject -- would then run on it (measured: six extra failing gate names
  in test_p0_failed_gate_names_preserved).

IT SUPPLIES AN INPUT; IT DOES NOT DISABLE A GATE. The gate still runs and
still reads the declaration; a tree this function was never called on is
still refused, and a declaration that names a catalog IP is still judged by
Yosys (test_f9_step1_catalog_synth_safe_gate.py).
"""
import json
from pathlib import Path

#: Where the front door writes the datasheet, and where the gate reads it.
DECLARATION_REL = "phase1/generated_docs/L1_DATASHEET.json"
#: The user's own input doc, which the gate also reads.
INPUT_DECLARATION_REL = "input/docs/L1_product_metadata.md"


#: The step that writes the declaration.
DECLARING_STEP = "D1"


def consumes_declaration(step: dict) -> bool:
    """True when ``step`` declares the design's declaration as its input."""
    for entry in step.get("required_inputs") or ():
        if not isinstance(entry, dict) or str(entry.get("from")) != DECLARING_STEP:
            continue
        if entry.get("outputs") == "all" or entry.get("path") == DECLARATION_REL:
            return True
    return False


def no_reuse_datasheet(ic_name: str = "toggle") -> dict:
    """An L1 datasheet whose words name no catalog IP: no declared reuse."""
    return {
        "schema_version": 2,
        "doc_class": "datasheet",
        "ic_name": ic_name,
        "no_ic_name_in_input": False,
        "package_info": None,
        "no_package_in_input": True,
        "pin_table": [
            {"name": "clk", "mode": "input", "rtl_name": "clk", "width": 1,
             "function": "system clock; state advances on the rising edge",
             "evidence": "input/docs/L1_overview.md"},
            {"name": "q", "mode": "output", "rtl_name": "q", "width": 1,
             "function": "registered output",
             "evidence": "input/docs/L1_overview.md"},
        ],
        "no_pin_table_in_input": False,
        "no_electrical_specs_in_input": True,
        "no_tapeout_metadata_in_input": True,
        "source_documents": ["input/docs/L1_overview.md"],
    }


def declare_no_reuse(project: Path, ic_name: str = "toggle") -> Path:
    """Write the no-reuse declaration into ``project``; return its path."""
    path = Path(project) / DECLARATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(no_reuse_datasheet(ic_name), indent=2) + "\n")
    return path


def no_reuse_input_doc(ic_name: str = "toggle") -> str:
    """An L1 input doc whose words name no catalog IP: no declared reuse."""
    return (
        "---\n"
        "layer: L1\n"
        f"ic: {ic_name}\n"
        "status: draft\n"
        "---\n\n"
        "# L1 — Product & Tapeout Metadata\n\n"
        "| field | value |\n"
        "|---|---|\n"
        f"| product_name | `{ic_name}` |\n"
        "| one-line description | a one-bit register that inverts on every "
        "rising edge of `clk` and drives `q` |\n")


def declare_no_reuse_input(project: Path, ic_name: str = "toggle") -> Path:
    """Write the no-reuse input doc into ``project``; return its path."""
    path = Path(project) / INPUT_DECLARATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(no_reuse_input_doc(ic_name))
    return path
