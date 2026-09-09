"""vibe-ic#2183, secondary finding — Step DT1 read a `pdk` Step 11 had not bound.

WHAT WAS MEASURED, ON LIVE MAIN, BEFORE THIS WAS WRITTEN
========================================================
`step_dft_lec_chain` binds `pdk` at exactly one place — inside the Step-11
branch, which is the `else` of `if not full_chip: ... elif not clk: ...`. Step
DT1 (transition-delay-fault ATPG), 500 lines further down, reads it at FUNCTION
scope. DT1's own preconditions are different and weaker: a derivable clock and
`phase2/stage2/dft/cut_netlist.v` on disk. Both survive an earlier full run, and
neither implies Step 11 ran this time.

So on a `--skip-phase3` run (`full_chip=False`) over a tree that already has a
cut netlist, driving the REAL step at b6a73c0aa3 raised:

    File ".../design_one_shot_runner.py", line 19582, in step_dft_lec_chain
        if pdk and pdk == _cpdk.COMMERCIAL_PDK_ID:
    UnboundLocalError: cannot access local variable 'pdk'

Nothing catches it. `main()` calls this through `_spf.gate` and then
`plan.extend(...)`, so the exception leaves the runner and the whole phase-2 run
dies at steps 11-13 — after step 9 has already written its artefacts.

WHY THE REPAIR IS A SNIFF AND NOT A DEFAULT
===========================================
`""` is `_dft_atpg_sniff_pdk`'s OWN answer for a generic / unmapped netlist.
Initialising `pdk = ""` would have made the crash go away while publishing
"there are no library-mapped cells" on a run that never looked — a zero nobody
measured, which is the shape this tree keeps repairing. `None` means NOT
SNIFFED, and DT1 sniffs when it sees it, using the same call on the same
netlist as Step 11 so the two branches can never name different PDKs.

`test_the_binding_is_at_function_scope` is the guard for the CLASS: the
behavioural tests below cover the one path measured, and re-nesting the binding
inside any conditional re-opens it for whichever path is added next.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROGRAMS = Path(__file__).resolve().parent.parent
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import design_one_shot_runner as R        # noqa: E402
import _commercial_pdk as _cpdk           # noqa: E402

_NETLIST_REL = "phase2/stage2/synth/netlist.v"


def _project(tmp_path: Path, *, cut_netlist: bool, clocked: bool = True) -> Path:
    """DT1's real preconditions, and only those. Step 11 is NOT run here — the
    tree is the state an earlier `full_chip` run leaves behind."""
    proj = tmp_path / "proj"
    (proj / "phase2/stage2/synth").mkdir(parents=True)
    (proj / "phase2/stage2/dft").mkdir(parents=True)
    port = "input clk, " if clocked else ""
    (proj / _NETLIST_REL).write_text(
        f"module top({port}input rst_n, output q);\n"
        "  DFF _1_ (.D(rst_n), .Q(q));\n"
        "endmodule\n")
    if cut_netlist:
        (proj / "phase2/stage2/dft/cut_netlist.v").write_text(
            "module top_cut(input clk); endmodule\n")
    return proj


@pytest.fixture()
def spawned(monkeypatch):
    """Every subprocess the step would launch, captured. The defect fires
    BEFORE the first one, so nothing here can hide or manufacture it."""
    seen: list = []

    def _run(argv, *a, **k):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(R._pr, "run", _run)
    return seen


def _drive(proj: Path):
    return R.step_dft_lec_chain(proj, "top", "", "digital_arithmetic_primitive",
                                full_chip=False)


def _tdf_argv(spawned):
    hits = [a for a in spawned
            if any("transition_fault_atpg_run.py" in str(x) for x in a)]
    assert hits, f"DT1 launched no producer; spawned={spawned}"
    return hits[0]


# --------------------------------------------------------------------------- #
# 1. THE DEFECT
# --------------------------------------------------------------------------- #
def test_dt1_runs_when_step11_did_not_and_does_not_raise(tmp_path, spawned):
    """The measured crash, as a claim. `full_chip=False` skips Step 11 — where
    `pdk` is bound — while DT1's own preconditions are met by the tree."""
    proj = _project(tmp_path, cut_netlist=True)
    rows = _drive(proj)
    assert [r.name for r in rows] == ["dft_insertion", "post_dft_opt",
                                      "lec_equivalence"]
    assert rows[0].status == "SKIP", rows[0].detail
    # and DT1 really reached its producer — otherwise the line that reads `pdk`
    # was never executed and this test would pass without touching the defect
    assert _tdf_argv(spawned)[3:5] == ["--clock", "clk"]


def test_the_precondition_branch_is_untouched(tmp_path, spawned):
    """The other side of DT1's own `if`: with no cut netlist it discloses a
    precondition-unmet skip and launches nothing. A repair that made DT1 run
    where it used not to would be a different change from this one."""
    proj = _project(tmp_path, cut_netlist=False)
    _drive(proj)
    assert not [a for a in spawned
                if any("transition_fault_atpg_run.py" in str(x) for x in a)]
    marker = proj / "phase2/stage2/dft/transition_atpg_not_run.json"
    assert marker.is_file() and "precondition_unmet" in marker.read_text()


# --------------------------------------------------------------------------- #
# 2. THE REPAIR IS A MEASUREMENT, NOT A DEFAULT
# --------------------------------------------------------------------------- #
def test_dt1_sniffs_the_same_netlist_step11_would_have(tmp_path, spawned,
                                                       monkeypatch):
    """`pdk` is not defaulted here — it is sniffed, by the same function and
    from the same file Step 11 hands it, so the two branches cannot name
    different PDKs for one run."""
    asked: list = []
    real = R._dft_atpg_sniff_pdk

    def _spy(project, netlist_rel):
        asked.append(netlist_rel)
        return real(project, netlist_rel)

    monkeypatch.setattr(R, "_dft_atpg_sniff_pdk", _spy)
    _drive(_project(tmp_path, cut_netlist=True))
    assert asked == [_NETLIST_REL], (
        "DT1 did not sniff the PDK it is about to declare on the producer's "
        "command line — a `pdk` that is defaulted rather than measured "
        "publishes an answer about a netlist nobody read")


def test_a_generic_netlist_adds_no_pdk_dir(tmp_path, spawned):
    """The negative control for the test below: a netlist with no
    library-mapped cells sniffs to "" and DT1 must pass no `--pdk-dir`."""
    argv = (_drive(_project(tmp_path, cut_netlist=True)), _tdf_argv(spawned))[1]
    assert "--pdk-dir" not in argv, argv


def test_a_commercial_pdk_netlist_reaches_the_producer_as_pdk_dir(
        tmp_path, spawned, monkeypatch):
    """And the positive one. Without it, `test_a_generic_netlist_adds_no_pdk_dir`
    would pass on a repair that dropped the `--pdk-dir` argument entirely.

    The real SKU is NDA config and is never written in shipped source, and it
    is EMPTY inside the pinned image — so reading `_commercial_pdk` here would
    make this arm skip, which is a NOT-MEASURED dressed as a pass. A stand-in
    id is installed on the module the runner actually consults instead, so the
    arm is measured on every checkout and the source stays SKU-free."""
    fake = "pdk-under-test"
    monkeypatch.setattr(_cpdk, "COMMERCIAL_PDK_ID", fake)
    assert R._cpdk.COMMERCIAL_PDK_ID == fake, "the runner reads this module"
    monkeypatch.setattr(R, "_dft_atpg_sniff_pdk",
                        lambda project, rel: (project / rel, fake))
    proj = _project(tmp_path, cut_netlist=True)
    (proj / "input" / "pdk").mkdir(parents=True)
    _drive(proj)
    argv = _tdf_argv(spawned)
    assert "--pdk-dir" in argv, argv
    assert argv[argv.index("--pdk-dir") + 1] == str(
        (proj / "input" / "pdk").resolve())


# --------------------------------------------------------------------------- #
# 3. THE CLASS, NOT ONLY THE PATH
# --------------------------------------------------------------------------- #
def test_the_binding_is_at_function_scope():
    """`pdk` must be bound in the function's OWN body, not inside a branch.

    The behavioural tests above cover the one path that was measured. This is
    the claim that survives the next path somebody adds: every read of `pdk` in
    this function is reachable from a binding that always runs."""
    tree = ast.parse(inspect.getsource(R))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "step_dft_lec_chain")

    def _binds(stmt) -> bool:
        """The STATEMENT ITSELF, never its subtree.

        Descending would count the `if not full_chip: ... else: pdk = ...`
        block as a top-level binding — which is precisely the defect, and this
        assertion then passes on the tree it is supposed to fail on. It did:
        measured against live main before the walk was narrowed."""
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            return False
        for tgt in targets:
            for n in ([tgt] + list(getattr(tgt, "elts", []))):
                if isinstance(n, ast.Name) and n.id == "pdk":
                    return True
        return False

    top_level = [s for s in fn.body if _binds(s)]
    assert top_level, (
        "`pdk` is bound only inside a branch of `step_dft_lec_chain`. Every "
        "later reader of it — Step DT1 is one — then depends on that branch "
        "having been taken, and raises UnboundLocalError when it was not. "
        "See vibe-ic#2183's secondary finding.")
    first_read = min(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Name) and n.id == "pdk"
         and isinstance(n.ctx, ast.Load)), default=None)
    assert first_read is not None
    assert min(s.lineno for s in top_level) < first_read, (
        "the unconditional binding comes after the first read")
