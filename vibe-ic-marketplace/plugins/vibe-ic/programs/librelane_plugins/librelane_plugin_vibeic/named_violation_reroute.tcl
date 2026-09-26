# vibe-ic step Vibeic.NamedViolationReroute (see routing.py).
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl

read_current_odb

# The detailed route's own report is the list of nets to rip up; every
# detailed_route the pass runs rewrites it (`-output_drc`), so the count after
# is the router's, not a projection.
set _vic_drc_opt [list -output_drc $::env(VIBEIC_NVR_REPORT_PATH)]
if {[info exists ::env(DRT_THREADS)]} { set_thread_count $::env(DRT_THREADS) }

source $::env(VIBEIC_NVR_TCL)

write_views
