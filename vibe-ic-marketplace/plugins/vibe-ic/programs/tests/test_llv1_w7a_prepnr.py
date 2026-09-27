"""llv1 W7a: pre-PnR preparation pulled out of `step_pnr`.

The extraction is a refactor for the default flow and a hook for the flag:
the default flow must stay byte-identical, and the between-segments step must
run the SAME code the default flow runs. Contracts:
  1. `step_pad_assignment` runs `pad_assignment_gen` exactly as the first
     program of `step_pad_ring_gen` (same program, same PDK arguments, the
     same rc reading), and `step_pad_ring_gen` still runs its four programs
     in order through the same `_run_pad_program`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))


def _r():
    import phase3_one_shot_runner as r
    return r


@pytest.fixture
def calls(monkeypatch):
    """Capture the real container command each pad program is launched with."""
    r = _r()
    seen = []
    rcs = {}
    monkeypatch.setattr(r, "_padring_pdk_root_and_tree",
                        lambda pdk, container: ("/pdkroot", "/pdkroot/tree"))
    monkeypatch.setattr(r, "_to_container_path", lambda path, c: str(path))

    def _exec(container, cmd, marker=None):
        name = Path(marker).name
        seen.append((name, cmd))
        return rcs.get(name, 0), "", ""

    monkeypatch.setattr(r, "_docker_exec", _exec)
    return SimpleNamespace(seen=seen, rc=rcs)


def test_pad_assignment_runs_what_the_ring_step_runs_first(tmp_path, calls):
    r = _r()
    pdk = SimpleNamespace(tech_lef=None)
    r.step_pad_ring_gen(tmp_path, container="c", pdk=pdk)
    ring_first = calls.seen[0]
    assert [n for n, _ in calls.seen] == [
        "pad_assignment_gen.py", "pad_ring_gen.py", "pad_ring_check.py"]
    calls.seen.clear()
    r.step_pad_assignment(tmp_path, container="c", pdk=pdk)
    assert calls.seen == [ring_first]            # the identical command line
    assert ring_first[1].endswith(
        f"{tmp_path} --pdk-root /pdkroot --pdk /pdkroot/tree")


@pytest.mark.parametrize("rc,status,reason", [
    (0, "PASS", ""), (1, "FAIL", ""), (2, "NOT_MEASURED", "not_executed"),
    (127, "NOT_MEASURED", "tool_absent")])
def test_pad_assignment_reads_rc_like_the_ring_step(tmp_path, calls, rc,
                                                    status, reason):
    r = _r()
    calls.rc["pad_assignment_gen.py"] = rc
    rep = tmp_path / "reports" / "phase3" / "pad_assignment.json"
    rep.parent.mkdir(parents=True)
    rep.write_text("{}")
    got = r.step_pad_assignment(tmp_path, container="c", pdk=None)
    assert (got.status, got.reason_class) == (status, reason)
    if rc == 1:
        # (For rc 2 and others the ring step itself raises: it computes a
        # reason class and never passes it to StepResult -- a pre-existing
        # defect of the default flow, left as it is by this refactor.)
        ring = r.step_pad_ring_gen(tmp_path, container="c", pdk=None)
        assert ring.status == status


def test_pad_assignment_rc0_without_its_report_is_a_fail(tmp_path, calls):
    got = _r().step_pad_assignment(tmp_path, container="c", pdk=None)
    assert got.status == "FAIL" and "is absent" in got.detail


# ── 4/4: the between-segments step ──────────────────────────────────────────

import ast  # noqa: E402

RUNNER = PROGRAMS / "phase3_one_shot_runner.py"


def _fn_src(name):
    src = RUNNER.read_text()
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, node)


@pytest.fixture
def staged(monkeypatch, tmp_path):
    """step_prepnr with each piece it calls recorded, in order."""
    r = _r()
    order = []
    nl = tmp_path / "phase2" / "stage2" / "synth" / "top_synth.v"
    nl.parent.mkdir(parents=True)
    nl.write_text("module top(); endmodule\n")
    monkeypatch.setattr(r, "pnr_input_netlist", lambda p, t: (nl, "n", False))

    def _padring(project, container, pdk, supply_plan=None):
        order.append("chip_top")
        return r.StepResult("io_pad_chip_top_gen", "PASS", 0.0, "ok")

    def _assign(project, container, pdk):
        order.append("pad_assignment")
        return r.StepResult("pad_assignment", state.assign, 0.0, "a")

    def _floorplan(project, top, pdk, container, die_um, util, netlist, t0):
        order.append("floorplan")
        if state.fp_fail:
            return r.StepResult("pnr", "FAIL", 0.0, "PDN_CORE_TOO_SMALL: x")
        return r.PrePnrDieCore(
            die_um="100x100", die_w=100, die_h=100, core_pad=10, core_w=80,
            core_h=80, fp_rect=None, util=0.4, auto_die_requested=True,
            l9_die_note="", ring_floor_pad=10, ring_inset=None,
            ring_pinned_die=False, seal_rec={}, slot=None,
            strap_floor_detail={}, strap_floor_um=None, ct03_pin_rect=None)

    state = SimpleNamespace(assign="PASS", fp_fail=False, ring=True)
    monkeypatch.setattr(r, "_padring_producer_dispatch", _padring)
    monkeypatch.setattr(r, "step_pad_assignment", _assign)
    monkeypatch.setattr(r, "_prepnr_floorplan", _floorplan)
    monkeypatch.setattr(r, "_chip_path_requests_pad_ring",
                        lambda p: state.ring)
    return SimpleNamespace(r=r, order=order, state=state, project=tmp_path)


def _run(st):
    return st.r.step_prepnr(st.project, "top", SimpleNamespace(), "c",
                            "auto", 0.4)


def test_prepnr_runs_the_pieces_in_step_pnrs_order(staged):
    res = _run(staged)
    assert staged.order == ["chip_top", "pad_assignment", "floorplan"]
    assert res.status == "NOT_MEASURED"                  # no step-7 SDC yet
    assert "SDC_SEAM_PENDING" in res.detail
    assert res.extras["floorplan"]["die_w"] == 100
    # it AUTHORS no SDC: nothing named constraint.sdc appears anywhere
    assert not list(staged.project.rglob("constraint.sdc"))


def test_prepnr_reads_step_7s_sdc(staged):
    sdc = staged.project / "phase2" / "stage2" / "constraints" / "top.sdc"
    sdc.parent.mkdir(parents=True)
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    res = _run(staged)
    assert res.status == "PASS"
    assert res.extras["sdc"]["path"] == str(sdc)
    assert sdc.read_text() == "create_clock -period 10 [get_ports clk]\n"


def test_prepnr_refusals_stop_it(staged):
    staged.state.assign = "FAIL"
    assert _run(staged).status == "FAIL"
    assert staged.order == ["chip_top", "pad_assignment"]
    staged.order.clear()
    staged.state.assign, staged.state.fp_fail = "PASS", True
    res = _run(staged)
    assert res.status == "FAIL" and "PDN_CORE_TOO_SMALL" in res.detail
    staged.order.clear()
    staged.state.fp_fail, staged.state.ring = False, False
    _run(staged)
    assert staged.order == ["chip_top", "floorplan"]     # no ring, no assignment


def test_the_default_flow_and_the_step_run_the_same_code():
    pnr, prep = _fn_src("step_pnr"), _fn_src("step_prepnr")
    for call in ("_padring_producer_dispatch(", "_prepnr_floorplan("):
        assert pnr.count(call) == 1 and prep.count(call) == 1, call
    assert "constraint.sdc" not in prep
    assert "_floorplan_rectangles_record(" in _fn_src("_prepnr_floorplan")
    assert "_prepnr_geometry(" in _fn_src("_prepnr_floorplan")
    assert "_floorplan_rectangles_record(" not in pnr.split(
        "_prep_dc = _prepnr_floorplan(")[0]


def test_the_window_plan_maps_the_prep_to_the_pnr_site():
    import step_preflight as spf
    r = _r()
    sites = dict(spf.RUNNER_PLANS["phase3_one_shot_runner"].sites)
    assert sites[r.PREPNR_DISPATCH_SITE][0] == r.PREPNR_CANONICAL_HEAD
    steps = spf.enterable_steps("phase3_one_shot_runner")
    assert r.PREPNR_CANONICAL_HEAD in steps
    assert "prepnr" not in steps          # not a canonical step: refused by name
