# icgate3a — the mutation arm, and what it proves

`icgate3a_the_refusal_goes_back_to_quoting_a_blob.patch` reintroduces all four
halves of the defect this branch closes:

1. `general_precheck._step_delegate` goes back to `f"{d.program} refused: {tail}"`
   — the console tail, not the report the delegate just wrote.
2. `general_precheck._undouble` goes back to returning its input, so a program
   that names itself is named twice.
3. `phase3_one_shot_runner._clip_clause` goes back to a bare `text[:limit]`.
4. `phase3_one_shot_runner._gate_detail` goes back to a bare `[:600]` on the
   joined line, with the disclosure suppressed.

## MEASURED, both directions, host 8HD-d, lane icgate3, base e8cd3bba1

    clean tree (branch next/icgate3a)   20 passed
    with the patch applied              8 failed, 12 passed

The eight:

    RefusalQuotesAReason::test_negative_control_a_genuine_refusal_still_refuses
    RefusalQuotesAReason::test_negative_control_a_clean_delegate_still_passes
    RefusalQuotesAReason::test_negative_control_rc2_is_still_neither_a_pass_nor_a_refusal
    ProgramNamesItselfOnce::test_the_doubled_prefix_is_removed
    ProgramNamesItselfOnce::test_negative_control_only_one_prefix_is_removed
    ClausesAreNotCutMidWord::test_a_long_clause_is_cut_on_a_word_and_says_how_much_is_missing
    ClausesAreNotCutMidWord::test_a_single_unbroken_token_is_still_shortened
    ClausesAreNotCutMidWord::test_the_step_line_drops_whole_clauses_never_bytes

## The two that stay GREEN under the mutation, and why that is the point

`test_the_delegates_own_findings_replace_the_echoed_blob` and
`test_the_drc_rung_publishes_the_violation_count_not_a_wrapper_note` call
`_delegate_reason` DIRECTLY. Mutation 1 removes only its CALL SITE, so the
function still behaves — and a test set that had only those two would have
reported the ladder fixed while the ladder had stopped calling it. The three
`_rung` controls are what notice, and they are why the un-wiring is caught.
A guard nothing invokes is not a guard.
