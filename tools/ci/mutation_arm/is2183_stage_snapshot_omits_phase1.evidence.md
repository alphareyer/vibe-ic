# is2183 — MUTATION ARM, both runs

The branch is TESTS-ONLY (the behaviour it pins is already correct on main), so the
falsification is a mutation arm rather than a G/R pair. Measured on 8HD-4 in clean
clones, base `28d13f16165a09315d62d63fe502b0528cb14f36` (v1.20.60).

    tree G' = main + ONLY the new test
      pytest programs/tests/test_issue2183_step5_denominator_is_measured_against_the_runners_project.py
      -> 5 passed in 1.87s                                              GREEN

    tree M  = main + the new test + is2183_stage_snapshot_omits_phase1.patch
      -> 2 failed, 3 passed in 0.79s                                    RED
         FAILED test_the_staging_snapshot_carries_the_designs_declarations
         FAILED test_CONTROL_the_fixture_really_drove_both_steps

The mutation is three lines: `_phase1_snapshot_to_stage` skips the `phase1` entry, so the
staging root a generator runs against becomes exactly the "input/ but no
phase1/generated_docs/" shape #2183 assumed the real one had.

WHY TWO CASES GO RED AND THREE DO NOT, stated so a reader is not surprised. The two that
move are the ones that read the STAGE. The three that stay green read the PUBLISHED
contract, and on this mutation Step 5 still measures the runner's own project — which is
the whole point of the refutation in the commit message: Step 5 does not read the stage.
A mutation that made all five red would have had to change two unrelated things at once
and would have proved less, not more.

## Tool note, recorded rather than worked around
`/home/reyerchu/fleet_scripts/falsref.sh` as installed on 8HD-4 has NO mutation-arm
support: it builds only trees G and R, never a tree M, and its `SRC` filter excludes
`test_*.py`, README/INVENTORY/fixtures but NOT `.patch`, so this branch's mutation patch
is counted as a source file (`src=1`) and the TEST-ONLY branch is never taken. Its run on
this branch therefore reads:

    is2183 base=28d13f1616 mod=1 src=1
    is2183 GREEN arm (fix in)      : 5 passed
    is2183 RED   arm (main sources): 5 passed

which is the documented RED-ARM-IS-GREEN shape whose stated meaning is "the behaviour
already landed" — the correct reading here, and the finding of this lane.
That script also prints no HYGIENE arm, so hygiene is recorded as NOT_MEASURED by it,
and it runs image sha256:66c33ff2… (0.3.6), not the pinned 0.3.49.
