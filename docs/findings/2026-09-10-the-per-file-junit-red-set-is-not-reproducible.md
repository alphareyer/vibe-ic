# `test_pytest_per_file_junit.py`'s red set is not reproducible — on main OR on any branch

MEASURED 2026-09-10 on 8HD-8 (32 cores), live main `f91aaa3915` (v1.20.15) and a
tree composing `next/cyregress` onto it. Ten runs of the SAME command on the SAME
trees.

## The measurement

    tree            scope    run  loadavg  result
    ------------------------------------------------------------------------
    bare main       1 file    1     66.5   1 failed  test_short_natural_collect_relays_its_terminal_protocol
    bare main       1 file    2     21.3   99 passed
    bare main       1 file    3     24.5   99 passed
    composed        1 file    1     22.1   101 passed
    composed        1 file    2     45.5   101 passed
    composed        1 file    3     23.8   5 failed
    composed        3 files   1     60.8   6 failed
    composed        3 files   2     43.3   143 passed
    composed        3 files   3     16.8   1 failed
    composed        3 files   -     ~44    1 failed   (the original observation)

## What the observations can establish

**Load average alone does not predict the result.** The table contains failures
at load averages 66.5 and 60.8 and passes at 45.5 and 43.3; it also contains
failures at lower readings. These samples do not exclude contention as a cause.
Load average is not CPU utilization, and one reading cannot characterize the
delays each child process experienced during the run.

**The symptom occurs at both scopes.** Failures and passes appear in both the
one-file and three-file selections. This does not establish equal failure rates.

**A failure does not by itself implicate the branch.** Bare main reddens too,
and the test it reddened —
`test_short_natural_collect_relays_its_terminal_protocol` — appears in NONE of the
composed tree's red sets.

## Working hypothesis

The failures share a possible timing mechanism. Every failing test listed drives
`pytest_per_file_junit`'s real subprocess driver against a window derived from a
timing reading, and the reading is taken on a shared host whose contention is not
fully observable to it. This is a hypothesis, not proof of a single root cause;
that requires the failing child's protocol and timing evidence. Twelve DISTINCT
tests failed across the original runs and no two runs
produced the same set:

    run                     failing set
    ------------------------------------------------------------------------
    composed 1f run 3       a_red_test_is_a_red_run / a_session_level_red /
                            files_not_launched / a_per_file_maxfail_prefix /
                            the_fallback_arms_maxfail_control
    composed 3f run 1       one_session_loses_the_whole_record / cpu_activity /
                            chatty_import_output / nested_collect /
                            pytest_deselection / natural_exit_with_live_descendant
    composed 3f run 3       nested_collect
    bare main run 1         short_natural_collect

Overlap between the two largest sets: ZERO.

## Why this matters beyond one branch

The land gate runs the full suite on the merged tree. Any branch whose targeted
selection reaches this file is judged by a sample from this distribution, so a
refusal naming one of these tests needs investigation before attribution to the
branch. These observations do not make a later failure safe to ignore.

It also made the file self-referential as evidence: `next/cyregress` existed to
fix exactly this class of defect ("the window reads the box, not the tree"), and
its GREEN arm had to be measured with the instrument its own subject is broken.
That branch has since LANDED as `104a3c30fb` (v1.20.21) — `stall_window` now
takes `width`, and `trivial_child_s` / `child_window` are on main — so the
self-reference is resolved. What is NOT resolved is the distribution this
document measures: the section below re-takes it on bare main, 30 versions after
that landing, and it is still there.

## NOT MEASURED, stated rather than implied

* Whether the composed tree's rate (4 red of 7) differs from main's (1 red of 3).
  Those samples are far too small to separate, and this document does not claim
  they are the same either.
* Whether `relay_window` — the serial relay-floor term — also needs to scale with
  concurrency. Nobody has measured the relay term under load.
* At the time of the first series, the rate on any host other than 8HD-8.
  The second series below adds 8HD-4.

## Re-measured on a second host, 30 versions later

The table above was taken on 8HD-8 at `f91aaa3915` (v1.20.15). A second
series records the symptom on a different host and tree; it does not isolate
which environment or implementation detail causes it.

MEASURED 2026-09-10 on 8HD-4 (32 cores), bare live main `b6a73c0aa3`
(v1.20.45) — no branch composed onto it. Six SERIAL runs of the same command,
`python3 -m pytest -q -p no:randomly programs/tests/test_pytest_per_file_junit.py`:

    tree        run  loadavg  wall      result
    ---------------------------------------------------------------------------
    bare main    1     7.87   98.62 s   1 failed  test_nested_collect_progress_is_relayed_to_the_outer_session
    bare main    2    12.21   95.72 s   101 passed
    bare main    3    11.39   97.44 s   101 passed
    bare main    4    11.83   98.04 s   101 passed
    bare main    5     8.19  104.85 s   101 passed
    bare main    6     7.62  103.83 s   101 passed

1 red in 6, and the load ordering is again the wrong way round: the single red run
carried the LOWEST load of the six (7.87), while the two highest (12.21, 11.83)
were both green, and the other low-load run (7.62) was green too. Wall time varies
by 9% across the six and does not order them either.

The test that reddened, `test_nested_collect_progress_is_relayed_to_the_outer_session`,
is one of the twelve above — it is the whole of the `composed 3f run 3` red set and
a member of `composed 3f run 1`. So a fourth independent red set drawn from the same
distribution, on a different host, on a tree 30 versions later, still names a member
of the same twelve and still fails to repeat any earlier set.

## Additional measurement limits

* Whether 1-in-6 here and 1-in-3 there differ. Both samples are far too small to
  separate, and this section does not claim they are the same either.
* Whether the phenomenon survives `-p randomly` (both hosts ran `-p no:randomly`).
* Any host other than 8HD-8 and 8HD-4.
