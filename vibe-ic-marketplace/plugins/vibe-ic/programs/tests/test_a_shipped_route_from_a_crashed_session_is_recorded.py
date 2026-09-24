"""A route the runner SHIPS from a session that exited non-zero is recorded in a
form step 21 accepts.

Review we8o6v9hg (sdrprov r2): once the main PnR session declares routed.def
(`_declared_session_exec`), a session that writes it and then exits non-zero
records that declaration under its RAW exit code -- 139 for a survivable SIGSEGV
in the best-effort `postroute_setup_repair_estimate` stage (step_pnr sets rc=0
and ships). `provenance_check` skips exit != 0 records, and the literal path in
that record suppresses canonicalize's openroad back-fill: step 21 FAILs where the
base run PASSed.

r3 records the SHIPPED products of such a session -- only bytes it itself
declared -- as a `record: artefact` row (`_declare_pnr_survivors` ->
`_log_surviving_artefact`): tool openroad, exit_code 0 (the code the runner
applied), the crash disclosed (raw rc, reason), a measurement derived from the
shipped DEF. Driven through the REAL step_pnr with a faked container that logs
each invocation exactly as `_docker_exec` does, then canonicalize, then the real
`provenance_check` exactly as step 21 runs it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import provenance_check as PC                                   # noqa: E402
import test_postlayout_lec_nameerror as LEC                     # noqa: E402
from test_pnr_tool_fatal_signal_and_checkpoint_resume import (  # noqa: E402
    _PG_OK, _SIGNOFF_SET, _build_project, _crash_log, _def_text, _sky130_pdk,
    mod as R)

TOP = "widget"
ROUTED = "phase3/stage3/pnr/routed.def"


def _drive(tmp_path, monkeypatch, *, stage, first_rc=139):
    project = _build_project(tmp_path, TOP)
    (project / "provenance.jsonl").write_text("")
    monkeypatch.setattr(R, "_PROV_SINK", project)
    monkeypatch.setattr(R, "_openroad_supports_postroute_spef_repair",
                        lambda *_a, **_k: True)
    calls = []

    def _openroad(container, cmd, timeout=None, **kw):
        if "openroad -no_init" not in cmd:
            return 0, "", ""
        out_dir = R._pl.pnr_dir(project)
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(cmd)
        if len(calls) == 1:          # the main session: writes, then crashes
            for pre in ("floorplan.def", "placed.def", "post_cts.def",
                        "post_hold.def", "routed_preantenna.def"):
                (out_dir / pre).write_text(_def_text(routed=True))
            for name in _SIGNOFF_SET:
                if name.endswith(".def"):
                    (out_dir / name).write_text(_def_text(routed=True))
                elif name.endswith(".v"):
                    (out_dir / name).write_text(f"module {TOP}(); endmodule\n")
                else:
                    (out_dir / name).write_text("worst slack 0.42\n")
            log = _crash_log(stage, signal_death=first_rc == 139) + _PG_OK
            (out_dir / "openroad.log").write_text(log)
            R._log_invocation(cmd, first_rc, 1, marker=kw.get("marker"),
                              container=None, outputs=kw.get("outputs"))
            return first_rc, log, ""
        R._log_invocation(cmd, 1, 1, marker=kw.get("marker"), container=None,
                          outputs=kw.get("outputs"))
        return 1, "", ""

    monkeypatch.setattr(R, "_docker_exec", _openroad)
    res = R.step_pnr(project, TOP, _sky130_pdk(), "iic", "200x200", 0.30)
    # canonicalize, as the runner does next (its openroad back-fill is the
    # base run's only record of routed.def)
    LEC._quiet_canonicalize(monkeypatch)
    monkeypatch.setattr(R, "_emit_lec_post_layout", lambda *a, **k: "SKIP")
    try:
        R.step_canonicalize_artefacts(project, TOP, _sky130_pdk(), "nocontainer")
    except Exception:                                         # noqa: BLE001
        pass
    return res, project


def _step21(project: Path):
    out = project / "pc.json"
    rc = PC.main([str(project), "--output", ROUTED, "--tool", "openroad",
                  "--json", str(out)])
    return rc, json.loads(out.read_text())["checks"][0]


def test_a_survivable_segfault_ships_a_route_step_21_accepts(tmp_path,
                                                            monkeypatch):
    res, project = _drive(tmp_path, monkeypatch,
                          stage="postroute_setup_repair_estimate")
    assert res.status == "PASS", (res.status, (res.detail or "")[:300])
    rc, check = _step21(project)
    assert (rc, check["status"]) == (0, "PASS"), check


def test_the_survivor_record_discloses_the_crash(tmp_path, monkeypatch):
    res, project = _drive(tmp_path, monkeypatch,
                          stage="postroute_setup_repair_estimate")
    rows = [json.loads(ln) for ln in
            (project / "provenance.jsonl").read_text().splitlines()
            if ln.strip()]
    surv = [r for r in rows if r.get("record") == "artefact"
            and ROUTED in (r.get("outputs") or {})]
    assert len(surv) == 1, rows
    s = surv[0]
    assert (s["tool"], s["exit_code"]) == ("openroad", 0), s
    assert s["survived"]["raw_exit_code"] == 139, s
    assert "postroute_setup_repair_estimate" in s["survived"]["reason"], s


def test_a_crash_that_is_not_survivable_leaves_step_21_red(tmp_path,
                                                           monkeypatch):
    """Negative control: the same crash in a LOAD-BEARING stage fails the step,
    ships nothing, and records no survivor."""
    monkeypatch.setattr(
        R, "_PNR_NONFATAL_STAGES",
        frozenset(s for s in R._PNR_NONFATAL_STAGES
                  if s != "postroute_setup_repair_estimate"))
    res, project = _drive(tmp_path, monkeypatch,
                          stage="postroute_setup_repair_estimate")
    assert res.status == "FAIL"
    rows = (project / "provenance.jsonl").read_text()
    assert '"record": "artefact"' not in rows or ROUTED not in rows.split(
        '"record": "artefact"', 1)[1].split("\n", 1)[0]
    rc, check = _step21(project)
    assert check["status"] != "PASS", check


def test_a_failed_antenna_rollback_ships_a_route_step_21_accepts(
        tmp_path, monkeypatch):
    """The review's second case, MEASURED through the real step_pnr: the
    refusing session writes its route and exits 0, the rollback tail FAILS
    (rc 1) -- its products were set aside, so its record attests nothing, and
    the refusing session's route ships under the refusing session's own exit-0
    record. (A refusing session that exits NON-zero makes step_pnr FAIL: it
    ships nothing -- measured, rc 1 and rc 139 -- so there is no survivor to
    record.)"""
    from test_pnr_tool_fatal_signal_and_checkpoint_resume import _ROUTE_OK
    project = _build_project(tmp_path, TOP)
    (project / "provenance.jsonl").write_text("")
    monkeypatch.setattr(R, "_PROV_SINK", project)
    calls = []

    def _openroad(container, cmd, timeout=None, **kw):
        if "openroad -no_init" not in cmd:
            return 0, "", ""
        out_dir = R._pl.pnr_dir(project)
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(cmd)
        if len(calls) == 1:
            for pre in ("floorplan.def", "placed.def", "post_cts.def",
                        "post_hold.def", "routed_preantenna.def"):
                (out_dir / pre).write_text(_def_text(routed=True))
            (out_dir / R._ANTENNA_CHECKPOINT_NAME).write_bytes(b"odb")
            for name in _SIGNOFF_SET:
                if name.endswith(".def"):
                    (out_dir / name).write_text(_def_text(routed=True))
                elif name.endswith(".v"):
                    (out_dir / name).write_text(f"module {TOP}(); endmodule\n")
                else:
                    (out_dir / name).write_text("worst slack 0.42\n")
            log = (_ROUTE_OK + _PG_OK
                   + "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: checkpoint="
                   + f"{out_dir}/{R._ANTENNA_CHECKPOINT_NAME} "
                   + "reason=REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n")
            (out_dir / "openroad.log").write_text(log)
            R._log_invocation(cmd, 0, 1, marker=kw.get("marker"),
                              container=None, outputs=kw.get("outputs"))
            return 0, log, ""
        R._log_invocation(cmd, 1, 1, marker=kw.get("marker"), container=None,
                          outputs=kw.get("outputs"))
        return 1, "", ""                     # the rollback tail fails

    monkeypatch.setattr(R, "_docker_exec", _openroad)
    res = R.step_pnr(project, TOP, _sky130_pdk(), "iic", "200x200", 0.30)
    assert len(calls) == 2, "the rollback tail was attempted"
    assert res.status == "PASS", (res.status, (res.detail or "")[:300])
    rc, check = _step21(project)
    assert (rc, check["status"]) == (0, "PASS"), check


# ── r4 (review wh6wcx64v): the reason is the matched SESSION's, per row ─────

def _survivors(project: Path):
    rows = [json.loads(ln) for ln in
            (project / "provenance.jsonl").read_text().splitlines()
            if ln.strip()]
    return [r for r in rows if r.get("record") == "artefact"
            and ROUTED in (r.get("outputs") or {})]


def test_a_resume_tail_that_dies_after_its_writes_is_named_as_the_producer(
        tmp_path, monkeypatch):
    """The main session dies in a best-effort stage BEFORE the sign-off writes;
    the RESUME tail writes the trio and then dies in the estimate stage; the
    step ships the tail's bytes. The row must name the resume tail -- the main
    session never wrote what shipped."""
    project = _build_project(tmp_path, TOP)
    (project / "provenance.jsonl").write_text("")
    monkeypatch.setattr(R, "_PROV_SINK", project)
    monkeypatch.setattr(R, "_openroad_supports_postroute_spef_repair",
                        lambda *_a, **_k: True)
    calls = []

    def _openroad(container, cmd, timeout=None, **kw):
        if "openroad -no_init" not in cmd:
            return 0, "", ""
        out_dir = R._pl.pnr_dir(project)
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(cmd)
        if len(calls) == 1:
            for pre in ("floorplan.def", "placed.def", "post_cts.def",
                        "post_hold.def", "routed_preantenna.def"):
                (out_dir / pre).write_text(_def_text(routed=True))
            log = _crash_log("postroute_drv_repair")
            (out_dir / "openroad.log").write_text(log)
            R._log_invocation(cmd, 139, 1, marker=kw.get("marker"),
                              container=None, outputs=kw.get("outputs"))
            return 139, log, ""
        for name in _SIGNOFF_SET:                  # the resume tail's writes
            if name.endswith(".def"):
                (out_dir / name).write_text(_def_text(routed=True))
            elif name.endswith(".v"):
                (out_dir / name).write_text(f"module {TOP}(); endmodule\n")
            else:
                (out_dir / name).write_text("worst slack 0.42\n")
        log = _crash_log("postroute_setup_repair_estimate") + _PG_OK
        if kw.get("log_path"):
            Path(kw["log_path"]).write_text(log)
        R._log_invocation(cmd, 139, 1, marker=kw.get("marker"),
                          container=None, outputs=kw.get("outputs"))
        return 139, log, ""

    monkeypatch.setattr(R, "_docker_exec", _openroad)
    res = R.step_pnr(project, TOP, _sky130_pdk(), "iic", "200x200", 0.30)
    assert len(calls) == 2 and R._PNR_RESUME_TCL in calls[1]
    assert res.status == "PASS", (res.status, (res.detail or "")[:300])
    surv = _survivors(project)
    assert len(surv) == 1, surv
    assert R._PNR_RESUME_TCL in surv[0]["survived"]["session_command"]
    assert "RESUME tail" in surv[0]["survived"]["reason"], surv[0]
    rc, check = _step21(project)
    assert (rc, check["status"]) == (0, "PASS"), check


def test_a_rollback_tail_that_wrote_then_failed_is_named_as_the_producer(
        tmp_path, monkeypatch):
    """The refusing session writes its route (rc 0); the rollback tail writes
    the trio AGAIN and then exits 1. Put-back keeps the tail's bytes (it wrote
    them), so what ships is the TAIL's route -- and the row must say that, not
    that the refusing session's route was kept."""
    from test_pnr_tool_fatal_signal_and_checkpoint_resume import _ROUTE_OK
    project = _build_project(tmp_path, TOP)
    (project / "provenance.jsonl").write_text("")
    monkeypatch.setattr(R, "_PROV_SINK", project)
    calls = []

    def _write_set(out_dir, tag):
        for name in _SIGNOFF_SET:
            if name.endswith(".def"):
                (out_dir / name).write_text(_def_text(routed=True)
                                            + f"# {tag}\n")
            elif name.endswith(".v"):
                (out_dir / name).write_text(f"module {TOP}(); endmodule // {tag}\n")
            else:
                (out_dir / name).write_text(f"worst slack 0.42 {tag}\n")

    def _openroad(container, cmd, timeout=None, **kw):
        if "openroad -no_init" not in cmd:
            return 0, "", ""
        out_dir = R._pl.pnr_dir(project)
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(cmd)
        if len(calls) == 1:
            for pre in ("floorplan.def", "placed.def", "post_cts.def",
                        "post_hold.def", "routed_preantenna.def"):
                (out_dir / pre).write_text(_def_text(routed=True))
            (out_dir / R._ANTENNA_CHECKPOINT_NAME).write_bytes(b"odb")
            _write_set(out_dir, "refusing session")
            log = (_ROUTE_OK + _PG_OK
                   + "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: checkpoint="
                   + f"{out_dir}/{R._ANTENNA_CHECKPOINT_NAME} "
                   + "reason=REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n")
            (out_dir / "openroad.log").write_text(log)
            R._log_invocation(cmd, 0, 1, marker=kw.get("marker"),
                              container=None, outputs=kw.get("outputs"))
            return 0, log, ""
        _write_set(R._pl.pnr_dir(project), "rollback tail")
        R._log_invocation(cmd, 1, 1, marker=kw.get("marker"), container=None,
                          outputs=kw.get("outputs"))
        return 1, "", ""

    monkeypatch.setattr(R, "_docker_exec", _openroad)
    res = R.step_pnr(project, TOP, _sky130_pdk(), "iic", "200x200", 0.30)
    assert len(calls) == 2 and "pnr_antenna_rollback" in calls[1]
    assert "rollback tail" in (project / ROUTED).read_text(), \
        "the tail's bytes are what ships"
    assert res.status == "PASS", (res.status, (res.detail or "")[:300])
    surv = _survivors(project)
    assert len(surv) == 1, surv
    reason = surv[0]["survived"]["reason"]
    assert "antenna-rollback tail" in reason and "not kept" in reason, reason
    rc, check = _step21(project)
    assert (rc, check["status"]) == (0, "PASS"), check
