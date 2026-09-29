"""R-0929-TAIL-CONTINUES — a measured sign-off FAIL does not stop stream-out.

MEASURED (IC_BLOCKER_AUDIT 2026-09-29, spm #2; subservient IC run
`subic_ic_20260928` on 8hd-3, read-only): `_chain_ok` in `main()` was
`row.status == "PASS"` for every Step-32 producer, and a pre-stream gate FAIL
set it False. On subservient the pre-stream gate measured FAIL (sta_signoff,
sta_corner, sta_record, em_authority, antenna), and then gds, drc, lvs and
canonicalize_artefacts all read NOT_MEASURED "sign-off skipped: pre-stream gate
failed". On spm a measured Step-32 DRV FAIL did the same. So no run has measured
the tail (GDS, DRC, LVS, fill, precheck, handoff), and the tail's own blockers
stay hidden. The flow also declared 32 `blocks_on [23..30]`, the reverse of the
runner's order, so the audit could never attribute a gap to 32.

The rule now:
  * a measured FAIL at Step 32 or at the pre-stream gate is RECORDED and keeps
    the run FAIL (rc != 0);
  * stream-out and the tail still run on the shipped route;
  * only a missing/unusable route stops them, NOT_MEASURED naming the upstream;
  * a stream after a non-PASS pre-stream gate is measurement-only: never
    admitted, never packaged, and its release receipt is not eligible.

chip-, PDK- and vendor-AGNOSTIC: synthetic fixtures; only EDA writes are faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import phase3_one_shot_runner as R
from test_phase3_postpnr_disclosure_and_gds_guard import (
    OLD_DIE, OLD_UTIL, NEW_DIE, NEW_UTIL, TOP, _REPAIR_PRODUCERS, _drive,
    _plan, _project, _repair_producer,
)

FLOW = Path(R.__file__).resolve().parent.parent / "flow" / "phase1_phase2_phase3.yaml"


def _stage_fill(monkeypatch, project, d):
    """The post-stream canonicalize writes (fill DEF + canonical GDS copy) the
    layout freeze reads -- the only EDA writes faked here."""
    pnr = R._pl.pnr_dir(project)

    def canonicalize(proj, top, pdk, container, **kw):
        if kw.get("prepv"):
            (pnr / "filled.def").write_text("post-fill\n")
            out = R._pl.gds_dir(proj) / f"{top}.gds"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes((pnr / f"{top}.gds").read_bytes())
        d.called.append("canonicalize_artefacts")
        return R.StepResult("canonicalize_artefacts", "PASS", 0.0, "staged")

    monkeypatch.setattr(R, "step_canonicalize_artefacts", canonicalize)


# ── Step 32 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("producer", sorted(_REPAIR_PRODUCERS))
def test_step32_fail_streams_and_measures_the_tail(tmp_path, monkeypatch,
                                                   producer):
    """RED on main: `gds` is never called and drc/lvs read 'sign-off skipped'."""
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    d = _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    _stage_fill(monkeypatch, project, d)
    row = _repair_producer(monkeypatch, project, producer, "FAIL",
                           "residual DRV 9 after repair (measured)")
    rc = R.main()
    plan = _plan(project)
    assert plan[row]["status"] == "FAIL"
    assert "gds" in d.called, plan.get("gds")
    assert plan["gds"]["status"] == "PASS"
    for step in ("drc", "lvs"):
        assert step in d.called, f"{step} was not measured: {plan.get(step)}"
        assert "sign-off skipped" not in plan[step]["detail"]
    assert rc != 0, "a Step-32 FAIL must keep the run FAIL"


@pytest.mark.parametrize("producer", sorted(_REPAIR_PRODUCERS))
def test_step32_that_left_no_route_stops_the_tail_and_names_32(
        tmp_path, monkeypatch, producer):
    """The stopping direction the ruling keeps: nothing to stream."""
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    d = _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    fn, row, _switch = _REPAIR_PRODUCERS[producer]
    _repair_producer(monkeypatch, project, producer, "FAIL", "died")

    def died(*_a, **_k):
        (R._pl.pnr_dir(project) / "routed.def").unlink()
        return R.StepResult(row, "FAIL", 0.0, "repair died mid-write")

    monkeypatch.setattr(R, fn, died)
    rc = R.main()
    plan = _plan(project)
    assert plan[row]["status"] == "FAIL"
    assert "gds" not in d.called
    assert plan["gds"]["status"] == "NOT_MEASURED"
    assert "step 32" in plan["gds"]["detail"]
    assert "routed.def" in plan["gds"]["detail"]
    assert rc != 0


# ── the pre-stream gate ───────────────────────────────────────────────────

def _measured_gate(verdict: str, *, failed=("sta_corner",), unmeasured=()):
    def gate(proj, top, pdk, container):
        basis, error = R._layout_basis(proj, top, pdk, container)
        assert not error, error
        rows = ([{"name": g, "status": "FAIL"} for g in failed]
                + [{"name": g, "status": "NOT_MEASURED"} for g in unmeasured]
                + [{"name": "drv_signoff", "status": "PASS"}])
        out = R._pl.reports_phase3_dir(proj) / "prestream_gate.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "layout_digest": basis, "verdict": verdict,
            "failed_gates": list(failed), "unmeasured_gates": list(unmeasured),
            "gates": rows}) + "\n")
        return R.StepResult("prestream_gate", verdict, 0.0,
                            f"failed gates: {', '.join(failed) or 'none'}",
                            [str(out)], reason_class=(
                                "partial_population" if verdict == "NOT_MEASURED"
                                else ""), extras={
                                "layout_digest": basis,
                                "failed_gates": list(failed),
                                "unmeasured_gates": list(unmeasured)})
    return gate


def _prestream_run(tmp_path, monkeypatch, gate):
    project = _project(tmp_path, cached_die=OLD_DIE, cached_util=OLD_UTIL)
    d = _drive(monkeypatch, project, die=NEW_DIE, util=NEW_UTIL)
    _stage_fill(monkeypatch, project, d)
    monkeypatch.setattr(R, "step_prestream_gate", gate)
    rc = R.main()
    return project, d, _plan(project), rc


def test_a_measured_prestream_fail_streams_for_measurement_only(
        tmp_path, monkeypatch):
    """RED on main: gds/drc/lvs read 'pre-stream gate failed (sta_corner)'."""
    project, d, plan, rc = _prestream_run(tmp_path, monkeypatch,
                                          _measured_gate("FAIL"))
    assert plan["prestream_gate"]["status"] == "FAIL"
    assert "gds" in d.called, plan.get("gds")
    assert plan["gds"]["status"] == "PASS"
    for step in ("drc", "lvs"):
        assert step in d.called, plan.get(step)
        assert "sign-off skipped" not in plan[step]["detail"]
    gds = R._pl.pnr_dir(project) / f"{TOP}.gds"
    assert gds.is_file(), "the measured stream must stay where the tail reads it"
    # never admitted, never packaged
    assert not R._ga.admitted_gds(project, gds)
    rec = json.loads(R._ga.measurement_path(project).read_text())
    assert rec["admitted"] is False and rec["prestream_verdict"] == "FAIL"
    assert rec["failed_gates"] == ["sta_corner"]
    assert rec["gds_sha256"] == R._sha256_file(gds)
    assert R._ga.measurement_stream_current(project)
    receipt = json.loads((project / "reports/phase3/layout_receipts.json").read_text())
    assert receipt["release_scope"] == "MEASUREMENT_ONLY"
    assert receipt["release_verdict"] == "NOT_ELIGIBLE_FOR_RELEASE"
    assert "sta_corner" in receipt["upstream_failed_gate"]
    assert not list(R._pl.foundry_handoff_dir(project).glob("*.gds"))
    assert rc != 0, "the pre-stream FAIL must keep the run FAIL"


def test_an_unusable_route_in_the_prestream_receipt_still_stops_stream_out(
        tmp_path, monkeypatch):
    """Control: the route itself unmeasurable -> nothing is streamed."""
    _project_, d, plan, rc = _prestream_run(
        tmp_path, monkeypatch,
        _measured_gate("NOT_MEASURED", failed=(), unmeasured=("route_completion",)))
    assert "gds" not in d.called
    assert plan["gds"]["status"] == "NOT_MEASURED"
    assert "route_completion" in plan["gds"]["detail"]
    assert rc != 0


def test_a_passing_gate_is_still_admitted(tmp_path, monkeypatch):
    project, d, plan, _rc = _prestream_run(tmp_path, monkeypatch,
                                           _measured_gate("PASS", failed=()))
    gds = R._pl.pnr_dir(project) / f"{TOP}.gds"
    assert "gds" in d.called
    assert R._ga.admitted_gds(project, gds)
    assert not R._ga.measurement_stream_current(project)


# ── the admission predicate ───────────────────────────────────────────────

def _gate_record(project: Path, **doc) -> None:
    out = R._pl.reports_phase3_dir(project) / "prestream_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc))


@pytest.mark.parametrize("doc, digest, streams", [
    ({"verdict": "PASS", "layout_digest": "a"}, "a", True),
    ({"verdict": "FAIL", "layout_digest": "a", "gates": [{"name": "x"}],
      "unmeasured_gates": ["si"]}, "a", True),
    ({"verdict": "NOT_MEASURED", "layout_digest": "a", "gates": [{"name": "x"}],
      "unmeasured_gates": ["si_mcf"]}, "a", True),
    # the receipt must bind THIS layout
    ({"verdict": "FAIL", "layout_digest": "b", "gates": [{"name": "x"}]}, "a", False),
    ({"verdict": "PASS", "layout_digest": "a"}, "", False),
    # a stub receipt measured nothing
    ({"verdict": "FAIL", "layout_digest": "a"}, "a", False),
    ({"verdict": "FAIL", "layout_digest": "a", "gates": []}, "a", False),
    # the route itself absent / unidentified
    ({"verdict": "NOT_MEASURED", "layout_digest": "a", "gates": [{"name": "x"}],
      "unmeasured_gates": ["layout_basis"]}, "a", False),
    ({"verdict": "NOT_MEASURED", "layout_digest": "a", "gates": [{"name": "x"}],
      "unmeasured_gates": ["route_evidence"]}, "a", False),
    ({"verdict": "WAIVED", "layout_digest": "a", "gates": [{"name": "x"}]}, "a", False),
])
def test_stream_refusal(tmp_path, doc, digest, streams):
    _gate_record(tmp_path, **doc)
    assert (R._ga.stream_refusal(tmp_path, digest) == "") is streams


# ── the declared order matches the runner's data flow ─────────────────────

def _ancestors(sid, bo):
    out, stack = set(), [sid]
    while stack:
        for p in bo.get(stack.pop(), []):
            if p not in out:
                out.add(p)
                stack.append(p)
    return out


def test_signoff_steps_are_downstream_of_step32():
    """RED on main: 32 blocks_on [23, 24, 25, 26, 27, 29, 30]."""
    doc = yaml.safe_load(FLOW.read_text())
    bo = {str(s["id"]): [str(p) for p in (s.get("blocks_on") or [])]
          for s in doc["steps"] if isinstance(s, dict) and "id" in s}
    for sid in ("23", "24", "25", "26", "27", "28", "29", "30"):
        assert "32" in _ancestors(sid, bo), f"step {sid} is not downstream of 32"
        assert sid not in _ancestors("32", bo), f"32 still waits on {sid}"
    # 32 repairs the route using the post-PnR extraction
    assert {"21", "22"} <= _ancestors("32", bo)
