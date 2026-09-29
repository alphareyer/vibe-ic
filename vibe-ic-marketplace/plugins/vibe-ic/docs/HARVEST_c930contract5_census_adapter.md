# CONTRACT5 harvest: the census producer owns its supervision binding

The original CONTRACT5 shipping narrow measured source `e1c91efe` as candidate
`ed0af572` and ended RED1. Its existing PPA extraction ratchet found the real
module-level runner definition `_take_synth_drv_census`: the `drv` token matches
the frozen timing vocabulary and the name is not in the ledger. The function
was introduced by original-family regression follow-up `874576c1`. This was
not a lexical phantom or a reason to extend the ledger.

The adapter now lives in the existing `drv_stage_receipts` producer as
`synth_stage_supervised`. `step_pnr` supplies explicit netlist, top, Liberty,
SDC, path mapping and executor callbacks. The producer binds the executor to
its own mapped `census.tcl` and host `census.log`, then calls the unchanged
`synth_stage` API. The actual OpenSTA command, stdout/rc log, failed-tool
behavior-report removal and source-bound receipt remain the existing producer's
responsibility. The runner delegates tool execution through its existing
`_docker_exec` supervised path.

The existing supervision controls still check the generated deck, marker,
command, log path, successful receipt values and failed-tool cleanup. Their
execution seam remains the existing watchdog implementation. The ratchet,
vocabulary, non-vacuity floor and all frozen ledger members remain unchanged.
No helper was renamed to evade the detector, and no valid registered call was
removed. The old runner wrapper was deleted after its full adapter acquired
this producer API destination.

Validation is limited to the original 17 author selectors plus the exact
failed existing PPA selector, and a reverse control of this transfer's
watchdog binding. The original gate remains RED1. This source repair requires
root acceptance before any further shipping narrow; it is not IC/EDA signoff.
