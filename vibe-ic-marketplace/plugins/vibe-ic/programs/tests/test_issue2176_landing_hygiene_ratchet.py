#!/usr/bin/env python3
"""vibe-ic#2176 — the landing path must refuse a hygiene finding it INTRODUCES.

The defect these tests pin is not in a gate; it is in the process. The hygiene
tier is the only instrument that measures the seven gates red on main, and
`tools/gatekeeper-land.sh --cheap-only` exits before that tier runs, so the
landing path has no opinion about them at all. Six of the seven reds on main on
2026-09-07 were introduced that day by this repository's own landings.

`landing_hygiene_ratchet_check.py` is the delta that can be afforded on the
landing path. What is proved here:

  * the registry cannot rot quietly — every label it ratchets is still a label
    `tools/ci/repo_hygiene_gates.sh` actually runs;
  * a NEW finding key blocks;
  * an ADDITIONAL occurrence under an EXISTING key blocks — this is the
    `dbba4729c` shape, three findings in one file, which plain set membership
    cannot see;
  * shrinking and disappearing are FREE and are never reported, so removing an
    offender can never pay for adding one;
  * every unanswerable question is a REFUSAL, never a pass.
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
_REPO = _PROGRAMS.parents[3]
_MOD_PATH = _PROGRAMS / "landing_hygiene_ratchet_check.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "landing_hygiene_ratchet_check", _MOD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


# ── the registry cannot rot quietly ───────────────────────────────────────

def test_every_ratcheted_label_is_still_run_by_the_hygiene_tier():
    """A gate renamed in the hygiene tier must not leave a dead row here.

    A registry naming a label nothing runs would ratchet a population that is
    always empty: green forever, measuring nothing. Read the hygiene script
    rather than trusting the string.
    """
    script = _REPO / "tools" / "ci" / "repo_hygiene_gates.sh"
    assert script.is_file(), f"NOT_MEASURED: {script} is absent"
    text = script.read_text(encoding="utf-8", errors="replace")
    labels = set(re.findall(r'^run\s+"([^"]+)"', text, re.M))
    assert labels, "NOT_MEASURED: parsed zero labels out of the hygiene tier"
    declared = {str(e["label"]) for e in MOD._RATCHETED_GATES}
    assert declared, "an empty ratchet registry ratchets nothing"
    assert declared <= labels, (
        f"ratcheted label(s) no longer run by the hygiene tier: "
        f"{sorted(declared - labels)}")


def test_the_subject_of_every_entry_moves_with_the_arm():
    """An entry that does not NAME its subject measures the instrument twice.

    `loop_watchdog_compliance_check.py` defaults its root to
    `Path(__file__).resolve().parent` -- the INSTRUMENT's directory, which is
    the candidate's for both arms. An entry invoking it bare would scan the
    same tree twice, report the same set twice, and pass vacuously forever:
    a gate that cannot fail is not a gate. So every entry must carry at least
    one argv token AFTER the checker that resolves against the arm.
    """
    for entry in MOD._RATCHETED_GATES:
        subject_tokens = [tok for tok in list(entry["argv"])[2:]
                          if "{plugin}" in tok or "{root}" in tok]
        assert subject_tokens, (
            f"[{entry['label']}] names no subject on its command line, so both "
            f"arms would measure the instrument")


def test_every_ratcheted_checker_exists_on_disk():
    for entry in MOD._RATCHETED_GATES:
        prog = str(entry["argv"][1]).format(plugin=_PROGRAMS.parent,
                                            root=_REPO)
        assert Path(prog).is_file(), f"declared checker is absent: {prog}"


# ── the comparison, in both directions ────────────────────────────────────

K1 = ("programs/a.py", "R1")
K2 = ("programs/b.py", "R1")


def test_a_new_key_is_an_introduction():
    assert MOD.compare(Counter({K1: 1}), Counter({K1: 1, K2: 1})) == [
        (K2, 0, 1)]


def test_an_additional_occurrence_under_an_existing_key_is_an_introduction():
    """The `dbba4729c` shape: :14 was already there, :52 and :54 were added.

    Set membership alone reports nothing here, which is exactly the landing
    that must be refused.
    """
    assert MOD.compare(Counter({K1: 1}), Counter({K1: 3})) == [(K1, 1, 3)]


def test_shrinking_is_free_and_is_not_reported():
    assert MOD.compare(Counter({K1: 3}), Counter({K1: 1})) == []


def test_a_key_that_disappears_is_never_mentioned():
    assert MOD.compare(Counter({K1: 1, K2: 1}), Counter({K2: 1})) == []


def test_removing_one_offender_does_not_pay_for_adding_another():
    """No total, so nothing to trade. This is what makes it not a count ratchet."""
    introduced = MOD.compare(Counter({K1: 5}), Counter({K2: 1}))
    assert introduced == [(K2, 0, 1)], (
        "a landing that removed five offenders and added one must still be "
        "refused for the one")


def test_an_unchanged_population_introduces_nothing():
    assert MOD.compare(Counter({K1: 9}), Counter({K1: 9})) == []


# ── every unanswerable question refuses ───────────────────────────────────

def _entry(script: Path):
    return {"label": "probe", "argv": ("python3", str(script), "{plugin}"),
            "finding": re.compile(r"^\s+(?P<path>\S+?):\d+\s+\[(?P<rule>\w+)\]",
                                  re.M)}


def test_a_failure_this_program_cannot_parse_is_a_refusal(tmp_path):
    """rc=1 with no parsable finding must never read as 'found nothing'."""
    script = tmp_path / "unparsable.py"
    script.write_text("import sys\nprint('FAIL - something')\nsys.exit(1)\n")
    with pytest.raises(MOD.Refusal) as exc:
        MOD._findings(_entry(script), _PROGRAMS.parent, tmp_path, "candidate")
    assert "cannot say what it found" in str(exc.value)


def test_an_exit_code_that_is_neither_clean_nor_a_report_is_a_refusal(tmp_path):
    script = tmp_path / "crashes.py"
    script.write_text("import sys\nsys.exit(2)\n")
    with pytest.raises(MOD.Refusal) as exc:
        MOD._findings(_entry(script), _PROGRAMS.parent, tmp_path, "base")
    assert "neither clean" in str(exc.value)


def test_a_checker_that_cannot_be_run_is_a_refusal(tmp_path):
    with pytest.raises(MOD.Refusal):
        MOD._findings(_entry(tmp_path / "absent.py"), _PROGRAMS.parent,
                      tmp_path, "base")


def test_an_unarchivable_base_is_a_refusal(tmp_path):
    with pytest.raises(MOD.Refusal) as exc:
        MOD._archive(_REPO, "refs/heads/no-such-ref-2176", tmp_path / "x")
    assert "could not archive" in str(exc.value)


def test_only_naming_no_declared_gate_refuses_rather_than_passing_vacuously():
    rc = MOD.main(["--repo", str(_REPO),
                   "--plugin-root", str(_PROGRAMS.parent),
                   "--base", "HEAD", "--only", "no such gate"])
    assert rc == 2


# ── the instrument does not move between arms ─────────────────────────────

def test_the_checker_is_taken_from_the_candidate_for_both_arms(tmp_path):
    """A landing that TIGHTENS a rule must not be refused for improving it.

    If each arm ran its OWN copy of the checker, a tightened rule would report
    every pre-existing offender as introduced. Only the SUBJECT moves.
    """
    instrument = tmp_path / "instrument"
    (instrument / "programs").mkdir(parents=True)
    script = instrument / "programs" / "probe.py"
    # Reports one finding naming the SUBJECT it was pointed at, so the test can
    # tell which copy of the checker ran.
    script.write_text(
        "import sys\n"
        "print('  from-instrument/%s:1 [R1] x' % sys.argv[1].split('/')[-1])\n"
        "sys.exit(1)\n")
    entry = {"label": "probe",
             "argv": ("python3", "{plugin}/programs/probe.py", "{plugin}"),
             "finding": re.compile(
                 r"^\s+(?P<path>\S+?):\d+\s+\[(?P<rule>\w+)\]", re.M)}
    arm = tmp_path / "arm"
    (arm / "vibe-ic-marketplace" / "plugins" / "vibe-ic").mkdir(parents=True)
    got = MOD._findings(entry, instrument, arm, "base")
    assert list(got) == [("from-instrument/vibe-ic", "R1")], (
        "the instrument must come from --plugin-root; the subject from the arm")
