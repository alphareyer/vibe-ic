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

THE REAL-IMAGE ARM. A stub proves the code reads a field; the report is about two
real builds a human could not tell apart, so the mismatch is also driven against
two containers created from two real digests. Those tests skip — never pass —
when either image is absent from this host.

WHICH DIGEST IS "THE OTHER BUILD", AND WHY IT IS NOT WRITTEN DOWN (#2163)
========================================================================
It used to be, and that literal is the second defect this file has carried. The
constant was ``_OTHER_0348 = sha256:1463dac5…``, documented as "a DIFFERENT build
from the pin". It was, for nine minutes: #2120 landed at 13:10 naming it, and
#2115 moved ``IMAGE_DIGEST`` ONTO that exact digest at 13:19. From then until
#2149 moved the pin off it, every arm here that expects a RUNTIME_PAIR_MISMATCH
was comparing the pin with itself — eight ids red on pristine main, on every host
holding the image, and nothing said WHY. A drift net could not have caught it
either: the pinned-digest net reads DECLARATIONS (``*IMAGE_DIGEST`` assignments,
``runner.image``), and a constant that means "not the pin" is invisible to it.

So neither of the two "other" digests here is a literal that a later pin move can
land on:

  * the STUB arms take ``_NOT_THE_PIN``, a SYNTHETIC digest. It is not any build
    and never will be one, so no pin move can ever silence them. Those arms only
    ever needed "some other bytes"; nothing they assert wanted a real image.
  * the REAL arms take ``_previous_pin()``, DERIVED by reading
    ``IMAGE_DIGEST`` out of ``tools/ci/hermetic_candidate_runner.py`` — the
    authority every other copy of the pin is a copy OF — at each commit that
    touched it, and taking the newest value that is not the current one. After
    any pin move the previous pin is, by construction, not the pin; and it is the
    build a host is most likely to still be holding. When the checkout is not a
    git work tree the derivation says so and those arms SKIP: "I could not read
    it" is not "I read it and it was the pin".

``_refuse_a_collided_other`` states the failure in one sentence for both, and
``test_MUTANT_…`` points an "other" digest at the pin to prove that sentence
fires — because an arm that can be satisfied by the pin compared with itself is
an arm that measures nothing.
"""
from __future__ import annotations

import ast
import json
import re
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

_DIGEST_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

#: THE STUB ARMS' OTHER BUILD. Synthetic, and that is the point: it names no
#: image, so no pin move can ever land on it and quietly turn a mismatch arm into
#: the pin compared with itself (#2163). Every arm that takes it is testing
#: composition, ordering or a refusal's wording, and none of them ever needed the
#: other digest to be a real build — the REAL arms below are what that is for.
_NOT_THE_PIN = "sha256:" + "0" * 64

#: The file the whole repository pins with. Read, never copied — the same
#: authority `test_the_run_path_resolves_the_pinned_image._runner_pin` reads, by
#: the same means, so the two cannot disagree about WHERE the pin lives.
_PIN_AUTHORITY = "tools/ci/hermetic_candidate_runner.py"


def _repo_root():
    """The checkout root, or None when this is not run from a full checkout."""
    for parent in [_PROGRAMS, *_PROGRAMS.parents]:
        if (parent / _PIN_AUTHORITY).is_file():
            return parent
    return None


def _declared_digest(source: str):
    """`IMAGE_DIGEST` as the authority DECLARES it, by AST.

    By AST and not by regex for the reason the drift net gives: this file quotes
    digests in prose that it does not declare, and a text scan cannot tell the
    two apart.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue
        for target in node.targets:
            if getattr(target, "id", "") == "IMAGE_DIGEST":
                return node.value.value
    return None


def _pin_history(limit: int = 40):
    """Every value `IMAGE_DIGEST` has held in the authority, newest first.

    Consecutive duplicates are collapsed, so the list is the sequence of PINS and
    not the sequence of commits: a commit that touched the file without moving
    the pin does not put a second copy of the same digest in it.
    """
    root = _repo_root()
    if root is None:
        return []
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%H", "-n", str(limit),
         "--", _PIN_AUTHORITY],
        capture_output=True, text=True, timeout=120)
    if log.returncode != 0:
        return []
    values: list[str] = []
    for sha in log.stdout.split():
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{_PIN_AUTHORITY}"],
            capture_output=True, text=True, timeout=120)
        if blob.returncode != 0:
            continue
        digest = _declared_digest(blob.stdout)
        if digest and (not values or values[-1] != digest):
            values.append(digest)
    return values


def _previous_pin():
    """THE REAL ARMS' OTHER BUILD: the pin this repository held before this one.

    None when it cannot be READ — not a git work tree, no earlier value, or a
    history whose newest declared value is not the digest the plugin actually
    pins (which would mean this function is reading the wrong thing and must not
    answer at all).
    """
    history = _pin_history()
    if not history or history[0] != P.IMAGE_DIGEST:
        return None
    for digest in history[1:]:
        if digest != P.IMAGE_DIGEST:
            return digest
    return None


def _refuse_a_collided_other(digest, where: str) -> None:
    """The one sentence #2163 exists to make possible.

    Without it, an "other" digest that has become the pin surfaces as eight
    unrelated-looking assertion failures about health lines, worker counts and
    run roots — none of which says that the file has stopped measuring anything.
    """
    assert digest != P.IMAGE_DIGEST, (
        f"{where} IS the pin ({digest}), so every arm that expects a "
        f"RUNTIME_PAIR_MISMATCH here is comparing the pin with itself and this "
        f"file measures nothing. This is not a stub that broke. It happened: "
        f"#2120 named the then-other build at 13:10 and #2115 moved the pin onto "
        f"that digest at 13:19, and eight ids stayed red until #2149 moved the "
        f"pin off it. Point {where} at a digest that is not the pin.")


# ── #2163: the "other" digest can never quietly become the pin ──────────────

def test_the_declared_other_digest_is_NEVER_the_pin():
    """THE GUARD, in the direction that decays silently.

    A literal that means "not the pin" is invisible to every drift net in this
    repo — those read DECLARATIONS (`*IMAGE_DIGEST`, `runner.image`), and this is
    the opposite of a declaration. So the binding has to be an assertion or it is
    nothing, and it has to be its OWN id: when the collision happened the only
    signal was eight arms failing about health lines and worker counts, none of
    which said the file had stopped measuring anything.
    """
    assert _DIGEST_RE.match(_NOT_THE_PIN), _NOT_THE_PIN
    _refuse_a_collided_other(_NOT_THE_PIN, "_NOT_THE_PIN")


def test_the_DERIVED_previous_pin_is_READ_from_the_authority_and_is_not_the_pin():
    """NON-VACUITY of the derivation, and the proof it reads the right file.

    `history[0] == P.IMAGE_DIGEST` is the load-bearing half: a derivation that
    walked some other file, or parsed nothing, would answer with a digest that
    is not the pin just as happily, and this arm would pass while measuring the
    wrong thing entirely.
    """
    history = _pin_history()
    if not history:
        pytest.skip(f"the history of {_PIN_AUTHORITY} is UNREADABLE here — it "
                    "is not in this checkout, or this checkout is not a git "
                    "work tree. NOT MEASURED, and nothing is assumed in its "
                    "place")
    assert history[0] == P.IMAGE_DIGEST, (
        f"the newest digest declared in {_PIN_AUTHORITY} is {history[0]} but the "
        f"plugin pins {P.IMAGE_DIGEST}; this derivation is reading something "
        "that is not the authority, so nothing it returns can be trusted")
    other = _previous_pin()
    assert other is not None, (
        f"{_PIN_AUTHORITY} declares only one digest in the last "
        f"{len(history)} value(s) of its history, so there is no previous pin "
        "to drive the real arms with")
    assert _DIGEST_RE.match(other), other
    _refuse_a_collided_other(other, "_previous_pin()")


def test_MUTANT_an_other_digest_equal_to_the_pin_is_REFUSED_and_cannot_pass(
        monkeypatch):
    """THE MUTATION ARM. Point the "other" digest at the pin — the only mutation
    that matters here, because it is the one that happened — and BOTH halves
    must hold:

      1. the guard refuses it BY NAME, so a reader is told what is wrong rather
         than left to infer it from eight unrelated-looking failures;
      2. and even with the guard removed, the arms still could not pass: the
         preflight answers RUNTIME_PAIR_MATCH for a container running the pinned
         bytes, which is the opposite of what every mismatch arm asserts.

    Without (2) this test would only prove that an assertion I just wrote fires.
    """
    with pytest.raises(AssertionError) as refusal:
        _refuse_a_collided_other(P.IMAGE_DIGEST, "_NOT_THE_PIN")
    assert "IS the pin" in str(refusal.value)
    assert P.IMAGE_DIGEST in str(refusal.value)

    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest",
                        lambda c: (P.IMAGE_DIGEST, ""))
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MATCH, (
        "the mutation must produce a MATCH — that is exactly why the mismatch "
        "arms went red instead of silently green, and why a collided constant "
        "can never be mistaken for a working test")
    assert rec["verdict"] != R.RUNTIME_PAIR_MISMATCH
    assert rec["disagreed"] is None


def test_CONTROL_the_guard_accepts_a_digest_that_is_not_the_pin():
    """A check that cannot pass is not a check either: the guard must accept the
    ordinary case, or it would refuse every value and prove nothing."""
    _refuse_a_collided_other("sha256:" + "f" * 64, "_a_control_digest")


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
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_NOT_THE_PIN, ""))
    line = R.pair_line(R.preflight())
    assert R.RUNTIME_PAIR_MISMATCH in line
    assert P.IMAGE_DIGEST in line and _NOT_THE_PIN in line
    assert "disagreed=container_matches_pin" in line
    assert "/" not in line and "registry" not in line, line


def test_the_evidence_is_the_refusal_VERBATIM(monkeypatch):
    """A preflight that paraphrases hands the reader a summary of a mismatch
    instead of the mismatch, and the reader re-runs it to see for themselves."""
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_NOT_THE_PIN, ""))
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
        monkeypatch, tmp_path, lambda c: (_NOT_THE_PIN, ""))
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
    _, _, run_p = _solve(monkeypatch, tmp_path, lambda c: (_NOT_THE_PIN, ""))
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
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_NOT_THE_PIN, ""))
    assert BD._cmd_solve_locked("rtllm", str(tmp_path / "d"),
                                str(tmp_path / "run")) == 2
    assert seen == [], "the clean room was built for a run that could not start"


# ── the same-version/different-digest case, on the two REAL images ───────────

def _have(digest: str) -> bool:
    ref = f"{P.image_repo()}@{digest}"
    return bool(P.local_repo_digests(ref)[0])


def _other_build():
    """The real not-the-pin digest these arms drive, or a SKIP saying why not.

    Three different reasons to be unable to measure, and none of them is a pass:
    the derivation could not run, the pinned image is not on this host, or the
    other build is not. Each says which.
    """
    other = _previous_pin()
    if other is None:
        pytest.skip(
            f"the previous pin cannot be READ here — {_PIN_AUTHORITY} has no "
            "earlier declared IMAGE_DIGEST reachable from this checkout (not a "
            "git work tree, or a history that does not reach one). NOT "
            "MEASURED; nothing is assumed in its place")
    _refuse_a_collided_other(other, "_previous_pin()")
    if not _have(P.IMAGE_DIGEST):
        pytest.skip(f"the pinned build {P.IMAGE_DIGEST} is not on this host")
    if not _have(other):
        pytest.skip(f"the previous pin {other} is not on this host, so the "
                    "two-real-builds case cannot be constructed")
    return other


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


def test_REAL_the_pin_and_the_PREVIOUS_pin_are_not_interchangeable(real_container,
                                                                  monkeypatch):
    """RENAMED from `test_REAL_two_builds_labelled_0348_are_not_interchangeable`
    (#2163). The old name was a fact about two particular builds and stopped
    being true the moment the pin moved; this one names the RELATION the arm
    actually drives, and stays true across every future move.

    A version label is what a human reads and it is not the runtime — that was
    #2120's whole finding, and it does not need both builds to share a label to
    be measured. The digests are what the runtime IS."""
    other = _other_build()
    name = real_container(other)
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, name)
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MISMATCH
    assert rec["container"] == name
    assert rec["found_digest"] == other
    assert rec["disagreed"] == "container_matches_pin"
    assert R.evidence_text(rec).startswith(P.CONTAINER_IMAGE_MISMATCH)


def test_REAL_the_pinned_build_is_a_MATCH(real_container, monkeypatch):
    """The control on real bytes. Without it the test above is satisfied by code
    that calls every container a mismatch."""
    _other_build()
    name = real_container(P.IMAGE_DIGEST)
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, name)
    rec = R.preflight()
    assert rec["verdict"] == R.RUNTIME_PAIR_MATCH, R.evidence_text(rec)
    assert rec["found_digest"] == P.IMAGE_DIGEST


def test_REAL_the_issue_acceptance_assertion_passes_on_a_matching_pair(
        real_container, monkeypatch):
    """The read-only assertion #2120 asks for, run VERBATIM against the
    container the runner actually chooses."""
    _other_build()
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
    other = _other_build()
    monkeypatch.setenv(P.CONTAINER_NAME_ENV, real_container(other))
    run = subprocess.run(
        [sys.executable, "-c",
         'import _eda_pin as p; ref, why = p.pinned_image_present(); '
         'assert ref, why; name = p.default_container_name(); '
         'why = p.container_matches_pin(name); assert not why, why; '
         'print("RUNTIME_PAIR_MATCH", name, ref)'],
        cwd=str(_PROGRAMS), capture_output=True, text=True, timeout=180)
    assert run.returncode != 0
    assert P.CONTAINER_IMAGE_MISMATCH in run.stderr
    assert other in run.stderr and P.IMAGE_DIGEST in run.stderr


# ── resume: the coordinator whose fan-outs are CONDITIONAL ──────────────────

def _mismatched(monkeypatch):
    monkeypatch.setattr(P, "pinned_image_present",
                        lambda env=None: ("repo@" + P.IMAGE_DIGEST, ""))
    monkeypatch.setattr(P, "container_image_digest", lambda c: (_NOT_THE_PIN, ""))


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
    """MEMBERSHIP, not a count — and DOMINANCE, not adjacency.

    A fourth fan-out added later must be guarded too, and the way to notice is
    to compare the SET of `_ordered_parallel_map` call sites in the resume
    coordinator against the SET that a guard actually protects — never to
    assert "there are three".

    WHAT "PROTECTED" MEANS, and why it is not a line distance. This assertion
    used to read `any(0 < line - g < 12 for g in guards)`: a guard counted only
    if it sat within eleven PHYSICAL LINES above the fan-out. MEASURED on live
    main ff3e383fb: the three fan-outs are at 5185 / 5336 / 5587 and their
    three guards at 5182 / 5322 / 5583 — a correct 1:1 pairing, each guard
    dominating its own fan-out — yet the middle pair is 14 lines apart because
    a nine-line nested `def _run_completed_backup` sits between them, and the
    test went red on a tree whose guards are all present and all effective.
    Eleven was a fact about the file's layout, not about the code: any comment,
    helper or intervening statement moves it, and widening the number just
    picks the next literal that happens to hold today.

    So the property is asked STRUCTURALLY instead. For each fan-out, walk the
    chain of statement blocks that reaches it and require an EARLIER statement
    on that chain to bind a `_runtime_pair_before_fan_out` verdict to a name
    AND a later-but-still-earlier statement to act on it with
    `if <name> is not None: return <name>`. That is what the guard is for, it
    holds at any distance, and it is STRICTER than the line test was: a guard
    whose verdict is computed and then dropped on the floor used to satisfy the
    old assertion purely by sitting close enough.
    """
    import ast                                             # noqa: PLC0415
    src = (_PROGRAMS / "benchmark_dispatch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    resume = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_cmd_resume_locked")

    def _called(node, fn):
        return {n.lineno for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == fn}

    fan_outs = _called(resume, "_ordered_parallel_map")
    guards = _called(resume, "_runtime_pair_before_fan_out")
    assert fan_outs, "the resume coordinator fans out somewhere"
    assert len(guards) == len(fan_outs), (
        f"fan-outs at {sorted(fan_outs)} but guards at {sorted(guards)}")

    def _spans(stmt, line):
        return stmt.lineno <= line <= (getattr(stmt, "end_lineno", None)
                                       or stmt.lineno)

    def _chain(block, line, acc):
        """[(block, index)] from outermost to innermost reaching `line`."""
        for i, st in enumerate(block):
            if not _spans(st, line):
                continue
            acc.append((block, i))
            for field in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(st, field, None)
                if not isinstance(sub, list):
                    continue
                for entry in sub:
                    inner = (entry.body if isinstance(entry, ast.ExceptHandler)
                             else None)
                    if inner is not None and any(_spans(s, line) for s in inner):
                        return _chain(inner, line, acc)
                if sub and isinstance(sub[0], ast.stmt) and any(
                        _spans(s, line) for s in sub):
                    return _chain(sub, line, acc)
            return acc
        return acc

    def _is_refusal_return(stmt, name):
        test = getattr(stmt, "test", None)
        return (isinstance(stmt, ast.If)
                and isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.IsNot)
                and isinstance(test.left, ast.Name)
                and test.left.id == name
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None
                and any(isinstance(b, ast.Return) for b in stmt.body))

    def _acted_on_guard_before(block, stop):
        """True when the LAST guard bound before `stop` is also acted on.

        KEYED ON THE BINDING, NOT ON THE NAME. All three call sites bind their
        verdict to the same identifier `gate_rc`, so asking merely "is some
        `gate_rc` early-returned somewhere above" lets the FIRST fan-out's
        guard vouch for a later one that has none — MEASURED: with the
        ai-backup `if gate_rc is not None: return gate_rc` deleted, that
        spelling still passed. Re-binding therefore RESETS the evidence: the
        verdict in hand at the fan-out is the one that must have been obeyed.
        """
        held, obeyed = None, False
        for st in block[:stop]:
            if isinstance(st, ast.Assign) and _called(
                    st, "_runtime_pair_before_fan_out"):
                names = [t.id for t in st.targets if isinstance(t, ast.Name)]
                if names:
                    held, obeyed = names[0], False
            elif held is not None and _is_refusal_return(st, held):
                obeyed = True
        return held is not None and obeyed

    for line in sorted(fan_outs):
        chain = _chain(resume.body, line, [])
        assert chain, f"the fan-out at line {line} is not inside the coordinator"
        assert any(_acted_on_guard_before(block, i) for block, i in chain), (
            f"the fan-out at line {line} is not dominated by a pair guard "
            f"whose verdict is returned on refusal")
