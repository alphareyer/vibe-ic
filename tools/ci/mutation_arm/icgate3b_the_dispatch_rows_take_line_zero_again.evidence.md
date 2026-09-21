# icgate3b — the mutation arm, and what it proves

The patch reverts all three producer-dispatch rows in
`phase3_one_shot_runner.py` to

    detail_lines = (cp.stdout or cp.stderr or "").strip().splitlines()
    detail = detail_lines[0] if detail_lines else f"rc={cp.returncode}"

i.e. it reintroduces BOTH halves of R-icgate3(b): only line 0 survives, and
only one of the two streams is read.

## MEASURED, both directions, host 8HD-d, lane icgate3, base e8cd3bba1

    clean tree (branch next/icgate3b)   14 passed
    with the patch applied              3 failed, 11 passed

The three:

    NoDispatchRowStillTakesLineZero::test_the_line_zero_expression_is_gone_from_the_runner
    NoDispatchRowStillTakesLineZero::test_every_producer_dispatch_row_uses_the_shared_reader
    NoDispatchRowStillTakesLineZero::test_the_step_name_each_row_passes_is_its_own

## The eleven that stay GREEN under the mutation, and why that is the point

They call `_producer_detail` DIRECTLY. The mutation removes only its CALL
SITES, so the function still behaves and a test set built only from those
eleven would have reported the rows fixed while the rows had stopped calling
it. The three MEMBERSHIP tests above are what notice, and they are asserted
over the AST rather than over the text — the defective expression is QUOTED in
`_producer_detail`'s own docstring as the measurement that motivated it, so a
grep-shaped check would be satisfied by deleting that prose and unsatisfied by
keeping it. A guard keyed on a comment is not a guard.
