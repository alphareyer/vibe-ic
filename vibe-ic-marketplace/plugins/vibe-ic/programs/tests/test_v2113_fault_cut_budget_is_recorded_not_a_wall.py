"""vibe-ic#2113 O1 — `fault cut` still launched under a 120 s container wall.

vibe-ic#2082 converted the `fault atpg` launch in this file: the declared
budget became a RECORDED ceiling, the container-side `timeout` was disabled,
and the engine is stopped only when every readable forward-progress signal has
sat still for the stall grace. The call that PRODUCES the netlist that engine
reads was left behind:

    ec, out, err = _run_docker(project, cut_cmd, timeout=120, pdk_dir=pdk_dir)

`timeout=120` is `atpg_container_deadline(120, 600)` = 720 s of coreutils
`timeout -k 5 720` INSIDE the container, as the cut's own parent. A cut still
flattening flops when it expires is signalled, `run_fault` returns
`stage: "cut"`, and the design is recorded as having no scan netlist. That
absence is not one measurement: `cut_netlist.v` is the shared input of the
stuck-at, transition, path-delay and SDD passes, so one clock removes all four.

MEASURED, both directions, on a REAL `fault cut` (2 flops, sky130 dfxtp_1
naming) in the pinned image 192.168.1.112:5000/vibeic-eda@sha256:8c5694ab
(0.3.48) on 8HD-4, same argv and same container shape in both arms — the only
variable is what the declared number does. Evidence:
`evidence/proof_O1_real_fault_cut.py` in the lane directory, and the numbers in
LAND.md.

THE NUMBER IS UNCHANGED. `ATPG_CUT_BUDGET_S` is still 120: a bigger constant
would be the same defect with a later date. What changes is what it does.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

_PROGRAMS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import fault_atpg_run as F          # noqa: E402
import _watchdog as W               # noqa: E402


# ── 1. THE LAUNCH SITE CARRIES NO TERMINATING DEADLINE ──────────────────────
def _cut_launch_call(src: str | None = None) -> ast.Call:
    """The `_run_docker` call that launches `fault cut`, found by the argument
    it passes (`cut_cmd`) rather than by line number, so an edit above it
    cannot silently make this test measure a different call."""
    tree = ast.parse(src if src is not None
                     else (_PROGRAMS / "fault_atpg_run.py").read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name != "_run_docker" or len(node.args) < 2:
            continue
        arg = node.args[1]
        if isinstance(arg, ast.Name) and arg.id == "cut_cmd":
            found.append(node)
    assert len(found) == 1, (
        f"expected exactly one `_run_docker(project, cut_cmd, ...)` launch, "
        f"found {len(found)}")
    return found[0]


def test_the_cut_is_launched_supervised_and_unclocked():
    """THE DEFECT, as a property of the call that had it."""
    kw = {k.arg: k for k in _cut_launch_call().keywords}
    assert "timeout" not in kw, (
        "the `fault cut` launch passes a `timeout=` again — that is a clock "
        "the container enforces on the cut, which is vibe-ic#2113 O1 restored")
    assert "supervised" in kw and kw["supervised"].value.value is True, (
        "the `fault cut` launch is not supervised, so nothing is reading its "
        "forward progress and only a clock could ever stop it")
    assert "ceiling_s" in kw, (
        "the declared budget is not passed as a RECORDED ceiling, so the "
        "number the cut was sized against is no longer anywhere in the record")
    assert "ceiling_notice" in kw, (
        "nothing announces the crossing; a budget nobody is told was crossed "
        "is a deleted number, not a record")


def test_a_reintroduced_clock_at_the_cut_launch_is_caught():
    """THE NEGATIVE CONTROL for the test above. A guard that cannot fail is
    not a guard, so put the defect back and require the finding."""
    src = (_PROGRAMS / "fault_atpg_run.py").read_text()
    mutated = src.replace(
        "    ec, out, err = _run_docker(project, cut_cmd, pdk_dir=pdk_dir,\n"
        "                               supervised=True, "
        "ceiling_s=ATPG_CUT_BUDGET_S,",
        "    ec, out, err = _run_docker(project, cut_cmd, pdk_dir=pdk_dir,\n"
        "                               timeout=120, "
        "ceiling_s=ATPG_CUT_BUDGET_S,",
        1)
    assert mutated != src, "the mutation did not apply — update this control"
    kw = {k.arg for k in _cut_launch_call(mutated).keywords}
    assert "timeout" in kw, (
        "the mutated tree does not show the clock this test claims to detect")


def test_the_declared_budget_is_the_same_number_it_always_was():
    """A fix that quietly RAISED the constant would be the same defect with a
    later date, and would pass every other test in this file."""
    assert F.ATPG_CUT_BUDGET_S == 120


# ── 2. EXECUTED: the kwargs the cut call actually carried, at run time ──────
def _min_project(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "phase2/stage2/synth").mkdir(parents=True)
    (tmp_path / "phase2/stage2/dft").mkdir(parents=True)
    (tmp_path / "phase2/stage2/synth/netlist.v").write_text(
        "module top(input clk, input d, output q);\n"
        "  MYLIB_DFF u0(.CLK(clk), .D(d), .Q(q));\nendmodule\n")
    (tmp_path / "phase2/stage2/dft/cut_netlist.v").write_text(
        "module top(clk, d, q, \\u0 , \\u0.d );\n"
        "  input clk;\n  input d;\n  output q;\n"
        "  input \\u0 ;\n  output \\u0.d ;\nendmodule\n")
    return tmp_path


def _drive_run_fault(monkeypatch, tmp_path, cut_rc=0, cut_err=""):
    project = _min_project(tmp_path)
    calls = []

    def _fake(project_, cmd, **kw):
        joined = " ".join(cmd)
        calls.append((joined, kw))
        if joined.startswith("fault cut"):
            return cut_rc, "cut ok", cut_err
        if joined.startswith("cat "):
            return 1, "", ""
        return 0, "", ""

    monkeypatch.setattr(F, "_run_docker", _fake)
    _ec, report = F.run_fault(project, "phase2/stage2/synth/netlist.v",
                              clock="clk", pdk="__none__", min_coverage=95.0,
                              tv_count=4, cell_model_override="/work/cells.v",
                              dff_cells_override="MYLIB_DFF",
                              run_transition=False)
    return report, calls


def test_the_cut_launch_asked_for_supervision_and_a_recorded_ceiling(
        monkeypatch, tmp_path):
    """EXECUTED, not read off the source: the kwargs the cut call carried."""
    _report, calls = _drive_run_fault(monkeypatch, tmp_path)
    cut = [kw for joined, kw in calls if joined.startswith("fault cut")]
    assert len(cut) == 1, calls
    kw = cut[0]
    assert kw.get("supervised") is True
    assert kw.get("ceiling_s") == F.ATPG_CUT_BUDGET_S
    assert callable(kw.get("ceiling_notice"))
    assert "timeout" not in kw, f"a clock reached the cut launch: {kw}"


def test_a_reaped_cut_is_recorded_as_a_stall_not_as_a_cut_failure(
        monkeypatch, tmp_path):
    """THE RECORD. `fault cut` failing and `fault cut` being reaped for going
    still are different findings about different things, and a consumer that
    sees only `exit` collapses them into "the tool cannot cut this design"."""
    report, _calls = _drive_run_fault(
        monkeypatch, tmp_path, cut_rc=W.RC_STALLED,
        cut_err="\nWATCHDOG_STALLED: configured forward-progress signals did "
                "not advance for > 1800s")
    assert report["stage"] == "cut"
    assert report["stopped_as"] == "STALLED"
    assert report["cut_wall_budget_s"] == F.ATPG_CUT_BUDGET_S
    assert "RECORDED ceiling" in report["cut_wall_budget_role"]
    assert report["cut_wall_budget_crossed"] is False, (
        "nothing crossed the budget in this run, and the record says it did")


def test_an_ordinary_cut_failure_is_NOT_relabelled_a_stall(
        monkeypatch, tmp_path):
    """THE OTHER DIRECTION, and the one a fix like this gets wrong: a cut that
    the tool itself failed must keep saying so. `stopped_as` is None on every
    path but the reap."""
    report, _calls = _drive_run_fault(monkeypatch, tmp_path, cut_rc=1)
    assert report["stage"] == "cut" and report["exit"] == 1
    assert report["stopped_as"] is None, (
        "a tool error was relabelled as a stall — the supervisor stopped "
        "nothing in this run")


def test_the_success_path_records_the_cut_budget_too(monkeypatch, tmp_path):
    """A budget that is only mentioned when the step FAILED cannot tell the
    next run how close it came. Both facts ride the ordinary report."""
    report, _calls = _drive_run_fault(monkeypatch, tmp_path)
    assert report.get("stage") != "cut", report          # the cut succeeded
    assert report["cut_wall_budget_s"] == F.ATPG_CUT_BUDGET_S
    assert report["cut_wall_budget_crossed"] is False
    assert report["cut_wall_budget_crossed_at_s"] is None


# ── 3. BOTH DIRECTIONS, EXECUTED ON REAL HOST PROCESSES ─────────────────────
# Driven through the LOCAL route so these run anywhere (in-image included)
# without a docker daemon; the supervision, the ceiling record and the reap are
# the same objects the container route uses.
def _force_local_route(monkeypatch):
    monkeypatch.setattr(F._CE, "no_container_route", lambda: True)
    monkeypatch.setattr(F, "_announce_local_atpg_route", lambda *_a, **_k: None)
    monkeypatch.setattr(F, "_localise_mounted_paths",
                        lambda inner, *_a, **_k: inner)
    monkeypatch.setattr(F, "ENV_PREAMBLE", "")


def test_a_cut_that_outlives_its_budget_still_delivers_its_netlist(
        monkeypatch, tmp_path):
    """DIRECTION ONE. The subject runs several times its declared budget, is
    not touched, produces its product, and the crossing is RECORDED once."""
    _force_local_route(monkeypatch)
    crossed = []
    out_file = tmp_path / "cut_netlist.v"
    job = ("python3 -c \"import sys,time\n"
           "for i in range(15): sys.stdout.write('flop %d\\n' % i); "
           "sys.stdout.flush(); time.sleep(0.2)\n"
           f"open({str(out_file)!r},'w').write('module top; endmodule\\n')\n"
           "sys.stdout.write('CUT_DONE\\n')\"")
    rc, out, _err = F._run_docker(
        tmp_path, [job], supervised=True, ceiling_s=1.0,
        ceiling_notice=lambda e: crossed.append(round(float(e), 2)),
        stall_grace_s=4.0)
    assert rc == 0, out
    assert "CUT_DONE" in out, "the cut was stopped before it finished"
    assert out_file.exists(), "the cut netlist was never written"
    assert crossed and crossed[0] >= 1.0, crossed
    assert len(crossed) == 1, f"the crossing must be announced ONCE: {crossed}"


def test_a_cut_that_stops_making_progress_is_still_reaped(
        monkeypatch, tmp_path):
    """DIRECTION TWO. Progress supervision is not "never stop anything". A cut
    that goes completely still IS stopped, under the supervisor's own rc, with
    the evidence on the record — otherwise this change would be an unbounded
    run wearing a supervisor's name."""
    _force_local_route(monkeypatch)
    job = "echo cutting; kill -STOP $$; sleep 600"
    rc, _out, err = F._run_docker(tmp_path, [job], supervised=True,
                                  ceiling_s=600.0, stall_grace_s=3.0)
    assert rc == W.RC_STALLED, f"rc={rc}: a stopped cut was not reaped"
    assert "WATCHDOG_STALLED" in err, err
    assert "since_last_progress_s" in err, (
        "the reap does not say what it saw — a stop that cannot answer 'for "
        "how long did nothing move' is an assertion, not a measurement")
