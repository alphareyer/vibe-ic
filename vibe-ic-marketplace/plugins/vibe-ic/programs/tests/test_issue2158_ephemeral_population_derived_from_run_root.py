#!/usr/bin/env python3
"""vibe-ic#2158 (half 2, GATE) — the population is "a path that will not exist
after the run", derived from the run root, not a hard list of four directories.

`project_outputs_in_tree_check` blocked only on `/tmp`, `/var/tmp`, `/dev/shm`
and `/run`. Those are one common way to land in the condition, not the
condition.

MEASURED (lanes rbsub5 and rbsub6, 8HD-9, 2026-09-07, same plugin tip): both
runs recorded, in `reports/orchestrator/phase2_one_shot.json`, a scratch path
that no longer existed. rbsub6's TMPDIR was `/tmp/lane.rbsub6/…` and the gate
FAILED. rbsub5's was under `$HOME` and the gate PASSED. The verdict turned on
where the operator's TMPDIR pointed, not on whether evidence was lost.

The derived class is deliberately narrow — outside the project root, and NOT ON
DISK. `test_an_existing_path_outside_the_tree_is_not_a_finding` is the arm that
holds it narrow: without it the gate would start blocking on `/usr/bin/...` and
every PDK root, which is a different (and wrong) gate.

BOTH DIRECTIONS. `test_the_pre_fix_gate_passed_the_home_fixture` runs the gate
as it stood at the fix's merge-base against the same fixture and asserts rc 0.
Without that arm these tests would pass against code that never had the defect.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from project_outputs_in_tree_check import (  # noqa: E402
    _VOLATILE_PREFIXES,
)

PROG = Path(__file__).resolve().parent.parent / \
    "project_outputs_in_tree_check.py"
_MERGE_BASE = "91902638a2ba302914bed49a478dc3b35bf9a555"
_REL = "vibe-ic-marketplace/plugins/vibe-ic/programs/" \
       "project_outputs_in_tree_check.py"


def _run(project_dir: Path, prog: Path = PROG) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(prog), str(project_dir)],
                          capture_output=True, text=True)


def _account_home_root() -> Path:
    """A home-shaped anchor that is NEVER under a volatile prefix.

    The fixtures below exist to exercise the DERIVED class — "outside the run
    root and not on disk" — on a path the pre-fix hard list of four volatile
    directories could NOT name. That is the only property that makes them
    discriminating, and anchoring them at `Path.home()` does not guarantee it:
    on the landing path this repo runs `HOME=/tmp`, and there the very same
    fixture is ALSO `/tmp/...`, so it collapses into the hard-list class the
    file is supposed to be measuring the absence of. Measured on the live tip
    `97990612f`, file unchanged since the sweep: HOME=<account-home>, /headless and
    /nonexistent all give 18 passed, HOME=/tmp gives 3 failed / 15 passed, and
    the three are exactly the assertions that name the derived class — the
    negative control among them announcing, correctly, that the fixture had
    stopped discriminating.

    So the anchor is DERIVED FROM THE PROPERTY rather than inherited from the
    operator: the account home when that already satisfies it, and a synthetic
    home root when it does not. Both are absolute, outside the run root, and
    never created. `_VOLATILE_PREFIXES` is imported from the gate rather than
    re-typed, so a prefix added there cannot leave this fixture behind."""
    home = Path.home()
    if not str(home).rstrip("/").startswith(
            tuple(p.rstrip("/") for p in _VOLATILE_PREFIXES)):
        return home
    return Path("/accounts/_vibeic2158_absent_account")


def _home_ephemeral(project: Path) -> str:
    """A path shaped exactly like the measured rbsub5 case: a STAGED COPY of
    the run root, under the account home, and gone.

    `<home>/_lane_gone_2158/<stage>/<project-name>` — the run root's own
    directory name is the last component, which is what makes it a relocated
    copy of THIS project rather than an unrelated absolute path. Never created:
    its absence is the point. The home root comes from `_account_home_root`,
    which guarantees the one property that makes this fixture discriminating."""
    return str(_account_home_root() / "_lane_gone_2158" /
               f"vibeic-rtl-step-{project.name}" / project.name)


# ── the shapes the hard list could not name ─────────────────────────────────

def test_a_gone_path_under_home_is_blocking(tmp_path):
    gone = _home_ephemeral(tmp_path)
    assert not Path(gone).exists()
    (tmp_path / "reports" / "orchestrator").mkdir(parents=True)
    (tmp_path / "reports" / "orchestrator" / "phase2_one_shot.json").write_text(
        json.dumps({"steps": [{"name": "rtl_gen", "status": "WAIVED",
                               "detail": f"run: check --project {gone} now"}]}))
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.splitlines()[0].startswith("[FAIL]"), r.stdout
    assert gone in r.stdout, r.stdout
    assert "outside-root)" in r.stdout, r.stdout
    assert "outside " + str(tmp_path) in r.stdout, r.stdout


@pytest.mark.parametrize("root", ["/scratch", "/mnt/fastscratch", "/data/tmpdir",
                                  "/var/lib/vibeic-scratch"])
def test_a_gone_path_under_any_other_scratch_root_is_blocking(tmp_path, root):
    gone = f"{root}/lane_gone_2158/stage/{tmp_path.name}/phase2/rtl/top.sv"
    assert not Path(gone).exists()
    (tmp_path / "RESULT.md").write_text(f"artefact staged at {gone}\n")
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert gone in r.stdout, r.stdout


def test_the_pre_fix_gate_passed_the_home_fixture(tmp_path):
    """NEGATIVE CONTROL — the same fixture, the gate as it was: rc 0."""
    repo = Path(__file__).resolve()
    while not (repo / ".git").exists():
        if repo.parent == repo:
            pytest.skip("not inside a git checkout")
        repo = repo.parent
    old = subprocess.run(
        ["git", "-C", str(repo), "show", f"{_MERGE_BASE}:{_REL}"],
        capture_output=True, text=True)
    if old.returncode != 0:
        pytest.skip(f"merge-base blob unavailable: {old.stderr.strip()}")
    prog = tmp_path / "_prefix_gate.py"
    prog.write_text(old.stdout)

    subject = tmp_path / "subject"
    (subject / "reports").mkdir(parents=True)
    gone = _home_ephemeral(subject)
    (subject / "RESULT.md").write_text(f"artefact staged at {gone}\n")

    before = _run(subject, prog)
    after = _run(subject, PROG)
    assert before.returncode == 0, (
        "the pre-fix gate must PASS this fixture, or every assertion above is "
        "vacuous:\n" + before.stdout + before.stderr)
    assert after.returncode == 1, after.stdout + after.stderr


# ── the shapes that must STAY out of the population ─────────────────────────

def test_an_existing_path_outside_the_tree_is_not_a_finding(tmp_path):
    """THE NARROWNESS ARM. System/toolchain references are on disk and stay
    legitimate; widening to them would be a different gate."""
    (tmp_path / "RESULT.md").write_text(
        f"interpreter {sys.executable}\n"
        f"deck at {Path(sys.executable).parent}\n"
        f"root {os.sep}usr{os.sep}bin{os.sep}env\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS]" in r.stdout


def test_an_in_tree_self_reference_under_a_non_volatile_root_is_not_a_finding(
        tmp_path):
    inner = tmp_path / "phase2" / "stage1" / "rtl"
    inner.mkdir(parents=True)
    (inner / "top.sv").write_text("// rtl\n")
    (tmp_path / "RESULT.md").write_text(f"RTL at {inner / 'top.sv'}\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # An in-tree reference that does NOT exist is still in-tree, not derived.
    (tmp_path / "RESULT.md").write_text(
        f"RTL at {tmp_path / 'phase2' / 'stage9' / 'never.sv'}\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_short_slash_prefixed_fragment_is_not_a_path(tmp_path):
    (tmp_path / "RESULT.md").write_text(
        "see /tmp and /usr and /nonexistentroot for details\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_log_sourced_derived_reference_is_disclosed_not_blocking(tmp_path):
    gone = _home_ephemeral(tmp_path)  # tmp_path IS the run root here
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "synth.log").write_text(f"tool scratch: {gone}\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "log-sourced dangling reference" in r.stdout, r.stdout
    assert gone in r.stdout, r.stdout


def test_a_waiver_covers_a_derived_reference(tmp_path):
    gone = _home_ephemeral(tmp_path)
    (tmp_path / "RESULT.md").write_text(f"artefact staged at {gone}\n")
    (tmp_path / "waivers.json").write_text(json.dumps({
        "project_artifacts_external_storage_intentional":
            "This reference is an intentionally ephemeral cache location that "
            "the run does not preserve and does not need to preserve.",
    }))
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS_WITH_WAIVER]" in r.stdout


# ── the old population, unchanged ───────────────────────────────────────────

def test_a_live_volatile_artifact_is_still_classified_live(tmp_path):
    sentinel = Path("/tmp") / f"vibe_2158_live_{tmp_path.name}.gds"
    sentinel.write_text("# fake GDS\n")
    try:
        (tmp_path / "RESULT.md").write_text(f"GDS at {sentinel}\n")
        r = _run(tmp_path)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "live external-storage" in r.stdout, r.stdout
        # It must NOT be double-counted into the derived class.
        assert "1 blocking external-storage reference(s)" in r.stdout, r.stdout
        assert "(1 live, 0 dangling, 0 outside-root)" \
            in r.stdout, r.stdout
    finally:
        sentinel.unlink(missing_ok=True)


def test_a_dangling_volatile_reference_keeps_its_own_class(tmp_path):
    gone = f"/tmp/vibe_2158_gone_{tmp_path.name}/out.gds"
    assert not Path(gone).exists()
    (tmp_path / "RESULT.md").write_text(f"GDS at {gone}\n")
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "(0 live, 1 dangling, 0 outside-root)" in r.stdout, \
        r.stdout


def test_an_empty_project_still_refuses(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "[SKIP]" in r.stdout


# ── the derivation itself: the run root's own name is what makes it ours ────

def test_an_unrelated_gone_path_outside_the_tree_is_not_a_finding(tmp_path):
    """THE SECOND NARROWNESS ARM, and the one the census forced.

    "Outside the tree and not on disk" was implemented first and censused over
    6847 published run roots on 8HD-8: 1659 moved rc 0 -> rc 1, and the
    population was spec prose (`/K/CS/CF/HD/SCR/JESDV/SUBCLASSV`, `/ARP/PMBus`)
    plus container-internal toolchain roots (`/foss/pdks/...`) that are absent
    only because the gate ran on the HOST. Absence alone is a fact about where
    the gate ran. The population is derived from the RUN ROOT instead: a path
    that carries this project's own directory name is a relocated copy of it.
    """
    (tmp_path / "RESULT.md").write_text(
        "prose: the /K/CS/CF/HD/SCR/JESDV field order\n"
        "pdk at /foss/pdks/gf180mcuD/libs.ref/does_not_exist_here.lib\n"
        "and /mnt/somewhere/unrelated/gone/output.gds\n")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS]" in r.stdout


def test_the_same_shape_becomes_a_finding_when_it_names_the_run_root(tmp_path):
    """ONE VARIABLE. The identical path, with the run root's name spliced in."""
    neutral = f"/mnt/somewhere/unrelated/gone_{tmp_path.name[:4]}/output.gds"
    ours = f"/mnt/somewhere/unrelated/{tmp_path.name}/output.gds"
    assert not Path(neutral).exists() and not Path(ours).exists()

    (tmp_path / "RESULT.md").write_text(f"artefact at {neutral}\n")
    assert _run(tmp_path).returncode == 0

    (tmp_path / "RESULT.md").write_text(f"artefact at {ours}\n")
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert ours in r.stdout, r.stdout


def test_a_relocated_copy_that_still_exists_is_not_reported_here(tmp_path):
    """Non-existence stays a NECESSARY condition: a staged copy still on disk
    is a LIVE external artefact, which is the pre-existing `/tmp` class's job,
    not this one."""
    live = tmp_path.parent / f"stagecopy_{tmp_path.name}" / tmp_path.name
    live.mkdir(parents=True)
    (live / "out.gds").write_text("# gds\n")
    (tmp_path / "RESULT.md").write_text(f"artefact at {live / 'out.gds'}\n")
    r = _run(tmp_path)
    # Under pytest's default basetemp this sits in /tmp, so the volatile-prefix
    # class claims it as LIVE; under a non-volatile TMPDIR nothing claims it.
    # Either way the DERIVED class must not, because the file is still there.
    assert "outside-root)" not in r.stdout or \
        "0 outside-root)" in r.stdout, r.stdout


def test_the_deciding_line_still_fits_the_published_cap_with_derived_hits(
        tmp_path):
    """#2084's 200-character budget, with the #2158 term NON-ZERO.

    Caught by running #2084's own suite against this change: the first draft
    spelled the third term "N dangling outside the run root", which pushed the
    1024-reference deciding line to 209 characters — `_p0_first_line` published
    it truncated mid-word, which is exactly the defect #2084 closed. The line
    was shortened; this arm is what stops it growing back.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import flow_compliance_check as F  # noqa: E402

    lines = []
    for i in range(1024):
        lines.append(f"artefact at /mnt/gone_2158_{i}/{tmp_path.name}/o{i}.gds")
    (tmp_path / "RESULT.md").write_text("\n".join(lines) + "\n")
    r = _run(tmp_path)
    assert r.returncode == 1, r.stdout[:2000] + r.stderr
    head = r.stdout.splitlines()[0]
    assert head.startswith("[FAIL]"), head
    assert "1024 blocking external-storage reference(s)" in head, head
    assert "(0 live, 0 dangling, 1024 outside-root)" in head, head
    assert F._p0_first_line(r.stdout) == head, (
        f"published reason truncated at {len(head)} chars: {head}")


# ── the fixture must measure the CODE, not the operator's HOME ──────────────

def test_the_fixture_stays_outside_the_hard_list_when_home_is_volatile(
        monkeypatch, tmp_path):
    """THE REGRESSION THIS FILE'S OWN REDS WERE. Every assertion above that
    names the derived class is only discriminating while the fixture is a path
    the pre-fix hard list could NOT name. Anchored at `Path.home()` that held
    on a developer box and silently stopped holding on the landing path, where
    `HOME=/tmp`: the fixture became `/tmp/_lane_gone_2158/...`, the gate
    classified it as dangling-volatile instead of outside-root, and three ids
    went red against code that had not changed in 55 versions.

    Driven, not inherited: HOME is pinned to each volatile prefix in turn and
    the built fixture must still fall outside all of them."""
    for prefix in _VOLATILE_PREFIXES:
        monkeypatch.setenv("HOME", prefix.rstrip("/"))
        built = _home_ephemeral(tmp_path)
        assert Path(built).is_absolute(), built
        assert not Path(built).exists(), built
        landed_in = [p for p in _VOLATILE_PREFIXES if built.startswith(p)]
        assert landed_in == [], (
            f"HOME={prefix.rstrip('/')} put the fixture back inside the "
            f"pre-fix hard list {landed_in}: {built}. The fixture would then "
            f"be classified by the old rule, and every derived-class assertion "
            f"in this file would be measuring the operator's HOME.")


def test_the_home_anchor_is_used_when_it_is_already_non_volatile(monkeypatch,
                                                                 tmp_path):
    """The other direction: where the account home ALREADY satisfies the
    property, it is the anchor — the fix must not throw the real-world shape
    away and hard-code a synthetic root everywhere."""
    monkeypatch.setenv("HOME", "/accounts/someone")
    assert _account_home_root() == Path("/accounts/someone")
    assert _home_ephemeral(tmp_path).startswith("/accounts/someone/_lane_gone_2158")
