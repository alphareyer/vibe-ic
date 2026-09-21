# icgate1b — MUTATION ARM, all three runs (sign-off DRC receipt / finding 31)

Lane `icgate1`, host **8HD-d (192.168.1.112)**, image
`ghcr.io/vibeic/vibeic-eda:0.3.67`
(`192.168.1.112:5000/vibeic-eda@sha256:4e9f54efe77a6f5b5664a3d3be3403c1e6cbe47de1d831a70abad4fb215c458b`,
its own `org.opencontainers.image.version` label reads `0.3.67`), base
`fdca61f576c8725fa4dbca012b763a8a3cac5351` (tree `12bb08a77bdf`), branch
`next/icgate1b` = `33e5ea8fd027e9bb8ef143b680d3c3e3d7eee61f` (tree
`d0361161aa2c`). 1-min load 1.21 at the last arm, every arm run ALONE, and the
R arm on a SEPARATE pristine clone so no arm can edit a tree another arm reads.

Selector = the file that carries the ruling plus the six that already own the
sign-off-DRC corroboration contract, its scope, and the symlink dedup it turns
on:

    tests/test_a_receipt_answers_about_the_file_not_the_alias.py
    tests/test_sha256_signoff_drc_zero_nobody_corroborated.py
    tests/test_signoff_drc_measurement_is_bound_to_the_path_it_is_read_at.py
    tests/test_report_audit_symlink_dedup.py
    tests/test_issue584_step31_drc_gate_is_scoped.py
    tests/test_drc_report_check.py
    tests/test_eda_report_audit.py

    tree R  = pristine main, separate clone (the new file does not exist there,
              so R runs the other six)
      -> 68 passed in 7.87s
    tree G  = main + next/icgate1b
      -> 92 passed in 8.41s
    tree M  = G + icgate1b_the_transcript_is_read_beside_the_alias_again.patch
      ->  4 failed, 88 passed in 8.39s                                   RED

No arm has a failure that is not the mutation's. There is no arm artefact in
this selector.

## What M puts back, and what that shows

M is ONE line — the transcript is read beside the LITERAL path again instead of
beside the same `rel` the ledger is queried with:

    -        transcript = project_dir / Path(rel).with_suffix(".log")
    +        transcript = report.with_suffix(".log")

It re-reddens exactly four assertions, and every one of them is the
`[steps-symlink]` half of a parametrised pair whose `[canonical]` half stays
GREEN:

    FAILED ...::test_every_alias_of_one_report_gets_the_same_receipt_answer[steps-symlink]
    FAILED ...::test_a_measured_zero_is_certified_through_either_alias[steps-symlink]
    FAILED ...::test_a_report_rewritten_after_the_row_was_written_is_refused[steps-symlink]
    FAILED ...::test_a_transcript_rewritten_after_the_row_was_written_is_refused[steps-symlink]

That split IS the finding. Same tree, same ledger, same inode, same bytes; the
only variable is which of two aliases of ONE physical report the audit was
handed, and `_discover` decides that by `os.scandir` order.

The last two fail at their POSITIVE PRECONDITION (`assert _corroborated(...)
== 1`) rather than at their refusal claim — under M the receipt is never found
through the symlink, so there is nothing left to revoke. Their refusal halves
are proven on the `[canonical]` parameter, which M leaves green. Said here
rather than left for a reader to notice.

Every negative control stays GREEN under M, which is the other half of a check
that can fail — they assert what must NOT happen, and the mutation does not
make it happen:

    test_a_report_with_no_run_behind_it_stays_not_measured[both aliases]
    test_every_leg_of_the_contract_still_refuses[6 legs x both aliases]
        measured:false / exit_code:1 / tool:openroad / no GDS bound /
        GDS changed after the run / no transcript at all
    test_an_alias_pointing_outside_the_project_is_refused

## The premise this branch had to correct first

The dispatch brief states that `step_drc` "writes none". MEASURED on run8's own
`provenance.jsonl`, that is false: row 73 is an `invocation`, `measured: true`,
`tool: klayout`, `exit_code: 0`, whose `outputs` carry BOTH
`reports/phase3/drc_signoff.rpt` and `reports/phase3/drc_signoff.log` at the
digests on disk and whose `inputs` carry `phase3/stage3/pnr/spm.gds` still
matching on disk. It is written by
`phase3_one_shot_runner._rebind_measured_drc_invocation_to_canonical_path`,
which exists for exactly this contract. The producer half was already done; the
reader could not find what the producer had written.

## The end-to-end verdict, on run8's own bytes

`_check_drc` over a tree rebuilt byte-for-byte from run8 (report, transcript,
`phase3/reports/` originals, the 68 MB GDS and the run's own ledger; digests in
the lane's `evidence/run8_SHA256SUMS.txt`), with the alias pinned to the one
run8's published `drc_signoff.json` names in `producers[0].file`:

    BEFORE (pristine main)   passed=False  receipt_corroborated_files=0
                             [ERROR] DRC_ZERO_NOT_MEASURED
    AFTER  (next/icgate1b)   passed=True   receipt_corroborated_files=1
                             DRC_ZERO_NOT_MEASURED gone

The two WARNINGs (`DRC_CATEGORY_PRESENT` density, `DRC_VIOLATION_COUNT`) are
unchanged in both arms and are honest: that report states no textual count and
carries no density category.

## Membership, not counts

    test files: base 3896 -> branch 3897   (+1, the new file)
    test names: base 47203 -> branch 47211 (+8 function names; they collect as
                24 parametrised nodes, which is the 24 the AFTER run adds)
    NAMES PRESENT ON BASE AND ABSENT ON THE BRANCH: 0
    files deleted or renamed: NONE
    unparseable test files on the branch: 0

Whole-set arms over the 39 files that name the DRC audit, its receipt contract
or its scope:

    BEFORE (pristine main)   944 passed, 35 skipped, 0 failed
    AFTER  (next/icgate1b)  1007 passed, 35 skipped, 0 failed
                            944 + 24 (new file) + 39 (inventory ratchet) = 1007

## What was NOT measured in either arm

20 nodes of `tests/test_matrix_mutation_ledger.py` report NOT_VERIFIED in BOTH
arms, identically:

    no scratch parent accepts a hardlink from the checkout, so `cp -al` cannot
    build the isolated mirror every replay needs

TMPDIR is `/tmp/lane.icgate1/` per the dispatch header and the checkout is under
`/home`, so the two are on different devices and `link()` returns EXDEV. These
are NOT passes and are not counted as any. The population is identical on both
sides, so the comparison above is unaffected; a host whose scratch shares the
checkout's mount would close it.
