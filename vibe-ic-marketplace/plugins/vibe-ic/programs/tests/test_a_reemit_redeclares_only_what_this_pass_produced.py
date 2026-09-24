"""No back-fill launders a rewrite: evidence comes from the PRODUCER, completely.

MEASURED on main 240c0a353: a declared `reports/phase3/lvs.rpt`, rewritten with
no invocation, read PROVENANCE_HASH_MISMATCH under
`provenance_output_hash_completeness_check` -- and PASS after the runner's
`_record_reemitted_outputs` appended a `reconstructed` row declaring the new bytes.

Review wo6zpfboe then refused r2's evidence (a StepResult's `output_files`) and
set the rule this file pins, approved as one rule with three entry points:
  * a tool session writing FRESH files declares them (`_declared_session_exec`);
  * an IN-PLACE / runner-side transform (`_declared_transform_exec`, or
    `_restamp_provenance_output(writer=(step, since, input_path, input_sha))`)
    is credited only on the CHAIN: the bytes it read equal the newest declared
    sha of its input -- otherwise it runs undeclared and the input drift is
    recorded;
  * the re-emit pass is a DETECTOR: it records unexplained drift and never
    declares it. A StepResult row is not evidence.
Every verdict below is the real `provenance_output_hash_completeness_check`.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

import phase3_one_shot_runner as R                              # noqa: E402
import provenance_output_hash_completeness_check as C           # noqa: E402

REL = "reports/phase3/lvs.rpt"
ORIGINAL = b"LVS: circuits match uniquely\n"
TOP = "spm"
PNR = "phase3/stage3/pnr"
GDS = f"{PNR}/{TOP}.gds"
REPORT = "reports/phase3/provenance_unexplained_rewrites.json"


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _project(tmp_path: Path, rel: str = REL, data: bytes = ORIGINAL) -> Path:
    proj = tmp_path / "proj"
    f = proj / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    (proj / "provenance.jsonl").write_text(json.dumps({
        "record": "invocation", "tool": "netgen", "command": "netgen -batch lvs",
        "exit_code": 0, "timestamp": "2026-09-24T01:00:00Z",
        "outputs": {rel: _sha(data)}}) + "\n")
    return proj


def _rows(proj: Path):
    return [json.loads(ln) for ln in
            (proj / "provenance.jsonl").read_text().splitlines() if ln.strip()]


def _rules(proj: Path):
    verdict, findings = C.audit(proj)
    return verdict, sorted({f.rule for f in findings})


def _declined(proj: Path):
    rep = proj / REPORT
    return ([r["path"] for r in json.loads(rep.read_text())["rewrites"]]
            if rep.is_file() else [])


# ── the detector: RED on main ──────────────────────────────────────────────

def test_an_undeclared_rewrite_is_not_laundered_into_a_declaration(tmp_path):
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS: edited by hand\n")
    note = R._record_reemitted_outputs(proj)
    assert _rules(proj)[0] == "FAIL"
    assert "PROVENANCE_HASH_MISMATCH" in _rules(proj)[1]
    assert len(_rows(proj)) == 1, _rows(proj)
    assert note and REL in note, note
    assert _declined(proj) == [REL]


def test_a_step_row_listing_the_file_is_not_evidence(tmp_path):
    """r2 credited this; the review refused it: a StepResult lists whatever it
    lists."""
    proj = _project(tmp_path)
    (proj / REL).write_bytes(b"LVS written by nobody who declared it\n")
    R.StepResult("lvs", "PASS", 1.0, "ran", [str(proj / REL)])
    R._record_reemitted_outputs(proj)
    assert len(_rows(proj)) == 1
    assert _rules(proj)[0] == "FAIL"


# ── the chain: a transform of declared bytes is credited ───────────────────

def test_a_transform_of_declared_bytes_is_declared_naming_its_step(tmp_path):
    proj = _project(tmp_path, GDS, b"GDS as streamed\n")
    R._declared_transform_exec(
        proj, proj / GDS, "gds:layer_merge", "klayout", "merge",
        lambda: (proj / GDS).write_bytes(b"GDS merged\n"))
    last = _rows(proj)[-1]
    assert last["outputs"] == {GDS: _sha(b"GDS merged\n")}
    assert last["producing_step"] == "gds:layer_merge"
    assert _rules(proj)[0] == "PASS" and _declined(proj) == []


def test_a_transform_of_tampered_bytes_runs_undeclared(tmp_path):
    proj = _project(tmp_path, GDS, b"GDS as streamed\n")
    (proj / GDS).write_bytes(b"GDS edited by hand\n")
    ran = []
    R._declared_transform_exec(
        proj, proj / GDS, "gds:dummy_fill", "klayout", "fill",
        lambda: (ran.append(1), (proj / GDS).write_bytes(b"GDS filled\n")))
    assert ran == [1], "the transform still RUNS"
    assert len(_rows(proj)) == 1, _rows(proj)
    assert GDS in _declined(proj)
    assert "PROVENANCE_HASH_MISMATCH" in _rules(proj)[1]


def test_the_netlist_normaliser_does_not_launder_its_input(tmp_path):
    """Review item 3: the transform ran over an undeclared rewrite."""
    rel = "phase2/stage2/synth/netlist.v"
    body = "module m(x);\n  wire signed [3:0] a;\nendmodule\n"
    proj = _project(tmp_path, rel, body.encode())
    (proj / rel).write_text(body + "// edited by hand\n")
    R._ensure_structural_reader_readable(proj / rel, proj)
    assert len(_rows(proj)) == 1, _rows(proj)
    assert rel in _declined(proj)


def test_the_netlist_normaliser_is_credited_on_declared_input(tmp_path):
    rel = "phase2/stage2/synth/netlist.v"
    body = "module m(x);\n  wire signed [3:0] a;\nendmodule\n"
    proj = _project(tmp_path, rel, body.encode())
    R._ensure_structural_reader_readable(proj / rel, proj)
    assert _rows(proj)[-1]["producing_step"] == "structural_reader_normalise"
    assert _rules(proj)[0] == "PASS"


def test_the_canonical_copy_is_credited_only_from_a_declared_pnr_gds(tmp_path):
    canon_rel = f"phase3/stage4/gds/{TOP}.gds"
    for tampered in (False, True):
        proj = _project(tmp_path / str(tampered), GDS, b"GDS final\n")
        canon = proj / canon_rel
        canon.parent.mkdir(parents=True)
        canon.write_bytes(b"canonical from an earlier run\n")
        with (proj / "provenance.jsonl").open("a") as f:
            f.write(json.dumps({"tool": "klayout", "command": "copy",
                                "exit_code": 0, "reconstructed": True,
                                "timestamp": "2026-09-24T01:00:01Z",
                                "outputs": {canon_rel: _sha(
                                    b"canonical from an earlier run\n")}})
                    + "\n")
        if tampered:
            (proj / GDS).write_bytes(b"GDS final, edited by hand\n")
        t, src = time.time(), (proj / GDS).read_bytes()
        canon.write_bytes(src)
        n = len(_rows(proj))
        R._step37_restamp_canon_gds_provenance(
            proj, TOP, canon,
            writer=("canonicalize_artefacts", t, proj / GDS, _sha(src)))
        added = _rows(proj)[n:]
        declared = any(canon_rel in (r.get("outputs") or {}) for r in added)
        assert declared is (not tampered), (tampered, added)
        assert (GDS in _declined(proj)) is tampered


def test_the_signoff_backfill_never_redeclares_changed_bytes(tmp_path):
    proj = _project(tmp_path, GDS, b"GDS as streamed\n")
    rows0 = _rows(proj)
    rows0[0]["reconstructed"] = True               # a BACK-FILLED record
    rows0[0].pop("record")
    (proj / "provenance.jsonl").write_text(json.dumps(rows0[0]) + "\n")
    (proj / GDS).write_bytes(b"GDS edited by hand\n")
    R._v1_6_620_append_pv_signoff_provenance(proj, TOP)
    assert len(_rows(proj)) == 1, _rows(proj)
    assert GDS in _declined(proj)


def test_the_signoff_backfill_still_corrects_a_tool_on_unchanged_bytes(
        tmp_path):
    proj = tmp_path / "proj"
    f = proj / GDS
    f.parent.mkdir(parents=True)
    f.write_bytes(b"GDS as streamed\n")
    (proj / "provenance.jsonl").write_text(json.dumps({
        "tool": "phase3_one_shot_runner", "command": "back-fill",
        "exit_code": 0, "duration_ms": None, "reconstructed": True,
        "timestamp": "2026-09-24T01:00:00Z",
        "outputs": {GDS: _sha(b"GDS as streamed\n")}}) + "\n")
    R._v1_6_620_append_pv_signoff_provenance(proj, TOP)
    last = _rows(proj)[-1]
    assert last["tool"] == "magic" and last["outputs"] == {
        GDS: _sha(b"GDS as streamed\n")}


# ── full-flow shape: the REAL step_gds over a tree WITH a ledger ───────────

_REWRITERS = ("_gds_grid_snap", "_klayout_merge_layers", "_die_finishing",
              "_density_metal_fill", "_die_density_fill",
              "_restore_port_labels_if_missing")


def _run_step_gds(tmp_path: Path, monkeypatch, *, tamper_after_stream: bool):
    from test_kspm43_streamout_top_cell_is_the_def_design import (  # noqa
        _Pdk, _def)
    proj = tmp_path / "proj"
    pnr = R._pl.pnr_dir(proj)
    _def(pnr / f"{TOP}.def", TOP)
    (proj / "provenance.jsonl").write_text("")
    monkeypatch.setattr(R, "_PROV_SINK", proj)
    gds = pnr / f"{TOP}.gds"

    def _exec(container, cmd, *a, **kw):
        outs = kw.get("outputs") or []
        if any(Path(o) == gds for o in outs):      # the stream-out session
            gds.write_bytes(b"GDS as streamed\n")
            R._log_invocation(cmd, 0, 1, marker=kw.get("marker"),
                              container=None, outputs=outs)
            if tamper_after_stream:                # between stream-out and fill
                with gds.open("ab") as fh:
                    fh.write(b"edited by hand\n")
        return 0, "", ""

    monkeypatch.setattr(R, "_docker_exec", _exec)
    monkeypatch.setattr(R, "_tool_in_path", lambda c, t: True)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    monkeypatch.setattr(R, "_vacuous_on_unrouted", lambda *a, **k: None)
    monkeypatch.setattr(R, "_magic_def_to_gds",
                        lambda *a, **k: (False, "forced"))
    for name in _REWRITERS:                        # each rewrites the GDS
        def _rewrite(*a, _n=name, **k):
            with gds.open("ab") as fh:
                fh.write(f"{_n}\n".encode())
            return True, f"{_n} ok"
        monkeypatch.setattr(R, name, _rewrite)
    R.step_gds(proj, TOP, _Pdk(), "cnt")
    R._record_reemitted_outputs(proj)
    return proj, gds


def test_an_untampered_gds_flow_leaves_zero_unexplained_rows(tmp_path,
                                                             monkeypatch):
    """The false-FAIL guard: every rewrite after the declared stream-out is a
    chained transform, so nothing is unexplained and the GDS verifies."""
    proj, gds = _run_step_gds(tmp_path, monkeypatch, tamper_after_stream=False)
    assert b"_die_density_fill" in gds.read_bytes(), "the rewriters ran"
    assert _declined(proj) == [], _declined(proj)
    _v, findings = C.audit(proj)
    assert not [f for f in findings if GDS in f.detail
                and f.rule == "PROVENANCE_HASH_MISMATCH"], findings
    steps = [r.get("producing_step") for r in _rows(proj)
             if GDS in (r.get("outputs") or {})]
    assert "gds:layer_merge" in steps and "gds:die_density_fill" in steps, steps


def test_a_gds_edited_between_stream_out_and_fill_fails(tmp_path, monkeypatch):
    proj, gds = _run_step_gds(tmp_path, monkeypatch, tamper_after_stream=True)
    assert b"_density_metal_fill" in gds.read_bytes(), "the fill still RAN"
    assert GDS in _declined(proj)
    declared = [r for r in _rows(proj) if GDS in (r.get("outputs") or {})]
    assert len(declared) == 1, declared            # only the stream-out
    assert "PROVENANCE_HASH_MISMATCH" in _rules(proj)[1]


def test_one_helper_decides_for_every_redeclaration():
    import inspect
    for fn in (R._restamp_provenance_output,
               R._v1_6_620_append_pv_signoff_provenance):
        assert "_redeclaration_evidence(" in inspect.getsource(fn), fn.__name__
    assert not hasattr(R, "_PASS_PRODUCED"), "a step row is not evidence"


# ── a removal event ends a path's declared life (ledger order) ────────────
#
# The hash check reads a removal by its ledger index: it retires the
# declarations OLDER than it. The re-declaration rule reads it the same way, so
# a path the runner pruned and a later run re-published is a FIRST declaration,
# not a rewrite of the pruned bytes; and a removed input credits no chain.

def _prune(proj: Path, rel: str, data: bytes) -> None:
    (proj / rel).unlink()
    with (proj / "provenance.jsonl").open("a") as f:
        f.write(json.dumps({
            "event": "drc_signoff_prune", "op": "remove", "outputs": {},
            "removed": [rel], "removed_outputs": [{"path": rel,
                                                   "sha256": _sha(data)}],
            "timestamp": "2026-09-24T02:00:00Z"}) + "\n")


def test_a_path_republished_after_its_prune_is_a_first_declaration(tmp_path):
    proj = _project(tmp_path)
    _prune(proj, REL, ORIGINAL)
    (proj / REL).write_bytes(b"LVS: a later run's report\n")
    R._restamp_provenance_output(proj, REL, proj / REL, "netgen", "lvs")
    assert _rows(proj)[-1]["outputs"] == {
        REL: _sha(b"LVS: a later run's report\n")}
    assert _declined(proj) == []
    assert R._newest_declared_sha(proj, REL) == _sha(
        b"LVS: a later run's report\n")


def test_a_removed_input_credits_no_transform_chain(tmp_path):
    proj = _project(tmp_path, GDS, b"GDS as streamed\n")
    _prune(proj, GDS, b"GDS as streamed\n")
    assert R._newest_declared_sha(proj, GDS) is None
    (proj / GDS).write_bytes(b"GDS as streamed\n")
    assert R._redeclaration_evidence(
        proj, proj / GDS, _sha(b"GDS as streamed\n"),
        ("gds:dummy_fill", 0.0, proj / GDS, _sha(b"GDS as streamed\n"))) is None


def test_reemit_detector_does_not_resurrect_a_removed_declaration(tmp_path):
    proj = _project(tmp_path)
    _prune(proj, REL, ORIGINAL)
    (proj / REL).write_bytes(b"LVS: first report after prune\n")
    assert R._record_reemitted_outputs(proj) is None
    assert len(_rows(proj)) == 2, "the detector must not first-declare it"
    assert _declined(proj) == []
    R._restamp_provenance_output(proj, REL, proj / REL, "netgen", "lvs")
    assert _rules(proj)[0] == "PASS"


def test_failed_invocation_cannot_become_the_transform_input(tmp_path):
    proj = _project(tmp_path, GDS, b"GDS as streamed\n")
    (proj / GDS).write_bytes(b"GDS edited outside session\n")
    with (proj / "provenance.jsonl").open("a") as f:
        f.write(json.dumps({"record": "invocation", "tool": "klayout",
                            "exit_code": 1,
                            "outputs": {GDS: _sha(b"GDS edited outside session\n")}})
                + "\n")
    R._declared_transform_exec(
        proj, proj / GDS, "gds:fill", "klayout", "fill",
        lambda: (proj / GDS).write_bytes(b"GDS filled\n"))
    assert len(_rows(proj)) == 2
    assert GDS in _declined(proj)
    assert "PROVENANCE_HASH_MISMATCH" in _rules(proj)[1]
