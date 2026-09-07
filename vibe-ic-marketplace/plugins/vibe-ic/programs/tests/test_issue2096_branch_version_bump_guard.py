"""#2096 — nothing refused a version bump riding into a `next/*` commit.

Lane czstarve4 measured it on 2026-09-07: the plugin test suite WRITES the
version-bearing files, `git add -A` swept them into two of the lane's own
commits, and no hook, gate or checker objected. The lane spotted it by hand
before pushing. The next one will not.

REPRODUCED HERE, ON THE REAL HOOK, before anything was changed. A synthetic
repo in the shipped layout, a `next/x` branch whose only difference from `main`
is a 1.0.0 -> 1.0.1 bump across all six version-bearing files, and
`origin/main`'s `tools/git-hooks/pre-push` fed the stdin git gives it:

    pre-push (origin/main)     rc 0     "PASS: no NDA foundry ... token"
    pre-push (this branch)     rc 1     FAILED — branch carries no version bump

Both directions are damage, and the gate that was already there covers neither:
`version_bump_monotonic_check --version-by-gatekeeper` MUST pass a forward bump
(on the landing path a forward bump is the point), and it reads plugin.json plus
one marketplace.json — the three shipped READMEs state the version in prose and
were read by nothing.

WHAT EACH ARM IS FOR
--------------------
  * `test_the_hook_runs_the_guard`              — the wiring. Red on origin/main.
  * `test_hook_END_TO_END_refuses_a_swept_bump` — the behaviour, driven.
  * `test_MUTANT_hook_without_the_gate_lets_it_through` — the mutation arm. Strip
    ONLY the `run_gate` call from the SHIPPED hook and the same fixture pushes
    clean, so the arm above is measuring that line and not something else.
  * `test_the_guarded_sites_ARE_the_files_the_version_writer_writes` — the
    non-vacuity arm. The guard derives its file list from the same helpers
    `gatekeeper_assign_version --write` writes through; this drives the REAL
    writer and asserts the two sets are equal, so a seventh version-bearing site
    cannot be added to the writer and silently escape the guard.
  * the green arms — an unrelated edit to a version-bearing file, a branch cut
    from an older main, `main` and `land/*` — each of which a guard written as
    "refuse any touch of these files" would have refused. A hook that refuses
    ordinary work is a hook that gets `--no-verify`'d.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import branch_version_bump_guard as G          # noqa: E402
import gatekeeper_assign_version as GAV        # noqa: E402
import _progress_run as _pr                    # noqa: E402

_PROGRAMS = Path(__file__).resolve().parents[1]


def _find_hook() -> Path:
    """Walk up for the artefact rather than counting directories — a
    hard-coded `parents[N]` is a guess about how deep this file sits."""
    for parent in Path(__file__).resolve().parents:
        cand = parent / "tools" / "git-hooks" / "pre-push"
        if cand.is_file():
            return cand
    raise AssertionError("tools/git-hooks/pre-push not found above this test")


HOOK = _find_hook()
REPO = HOOK.parents[1]

#: The `run_gate` label the hook uses. One spelling, read by both the wiring
#: assertion and the mutation arm, so they cannot drift apart.
GATE_LABEL = "branch carries no version bump"


def _hook_code() -> str:
    """The hook with COMMENTS STRIPPED. The comments name the program and the
    label to explain the change, so a substring search over the whole file
    passes on a hook that only TALKS about the gate."""
    return "\n".join(l for l in HOOK.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))


# ── the fixture: the shipped layout, in miniature ───────────────────────────

def _write_tree(root: Path, version: str) -> None:
    """A repo in the SHIPPED shape: a nested plugin, TWO marketplace manifests
    that reference it (the repo-root one and the nested one), and the three
    prose sites. A one-manifest fixture cannot see a guard that reads only the
    nearest ancestor."""
    plug = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / "vibe-ic-marketplace" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plug / ".claude-plugin").mkdir(parents=True, exist_ok=True)

    (plug / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "vibe-ic", "version": version}) + "\n", encoding="utf-8")
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "m", "plugins": [
            {"name": "vibe-ic", "source": "./vibe-ic-marketplace/plugins/vibe-ic",
             "version": version}]}) + "\n", encoding="utf-8")
    (root / "vibe-ic-marketplace" / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "m", "plugins": [
            {"name": "vibe-ic", "source": "./plugins/vibe-ic",
             "version": version}]}) + "\n", encoding="utf-8")
    (root / "README.md").write_text(
        f"[![Plugin v{version}](x)](y)\n\nsome prose\n", encoding="utf-8")
    (root / "vibe-ic-marketplace" / "README.md").write_text(
        f"| Plugin version | **{version}** |\n", encoding="utf-8")
    (plug / "README.md").write_text(
        f"the vibe-ic plugin (**v{version}**)\n", encoding="utf-8")


def _git(root: Path, *a: str) -> subprocess.CompletedProcess:
    return _pr.run(["git", "-C", str(root), *a], capture_output=True, text=True)


def _seed(root: Path, version: str = "1.0.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _write_tree(root, version)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    _git(root, "branch", "-M", "main")
    return root


def _branch(root: Path, name: str, *, from_ref: str = "main") -> None:
    assert _git(root, "checkout", "-q", "-B", name, from_ref).returncode == 0


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    assert _git(root, "commit", "-q", "-m", msg).returncode == 0


def _run(root: Path, *args: str):
    """The program's real main(), from `root`, as the hook drives it."""
    here = os.getcwd()
    try:
        os.chdir(root)
        return G.main(["--repo", str(root), *args])
    finally:
        os.chdir(here)


# ── the exemption, which is the whole scope of the gate ─────────────────────

@pytest.mark.parametrize("ref", [
    "refs/heads/main",
    "refs/heads/land/abc1234",
    "refs/heads/land_abc1234",
])
def test_destinations_that_may_state_a_version_are_exempt(ref):
    """These carry the LANDER's own assignment. `land/<short-sha>` is the shape
    `tools/ci/gatekeeper_protect_main.sh` documents for the direct-push flow."""
    assert G.destination_is_exempt(ref) is True


@pytest.mark.parametrize("ref", [
    "refs/heads/next/cz2096",
    "refs/heads/feat/anything",
    "refs/heads/mainline",          # not `main`; a prefix match would exempt it
    "refs/heads/landing-notes",     # not `land/`; likewise
    "",
    None,
])
def test_every_other_destination_including_an_unknown_one_is_guarded(ref):
    """FAIL-SAFE, and it is the direction that matters: the safe reading of
    "I do not know where this push is going" is the one that still asks."""
    assert G.destination_is_exempt(ref) is False


# ── the defect, on the program ──────────────────────────────────────────────

def test_a_swept_forward_bump_is_refused(tmp_path, capsys):
    root = _seed(tmp_path / "fwd")
    _branch(root, "next/x")
    _write_tree(root, "1.0.1")
    _commit(root, "fix(x): a real change that swept a version bump in")

    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 1, f"a branch carrying a bump was not refused (rc={rc}):\n{out}"
    # THE MESSAGE NAMES THE FILES — that is half of what #2096 asks for.
    for rel in ("vibe-ic-marketplace/plugins/vibe-ic/.claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
                "vibe-ic-marketplace/.claude-plugin/marketplace.json",
                "README.md",
                "vibe-ic-marketplace/README.md",
                "vibe-ic-marketplace/plugins/vibe-ic/README.md"):
        assert rel in out, f"the finding never named {rel}:\n{out}"
    assert "1.0.1" in out and "1.0.0" in out, out


def test_the_lander_sentence_survives_the_hooks_twelve_line_excerpt(tmp_path,
                                                                    capsys):
    """The other half of what #2096 asks for, and it is a real constraint, not
    a wording preference: `_gate_excerpt` in the hook prints at most TWELVE
    finding-shaped lines and then ALWAYS the last line. A six-file bump emits
    more than twelve, so a sentence placed among the `x` lines is the one that
    gets cut. It has to be on the last line."""
    root = _seed(tmp_path / "sentence")
    _branch(root, "next/x")
    _write_tree(root, "1.0.1")
    _commit(root, "swept")
    _run(root, "--head", "HEAD", "--base", "main", "--dest-ref", "refs/heads/next/x")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert "LANDER ASSIGNS THE VERSION" in lines[-1].upper(), (
        "the sentence a reader acts on is not on the last line, so the hook's "
        "excerpt will drop it:\n" + "\n".join(lines))


def test_a_swept_BACKWARDS_version_is_refused_too(tmp_path, capsys):
    """The silent half. When the branch's delta is applied the shipped version
    ROLLS BACK, `/plugin update` sees a number it has already seen, and every
    installed copy stays on a stale cache."""
    root = _seed(tmp_path / "back", version="1.18.52")
    _branch(root, "next/x")
    _write_tree(root, "1.18.40")
    _commit(root, "rolls it back")
    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 1, f"a backwards version was not refused (rc={rc}):\n{out}"
    assert "1.18.40" in out


def test_a_PROSE_ONLY_bump_is_refused(tmp_path, capsys):
    """The half `version_bump_monotonic_check` structurally cannot see: it reads
    plugin.json and one marketplace.json and never opens a README."""
    root = _seed(tmp_path / "prose")
    _branch(root, "next/x")
    (root / "README.md").write_text("[![Plugin v1.0.1](x)](y)\n", encoding="utf-8")
    _commit(root, "prose only")
    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 1, f"a prose-only bump passed (rc={rc}):\n{out}"
    assert "README.md" in out and "1.0.1" in out


# ── the directions that must NOT move ───────────────────────────────────────

def test_editing_a_version_bearing_file_WITHOUT_its_claim_still_passes(tmp_path,
                                                                       capsys):
    """A guard spelled "refuse any touch of these files" would refuse ordinary
    README work, and a hook that refuses ordinary work gets bypassed."""
    root = _seed(tmp_path / "prosework")
    _branch(root, "next/x")
    with (root / "README.md").open("a", encoding="utf-8") as fh:
        fh.write("\na new paragraph that says nothing about the version\n")
    _commit(root, "docs: unrelated prose")
    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    assert rc == 0, ("an edit to a version-bearing file that left its version "
                     "claim alone was refused:\n" + capsys.readouterr().out)


def test_a_branch_cut_from_an_OLDER_main_asserted_nothing(tmp_path, capsys):
    """THE FORK POINT, NOT THE TIP. A stale branch declares an older version by
    inheritance; measuring against main's tip reports that inheritance as a
    rollback on every stale branch, which is a false refusal on the commonest
    branch there is."""
    root = _seed(tmp_path / "stale", version="1.0.0")
    fork = _git(root, "rev-parse", "HEAD").stdout.strip()
    # main moves on, and legitimately carries the lander's bump
    _write_tree(root, "1.0.9")
    _commit(root, "the lander assigned 1.0.9 on main")
    _branch(root, "next/x", from_ref=fork)
    (root / "notes.txt").write_text("work\n", encoding="utf-8")
    _commit(root, "unrelated work on a branch cut earlier")

    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    assert rc == 0, ("a branch that inherited an older version was refused:\n"
                     + capsys.readouterr().out)

    # and the fork point really is what was used — otherwise this arm passes
    # for the wrong reason on a guard that compared nothing at all.
    sha, note = G.resolve_fork_point(root, "HEAD", "main")
    assert sha == fork, f"{note}: fork point {sha} != {fork}"
    assert sha != _git(root, "rev-parse", "main").stdout.strip()


@pytest.mark.parametrize("dest", ["refs/heads/main", "refs/heads/land/abc1234"])
def test_the_landing_destinations_may_carry_the_bump(tmp_path, capsys, dest):
    root = _seed(tmp_path / re.sub(r"\W", "_", dest))
    _branch(root, "next/x")
    _write_tree(root, "1.0.1")
    _commit(root, "the assigned version")
    rc = _run(root, "--head", "HEAD", "--base", "main", "--dest-ref", dest)
    assert rc == 0, (f"{dest} may state a version and was refused:\n"
                     + capsys.readouterr().out)


# ── "could not look" is not "looked and it was clean" ───────────────────────

def test_an_unresolvable_base_is_rc_2_not_a_pass(tmp_path, capsys):
    root = _seed(tmp_path / "nobase")
    _branch(root, "next/x")
    _write_tree(root, "1.0.1")
    _commit(root, "swept")
    rc = _run(root, "--head", "HEAD", "--base", "no/such/ref",
              "--dest-ref", "refs/heads/next/x")
    assert rc == 2, (f"a guard that could not find its base reported rc={rc}; "
                     "0 here is a PASS it did not earn")


def test_a_tree_with_no_plugin_version_is_a_determination_that_says_so(tmp_path,
                                                                       capsys):
    """The other side of the same rule: this is NOT "could not look". There is
    no version anywhere, so no branch cut from this tree can carry a bump — a
    real answer, and the PASS states how much it read."""
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    rc = _run(root, "--head", "HEAD", "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "0 sites" in out, ("a PASS must say how much it looked at, or it is "
                              "indistinguishable from a fail-open:\n" + out)


# ── NON-VACUITY: the guarded set IS the written set ─────────────────────────

def test_the_guarded_sites_ARE_the_files_the_version_writer_writes(tmp_path):
    """THE ANTI-DRIFT ARM.

    A hand-copied list of version-bearing files in the guard would be correct on
    the day it was typed. The day the writer learns a seventh site, the guard
    passes exactly the bump it exists to refuse and nothing says so.

    So this drives the REAL writer — `gatekeeper_assign_version._write_version`,
    the one function `--write` goes through — and compares the files it reports
    writing against the files the guard reports guarding. MEMBERSHIP, not a
    count: equal counts is the one summary a substitution cannot disturb."""
    root = _seed(tmp_path / "sameset")
    manifest_sites, prose_sites = G.version_sites(root)
    guarded = set(manifest_sites) | set(prose_sites)

    plugin_root = GAV._plugin_root(root)
    wrote = GAV._write_version(plugin_root, "9.9.9")
    written = {Path(w).resolve().relative_to(root.resolve()).as_posix()
               for w in wrote}

    assert guarded == written, (
        "the guard's scope and the version WRITER's scope have drifted apart.\n"
        f"  written but NOT guarded: {sorted(written - guarded)}\n"
        f"  guarded but NOT written: {sorted(guarded - written)}\n"
        "A file the writer writes and the guard does not read is a bump that "
        "rides into a branch unrefused.")
    assert guarded, "both sets are empty — this arm is vacuous on this fixture"


def test_the_guard_does_not_carry_its_own_list_of_version_bearing_files():
    """The property above, at the source, so a future edit that re-introduces a
    literal list is refused by name rather than by luck."""
    src = (_PROGRAMS / "branch_version_bump_guard.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    for literal in ('"README.md"', "'README.md'",
                    '"marketplace.json"', "'marketplace.json'"):
        assert literal not in code, (
            f"{literal} is spelled out in the guard. The site list must come "
            "from plugin_manifest_discovery / plugin_version_prose_sync_check, "
            "which is what keeps it equal to the writer's.")
    assert "_prose._PROSE_SITES" in code and "find_plugin_and_manifests" in code


# ── polarity: a sentence that prints a number and denies it ─────────────────
#
# `prose_polarity_consulted_check` (vibe-ic#712) blocks a NEW extractor that
# searches prose for a value and writes it as a declaration without asking
# whether the sentence DENIES it, and it caught `claims_at` on this branch —
# correctly. These two arms are the behaviour that answer implements, driven
# rather than asserted from the import.

# The `title` claim is `plugin\s*\(\*\*v(\d+\.\d+\.\d+)\*\*\)` — the `v` is
# part of the pattern. Written without it the regex matches NOTHING and this
# fixture passes for the wrong reason; the disclosure assertion below is what
# caught that while this test was being written.
_DENIED_README = ("The old numbering is gone. The vibe-ic plugin (**v1.0.1**) is "
                  "not shipped from this branch.\n")
_PLAIN_README = "the vibe-ic plugin (**v1.0.1**)\n"


def _prose_only_branch(root: Path, readme_body: str) -> None:
    """A branch whose ONLY difference from main is the nested README's text."""
    _branch(root, "next/x")
    (root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "README.md").write_text(
        readme_body, encoding="utf-8")
    _commit(root, "prose")


def test_a_version_a_sentence_DENIES_is_read_but_not_counted(tmp_path, capsys):
    """The refusal that must NOT happen. Without polarity the regex capture is
    taken as a declaration and the push is refused over a number the document
    itself takes back."""
    root = _seed(tmp_path / "denied")
    _prose_only_branch(root, _DENIED_README)
    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 0, ("a version printed inside a sentence that denies it was "
                     "counted as a declaration:\n" + out)
    # AND IT IS DISCLOSED. "the sentence denied it" and "the pattern never
    # matched" are the same silence to a reader; only one of them is true here.
    assert "denies it" in out and "1.0.1" in out, (
        "the guard dropped a value it READ and said nothing — the silent "
        "direction _prose_polarity's own docstring names:\n" + out)


def test_PAIRED_the_same_claim_without_the_denial_IS_refused(tmp_path, capsys):
    """The arm that makes the one above a check rather than a ban. Same file,
    same claim, same number — only the denial removed. If this ever goes green,
    the fixture has stopped discriminating and the test above proves nothing."""
    root = _seed(tmp_path / "plain")
    _prose_only_branch(root, _PLAIN_README)
    rc = _run(root, "--head", "HEAD", "--base", "main",
              "--dest-ref", "refs/heads/next/x")
    out = capsys.readouterr().out
    assert rc == 1, ("the identical claim WITHOUT a denial was not refused, so "
                     "the polarity arm above is not measuring polarity:\n" + out)
    assert "1.0.1" in out


def test_the_guard_reaches_the_ONE_negation_vocabulary_not_a_private_copy():
    """`_prose_polarity` exists because two fixes each built their own copy of
    "words that mean no" and the copies drifted. A third copy here would be the
    same defect, so this pins the import and refuses a local re-spelling."""
    src = (_PROGRAMS / "branch_version_bump_guard.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "_prose_polarity" in code and "is_denied" in code
    assert "sentence_scope" in code, (
        "polarity was asked without a sentence window, so a denial anywhere in "
        "the file would retract a claim it does not govern")
    for private in ("not\\b", "NEGATION", "DENIAL"):
        assert f're.compile' not in code or private not in code, (
            "a private negation vocabulary is being built here; take it from "
            "_prose_polarity, which is the reason that module exists")


# ── the wiring, and the mutation arm ────────────────────────────────────────

def test_the_hook_runs_the_guard():
    """RED ON origin/main: this program is not called there at all."""
    code = _hook_code()
    assert "branch_version_bump_guard.py" in code, (
        "tools/git-hooks/pre-push does not run the guard, so nothing refuses a "
        "swept version bump at the one place this repo enforces anything")
    assert GATE_LABEL in code
    seg = code[code.index("branch_version_bump_guard.py"):]
    assert "--dest-ref" in seg[:400], (
        "the guard is called without a destination, so it cannot exempt the "
        "landing branch — and a gate that refuses the lander gets removed")
    assert "PUSH_DEST_REF" in code and "PUSH_HEAD" in code


def _fixture_repo_with_a_swept_bump(tmp_path: Path) -> Path:
    """A synthetic repo whose `next/x` differs from `main` ONLY by a bump, with
    the REAL programs/ symlinked in so the hook resolves its gates the way it
    does in a checkout."""
    root = _seed(tmp_path / "e2e")
    _branch(root, "next/x")
    _write_tree(root, "1.0.1")
    _commit(root, "fix(x): a real change that swept a version bump in")
    link = root / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(_PROGRAMS)
    return root


def _drive_hook(hook: Path, root: Path, dest: str = "refs/heads/next/x"):
    """Invoke a pre-push hook the way git does: argv = <remote> <url>, and the
    ref line on stdin."""
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    base = _git(root, "rev-parse", "main").stdout.strip()
    stdin = f"{dest} {head} {dest} {base}\n"
    return _pr.run(["bash", str(hook), "origin", str(root)],
                   input=stdin, capture_output=True, text=True, cwd=str(root))


@pytest.mark.skipif(not HOOK.is_file(), reason="hook not present in this tree")
def test_hook_END_TO_END_refuses_a_swept_bump(tmp_path):
    root = _fixture_repo_with_a_swept_bump(tmp_path)
    r = _drive_hook(HOOK, root)
    assert r.returncode != 0, (
        "the real hook let a branch carrying a version bump through:\n"
        + (r.stderr or r.stdout)[-2000:])
    assert GATE_LABEL in r.stderr, (
        "the push was refused, but not by this gate — this arm would then be "
        "measuring something else:\n" + r.stderr[-2000:])
    assert "LANDER ASSIGNS THE VERSION" in r.stderr.upper(), (
        "the hook blocked the push without telling the reader who assigns the "
        "version, which is the one thing they act on:\n" + r.stderr[-2000:])


@pytest.mark.skipif(not HOOK.is_file(), reason="hook not present in this tree")
def test_MUTANT_hook_without_the_gate_lets_it_through(tmp_path):
    """THE MUTATION ARM. Take the SHIPPED hook, delete ONLY the `run_gate` call
    for this gate, and the identical fixture pushes clean. Without this arm the
    end-to-end above would pass just as well against a hook that refuses for
    some other reason."""
    root = _fixture_repo_with_a_swept_bump(tmp_path)
    src = HOOK.read_text(encoding="utf-8").splitlines()
    out, i, removed = [], 0, 0
    while i < len(src):
        if GATE_LABEL in src[i] and "run_gate" in src[i]:
            # the call and its continuation lines
            while i < len(src) and src[i].rstrip().endswith("\\"):
                i += 1
            i += 1
            removed += 1
            continue
        out.append(src[i])
        i += 1
    assert removed == 1, (
        f"expected exactly one run_gate call for {GATE_LABEL!r}, removed "
        f"{removed}. PREMISE FIRST: an arm that strips nothing proves nothing.")
    mutant = tmp_path / "pre-push-mutant"
    mutant.write_text("\n".join(out) + "\n", encoding="utf-8")

    m = _drive_hook(mutant, root)
    assert GATE_LABEL not in m.stderr, (
        "the mutant still named the gate, so the line removed is not the one "
        "doing the work:\n" + m.stderr[-2000:])
    assert m.returncode == 0, (
        "the mutant refused the push for some OTHER reason, so the end-to-end "
        "arm is not attributable to this gate:\n" + (m.stderr or m.stdout)[-2000:])
