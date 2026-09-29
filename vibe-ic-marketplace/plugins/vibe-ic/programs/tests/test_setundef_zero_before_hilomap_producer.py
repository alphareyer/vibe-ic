"""The synth producer emits `setundef -zero` before `hilomap` — RULE 1.

CUT_W4: the checker-driven half of this file (RULE 1 / RULE 2 read off the
recipe TEXT by yosys_tiecell_recipe_order_check) moved to the netlist gate,
test_cut9_step14_judges_the_handoff_netlist.py (UNDEFINED_CONSTANT,
CONSTANT_NOT_TIED): the consequence is judged on the netlist either arm hands
to PnR. The producer assertions below stay: the direct recipe still runs for
designs outside the chip path.

`yosys_tiecell_recipe_order_check.py` has enforced RULE 1 (`setundef -zero`
MUST precede `hilomap`) since v0.1.98, and has been wired ADVISORY for exactly
one reason, quoted from its own docstring:

    EVERY runner-produced real-PDK synthesis violates RULE 1.
    `phase3_one_shot_runner.py` builds its inline yosys command with a
    `hilomap` clause and never emits `setundef -zero` (grep: the only two
    `setundef` occurrences in that file are comments).

The same docstring argued the violation was not severe, because "the flow
already mitigates the routing symptom downstream" — the PG-net cleanup pass
retyped the resulting `zero_` net to SIGNAL and the run routed:

    phase3/stage3/pnr/openroad.log:278  PG_CLEANUP_SIG: zero_ (GROUND)
    phase3/stage3/pnr/openroad.log:595  [INFO DRT-0199]   Number of violations = 0.

vibe-ic#687 REMOVED that retype — correctly, because it was also hiding
genuinely unrouted supplies. With the mitigation gone the untied-`x` path now
reaches `PG_CLEANUP_UNROUTED_SUPPLY` and hard-FAILs PnR. MEASURED on
caravel_user_project x sky130A, plugin v1.9.65, die 2920x3520:

    synth netlist : assign io_out = { \\mprj.counter.count [15:8],
                                      22'hxxxxxx, \\mprj.counter.count [7:0] };
    openroad.log  : PG_CLEANUP_UNROUTED_SUPPLY: zero_ (GROUND) iterms=0 bterms=44
    pnr verdict   : FAIL  PG_UNROUTED_SUPPLY: 1 POWER/GROUND net(s) ...

44 driverless chip-top output bits, reported as a power/ground rail. So RULE 1's
consequence is no longer "a downstream pass decides the tie value"; it is a hard
PnR FAIL with a misleading finding name, on every design that has an unconnected
top-level output bit.

`hilomap` maps constant 1'b0 / 1'b1 to tie cells. It does NOT map `x`. So the
producer must resolve `x` to 0 FIRST — which is what RULE 1 always said.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, _PROGRAMS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    # Registered only while it executes. This runs at COLLECTION, so a copy
    # left in sys.modules replaced the runner on every xdist worker before any
    # test ran: tests that bound the original at import then monkeypatched a
    # module their code under test no longer imported (d3 live measurement,
    # pdn_em identity, r34 pad drop: red in the sweep, green alone).
    previous = sys.modules.get(name)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return mod


R = _load("phase3_one_shot_runner")

_SRC = (_PROGRAMS / "phase3_one_shot_runner.py").read_text(encoding="utf-8")

# ── the producer ──────────────────────────────────────────────────────────
def test_the_producer_emits_setundef_zero_in_the_hilomap_clause():
    """The one-line defect. Putting it INSIDE the clause (rather than at each
    of the four call sites that interpolate it) is what makes the order true
    by construction and un-driftable."""
    assert 'f"setundef -zero; {hilomap_directive}; "' in _SRC


def test_no_tie_cells_means_no_setundef_either():
    """NO-LEAK BOUNDARY (§4.05). `setundef -zero` on its own is NOT an
    improvement: with no tie cell to map to, it converts `x` into a bare 1'b0
    constant net with no driver — the same driverless shape the fix exists to
    remove, just spelled differently. The empty-clause branch must stay empty.
    """
    i = _SRC.index('f"setundef -zero; {hilomap_directive}; "')
    tail = _SRC[i:i + 200]
    assert 'if hilomap_directive else ""' in tail


# ── the rules, driven through the REAL checker ────────────────────────────
def test_setundef_zero_precedes_EVERY_hilomap_call_site():
    """The clause is interpolated at four sites. Order-by-construction means
    none of them can regress independently — but only if they all use the
    clause rather than the raw directive."""
    assert _SRC.count("{hilomap_clause}") >= 4
    # the raw directive is referenced only where the clause is built
    assert _SRC.count("{hilomap_directive}") == 1
