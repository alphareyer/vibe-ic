#!/usr/bin/env python3
"""ORGANIC #771 [P2 structural real_gap] — two prompt parsers in the SAME ppa
gate file disagreed on the EXACT prompt: `parse_threshold_from_prompt` used a
±60-char metric window, but `_nearest_metric_for_pct` (the clause parser's
backward fallback) used only 40 chars. An explicitly single-metric spec whose
metric word sat 41-60 chars before the '%' silently degraded to the conservative
`both` bind → a correct wire-only / cell-only optimization received a vacuous
NOT-APPLICABLE verdict (no enforcement of the spec's explicit success criterion).

Fix: widen the backward window 40→60 to match the single-tuple parser, AND
collapse to `both` when BOTH metric words co-occur in the (now wider) window with
no clause break — so a true "both cells and wires by N%" spec is not mis-bound to
the nearer single metric (the widening's own no-leak boundary).

§4.05 NO-LEAK: this is a STRUCTURAL real_gap — it must KEEP blocking once fixed.
A multi-clause "cells by 20% OR wires by 12%" must still split into two clauses;
a true "both" spec must stay `both`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import ppa_area_threshold_check as P  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402


def _clauses(txt):
    return P.parse_threshold_clauses_from_prompt(txt)[0]


# ── NEW-PATH: single-metric specs bind correctly (no degrade to 'both') ──────
def test_771_wire_only_binds_wires():
    txt = ("Reduce the interconnect, specifically the number of wires used. "
           "The minimum reduction must be 50% for this to count.")
    assert P.parse_threshold_from_prompt(txt) == (50.0, "wires")
    assert _clauses(txt) == [(50.0, "wires")]


def test_771_cell_only_binds_cells():
    txt = "Cut down on the cell count. A reduction of at least 70% is required."
    assert _clauses(txt) == [(70.0, "cells")]


def test_771_two_sentence_phrasing_binds_single_metric():
    txt = "Use fewer wires.\nThe area reduction threshold must be 60%."
    assert _clauses(txt) == [(60.0, "wires")]


# ── §4.05 NO-LEAK: a true 'both' spec must STAY both (the widening must not
#    mis-bind it to the nearer single metric) ──────────────────────────────────
def test_771_noleak_true_both_stays_both():
    txt = "Reduce both cells and wires by at least 30%."
    assert P.parse_threshold_from_prompt(txt) == (30.0, "both")
    assert _clauses(txt) == [(30.0, "both")]


# ── §4.05 NO-LEAK: a genuine multi-clause spec must STILL split ──────────────
def test_771_noleak_multiclause_or_still_splits():
    txt = "Cells must drop by 20% or wires by 12%."
    cl, comb = P.parse_threshold_clauses_from_prompt(txt)
    assert sorted(cl) == [(12.0, "wires"), (20.0, "cells")], cl
    assert comb == "or"


def test_771_noleak_multiclause_and_still_splits():
    txt = "Reduce cells by 25% and wires by 15%."
    cl, _ = P.parse_threshold_clauses_from_prompt(txt)
    assert sorted(cl) == [(15.0, "wires"), (25.0, "cells")], cl


import sys as _cg_sys
from pathlib import Path as _cg_path
_cg_sys.path.insert(0, str(_cg_path(__file__).resolve().parent))
import _container_guard as _cg  # noqa: E402


def _container_up(container: str | None = None) -> bool:
    """Is the PINNED runtime reachable under the name the PROGRAM will use?

    #2230 replaced `docker inspect -f {{.State.Running}}` — presence, not
    identity — with `container_usable`, so a container running the wrong bytes
    stopped being a red and became the skip an absent one always got. That
    fixed half of the cause and left the other half standing: the NAME asked
    about was still the bare literal `vibeic-eda`, which is not the name the
    subject uses.

    MEASURED 2026-09-10 on 8HD-8, clean main 1ace9b3f85, by running the two
    containers apart. With the pinned runtime absent under the name the program
    derives, `ppa_area_threshold_check` answers:

        NOT-APPLICABLE: container 'vibeic-eda-89a8fd729520' is not running
        — cannot synthesise; NOT-APPLICABLE (no false block)
        >>> RC=0   verdict=NOT_APPLICABLE

    while this file asserts BLOCK / rc 1. A guard that admits the test in that
    state produces a red about the HOST, not about the code. With the derived
    name present the same invocation returns metric='wires', verdict='BLOCK',
    rc 1 — the #771 behaviour this file exists to pin.

    IT ALSO COSTS COVERAGE IN THE OTHER DIRECTION, measured here: with the pin
    present under the derived name, the bare-literal guard SKIPPED the live
    path (7 passed, 1 skipped) while this one runs it (8 passed). `vibeic-eda`
    is guessable and shared and nothing in this repo creates it, so it can only
    be somebody else's — on this host it is held by a four-day-old container of
    a different image. Guarding on it asks which container won a race for a
    name.

    `container=None` asks `_eda_pin.default_container_name`, the per-pin name
    `ppa_area_threshold_check.main` itself defaults `--container` to: the
    container this file's live path really execs into, carrying the pin digest
    so it cannot be squatted. Same repair, same reasoning, as
    `test_v1_0_83_issue756_ppa_disjunctive_clauses`, which already carries it.
    """
    return _cg.container_usable(container)


# ── #478 END-STATE: the real program binds the single metric ('wires'), via a
#    tmp_path defect artifact + subprocess + returncode/JSON assert ────────────
# Container-gated like its ppa siblings (test_v1_0_85_issue769, _80, _83, _85):
# the program needs to SYNTHESISE to count wires, and without the container it
# honestly self-reports NOT-APPLICABLE (rc 0) — so asserting the BLOCK rc 1 is
# only meaningful when the container is up. This guard was missing here, which
# made the file the single red in a full-suite run on a host with no container.
def test_the_guard_asks_for_the_container_the_program_defaults_to():
    """THE INVARIANT THE SKIP-GUARD RESTS ON, asserted instead of assumed.

    This file's live path skips unless a container is usable, and that skip is
    honest only if the container it asks about is the one the subject execs
    into. When the two names drift apart the guard admits the test on a host
    where the program cannot synthesise, the program answers rc 0
    NOT-APPLICABLE, and the BLOCK/rc-1 assertion below fails for a reason that
    is about the host and not the code.

    THE COMPARISON IS AGAINST THE PROGRAM'S OWN ARGPARSE DEFAULT, not against
    `_eda_pin.default_container_name()`. A first version of this test compared
    the pin helper to itself, which is a tautology no input can fail — the
    thing that actually drifts is the `--container` default the CLI declares,
    and that is what is read here.
    """
    import ast
    src = (_PROGRAMS / "ppa_area_threshold_check.py").read_text(
        encoding="utf-8")
    declared = None
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        if not any(isinstance(a, ast.Constant) and a.value == "--container"
                   for a in node.args):
            continue
        for kw in node.keywords:
            if kw.arg == "default":
                declared = kw.value
    assert declared is not None, (
        "ppa_area_threshold_check no longer declares a --container default; "
        "this file's skip-guard has nothing to agree with")
    # A bare string default is the defect: it names a shared, squattable
    # container instead of the per-pin one the guard resolves.
    assert not isinstance(declared, ast.Constant), (
        "the program's --container default is the string literal "
        f"{getattr(declared, 'value', None)!r}. The skip-guard resolves "
        "`_eda_pin.default_container_name()`, so the two now name DIFFERENT "
        "containers: the guard would admit this test on a host where the "
        "program cannot synthesise, and the failure would be about the host.")
    assert (isinstance(declared, ast.Call)
            and getattr(declared.func, "attr", None)
            == "default_container_name"), ast.dump(declared)[:200]


@pytest.mark.skipif(not _container_up(),
                    reason="vibeic-eda container not running — cannot synthesise")
def test_771_endstate_real_program_binds_wires(tmp_path):
    import json
    import subprocess
    prog = _PROGRAMS / "ppa_area_threshold_check.py"
    (tmp_path / "orig.v").write_text(
        "module m(input a, output b); assign b=a; endmodule\n")
    (tmp_path / "opt.v").write_text(
        "module m(input a, output b); assign b=a; endmodule\n")
    (tmp_path / "prompt.txt").write_text(
        "Reduce the interconnect, specifically the number of wires used. "
        "The minimum reduction must be 50% for this to count.\n")
    out = tmp_path / "out.json"
    cp = subprocess.run(
        [sys.executable, str(prog),
         "--original", str(tmp_path / "orig.v"),
         "--optimized", str(tmp_path / "opt.v"), "--top", "m",
         "--prompt", str(tmp_path / "prompt.txt"), "--json", str(out)],
        capture_output=True, text=True)
    rep = json.loads(out.read_text())
    # the #771 fix: the gate binds the EXPLICIT single metric 'wires', not the
    # conservative 'both' (which made the 0-cell design vacuously NOT-APPLICABLE).
    assert rep["metric"] == "wires", rep
    # a do-nothing (0% wires) submission against an explicit 50% wires bar is a
    # real under-reduction → the gate BLOCKs (rc 1), proving enforcement is live.
    assert cp.returncode == 1, cp.stdout + cp.stderr


def test_771_endstate_parsers_agree_on_single_metric():
    txt = ("Reduce the interconnect, specifically the number of wires used. "
           "The minimum reduction must be 50% for this to count.")
    single = P.parse_threshold_from_prompt(txt)
    clauses = P.parse_threshold_clauses_from_prompt(txt)[0]
    assert single[1] == "wires"
    assert clauses == [(50.0, "wires")]
    assert clauses != [(50.0, "both")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
