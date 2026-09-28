"""Step 32 (Vibeic.PostRouteRepair) hands back every route its antenna repair took (cmp3 D11).

THE DEFECT. spm x gf180mcuD (lane fxpad, D7+D1 stack, image 0.3.83): pnr
failed POSTROUTE_UNROUTED -- `_vibeic_aux_tie_0040` (tie cell ZN -> pad
u_pad_x_22 PD) and `net47` (step 32's own buffer fanout44 -> 4 core sinks)
carried no wire. Replaying 32-cand01 on its own input, instrumented, measured:

    after the ECO route         unrouted signal nets = 0
    after repair_antennas       unrouted = 4: _vibeic_aux_tie_0040 net65 net47 net48
    grt::repaired_net_names     net65 net48      (the diodes' nets, the same two)
    antenna_route -nets         net48 net65
    before re-verify            unrouted = 2: _vibeic_aux_tie_0040 net47
    PRR: reverify route_drc=0 antenna_nets=0     (ANT-0018 skips a wireless net)

OpenROAD's `repair_antennas` (updateDirtyNets destroys the wire of every
dirty net, reports only the ones it re-routes) took two routes it never
named, the script routed only the named ones, and its re-verify could not
see the loss. Both nets were routed in step 21; neither is a pad-access or
tie-placement problem.

These tests run the SCRIPT'S OWN text -- from the post-ECO antenna census to
the end of re-verify, the same markers on main and on the fix -- under tclsh
against a reduced odb whose `repair_antennas` does what the tool measurably
did. Only the tool is modelled.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
TCL = PROGRAMS / 'librelane_plugins' / 'librelane_plugin_vibeic' / 'postroute_repair.tcl'

START = 'set ::vic_ant_eco [check_antennas]\n'
END = 'if {$::vic_fillers > 0} {'

#: The reduced odb and tool. `repair_antennas` inserts two diodes on the two
#: violating nets and takes the wire of four, exactly as measured. The ECO
#: route routes every net it is handed that `::UNROUTABLE` does not name.
HARNESS = r'''
set ::nets [dict create]
proc mknet {name {sig SIGNAL} {terms 2} {abut 0}} {
    set obj ::net_[dict size $::nets]
    dict set ::nets $name $obj
    set ::W($name) 1
    interp alias {} $obj {} netcall $name $sig $terms $abut
}
proc netcall {name sig terms abut method args} {
    switch -- $method {
        getName { return $name }
        getSigType { return $sig }
        isSpecial { return [expr {$sig in {POWER GROUND}}] }
        getITerms { return [lrepeat $terms x] }
        getBTerms { return {} }
        isConnectedByAbutment { return $abut }
        getWire { return [expr {$::W($name) ? "wire_$name" : "NULL"}] }
        default { error "net $name: $method" }
    }
}
set ::insts [list]
proc mkinst {name net} {
    set obj ::inst_$name
    lappend ::insts $obj
    interp alias {} $obj {} instcall $name $net
    interp alias {} ::it_$name {} iterm $net
}
proc instcall {name net method args} {
    switch -- $method {
        getName { return $name }
        getPlacementStatus { return PLACED }
        setPlacementStatus { return }
        getITerms { return [list ::it_$name] }
        default { error "inst $name: $method" }
    }
}
proc iterm {net method} { return [dict get $::nets $net] }
proc blk {method args} {
    switch -- $method {
        getNets { return [dict values $::nets] }
        getInsts { return $::insts }
        findNet {
            set n [lindex $args 0]
            return [expr {[dict exists $::nets $n] ? [dict get $::nets $n] : "NULL"}]
        }
        default { error "block: $method" }
    }
}
set ::block blk

foreach n {netA netB netC tie0 other} { mknet $n }
# A supply net's straps are special wiring, never a dbWire; a pad net joined by
# abutment needs none; `stub` is a net the INPUT already had unrouted.
mknet VDD POWER 9
set ::W(VDD) 0
mknet pad_x SIGNAL 2 1
set ::W(pad_x) 0
mknet stub
set ::W(stub) 0
mkinst u1 other

proc repair_antennas {args} {
    mkinst ANTENNA_1 netA
    mkinst ANTENNA_2 netB
    foreach n {netA netB netC tie0} { set ::W($n) 0 }
}
namespace eval grt { proc repaired_net_names {} { return {netA netB} } }
proc log_cmd {cmd args} { if {$cmd eq "repair_antennas"} { repair_antennas {*}$args } }
set ::ANT {2 0}
proc check_antennas {} { set v [lindex $::ANT 0]; set ::ANT [lrange $::ANT 1 end]; return $v }
proc append_if_exists_argument {args} {}
proc check_placement {args} {}
proc global_connect {args} {}
namespace eval utl { proc metric_integer {name value} { puts "METRIC $name $value" } }
proc vic_say {line} { puts "PRR: $line" }
proc vic_eco_route {varname tag} {
    upvar #0 $varname dirty
    puts "ROUTED $tag [lsort [dict keys $dirty]]"
    foreach n [dict keys $dirty] { if {$n ni $::UNROUTABLE} { set ::W($n) 1 } }
    return 1
}
rename exit _exit
proc exit {{code 0}} { puts "EXIT $code"; _exit $code }

set ::env(DIODE_CELL) diode/I
set ::env(VIBEIC_PRR_ANTENNA_REPAIR) 1
set ::env(PL_MAX_DISPLACEMENT_X) 500
set ::env(PL_MAX_DISPLACEMENT_Y) 100
set ::env(STEP_DIR) [pwd]
set ::vic_ant_before 0
set ::vic_created [list]
set ::vic_unrouted_before [dict create stub 1]
set ::vic_fillers 0
'''


def _script_procs(text):
    """The script's census procs, when it has them (the fix's); main has none."""
    start = text.find('proc vic_needs_wire')
    if start < 0:
        return ''
    return text[start:text.index('\n# ---- 0.', start)]


def run(tmp_path, unroutable=()):
    text = TCL.read_text()
    section = text[text.index(START):text.index(END)]
    script = (HARNESS + f'set ::UNROUTABLE {{{" ".join(unroutable)}}}\n'
              + _script_procs(text)
              # the input census, by the script's own instrument when it has one
              + 'if {[llength [info commands vic_unrouted_nets]]} '
                '{ set ::vic_unrouted_before [vic_unrouted_nets] }\n'
              + section + 'puts "EXIT 0"\n')
    path = tmp_path / 'prr.tcl'
    path.write_text(script)
    return subprocess.run(['tclsh', str(path)], capture_output=True, text=True,
                          cwd=tmp_path, timeout=60)


def routed(out, tag):
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith(f'ROUTED {tag} ')]
    assert len(lines) == 1, out.stdout + out.stderr
    return lines[0].split()[2:]


def test_every_net_the_antenna_repair_took_is_routed_again(tmp_path):
    out = run(tmp_path)
    assert routed(out, 'antenna_route') == ['netA', 'netB', 'netC', 'tie0'], out.stdout
    assert 'EXIT 0' in out.stdout, out.stdout + out.stderr
    assert 'METRIC vibeic__prr__antenna__ripped_unreported 2' in out.stdout
    assert 'did not report: netC tie0' in out.stdout
    assert 'METRIC vibeic__prr__unrouted__added 0' in out.stdout


def test_a_route_the_candidate_cannot_hand_back_refuses_it(tmp_path):
    out = run(tmp_path, unroutable=('tie0',))
    assert out.returncode == 1, out.stdout + out.stderr
    assert 'VIBEIC_PRR_LOST_ROUTE_REFUSED: 1 signal net(s)' in out.stderr
    assert 'tie0' in out.stderr
    assert 'METRIC vibeic__prr__unrouted__added 1' in out.stdout


def test_a_net_the_input_already_lacked_is_not_the_candidates_loss(tmp_path):
    """`stub` was unrouted in the INPUT: counted, not refused -- the input's
    route is judged by step 21, not by this candidate. The pad net joined by
    abutment and the supply net need no dbWire and are not counted at all."""
    out = run(tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert 'METRIC vibeic__prr__after__unrouted__count 1' in out.stdout, out.stdout
    assert 'METRIC vibeic__prr__unrouted__added 0' in out.stdout
    assert 'pad_x' not in ' '.join(ln for ln in out.stdout.splitlines()
                                   if 'unrouted' in ln or 'ROUTED' in ln)


def test_the_census_is_taken_before_the_repair_and_checked_after_the_reroute():
    text = TCL.read_text()
    code = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith('#')]

    def at(fragment):
        hits = [i for i, ln in enumerate(code) if fragment in ln]
        assert hits, fragment
        return hits[0]
    assert at('set routed [vic_routed_nets]') < at('log_cmd repair_antennas') \
        < at('vic_lost_routes $routed') < at('vic_eco_route ::vic_ant_dirty antenna_route') \
        < at('set ::vic_unrouted_after [vic_unrouted_nets]') < at('write_views')
    # the input census is the baseline the loss is measured against
    assert at('set ::vic_unrouted_before [vic_unrouted_nets]') < at('log_cmd repair_design')


def _global_route_loss_replay(tmp_path, *, unrouteable=(), loss_phase='global_route'):
    """Run the shipped ECO proc with a GRT side effect seen on the routed chip.

    The tool's grt.tcl drops an existing wire outside the original ECO set;
    detailed_route routes exactly the names passed to -nets.
    """
    body = TCL.read_text()
    start = body.index('proc vic_eco_route {varname tag}')
    end = body.index('\nset ::vic_eco_attempts 0', start)
    grt = tmp_path / 'openroad/common/grt.tcl'
    grt.parent.mkdir(parents=True)
    assert loss_phase in {'global_route', 'repair'}
    grt.write_text('set ::W(other) 0\n' if loss_phase == 'global_route' else '')
    script = (HARNESS + _script_procs(body) + r'''
rename netcall original_netcall
proc netcall {name sig terms abut method args} {
    if {$method eq "setWireOrdered"} { return }
    return [original_netcall $name $sig $terms $abut $method {*}$args]
}
namespace eval odb {
    proc dbWire_destroy {wire} { set ::W([string range $wire 5 end]) 0 }
}
proc set_thread_count {n} {}
proc log_cmd {command args} {
    if {$command ne "detailed_route"} { error "unexpected command $command" }
    set i [lsearch -exact $args -nets]
    set nets [lindex $args [expr {$i + 1}]]
    foreach n $nets {
        if {$n ni $::UNROUTABLE} { set ::W($n) 1 }
    }
    set j [lsearch -exact $args -output_drc]
    close [open [lindex $args [expr {$j + 1}]] w]
}
set ::env(SCRIPTS_DIR) [pwd]
set ::env(STEP_DIR) [pwd]
set ::env(DRT_THREADS) 1
set ::env(DRT_OPT_ITERS) 1
set ::env(VIBEIC_PRR_ECO_EXPANSIONS) 2
set ::vic_dirty [dict create netA [dict get $::nets netA]]
set ::vic_eco_attempts 0
'''.replace('set ::vic_dirty',
            f'set ::UNROUTABLE {{{" ".join(unrouteable)}}}\n'
            'set ::vic_routed_before [vic_routed_nets]\n'
            + ('set ::W(other) 0\n' if loss_phase == 'repair' else '')
            + 'set ::vic_dirty')
              + body[start:end]
              + '\nset outcome [vic_eco_route ::vic_dirty eco_route]\n'
                'puts "RESULT $outcome $::W(other) [lsort [dict keys $::vic_dirty]]"\n')
    deck = tmp_path / 'eco.tcl'
    deck.write_text(script)
    return subprocess.run(['tclsh', str(deck)], capture_output=True, text=True,
                          cwd=tmp_path, timeout=30)


def test_global_route_loss_joins_the_scoped_eco_route(tmp_path):
    out = _global_route_loss_replay(tmp_path)
    assert out.returncode == 0, out.stderr
    assert 'RESULT 1 1 netA other' in out.stdout, out.stdout
    assert (tmp_path / 'eco_route.drc').is_file()


def test_repair_tool_loss_joins_the_scoped_eco_route(tmp_path):
    out = _global_route_loss_replay(tmp_path, loss_phase='repair')
    assert out.returncode == 0, out.stderr
    assert 'RESULT 1 1 netA other' in out.stdout, out.stdout


def test_unrecoverable_global_route_loss_refuses_the_candidate(tmp_path):
    out = _global_route_loss_replay(tmp_path, unrouteable=('other',))
    assert out.returncode == 0, out.stderr
    assert 'RESULT 0 0 netA other' in out.stdout, out.stdout
    assert not (tmp_path / 'eco_route.drc').exists()
