"""The three runtime identities are reconciled BEFORE the first worker (#2120).

WHAT WENT WRONG, AND WHY NOTHING CAUGHT IT
==========================================
Plugin 1.18.68 and the published EDA 0.3.48 each passed their own checks and did
not form a runnable pair:

    the installed pin required             sha256:8c5694ab…
    a fresh pull of the published :latest  sha256:1463dac5…
    …whose own version label ALSO read     0.3.48

Same version, different build, different bytes. A freshly started MCP reported
``16/16 checks passed`` against the upgraded shared container — true, and about
tool availability, which is not a statement about which container the dispatcher
selects or whether it accepts its digest. The dispatcher fanned out anyway,
every worker recorded provenance about a container the pin does not name, and
the batch had to be stopped and excluded from results.

WHAT EACH TEST HERE WOULD MISS IF IT WERE WRITTEN LAZILY is stated on the test.

THE FAN-OUT ASSERTION IS A MEMBERSHIP ONE, NOT A COUNT. `_ordered_parallel_map`
is replaced by a recorder and the test asserts it was never ENTERED. Asserting
"no results were written" would pass just as well if the workers ran and their
output was discarded, which is the outcome that actually happened in #2120.

THE REAL-IMAGE ARM. Two images that BOTH label themselves ``0.3.48`` exist on
this fleet, and the same-version/different-digest case cannot be constructed
honestly from a stub: a stub proves the code reads a field, and the report is
about two real builds a human could not tell apart. Those tests skip — never
pass — when either image is absent.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import _eda_pin as P  # noqa: E402
import _runtime_pair_preflight as R  # noqa: E402
import benchmark_dispatch as BD  # noqa: E402

#: The canonical published 0.3.48. A DIFFERENT build from the pin, carrying the
#: SAME version label — the whole point of #2120 and the reason a version
#: literal may not stand in for a digest anywhere in this repo.
_OTHER_0348 = ("sha256:1463dac58116ca6650ec84e9e4b11a73a70f4094"
               "c95a9a19e620482251471c57")


# ── the module: three checks, three codes ────────────────────────────────────

def test_a_matching_pair_is_RUNTIME_PAIR_MATCH(monkeypatch):
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest",
                        lambda c: (P.IMAGE_DIGEST, ""))
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MATCH
    assert rec["disagreed"] is None
    assert [row["check"] for row in rec["checks"]] == list(R.PREFLIGHT_CHECKS), (
        "all three identities must be asked, by name, every time — a preflight "
        "that skips a check when an earlier one passed cannot say which "
        "disagreed")
    assert all(row["ok"] for row in rec["checks"])


def test_an_absent_container_names_CONTAINER_ABSENT_and_not_a_mismatch(monkeypatch):
    """"The container is gone" and "the container is the wrong build" need two
    different actions. A preflight that collapses them sends the operator to
    re-pull an image that is already there."""
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (
        None, f"{P.CONTAINER_ABSENT}: no container named {c}"))
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MISMATCH
    assert rec["disagreed"] == "default_container_name"
    codes = {row["check"]: row["code"] for row in rec["checks"]}
    assert codes["default_container_name"] == P.CONTAINER_ABSENT
    assert P.CONTAINER_IMAGE_MISMATCH not in json.dumps(rec)
    assert rec["found_digest"] is None, (
        "NOT-READ is not a digest; supplying one here would be the default "
        "this repo refuses everywhere else")


def test_an_absent_image_is_reported_against_the_image_not_the_container(monkeypatch):
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: (None, f"{P.IMAGE_NOT_PRESENT}: repo@x"))
    monkeypatch.setattr(P, "container_image_digest",
                        lambda c: (P.IMAGE_DIGEST, ""))
    rec = R.preflight()
    assert rec["disagreed"] == "pinned_image_present"
    assert rec["evidence"] and rec["evidence"][0].startswith(P.IMAGE_NOT_PRESENT)


def test_the_health_line_carries_the_two_digests_and_never_a_repository(monkeypatch):
    """A registry address is deployment configuration. It must not travel in a
    line that lands in a report, an artefact, or a paste."""
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("some.registry.example/x@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_OTHER_0348, ""))
    line = R.pair_line(R.preflight())
    assert R.RUNTIME_PAIR_MISMATCH in line
    assert P.IMAGE_DIGEST in line and _OTHER_0348 in line
    assert "disagreed=container_matches_pin" in line
    assert "/" not in line and "registry" not in line, line


def test_the_evidence_is_the_refusal_VERBATIM(monkeypatch):
    """A preflight that paraphrases hands the reader a summary of a mismatch
    instead of the mismatch, and the reader re-runs it to see for themselves."""
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_OTHER_0348, ""))
    rec = R.preflight()
    expected = P.container_matches_pin(rec["container"])
    assert expected, "the fixture must produce a refusal for this to measure"
    assert R.evidence_text(rec) == expected


def test_no_second_copy_of_the_digest_is_spelled_in_this_module():
    """The pin is stated ONCE. A second literal is the defect `_eda_pin` exists
    to end, and it would freeze silently the next time the pin moves."""
    src = (_PROGRAMS / "_runtime_pair_preflight.py").read_text(encoding="utf-8")
    assert P.IMAGE_DIGEST not in src
    assert "8c5694abdf5c269c" not in src.replace("sha256:8c5694ab…", "")


# ── the dispatcher: ZERO workers, and the control that it can reach them ─────

class _NeverEntered:
    def __init__(self):
        self.entered = 0

    def __call__(self, items, worker, jobs):
        self.entered += 1
        return []


def _solve(monkeypatch, tmp_path, digest_answer):
    """Drive the real coordinator far enough to fan out, and record whether it did."""
    recorder = _NeverEntered()
    monkeypatch.setattr(BD, "_ordered_parallel_map", recorder)
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", digest_answer)
    run_p = tmp_path / "run"
    rc = BD._cmd_solve_locked("rtllm", str(tmp_path / "absent-dataset"),
                              str(run_p))
    return rc, recorder, run_p


def test_a_mismatched_pair_launches_ZERO_workers(monkeypatch, tmp_path, capsys):
    """THE CASE THIS EXISTS FOR. Not "the run produced no score" — the workers
    must never be ENTERED, because in #2120 they ran and their provenance was
    about the wrong container."""
    rc, recorder, run_p = _solve(
        monkeypatch, tmp_path, lambda c: (_OTHER_0348, ""))
    assert rc == 2
    assert recorder.entered == 0, "the dispatcher fanned out over a bad pair"
    err = capsys.readouterr().err
    assert R.RUNTIME_INFRASTRUCTURE_NOT_READY in err
    assert P.CONTAINER_IMAGE_MISMATCH in err
    assert "0 worker(s) launched" in err
    assert "NOT a design result" in err


def test_an_absent_container_launches_ZERO_workers_and_says_which(monkeypatch,
                                                                  tmp_path,
                                                                  capsys):
    rc, recorder, run_p = _solve(monkeypatch, tmp_path, lambda c: (
        None, f"{P.CONTAINER_ABSENT}: no container named {c}"))
    assert rc == 2 and recorder.entered == 0
    err = capsys.readouterr().err
    assert P.CONTAINER_ABSENT in err
    assert "default_container_name disagreed" in err


def test_the_refusal_is_recorded_OUTSIDE_the_run_root(monkeypatch, tmp_path):
    """A refusal must not be the thing that stops the CORRECTED re-run. Dropping
    the record into the run root would make the next `--solve` refuse it as a
    non-empty clean room, and the operator would read the clean-room message
    instead of the mismatch that caused it."""
    _, _, run_p = _solve(monkeypatch, tmp_path, lambda c: (_OTHER_0348, ""))
    sibling = run_p.parent / f"{run_p.name}.runtime_pair_preflight.json"
    assert sibling.is_file(), sorted(p.name for p in tmp_path.iterdir())
    assert not run_p.exists() or not any(run_p.iterdir())
    rec = json.loads(sibling.read_text())
    assert rec["failure_class"] == R.RUNTIME_INFRASTRUCTURE_NOT_READY
    assert rec["workers_launched"] == 0
    assert rec["disagreed"] == "container_matches_pin", (
        "the record must say WHICH of the three identities disagreed")
    assert rec["operation"] == "solve"


def test_CONTROL_a_matching_pair_does_reach_the_fan_out(monkeypatch, tmp_path):
    """THE CONTROL, in the direction that matters. Without it every assertion
    above is satisfied by a gate that refuses everything — a check that cannot
    pass is not a check, and it would take the whole dispatcher down with it.

    Only the two things a matching pair does not need are stubbed: a dataset on
    disk, and the clean-room envelope that reads it. The gate, its record, and
    the coordinator's own ordering are the real ones."""
    import benchmark_io_adapter as bio                     # noqa: PLC0415
    monkeypatch.setattr(BD, "_prepare_general_solve_run",
                        lambda bench, ds, run, fmt, limit: run.mkdir(
                            parents=True, exist_ok=True))
    monkeypatch.setattr(bio, "problems",
                        lambda fmt, ds: [{"id": "p-1", "prompt": "x"}])
    rc, recorder, run_p = _solve(monkeypatch, tmp_path,
                                 lambda c: (P.IMAGE_DIGEST, ""))
    assert recorder.entered == 1, (
        f"a matching pair must follow the unchanged general entry (rc={rc})")
    rec = json.loads((run_p / "runtime_pair_preflight.json").read_text())
    assert rec["verdict"] == R.RUNTIME_PAIR_MATCH
    assert rec["operation"] == "solve"


def test_the_gate_is_asked_before_the_run_root_is_built(monkeypatch, tmp_path):
    """Order is the fix. Asking after the clean room is created leaves a half
    run behind on every refusal; asking per worker gives N identical refusals
    and N partial projects."""
    seen = []
    monkeypatch.setattr(BD, "_prepare_general_solve_run",
                        lambda *a, **k: seen.append("prepared"))
    monkeypatch.setattr(BD, "_ordered_parallel_map", _NeverEntered())
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_OTHER_0348, ""))
    assert BD._cmd_solve_locked("rtllm", str(tmp_path / "d"),
                                str(tmp_path / "run")) == 2
    assert seen == [], "the clean room was built for a run that could not start"


# ── the same-version/different-digest case, on the two REAL images ───────────

def _have(digest: str) -> bool:
    ref = f"{P.image_repo()}@{digest}"
    return bool(P.local_repo_digests(ref)[0])


def _requires_both_builds():
    if not _have(P.IMAGE_DIGEST) or not _have(_OTHER_0348):
        pytest.skip("both 0.3.48 builds must be present to measure the "
                    "same-version/different-digest case")


@pytest.fixture()
def real_container():
    """A container CREATED (never started) from a chosen digest, removed by the
    name this fixture recorded — never by a pattern that could match another
    lane's container."""
    made: list[str] = []

    def _make(digest: str) -> str:
        name = f"cz2120-{uuid.uuid4().hex[:10]}"
        subprocess.run(
            ["docker", "create", "--name", name,
             f"{P.image_repo()}@{digest}", "true"],
            check=True, capture_output=True, text=True, timeout=120)
        made.append(name)
        return name

    yield _make
    for name in made:
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, timeout=120)


def test_REAL_two_builds_labelled_0348_are_not_interchangeable(real_container,
                                                               monkeypatch):
    """Both images answer `0.3.48` to the question a human asks. The digests are
    what the runtime is, and this is the pair the report was written about."""
    _requires_both_builds()
    other = real_container(_OTHER_0348)
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, other)
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MISMATCH
    assert rec["container"] == other
    assert rec["found_digest"] == _OTHER_0348
    assert rec["disagreed"] == "container_matches_pin"
    assert R.evidence_text(rec).startswith(P.CONTAINER_IMAGE_MISMATCH)


def test_REAL_the_pinned_build_is_a_MATCH(real_container, monkeypatch):
    """The control on real bytes. Without it the test above is satisfied by code
    that calls every container a mismatch."""
    _requires_both_builds()
    name = real_container(P.IMAGE_DIGEST)
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, name)
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MATCH, R.evidence_text(rec)
    assert rec["found_digest"] == P.IMAGE_DIGEST


def test_REAL_the_issue_acceptance_assertion_passes_on_a_matching_pair(
        real_container, monkeypatch):
    """The read-only assertion #2120 asks for, run VERBATIM against the
    container the runner actually chooses."""
    _requires_both_builds()
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, real_container(P.IMAGE_DIGEST))
    run = subprocess.run(
        [sys.executable, "-c",
         'import _eda_pin as p; ref, why = p.pinned_image_present(); '
         'assert ref, why; name = p.default_container_name(); '
         'why = p.container_matches_pin(name); assert not why, why; '
         'print("RUNTIME_PAIR_MATCH", name, ref)'],
        cwd=str(_PROGRAMS), capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stderr
    assert run.stdout.startswith("RUNTIME_PAIR_MATCH")


def test_REAL_the_acceptance_assertion_fails_BY_NAME_on_the_other_build(
        real_container, monkeypatch):
    """A read-only assertion that cannot fail proves nothing about the pair."""
    _requires_both_builds()
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, real_container(_OTHER_0348))
    run = subprocess.run(
        [sys.executable, "-c",
         'import _eda_pin as p; ref, why = p.pinned_image_present(); '
         'assert ref, why; name = p.default_container_name(); '
         'why = p.container_matches_pin(name); assert not why, why; '
         'print("RUNTIME_PAIR_MATCH", name, ref)'],
        cwd=str(_PROGRAMS), capture_output=True, text=True, timeout=180)
    assert run.returncode != 0
    assert P.CONTAINER_IMAGE_MISMATCH in run.stderr
    assert _OTHER_0348 in run.stderr and P.IMAGE_DIGEST in run.stderr


# ── resume: the coordinator whose fan-outs are CONDITIONAL ──────────────────

def _mismatched(monkeypatch):
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_OTHER_0348, ""))


def test_resume_refuses_at_a_fan_out_that_has_work(monkeypatch, tmp_path, capsys):
    _mismatched(monkeypatch)
    run_p = tmp_path / "run"
    run_p.mkdir()
    assert BD._runtime_pair_before_fan_out(
        [{"id": "p1"}], run_p, "resume:retry") == 2
    rec = json.loads((run_p / "runtime_pair_preflight.json").read_text())
    assert rec["failure_class"] == R.RUNTIME_INFRASTRUCTURE_NOT_READY
    assert rec["operation"] == "resume:retry", (
        "the record must name WHICH fan-out was refused, not just 'resume'")
    assert rec["workers_launched"] == 0
    assert P.CONTAINER_IMAGE_MISMATCH in capsys.readouterr().err


def test_CONTROL_resume_with_nothing_to_run_is_not_blocked_by_the_pair(
        monkeypatch, tmp_path):
    """Most of `--resume` is bookkeeping — accepting a review, refusing a
    re-entry under a stale source identity, writing a worklist — and none of it
    touches a container. Refusing that on the state of the machine would answer
    a routing question with a deployment one, and `_ordered_parallel_map` never
    enters a worker for an empty list anyway.

    THE DIRECTION THAT MATTERS: this test is red if the gate is hoisted back to
    the top of the resume coordinator."""
    _mismatched(monkeypatch)
    assert BD._runtime_pair_before_fan_out([], tmp_path, "resume:retry") is None
    assert not (tmp_path / "runtime_pair_preflight.json").exists(), (
        "a resume that was never going to launch a worker must not record an "
        "infrastructure refusal it did not make")


def test_every_conditional_fan_out_in_resume_is_guarded():
    """MEMBERSHIP, not a count. A fourth fan-out added later must be guarded
    too, and the way to notice is to compare the SET of `_ordered_parallel_map`
    call sites in the resume coordinator against the SET that is preceded by
    the guard — never to assert 'there are three'."""
    import ast                                             # noqa: PLC0415
    src = (_PROGRAMS / "benchmark_dispatch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    resume = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_cmd_resume_locked")
    def _called(fn):
        return {n.lineno for n in ast.walk(resume)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == fn}
    fan_outs = _called("_ordered_parallel_map")
    guards = _called("_runtime_pair_before_fan_out")
    assert fan_outs, "the resume coordinator fans out somewhere"
    assert len(guards) == len(fan_outs), (
        f"fan-outs at {sorted(fan_outs)} but guards at {sorted(guards)}")
    for line in fan_outs:
        assert any(0 < line - g < 12 for g in guards), (
            f"the fan-out at line {line} is not preceded by a pair guard")
