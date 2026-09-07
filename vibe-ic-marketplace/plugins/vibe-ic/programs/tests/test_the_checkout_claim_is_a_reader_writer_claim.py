#!/usr/bin/env python3
"""The checkout claim admits READERS together and WRITERS alone (ruling R5).

WHAT WAS MEASURED, AND WHY IT HAD TO CHANGE. `_CheckoutClaim` was an
unconditional single-holder `flock`, polled every 0.25 s, and the shipped wiring
of `gates are host-independent` is `--jobs 8`. So seven of eight workers were
queued on it AT ALL TIMES — by construction, not by contention. It costs
attribution and never the drive, so no verdict was ever wrong because of it; what
it cost was the ability to SUPERVISE the workers, because a process correctly
waiting on a lock is indistinguishable from a wedged one to output, to CPU and to
block I/O alike. Three healthy workers were reaped on exactly that evidence
(czstarve, 8hd-3, f3e5bd985).

THE REPAIR HAS TWO HALVES AND THE SECOND IS THE LOAD-BEARING ONE.

  * A gate that DECLARES a write in its own command takes the exclusive claim.
    Derived from the declaration, never from a list of gate names — and MEASURED
    on `repo_hygiene_gates.sh` at v1.18.40, 0 of 153 declared gates carry a write
    flag, so on today's script that half selects nothing at all.
  * Therefore the half that keeps `GATE_CORRUPTED_CHECKOUT` reachable is the
    ESCALATION: a shared window that ends with the tree changed takes the
    exclusive claim, puts the tree back, re-drives THAT ONE GATE ALONE, and lets
    the second bracket decide. Reproduced -> named. Not reproduced -> not named,
    and the overlap is reported instead.

Every test below is one of those two claims, and the negative control for the
escalation is the last one: with the escalation switched off, the SAME planted
writer stops being named. A check that cannot fail is not a check.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_PROGRAMS = _TESTS.parent
sys.path.insert(0, str(_PROGRAMS))
import gate_host_independence_check as G  # noqa: E402

_T = 30


def _repo_with(tmp_path: Path, script_body: str, name: str = "r") -> Path:
    r = tmp_path / name
    (r / "tools" / "ci").mkdir(parents=True)
    (r / "tools" / "ci" / "repo_hygiene_gates.sh").write_text(script_body)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(r), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], check=True)
    return r


# ── the derivation: what the gate DECLARES, never a list of gates ──────────
@pytest.mark.parametrize("cmd", [
    'python3 "$PG/gen_flow_matrix_census.py" "$ROOT" --fix',
    "python3 x.py --write-baseline",
    "python3 x.py --out report.json",
    "python3 x.py --update",
])
def test_a_declared_write_takes_the_exclusive_claim(cmd):
    assert G.declares_a_checkout_write(cmd) is True, cmd


@pytest.mark.parametrize("cmd", [
    'python3 "$PG/source_chip_agnostic_check.py" "$PLUGIN"',
    'python3 programs/x.py . --marker "RULE 0"',
    "python3 x.py --check --require-remote",
    # THE ONE A PREFIX TEST GETS WRONG, and it is not hypothetical: the first
    # version of this predicate matched `token.startswith("--fix")` and reported
    # the shipped script's `gate fixtures exist` as a WRITER, because its
    # read-only `--fixtures` path begins with those four characters. A claim
    # mode chosen from an invented writer is a serialisation nobody asked for.
    'python3 x.py --script s.sh --fixtures /p --debt d.json',
    "python3 x.py --outline /p",
])
def test_a_command_that_declares_no_write_is_a_reader(cmd):
    assert G.declares_a_checkout_write(cmd) is False, cmd


def test_the_write_flags_are_matched_as_FLAG_NAMES_not_as_string_prefixes():
    """The repair, stated as the property rather than as the implementation.

    A flag counts when its NAME is one of the write flags, or extends one with a
    `-` — which is how this repo spells a variant (`--write-baseline`). It does
    not count when it merely BEGINS with one (`--fixtures`, `--outline`).
    """
    assert G.declares_a_checkout_write("p --write-baseline") is True
    assert G.declares_a_checkout_write("p --out=/tmp/x") is True
    assert G.declares_a_checkout_write("p --output /tmp/x") is True
    assert G.declares_a_checkout_write("p --fixtures /tmp/x") is False
    assert G.declares_a_checkout_write("p --outline /tmp/x") is False


def test_the_shipped_script_declares_no_writer_and_that_is_MEASURED():
    """The number the escalation's necessity rests on, read off the script by the
    program's own parser rather than counted by hand.

    It is asserted as a PROPERTY, not pinned to 157: the declared count moves
    whenever a gate is added, and a test that pinned it would go red for a reason
    that has nothing to do with this claim. What must hold is that the parser
    sees a real population and that none of it declares a write — because that is
    what makes the escalation, and not the predicate, the thing that keeps
    `GATE_CORRUPTED_CHECKOUT` reachable.
    """
    script = (_PROGRAMS.parents[3] / "tools" / "ci" / "repo_hygiene_gates.sh")
    if not script.is_file():
        pytest.skip(f"NOT_MEASURED: {script} is not in this tree")
    gates = G.corpus_gates(script)
    assert len(gates) > 100, len(gates)
    writers = [g.label for g in gates if G.declares_a_checkout_write(g.cmd)]
    assert writers == [], (
        "a declared writer appeared in the shipped script. That is not a defect "
        "by itself — it takes the exclusive claim and is attributed inside it — "
        "but the escalation's necessity was argued from this set being EMPTY, so "
        "the argument needs re-reading: " + ", ".join(writers))


def test_an_unparseable_declaration_is_answered_EXCLUSIVE():
    """The fail-safe direction, stated as a test rather than as a comment.

    A wrong True costs one gate a claim it did not need; a wrong False would
    cost a finding. The two are not symmetric, so the unknown answers True.
    """
    assert G.declares_a_checkout_write('python3 x.py --marker "unbalanced') is True


# ── the lock itself ────────────────────────────────────────────────────────
def test_two_readers_hold_the_same_checkout_at_the_same_time(tmp_path):
    """THE WHOLE POINT, asserted on the primitive: both claims are held AT ONCE.

    Under the single-holder claim the second of these two waits for the first,
    which is what made seven of eight workers queue.
    """
    repo = tmp_path / "r"
    repo.mkdir()
    locks = tmp_path / "locks"
    with G._CheckoutClaim(repo, want=True, lock_root=locks,
                          exclusive=False) as first:
        assert first.held, first.why
        with G._CheckoutClaim(repo, want=True, lock_root=locks, wait_s=5,
                              exclusive=False) as second:
            assert second.held, second.why
            assert second.waited_s < 1.0, (
                f"a reader queued {second.waited_s:.2f}s behind another reader")


def test_a_writer_waits_for_a_reader_and_says_so(tmp_path):
    """The other direction: shared and exclusive still exclude each other, so a
    gate that writes is still alone in its window."""
    repo = tmp_path / "r"
    repo.mkdir()
    locks = tmp_path / "locks"
    with G._CheckoutClaim(repo, want=True, lock_root=locks,
                          exclusive=False) as reader:
        assert reader.held
        with G._CheckoutClaim(repo, want=True, lock_root=locks, wait_s=0.5,
                              exclusive=True) as writer:
            assert not writer.held
            assert "conflicting claim" in writer.why, writer.why


def test_a_reader_waits_for_a_writer(tmp_path):
    """And symmetrically, so a declared writer's bracket is never shared."""
    repo = tmp_path / "r"
    repo.mkdir()
    locks = tmp_path / "locks"
    with G._CheckoutClaim(repo, want=True, lock_root=locks,
                          exclusive=True) as writer:
        assert writer.held
        with G._CheckoutClaim(repo, want=True, lock_root=locks, wait_s=0.5,
                              exclusive=False) as reader:
            assert not reader.held, "a reader entered a writer's window"


def test_eight_readers_are_granted_concurrently_and_queue_for_nothing(tmp_path):
    """THE MEASUREMENT THE RULING ASKS FOR, in miniature: `--jobs 8` worth of
    readers, and the total queued time across all eight.

    The single-holder claim cannot produce this row at all — with eight
    single-holder claims, seven are queued for as long as the first is held.
    """
    repo = tmp_path / "r"
    repo.mkdir()
    locks = tmp_path / "locks"
    G.reset_claim_census()
    claims = [G._CheckoutClaim(repo, want=True, lock_root=locks, wait_s=5,
                               exclusive=False) for _ in range(8)]
    entered = [c.__enter__() for c in claims]
    try:
        assert all(c.held for c in entered), [c.why for c in entered]
    finally:
        for c in claims:
            c.__exit__(None, None, None)
    census = G.claim_census()
    assert census["claims"] == 8 and census["shared"] == 8
    assert census["exclusive"] == 0 and census["gave_up"] == 0
    assert census["waited_s"] < 1.0, census


# ── the escalation, in both directions, on the real audit ──────────────────
_WRITER_GATE = 'run "writer" "$ROOT" python3 writer.py\n'


def _repo_with_a_writing_gate(tmp_path: Path) -> Path:
    r = _repo_with(tmp_path, _WRITER_GATE)
    (r / "writer.py").write_text(
        "import pathlib\n"
        "p = pathlib.Path('tools/ci/repo_hygiene_gates.sh')\n"
        "p.write_text(p.read_text() + '# written by the gate\\n')\n"
        "print('PASS 1 thing examined')\n")
    subprocess.run(["git", "-C", str(r), "add", "writer.py"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "writer"], check=True)
    return r


def test_a_gate_that_writes_without_declaring_it_is_still_named(tmp_path):
    """The true positive, through the SHARED path and the escalation.

    `writer` declares no write, so it takes the shared claim — and it writes
    anyway. The escalation re-drives it alone under an exclusive claim, the
    write reproduces, and the gate is named exactly as it was when every gate
    held the exclusive claim.
    """
    r = _repo_with_a_writing_gate(tmp_path)
    G.reset_claim_census()
    res = G.audit(r, timeout=_T)
    kinds = {f["kind"] for f in res.findings}
    assert "GATE_CORRUPTED_CHECKOUT" in kinds, res.findings
    named = [f["gate"] for f in res.findings
             if f["kind"] == "GATE_CORRUPTED_CHECKOUT"]
    assert named == ["writer"], named
    assert G.claim_census()["escalations"] >= 1, G.claim_census()
    # AND THE TREE IS PUT BACK. A finding that leaves the write in place would
    # hand the next gate a different subject.
    porcelain = subprocess.run(
        ["git", "-C", str(r), "status", "--porcelain"],
        capture_output=True, text=True).stdout.strip()
    assert porcelain == "", porcelain


def test_without_the_escalation_the_same_writer_is_NOT_named(tmp_path):
    """THE NEGATIVE CONTROL, and the reason the escalation is not decoration.

    One thing differs from the test above: the escalation is switched off. The
    planted writer is identical, the claim is identical, the drive is identical
    — and the finding disappears, which is what proves the escalation is what
    earns it. If this test ever passes WITH the escalation on, the test above
    is passing for some other reason.
    """
    r = _repo_with_a_writing_gate(tmp_path)
    G.reset_claim_census()
    saved = G._ESCALATE_ON_SHARED_WRITE
    try:
        G._ESCALATE_ON_SHARED_WRITE = False
        res = G.audit(r, timeout=_T)
    finally:
        G._ESCALATE_ON_SHARED_WRITE = saved
    kinds = {f["kind"] for f in res.findings}
    assert "GATE_CORRUPTED_CHECKOUT" not in kinds, (
        "the write was attributed with the escalation switched off, so the "
        "test above is not measuring the escalation", res.findings)
    # AND THE SHARED WINDOW LEFT THE WRITE ALONE. This is the SAFETY half and it
    # is the reason the shared path may not call `_repair_checkout`: that
    # function runs `git checkout -- <path>`, which under `gatekeeper-land.sh`'s
    # concurrent lanes would discard another lane's in-flight work. Inside a
    # shared bracket "this drive wrote it" is not established, so nothing may be
    # undone on that evidence. With the escalation off there is no exclusive
    # window either, so the honest outcome is a tree still carrying the write —
    # observed, not repaired.
    porcelain = subprocess.run(["git", "-C", str(r), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
    assert porcelain, (
        "the shared window REPAIRED a write it could not attribute. That is the "
        "destructive direction: `git checkout --` on a path this process may not "
        "have written is another lane's work discarded, and no finding is worth "
        "it")


def test_the_shipped_default_escalates(tmp_path):
    """The control switch above exists for the control and for nothing else."""
    assert G._ESCALATE_ON_SHARED_WRITE is True


def test_a_gate_that_declares_its_write_never_needs_the_escalation(tmp_path):
    """The declared-writer path, end to end: exclusive claim, attributed inside
    it, and the escalation counter stays at zero."""
    r = _repo_with(tmp_path, 'run "writer" "$ROOT" python3 writer.py --fix\n')
    (r / "writer.py").write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path('tools/ci/repo_hygiene_gates.sh')\n"
        "p.write_text(p.read_text() + '# written by the gate\\n')\n"
        "print('PASS 1 thing examined')\n")
    subprocess.run(["git", "-C", str(r), "add", "writer.py"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "writer"], check=True)
    G.reset_claim_census()
    res = G.audit(r, timeout=_T)
    kinds = {f["kind"] for f in res.findings}
    assert "GATE_CORRUPTED_CHECKOUT" in kinds, res.findings
    census = G.claim_census()
    assert census["escalations"] == 0, census
    assert census["exclusive"] >= 1, census


def test_a_reader_that_writes_nothing_is_not_accused_and_costs_no_wait(tmp_path):
    """The accept direction. A gate that reads must not be named, and must not
    pay for a lock it does not need."""
    r = _repo_with(tmp_path, 'run "reader" "$ROOT" python3 -c "print(1)"\n')
    G.reset_claim_census()
    res = G.audit(r, timeout=_T)
    kinds = {f["kind"] for f in res.findings}
    assert "GATE_CORRUPTED_CHECKOUT" not in kinds, res.findings
    census = G.claim_census()
    assert census["escalations"] == 0 and census["shared"] >= 1, census
    assert census["waited_s"] < 1.0, census


def test_a_planted_writer_still_serialises_while_eight_workers_share_the_checkout(
        tmp_path, capsys):
    """THE PROOF THE RULING ASKS FOR, under the condition that makes it hard.

    `test_a_gate_that_writes_without_declaring_it_is_still_named` above drives the
    SERIAL `audit`, where the escalation has nobody to wait for. That is the easy
    half. The claim the ruling actually makes is about `--jobs 8`: readers hold the
    checkout CONCURRENTLY, and a writer among them must still end up alone in a
    window before it is named. So this drives the real `parallel_audit(jobs=8)`
    over sixteen readers and one writer and asserts the three things that have to
    be simultaneously true:

      * the WRITER is named, by name, with `GATE_CORRUPTED_CHECKOUT`;
      * no READER is named — the harm this mechanism exists to replace is naming
        whichever gate happened to be standing there;
      * the escalation actually ran, read off the census the parent PRINTS, which
        is the number a human sees rather than an internal this test could reach
        and a reader could not.

    The census is read from stdout deliberately: the claims are taken in worker
    PROCESSES, so the parent's own `claim_census()` is empty by construction, and
    a test that asserted on it would pass while measuring nothing.
    """
    body = ["#!/usr/bin/env bash\n"]
    for i in range(16):
        body.append(f'run "reader {i:02d}" "$ROOT" python3 -c '
                    f'"print(\'PASS 1 thing examined\')"\n')
    body.append('run "planted writer" "$ROOT" python3 writer.py\n')
    r = _repo_with(tmp_path, "".join(body))
    (r / "writer.py").write_text(
        "import pathlib\n"
        "p = pathlib.Path('tools/ci/repo_hygiene_gates.sh')\n"
        "p.write_text(p.read_text() + '# written by the gate\\n')\n"
        "print('PASS 1 thing examined')\n")
    subprocess.run(["git", "-C", str(r), "add", "writer.py"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "writer"], check=True)

    res = G.parallel_audit(r, jobs=8, checkout_attestations=None)
    printed = capsys.readouterr().out

    named = sorted(f["gate"] for f in res.findings
                   if f["kind"] == "GATE_CORRUPTED_CHECKOUT")
    assert named == ["planted writer"], (named, res.findings)
    assert not [f for f in res.findings if f["gate"].startswith("reader")], (
        "a READER was named for a write it did not make — this is the false "
        "accusation the claim exists to prevent", res.findings)

    line = [l for l in printed.splitlines() if "checkout claim:" in l]
    assert line, printed
    assert "0 escalated" not in line[0], (
        "the writer was named without the escalation ever running, so this test "
        "is not measuring what it says", line[0])
    assert "shared" in line[0], line[0]

    porcelain = subprocess.run(["git", "-C", str(r), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
    assert porcelain == "", (
        "the escalation named the writer and left its write in the tree", porcelain)
