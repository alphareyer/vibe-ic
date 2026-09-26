"""A1 and A2 gates read the producer's own record, not just its vocabulary
(T94, A1 harvest #2, A2 harvest #3).

A1: `analog_a1_spec_emit` stamps `_provenance.fields_defaulted` (always empty
from that producer). A spec.json whose record names a defaulted field carries
a value no document stated — the class that graded a bandgap against 1.205 V
— and the gate PASSED it on the shape of `specs[]` alone.

A2: the gate PASSED any topology.md over 200 bytes naming one circuit word.
When the block's `topology.json` IR exists (A3 renders from it), its
structure is now checked. MEASURED on vibeic-eda 0.3.77 /
u_hawaii_adc/delta_sigma: 8 nets its devices touch were never declared, so
A3's per-internal-net rail measurement never measured them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path

PROGRAMS = Path(_plugin_tree.plugin_path("programs"))


def _gate(prog, project):
    cp = subprocess.run([sys.executable, str(PROGRAMS / prog), str(project),
                         "--block", "blk"], capture_output=True, text=True)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def _project(tmp_path):
    p = tmp_path / "proj"
    b = p / "phase3" / "analog" / "blk"
    b.mkdir(parents=True)
    (p / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": [{"name": "blk", "type": "ldo"}]}))
    return p, b


def _spec(b, defaulted):
    (b / "spec.json").write_text(json.dumps({
        "block": "blk", "specs": [{"name": "vout", "target": 1.2}],
        "_provenance": {"producer": "analog_a1_spec_emit",
                        "fields_bound": ["vout"],
                        "fields_defaulted": defaulted,
                        "defaults_used": bool(defaulted)}}))


def test_a1_refuses_a_spec_with_a_defaulted_field(tmp_path):
    p, b = _project(tmp_path)
    _spec(b, ["vref"])
    rc, out = _gate("analog_a1_spec_extract_check.py", p)
    assert rc == 1 and "A1_SPEC_FIELDS_DEFAULTED" in out, out


def test_a1_passes_a_fully_bound_spec(tmp_path):
    p, b = _project(tmp_path)
    _spec(b, [])
    rc, out = _gate("analog_a1_spec_extract_check.py", p)
    assert rc == 0, out


MEMO = ("# Topology - blk\n\nAn NMOS-input amplifier drives a PMOS pass "
        "transistor closed by a resistor divider; Miller compensated. " * 3)


def _ir(b, devices, internal):
    (b / "topology.md").write_text(MEMO)
    (b / "topology.json").write_text(json.dumps({
        "ir_schema": 1, "block": "blk", "ports": ["vin", "vss", "vout"],
        "rails": {"vdd": "vin", "vss": "vss"}, "internal_nets": internal,
        "role_terminals": {"nmos": 4, "res": 3}, "devices": devices}))


GOOD = [{"name": "m1", "role": "nmos", "nets": ["vout", "ng", "vss", "vss"]},
        {"name": "r1", "role": "res", "nets": ["vin", "ng", "vss"]}]


def test_a2_refuses_a_device_net_the_ir_never_declares(tmp_path):
    p, b = _project(tmp_path)
    _ir(b, GOOD + [{"name": "m2", "role": "nmos",
                    "nets": ["vout", "nhidden", "vss", "vss"]}], ["ng"])
    rc, out = _gate("analog_a2_topology_select_check.py", p)
    assert rc == 1 and "A2_TOPOLOGY_IR_NET_UNDECLARED" in out, out
    assert "nhidden" in out


def test_a2_refuses_a_wrong_terminal_count_and_a_dangling_net(tmp_path):
    p, b = _project(tmp_path)
    _ir(b, [{"name": "m1", "role": "nmos", "nets": ["vout", "ng", "vss"]},
            GOOD[1]], ["ng", "nfloat"])
    rc, out = _gate("analog_a2_topology_select_check.py", p)
    assert rc == 1, out
    assert "A2_TOPOLOGY_IR_TERMINALS" in out and "A2_TOPOLOGY_IR_NET_UNUSED" in out


def test_a2_passes_a_structurally_sound_ir(tmp_path):
    p, b = _project(tmp_path)
    _ir(b, GOOD, ["ng"])
    rc, out = _gate("analog_a2_topology_select_check.py", p)
    assert rc == 0, out


def test_the_delta_sigma_library_entry_declares_every_net_it_touches():
    """The producer half: the library entry whose emitted IR the gate refused
    now declares the nets its devices touch (the emitted IR then passes the
    structural check — measured on the fresh 0.3.77 emission)."""
    import analog_a2_topology_emit as T
    entries = [e for e in T.LIBRARY.values() if isinstance(e, dict)
               and "nqz" in (e.get("internal_nets") or [])]
    assert len(entries) == 1
    internal = set(entries[0]["internal_nets"])
    touched = {n for d in entries[0].get("devices") or []
               for n in (d.get("nets") or []) if isinstance(n, str)}
    for net in ("nd1_cm", "nd2_cm", "ntail_cm", "nvcmr", "nqd1", "nqstb",
                "ndi_n", "ndi_p"):
        assert net in touched and net in internal, net
