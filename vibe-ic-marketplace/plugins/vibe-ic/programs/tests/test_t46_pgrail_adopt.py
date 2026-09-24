"""The adopted route and the preroute PG connect retain their own state."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def _tcl(body: str, tmp_path: Path):
    assert shutil.which("tclsh")
    script = tmp_path / "probe.tcl"
    script.write_text(body)
    return subprocess.run(["tclsh", str(script)], text=True, cwd=tmp_path,
                          capture_output=True, check=False)


def test_preroute_pg_connect_does_not_query_an_unstarted_detail_router(tmp_path):
    # DRT-0002 came from this counter call, before the first detailed route.
    prefix = R._build_pg_reconnect_tcl(reroute=False).split("set _pg_dnt {}")[0]
    r = _tcl("set calls 0\n"
             "proc detailed_route_num_drvs {} {incr ::calls; error DRT-0002}\n"
             + prefix + "puts \"T46_COUNTER_CALLS $calls\"\n", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "T46_COUNTER_CALLS 0" in r.stdout
    assert "PG_DELTA_DRC_NOT_APPLICABLE" in r.stdout


def test_adopt_refuses_a_wire_lost_before_antenna(tmp_path):
    # One accepted candidate net had a wire; the resumed DB lost it. Drive
    # the emitted Tcl with only the ODB accessor results supplied by the tool.
    mock = ("array set _vic_adopt_wire {signal 12}\n"
            "namespace eval ord {proc get_db_block {} {return blk}}\n"
            "proc blk {method} {return {net}}\n"
            "proc net {method} {switch -- $method {isSpecial {return 0} "
            "getName {return signal} getWire {return NULL}}}\n")
    r = _tcl(mock + R._adopt_route_presence_guard_tcl(), tmp_path)
    assert r.returncode != 0
    assert "ADOPT_ROUTE_LOST" in r.stderr


def test_adopt_keeps_an_intact_wire(tmp_path):
    mock = ("array set _vic_adopt_wire {signal 12}\n"
            "namespace eval ord {proc get_db_block {} {return blk}}\n"
            "proc blk {method} {return {net}}\n"
            "proc net {method} {switch -- $method {isSpecial {return 0} "
            "getName {return signal} getWire {return wire}}}\n"
            "proc wire {method} {return 12}\n")
    r = _tcl(mock + R._adopt_route_presence_guard_tcl(), tmp_path)
    assert r.returncode == 0, r.stderr
    assert "shrank=0" in r.stdout


def test_antenna_boundary_refuses_an_unrouted_db(tmp_path):
    mock = ("namespace eval ord {proc get_db_block {} {return blk}}\n"
            "proc blk {method} {return {net}}\n"
            "proc net {method} {switch -- $method {getSigType {return SIGNAL} "
            "getITerms {return {t1 t2}} getBTerms {return {}} "
            "getWire {return NULL} getName {return signal}}}\n"
            "proc t1 {method} {return {{ly r1}}}\n"
            "proc t2 {method} {return {{ly r2}}}\n"
            "proc ly {method} {return M1}\n"
            "proc r1 {method} {switch -- $method {xMin {return 0} "
            "yMin {return 0} xMax {return 10} yMax {return 10}}}\n"
            "proc r2 {method} {switch -- $method {xMin {return 20} "
            "yMin {return 0} xMax {return 30} yMax {return 10}}}\n")
    r = _tcl(mock + R._unrouted_probe_tcl("after_postroute_antenna_repair",
                                        str(tmp_path))
             + R._unrouted_boundary_guard_tcl("after_postroute_antenna_repair"),
             tmp_path)
    assert r.returncode != 0
    assert "POSTROUTE_UNROUTED" in r.stderr


def test_both_antenna_boundaries_gate_before_the_next_stage():
    src = Path(R.__file__).read_text()
    for tag in ("after_postroute_antenna_repair",
                "after_postroute_antenna_reconverge"):
        probe = f'_unrouted_probe_tcl("{tag}", out_dir_c)'
        guard = f'_unrouted_boundary_guard_tcl("{tag}")'
        assert src.index(probe) < src.index(guard)
        assert guard in src
    assert '_wire_content_census_tcl("_vic_adopt_wire") + common' in src
    assert '{spef_repair_block}{_adopt_route_presence_guard_tcl()}' in src


def test_successful_scoped_antenna_route_cannot_drop_a_held_net(tmp_path):
    # The fork reported success while three of its supposedly held nets lost
    # wire. Reuse the existing Tcl ODB emulator but make the native call return
    # success, which was the missing branch of its damage census.
    from test_r0915_121_a_pass_answers_for_every_wire_it_took import (  # noqa: E402
        _H, _tcl as antenna_tcl,
    )
    harness = (_H % {"seq": "5 5 0", "inserts": "d1", "unwire": "nX",
                     "shrink": ""}).replace(
                         'error "DRT-0206 checkConnectivity"', 'return ""')
    r = _tcl(harness + antenna_tcl(), tmp_path)
    assert r.returncode != 0
    assert "ANTENNA_SCOPED_HELD_WIRE_DAMAGE" in r.stdout
    assert "ANTENNA_DIODE_ROLLED_BACK" in r.stdout
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST:" in r.stdout
    assert "nX" in r.stdout


def test_successful_scoped_antenna_route_keeps_an_intact_held_net(tmp_path):
    from test_r0915_121_a_pass_answers_for_every_wire_it_took import (  # noqa: E402
        _H, _tcl as antenna_tcl,
    )
    harness = (_H % {"seq": "5 5 0", "inserts": "d1", "unwire": "",
                     "shrink": ""}).replace(
                         'error "DRT-0206 checkConnectivity"', 'return ""')
    r = _tcl(harness + antenna_tcl(), tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ANTENNA_SCOPED_HELD_WIRE_DAMAGE" not in r.stdout
