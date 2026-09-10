# Two-arm evidence — corpus subject bounded at the repository

`falsref.sh` is NOT on 8hd-3 (`/home/reyerchu/fleet_scripts/` holds nine scripts and none is
it; 192.168.1.112/.120 refuse ssh with publickey, .122 times out). The arms below were
therefore built by hand to the definition the auto-lander states, with `git archive` so no arm
can carry the other's mtimes or `__pycache__`, and verified BY BLOB.

base (live main) : e777ed7fe5143fe649fe57abcd8717d546f4d39b
branch           : next/mrskip

## Blob check — R must carry main's sources, G must not

    _plugin_tree.py                                main=5fb52908 R=5fb52908 G=92ef322f
    test_l9_memory_generator_macro_identifier.py   main=73f03b08 R=73f03b08 G=8ffff303
    test_v0_2_97_issue466_real_input_fixture.py    main=00ad05b6 R=00ad05b6 G=3563a197
    test_ic_class_stability_fixtures.py            main=d3b40378 R=d3b40378 G=95ae7d10

## Arm R = live main sources + ONLY the new test  ->  RED

    4 failed, 3 passed

    FAILED ...::test_a_corpus_above_the_repository_is_never_the_subject[test_l9_memory_generator_macro_identifier.py-PLUGIN-_real_docs_dir-tail0]
    FAILED ...::test_a_corpus_above_the_repository_is_never_the_subject[test_v0_2_97_issue466_real_input_fixture.py-_PLUGIN_ROOT-_candidate_benchmark_dirs-tail1]
    FAILED ...::test_a_corpus_above_the_repository_is_never_the_subject[test_ic_class_stability_fixtures.py-_PLUGIN_ROOT-_repo_roots-tail2]
    FAILED ...::test_no_test_module_resolves_a_corpus_by_walking_to_the_filesystem_root

The three parametrised failures are BEHAVIOURAL: each calls the module's own resolver, which
exists on main, and each resolver really does bind a `benchmark-data` planted above the
synthetic repository. The three POSITIVE-half cases PASS on R, so the red arm is red for the
defect and not for the feature.

(The first version of this test asserted against `_plugin_tree.repo_root_of` /
`corpus_search_roots`, which do not exist on main, and five of six failures were
`AttributeError` — a control that observed only that the fix was absent. That version was
replaced, not kept.)

## Arm G = live main + the whole change  ->  GREEN

    7 passed

## The property, measured both ways

Same arm, two LOCATIONS. `/home/reyerchu` holds a clone of `vibeic/benchmark-data`;
`/tmp` does not. The three affected modules only:

    arm  location                     result
    R    under $HOME                  71 passed,  5 skipped
    R    under /tmp                   50 passed, 26 skipped     <- 21 nodes move
    G    under $HOME                  50 passed, 26 skipped
    G    under /tmp                   50 passed, 26 skipped     <- 0 nodes move

Live main's verdict is a function of where the checkout sits, by 21 nodes. After the change it
is not, and it agrees with the location-independent answer. The direction is deliberate: the
change REMOVES 21 apparent passes. They were green against a corpus no run record names, so
they were never measuring this repository; an honest skip is the correct outcome and the
existing `VIBE_IC_BENCHMARK_DATA` pointer is how a run that wants them binds a real one.

## Regression control — identical 76-file selection on BOTH arms

The selection is every `programs/tests/test_*.py` that names `_plugin_tree`, taken from the G
arm and verified present in R, so both arms run the same files.

    arm R (live main sources + the new test)   16 failed, 1240 passed, 126 skipped
    arm G (main + the whole change)            12 failed, 1223 passed, 147 skipped

    failures unique to G (introduced by this change) : 0
    failures unique to R (closed by this change)     : 4  — exactly the new test's
                                                         four defect observations

The 12 remaining failures fail on BOTH arms, so they are main's and not this branch's.
Skips move 126 -> 147, i.e. **+21**: exactly the 21 apparent passes this change converts into
honest skips, and the same 21 the location A/B above measures.
