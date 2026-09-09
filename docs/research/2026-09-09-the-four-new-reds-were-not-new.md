# The four "new" reds of 2026-09-09 were not new, and they are one defect

**Subject trees.** `610cae2cc` (this morning) and `6883a9c93` = `v1.20.7` (live main
at the time of writing), plus the candidate `7fd21f2712`.
**Instrument.** One container per job, one fresh `git clone --no-hardlinks` per job,
pinned image `192.168.1.112:5000/vibeic-eda@sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49`,
`--memory=8g --pids-limit=1024`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Arms are always
INTERLEAVED in one queue so both see the same box in the same minutes.
Harness: `fleet-artefacts/cyregress-20260909/scripts/run_queue.py` (a copy of the
`mainred2` queue with one added `cpus` field).

## 1. Attribution: none of today's seven landings

`git diff 610cae2cc..6883a9c93` restricted to every module the four tests exercise —
`pytest_per_file_junit.py`, `_watchdog.py`, `_semantic_child_progress.py`,
`_owned_process_supervisor.py`, `tests/_session_floor.py`, `tests/conftest.py`, and the
three test files themselves — is **empty**. The subject is byte-identical across the day.

The brief's hypothesis was `v1.20.5` (`repo_hygiene_parallel.py`, +117). That commit adds
`OuterProgressRelay` and one module-level import and edits `main()`; it does not touch
`_run`, which is the only entry `test_semantic_child_progress.py` calls.

CONTENT is not the whole answer, so both trees were also RUN:

| file | selector | `610cae2cc` | `6883a9c93` | shape |
|---|---|---|---|---|
| `test_pytest_per_file_junit.py` | whole file | **3/3 RED** | **3/3 RED** | `--cpus=4`, 3 reps/arm |
| ↳ `…ignores_legacy_failure_threshold` | | red in **all 6** runs | | |
| `test_recovery_arm_is_not_a_fail_fast_run.py` | whole file | **1/6 RED** | **2/6 RED** | 12 concurrent containers, 6 reps/arm |
| ↳ `…more_reds_than_the_bound_still_produces_a_record` | | the failing name in every one | | |
| `test_semantic_child_progress.py` | whole file | **1/6 RED** | **2/6 RED** | same |
| ↳ `…one_slow_document_uses_finite_chunks_past_old_stall_window` | | the failing name in every one | | |

Every name in the brief reproduces on **this morning's tree**. The red SET inside one file
also moves run to run (2 to 4 names). That drift, not the tree, is what two population
sweeps differenced.

### 1b. Why the noise-floor measurement could not see them

The same day, the 33 failing files of the morning sweep were re-run **alone** and the
result was reported as "76 raw = 76 real, noise floor 0%". That is sound for the files
that were red, and it **bounds one direction only**.

Running a file alone is precisely the condition under which a load-sensitive member
PASSES: the eight fallback interpreters get the whole box, the bare `python3 -c` child
starts in a tenth of the declared grace, and every window is comfortably above the floor.
An alone-rerun can therefore confirm that an observed red is real — it cannot find the
reds it silences. Its zero is a zero about false POSITIVES; it says nothing about false
negatives, and the four names here are exactly false negatives of that instrument.

A noise floor for THIS class has to be measured at the concurrency the population sweep
itself runs at, on both trees, N times per arm.

## 2. One defect, in the three files' own words

```
NORECORD  test_01_red.py  STALLED after 1.0268 s with no validated pytest lifecycle
          progress — this file's result is UNKNOWN, not clean
NORECORD  test_more_reds_than_the_bound.py  STALLED after 1 s …
NORECORD  test_green_neighbour.py           STALLED after 1 s …
WATCHDOG_STALLED: … did not advance for > 0.18s
… incomplete domain-progress relay (0/4)
```

`1.0268` is `2 x` a **serially** measured 0.51 s pytest floor, handed to an arm that starts
**eight** sessions into one cgroup at once. `1` and `0.18` are bare literals. `0/4` is zero
checkpoints: the child was killed before it reached its first one.

`tests/_session_floor.py` was written on 2026-09-02 for exactly this class and is right —
"a stall-window test whose window is shorter than this environment's start-up … measures
the interpreter, and its colour is decided by which box runs it." Two gaps remained: it
measures ONE session at a time, and it was applied to two files. The two files that
reddened tonight were never swept, and the one that was is swept only along the serial axis.

## 3. The repair, and both directions

Nothing is relaxed; no bound is raised by a constant; every window still returns its
declared nominal on a box that can start inside it.

* `_session_floor`: the reading takes a `width` and really starts that many interpreters at
  once; `stall_window(..., width=)` and the new `child_window()` derive from it.
* `test_pytest_per_file_junit._run_driver` derives `--stall-after` from `_driver_width`,
  read off the invocation (1 for the serial per-file lane, else `--fallback-jobs` capped by
  the selection size), so no call site has to remember.
* `test_recovery_arm_…`: `stall_window(1)` for the one-session aggregate arm,
  `stall_window(1, width=2)` for the two-session recovery arm.
* `test_semantic_child_progress`: the GAP is `child_window(0.09)` over a **bare-interpreter**
  floor and the lease is twice it — the lease is armed at spawn and the first checkpoint
  arrives one gap later, so it must cover start-up AND one gap. A first attempt that derived
  the LEASE at two floors still lost 2 of 12 runs at 24-way contention.

**FIX direction** (pre `6883a9c93` vs fix `7fd21f2712`, interleaved):

| shape | file | pre | fix |
|---|---|---|---|
| `--cpus=4`, 4 reps | `test_pytest_per_file_junit.py` | **4/4 RED** (same 4 names each) | **0/4** (101 passed) |
| `--cpus=1`, 3 reps | `test_recovery_arm_…` | **3/3 RED** | **0/3** |
| 24 concurrent, 12 reps | `test_semantic_child_progress.py` | **3/12 RED** | **0/12** |
| host, all three files | | | **143/143 passed** |

**KILL direction** — the historical defect planted back, WITH the widened windows:

| plant | change | result |
|---|---|---|
| A | `recovery_pytest_argv` returns the caller's argv unchanged (keeps `--maxfail`) | `…more_reds_than_the_bound…` and `…neighbour_record_is_not_traded_away…` **RED** (7 of 12 in the file) |
| B | `--stop-after-failures` back inside the recovery loop | `…ignores_legacy_failure_threshold` **RED** (`assert 8 == 9`) |
| C | the semantic lease stops being renewed by checkpoints | `…one_slow_document…` **RED** (relay 2/4) |

Two new guards pin the derivation: one asserts the width-8 reading really observes eight
concurrent calibration sessions and sixteen calls; the other asserts an aggregate invocation
is handed the width-8 window while a plain one keeps the width-1 one. Restoring the constant
`str(_STALL)` makes the second pair equal and reddens it.

## 4. Named, not fixed: the family's fifth member

`test_pytest_per_file_junit.py::test_nested_validated_progress_is_relayed_to_the_outer_session`
is the same family and is **red on BOTH arms**: 3/3 on the candidate and 1/3 on `6883a9c93`
at `--cpus=1`, and once on the loaded host. It uses `stall_window(2.5, starts=2)` directly,
not `_run_driver`, and it dies mid-run —
`since_last_progress_s=2.511 elapsed_s=4.787 … terminal event missing (stage=running)` —
so the quantity that is under-derived is the inner nested lane's inter-event gap, not its
start-up. That is a different measurement and no number for it was taken here, so no number
is guessed. At the sweep's own `--cpus=4` it is green in 4 of 4 candidate runs.

Filed as **vibeic/vibe-ic#2219** with these figures, so it does not dissolve into
"known flaky". A correct repair needs a measured floor for *one relayed lifecycle event of a
nested driver session on this box*, in the same shape as `trivial_session_s` /
`trivial_child_s`; raising `2.5` to a larger constant is the wall clock this module exists
to remove.
