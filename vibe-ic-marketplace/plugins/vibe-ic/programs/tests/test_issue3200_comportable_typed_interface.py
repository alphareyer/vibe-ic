"""Typed Comportable interfaces stay logical through actual L1/L9 consumers."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase1_doc_one_shot_runner as p1
import phase2_scaffold_gen as scaffold

ORIGINAL = (Path(__file__).parent / "fixtures/phase1_local/issue3200_comportable_typed/interfaces.md").read_text()


def _emit(project, text):
    docs = {"interfaces.md": text}
    first = p1.gen_l1_datasheet(project, docs)
    l1 = json.loads(Path(first.path).read_text())
    ninth = p1.gen_l9_integration_spec(project, docs, {})
    l9 = json.loads(Path(ninth.path).read_text())
    return l1, l9


def _bus(rows):
    found = [row for row in rows if row["name"] == "bus"]
    assert len(found) == 1, rows
    return found[0]


def _typed(row, count, role, package="transport_pkg::transport"):
    assert not (row.get("mode") == "inout" and row.get("width") == 1), "STRUCT_BUNDLE_SCALARIZED"
    assert row["mode"] == "interface", row
    assert row["interface_type"] == "req_rsp", row
    assert row["interface_role"] == role, row
    assert row.get("package_struct") == package, "STRUCT_IDENTITY_LOST"
    assert row.get("instance_count") == count, row
    assert row.get("width") is None and row["width_unresolved"] is True, row
    assert row.get("msb") is None and row.get("lsb") is None, row


@pytest.mark.parametrize("count,role", [(1, "rsp"), (3, "req")], ids=["original", "instance-array"])
def test_typed_bundle_reaches_l1_both_l9_paths_and_refuses_physical_render(tmp_path, count, role):
    text = ORIGINAL if count == 1 else ORIGINAL.replace("| rsp | 1 |", "| req | 3 |").replace("Bus Device", "Bus Host")
    l1, l9 = _emit(tmp_path, text)
    _typed(_bus(l1["pin_table"]), count, role)
    for key in ("top_ports", "ports", "top_module_pins"):
        _typed(_bus(l9[key]), count, role)
    late = p1._promote_l1_pins_to_l9_ports(l1["pin_table"], l1.get("ic_name"))
    _typed(_bus(late), count, role)
    mirrored = {}
    assert p1._v1_6_555_crosswalk_l9_ports_to_l1_pin_table(mirrored, late)
    _typed(_bus(mirrored["pin_table"]), count, role)
    for channels in ([], [{"name": "bus[7:0]", "direction_master": "inout"}]):
        signals = scaffold.derive_signals({"channels": channels}, l9, include_synthesis_handoff=False)
        _typed(_bus(signals), count, role)
        with pytest.raises(scaffold.UnresolvedPortWidth, match="bus"):
            scaffold.emit_top_v("packet_engine", signals, "packet_engine")
    print("TYPED_INTERFACE_OBSERVATION " + json.dumps({"l1": l1, "l9": l9, "late": late, "mirror": mirrored}, sort_keys=True))


def test_explicit_physical_scalar_inout_and_vector_ports_remain_physical(tmp_path):
    text = ("| Signal | Direction | Width | Description |\n"
            "|:--|:--|--:|:--|\n"
            "| ready_o | output | 1 | Scalar ready flag |\n"
            "| pad_io | inout | 1 | Physical bidirectional pad |\n"
            "| payload_i | input | 8 | Physical data vector |\n")
    l1, l9 = _emit(tmp_path, text)
    expected = {"ready_o": ("output", 1), "pad_io": ("inout", 1), "payload_i": ("input", 8)}
    for name, value in expected.items():
        pin = next(row for row in l1["pin_table"] if row["name"] == name)
        assert (pin["mode"], pin["width"]) == value, pin
        assert "interface_type" not in pin, pin
        signal = next(row for row in scaffold.derive_signals({}, l9, include_synthesis_handoff=False) if row["name"] == name)
        assert (signal["direction"], signal["width"]) == value, signal


def test_bus_bullet_alone_does_not_invent_a_physical_bit_or_package(tmp_path):
    text = ORIGINAL.split("## Inter-Module Signals")[0]
    l1, l9 = _emit(tmp_path, text)
    _typed(_bus(l1["pin_table"]), None, "rsp", package=None)
    _typed(_bus(l9["ports"]), None, "rsp", package=None)
    signals = scaffold.derive_signals({}, l9, include_synthesis_handoff=False)
    with pytest.raises(scaffold.UnresolvedPortWidth, match="bus"):
        scaffold.emit_top_v("packet_engine", signals, "packet_engine")


def test_bullet_after_typed_table_cannot_replace_the_richer_logical_row(tmp_path):
    bullet = "- Bus Device Interfaces (TL-UL): **`bus`**\n"
    text = ORIGINAL.replace(bullet, "") + bullet
    l1, l9 = _emit(tmp_path, text)
    _typed(_bus(l1["pin_table"]), 1, "rsp")
    _typed(_bus(l9["ports"]), 1, "rsp")


def test_selected_numeric_alias_cannot_flatten_an_unresolved_typed_bundle(tmp_path, monkeypatch):
    import _staged_top_module

    _l1, l9 = _emit(tmp_path, ORIGINAL)
    l9["selected_interface_binding"] = {"consumer_control": True}
    # This is the existing selector transport seam, not an assertion that a
    # complete source-authority selection receipt has been issued here.
    monkeypatch.setattr(_staged_top_module, "selected_interface_ports", lambda *_a, **_k: [
        {"name": "bus", "direction": "inout", "mode": "inout", "width": 1}])
    signals = scaffold.derive_signals({}, l9, project=tmp_path)
    _typed(_bus(signals), 1, "rsp")
    with pytest.raises(scaffold.UnresolvedPortWidth, match="bus"):
        scaffold.emit_top_v("packet_engine", signals, "packet_engine")
