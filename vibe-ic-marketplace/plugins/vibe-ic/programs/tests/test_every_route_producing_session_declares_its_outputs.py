"""Every route- and LVS-producing tool session declares what it writes, so a
legitimate RE-RUN leaves a ledger whose newest record of each path is the
session that wrote it.

Review wo6zpfboe on next/ictier1-reemit: under an evidence rule, a supported
re-run (--force-step pnr, a cache rejection after an RTL / identity / die change)
rewrites routed.def, the stage DEFs, <top>_pnr.v and openroad.log -- and the main
PnR session declared NONE of them (only a "routed.def if missing" back-fill ever
named it). On a tree that already had a ledger (published spm v1.14.88: rows
30/31 declare 9 PnR paths) the checker then reads PROVENANCE_HASH_MISMATCH on
every one. The producer has to say what it wrote.

Driven through the REAL `step_pnr`, on a project WITH a prior ledger declaring
the PnR products at an earlier run's bytes. The container is faked only where
openroad writes its files (and logs the invocation exactly as `_docker_exec`
does); the verdict is the real `provenance_output_hash_completeness_check`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phase3_one_shot_runner as R                              # noqa: E402
import provenance_output_hash_completeness_check as C           # noqa: E402
from test_issue2108_pnr_approach_log_survives_the_next_approach import (  # noqa: E402
    _PG_OK, _build_project, _pdk)

TOP = "widget"
_TEE_RE = re.compile(r"\|\s*tee\s+(?:-a\s+)?(\S+)")


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _rerun_tree(tmp_path: Path, monkeypatch) -> Path:
    """A project a previous run already routed: every PnR product on disk and
    declared by an openroad record at THOSE bytes."""
    project = _build_project(tmp_path, TOP)
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    outs = {}
    for name in ("floorplan.def", "placed.def", "post_cts.def",
                 "post_hold.def", "routed.def", f"{TOP}.def", f"{TOP}_pnr.v",
                 "openroad.log"):
        data = f"previous run: {name}\n".encode()
        (pnr / name).write_bytes(data)
        outs[str((pnr / name).relative_to(project))] = _sha(data)
    (project / "provenance.jsonl").write_text(json.dumps({
        "record": "invocation", "tool": "openroad",
        "command": "openroad -no_init -exit pnr.tcl", "exit_code": 0,
        "timestamp": "2026-08-31T18:07:38Z", "outputs": outs}) + "\n")
    monkeypatch.setattr(R, "_PROV_SINK", project)
    return project


def _rerun_pnr(project: Path, monkeypatch):
    pnr = R._pl.pnr_dir(project)

    def _openroad(container, cmd, timeout=None, **kw):
        if "openroad -no_init" not in cmd:
            return 0, "", ""
        out = "[INFO DRT-0199] Number of violations = 0.\n" + _PG_OK
        # openroad writes what its DECK says, whatever the runner declared
        deck = (pnr / "pnr.tcl").read_text(errors="replace")
        for full in re.findall(r"write_(?:def|verilog)\s+(\S+?\.(?:def|v))(?=[\s}]|$)",
                               deck):
            p = Path(full)
            if p.parent == pnr:
                p.write_text(f"this run: {p.name}\n")
        m = _TEE_RE.search(cmd)
        if m:
            (pnr / Path(m.group(1)).name).write_text(out)
        R._log_invocation(cmd, 0, 1, marker=kw.get("marker"), container=None,
                          outputs=kw.get("outputs"))
        return 0, out, ""

    monkeypatch.setattr(R, "_docker_exec", _openroad)
    return R.step_pnr(project, TOP, _pdk(), "iic", "auto", 0.30)


def _mismatched(project: Path):
    _v, findings = C.audit(project)
    return sorted(f.detail.split("'")[1] for f in findings
                  if f.rule == "PROVENANCE_HASH_MISMATCH")


def test_a_pnr_rerun_leaves_no_hash_mismatch_on_its_products(tmp_path,
                                                             monkeypatch):
    project = _rerun_tree(tmp_path, monkeypatch)
    _rerun_pnr(project, monkeypatch)
    assert (R._pl.pnr_dir(project) / "routed.def").read_text() == \
        "this run: routed.def\n"
    assert _mismatched(project) == [], _mismatched(project)


def test_the_main_session_names_every_stage_product_and_its_log(
        tmp_path, monkeypatch):
    project = _rerun_tree(tmp_path, monkeypatch)
    _rerun_pnr(project, monkeypatch)
    rows = [json.loads(ln) for ln in
            (project / "provenance.jsonl").read_text().splitlines()
            if ln.strip()]
    session = [r for r in rows[1:] if r.get("tool") == "openroad"
               and "pnr.tcl" in (r.get("command") or "")]
    assert session, rows
    declared = {Path(k).name for k in session[-1]["outputs"]}
    for name in ("routed.def", f"{TOP}.def", f"{TOP}_pnr.v", "post_cts.def",
                 "openroad.log"):
        assert name in declared, (name, sorted(declared))


def test_every_lvs_tool_invocation_declares_its_output():
    """The KLayout-LVS invocations and the Magic extraction run through the
    one declared-session helper, each naming the file it writes."""
    import inspect
    src = inspect.getsource(R._run_klayout_lvs)
    for product in ("[cells_sp]", "[layout_sp]", "[source_sp]", "[lvs_rpt]",
                    "[cmp_json]"):
        assert product in src, product
    assert src.count("_declared_session_exec(") == 5
    ext = inspect.getsource(R._run_extraction_lvs)
    assert ('_declared_session_exec(\n        container, cmd, [spice_out, '
            'feedback_out, ext_dir / "ext2spice.log"]') in ext
