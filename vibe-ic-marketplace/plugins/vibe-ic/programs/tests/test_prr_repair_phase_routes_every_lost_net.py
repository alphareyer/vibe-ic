"""Step 32 routes again every net its repairs took, and never runs the antenna
repair over a wireless net (fxpad note7).

MEASURED on subservient x gf180mcuD (lane fxpdn D16 arm B, 32-cand01, replayed
on a copy with fxpdn's OpenROAD build): after repair_design / repair_timing and
the legalizer, 741 signal nets had no wire -- the 709 of the changed cells, and
32 more that no changed cell touches. The ECO route put back only the 709
(DRC 0 -> 0). The 32 reached repair_antennas, whose checker builds global-route
GUIDE wires for every net without a detailed wire and, on a detailed-routed
design, never removes them: after the call 31 of them carried gcell-centre
wires (x on a gcell centre, y stepping by exactly the 16.8 um GCELLGRID step; a
clock net among them) over real routes, and the one-net antenna reroute met
1,326 whole-design violations on entry (723 Short, 451 Metal Spacing, 129 Off
Grid). D11's refusal (VIBEIC_PRR_LOST_ROUTE_REFUSED) caught it; it stays.

These run the SCRIPT'S OWN text under tclsh on the reduced odb of
test_prr_repairs_keep_repair_design_limits (real pin directions).
"""
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_prr_repairs_keep_repair_design_limits import HARNESS as _BASE, TCL, _region  # noqa: E402

#: `out_l3` (l3's output) is a real two-pin routed net on no changed cell.
HARNESS = _BASE + 'mknet out_l3\nmkinst sink3 buf_1 {A out_l3 INPUT}\n'

ECO_START = '# ECO: the nets the repair touched lose their wires and are routed again;'
ECO_END = 'vic_say "eco nets=[dict size $::vic_dirty]"'
ANT = 'set ::vic_ant_eco [check_antennas]\n'


def _procs(text):
    return _region(text, 'proc vic_needs_wire', '\n# ---- 0.')


def run_eco_set(tmp_path):
    """The dirty-set construction, on a route where `out_l3` -- on no changed
    cell -- lost its wire in the repairs, and `drv` was resized."""
    text = TCL.read_text()
    section = text[text.index(ECO_START):text.index(ECO_END)]
    script = (HARNESS + _procs(text)
              + 'set ::vic_routed_input [vic_routed_nets]\n'
              + 'set ::W(out_l3) 0\n'
              + 'set ::vic_created [list]\nset ::vic_resized [list ::inst_drv]\n'
              + 'set ::vic_moved [list]\n'
              + section
              + 'puts "DIRTY [lsort [dict keys $::vic_dirty]]"\n')
    path = tmp_path / 'eco.tcl'
    path.write_text(script)
    return subprocess.run(['tclsh', str(path)], capture_output=True, text=True, timeout=60)


def test_a_net_the_repairs_took_joins_the_eco_route(tmp_path):
    out = run_eco_set(tmp_path)
    dirty = next(ln for ln in out.stdout.splitlines() if ln.startswith('DIRTY '))
    assert 'out_l3' in dirty.split(), out.stdout + out.stderr
    assert 'hot' in dirty.split()          # the resized cell's own net
    assert 'METRIC vibeic__prr__repair__ripped_unreported 1' in out.stdout
    assert 'no changed cell touches: out_l3' in out.stdout


def test_the_antenna_repair_never_runs_over_a_wireless_net(tmp_path):
    """A net the input routed, still wireless when the antenna phase starts,
    refuses the candidate before repair_antennas can give it a guide wire."""
    text = TCL.read_text()
    start = text.index('# repair_antennas on a detailed-routed design turns')
    section = text[start:text.index(ANT)] + ANT
    script = (HARNESS + _procs(text)
              + 'set ::vic_unrouted_before [dict create]\n'
              + 'set ::W(out_l3) 0\n'
              + 'proc repair_antennas {args} { puts "REPAIR_ANTENNAS_RAN" }\n'
              + section + 'repair_antennas\nputs "EXIT 0"\n')
    path = tmp_path / 'ant.tcl'
    path.write_text(script)
    out = subprocess.run(['tclsh', str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 1, out.stdout + out.stderr
    assert 'LL_PRR_ANTENNA_REPAIR_ON_UNROUTED_NETS: 1 signal net(s)' in out.stderr
    assert 'out_l3' in out.stderr
    assert 'REPAIR_ANTENNAS_RAN' not in out.stdout


def test_a_net_the_input_already_lacked_does_not_block_the_antenna_phase(tmp_path):
    text = TCL.read_text()
    start = text.index('# repair_antennas on a detailed-routed design turns')
    section = text[start:text.index(ANT)] + ANT
    script = (HARNESS + _procs(text)
              + 'set ::W(out_l3) 0\n'
              + 'set ::vic_unrouted_before [dict create out_l3 1]\n'
              + section + 'puts "EXIT 0"\n')
    path = tmp_path / 'ant2.tcl'
    path.write_text(script)
    out = subprocess.run(['tclsh', str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and 'EXIT 0' in out.stdout, out.stdout + out.stderr
