#!/usr/bin/env python3
"""N5 — the post-route PVT sweep the sign-off gate requires was MEASURED and
then thrown away by the producer's own annotation census.

MEASURED (spm x gf180mcuD as a HARDMACRO, image 0.3.83, stack33 + N4,
2026-09-28): `_emit_declared_process_sta` ran all six sections (FF/SS/TT x
setup/hold) on the routed netlist + the run's own SPEF, and OpenSTA reported
15 unannotated drivers per corner: 13 unconnected clkload/spare outputs, and
the two SUPPLY PORTS `VDD` / `VSS` of the hard-macro netlist (`inout VDD;`).
The routed DEF carries the supply only as special nets
(`- VDD ( * VNW ) ( * VDD ) + USE POWER`) with no `( PIN VDD )` and no PINS
row, so `sta_annotation_population.classify` bound nothing to the port and
left it REQUIRED_OR_UNKNOWN; the whole sweep was refused and renamed
`.attempt-<uuid>`; `sta_record` R6 then found FF-setup / SS-hold / TT
NOT_MEASURED, the pre-stream gate failed, and no GDS was streamed.

With the report promoted, the UNCHANGED gate returns PASS (checked on the run's
own bytes). The gate is not touched here; these tests pin the producer:

  1. a top-level port whose OWN net the DEF types USE POWER/GROUND (exactly
     one statement) is supply, not signal parasitics; conflicting or signal
     typing stays unknown;
  2. with such ports, the sweep is PROMOTED and the unchanged gate's R6 finds
     every declared (corner, check) measured post-route;
  3. every section names the sha256 of the liberty / SPEF / netlist / SDC it
     timed, and one record row per (declared corner, check) is written --
     MEASURED with its slack and sources, or NOT_MEASURED with the reason.

chip-, PDK- and vendor-AGNOSTIC: the fixture is synthetic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import phase3_one_shot_runner as p3
import sta_corner_record_completeness_check as gate
from sta_annotation_population import classify
from test_declared_process_sta_producer import scene  # noqa: F401  (fixture)

_CENSUS_WITH_SUPPLY_PORTS = (
    "STA_LINK_INSTANCE u1 core/INV\n"
    "STA_LINK_CENSUS total=1 linked=1 missing=0\n"
    "Found 2 unannotated drivers.\n VDD\n VSS\n"
    "Found 0 partially unannotated drivers.\n")


def _def(tmp: Path, vdd_use="POWER", vss_use="GROUND", extra="") -> Path:
    d = tmp / "phase3/stage3/pnr/dut.def"
    d.parent.mkdir(parents=True, exist_ok=True)
    special = [f"- VDD ( * VDD ) + USE {vdd_use} ;",
               f"- VSS ( * VSS ) + USE {vss_use} ;"] + ([extra] if extra else [])
    d.write_text(
        "DESIGN dut ;\n"
        "PINS 1 ;\n- clk + NET clk + DIRECTION INPUT + USE SIGNAL ;\nEND PINS\n"
        "COMPONENTS 1 ;\n- u1 INV ;\nEND COMPONENTS\n"
        f"SPECIALNETS {len(special)} ;\n" + "\n".join(special) + "\nEND SPECIALNETS\n"
        "NETS 1 ;\n- clk ( PIN clk ) ( u1 A ) + USE SIGNAL ;\nEND NETS\n")
    return d


# ── 1. the classifier ────────────────────────────────────────────────────────

def test_a_supply_port_typed_by_its_own_special_net_is_not_signal_parasitics(tmp_path):
    """RED on main: VDD/VSS stay REQUIRED_OR_UNKNOWN and `complete` is False."""
    res = classify(_CENSUS_WITH_SUPPLY_PORTS, _def(tmp_path))
    got = {r["driver"]: r["classification"] for r in res["drivers"]}
    assert got == {"VDD": "EXPLICIT_PG_NOT_SIGNAL_PARASITICS",
                   "VSS": "EXPLICIT_PG_NOT_SIGNAL_PARASITICS"}, res
    assert res["complete"] is True


def test_a_port_whose_net_is_typed_signal_stays_unknown(tmp_path):
    res = classify(_CENSUS_WITH_SUPPLY_PORTS, _def(tmp_path, vdd_use="SIGNAL"))
    got = {r["driver"]: r["classification"] for r in res["drivers"]}
    assert got["VDD"] == "REQUIRED_OR_UNKNOWN" and res["complete"] is False


def test_conflicting_typing_of_the_ports_net_stays_unknown(tmp_path):
    res = classify(_CENSUS_WITH_SUPPLY_PORTS,
                   _def(tmp_path, extra="- VDD ( * VPW ) + USE GROUND ;"))
    got = {r["driver"]: r["classification"] for r in res["drivers"]}
    assert got["VDD"] == "REQUIRED_OR_UNKNOWN" and res["complete"] is False


def test_a_port_with_no_net_of_its_name_stays_unknown(tmp_path):
    body = _CENSUS_WITH_SUPPLY_PORTS.replace(" VSS\n", " VDDX\n")
    res = classify(body, _def(tmp_path))
    got = {r["driver"]: r["classification"] for r in res["drivers"]}
    assert got["VDDX"] == "REQUIRED_OR_UNKNOWN" and res["complete"] is False


# ── 2+3. the producer, driven end to end with a Tcl-faithful double ──────────

def _native_double(census: str):
    """Writes what the Tcl ASKS for: every `puts $_f {...}` header line the
    script emits, the census after the SETUP header, and one slack line per
    requested check. A CPU stand-in for `sta`, like the sibling fixture's."""
    def run(container, cmd, **kw):
        tcl = Path(kw["marker"]).read_text()
        out = Path(kw["isolate"][0])
        out.parent.mkdir(parents=True, exist_ok=True)
        chunks = re.split(r"set _f \[open \{[^}]*\} (?:w|a)\]\n", tcl)[1:]
        text = ""
        for ch in chunks:
            head = ch.split("close $_f", 1)[0]
            lines = re.findall(r"^puts \$_f \{(.*)\}$", head, re.M)
            text += "\n".join(lines) + "\n"
            if lines and lines[0].startswith("=== SETUP"):
                text += census + "worst slack max 1.25\ntns max 0.00\n"
            elif lines and lines[0].startswith("=== HOLD"):
                text += "worst slack min 0.40\ntns min 0.00\n"
        mode = "a" if out.exists() else "w"
        with out.open(mode) as f:
            f.write(text)
        return 0, "", ""
    return run


def _stage_gate_inputs(tmp: Path, by: dict) -> None:
    """The run artefacts the gate reads besides the sweep: the PVT matrix
    (step 7) and the process stance, as the flow writes them."""
    m = tmp / "phase2/stage2/constraints/pvt_matrix.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"corners": [
        {"name": Path(by[c]).stem, "label": c, "liberty": by[c]}
        for c in ("SS", "TT", "FF")], "primary_corner": "TT"}))
    st = tmp / "reports/phase3/mcorner_ocv_stance.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text(json.dumps({"setup_process_corner": "SS",
                              "hold_process_corner": "FF"}))


def test_supply_ports_no_longer_quarantine_the_sweep_and_the_gate_sees_every_corner(
        scene, monkeypatch):
    """RED on main: the sweep is refused, `sta_spef_based.rpt` is never
    promoted, and R6 names every required (corner, check) NOT_MEASURED."""
    s = scene
    _def(s["tmp_path"])
    monkeypatch.setattr(p3, "_docker_exec", _native_double(_CENSUS_WITH_SUPPLY_PORTS))
    assert s["emit"](), "the complete sweep was refused"
    assert s["rpt"].is_file()
    _stage_gate_inputs(s["tmp_path"], s["by"])
    decl = gate.read_declarations(s["tmp_path"])
    assert set(decl["required_pvt_corners"]) == {"SS", "TT", "FF"}
    recs = gate.read_records(s["tmp_path"], decl)
    for c in ("SS", "TT", "FF"):
        rec = recs[gate._key(gate.AXIS_PROCESS, c)]
        assert rec["basis_used"].get("setup_wns_ns") == gate.BASIS_SIGNOFF, rec
        assert rec["basis_used"].get("hold_wns_ns") == gate.BASIS_SIGNOFF, rec


def test_each_section_names_the_sha256_of_what_it_timed_and_the_record_has_every_row(
        scene, monkeypatch):
    """RED on main: no digest lines, and no per-(corner, check) record."""
    s = scene
    _def(s["tmp_path"])
    monkeypatch.setattr(p3, "_docker_exec", _native_double(_CENSUS_WITH_SUPPLY_PORTS))
    assert s["emit"]()
    text = s["rpt"].read_text()
    spef_sha = p3._file_sha256(s["spef"]).split(":", 1)[1]
    for kind in ("LIBERTY", "SPEF", "NETLIST", "SDC"):
        assert f"STA_BASIS_SHA256_{kind}:" in text
    assert f"STA_BASIS_SHA256_SPEF: {spef_sha}" in text
    rec = json.loads((s["tmp_path"] / "reports/phase3/sta/declared_process_sta.json").read_text())
    assert rec["status"] == "MEASURED"
    for c in ("SS", "TT", "FF"):
        lib_sha = p3._file_sha256(Path(s["by"][c])).split(":", 1)[1]
        for role, wns in (("setup", 1.25), ("hold", 0.40)):
            row = rec["corners"][c][role]
            assert row["status"] == "MEASURED" and row["wns_ns"] == wns, row
            assert row["spef_sha256"] == spef_sha and row["spef_rc_corner"] == "nom"
            assert row["liberty"] == s["by"][c] and row["liberty_sha256"] == lib_sha


def test_a_refused_sweep_records_every_corner_not_measured_with_the_reason(
        scene, monkeypatch):
    """RED on main (no record). A supply port the DEF does NOT type is still
    refused -- and the reason, naming the driver, is in the record for every
    (corner, check), not only in a transient note."""
    s = scene
    _def(s["tmp_path"], vdd_use="SIGNAL")
    monkeypatch.setattr(p3, "_docker_exec", _native_double(_CENSUS_WITH_SUPPLY_PORTS))
    assert not s["emit"]()
    rec = json.loads((s["tmp_path"] / "reports/phase3/sta/declared_process_sta.json").read_text())
    assert rec["status"] == "NOT_MEASURED" and "VDD" in rec["reason"], rec
    for c in ("SS", "TT", "FF"):
        for role in ("setup", "hold"):
            row = rec["corners"][c][role]
            assert row["status"] == "NOT_MEASURED" and row["wns_ns"] is None
            assert "annotation census" in row["reason"]


def test_a_missing_spef_records_not_measured_with_its_reason(scene):
    """The corner whose SPEF was not extracted is NOT_MEASURED, reason stated."""
    s = scene
    s["spef"].unlink()
    assert not s["emit"]()
    rec = json.loads((s["tmp_path"] / "reports/phase3/sta/declared_process_sta.json").read_text())
    assert rec["status"] == "NOT_MEASURED" and "SPEF" in rec["reason"]
    assert {r["status"] for c in rec["corners"].values() for r in c.values()} == {"NOT_MEASURED"}
