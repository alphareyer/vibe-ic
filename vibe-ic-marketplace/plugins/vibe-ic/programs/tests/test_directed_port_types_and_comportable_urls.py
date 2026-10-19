"""Two contributor-derived public document gaps on the canonical99 schema."""
import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase1_doc_one_shot_runner as producer
from _specrtl_common import extract_spec_contract

FIXTURES = Path(__file__).parent / "fixtures/phase1_local/typed_port_and_subject_url"


def publish(tmp_path, text):
    docs = {"interfaces.md": text}
    gen = producer.gen_l1_datasheet(tmp_path, docs)
    l1 = json.loads(Path(gen.path).read_text())
    l9 = json.loads(Path(producer.gen_l9_integration_spec(tmp_path, docs, {}).path).read_text())
    return l1, l9


@pytest.mark.parametrize("name", ["directed_direction_first.md", "directed_type_first.md"])
def test_directed_nominal_type_survives_producer_and_both_projections(tmp_path, name):
    l1, l9 = publish(tmp_path, (FIXTURES / name).read_text())
    promoted = producer._promote_l1_pins_to_l9_ports(l1["pin_table"], None)
    for pins in [l1["pin_table"], l9["ports"], l9["top_ports"], l9["top_module_pins"], promoted]:
        rows = {p["name"]: p for p in pins}
        assert "reply_i" in rows, "DIRECTED_TYPE_ROW_LOST"
        assert rows["reply_i"].get("type_ref") == "transport_pkg::reply_t"
        assert rows["reply_i"].get("width") is None
        assert rows["reply_i"]["width_symbolic"] == "transport_pkg::reply_t"
        assert rows["ready_o"].get("type_ref") == "logic"
        assert rows["ready_o"]["width"] == 1
    mirrored = {"pin_table": []}
    assert producer._v1_6_555_crosswalk_l9_ports_to_l1_pin_table(mirrored, l9["ports"])
    reply = next(p for p in mirrored["pin_table"] if p["name"] == "reply_i")
    assert reply["type_ref"] == "transport_pkg::reply_t"
    assert reply["width_symbolic"] == "transport_pkg::reply_t" and reply.get("width") is None
    contract = extract_spec_contract(json.dumps(l9), is_json=True, confirm=False)
    assert next(p for p in contract.ports if p.name == "reply_i").width == 0
    assert next(p for p in contract.ports if p.name == "ready_o").width == 1


def test_symbolic_projection_without_literal_is_unknown_in_real_consumer():
    contract = extract_spec_contract(json.dumps({"ports": [{"name": "reply_i", "dir": "input", "type_ref": "transport_pkg::reply_t", "width_symbolic": "transport_pkg::reply_t"}]}), is_json=True, confirm=False)
    assert contract.ports[0].width == 0


def test_preceding_url_subject_reaches_normal_l9(tmp_path):
    text = (FIXTURES / "preceding_url.md").read_text()
    assert producer._doc_module_name_label_or_inline({"interfaces.md": text}) == "sha256"
    _l1, l9 = publish(tmp_path, text)
    assert l9["top_module"] == "sha256"
    assert l9["top_module_status"] == "declared_in_input"


def test_real_hdl_comment_does_not_become_a_url_subject(tmp_path):
    text = "// " + (FIXTURES / "preceding_url.md").read_text()
    assert producer._doc_module_name_label_or_inline({"interfaces.md": text}) is None
    block_comment = "/* " + (FIXTURES / "preceding_url.md").read_text() + " */"
    assert producer._doc_module_name_label_or_inline({"interfaces.md": block_comment}) is None
    _l1, l9 = publish(tmp_path, text)
    assert l9["top_module"] is None and l9["top_module_status"] == "top_undeclared"


def test_explicit_wrapper_stays_above_url_interface_subject(tmp_path):
    text = (FIXTURES / "preceding_url.md").read_text() + "\nThe top module is `wrapper_top`.\n"
    _l1, l9 = publish(tmp_path, text)
    assert l9["top_module"] == "wrapper_top"
    assert l9["top_module_status"] == "declared_in_input"


def test_url_subject_conflict_cannot_select_the_other_interface(tmp_path):
    text = (FIXTURES / "preceding_url.md").read_text() + "\nthe module `other_engine` has the following hardware interfaces defined\n"
    _l1, l9 = publish(tmp_path, text)
    assert l9["top_module"] is None and l9["top_module_status"] == "top_undeclared"
