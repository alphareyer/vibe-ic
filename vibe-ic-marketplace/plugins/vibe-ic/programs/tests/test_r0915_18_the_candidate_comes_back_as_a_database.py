"""R-0915-18 — the SDR candidate travels back to the parent as a DATABASE.

R-0915-16 made the leg INTO the child an ODB.  The leg back OUT of it stayed a
DEF, and that is where the failure that motivated the whole transaction was
still happening.  MEASURED on `subservient` x gf180mcuD (lane icsub2, run r12
on 205c52b1a, every count off a finished log):

    child, restored with `read_db pre_repair.odb`
        DRT-0157 Number of guides: 20986   over   Number of nets: 2378
        ROUTE_GUIDES_HELD                  DRT-0047 = 0
    adopt session, restored with `read_def candidate.def`
        ROUTE_GUIDES_UNAVAILABLE           DRT-0047 = 3   (+3 in the parent tail)

The adopt session is not a reader.  It is the session that runs the whole
post-route tail -- antenna repair, the second SDR site, the named-violation
reroute -- so it ROUTES AGAIN, and `global_route` no-ops on nets that already
carry committed detailed routing.  Restored from a DEF it therefore has no
guides, every `detailed_route` after it refuses with DRT-0047, the refusal is
swallowed as `PG_REROUTE_NONFATAL`, and the run SHIPS a tail whose reroutes
were all skipped.  That is the defect: not a crash, a silent skip.

On sha256 (lane icsha2) the SAME leg fails the other way and the guides are
fine: the DEF's wire text does not re-parse into router-legal geometry, the
diagonal `spare_tielo_*` wires come back, and the PG reroute hits DRT-1010 in
`pnr_sdr_adopt_2` -- which the #2263 relay cannot absorb, because the adopt tail
SHIPS and must not drop wiring.  An ODB is the tool's own representation and is
never re-parsed, so one carrier answers both failures.

So the candidate gets a restore point of its own, and a candidate that has only
a DEF is REFUSED BY NAME rather than degraded into that silence.  The DEF stays
-- it is the human-readable artefact the rejection path copies to
`rejected.def` and the one a reader diffs.
"""
import shutil
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sdr_checkpoint_and_child import (  # noqa: E402
    SITE1, SITE2, _full_pnr_tcl)

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

#: SPELT OUT, not imported from the module under test, so that every case below
#: RUNS on a tree that does not have the fix and fails on BEHAVIOUR -- a base
#: arm that dies with AttributeError has answered nothing.  The one case that
#: does read the constant is the binding check directly below it.
CAND_ODB = "candidate.odb"


def test_the_constant_names_the_file_these_cases_use():
    """The module's own name for the candidate restore point, bound to the
    literal the rest of this file is written against."""
    assert R._SDR_CANDIDATE_ODB_NAME == CAND_ODB


def _adopt_log(txn: Path, stage: str) -> str:
    return (f"{R._SDR_ADOPT_MARKER} stage={stage} "
            f"def={txn / R._SDR_CANDIDATE_DEF_NAME} "
            f"report={txn / R._SDR_CANDIDATE_DRC_NAME} "
            f"txn={R._SDR_TXN_DIRS[stage]}\n")


def _out_with_deck(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    return out


# --------------------------------------------------------------- the child

@pytest.mark.parametrize("stage", [SITE1, SITE2])
def test_the_child_publishes_a_database_beside_its_def(stage):
    """Both artefacts, and the DEF first: the DEF is what a rejection copies
    and what a human reads, the ODB is what the parent restores from."""
    blk = R._v1_8_100_signoff_drv_repair_tcl("/c/out", stage=stage)
    txn = R._SDR_TXN_DIRS[stage]
    cand_def = f"/c/out/{txn}/{R._SDR_CANDIDATE_DEF_NAME}"
    cand_odb = f"/c/out/{txn}/{CAND_ODB}"
    assert f"write_def {cand_def}" in blk
    assert f"write_db {cand_odb}" in blk
    assert blk.index(cand_def) < blk.index(cand_odb)


def test_the_two_sites_publish_to_distinct_databases():
    """The sites used to share one transaction directory and destroy each
    other's evidence; the candidate ODB must not reintroduce that."""
    b1 = R._v1_8_100_signoff_drv_repair_tcl("/c/out", stage=SITE1)
    b2 = R._v1_8_100_signoff_drv_repair_tcl("/c/out", stage=SITE2)
    o1 = f"/c/out/{R._SDR_TXN_DIRS[SITE1]}/{CAND_ODB}"
    o2 = f"/c/out/{R._SDR_TXN_DIRS[SITE2]}/{CAND_ODB}"
    assert o1 != o2
    assert f"write_db {o1}" in b1 and f"write_db {o1}" not in b2
    assert f"write_db {o2}" in b2 and f"write_db {o2}" not in b1


def test_a_failed_database_write_is_disclosed_and_does_not_forge_a_verdict():
    """A candidate that routed and measured clean did so whether or not its
    db could be written.  The ONE place that decides what happens to a
    candidate with no restore point is the parent's refusal, so the child
    discloses and leaves `route_ok` alone -- it must not invent a route
    failure, and it must not stay silent either."""
    blk = R._v1_8_100_signoff_drv_repair_tcl("/c/out", stage=SITE1)
    i = blk.index(CAND_ODB + "}")
    line = blk[blk.rindex("\n", 0, i) + 1:blk.index("\n", i)]
    assert "SDR_CHILD_CANDIDATE_ODB_NONFATAL" in line
    assert "SDR_CHILD_CANDIDATE_ODB_WRITTEN" in line
    assert "set _sdr_tx_route_ok 0" not in line
    # the DEF's own failure branch is UNCHANGED and still clears route_ok
    j = blk.index("write_def /c/out/" + R._SDR_TXN_DIRS[SITE1])
    def_line = blk[blk.rindex("\n", 0, j) + 1:blk.index("\n", j)]
    assert "set _sdr_tx_route_ok 0" in def_line


# --------------------------------------------------------------- the parent

def test_the_adopt_session_restores_from_the_database(tmp_path, monkeypatch):
    """The direction that must work: candidate.odb present -> `read_db`, in a
    session that no longer reads the candidate DEF at all."""
    out = _out_with_deck(tmp_path)
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE DEF\n")
    (txn / CAND_ODB).write_bytes(b"CANDIDATE ODB\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("candidate report\n")
    monkeypatch.setattr(R, "_docker_exec",
                        lambda container, cmd, **kw: (0, "tail ran\n", ""))
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=_adopt_log(txn, SITE1),
        hard_ceiling_s=60)
    assert rec["status"] == "ADOPTED"
    tail = (out / "pnr_sdr_adopt_1.tcl").read_text()
    assert f"read_db {txn / CAND_ODB}" in tail
    assert f"read_def {txn / R._SDR_CANDIDATE_DEF_NAME}" not in tail
    # an ODB restore replaces the whole load: the LEFs come back with it
    assert not any(ln.startswith("read_lef ") for ln in tail.splitlines())


def test_a_candidate_with_only_a_def_is_refused_by_name(tmp_path, monkeypatch):
    """The other direction, and the point of the ruling.  The DEF is there,
    the candidate routed, the receipt says ACCEPTED -- and the tail is still
    refused, because restoring it from the DEF is the silent skip."""
    out = _out_with_deck(tmp_path)
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE DEF\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("candidate report\n")
    calls = []
    monkeypatch.setattr(
        R, "_docker_exec",
        lambda container, cmd, **kw: (calls.append(cmd), (0, "", ""))[1])
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=_adopt_log(txn, SITE1),
        hard_ceiling_s=60)
    assert rec["status"] == "FAILED"
    assert rec["rc"] == 1
    # NAMED, not described: a reader can go and look at the path that is missing
    assert CAND_ODB in rec["reason"]
    assert str(txn / CAND_ODB) in rec["reason"]
    # and it says WHY, so the refusal is not a bare policy
    assert "DRT-0047" in rec["reason"]
    # nothing was run and no tail was written: the refusal is not a degrade
    assert calls == []
    assert not (out / "pnr_sdr_adopt_1.tcl").exists()


def test_the_refusal_is_not_reachable_by_deleting_the_disclosure(tmp_path,
                                                                monkeypatch):
    """The DEF is still REQUIRED and still checked first, so removing it gives
    the old refusal and not the new one -- two absences, two messages."""
    out = _out_with_deck(tmp_path)
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / CAND_ODB).write_bytes(b"CANDIDATE ODB\n")
    monkeypatch.setattr(R, "_docker_exec",
                        lambda container, cmd, **kw: (0, "", ""))
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=_adopt_log(txn, SITE1),
        hard_ceiling_s=60)
    assert rec["status"] == "FAILED"
    assert R._SDR_CANDIDATE_DEF_NAME in rec["reason"]
    assert "DRT-0047" not in rec["reason"]


def test_the_leg_into_the_child_is_still_the_odb(tmp_path):
    """R-0915-16 must not be undone by R-0915-18: the parent checkpoint the
    child restores from stays a database, and its DEF stays beside it."""
    out = _out_with_deck(tmp_path)
    R._write_sdr_child_decks(out / "pnr.tcl", out, container="")
    child = (out / R._sdr_child_tcl_name(SITE1)).read_text()
    txn = out / R._SDR_TXN_DIRS[SITE1]
    assert f"read_db {txn / R._SDR_CHECKPOINT_ODB_NAME}" in child
    assert "read_def " not in child
    blk = R._v1_8_100_signoff_drv_repair_tcl("/c/out", stage=SITE1)
    assert "write_def $_sdr_tx_dir/pre_repair.def" in blk
    assert "write_db $_sdr_tx_ckpt_odb" in blk


@needs_tclsh
def test_the_adopt_tail_is_valid_tcl(tmp_path, monkeypatch):
    """The restore line is line surgery on a real deck; a deck that does not
    parse is a defect the run cannot report, because it dies before it can."""
    import subprocess
    out = _out_with_deck(tmp_path)
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE DEF\n")
    (txn / CAND_ODB).write_bytes(b"CANDIDATE ODB\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("candidate report\n")
    monkeypatch.setattr(R, "_docker_exec",
                        lambda container, cmd, **kw: (0, "tail ran\n", ""))
    R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=_adopt_log(txn, SITE1),
        hard_ceiling_s=60)
    tail = out / "pnr_sdr_adopt_1.tcl"
    probe = tmp_path / "parse.tcl"
    probe.write_text(
        'if {[catch {info complete [read [open {%s} r]]} e]} {puts "ERR $e"; '
        'exit 1}\nexit 0\n' % tail)
    cp = subprocess.run([tclsh, str(probe)], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
