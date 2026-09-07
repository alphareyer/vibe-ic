"""The run VERIFIED one image and silently ran another.

`--container` / `--require-image` pin the toolchain, and the runner RECORDS the
result in `reports/container_image.json`. Recording is not using. Steps that
shell out with `docker exec <container>` inherit the pin. Steps that shell out
with `docker run <IMAGE>` resolve an image of their OWN:
`fault_atpg_run._resolve_docker_image()` walks a candidate list and returns the
first tag that happens to be present LOCALLY, falling through to the upstream
`hpretl/iic-osic-tools:latest` — a DIFFERENT DISTRIBUTION, shipping stock tools
without this project's forks, not an older build of ours.

MEASURED on caravel_user_project x sky130A (plugin v1.9.65, die 2920x3520):

    reports/container_image.json
        "image_ref": "ghcr.io/vibeic/vibeic-eda:0.2.58",
        "image_match": true, "verdict": "PASS"
    locally present candidates
        ghcr.io/vibeic/vibeic-eda:0.2.60  absent
        vibeic-eda:0.2.60                 absent
        vibeic/vibeic-eda:0.2.60          absent
        hpretl/iic-osic-tools:latest      PRESENT   <-- what DFT actually used
    `fault chain --help` | grep -c skip-boundary
        ghcr.io/vibeic/vibeic-eda:0.2.58  1
        hpretl/iic-osic-tools:latest      0

Consequence: `fault chain` rc=64 `Unknown option '--skip-boundary'` -> no scan
netlist -> Step 11 DFT FAIL -> **24 downstream steps PASS-VOIDED**. And the
report blamed the operator's image ("this build of the `fault` binary predates
the flag"), which was FALSE — the pinned image has the flag.

Re-running the same step with the run's own image, changing nothing else:

    published: True   skip_boundary: True
    Internal scan chain successfully constructed. Length: 33
    Boundary scan register NOT inserted

Same class as choosing a tech LEF by filesystem order: the artefact is selected
by what is lying around on the host, so a different host implements a different
chip — and the run's own declaration is not what governs.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

_RUNNER_SRC = (_PROGRAMS / "vibe_ic_one_shot_runner.py").read_text(
    encoding="utf-8")
_SCAN_SRC = (_PROGRAMS / "fault_scan_chain_insert.py").read_text(
    encoding="utf-8")


def _capture(monkeypatch_env: dict, rec: dict, tmp: pathlib.Path):
    """Drive the runner's `_capture_container_image` with a stubbed prober and
    a controlled environment; return (record, env_after)."""
    spec = importlib.util.spec_from_file_location(
        "vibe_ic_one_shot_runner", _PROGRAMS / "vibe_ic_one_shot_runner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vibe_ic_one_shot_runner"] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass

    import container_image_provenance as _cip
    real_verify = _cip.verify
    saved = {k: os.environ.get(k)
             for k in ("VIBEIC_EDA_IMAGE", "IIC_EDA_IMAGE")}
    try:
        _cip.verify = lambda *a, **k: dict(rec)          # type: ignore
        for k in ("VIBEIC_EDA_IMAGE", "IIC_EDA_IMAGE"):
            os.environ.pop(k, None)
        os.environ.update(monkeypatch_env)
        out = mod._capture_container_image(tmp, "c", None)
        return out, dict(os.environ)
    finally:
        _cip.verify = real_verify                        # type: ignore
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── the propagation ───────────────────────────────────────────────────────
def test_the_verified_image_is_exported_to_child_docker_run(tmp_path):
    """The fix in one assertion: the image the run VERIFIED becomes the image a
    child that resolves its own will pick.

    RE-ANCHORED ON THE SHAPE (#2085), and the fixture is the real world rather
    than a sketch of it. A container started from a digest-pinned reference has
    that reference verbatim in `.Config.Image`, which is what `image_ref`
    carries; the assertion below used to demand `image_id` instead — the bare
    `sha256:<hex>` Id from `.Image` — and that value is not a reference. The far
    end read it as one, split it into repository `sha256` + tag, and handed
    `docker run` `sha256@sha256:<digest>`: rc=125, and step 0.5ic published
    `database_unit_um` as NOT_DETERMINED with nothing saying the reference had
    been malformed. The property this test was written for is unchanged — the
    verified image reaches the child — and it is now asserted in the one shape
    the child can actually run."""
    rec, env = _capture(
        {}, {"verdict": "PASS",
             "image_ref": "ghcr.io/vibeic/vibeic-eda@sha256:" + "4e" * 32,
             "image_id": "sha256:" + "9f" * 32}, tmp_path)
    want = "ghcr.io/vibeic/vibeic-eda@sha256:" + "4e" * 32
    assert env["VIBEIC_EDA_IMAGE"] == want
    assert rec["propagated_to_child_docker_run"] == want


def test_the_child_env_is_snapshotted_AFTER_the_image_capture():
    """THE ORDERING IS HALF THE FIX, and it is the half that actually reaches
    the delegated phase runners.

    `_runner_lock.child_env()` COPIES `os.environ`; `_capture_container_image`
    is what writes VIBEIC_EDA_IMAGE into it. With the snapshot taken first, the
    export lands in the parent's env only, every delegated phase runner is
    spawned with `env=_phase_env` and never sees it, and the propagation is a
    no-op that the run record nevertheless ATTESTS TO.

    MEASURED with the snapshot in the wrong place (the state this test pins
    against): reports/container_image.json said
        "propagated_via": "VIBEIC_EDA_IMAGE",
        "propagated_to_child_docker_run": "sha256:4e89590fcb9c..."
    while the DFT step, running inside the delegated phase-2 runner, recorded
        "image_used": "hpretl/iic-osic-tools:latest"
        "exit": 64
    A record that claims a propagation the children never received is worse
    than no propagation at all."""
    cap = _RUNNER_SRC.index("_img_rec = _capture_container_image")
    snap = _RUNNER_SRC.index("_phase_env = _runner_lock.child_env")
    assert cap < snap, (
        "child_env() snapshots os.environ, so it must run AFTER the image "
        "capture that writes VIBEIC_EDA_IMAGE into it")


def test_the_immutable_identity_wins_over_the_tag_in_a_shape_that_resolves():
    """A tag can be re-pointed; the digest is exactly what the container runs.
    The whole defect is an identity resolved by something other than the run's
    own declaration, so the strongest available identity is the one to
    propagate — PROVIDED it is one a child can hand to `docker run`.

    THIS TEST WAS `test_the_content_addressed_id_wins_over_the_tag`, and it
    pinned `rec.get("image_id") or rec.get("image_ref")` as source text. That
    expression is #2085: `image_id` is `.Image`, a bare Id, which has no
    repository half. The reasoning behind preferring it was right about
    IMMUTABILITY and wrong about SHAPE, and a source-literal assertion could not
    tell the two apart. Both properties are kept by preferring the
    registry-portable DIGEST REFERENCE, which is immutable AND resolvable; the
    behaviour is asserted directly below and in
    `test_issue2085_the_image_variable_carries_a_reference.py`, and the retired
    expression is named here so it cannot come back."""
    assert 'rec.get("image_id") or rec.get("image_ref")' not in _RUNNER_SRC, (
        "the runner is selecting the container's image Id again; an Id is not "
        "a reference (#2085)")
    assert "_propagatable_image" in _RUNNER_SRC


def test_the_repo_digest_beats_the_tag_the_container_was_started_from(tmp_path):
    """The behavioural half of the test above: started from a TAG, what
    propagates is the portable digest of the bytes that tag resolved to."""
    import _eda_pin as _pin
    ref = "ghcr.io/vibeic/vibeic-eda@sha256:" + "7c" * 32
    real = _pin._docker

    def fake(*argv, timeout=None):
        fmt = list(argv)[list(argv).index("--format") + 1]
        if "Config.Image" in fmt:
            return 0, "sha256:%s\tghcr.io/vibeic/vibeic-eda:0.2.58\n" % ("9f" * 32), ""
        if "RepoDigests" in fmt:
            return 0, '["%s"]\n' % ref, ""
        return 1, "", "unexpected format"

    _pin._docker = fake                                      # type: ignore
    try:
        _rec, env = _capture(
            {}, {"verdict": "PASS",
                 "image_ref": "ghcr.io/vibeic/vibeic-eda:0.2.58",
                 "image_id": "sha256:" + "9f" * 32}, tmp_path)
    finally:
        _pin._docker = real                                  # type: ignore
    assert env["VIBEIC_EDA_IMAGE"] == ref, env["VIBEIC_EDA_IMAGE"]


def test_the_tag_is_used_when_no_portable_digest_could_be_named(tmp_path):
    """A tag is mutable and is still a REFERENCE. When no registry digest can
    be recovered — a locally built image, or a container docker will not
    describe — the tag is what a child can run, and withholding it would revive
    the very defect this capture exists for: a child resolving an image of its
    own and landing on upstream iic-osic-tools."""
    _, env = _capture({}, {"verdict": "PASS",
                           "image_ref": "ghcr.io/vibeic/vibeic-eda:0.2.58"},
                      tmp_path)
    assert env["VIBEIC_EDA_IMAGE"] == "ghcr.io/vibeic/vibeic-eda:0.2.58"


# ── no-leak boundary (§4.05): what it must NOT overwrite ──────────────────
def test_an_operator_set_VIBEIC_EDA_IMAGE_is_NOT_overwritten(tmp_path):
    """NO-LEAK. Deliberately running the flow against a different image is a
    real experiment (it is how the 0.2.52-vs-0.2.54 `--skip-boundary` behaviour
    was measured in the first place). Filling an EMPTY slot fixes the defect;
    overwriting a set one would silently destroy that experiment."""
    rec, env = _capture({"VIBEIC_EDA_IMAGE": "operator/pinned:1.2.3"},
                        {"verdict": "PASS",
                         "image_ref": "ghcr.io/vibeic/vibeic-eda:0.2.58",
                         "image_id": "sha256:4e89590fcb9c"}, tmp_path)
    assert env["VIBEIC_EDA_IMAGE"] == "operator/pinned:1.2.3"
    assert rec["propagated_to_child_docker_run"] is None


def test_the_legacy_IIC_EDA_IMAGE_override_is_also_respected(tmp_path):
    """NO-LEAK. `_resolve_docker_image` honours BOTH names; propagating over
    the legacy one would change the resolved image for anyone still using it."""
    _, env = _capture({"IIC_EDA_IMAGE": "legacy/pinned:9"},
                      {"verdict": "PASS",
                       "image_ref": "ghcr.io/vibeic/vibeic-eda:0.2.58",
                       "image_id": "sha256:4e89590fcb9c"}, tmp_path)
    assert "VIBEIC_EDA_IMAGE" not in env


def test_an_unresolvable_image_propagates_NOTHING(tmp_path):
    """NO-LEAK. When the probe could not identify an image there is nothing
    verified to propagate; inventing one would be worse than the fallback."""
    rec, env = _capture({}, {"verdict": "SKIP",
                             "reason": "image identity unverifiable"}, tmp_path)
    assert "VIBEIC_EDA_IMAGE" not in env
    assert "propagated_to_child_docker_run" not in rec


# ── the diagnostic must not name a cause it did not measure ───────────────
def test_the_skip_boundary_error_names_the_image_that_actually_ran():
    """The old text asserted "this build of the `fault` binary predates the
    flag". On the measured run that was false: the pinned image HAS the flag,
    a different one ran. A diagnostic naming the wrong cause is worse than one
    naming none — the reader checks their image, finds it new enough, and
    stops."""
    # RE-ANCHORED on the CONTAINER branch. The message is now route-branched:
    # on the LOCAL route no image runs at all, so naming one would be the very
    # defect this test exists to prevent, pointing the other way. A fixed-width
    # slice from `image_used` no longer spans the right text, so the branch is
    # located by its own delimiter instead of by character arithmetic.
    seg = _container_arm(_SCAN_SRC)
    assert "_fatpg.DOCKER_IMAGE" in seg, "the error must name the image used"
    assert "reports/container_image.json" in seg, "and what to compare it to"


def _container_arm(src: str) -> str:
    """The `--skip-boundary` diagnostic's CONTAINER branch, by delimiter."""
    assert 'if err_report.get("exec_route") == "local":' in src, (
        "the diagnostic is no longer route-branched")
    return src.split('if err_report.get("exec_route") == "local":')[1]               .split("            else:")[1]


def _local_arm(src: str) -> str:
    return src.split('if err_report.get("exec_route") == "local":')[1]               .split("            else:")[0]


def test_the_local_route_names_no_image_and_no_docker_command():
    """The mirror of the test above, and the reason it had to be re-anchored:
    with no docker client no image is started, and `DOCKER_IMAGE` is whatever
    the resolver fell back to when the registry was unreachable. Naming it, or
    offering a `docker run` the reader cannot execute, is a diagnostic naming
    the wrong cause — which this file exists to refuse."""
    seg = _local_arm(_SCAN_SRC)
    assert "No image ran" in seg
    assert "DOCKER_IMAGE" not in seg, seg
    assert "docker run" not in seg, seg


def test_the_error_lists_BOTH_causes_not_just_the_age_one():
    """The defect was a SINGLE-cause claim, not the mention of age. Age is a
    real cause; it just is not the only one that produces this error, and it
    was not the one that produced it on the measured run. The report must
    carry both so the reader checks the image identity before the version."""
    seg = _container_arm(_SCAN_SRC)
    assert "predates" in seg, "the age cause is still offered"
    assert "DIFFERENT " in seg.upper() or "different image" in seg.lower(), \
        "the wrong-image cause must be offered too"


def test_the_symptom_detector_does_not_assert_a_cause():
    """`skip_boundary_unsupported_in_log` detects the tool's error string. Its
    docstring used to conclude the cause from it ("this build of `fault`
    predates the flag"); that conclusion is how the wrong cause reached the
    report."""
    i = _SCAN_SRC.index("def skip_boundary_unsupported_in_log")
    doc = _SCAN_SRC[i:i + 1200]
    assert "SYMPTOM only" in doc
    assert "does NOT establish the cause" in doc.replace("not", "NOT")
