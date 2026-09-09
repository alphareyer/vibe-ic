#!/usr/bin/env python3
"""vibe-ic#2224 — a BLOCKING gate nothing on the landing path ever asks.

#2104's `program_path_load_check` has exactly ONE caller,
`tools/ci/repo_hygiene_gates.sh:1128`; that script has exactly one caller,
`run_capture "full:repo-hygiene"` in `tools/gatekeeper-land.sh`; and that call
site is AFTER the `--cheap-only` exit which the direct-push landing path takes.
So the gate shipped BLOCKING and was asked at no landing, and two offenders
reached main under it.

The filer read that as one cause with two symptoms and was right about the
cause.  What the filer left NOT_MEASURED — "how many other BLOCKING gates are
in the same position" — is measured here: `repo_hygiene_gates.sh` declares 124
`run` (BLOCKING) gates, of which the registry asked 2.

So there are two halves to one repair, and both are pinned below:

  * the gate is ASKED — it joins `_RATCHETED_GATES`, so a landing that
    introduces a bare-sibling program is refused on the direct-push path;
  * what is still NOT asked is SAID — the run discloses the blocking gates
    outside the registry as NOT_MEASURED instead of letting one unqualified
    `PASS  this landing introduces no hygiene finding` stand for the tier.

The disclosure is ADVISORY by declaration, and `test_the_coverage_disclosure_
never_changes_the_return_code` runs the program end to end to prove that is
what it actually does rather than what its docstring says.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
_PLUGIN = _PROGRAMS.parent
_REPO = _PROGRAMS.parents[3]
_MOD_PATH = _PROGRAMS / "landing_hygiene_ratchet_check.py"

_LABEL = "programs load when loaded by path"


def _load():
    spec = importlib.util.spec_from_file_location(
        "landing_hygiene_ratchet_check_2224", _MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


def _entry():
    for e in MOD._RATCHETED_GATES:
        if str(e["label"]) == _LABEL:
            return e
    pytest.fail(f"the #2104 gate is not ratcheted: {_LABEL!r} is not in "
                f"_RATCHETED_GATES — the direct-push landing path still never "
                f"asks it")


# ── half one: the gate is ASKED ───────────────────────────────────────────

def test_the_2104_gate_is_ratcheted_on_the_landing_path():
    """Without this entry no landing has an opinion about #2104 at all."""
    assert _entry() is not None


def test_the_ratcheted_label_is_one_the_hygiene_tier_really_runs():
    """A label the tier does not run would ratchet an always-empty population.

    Read the tier rather than trusting the string — the same rule
    `test_every_ratcheted_label_is_still_run_by_the_hygiene_tier` applies to
    the registry as a whole, asserted here for this entry by name so a rename
    of THIS gate is named rather than folded into a set difference.
    """
    script = _REPO / "tools" / "ci" / "repo_hygiene_gates.sh"
    assert script.is_file(), f"NOT_MEASURED: {script} is absent"
    text = script.read_text(encoding="utf-8", errors="replace")
    labels = set(re.findall(r'^run\s+"([^"]+)"', text, re.M))
    assert labels, "NOT_MEASURED: parsed zero labels out of the hygiene tier"
    assert _LABEL in labels, (
        f"{_LABEL!r} is ratcheted but the hygiene tier no longer runs it")


def test_the_entry_names_its_subject_so_the_two_arms_differ():
    """`--programs` defaults to the INSTRUMENT's own directory.

    The instrument is the candidate's for both arms, so an entry that did not
    name a subject would sweep the same tree twice, report the same set twice
    and pass vacuously forever.
    """
    subject = [tok for tok in list(_entry()["argv"])[2:]
               if "{plugin}" in tok or "{root}" in tok]
    assert subject, f"[{_LABEL}] names no subject that moves with the arm"


# ── the checker's REAL output, not a hand-written fixture of it ───────────

def _arm(root: Path, *, plant_offender: bool) -> Path:
    """A minimal arm with the plugin layout the entry's argv expects."""
    programs = root / "vibe-ic-marketplace/plugins/vibe-ic/programs"
    programs.mkdir(parents=True)
    (programs / "helper_mod.py").write_text("VALUE = 1\n")
    (programs / "clean_program.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        "from helper_mod import VALUE  # noqa: E402\n")
    if plant_offender:
        # THE DEFECT ITSELF: a BARE sibling import with no `sys.path` seeding.
        # Loaded as a script this works (the script's directory is sys.path[0]);
        # loaded by `spec_from_file_location` it raises ModuleNotFoundError.
        (programs / "offender_program.py").write_text(
            "from helper_mod import VALUE\n")
    return root


def _findings_over(root: Path, arm_name: str) -> Counter:
    return MOD._findings(_entry(), _PLUGIN, root, arm_name)


def test_the_declared_finding_shape_matches_the_checkers_real_output(tmp_path):
    """Run the REAL #2104 checker and parse its REAL failure line.

    A regex agreed only with a transcript in a test file is a fixture, not a
    gate: the checker's wording moves and the ratchet then reports a clean
    population under a label that is red. `_findings` refuses (rc 1 with no
    match) rather than passing, so this asserts the key it actually extracts.
    """
    found = _findings_over(_arm(tmp_path / "cand", plant_offender=True),
                           "candidate")
    assert found == Counter({("offender_program.py", "helper_mod"): 1}), (
        f"the entry's `finding` regex did not read the checker's own output; "
        f"got {dict(found)}")


def test_a_clean_arm_reports_no_finding(tmp_path):
    """The negative control: the same instrument over a clean tree finds none.

    Without this, a regex that matched nothing at all would pass the test above
    only by accident of the arm, and every landing would ratchet an empty set.
    """
    found = _findings_over(_arm(tmp_path / "base", plant_offender=False), "base")
    assert found == Counter(), f"a clean arm reported {dict(found)}"


def test_a_planted_bare_sibling_is_an_introduction_the_landing_refuses(tmp_path):
    """PROVE BY RUN, both directions, on the SAME instrument.

    base clean -> candidate with the defect  == an introduction (blocks);
    the reverse  == nothing (removing an offender is free and unreported).
    """
    base = _findings_over(_arm(tmp_path / "base", plant_offender=False), "base")
    cand = _findings_over(_arm(tmp_path / "cand", plant_offender=True),
                          "candidate")
    introduced = MOD.compare(base, cand)
    assert introduced == [(("offender_program.py", "helper_mod"), 0, 1)], (
        f"a planted bare-sibling import was not read as an introduction: "
        f"{introduced}")
    assert MOD.compare(cand, base) == [], (
        "removing an offender was reported as an introduction")


# ── half two: what is NOT asked is SAID ───────────────────────────────────

def test_the_disclosure_names_the_blocking_gates_this_path_never_asks():
    """Over the real tree: the gap is the tier's blocking set minus the registry."""
    ratcheted = [str(e["label"]) for e in MOD._RATCHETED_GATES]
    gaps, total, not_measured = MOD.unratcheted_blocking_labels(_REPO, ratcheted)
    assert not_measured is None, f"NOT_MEASURED over the real repo: {not_measured}"
    assert total > len(ratcheted), (
        f"the tier declares {total} blocking gate(s) and {len(ratcheted)} are "
        f"ratcheted — a registry covering everything makes this test vacuous")
    assert gaps, "the tier declares blocking gates outside the registry; none named"
    assert _LABEL not in gaps, (
        f"{_LABEL!r} is ratcheted and must not be disclosed as unasked")
    assert set(gaps).isdisjoint(ratcheted)


def test_an_absent_hygiene_tier_is_NOT_MEASURED_and_never_an_empty_gap(tmp_path):
    """The whole point: a coverage figure nobody computed is not a zero."""
    gaps, total, not_measured = MOD.unratcheted_blocking_labels(tmp_path, ["x"])
    assert not_measured and "cannot read" in not_measured
    assert gaps == [] and total == 0
    line = MOD.summarize_coverage(gaps, total, not_measured, 1)
    assert line.startswith(f"{MOD.GATE}: NOT_MEASURED")
    assert "are ratcheted on this path" not in line, (
        "an unreadable tier was summarised as full coverage")


def test_a_tier_whose_declaration_syntax_no_longer_parses_is_NOT_MEASURED(tmp_path):
    """Rot in the tier's syntax must be loud, not read as 'nothing missing'."""
    script = tmp_path / MOD._HYGIENE_TIER_REL
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\ncheck 'a gate under another word'\n")
    gaps, total, not_measured = MOD.unratcheted_blocking_labels(tmp_path, ["x"])
    assert not_measured and "zero `run` declarations" in not_measured
    assert gaps == [] and total == 0


def test_a_gate_the_landing_adds_is_disclosed_by_the_same_landing(tmp_path):
    """The subject is the CANDIDATE's tree, so a new blocking gate shows at once."""
    script = tmp_path / MOD._HYGIENE_TIER_REL
    script.parent.mkdir(parents=True)
    script.write_text('#!/bin/bash\nrun "kept" "$ROOT" true\n'
                      'run "brand new blocking gate" "$ROOT" true\n')
    gaps, total, not_measured = MOD.unratcheted_blocking_labels(tmp_path, ["kept"])
    assert not_measured is None
    assert total == 2 and gaps == ["brand new blocking gate"]


def test_the_non_blocking_wrapper_is_not_counted_as_an_unasked_question(tmp_path):
    """`run_tolerating_uncheckable` does not block, so not asking it is not this.

    Counting it would inflate the disclosure with gates whose absence costs
    nothing, and a number padded with things that do not matter is the reason
    numbers stop being read.
    """
    script = tmp_path / MOD._HYGIENE_TIER_REL
    script.parent.mkdir(parents=True)
    script.write_text('#!/bin/bash\nrun "blocking one" "$ROOT" true\n'
                      'run_tolerating_uncheckable "tolerant one" "$ROOT" true\n')
    gaps, total, _ = MOD.unratcheted_blocking_labels(tmp_path, [])
    assert total == 1 and gaps == ["blocking one"]


# ── the ADVISORY declaration, proved by running the program ───────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def test_the_coverage_disclosure_never_changes_the_return_code(tmp_path):
    """END TO END. Coverage NOT_MEASURED + nothing introduced == rc 0.

    Declared ADVISORY in the module docstring; this is the run that proves the
    program behaves that way. The fixture repo carries no
    `tools/ci/repo_hygiene_gates.sh`, so coverage is unanswerable — and an
    unanswerable ADVISORY must not refuse a landing that introduced nothing.
    """
    repo = tmp_path / "repo"
    _arm(repo, plant_offender=False)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    (repo / "docs.md").write_text("a landing that touches no program\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "head")

    proc = subprocess.run(
        [sys.executable, str(_MOD_PATH), "--repo", str(repo),
         "--plugin-root", str(_PLUGIN), "--base", base,
         "--only", _LABEL],
        capture_output=True, text=True, timeout=900)
    out = proc.stdout + proc.stderr
    assert f"{MOD.GATE}: NOT_MEASURED" in out, out[-2000:]
    assert proc.returncode == 0, (
        f"the ADVISORY coverage disclosure changed the verdict "
        f"(rc={proc.returncode}):\n{out[-2000:]}")
    assert f"PASS — [{_LABEL}]" in out, out[-2000:]


# ── the REACHABILITY the issue is named for, pinned as a property ─────────

_LAND = _REPO / "tools" / "gatekeeper-land.sh"
_PLAN = _REPO / "tools" / "ci" / "landing_execution_plan.py"

_RATCHET_UNIT = "cheap:hygiene-ratchet"
_TIER_UNIT = "full:repo-hygiene"


def _plan_phase(unit: str) -> str:
    assert _PLAN.is_file(), f"NOT_MEASURED: {_PLAN} is absent"
    text = _PLAN.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\(\s*"%s"\s*,\s*"([a-z_]+)"' % re.escape(unit), text)
    assert m, (f"{unit!r} is not declared in "
               f"tools/ci/landing_execution_plan.py")
    return m.group(1)


def test_the_ratchet_and_the_tier_are_in_different_phases_and_that_is_2224():
    """The whole defect, read off the plan: one phase runs, the other does not.

    `landing_plan_dispatch cheap` skips every unit whose phase is not `cheap`,
    and `--cheap-only` exits before any other phase is dispatched. So a unit
    registered outside `cheap` is unreachable on the direct-push path — which
    is where the 154-gate hygiene tier is, and is why the ratchet exists and
    why #2104's gate had to join its registry rather than rely on the tier.
    """
    assert _plan_phase(_RATCHET_UNIT) == "cheap", (
        f"{_RATCHET_UNIT} left the cheap phase — the direct-push landing no "
        f"longer asks ANY gate in this registry, which is #2224 again")
    assert _plan_phase(_TIER_UNIT) != "cheap", (
        f"{_TIER_UNIT} is now a cheap unit; if the tier really does run on "
        f"the direct-push path this registry is redundant and should be "
        f"reconsidered, not left half-wired")


def test_the_cheap_phase_is_dispatched_before_the_cheap_only_exit():
    """And the plan buys nothing if the script dispatches it too late.

    Read from the script's EXECUTABLE lines. A comment is not a call site:
    `run_capture "full:repo-hygiene"` appears at line 902 inside #2176's own
    explanation of this defect, 40 lines ABOVE the exit that skips it, and
    reading that as the call site inverts the answer.
    """
    assert _LAND.is_file(), f"NOT_MEASURED: {_LAND} is absent"
    lines = _LAND.read_text(encoding="utf-8", errors="replace").splitlines()
    code = [(i, l) for i, l in enumerate(lines)
            if not l.lstrip().startswith("#")]

    def _first(pattern: str) -> int:
        for i, line in code:
            if re.search(pattern, line):
                return i
        pytest.fail(f"NOT_MEASURED: {pattern!r} matches no executable line of "
                    f"tools/gatekeeper-land.sh")

    cheap = _first(r"^landing_plan_dispatch cheap\b")
    cheap_exit = _first(r'^if \[ "\$CHEAP_ONLY" = "1" \]')
    assert cheap < cheap_exit, (
        f"the cheap phase is dispatched at line {cheap + 1}, AFTER the "
        f"--cheap-only exit at {cheap_exit + 1} — the ratchet never runs on "
        f"the direct-push path")
    later = [i for i, line in code
             if re.search(r"^landing_plan_dispatch\s+(?!cheap\b)", line)]
    assert later, "NOT_MEASURED: no other phase is dispatched at all"
    assert min(later) > cheap_exit, (
        f"a non-cheap phase is dispatched at line {min(later) + 1}, before "
        f"the --cheap-only exit at {cheap_exit + 1}; the two-tier split this "
        f"registry is built on no longer holds")
