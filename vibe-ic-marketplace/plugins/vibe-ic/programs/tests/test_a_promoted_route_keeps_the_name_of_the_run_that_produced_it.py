"""A route a sign-off repair PRODUCED and the runner PROMOTED stays bound to the
openroad run that produced its bytes.

MEASURED on spm run23 after a phase-3 re-run: step 21 FAILED

    provenance_check . --output phase3/stage3/pnr/routed.def --tool openroad
        --require-measured
    -> hash mismatch: log=sha256:d7b41629... disk=sha256:b5bfaf42...

provenance.jsonl held two records of routed.def: 11:38Z openroad d7b41629 (the
ORIGINAL route) and 17:24Z phase3_one_shot_runner b5bfaf42 (a bulk re-emit). The
bytes on disk were openroad's -- the repair session wrote `routed_repaired.def`
and the runner copied it over routed.def -- but the repair's openroad invocation
declared no outputs, so no record carried b5bfaf42 under the tool that made it.

THE CHECK IS NOT LOOSENED. `provenance_check` already binds an artefact to a run
BY CONTENT (`_declares`); the missing thing was the producing record. The runner
now declares what each route-producing openroad session writes
(`_run_route_producer`, removing any copy an earlier run left at that path first)
and records every promotion with its source, sha and producing entry
(`_record_route_promotion`).

The container is faked only where openroad would write its file; the runner's own
`_log_invocation`, promotion and re-emit bookkeeping run as shipped, and the
verdict is the real `provenance_check`.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R                          # noqa: E402
import provenance_check as PC                               # noqa: E402

PNR = "phase3/stage3/pnr"
ROUTED = f"{PNR}/routed.def"
ORIGINAL = b"VERSION 5.8 ;\nDESIGN spm ;\n# the original route\nEND DESIGN\n"
REPAIRED = b"VERSION 5.8 ;\nDESIGN spm ;\n# the repaired route\nEND DESIGN\n"


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _project(tmp_path: Path) -> Path:
    """run23's ledger before the re-run: the ORIGINAL route, by openroad."""
    proj = tmp_path / "proj"
    (proj / PNR).mkdir(parents=True)
    (proj / ROUTED).write_bytes(ORIGINAL)
    (proj / f"{PNR}/spm.def").write_bytes(ORIGINAL)
    (proj / "provenance.jsonl").write_text(json.dumps({
        "record": "invocation", "tool": "openroad",
        "command": "openroad -no_init -exit /w/phase3/stage3/pnr/pnr.tcl",
        "exit_code": 0, "timestamp": "2026-09-23T11:38:00Z",
        "outputs": {ROUTED: _sha(ORIGINAL)}}) + "\n")
    return proj


def _records(proj: Path):
    return [json.loads(ln) for ln in
            (proj / "provenance.jsonl").read_text().splitlines() if ln.strip()]


def _check(proj: Path):
    out = proj / "pc.json"
    rc = PC.main([str(proj), "--output", ROUTED, "--tool", "openroad",
                  "--json", str(out)])
    return rc, json.loads(out.read_text())["checks"][0]


@pytest.fixture
def sink(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.setattr(R, "_PROV_SINK", proj)
    return proj


def _fake_openroad(writes: dict):
    """Stand-in for the container: openroad writes `writes`, exits 0, and the
    runner logs the invocation exactly as `_docker_exec` does."""
    def _exec(container, cmd, timeout=1800, *, marker=None, outputs=None,
              **_kw):
        for path, data in writes.items():
            Path(path).write_bytes(data)
        R._log_invocation(cmd, 0, 1, marker=marker, container=None,
                          outputs=outputs)
        return 0, "", ""
    return _exec


def _repair_and_promote(proj: Path, monkeypatch):
    pnr = proj / PNR
    repaired_def = pnr / "routed_repaired.def"
    repaired_v = pnr / "spm_pnr_repaired.v"
    monkeypatch.setattr(R, "_docker_exec", _fake_openroad(
        {repaired_def: REPAIRED, repaired_v: b"module spm; endmodule\n"}))
    R._run_route_producer("c", "/w/phase3/stage3/pnr/signoff_spef_repair.tcl",
                          [repaired_def, repaired_v])
    shutil.copy2(repaired_def, proj / ROUTED)
    shutil.copy2(repaired_def, pnr / "spm.def")
    R._record_route_promotion(proj, repaired_def,
                              [proj / ROUTED, pnr / "spm.def"],
                              "signoff_spef_repair")
    # the runner's later bulk re-emit pass, as on run23
    assert R._record_reemitted_outputs(proj) is None


# ── the repair promotion binds: RED on main (no producer declares the bytes) ─

def test_a_repair_promotion_binds_routed_def_to_the_openroad_run(
        sink, monkeypatch):
    _repair_and_promote(sink, monkeypatch)
    rc, check = _check(sink)
    assert (rc, check["status"]) == (0, "PASS"), check
    assert check["tool"] == "openroad", check
    assert check.get("declared_under") == f"{PNR}/routed_repaired.def", check


def test_the_promotion_record_names_the_producing_openroad_run(
        sink, monkeypatch):
    _repair_and_promote(sink, monkeypatch)
    promos = [r for r in _records(sink) if r.get("record") == "promotion"]
    assert len(promos) == 1, promos
    src = promos[0]["promoted_from"]
    assert src["path"] == f"{PNR}/routed_repaired.def"
    assert src["sha256"] == _sha(REPAIRED)
    assert src["entry"]["tool"] == "openroad", src
    assert "signoff_spef_repair.tcl" in src["entry"]["command"], src
    assert promos[0]["outputs"] == {ROUTED: _sha(REPAIRED),
                                    f"{PNR}/spm.def": _sha(REPAIRED)}
    # the newest record of routed.def is the promotion, not a nameless re-emit
    newest = [r for r in _records(sink) if ROUTED in (r.get("outputs") or {})][-1]
    assert newest.get("record") == "promotion", newest


def test_every_route_producing_session_declares_what_it_writes():
    """The three openroad sessions whose output the runner promotes."""
    for fn in (R.step_signoff_spef_repair,
               R.step_signoff_drv_wire_length_repair):
        src = inspect.getsource(fn)
        assert "_run_route_producer(" in src, fn.__name__
        assert 'f"openroad -no_init -exit {tcl_c}", marker=tcl_c)' not in src, (
            fn.__name__)
    # the repair step runs TWO: the repair session and the convergence restore
    src = inspect.getsource(R.step_signoff_spef_repair)
    calls = src.split("_run_route_producer(")[1:]
    assert len(calls) == 2, len(calls)
    assert any('"routed_repaired.def"' in c.split("]")[0] for c in calls)
    assert any('"routed_cvg_restored.def"' in c.split("]")[0] for c in calls)


# ── the other direction: nothing the check refused before is accepted now ──

def test_run23s_ledger_as_it_stands_still_fails(sink):
    """The measured shape: original openroad record + a nameless re-emit."""
    (sink / ROUTED).write_bytes(REPAIRED)
    with (sink / "provenance.jsonl").open("a") as f:
        f.write(json.dumps({
            "tool": "phase3_one_shot_runner",
            "command": "re-emit (phase3 iteration)", "exit_code": 0,
            "timestamp": "2026-09-23T17:24:00Z", "reconstructed": True,
            "outputs": {ROUTED: _sha(REPAIRED)}}) + "\n")
    rc, check = _check(sink)
    assert (rc, check["status"]) == (1, "FAIL"), check
    assert any("hash mismatch" in r for r in check["reasons"]), check


def test_a_hand_copied_def_fails(sink):
    """A DEF no recorded run produced, promoted by the runner's own helper:
    the record says so (`entry: null`) and binds nothing."""
    hand = sink / PNR / "hand_edited.def"
    hand.write_bytes(REPAIRED)
    shutil.copy2(hand, sink / ROUTED)
    R._record_route_promotion(sink, hand, [sink / ROUTED], "hand")
    promo = [r for r in _records(sink) if r.get("record") == "promotion"][-1]
    assert promo["promoted_from"]["entry"] is None, promo
    rc, check = _check(sink)
    assert (rc, check["status"]) == (1, "FAIL"), check


def test_a_stale_older_openroad_entry_alone_fails(sink):
    (sink / ROUTED).write_bytes(REPAIRED)
    rc, check = _check(sink)
    assert (rc, check["status"]) == (1, "FAIL"), check
    assert any("hash mismatch" in r for r in check["reasons"]), check


def test_a_file_an_earlier_run_left_is_not_attested(sink, monkeypatch):
    """A producer that writes nothing must not be credited with the stale
    copy a previous run left at its path."""
    stale = sink / PNR / "routed_repaired.def"
    stale.write_bytes(REPAIRED)
    monkeypatch.setattr(R, "_docker_exec", _fake_openroad({}))
    R._run_route_producer("c", "/w/x.tcl", [stale])
    assert not stale.exists()
    assert not any(_sha(REPAIRED) in (r.get("outputs") or {}).values()
                   for r in _records(sink)), _records(sink)
