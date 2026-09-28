"""llv1 W5: the two-segment LibreLane driver (`librelane_whole_flow`).

The only substitution is the LibreLane CLI (a `docker run`): the fake below
writes what a real run writes -- `runs/<tag>/NN-step/state_out.json`, a
`flow.log` whose `Running '<id>' at '<path relative to the CONTAINER cwd>'`
lines include a composite step's sub-steps, an `error.log`, and the exit code.
Those shapes are copied from real 0.3.83 runs (LLV1_W5_spike.md). The fake
honours the real argv: `--run-tag`, `--to`, `--from`, `--with-initial-state`.

Contracts:
  1. Every invocation carries the memory ceiling, a deadline, `--network none`,
     the explicit PDK/cell library, and the project as the design directory.
  2. The step list is read from the run's own log (not ordinal folders), and a
     composite step's sub-steps are not flow steps.
  3. A run that completed every planned step with only deferred checker
     findings returns them as the tool's verdict; an aborted run, an
     undeferred error or an unexpected step list is a named FAIL that points
     at the tool's report and names the next action.
  4. The handoff: netlist ++ wrapper byte for byte, segment 1's metrics
     carried, every input bound by sha256.
  5. Netlist identity: PASS only when step 13's netlist is the one segment 2
     consumed; a stale segment-1 netlist, an edited layout netlist or a
     different proven netlist is FAIL (plan A4).
  6. Output-only pad masters are ignored for the disconnected-pin checker,
     from the producer's record; a master that also serves another direction
     refuses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import librelane_whole_flow as W  # noqa: E402
import librelane_contract as LC  # noqa: E402

FLOW = ["Lint", W.JSON_HEADER_STEP, "Synthesis", W.SEGMENT1_LAST, W.SEGMENT2_FIRST,
        "STAPrePNR", "Floorplan", "RepairAntennas", "StreamOut"]
SEG1 = FLOW[:4]
SEG2 = FLOW[4:]


class FakeLibreLane:
    def __init__(self, *, rc=0, errors=(), abort_at=None, skip=()):
        self.rc, self.errors, self.abort_at, self.skip = rc, list(errors), abort_at, set(skip)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
        if "-c" in argv and argv[argv.index("-c") + 1] == W._PLANNED_STEPS_SCRIPT:
            first, last = argv[-2], argv[-1]
            rows = [{"id": s, "gated_off_by": ["RUN_X"] if s in self.skip else []}
                    for s in FLOW[FLOW.index(first):FLOW.index(last) + 1]]
            return type("R", (), {"returncode": 0, "stdout": json.dumps(rows), "stderr": ""})
        if opt("--only"):
            argv = [x for x in argv]
            i = argv.index("--only")
            argv[i:i + 2] = ["--from", opt("--only"), "--to", opt("--only")]
            opt = lambda k: argv[argv.index(k) + 1] if k in argv else None
        design_dir, tag = Path(opt("--design-dir")), opt("--run-tag")
        run = design_dir / "runs" / tag
        run.mkdir(parents=True, exist_ok=True)
        first = FLOW.index(opt("--from")) if opt("--from") else 0
        last = FLOW.index(opt("--to")) if opt("--to") else len(FLOW) - 1
        state = {"metrics": {}}
        if opt("--with-initial-state"):
            state = json.loads(Path(opt("--with-initial-state")).read_text())
        log = []
        n = 0
        for step in FLOW[first:last + 1]:
            if step in self.skip:
                continue
            n += 1
            folder = run / f"{n:02d}-{step.lower()}"
            folder.mkdir()
            # The real log's paths are relative to the container's cwd.
            log.append(f"Running '{step}' at '../../x/y/runs/{tag}/{folder.name}'…")
            if step == "RepairAntennas":
                log.append(f"Running 'Sub' at '../../x/y/runs/{tag}/{folder.name}/1-sub'…")
            if step == self.abort_at:
                (run / "flow.log").write_text("\n".join(log) + "\n")
                (run / "error.log").write_text(f"{step} failed: segfault\n")
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"})
            state = dict(state, metrics=dict(state.get("metrics") or {}, **{f"m_{step}": n}))
            if step == "Synthesis":
                nl = folder / "core.nl.v"
                nl.write_text("module core(); endmodule\n")
                state["nl"] = str(nl)
            if step == W.JSON_HEADER_STEP:
                jh = folder / "core.h.json"
                jh.write_text("{}")
                state["json_h"] = str(jh)
            (folder / "state_out.json").write_text(json.dumps(state))
        (run / "flow.log").write_text("\n".join(log) + "\n")
        (run / "error.log").write_text("".join(e + "\n" for e in self.errors))
        return type("R", (), {"returncode": self.rc, "stdout": "ok", "stderr": ""})


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    (p / "cfg.json").write_text("{}")
    return p


def _seg(project, fake, monkeypatch, name, extra, expected):
    monkeypatch.setattr(W.subprocess, "run", fake)
    return W.run_segment(project, "img", project / "cfg.json", name=name, flow="F",
                         pdk="processA", pdk_root=project, scl="libA", extra=extra,
                         expected=expected, deadline_s=77)


def test_the_invocation_shape(project, monkeypatch):
    fake = FakeLibreLane()
    _seg(project, fake, monkeypatch, "seg1", ["--to", W.SEGMENT1_LAST], SEG1)
    argv, kw = fake.calls[0]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[3:3 + len(W._dmem.docker_memory_flags())] == W._dmem.docker_memory_flags()
    assert "--memory" in argv or W._dmem.memory_limit() is None
    assert ["--network", "none"] == argv[argv.index("--network"):argv.index("--network") + 2]
    for flag, value in (("--pdk", "processA"), ("--scl", "libA"), ("--flow", "F"),
                        ("--design-dir", str(project.resolve())), ("--run-tag", "seg1")):
        assert argv[argv.index(flag) + 1] == value
    assert "--manual-pdk" in argv and kw["timeout"] == 77
    rec = json.loads((W.whole_dir(project) / "seg1.invocation.json").read_text())
    assert rec["argv"] == argv and rec["config_sha256"] == LC.digest(project / "cfg.json")


def test_steps_come_from_the_runs_own_log_not_its_sub_steps(project, monkeypatch):
    out = _seg(project, FakeLibreLane(), monkeypatch, "seg2",
               ["--from", W.SEGMENT2_FIRST], SEG2)
    assert [s for s, _ in out["steps"]] == SEG2          # 'Sub' is not a step
    assert all(p.is_dir() and p.parent == out["run_dir"] for _, p in out["steps"])
    assert out["tool_verdict"] == "CLEAN" and out["tool_findings"] == []


def test_a_completed_run_with_deferred_findings_returns_them(project, monkeypatch):
    fake = FakeLibreLane(rc=2, errors=["6 Magic DRC errors found. - deferred"])
    out = _seg(project, fake, monkeypatch, "seg2", ["--from", W.SEGMENT2_FIRST], SEG2)
    assert out["tool_verdict"] == "FINDINGS" and out["tool_rc"] == 2
    assert out["tool_findings"] == ["6 Magic DRC errors found. - deferred"]


@pytest.mark.parametrize("fake,why", [
    (lambda: FakeLibreLane(abort_at="Floorplan"), "aborted"),
    (lambda: FakeLibreLane(rc=2, errors=["Floorplan crashed"]), "undeferred"),
    (lambda: FakeLibreLane(rc=2, errors=["x - deferred"], skip={"StreamOut"}), "short"),
])
def test_anything_but_a_completed_run_is_a_named_fail(project, monkeypatch, fake, why):
    with pytest.raises(LC.Refusal) as exc:
        _seg(project, fake(), monkeypatch, "seg2", ["--from", W.SEGMENT2_FIRST], SEG2)
    assert exc.value.code == "LL_SEGMENT_FAILED", why
    text = str(exc.value)
    assert "error.log" in text and "Next action" in text and "default flow" in text


def test_a_clean_run_with_an_unexpected_step_list_refuses(project, monkeypatch):
    with pytest.raises(LC.Refusal) as exc:
        _seg(project, FakeLibreLane(skip={"STAPrePNR"}), monkeypatch, "seg2",
             ["--from", W.SEGMENT2_FIRST], SEG2)
    assert exc.value.code == "LL_SEGMENT_STEPS_UNEXPECTED"


def _two_segments(project, monkeypatch, wrapper_text="module chip(); core u(); endmodule\n"):
    s1 = _seg(project, FakeLibreLane(), monkeypatch, "seg1", ["--to", W.SEGMENT1_LAST], SEG1)
    st = json.loads(s1["state"].read_text())
    wrapper = project / "chip_top_io.v"
    wrapper.write_text(wrapper_text)
    rec = W.handoff(project, s1["state"], netlist=Path(st["nl"]), wrapper=wrapper,
                    json_header=Path(st["json_h"]), top="chip")
    s2 = _seg(project, FakeLibreLane(), monkeypatch, "seg2",
              ["--from", W.SEGMENT2_FIRST, "--with-initial-state", rec["state_in"]], SEG2)
    return s1, st, rec, s2


def test_the_handoff_binds_netlist_wrapper_and_metrics(project, monkeypatch):
    s1, st, rec, s2 = _two_segments(project, monkeypatch)
    layout = Path(rec["layout_netlist"]).read_bytes()
    assert layout == Path(st["nl"]).read_bytes() + (project / "chip_top_io.v").read_bytes()
    state_in = json.loads(Path(rec["state_in"]).read_text())
    assert state_in["nl"] == str(Path(rec["layout_netlist"]).resolve())
    assert state_in["metrics"] == st["metrics"]
    final = json.loads(s2["state"].read_text())["metrics"]
    assert set(st["metrics"]) <= set(final)                  # segment 1 survives
    for key in ("netlist", "wrapper", "layout_netlist", "json_header", "state_in"):
        assert rec[key + "_sha256"] == LC.digest(Path(rec[key]))


def test_netlist_identity_passes_only_on_the_consumed_netlist(project, monkeypatch):
    _, st, rec, _ = _two_segments(project, monkeypatch)
    assert W.netlist_identity(project, Path(st["nl"]))["verdict"] == "PASS"
    other = project / "other.v"
    other.write_text("module core(); wire x; endmodule\n")
    assert W.netlist_identity(project, other)["verdict"] == "FAIL"


def test_a_stale_segment1_netlist_fails_identity(project, monkeypatch):
    _, st, rec, _ = _two_segments(project, monkeypatch)
    Path(st["nl"]).write_text("module core(); wire changed; endmodule\n")   # re-synthesised later
    got = W.netlist_identity(project, Path(st["nl"]))
    assert got["verdict"] == "FAIL" and got["problems"]


def test_an_edited_layout_netlist_fails_identity(project, monkeypatch):
    _, st, rec, _ = _two_segments(project, monkeypatch)
    Path(rec["layout_netlist"]).write_text("module chip(); endmodule\n")
    assert W.netlist_identity(project, Path(st["nl"]))["verdict"] == "FAIL"


def test_no_handoff_is_not_measured(project):
    assert W.netlist_identity(project, project / "cfg.json")["verdict"] == "NOT_MEASURED"


def test_completed_segments_use_w6_once_and_in_order(project):
    seen = []
    def importer(proj, segments):
        seen.extend((proj, segments))
        return {"imported": True}
    got = W.import_completed_segments(project, project / "runs" / "segment1",
                                      project / "runs" / "segment2", importer=importer)
    assert got == {"imported": True}
    assert seen[0] == project
    assert seen[1] == [(project / "runs" / "segment1", W.SEGMENT1_LAST),
                       (project / "runs" / "segment2", None)]


def _pads(project, rows):
    path = project / W.CHIP_TOP_RECORD_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pad_instances": rows}))


def test_output_only_masters_are_ignored_from_the_record(project):
    _pads(project, {"u_a": {"port": "a", "master": "IN_M", "direction": "input"},
                    "u_p": {"port": "p", "master": "BI_M", "direction": "output"},
                    "u_q": {"port": "q", "master": "BI_M", "direction": "output"}})
    masters, source = W.ignore_disconnected_masters(project)
    assert masters == ["BI_M"] and "pad_instances" in source


def test_a_master_serving_two_directions_refuses(project):
    _pads(project, {"u_p": {"port": "p", "master": "BI_M", "direction": "output"},
                    "u_b": {"port": "b", "master": "BI_M", "direction": "inout"}})
    with pytest.raises(LC.Refusal) as exc:
        W.ignore_disconnected_masters(project)
    assert exc.value.code == "LL_DISCONNECTED_MASTER_MIXED"


def test_no_record_ignores_nothing(project):
    assert W.ignore_disconnected_masters(project)[0] == []


# ── segment configs and the orchestration ───────────────────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_t95_mig_front import design, put  # noqa: E402
import _owner_declared as _OD  # noqa: E402
import _declared_die as DD  # noqa: E402
import _impl_flow as IF  # noqa: E402

FLOORPLAN = {"program": "phase3_one_shot_runner.step_prepnr", "die_rect_um": [0, 0, 3000, 3000],
             "die_source": "auto-die", "floorplan_rect_um": None,
             "floorplan_rect_is_the_die": True, "core_pad_um": 300}


def _chip(tmp_path):
    p = design(tmp_path, {})
    st = p / "input/submission_template"
    (st / "SELF_TAPEOUT.txt").write_text("# self\n")
    (st / "tapeout_declaration.json").write_text(json.dumps(_OD.attest(
        {"schema": "vibe-ic/tapeout_declaration/1",
         "answers": {"deliverable": "DIE", "top_cell": "core"}})))
    put(p / "phase2/stage1/rtl/core.v", "module core(input clk); endmodule\n")
    put(p / "phase3/stage3/pnr/chip_top_io.v", "module chip_top(); core u(); endmodule\n")
    put(p / W.CHIP_TOP_RECORD_REL, {
        "verdict": "WROTE", "chip_top_module": "chip_top", "core_module": "core",
        "chip_top_verilog": "phase3/stage3/pnr/chip_top_io.v",
        "pad_instances": {"u_p": {"port": "p", "master": "BI_M", "direction": "output"},
                          "u_c": {"port": "clk", "master": "IN_M", "direction": "input"}}})
    put(p / DD.FLOORPLAN_RECTANGLES_REL, FLOORPLAN)
    IF.write_record(p, "librelane", resolved_by="test")
    return p


def _macro(tmp_path):
    p = design(tmp_path, {})
    (p / "input/submission_template/tapeout_declaration.json").write_text(json.dumps(_OD.attest(
        {"schema": "vibe-ic/tapeout_declaration/1",
         "answers": {"deliverable": "HARDMACRO", "top_cell": "core"}})))
    put(p / "phase2/stage1/rtl/core.v", "module core(input clk); endmodule\n")
    put(p / DD.FLOORPLAN_RECTANGLES_REL, FLOORPLAN)
    IF.write_record(p, "librelane", resolved_by="test")
    return p


def test_segment2_config_on_the_chip_path(tmp_path):
    p = _chip(tmp_path)
    sdc = put(p / "phase2/stage2/constraints/core.sdc", "create_clock -period 10 clk\n")
    layout = put(p / "L.nl.v", "module chip_top(); endmodule\n")
    out = W.segment2_config(p, "processA", p / "seg2.json", layout_netlist=layout,
                            sdc=sdc, sdc_source="step 7's declared SDC")
    cfg = json.loads(out.read_text())
    src = json.loads(out.with_suffix(".provenance.json").read_text())
    assert cfg["DESIGN_NAME"] == "chip_top"                    # D7: the layout top
    assert cfg["VERILOG_FILES"] == [str(layout.resolve())]
    assert cfg["DIE_AREA"] == [0, 0, 3000, 3000]              # D1: the run's die
    assert cfg["PNR_SDC_FILE"] == cfg["SIGNOFF_SDC_FILE"] == str(sdc.resolve())
    assert cfg["IGNORE_DISCONNECTED_MODULES"] == ["BI_M"]
    assert "pad_instances" in src["IGNORE_DISCONNECTED_MODULES"]
    assert "handoff" in src["VERILOG_FILES"]


def test_segment2_config_on_a_hardmacro_takes_no_runner_die(tmp_path):
    p = _macro(tmp_path)
    layout = put(p / "L.nl.v", "module core(); endmodule\n")
    cfg = json.loads(W.segment2_config(p, "processA", p / "seg2.json", layout_netlist=layout,
                                       sdc=None, sdc_source="SDC_SEAM_PENDING").read_text())
    assert cfg["DESIGN_NAME"] == "core" and "DIE_AREA" not in cfg
    assert "PNR_SDC_FILE" not in cfg and "IGNORE_DISCONNECTED_MODULES" not in cfg


def test_an_absent_sdc_is_named_in_the_provenance(tmp_path):
    p = _macro(tmp_path)
    layout = put(p / "L.nl.v", "module core(); endmodule\n")
    out = W.segment2_config(p, "processA", p / "seg2.json", layout_netlist=layout,
                            sdc=None, sdc_source="SDC_SEAM_PENDING: step 7 absent")
    src = json.loads(out.with_suffix(".provenance.json").read_text())
    assert src["PNR_SDC_FILE"].startswith("ABSENT: SDC_SEAM_PENDING")


def _run_two(p, monkeypatch, wrapper: bool):
    fake = FakeLibreLane()
    monkeypatch.setattr(W.subprocess, "run", fake)
    seg1 = put(p / "seg1.json", "{}")

    def between(project, state):
        st = json.loads(Path(state).read_text())
        out = {"netlist": Path(st["nl"]), "top": "chip_top" if wrapper else "core",
               "sdc": None, "sdc_source": "SDC_SEAM_PENDING"}
        out["wrapper"] = (p / "phase3/stage3/pnr/chip_top_io.v") if wrapper else None
        return out

    summary = W.run_two_segments(p, "img", pdk="processA", pdk_root=p, scl="libA",
                                 segment1=seg1, between=between, segment2_kwargs={},
                                 first_step=FLOW[0], last_step=FLOW[-1], deadline_s=5,
                                 importer=lambda project, segments: {"segments": len(segments)})
    names = [a[a.index("--run-tag") + 1] for a, _ in fake.calls if "--run-tag" in a]
    return summary, names


def test_the_chip_path_runs_segment1_the_header_then_segment2(tmp_path, monkeypatch):
    p = _chip(tmp_path)
    summary, names = _run_two(p, monkeypatch, wrapper=True)
    assert names == ["segment1", "json_header", "segment2"]
    assert summary["segment1"]["steps"] == SEG1 and summary["segment2"]["steps"] == SEG2
    assert summary["netlist_identity"]["verdict"] == "PASS"
    rec = summary["handoff"]
    assert rec["wrapper"] and "json_header/" in rec["json_header"]
    on_disk = json.loads((W.whole_dir(p) / "whole_flow.json").read_text())
    assert on_disk["netlist_identity"]["verdict"] == "PASS"


def test_a_hardmacro_reuses_segment1s_header(tmp_path, monkeypatch):
    p = _macro(tmp_path)
    summary, names = _run_two(p, monkeypatch, wrapper=False)
    assert names == ["segment1", "segment2"]
    assert "segment1/" in summary["handoff"]["json_header"]
    assert summary["netlist_identity"]["verdict"] == "PASS"


def test_a_between_that_refuses_stops_the_run(tmp_path, monkeypatch):
    p = _macro(tmp_path)
    fake = FakeLibreLane()
    monkeypatch.setattr(W.subprocess, "run", fake)

    def between(project, state):
        raise LC.Refusal("LEC_NOT_PROVEN", "step 13 did not prove the netlist")

    with pytest.raises(LC.Refusal):
        W.run_two_segments(p, "img", pdk="processA", pdk_root=p, scl="libA",
                           segment1=put(p / "seg1.json", "{}"), between=between,
                           segment2_kwargs={}, first_step=FLOW[0], last_step=FLOW[-1])
    names = [a[a.index("--run-tag") + 1] for a, _ in fake.calls if "--run-tag" in a]
    assert names == ["segment1"]
