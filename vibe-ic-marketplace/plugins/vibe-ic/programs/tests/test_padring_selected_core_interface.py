"""The routed core interface must survive the producer's pad wrapper."""
import json
from pathlib import Path
import pytest
import phase3_one_shot_runner as r


def fixture(tmp_path, connected, *, extra="", core_ports="input a, input scan_clock, output z"):
    netlist = tmp_path / "selected.v"
    wrapper = tmp_path / "wrapper.v"
    netlist.write_text(f"module logic_core ({core_ports});\nendmodule\n")
    wrapper.write_text("module physical_top(input a, input scan_clock, output z);\n"
                       f"logic_core u_core ({connected}{extra});\nendmodule\n")
    return netlist, wrapper


@pytest.mark.parametrize("connections", [
    ".a(a), .z(z)",
    ".a(a), .scan_clock(), .z(z)",
    ".a(a), .scan_clock(scan_clock)",
    ".a(a), .scan_clock(scan_clock), .z(z), .stray(a)",
    ".a(a), .scan_clock(scan_clock), .z(z), .z(z)",
])
def test_omitted_empty_unknown_or_duplicate_port_refuses(tmp_path, connections):
    n, w = fixture(tmp_path, connections)
    with pytest.raises(ValueError, match="PADRING_CORE_PORT_CONNECTION_MISMATCH"):
        r._validate_padring_core_connections(n, w, "logic_core", "physical_top")


def test_complete_selected_interface_is_preserved(tmp_path):
    n, w = fixture(tmp_path, ".a(a), .scan_clock(scan_clock), .z(z)")
    before = n.read_bytes(), w.read_bytes()
    r._validate_padring_core_connections(n, w, "logic_core", "physical_top")
    assert before == (n.read_bytes(), w.read_bytes())


def test_non_ansi_and_bus_ports_use_the_same_contract(tmp_path):
    n, w = fixture(tmp_path, ".a(a), .scan_clock(scan_clock), .z(z)")
    n.write_text("module logic_core(a, scan_clock, z);\ninput [7:0] a;\n"
                 "input scan_clock;\noutput z;\nendmodule\n")
    r._validate_padring_core_connections(n, w, "logic_core", "physical_top")


def test_implicit_core_connection_is_not_an_explicit_wrapper(tmp_path):
    n, w = fixture(tmp_path, ".*")
    with pytest.raises(ValueError, match="INTERFACE_UNRESOLVED"):
        r._validate_padring_core_connections(n, w, "logic_core", "physical_top")


def test_physical_producer_refuses_before_native_floorplan(tmp_path, monkeypatch):
    project = tmp_path / "project"
    pnr = project / "phase3/stage3/pnr"
    pnr.mkdir(parents=True)
    n, w = fixture(pnr, ".a(a), .z(z)")
    rec = {"core_module": "logic_core", "chip_top_module": "physical_top",
           "chip_top_verilog": str(w.relative_to(project))}
    monkeypatch.setattr(r, "_padring_chip_top_record", lambda p: rec)
    monkeypatch.setattr(r, "pnr_input_netlist", lambda p, top: (n, "selected", True))
    def forbidden(*a, **kw):
        raise AssertionError("native floorplan must not run with missing core ports")
    monkeypatch.setattr(r, "_docker_exec", forbidden)
    result, route = r._prepare_padring_for_route(
        project, None, "container", pnr, str(pnr),
        "read_lef tech.lef\nread_verilog selected.v\nlink_design logic_core\n"
        f'puts "{r._PNR_STAGE_MARKER} floorplan"\n'
        "write_def /work/floorplan.def\nglobal_placement\n",
        io_view_discover=lambda *a: (["io.lef"], []))
    assert result.status == "FAIL"
    assert "missing=['scan_clock']" in result.detail
    assert route is None
    assert not (pnr / "padring_floorplan_seed.tcl").exists()
