#!/usr/bin/env python3
"""`run_suite_in_eda_image.sh` from a linked worktree ran with NO write guard.

MEASURED 2026-09-10 on 8HD-8, v1.20.13, this script invoked from the linked
worktree `/mnt/ssd2/vibe-ic/scratch/cy538`::

    WRITE_GUARD_NOT_CHECKED: git rev-parse --show-toplevel exited 128:
    fatal: not a git repository: /home/reyerchu/vibe-ic/.git/worktrees/cy538
    35 passed

`-v "$REPO_ROOT:$REPO_ROOT"` carries the TREE. In a linked worktree
`$REPO_ROOT/.git` is a FILE holding `gitdir: <main>/.git/worktrees/<name>`, and
that address is on the host outside the mount, so git inside the container has
no repository at all. `suite_write_guard` declined to measure — honestly, by
name, which is the part that works — and the run still printed `35 passed`.

WHY IT MATTERS BEYOND ONE RUN: the fleet runs this harness from a throwaway
worktree as a matter of course (it is the standing advice, because a suite that
runs in the main checkout reverts edits made under it). So the guard was off for
essentially every run, and every `N passed` from this harness was a number with
its write guard NOT CHECKED behind it.

THE TEST IS BEHAVIOURAL, NOT A GREP. It executes the script's OWN mount block —
read from the file between its sentinels, so deleting or renaming the block
fails here loudly rather than silently — against two real repositories built in
tmp_path: a linked worktree, where the common dir is outside REPO_ROOT and MUST
be mounted, and an ordinary checkout, where it is inside and must add NO mount.
Both directions, because a block that mounted unconditionally would also pass
the first half.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[5]
          / "tools" / "ci" / "run_suite_in_eda_image.sh")

# The shipped plugin tree carries `programs/` without `tools/`. Skipping there
# is honest — the subject is not in this checkout — and is NOT the same as
# passing: every developer and CI run has the full repo.
pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(),
    reason=f"{SCRIPT} is not in this checkout (plugin-only tree); the mount "
           "block is UNVERIFIED here, which is not the same as verified")
_OPEN = "# >>> GIT_COMMON_MOUNT BLOCK"
_CLOSE = "# <<< GIT_COMMON_MOUNT BLOCK"


def _mount_block() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    assert _OPEN in text and _CLOSE in text, (
        f"the sentinels this test executes are gone from {SCRIPT}; the mount "
        "block was renamed or deleted and this test would otherwise measure "
        "nothing")
    body = text.split(_OPEN, 1)[1].split(_CLOSE, 1)[0]
    return body.split("\n", 1)[1]          # drop the rest of the sentinel comment


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _resolve(repo_root: Path) -> str:
    """Run the script's own block with REPO_ROOT bound, print the mount args."""
    script = (f'set -u\nREPO_ROOT={repo_root!s}\n' + _mount_block()
              + '\nprintf "%s\\n" "${GIT_COMMON_MOUNT[@]:-}"\n')
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture()
def main_checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "f").write_text("x\n")
    _git(repo, "add", "f")
    _git(repo, "commit", "-qm", "c")
    return repo


def test_a_linked_worktree_mounts_the_common_git_dir(main_checkout, tmp_path):
    wt = tmp_path / "wt"
    _git(main_checkout, "worktree", "add", "-q", "--detach", str(wt))
    common = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--path-format=absolute",
         "--git-common-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert not common.startswith(str(wt)), (
        "PRECONDITION: a linked worktree's common dir must be outside it, "
        f"otherwise this case models nothing: {common}")

    got = _resolve(wt)
    assert got.splitlines() == ["-v", f"{common}:{common}"], (
        "the harness would start a container that cannot see this worktree's "
        f"repository, so every git-backed gate inside it declines: {got!r}")


def test_an_ordinary_checkout_adds_no_mount(main_checkout):
    """THE OTHER DIRECTION. A block that mounted unconditionally would satisfy
    the case above and quietly bind the repo root twice on every ordinary run."""
    assert _resolve(main_checkout) == "", (
        "an ordinary checkout already carries its own .git inside REPO_ROOT; "
        "mounting it again is a second, redundant bind")


def test_an_unreadable_root_is_not_treated_as_a_worktree(tmp_path):
    """`git rev-parse` fails outside a repository. The block must add no mount
    there rather than mounting the empty string, which docker reads as a
    malformed `-v :` and refuses — turning a missing repo into a harness crash
    instead of the container's own honest refusal."""
    outside = tmp_path / "not_a_repo"
    outside.mkdir()
    assert _resolve(outside) == ""
