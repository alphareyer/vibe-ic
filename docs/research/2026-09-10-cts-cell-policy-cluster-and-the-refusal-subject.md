# CTS_CELL_POLICY_UNAVAILABLE — the cluster, and what was still wrong after it closed

Measured 2026-09-10 on 8hd-3 (192.168.1.121). The bisect and the pinned-image
probes were taken against `f91aaa391` (v1.20.15). Main moved twice during the
work: the ARMS were run in full against `3588d0806` (v1.20.16) and both were
re-confirmed against `756d84f40` (v1.20.17), which is the base the branch sits
on. The v1.20.16 -> v1.20.17 delta touches one file under `programs/tests/`
(`test_signoff_required_outputs_completeness.py`), two `tools/ci/mutation_arm/`
patches and version strings — nothing this change reads or is read by, and no
population count moves. Every arm was built with `git archive` into a separate tree (never
a linked worktree, never a `cp -a`, so no arm can be read through another's
`__pycache__`), in a private clone with `gc.auto 0`.

## 1. The brief's hypothesis — CONFIRMED

> Check whether `_cts_legal_buffer_selection_tcl`'s arrival and this cluster's
> arrival are the same commit.

They are, and it is one commit, not a coincidence of two:

    git log -S'_cts_legal_buffer_selection_tcl' origin/main
      e435688b7  (v1.20.6)   -- the later repair
      4b74ba713  (v1.19.95)  -- the arrival

    git log -S'CTS_CELL_POLICY_UNAVAILABLE' origin/main
      4b74ba713  (v1.19.95)  -- the ONLY commit that ever touched the token

Symptom bisect, six deck-evaluator files run at four shas (`BISECT.txt`):

| sha         | version  | result                    | occurrences of the error |
|-------------|----------|---------------------------|--------------------------|
| `aa08f0556` | v1.19.95^ | 149 passed, 1 skipped    | 0 |
| `4b74ba713` | v1.19.95  | **17 failed**, 132 passed | **32** |
| `e435688b7` | v1.20.6   | 149 passed, 1 skipped    | 0 |
| `f91aaa391` | v1.20.15  | 149 passed, 1 skipped    | 0 |

The 17 ids fall in exactly the six files the brief names:
`test_g_antenna_reroute_thread_parallel` (1),
`test_hardmacro_supply_globalconnect` (1),
`test_phase3_routability_driven_placement` (4),
`test_pnr_tool_fatal_signal_and_checkpoint_resume` (8),
`test_reference_flow_pnr_qor_knobs` (2),
`test_v0_3_39_issue581_pnr_tcl_syntax` (1).

## 2. The cluster is CLOSED, and was closed before this brief was written

`e435688b7` (v1.20.6) added `programs/tests/_pnr_tcl_stub.py` — a `tclsh`
stand-in that models the four primitives the v1.19.95 block reads a VALUE from
(`utl::redirectStringBegin` / `utl::report` / `utl::redirectStringEnd`,
`[ord::get_db] findMaster`), where the previous one-line
`proc unknown {args} { return "" }` returned `""` for all of them.

At `f91aaa391` (v1.20.15) every test that evaluates the deck is green: all 37
test files under `programs/tests/` that invoke `tclsh` — **616 passed, 4
skipped** — and the token `CTS_CELL_POLICY_UNAVAILABLE` exists at exactly one
site in the whole tree (`phase3_one_shot_runner.py`), so no other test can
observe it. That last fact is what bounds the sweep: the error is emitted by
the DECK, so only a test that evaluates the deck can ever see it, and every one
of those was run.

## 3. The image was never the subject — measured, not assumed

The brief reserved the possibility that the policy is genuinely unavailable in
the pinned image. It is not. Against the digest the repo pins
(`tools/ci/protected_landing_transition.py:RUNNER_IMAGE_DIGEST` =
`sha256:89a8fd7295208ee6…`, OpenROAD **26Q3-2075-g18e98f9e44**), probes in
`IMAGE.txt`:

* `report_dont_use`, `utl::redirectStringBegin/report/redirectStringEnd` all
  exist (`info commands` -> 1 each);
* on a linked design the capture round-trips **through** `report_dont_use`:
  the excluded masters come back inside the block's own sentinels, and the
  block's `lsearch` finds them (`FINDS_buf_4=4`, `FINDS_buf_1=-1`);
* with **nothing** excluded the tool prints `Don't Use Cells:` / `  none` —
  byte-identical to what `_pnr_tcl_stub` models;
* `findMaster` on an absent master returns the literal `NULL`, which is what
  the deck's `$_cts_master eq "NULL"` branch tests for;
* all of the above holds identically **under `-metrics`**.

So the stub is faithful, and nothing about the image needs naming. Recorded
because it was asserted in a docstring and pinned by no test.

## 4. What was STILL wrong at v1.20.15 — the refusal named the wrong subject

The guard fires on a disjunction of three conditions and raised ONE sentence
for all of them:

    if {$_cts_policy_rc || ![string match *CTS_POLICY_BEGIN* $_cts_policy]
        || ![string match *CTS_POLICY_END* $_cts_policy]} {
      error "CTS_CELL_POLICY_UNAVAILABLE: $_cts_policy_err"
    }

`catch` sets its variable to the command's RESULT on success. So driving the
two conditions through the same emitted block under `tclsh`, on live main:

| condition | refusal emitted at v1.20.15 |
|---|---|
| `report_dont_use` raised | `CTS_CELL_POLICY_UNAVAILABLE: no network has been linked.` |
| capture lost, `report_dont_use` rc=0 | `CTS_CELL_POLICY_UNAVAILABLE:` |

The second is an empty claim about the POLICY for a failure of the CHANNEL.
Nothing had been learned about the cell policy at all — the command succeeded.
That is the sentence the six files emitted 32 times, and it is why the cluster
read as an image/PDK problem: it points at the policy, and says nothing else.

**Fix**: split the diagnosis, keep the firing condition byte-for-byte, keep the
`CTS_CELL_POLICY_UNAVAILABLE` token on both branches so nothing that greps for
it loses the signal. The capture branch now reports which sentinel came back
and how many bytes were captured. Nothing is relaxed: both conditions still
raise, still fatally, exactly as before.

## 5. Arms

`falsref.sh` is **NOT PRESENT** on 8hd-3 — `/home/reyerchu/fleet_scripts/`
exists and does not contain it. The two arms were therefore built by hand to
the same contract, with `git archive` (no `cp -a`, so no stale `__pycache__`):

* **tree G** — live main (`3588d0806`) + the whole change. `GREEN_ARM.log`:
  **686 passed, 5 skipped, 0 failed** over every test file under
  `programs/tests/` that drives `tclsh` (37 files), plus
  `test_ppa_runner_extraction_ledger`, plus the three inventory/index guards a
  new test file moves.

  One of those five is a `NOT_VERIFIED` (vibe-ic#1128), and it is NOT counted
  as a pass here. It named a leftover container
  `vibeic-candidate-b7217d378fc7c487bff1b7a0` from another session, mounted
  read-only. CONTROLLED both ways after the fact:
  `test_signoff_worst_path_evidence` passes 9/9 on a real clone of
  `3588d0806` AND 9/9 on this branch, and the container is gone
  (`docker ps -a | grep -c vibeic-candidate` = 0). It is a foreign container
  race, not a property of either tree — and the test is about STA worst-path
  evidence, which this diff does not touch.
* **tree R** — live main sources + ONLY the new test file. `RED_ARM.log`:
  **3 failed, 1 passed**, rc=1, at `756d84f40`; identical at `3588d0806`. The
  refusal it fails on is the bare `CTS_CELL_POLICY_UNAVAILABLE:` — the empty
  claim.

At `756d84f40` the green side was re-confirmed on the four files that bear on
this change directly (`..._refusal_names_its_own_subject`,
`test_v0_3_39_issue581_pnr_tcl_syntax`,
`test_i2172_cts_buf_list_from_pdk_family`,
`test_ppa_runner_extraction_ledger`): **32 passed**. The 686-test green arm was
NOT re-run at `756d84f40` — say so rather than restate the number as if it
were.

The one test that passes on BOTH arms is
`test_the_firing_condition_is_unchanged`, and it is supposed to: it is the
guard on what this change must not move, so a red there would mean the change
had altered behaviour rather than diagnosis.

The branch is not TEST-ONLY — it changes `phase3_one_shot_runner.py` — so no
mutation arm is owed.

`gen_program_inventory.py` was re-run (not a baseline write) because a new file
under `programs/tests/` moves `test_files` and `programs_tree_all_py` and the
six README figures bound to them; `--check` is OK, and the delta against main's
population is exactly one file, confirmed by set difference rather than by
subtracting counts.

## 6. Named, not built

The six deck tests are green against a STUB. If a future image bump changed
`report_dont_use` or `utl::redirectString*`, all six would stay green while the
real deck died at CTS. Nothing in the repo binds the stub's model to the image
it claims to model; §3 above is the first measurement of it. Not built here —
it is a new piece of work, and it is not this cluster.
