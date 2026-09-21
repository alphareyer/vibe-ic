# icgate3c — the mutation arm, and what it proves

The patch reintroduces both halves of R-icgate3(c):

1. `DRC_CATEGORY_PRESENT` goes back to the flat sentence
   `DRC category '<cat>' not found in reports`, which names no scope;
2. `_category_also_named_beside` stops consulting the sibling tool log, so the
   distinction between "not checked" and "checked and clean" is lost.

## MEASURED, both directions, host 8HD-d, lane icgate3, base e8cd3bba1

    clean tree (branch next/icgate3c)   7 passed
    with the patch applied              4 failed, 3 passed

The four:

    test_the_message_names_the_scope_it_searched
    test_a_log_that_names_the_class_is_quoted_and_the_absence_explained
    test_negative_control_a_log_that_does_NOT_name_it_claims_nothing
    test_negative_control_no_sibling_log_at_all

## The three that stay GREEN under the mutation, and why that is correct

    test_negative_control_nothing_but_the_sentence_moves
    test_negative_control_a_class_the_report_DOES_name_gets_no_finding
    test_negative_control_a_scoped_file_is_not_read_as_its_own_sibling

These assert that the verdict, the counts, the found-set and the quiet cases do
NOT move. The mutation does not move them either, so they stay green in both
directions -- which is what an invariant control is for. The third calls
`_category_also_named_beside` directly and the mutation guts that function's
body while leaving its call site, so it returns "" for the wrong reason; the
first two tests in the red list are what notice the un-wiring.
