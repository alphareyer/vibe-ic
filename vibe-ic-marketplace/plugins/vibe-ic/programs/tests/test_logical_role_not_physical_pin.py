"""Issue3220 public source through actual L1 and L9 producers."""
import json
from pathlib import Path

import pytest

import phase1_doc_one_shot_runner as runner


FIXTURE = Path(__file__).parent / "fixtures/phase1_local/logical_role_not_physical_pin/interfaces.md"
EXPECTED = {"bus_req_i": "input", "bus_rsp_o": "output",
            "entropy_req_o": "output", "entropy_rsp_i": "input"}


def _produce(root, source):
    path = root / "input/docs/interfaces.md"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    extracted = {"interfaces.md": source}
    l1 = runner.gen_l1_datasheet(root, extracted)
    l9 = runner.gen_l9_integration_spec(root, extracted, {})
    rosters = {
        "L1": {r["name"]: r["mode"] for r in json.loads(l1.path.read_text())["pin_table"]},
        "L9": {r["name"]: r["direction"] for r in json.loads(l9.path.read_text())["top_ports"]},
    }
    (root / "PUBLIC_ROSTERS.json").write_text(json.dumps(rosters, indent=2))
    return rosters


@pytest.mark.parametrize("layout", ["verbatim", "sentences_on_separate_lines"])
def test_logical_names_do_not_borrow_another_sentences_direction(tmp_path, layout):
    source = FIXTURE.read_text()
    if layout == "sentences_on_separate_lines":
        source = source.replace(". ", ".\n")
    assert _produce(tmp_path, source) == {"L1": EXPECTED, "L9": EXPECTED}


def test_the_same_short_names_remain_valid_explicit_physical_ports(tmp_path):
    source = ("# Physical interface\n\n"
              "Physical input `req`. Physical output `rsp`. Physical output `cdc`.\n")
    expected = {"req": "input", "rsp": "output", "cdc": "output"}
    assert _produce(tmp_path, source) == {"L1": expected, "L9": expected}


def test_explicit_legacy_inout_declaration_stays_physical(tmp_path):
    expected = {"PAD_BUS": "inout"}
    assert _produce(tmp_path, "# Physical interface\n\ninout PAD_BUS\n") == {
        "L1": expected, "L9": {"pad_bus": "inout"}}
