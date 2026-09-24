"""An SDR-adopted route names the openroad session that wrote it.

MEASURED on spm run23 (final re-run on main 240c0a353, via icspm5), step 21:

    provenance.jsonl  row  57  openroad  routed.def d7b41629...  (reconstructed)
                      row 108  phase3_one_shot_runner re-emit     b5bfaf42...
                      row 161  phase3_one_shot_runner re-emit     6f8e3d27...  = disk
    provenance_check --tool openroad --require-measured
        -> routed.def hash mismatch  log=d7b41...  disk=6f8e3...  -> FAIL

and p3final.log: `SDR ADOPT: finishing the post-route tail from candidate.def
with postroute_drv_repair omitted` / `SDR_ADOPT ADOPTED`. The parent session
stops at the accept having written nothing; the ADOPT TAIL -- a fresh openroad
session restored from the candidate database -- writes routed.def, <top>.def and
<top>_pnr.v. It declared no outputs, so no openroad record carried those bytes.

The tail now declares the shipped products it writes (read off its own Tcl),
after removing any copy an earlier session left there -- the shape #2569 gave
the sign-off repair sessions. Driven through the REAL `_pnr_adopt_sdr_candidates`
over the REAL PnR template; the container is faked only where openroad writes
its files, and logs the invocation exactly as `_docker_exec` does. The verdict
is the real `provenance_check`.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phase3_one_shot_runner as R                              # noqa: E402
import provenance_check as PC                                   # noqa: E402
from test_sdr_checkpoint_and_child import SITE1, _full_pnr_tcl  # noqa: E402

TOP = "chip_top"
PNR = "phase3/stage3/pnr"
ROUTED = f"{PNR}/routed.def"
ORIGINAL = b"VERSION 5.8 ;\nDESIGN chip_top ;\n# the original route\nEND DESIGN\n"
EARLIER = b"VERSION 5.8 ;\nDESIGN chip_top ;\n# an earlier re-run\nEND DESIGN\n"
ADOPTED = b"VERSION 5.8 ;\nDESIGN chip_top ;\n# the adopted candidate\nEND DESIGN\n"


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _rows(proj: Path):
    return [json.loads(ln) for ln in
            (proj / "provenance.jsonl").read_text().splitlines() if ln.strip()]


def _run23(tmp_path: Path, monkeypatch) -> Path:
    """run23's ledger before the adoption: row 57 (the original route, a
    reconstructed openroad record) and row 108 (a nameless runner re-emit);
    the disk holds the earlier re-run's bytes."""
    proj = tmp_path / "proj"
    pnr = proj / PNR
    pnr.mkdir(parents=True)
    (pnr / "pnr.tcl").write_text(
        _full_pnr_tcl(tmp_path).replace(str(tmp_path / "out"), str(pnr)))
    (pnr / "routed.def").write_bytes(EARLIER)
    with (proj / "provenance.jsonl").open("w") as f:
        f.write(json.dumps({
            "tool": "openroad", "command": "openroad -no_init -exit pnr.tcl",
            "exit_code": 0, "duration_ms": None, "reconstructed": True,
            "timestamp": "2026-09-23T11:38:55Z",
            "outputs": {ROUTED: _sha(ORIGINAL)}}) + "\n")
        f.write(json.dumps({
            "tool": "phase3_one_shot_runner",
            "command": "re-emit (phase3 iteration)", "exit_code": 0,
            "duration_ms": None, "reconstructed": True,
            "timestamp": "2026-09-23T17:24:00Z",
            "outputs": {ROUTED: _sha(EARLIER)}}) + "\n")
    monkeypatch.setattr(R, "_PROV_SINK", proj)
    return proj


def _candidate(proj: Path) -> Path:
    txn = proj / PNR / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_bytes(ADOPTED)
    (txn / R._SDR_CANDIDATE_ODB_NAME).write_bytes(b"CANDIDATE ODB\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("candidate report\n")
    return txn


def _adopt(proj: Path, monkeypatch, *, writes: bool = True):
    pnr = proj / PNR
    txn = _candidate(proj)

    def _openroad_tail(container, cmd, **kw):
        if writes:
            (pnr / "routed.def").write_bytes(ADOPTED)
            (pnr / f"{TOP}.def").write_bytes(ADOPTED)
            (pnr / f"{TOP}_pnr.v").write_bytes(b"module chip_top; endmodule\n")
        R._log_invocation(cmd, 0, 1, marker=kw.get("marker"), container=None,
                          outputs=kw.get("outputs"))
        return 0, "tail ran\n", ""

    monkeypatch.setattr(R, "_docker_exec", _openroad_tail)
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={txn / R._SDR_CANDIDATE_DEF_NAME} "
           f"report={txn / R._SDR_CANDIDATE_DRC_NAME} "
           f"txn={R._SDR_TXN_DIRS[SITE1]}\n")
    return R._pnr_adopt_sdr_candidates(
        container="", out_dir=pnr, out_dir_c=str(pnr),
        pnr_tcl=pnr / "pnr.tcl", log_text=log, hard_ceiling_s=60)


def _step21(proj: Path):
    out = proj / "pc.json"
    rc = PC.main([str(proj), "--output", ROUTED, "--tool", "openroad",
                  "--json", str(out)])
    return rc, json.loads(out.read_text())["checks"][0]


# ── run23's adoption: RED on main ──────────────────────────────────────────

def test_an_adopted_route_binds_to_the_adopting_openroad_session(
        tmp_path, monkeypatch):
    proj = _run23(tmp_path, monkeypatch)
    assert _adopt(proj, monkeypatch)["status"] == "ADOPTED"
    rc, check = _step21(proj)
    assert (rc, check["status"]) == (0, "PASS"), check
    assert check["tool"] == "openroad"


def test_the_tail_declares_every_shipped_product_it_wrote(tmp_path,
                                                          monkeypatch):
    proj = _run23(tmp_path, monkeypatch)
    _adopt(proj, monkeypatch)
    tail = [r for r in _rows(proj) if r.get("tool") == "openroad"
            and "pnr_sdr_adopt_1.tcl" in (r.get("command") or "")]
    assert len(tail) == 1, _rows(proj)
    assert tail[0]["outputs"] == {
        ROUTED: _sha(ADOPTED), f"{PNR}/{TOP}.def": _sha(ADOPTED),
        f"{PNR}/{TOP}_pnr.v": _sha(b"module chip_top; endmodule\n")}


def test_after_the_adoption_the_re_emit_has_nothing_to_claim(tmp_path,
                                                             monkeypatch):
    """The interaction with the re-emit: the adopting session's record IS the
    newest record of routed.def, so no nameless runner row is needed."""
    proj = _run23(tmp_path, monkeypatch)
    _adopt(proj, monkeypatch)
    n = len(_rows(proj))
    assert R._record_reemitted_outputs(proj) is None
    assert not any(ROUTED in (r.get("outputs") or {})
                   for r in _rows(proj)[n:]), _rows(proj)[n:]


def test_a_tail_that_writes_nothing_is_not_credited_with_stale_bytes(
        tmp_path, monkeypatch):
    """The earlier re-run's routed.def must not be hashed as the tail's work."""
    proj = _run23(tmp_path, monkeypatch)
    _adopt(proj, monkeypatch, writes=False)
    assert not (proj / ROUTED).exists()
    assert not any(_sha(EARLIER) in (r.get("outputs") or {}).values()
                   and r.get("tool") == "openroad" for r in _rows(proj))


# ── what the check refused before, it still refuses ───────────────────────

def test_run23s_ledger_as_it_stands_still_fails(tmp_path, monkeypatch):
    proj = _run23(tmp_path, monkeypatch)
    (proj / ROUTED).write_bytes(ADOPTED)
    with (proj / "provenance.jsonl").open("a") as f:
        f.write(json.dumps({
            "tool": "phase3_one_shot_runner",
            "command": "re-emit (phase3 iteration)", "exit_code": 0,
            "timestamp": "2026-09-24T02:07:49Z", "reconstructed": True,
            "outputs": {ROUTED: _sha(ADOPTED)}}) + "\n")
    rc, check = _step21(proj)
    assert (rc, check["status"]) == (1, "FAIL"), check
    assert any("hash mismatch" in r for r in check["reasons"]), check


def test_the_product_list_is_read_off_the_real_template(tmp_path):
    out = tmp_path / "out"
    names = [p.name for p in R._sdr_adopt_tail_products(
        out, str(out), _full_pnr_tcl(tmp_path))]
    assert names == ["routed.def", f"{TOP}.def", f"{TOP}_pnr.v"], names
