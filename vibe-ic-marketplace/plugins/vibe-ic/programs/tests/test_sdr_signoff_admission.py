"""An SDR handoff cannot ship a candidate without sign-off evidence."""
from pathlib import Path
import inspect
import json
import sys
from types import SimpleNamespace

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sdr_checkpoint_and_child import SITE1, _full_pnr_tcl  # noqa: E402
from _hostpaths import require_repo  # noqa: E402
from _prestream_admission_fixture import _fixture_pdk_hasher  # noqa: E402


def _routed_basis(monkeypatch, project, top="chip_top"):
    """The routed basis a candidate's sign-off is bound to (57b1579a3): the
    routed DEF, the PnR input netlist and the SDC, plus the fixture PDK hasher
    (a fixture PDK's paths exist on no host). Without them the admission now
    refuses `candidate_layout_basis:missing layout input` BEFORE any DRC, which
    is correct and is not what these tests are about (T109c)."""
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    if not (pnr / "routed.def").is_file():
        (pnr / "routed.def").write_text(f"DESIGN {top} ;\nEND DESIGN\n")
    netlist = R.pnr_input_netlist(project, top)[0]
    if not netlist.is_file():
        netlist.parent.mkdir(parents=True, exist_ok=True)
        netlist.write_text(f"module {top}();\nendmodule\n")
    if not (pnr / "constraint.sdc").is_file():
        (pnr / "constraint.sdc").write_text(
            "create_clock -name clk -period 10 [get_ports clk]\n")
    monkeypatch.setattr(R, "_step_pdk_hasher", _fixture_pdk_hasher)


@pytest.mark.parametrize("case,expected", [
    ("violations", "drc_fail:DF.13_MV,DF.14_MV"),
    ("missing", "drc_report_missing:"),
    ("lvs", "lvs_fail:fail"),
])
def test_sdr_signoff_admission_discloses_the_actual_failure(
        tmp_path, monkeypatch, case, expected):
    project = tmp_path / "design"
    layout = project / "phase3" / "stage3" / "pnr" / "chip_top.gds"
    report = project / "phase3" / "reports" / "drc.rpt"
    evidence = project / "phase3" / "stage3" / "pnr" / "sdr_transaction"
    pdk = SimpleNamespace(drc_deck="deck.drc")
    monkeypatch.setattr(R.time, "time_ns", lambda: 1)
    _routed_basis(monkeypatch, project)

    # THE STUBS WRITE WHERE THEY ARE TOLD. Since 57b1579a3 a candidate is
    # signed off in a private shadow copy of the project, so each tool stub
    # writes under the project it is HANDED (args[0]), exactly as the real
    # step does -- not under the outer fixture path (T109c).
    written = {}

    def gds(*args, **kwargs):
        out = R._pl.pnr_dir(Path(args[0])) / "chip_top.gds"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"candidate layout")
        return R.StepResult("gds", "PASS")

    def drc(*args, **kwargs):
        if case != "missing":
            rpt = Path(args[0]) / "phase3" / "reports" / "drc.rpt"
            rpt.parent.mkdir(parents=True, exist_ok=True)
            items = ("<item><category>'DF.14_MV'</category></item>"
                     "<item><category>'DF.13_MV'</category></item>") \
                if case == "violations" else ""
            written["drc"] = ("<report-database><items>" + items +
                              "</items></report-database>")
            rpt.write_text(written["drc"])
        return R.StepResult("drc", "FAIL" if case != "lvs" else "PASS")

    monkeypatch.setattr(R, "step_gds", gds)
    monkeypatch.setattr(R, "step_drc", drc)
    monkeypatch.setattr(R, "step_lvs",
                        lambda *args, **kwargs: R.StepResult("lvs", "FAIL"))
    kwargs = {"evidence_dir": evidence} if "evidence_dir" in inspect.signature(
        R._sdr_candidate_signoff_clean).parameters else {}
    ok, reason = R._sdr_candidate_signoff_clean(
        project, "chip_top", pdk, "", **kwargs)
    assert not ok
    assert reason.startswith(expected), reason
    if case == "missing":
        # it names the report it looked for: the step's own path, in the
        # shadow the candidate was signed off in
        assert reason.startswith("drc_report_missing:")
        assert reason.endswith(str(Path("phase3") / "reports" / "drc.rpt"))
    else:
        assert (evidence / "candidate_drc_signoff.rpt").read_text() == \
            written["drc"]


def test_sdr_child_restores_wellties_before_refilling_new_buffer_rows():
    pdk = SimpleNamespace(tapcell_master="TIE", tapcell_distance_um=20)
    tie = R._build_welltie_coverage_repair_tcl(pdk, 20, "declared pitch")
    remove, refill = R._postroute_filler_bracket_from_spec("SDR", {
        "filler_masters": ["FILL"], "welltie_repair_tcl": tie})
    assert "remove_fillers" in remove
    assert "WELLTIE_COVERAGE_REPAIR:" in refill
    assert refill.startswith("# === well-tie coverage repair")
    assert refill.index("WELLTIE_COVERAGE_REPAIR:") < refill.rindex(
        "filler_placement")


def test_sdr_admission_requires_both_clean_drc_and_matching_lvs(
        tmp_path, monkeypatch):
    project = tmp_path / "design"
    layout = project / "phase3" / "stage3" / "pnr" / "chip_top.gds"
    report = project / "phase3" / "reports" / "drc.rpt"
    verdict = project / "reports" / "phase3" / "lvs_verdict.json"
    monkeypatch.setattr(R.time, "time_ns", lambda: 1)
    _routed_basis(monkeypatch, project)

    def gds(*args, **kwargs):
        out = R._pl.pnr_dir(Path(args[0])) / "chip_top.gds"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"candidate")
        return R.StepResult("gds", "PASS")

    def drc(*args, **kwargs):
        rpt = Path(args[0]) / "phase3" / "reports" / "drc.rpt"
        rpt.parent.mkdir(parents=True, exist_ok=True)
        rpt.write_bytes(require_repo(
            "vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
            "tests", "fixtures", "drc_native_input_binding",
            "zero.xml").read_bytes())
        return R.StepResult("drc", "PASS")

    def lvs(*args, **kwargs):
        out = Path(args[0]) / "reports" / "phase3" / "lvs_verdict.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"status": "PASS"}))
        return R.StepResult("lvs", "PASS")

    monkeypatch.setattr(R, "step_gds", gds)
    monkeypatch.setattr(R, "step_drc", drc)
    monkeypatch.setattr(R, "step_lvs", lvs)
    assert R._sdr_candidate_signoff_clean(
        project, "chip_top", SimpleNamespace(drc_deck="deck.drc"), "") == \
        (True, "deck_drc_zero_lvs_match")


def test_sdr_transaction_records_the_refused_rules_and_report(
        tmp_path, monkeypatch):
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
    (txn / "receipt.tsv").write_text(
        "status\treason\tbefore_router_drc\tafter_router_drc\n"
        "ACCEPTED\trouter_drc_preserved_clean\t0\t0\n")
    report = project / "phase3" / "reports" / "drc.rpt"
    monkeypatch.setattr(R.time, "time_ns", lambda: 1)
    _routed_basis(monkeypatch, project)

    def eda_writes(container, cmd, **kwargs):
        (out / "chip_top.def").write_text(
            "INCUMBENT\n" if "pnr_sdr_reject_" in cmd else "CANDIDATE\n")
        return 0, "OpenROAD completed\n", ""

    drc_text = ("<report-database><items><item>"
                "<category>'DF.13_MV'</category>"
                "</item></items></report-database>")

    def gds(*args, **kwargs):
        dst = R._pl.pnr_dir(Path(args[0])) / "chip_top.gds"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"candidate layout")
        return R.StepResult("gds", "PASS")

    def drc(*args, **kwargs):
        rpt = Path(args[0]) / "phase3" / "reports" / "drc.rpt"
        rpt.parent.mkdir(parents=True, exist_ok=True)
        rpt.write_text(drc_text)
        return R.StepResult("drc", "FAIL")

    monkeypatch.setattr(R, "_docker_exec", eda_writes)
    monkeypatch.setattr(R, "step_gds", gds)
    monkeypatch.setattr(R, "step_drc", drc)
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={txn / R._SDR_CANDIDATE_DEF_NAME}\n")
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out), pnr_tcl=deck,
        log_text=log, hard_ceiling_s=60, project=project,
        top="chip_top", pdk=SimpleNamespace(drc_deck="deck.drc"))
    assert rec["status"] == "REJECTED", rec
    assert rec["reason"] == "signoff_refused:drc_fail:DF.13_MV"
    assert rec["adoptions"][0]["signoff_admission"] == "drc_fail:DF.13_MV"
    assert rec["adoptions"][0]["signoff_drc_report"] == str(
        txn / "candidate_drc_signoff.rpt")
    assert (txn / "candidate_drc_signoff.rpt").read_text() == drc_text
    assert "signoff_drc_fail:DF.13_MV" in (txn / "receipt.tsv").read_text()
    assert (out / "chip_top.def").read_text() == "INCUMBENT\n"


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
