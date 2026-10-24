# Native Chip configuration and results

This gf180mcuD pad-ring configuration is an overlay for the existing generated SPM RTL, pad wrapper and SDC. Load it after the design configuration with LibreLane's ordinary config-file merge. It keeps one `Chip` flow, retains `Vibeic.InsertSpareCells`, and leaves `Vibeic.PostRouteRepair` out until actual post-route timing requires repair.

The existing native PDN configuration adds Metal2-to-Metal4/Metal5 pad connections. Both core-ring offsets are -20 micrometers: the ring lies inside the core boundary to avoid the adjacent pad obstructions. Widths remain 1.6 micrometers and spacings 1.7 micrometers; trim is enabled. These are measured design settings, not universal values for every die.

Validation: the GeneratePDN-to-PowerGridViolations segment reports zero VDD/VSS/total violations. A fresh Chip run using released image 0.4.42 again reports zero PDN and routing DRC violations, then correctly stops at `Checker.DisconnectedPins` with 24 critical pins. No checker exception is included. This is not a full Chip PASS.

Keep the existing owner SDC with `set_max_fanout 4 [current_design]`, slew/cap checks on all corners, and `MAGIC_EXT_USE_GDS=true`. Mount the repository programs read-only and its `librelane_plugins` directory on PYTHONPATH. Resolve the released image at execution rather than storing its digest here.

Use the published `https://vibeic.ai/70step_dev/native-default-step-map.csv` mapping. Read a formal native run with `programs/native_flow_results.py RUN /path/to/native-default-step-map.csv --returncode RC --selection SELECTION --accepted-ledger LEDGER --output RESULT --rows-dir ROWS`. The reader records native outcomes and does not modify the global ledger. It does not rerun checkers or infer missing analyses. Do not use it to publish diagnostic runs whose error switches were disabled.

Already accepted IDs D10, D11, D19, D20, D22, D31, D32, D34 and D35 gain native evidence, not additional points. Final-geometry and external obligations retain their stated CSV scope.

## IO streamout and native post-GRT repair

Load `streamout-and-drv.json` after the design/PDN configuration when using this IO library. It selects the actually instantiated `gf180mcu_fd_io` GDS, avoiding an unused `ef_io` wrapper with an undefined child. Official wafer.space import-flatten patterns resolve duplicate PDK contact subcells while retaining their geometry; no DRC rule is suppressed. Paths use native `pdk_dir::` resolution.

`RUN_POST_GRT_DESIGN_REPAIR=true` enables LibreLane's existing native repair before detailed routing. On the retained SPM checkpoint this changed fanout violations from 5 to 0 and slew violations from 4 to 0, with zero cap violations across all nine corners and zero routing DRC. Fanout4, slew3ns and cap0.2pF are unchanged. This repair result was collected from a diagnostic segment, not counted as final-IC or Dxx acceptance.

The source overlay was also replayed through native Magic.StreamOut under unchanged formal error switches: PASS in17.14s. The previous uncorrected native streamout failed. No ERROR_ON switch, threshold, baseline, waiver or disconnected-pin exception is present in this overlay. Output-only spare pins remain subject to the owner's pending rule decision; final signoff has not been completed.
