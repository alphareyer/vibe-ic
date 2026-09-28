"""Value-bearing controls for data fanout added by placement and diodes."""
from pathlib import Path
import subprocess
import textwrap

STEP = (Path(__file__).resolve().parents[1] / "librelane_plugins"
        / "librelane_plugin_vibeic")


def test_second_placement_repair_uses_the_active_sdc_and_legalizes():
    script = (STEP / "postgpl_fanout_closure.tcl").read_text()
    assert script.index("read_current_odb") < script.index("estimate_parasitics -placement")
    assert script.index("estimate_parasitics -placement") < script.index("log_cmd repair_design")
    assert script.index("log_cmd repair_design") < script.index("common/dpl.tcl")
    assert "read_sdc" not in script  # read_current_odb supplies the step's SDC


def test_logic_loads_are_rebuffered_without_stranding_antenna_diodes(tmp_path):
    """Execute the shipped Tcl proc on a neutral five-load DB model.

    The pre-fix flow has no such proc; its equivalent no-op leaves the measured
    driver at five. Move two logic inputs while retaining the antenna diodes
    on the protected net.
    """
    source = (STEP / "postroute_repair.tcl").read_text()
    marker = "proc vic_close_data_fanout {} {"
    if marker in source:
        body = source[source.index(marker):]
        body = body[:body.index("if {![vic_close_data_fanout]}")]
        helper = "proc vic_fanout_target_limits {} {"
        if helper in source:
            body = source[source.index(helper):source.index("proc vic_fanout_over {} {")] + body
    else:
        body = "proc vic_close_data_fanout {} { return 1 }\n"
    proc_file = tmp_path / "fanout.tcl"
    proc_file.write_text(body)
    harness = textwrap.dedent(r"""
        namespace eval sta { proc max_fanout_check_limit {} { return 4.0 } }
        set ::env(STEP_DIR) __STEP_DIR__
        proc report_check_types {args} {
            set f [open [lindex $args end] w]
            puts $f {driver/Z 4.0 5.0 -1.0 (VIOLATED)}
            close $f
        }
        proc get_nets {args} { return data_net }
        proc get_name {obj} { return $obj }
        namespace eval utl { proc metric_integer {args} {} }
        set ::env(DIODE_CELL) diode/I
        set ::env(SYNTH_BUFFER_CELL) buffer/I/Z
        set ::env(PL_MAX_DISPLACEMENT_X) 500
        set ::env(PL_MAX_DISPLACEMENT_Y) 100
        set ::block block
        set ::vic_created {}
        set ::vic_changed 1
        set ::root {out d1 d2 l1 l2 l3}
        set ::buffer_added 0
        set ::moved {}
        set ::routed {}
        proc block {method args} {
            switch -- $method {
                getNets { return {net0} }
                getInsts {
                    set result {driver diode1 diode2 logic1 logic2 logic3}
                    if {$::buffer_added} { lappend result newbuf }
                    return $result
                }
                findNet { if {[lindex $args 0] eq "data_net"} {return net0}; return NULL }
                findInst {
                    if {$::buffer_added && [lindex $args 0] eq "newbuf"} {return newbuf}
                    return NULL
                }
            }
        }
        proc net0 {method args} {
            switch -- $method {
                isSpecial {return 0}
                getSigType {return SIGNAL}
                getITerms {return $::root}
                getName {return data_net}
                isDoNotTouch {return 0}
            }
        }
        proc net1 {method args} {
            switch -- $method {
                getSigType {return SIGNAL}
                getName {return new_net}
                getITerms {return {newout l1 l2}}
            }
        }
        proc iterm {name method args} {
            switch -- $method {
                getIoType { if {$name in {out newout}} {return OUTPUT}; return INPUT }
                getInst {
                    return [dict get {out driver d1 diode1 d2 diode2 l1 logic1
                                      l2 logic2 l3 logic3 newin newbuf newout newbuf} $name]
                }
                getMTerm {return pin}
                getNet {
                    if {$name eq "newout" || ($name in {l1 l2} && $::buffer_added)} {
                        return net1
                    }
                    return net0
                }
            }
        }
        foreach name {out d1 d2 l1 l2 l3 newin newout} {
            interp alias {} $name {} iterm $name
        }
        proc pin {method args} {return [expr {$method eq "getName" ? "I" : ""}]}
        proc inst {name method args} {
            switch -- $method {
                getName {return $name}
                isDoNotTouch {return 0}
                getMaster {return [expr {$name in {diode1 diode2} ? "diode_master" :
                    $name eq "newbuf" ? "buffer_master" : "logic_master"}]}
                getITerms {if {$name eq "newbuf"} {return {newin newout}}; return {}}
                getPlacementStatus {return PLACED}
                setPlacementStatus {return}
            }
        }
        foreach name {driver diode1 diode2 logic1 logic2 logic3 newbuf} {
            interp alias {} $name {} inst $name
        }
        proc diode_master {method args} {return diode}
        proc buffer_master {method args} {return buffer}
        proc logic_master {method args} {return logic}
        proc get_pins {args} {
            if {[lindex $args 0] eq "-quiet"} { return driver_pin }
            return [lindex $args 0]
        }
        proc insert_buffer {args} {
            set i [lsearch -exact $args -load_pins]
            set ::moved [lindex $args [expr {$i + 1}]]
            if {$::moved ne {logic1/I logic2/I}} {error "wrong load selection $::moved"}
            set ::root {out d1 d2 l3 newin}
            set ::buffer_added 1
        }
        proc detailed_placement {args} {return}
        proc check_placement {args} {return}
        proc global_connect {} {return}
        proc log_cmd {args} {uplevel 1 $args}
        proc vic_say {line} {puts "NOTE $line"}
        proc vic_eco_route {varname tag} {
            upvar #0 $varname dirty
            set ::routed [lsort [dict keys $dirty]]
            return 1
        }
        source __PROC_PATH__
        puts "BEFORE 5"
        puts "RESULT [vic_close_data_fanout]"
        puts "AFTER [expr {[llength $::root] - 1}]"
        puts "BUFFER_LOADS [expr {$::buffer_added ? 2 : 0}]"
        puts "ROUTED $::routed"
    """).replace("__PROC_PATH__", str(proc_file)).replace("__STEP_DIR__", str(tmp_path))
    run = subprocess.run(["tclsh"], input=harness, text=True,
                         capture_output=True, check=False)
    assert run.returncode == 0, run.stderr
    assert "RESULT 1" in run.stdout, run.stdout
    assert "AFTER 4" in run.stdout, run.stdout
    assert "BUFFER_LOADS 2" in run.stdout, run.stdout
    assert "ROUTED data_net new_net" in run.stdout, run.stdout
