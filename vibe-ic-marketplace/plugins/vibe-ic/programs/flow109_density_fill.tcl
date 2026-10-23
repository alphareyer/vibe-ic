# OpenROAD's native ODB density-fill operation; no geometry is authored here.
read_db $::env(VIBEIC_D62_INPUT_ODB)
set block [ord::get_db_block]
set before [llength [$block getFills]]
if {$::env(VIBEIC_D62_MODE) eq "produce"} {
    write_def $::env(VIBEIC_D62_BEFORE_DEF)
    density_fill -rules $::env(VIBEIC_D62_RULES)
    write_db $::env(VIBEIC_D62_OUTPUT_ODB)
} elseif {$::env(VIBEIC_D62_MODE) ne "verify"} {
    error "unsupported D62 operation"
}
write_def $::env(VIBEIC_D62_OUTPUT_DEF)
set report [open $::env(VIBEIC_D62_CENSUS) w]
puts $report "BEFORE $before"
puts $report "AFTER [llength [$block getFills]]"
close $report
