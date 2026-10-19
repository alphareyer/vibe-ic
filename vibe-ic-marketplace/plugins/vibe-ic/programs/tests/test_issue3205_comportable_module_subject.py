"""Public document subjects use the existing Comportable declaration grammar."""
import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
from phase1_doc_one_shot_runner import gen_l1_datasheet, gen_l9_integration_spec

PUBLIC_SNIPPET = """# Hardware Interfaces
the module **`packet_engine`** has the following hardware interfaces defined
- Primary Clock: **`clk_i`**
- Bus Device Interfaces (TL-UL): *none*

The supporting module `helper_top` handles test stimuli.
"""


def published(tmp_path, text):
    docs = {"interfaces.md": text}
    (tmp_path / "input.md").write_text(text)
    gen_l1_datasheet(tmp_path, docs)
    return json.loads(Path(gen_l9_integration_spec(tmp_path, docs, {}).path).read_text())


@pytest.mark.parametrize("name,helper", [("packet_engine", True), ("packet_engine", False), ("SHA256", True)])
def test_comportable_subject_reaches_published_l9_without_helper_capture(tmp_path, name, helper):
    text = PUBLIC_SNIPPET.replace("packet_engine", name)
    if not helper:
        text = text.replace("The supporting module `helper_top` handles test stimuli.", "")
    l9 = published(tmp_path, text)
    assert l9["top_module"] == name, "COMPORTABLE_MODULE_TARGET_LOST"
    assert l9["top_module_status"] == "declared_in_input"
    assert l9["top_module_extraction_strategy"] == "doc_module_decl_or_heading"


def test_conflicting_comportable_subjects_are_not_chosen_by_order(tmp_path):
    other = "\nthe module `other_engine` has the following hardware interfaces defined\n"
    l9 = published(tmp_path, PUBLIC_SNIPPET + other)
    assert l9["top_module"] is None
    assert l9["top_module_status"] == "top_undeclared"


def test_repeated_same_subject_keeps_exact_spelling(tmp_path):
    l9 = published(tmp_path, PUBLIC_SNIPPET.replace("packet_engine", "Packet_Engine") * 2)
    assert l9["top_module"] == "Packet_Engine"
    assert l9["top_module_status"] == "declared_in_input"


def test_ordinary_explicit_top_stays_declared(tmp_path):
    l9 = published(tmp_path, "The top module is `ordinary_top`.\n - input clk_i\n - output ready_o\n")
    assert l9["top_module"] == "ordinary_top"
    assert l9["top_module_status"] == "declared_in_input"


@pytest.mark.parametrize("text", [
    "the module packet_engine has the following hardware interfaces defined\n",
    "# Hardware Interfaces\nSee interfaces.md and tool yosys for details.\n",
    "A clocked datapath.\n - input clk_i\n - output ready_o\n",
])
def test_non_declarations_do_not_invent_a_top(tmp_path, text):
    l9 = published(tmp_path, text)
    assert l9["top_module"] is None
    assert l9["top_module_status"] == "top_undeclared"


@pytest.mark.parametrize("declaration", ["Module Name: wrapper_top", "The top module is `wrapper_top`."])
def test_explicit_top_authority_stays_above_a_comportable_child(tmp_path, declaration):
    l9 = published(tmp_path, PUBLIC_SNIPPET + "\n" + declaration + "\n")
    assert l9["top_module"] == "wrapper_top"
    assert l9["top_module_status"] == "declared_in_input"
