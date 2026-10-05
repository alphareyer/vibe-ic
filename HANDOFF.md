# Professional-TB container path repair handoff

## Identity and preservation

- Worker clone: `/home/reyerchu/codex_tasks/spmtbpath1006-clean`
- Base commit: `0daf7a0d2fb51e484c3e89921e7300f4e6b2bad7`
- Base tree: `9653c45e4e6e5505305a06a98b666bee3686efac`
- The prior area diagnosis at `/home/reyerchu/codex_tasks/spmarea1006r1-clean` was not modified.
- No `main`/version change, push, native rerun, RTL edit, golden edit, or harness inspection was performed.

## Observed red and contract

The supplied `/tmp/spm-native1006.log` (sha256
`4ce8e0c68f18c5f3b46890752b2b344fd4ef5a1835eb480b182843a70bea7124`) records
`professional_tb_gen` generating and running the `spm` serial-stream bundle,
then refusing it because:

```text
bash: line 1: cd: /home/reyerchu/codex_tasks/runs/spmcanonical1006r2/phase2/stage1/sim_professional/spm: No such file or directory
```

The producer `professional_tb_gen.generate()` publishes the host-side bundle
at `_pl.rtl_dir(project).parent / sim_professional / top`
(`programs/professional_tb_gen.py:846-907`). The consumer
`design_one_shot_runner.step_professional_tb_gen` dispatched
`cd '<host out_dir>' && make SIM=icarus` through `_docker_exec`
(`programs/design_one_shot_runner.py:9820-10015`). The existing
`_container_mounts`/`_to_container_path` seam already defines the host-to-image
mapping (`:867-915`), and the native log proves the unconverted host spelling
was consumed inside the image.

## Repair

`step_professional_tb_gen` now:

1. refuses by name with `NOT_MEASURED` when the generated bundle directory is
   not visible through a configured container bind mount;
2. translates `out_dir` through `_to_container_path` for the container `cd`
   command and watchdog process marker;
3. keeps `cocotb_run.log` on the host-side run tree so the bind-mounted output
   remains readable by downstream consumers; and
4. retains the existing no-bundle and no-JUnit refusal semantics.

The production change is only in
`programs/design_one_shot_runner.py`. The focused regression is
`programs/tests/test_spm_professional_tb_container_path.py`.

## Controls and tests

The exact-base control used an archived `0daf7a0d` tree plus the new test:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider \
  programs/tests/test_spm_professional_tb_container_path.py \
  --junitxml=/tmp/spm-tb-path-control-final.xml
```

It produced `1 failed, 1 passed`; `control_substance_check.py` classified the
failure as substantive (`1 of 1 reported failures observed a VALUE`, rc=0).

On the repaired clone, the new tests pass:

```text
test_container_dispatch_consumes_the_mount_translated_bundle_path PASSED
test_missing_generated_bundle_is_refused_without_dispatch PASSED
2 passed in 0.54s
```

The bounded focused suite covering the professional-TB path, existing bundle
and simulator provenance controls, and host fallback is:

```text
116 passed in 6.41s
OUTCOME_STATES FAIL=0 NOT_VERIFIED=0 NOT_MEASURED=0 BOOKKEEPING=0
```

Relevant production and regression files pass `python3 -m py_compile`.
`source_chip_agnostic_check.py .` also passed (`1983 file(s) scanned`, zero
forbidden-token findings). No whole `programs/tests` run was attempted.

The prior native run remains historical evidence of the path bug; a fresh SPM
native run is required after landing to measure the real GF180 professional-TB
bundle. The fix does not manufacture a JUnit result when the bundle or mount
is absent.
