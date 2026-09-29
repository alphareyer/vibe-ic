# CUT20 native hold count ceiling proposal

Status: SOURCE PROPOSAL, NOT BUILT, NATIVE/PHYSICAL NOT_MEASURED. Root and the
fork gatekeeper own review, build, publication and all native acceptance.

The official v0.3.87 recipe pins OpenROAD
`7c05ab8a1c2b93deb9dd092fe8c126bce1f278fc` and LibreLane
`f199e900aedbed9edcbc99fd0a4cf13cbd3e8161`. `source-binding.json` binds every
original and proposed file. The released RepairHold SHA256 is
`57be9517bd58e2c628a1dc565744549c089b95f4fce3ffe03a4de7e61c962705`.
Its percentage-derived minimum of 100 and setup-first instance growth do not
implement a small or zero absolute allowance.

`OpenROAD.patch` adds **proposed** `repair_timing -hold -max_buffer_count N`,
nonnegative int32 including zero, forwarded through Tcl, SWIG, Resizer and
RepairHold. The effective cap is the minimum of the existing percentage cap
and N. Absolute mode checks every path position and endpoint, and rolls back
any move that would exceed the cap. The legacy API remains available without
an absolute argument. The ABI query `rsz::hold_buffer_count_limit_abi` returns
1; native metrics record ABI, absolute mode, requested and effective ceilings.
These names do not exist in the released tool and must not be inferred from
a release label or a copied JSON value.

`librelane.patch` declares **proposed** `PL_RESIZER_HOLD_MAX_BUFFER_COUNT` and
refuses a missing native ABI before any setup/hold repair. It forwards the
absolute allowance unchanged across setup-first repair. Pool policy,
exclusions, delay availability throughout setup and hold, first-index/resume,
and timing margins are unchanged.

The plugin adapter sends the key only when the resolved schema declares it,
requires the current native State's ABI/absolute/count attestation, and judges
the actual retap/resizer area pair before adopting views or routing. Its 5%
total stdcell policy is unchanged. Setup-plus-hold area delta remains an upper
bound. A 20,000 x 5 setup addition alone exceeds the original 50,000 allowance:
an absolute hold ceiling does not certify that session's upper-bound area or
timing. Known measured area FAIL stays FAIL even if another binding is missing.

Root recipe: apply both patches against their exact refs (`git apply --check`
first), review and build through the fork gatekeeper, then run
`native_feature_probe.tcl` using the published approved image, one fresh
container per limit. `run_native_recipe.py` records exact image, CID, PID,
command, rc and native metrics, uses the image entrypoint with `--skip` first,
and limits each engineering probe to two CPUs/4 GiB/45 seconds. It never
builds, edits an image or executes inside an existing container. No probe was
run by this author. Baseline must actually exercise more than five inserts;
otherwise the small-cap positive is INCONCLUSIVE, not a feature PASS.

After that, root must supply separately named source-bound NEW native fixtures
for both unchanged 500-buffer positive obligations and an actual setup-loaded
case. Retain every original 14/4 file and old transcript. Do not substitute
source bounds or early refusal for those numerical/native obligations.
`source_cap_bounds.py` explicitly proves only the proposed arithmetic ceiling;
its separately restored min100 and frozen-percentage controls recover the
original 6/650 countervalues, without claiming new observed insertions.
