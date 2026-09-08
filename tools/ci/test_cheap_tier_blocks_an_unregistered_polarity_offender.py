#!/usr/bin/env python3
"""The polarity ratchet is wired into the LANDING TIER, not only into a suite.

vibe-ic#712. `prose_polarity_consulted_check.py --ratchet` renders a MEMBERSHIP
verdict against `_OFFENDER_REGISTER`: it fails on an offender that is not
registered — a landing ADDING one — and on a register entry that outlived its
offender. A gate nobody runs at landing time stops a nothing, so the wiring is
pinned here as well as the behaviour.

WHY MEMBERSHIP AND NOT A COUNT, since a count is the obvious thing to wire:
measured across v1.17.51..v1.17.83 the polarity-blind population went
212 -> 213 -> 214 -> 213 -> 214 -> 215, because entries both ENTER and LEAVE. The
number moved DOWN inside a window in which three new offenders arrived, so a
count-based gate would have passed all three and a count-based bisect names the
wrong landing. Only the set names them.
"""
from __future__ import annotations

import json
import re
import runpy
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_LAND = _REPO / "tools" / "gatekeeper-land.sh"
_PLUGIN = _REPO / "vibe-ic-marketplace" / "plugins" / "vibe-ic"
_GATE = _PLUGIN / "programs" / "prose_polarity_consulted_check.py"

#: A prose extractor of exactly the shape the scanner is looking for: it
#: `.search`es text and writes the MATCH-DERIVED value into a record, and it
#: never consults the polarity vocabulary. Nothing about it is special — that is
#: the point, it is what an ordinary careless landing looks like.
_SYNTHETIC_OFFENDER = '''\
import re

_TARGET_RE = re.compile(r"target is (\\\\w+)")


def read_target(text, out):
    m = _TARGET_RE.search(text)
    if m:
        out["target"] = m.group(1)
    return out
'''


#: 60 s, WHICH IS THE HARNESS CEILING AND NOT A ROUND NUMBER.
#: `ci_harness_timeout_ceiling_check.py` resolves the binding pytest bound from
#: `.github/workflows/*.yml` — MIN(180, 300) = 180 — and divides it by 3, so any
#: bound above 60 s here can never FIRE: `--timeout-method=thread` takes the
#: whole SESSION down at 180 s first, and every other file in the selection
#: loses its verdict with it. This file first shipped `timeout=900`, which that
#: gate flagged by name (`test_the_two_trees_use_different_globs_for_a_measured
#: _reason`), and 900 was a number nobody had measured.
#:
#: MEASURED, with the load average beside it as that gate's own advisory census
#: asks: the widest call this helper makes is the WHOLE-TREE `--ratchet` run in
#: `test_the_ratchet_passes_the_tree_that_ships`, at 26.7 / 27.1 / 27.3 s on
#: 8HD-4 under a 5-minute load average of 64. The synthetic-tree calls are under
#: a second. So 60 s is ~2.2x the widest observed call on a host running at
#: eight times its core count, and the bound can still fire inside the harness.
_GATE_TIMEOUT_S = 60


def _run_gate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_GATE), *args],
                          capture_output=True, text=True,
                          timeout=_GATE_TIMEOUT_S)


# ── the WIRING: red before it exists ──────────────────────────────────────
def test_the_cheap_tier_runs_the_polarity_ratchet(tmp_path):
    """The landing tier must invoke the gate, in `--ratchet` mode.

    RED before the wiring: this is the assertion that fails on a tree where the
    gate exists and nothing at landing time runs it.
    """
    text = _LAND.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines()
             if "prose_polarity_consulted_check.py" in ln]
    assert lines, (
        "tools/gatekeeper-land.sh never invokes prose_polarity_consulted_check.py; "
        "a gate nobody runs at landing time stops nothing")
    # the invocation and its flag may be split across a line continuation
    joined = text.replace("\\\n", " ")
    inv = [ln for ln in joined.splitlines()
           if "prose_polarity_consulted_check.py" in ln]
    assert any("--ratchet" in ln for ln in inv), (
        "the landing tier runs the gate WITHOUT --ratchet. The no-argument mode "
        "compares against a baseline debt file and prints an errand naming a "
        "write flag; a landing gate must name the offender and its owner, never "
        "a flag that banks it.\n" + "\n".join(inv))
    assert "\nlanding_plan_dispatch cheap\n" in text
    plan = runpy.run_path(str(_REPO / "tools/ci/landing_execution_plan.py"))

    def extract(name):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}$", text, re.M | re.S)
        assert match, name
        return match.group(0)

    # Exercise the real plan -> dispatcher -> handler -> capture/emit/record.
    # Unrelated cheap units are explicitly SKIP, not fabricated gate results.
    # Only the gate's input root/register are synthetic; its predicate is real.
    functions = "\n".join(extract(name) for name in (
        "landing_plan_dispatch", "landing_record", "lane_write",
        "lane_reported", "run_capture", "lane_resolve", "run_emit", "run",
        "landing_unit_cheap_prose_polarity"))
    unrelated = "\n".join(
        'landing_unit_' + unit.replace(':', '_').replace('-', '_')
        + '() { landing_record "$1" SKIP 0 "outside this control"; }'
        for unit, phase, _ in plan["STEPS"]
        if phase == "cheap" and unit != "cheap:prose-polarity")
    root = tmp_path / "plugin"
    (root / "programs").mkdir(parents=True)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"_comment": "synthetic", "known": []}))

    def drive(name, mutation=""):
        lanes = tmp_path / name
        lanes.mkdir()
        script = f'''set -uo pipefail
PROGRAMS={shlex.quote(str(_GATE.parent))}
LANE_DIR={shlex.quote(str(lanes))}
FAILED=0; LANDING_NEXT=0; LANDING_RECORD_ENABLED=0
LANE_BROKEN=0; LANE_WAIT_RC=0
{plan['shell']()}
{functions}
{unrelated}
python3() {{
  [ "$1" = {shlex.quote(str(_GATE))} ] || return 93
  command python3 "$@" --root {shlex.quote(str(root))} --baseline {shlex.quote(str(baseline))}
}}
{mutation}
landing_plan_dispatch cheap
cat "$LANE_DIR/cheap:prose-polarity.out"
echo "PREDICATE_RC=$(cat "$LANE_DIR/cheap:prose-polarity.rc")"
echo "FAILED=$FAILED"
exit "$FAILED"
'''
        result = subprocess.run(["bash", "-c", script], capture_output=True,
                                text=True, timeout=_GATE_TIMEOUT_S)
        print(f"CONTROL {name}: rc={result.returncode}\n{result.stdout}{result.stderr}")
        return result

    clean = drive("clean")
    assert clean.returncode == 0 and "PREDICATE_RC=0" in clean.stdout, clean.stdout + clean.stderr
    assert "[PASS]" in clean.stdout
    (root / "programs" / "careless_landing.py").write_text(_SYNTHETIC_OFFENDER)
    bad = drive("offender")
    assert bad.returncode == 1 and "PREDICATE_RC=1" in bad.stdout, bad.stdout + bad.stderr
    assert "careless_landing::read_target" in bad.stdout and "FAILED=1" in bad.stdout
    for name, mutation in (
        ("missing-handler", "unset -f landing_unit_cheap_prose_polarity"),
        ("disconnected-handler", "landing_unit_cheap_prose_polarity() { :; }"),
    ):
        refused = drive(name, mutation)
        assert refused.returncode == 2 and "[NORECORD]" in refused.stderr, (
            refused.stdout + refused.stderr)


# ── the BEHAVIOUR, both directions ────────────────────────────────────────
def test_the_ratchet_refuses_a_tree_carrying_an_unregistered_offender(tmp_path):
    """A synthetic tree with one unregistered offender must be REFUSED."""
    root = tmp_path / "plugin"
    (root / "programs").mkdir(parents=True)
    (root / "programs" / "careless_landing.py").write_text(_SYNTHETIC_OFFENDER)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"_comment": "synthetic", "known": []}))

    r = _run_gate("--root", str(root), "--baseline", str(baseline), "--ratchet")
    assert r.returncode == 1, (
        "an unregistered polarity-blind extractor did not block:\n"
        f"{r.stdout}\n{r.stderr}")
    assert "careless_landing::read_target" in r.stdout, (
        "the refusal must NAME the offender:\n" + r.stdout)
    assert "register" in r.stdout.lower()


def test_the_ratchet_passes_the_tree_that_ships():
    """And it must not refuse everything: the shipped tree is GREEN.

    A gate that cannot pass is not a gate — it is a ban, and this repo has
    already learned to distrust one.
    """
    r = _run_gate("--ratchet")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS]" in r.stdout


def test_the_refusal_names_no_write_flag():
    """An errand is not a finding (hygiene census #2066, CZH-12).

    The red must name the offender and the owning lane. A red that also prints
    "re-run with --write-baseline" invites the next lane to bank every offender
    that run happened to see as accepted debt.
    """
    root = Path(__file__).parent
    r = _run_gate("--root", str(root), "--ratchet")
    for flag in ("--write-baseline", "--record-shrink"):
        assert flag not in r.stdout, (
            f"the ratchet's output offers {flag}, which banks the finding "
            f"instead of reporting it:\n{r.stdout}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
