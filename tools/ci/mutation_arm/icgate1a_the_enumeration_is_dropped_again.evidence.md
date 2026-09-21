# icgate1a — MUTATION ARM, all three runs (FS1 / R-0915-119)

Lane `icgate1`, host **8HD-d (192.168.1.112)**, image
`ghcr.io/vibeic/vibeic-eda:0.3.67`
(`192.168.1.112:5000/vibeic-eda@sha256:4e9f54efe77a6f5b5664a3d3be3403c1e6cbe47de1d831a70abad4fb215c458b`,
its own `org.opencontainers.image.version` label reads `0.3.67`), base
`fdca61f576c8725fa4dbca012b763a8a3cac5351` (tree `12bb08a77bdf`), branch
`next/icgate1a` = `a72ab4899e74c2af2210958df5b7e33e00234f12` (tree
`d60195c4b055`). 1-min load 2.26 at the last arm, every arm run ALONE, and the
R arm on a SEPARATE pristine clone so no arm can edit a tree another arm reads.

Selector = the four files that carry the ruling and the checkers it moves:

    tests/test_r0915_119_fs1_safety_absence_is_answered_not_unmeasured.py
    tests/test_fmeda_fault_injection_coverage.py
    tests/test_issue515_skip_reaches_vacuous_tier.py
    tests/test_r0915_119_a_subject_class_that_is_absent_has_been_answered.py

    tree R  = pristine main, separate clone (the new file does not exist there,
              so R runs the other three)
      ->  6 failed,  96 passed in 6.60s
    tree G  = main + next/icgate1a
      ->  6 failed, 110 passed in 8.86s
    tree M  = G + icgate1a_the_enumeration_is_dropped_again.patch
      -> 12 failed, 104 passed in 8.96s                                    RED

## The six failures present in ALL THREE arms are an ARM ARTEFACT, not a result

    test_dc_below_the_asil_floor_is_a_real_non_zero_exit
    test_dc_at_or_above_the_asil_floor_is_a_measured_pass
    test_the_floor_itself_is_load_bearing
    test_local_image_probe_reports_absence_where_resolve_invents_a_pin
    test_the_declared_regenerated_paths_are_the_ones_it_actually_writes
    test_a_second_run_rewrites_exactly_the_declared_paths

Every one raises the SAME uncaught exception, and it is about the arm:

    _eda_pin.ImageNotResolvable: IMAGE_NOT_RESOLVABLE: no vibeic-eda image
    identity could be resolved on this host; tried: this host: docker unusable:
    FileNotFoundError: [Errno 2] No such file or directory: 'docker'

The suite runs INSIDE the image, which carries no `docker` CLI, so
`fmeda_fault_injection_coverage.resolve_injection_backend` cannot resolve an
injection backend for any design that IS applicable. Reproduced IDENTICALLY on
the pristine clone (arm R) before this branch existed. It is NOT counted as a
result of this change in either direction, and it is reported separately as an
out-of-scope finding: the exception ESCAPES as a traceback instead of becoming
an honest verdict.

## What M puts back, and what that shows

M is two hunks, one per half of the defect:

  * `flow_compliance_check._check_program_exit_zero` reads a report's typed
    class and passes it to `infer_nonverdict_reason` WITHOUT the enumeration,
    so `_guard_structural` — the reader's half of R-0915-119 guard (i) — never
    sees a valid record and falls back to the fail-closed default. The class
    becomes UNREACHABLE through the report channel.
  * `fmeda_fault_injection_coverage.run` stops STATING the class, so the two
    FS1 gates go back to the rc-0 `VACUOUS_PASS` disclosure.

It re-reddens exactly the six assertions the ruling is about, and nothing else:

    FAILED test_r0915_119_fs1_...::test_the_report_channel_and_the_stdout_channel_agree
           [break_handler_safety_check phase2/stage1/rtl --json .../break.json-True]
    FAILED test_r0915_119_fs1_...::test_the_producer_states_the_class_with_its_enumeration
    FAILED test_r0915_119_fs1_...::test_the_producers_sentence_fits_the_consumers_stdout_window
    FAILED test_r0915_119_fs1_...::test_the_second_gate_mirrors_the_class_and_does_not_re_derive_it
    FAILED test_r0915_119_fs1_...::test_the_fs1_step_row_is_decided_and_never_not_measured
    FAILED test_fmeda_fault_injection_coverage.py::test_rtl_with_no_mechanism_keeps_its_honest_not_applicable

The FIRST is the whole argument for the reader hunk: the parametrised sibling
WITHOUT `--json` stays GREEN under M. One checker, one tree, one rc, and the
only variable is whether the gate clause names a report — which is a fact about
the clause, not about the design.

Every negative control stays GREEN under M, which is the other half of a check
that can fail: they assert what must NOT happen, and the mutation does not make
it happen.

    test_an_rtl_dir_with_no_hdl_keeps_its_disclosed_vacuous_answer
    test_an_absent_rtl_dir_is_still_a_fail_not_an_absence
    test_rtl_that_parses_to_no_module_establishes_nothing
    test_a_mechanism_that_was_found_is_a_zero_denominator_not_an_absence
    test_a_class_token_with_no_enumeration_is_still_refused
    test_the_second_gate_mirrors_nothing_when_there_is_nothing_to_mirror

## Membership, not counts

    test files: base 3896 -> branch 3897   (+1, the new file)
    test names: base 47203 -> branch 47216 (+13)
    NAMES PRESENT ON BASE AND ABSENT ON THE BRANCH: 0
    files deleted or renamed: NONE
    unparseable test files on the branch: 0

ast-parsed from each tree, not grepped: a merge can turn prose into code and a
grep cannot tell the two apart.
