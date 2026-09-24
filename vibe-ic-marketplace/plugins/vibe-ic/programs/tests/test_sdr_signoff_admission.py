"""An SDR handoff cannot ship a candidate without sign-off evidence."""
from pathlib import Path
import inspect
import sys

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sdr_checkpoint_and_child import SITE1, _full_pnr_tcl  # noqa: E402


@pytest.mark.parametrize("pdk", [None, object()])
def test_sdr_adopt_refuses_unmeasured_signoff_and_restores_incumbent(
        tmp_path, monkeypatch, pdk):
    project = tmp_path / "design"
    out = project / "phase3" / "stage3" / "pnr"
    out.mkdir(parents=True)
    deck = out / "pnr.tcl"
    deck.write_text(_full_pnr_tcl(tmp_path))
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir()
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE\n")
    (txn / R._SDR_CANDIDATE_ODB_NAME).write_bytes(b"candidate database")
    (txn / R._SDR_CHECKPOINT_ODB_NAME).write_bytes(b"incumbent database")
    (txn / "pre_repair.def").write_text("INCUMBENT\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("0\n")
    (txn / "receipt.tsv").write_text(
        "status\treason\tbefore_router_drc\tafter_router_drc\n"
        "ACCEPTED\trouter_drc_preserved_clean\t0\t0\n")
    # A sign-off probe may have streamed the candidate before rejecting it.
    # These names must not remain cacheable after the incumbent is restored.
    (out / "chip_top.gds").write_bytes(b"rejected candidate layout")
    (out / "chip_top.prefinish.gds").write_bytes(b"rejected prefinish layout")
    calls = []

    def eda_writes(container, cmd, **kwargs):
        calls.append(cmd)
        if "pnr_sdr_adopt_" in cmd:
            (out / "chip_top.def").write_text("CANDIDATE\n")
        elif "pnr_sdr_reject_" in cmd:
            (out / "chip_top.def").write_text("INCUMBENT\n")
        return 0, "OpenROAD completed\n", ""

    monkeypatch.setattr(R, "_docker_exec", eda_writes)
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={txn / R._SDR_CANDIDATE_DEF_NAME}\n")
    kwargs = dict(container="", out_dir=out, out_dir_c=str(out),
                  pnr_tcl=deck, log_text=log, hard_ceiling_s=60)
    # On the base tree these inputs are not accepted at all. Call its real
    # adopter anyway, so the red control is a wrong decision, not TypeError.
    if "project" in inspect.signature(R._pnr_adopt_sdr_candidates).parameters:
        kwargs.update(project=project, top="chip_top", pdk=pdk)
    rec = R._pnr_adopt_sdr_candidates(**kwargs)
    assert rec["status"] == "REJECTED", rec
    assert rec["rc"] == 0
    assert rec["reason"].startswith("signoff_refused:")
    assert (out / "chip_top.def").read_text() == "INCUMBENT\n"
    assert not (out / "chip_top.gds").exists()
    assert not (out / "chip_top.prefinish.gds").exists()
    assert (txn / "invalidated_chip_top.gds").read_bytes() == \
        b"rejected candidate layout"
    assert (txn / "receipt.tsv").read_text().splitlines()[1].startswith(
        "REJECTED_CANDIDATE_DISCARDED\tsignoff_")
    assert len(calls) == 2
