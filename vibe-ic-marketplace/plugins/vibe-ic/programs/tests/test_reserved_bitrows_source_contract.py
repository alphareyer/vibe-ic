"""Explicit reserved spans through the public L4 producer, with source policy."""
import json
from pathlib import Path

import pytest
import phase1_doc_one_shot_runner as producer
from _named_register_fields import attach_named_fields


FIXTURE = (Path(__file__).parent / "fixtures/phase1_local/neutral_reserved_bitrows/L5_register_map.md").read_text()


def emit(tmp_path, text=FIXTURE):
    docs = tmp_path / "input/docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "L5_register_map.md").write_text(text)
    result = producer.gen_l4_regmap(tmp_path, {"L5_register_map.md": text})
    return {r["name"]: r for r in json.loads(result.path.read_text())["registers"]}


@pytest.mark.parametrize("span", ["1-31", "31-1", "1:31", "31:1", "1 - 31"])
def test_explicit_reserved_range_keeps_owning_register_policy(tmp_path, span):
    text = FIXTURE.replace("1-31", span)
    rows = emit(tmp_path, text)
    assert [{f["field_name"] for f in rows[name]["fields"]} for name in ("CTRL", "STATUS")] == [
        {"START", "reserved"}, {"READY", "reserved"}]
    for name, policy in [("CTRL", "讀回 0,寫入忽略"), ("STATUS", "讀回 0")]:
        field = next(f for f in rows[name]["fields"] if f["field_name"] == "reserved")
        assert field["bits"] == "31:1"
        assert field["role"] == "reserved"
        assert field["description"] == policy
        assert field["evidence"]["source"] == rows[name]["evidence"] == "input/docs/L5_register_map.md"
        line = text.splitlines()[field["evidence"]["line"] - 1]
        assert line == field["evidence"]["quote"]
        assert span in line and policy in line
        assert "reset" not in field and "default" not in field
    assert rows["CTRL"]["fields"][0]["field_name"] == "START"
    assert rows["STATUS"]["fields"][0]["field_name"] == "READY"


@pytest.mark.parametrize("policy", ["", "policy unspecified", "read zero or one; write behavior unspecified",
                                    "reads one; writes may change this field"])
def test_unknown_or_conflicting_policy_is_not_replaced_by_generic_defaults(tmp_path, policy):
    rows = emit(tmp_path, FIXTURE.replace("讀回 0,寫入忽略", policy))
    assert {f["field_name"] for f in rows["CTRL"]["fields"]} == {"START", "reserved"}
    field = next(f for f in rows["CTRL"]["fields"] if f["field_name"] == "reserved")
    assert field["description"] == policy
    assert not {"reset", "default", "read_value", "write_behavior"} & set(field)


def test_no_reserved_declaration_does_not_invent_a_reserved_field(tmp_path):
    text = "\n".join(line for line in FIXTURE.splitlines() if "| reserved |" not in line)
    rows = emit(tmp_path, text)
    assert [[f["field_name"] for f in rows[name]["fields"]] for name in ("CTRL", "STATUS")] == [["START"], ["READY"]]


def test_reserved_name_translation_does_not_change_range_or_source(tmp_path):
    rows = emit(tmp_path, FIXTURE.replace("| reserved |", "| 保留 |"))
    assert {f["field_name"] for f in rows["CTRL"]["fields"]} == {"START", "保留"}
    assert next(f for f in rows["CTRL"]["fields"] if f.get("role") == "reserved")["bits"] == "31:1"


def test_existing_fields_and_neighboring_source_remain_authoritative():
    import copy
    rows = [{"name": "CTRL", "address_int": 8, "evidence": "input/docs/other.md"},
            {"name": "STATUS", "address_int": 8, "evidence": "input/docs/L5_register_map.md"},
            {"name": "CTRL", "address_int": 8, "evidence": "input/docs/L5_register_map.md",
             "fields": [{"field_name": "EXISTING", "bits": "2"}]}]
    before = copy.deepcopy(rows)
    attach_named_fields(rows, {"L5_register_map.md": FIXTURE})
    assert rows == before


def test_multiple_disjoint_reserved_ranges_remain_distinct(tmp_path):
    text = FIXTURE.replace("| 1-31 | reserved | 讀回 0,寫入忽略 |",
                           "| 1-7 | reserved | read zero |\n| 8-31 | reserved | write ignored |")
    rows = emit(tmp_path, text)
    assert [(f["bits"], f["description"]) for f in rows["CTRL"]["fields"] if f["field_name"] == "reserved"] == [
        ("7:1", "read zero"), ("31:8", "write ignored")]


def test_conflicting_duplicate_declarations_do_not_select_one_policy():
    row = {"name": "CTRL", "address_int": 8, "evidence": "input/docs/L5_register_map.md"}
    text = FIXTURE.split("## STATUS")[0]
    attach_named_fields([row], {"L5_register_map.md": text + "\n" + text.replace("讀回 0,寫入忽略", "read one")})
    assert "fields" not in row


def test_identical_duplicate_declarations_keep_first_attributable_policy():
    row = {"name": "CTRL", "address_int": 8, "evidence": "input/docs/L5_register_map.md"}
    text = FIXTURE.split("## STATUS")[0]
    attach_named_fields([row], {"L5_register_map.md": text + "\n" + text})
    assert {f["field_name"] for f in row["fields"]} == {"START", "reserved"}
    field = next(f for f in row["fields"] if f.get("role") == "reserved")
    assert field["description"] == "讀回 0,寫入忽略"
    assert field["evidence"]["line"] == 11
