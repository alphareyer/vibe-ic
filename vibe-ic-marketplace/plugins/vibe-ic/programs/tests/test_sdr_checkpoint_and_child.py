"""#2253 — the CHILD deck, and the Python half that adopts an accepted candidate.

The Tcl decision is pinned by `test_postroute_sdr_transaction.py`.  What is
pinned here is the OTHER two halves of the same transaction:

  * the CHILD deck is derived from pnr.tcl ITSELF (never re-emitted from a
    second builder), reads the checkpoint the shipping session wrote, elides
    the work that checkpoint already contains, and STOPS at the end of its own
    SDR stage — so it can publish a candidate and never a shipped artifact;
  * the PYTHON side finishes the post-route tail from an ACCEPTED candidate the
    shipping session was not allowed to read back, and refuses — loudly, with
    `routed.def` still absent — when it cannot.
"""
import shutil
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pnr_tcl_stub import STUB as _STUB  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

SITE1 = "postroute_drv_repair"
SITE2 = "postroute_drv_reconverge"


def _pdk() -> "R.PdkConfig":
    return R.PdkConfig(
        name="fixture_pdk",
        liberty="/pdk/lib.lib", tech_lef="/pdk/tech.lef",
        cell_lef="/pdk/cells.lef", cell_gds=None,
        site="unithd", drc_deck=None, metal_prefix="met",
        tapcell_master="sky130_fd_sc_hd__tapvpwrvgnd_1",
        antenna_diode_cell="sky130_fd_sc_hd__diode_2",
        pnr_exclude_cell_file="/pdk/drc_exclude.cells",
    )


def _stage_captable(tmp_path: Path) -> str:
    (tmp_path / "pdk" / "libs.ref" / "fix").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pdk" / "libs.tech" / "openlane").mkdir(parents=True,
                                                        exist_ok=True)
    (tmp_path / "pdk" / "libs.tech" / "openlane" /
     "rules.openrcx.fix.nom.magic").write_text("# captable fixture\n")
    return str(tmp_path / "pdk" / "libs.ref" / "fix" / "tech.lef")


def _full_pnr_tcl(tmp_path: Path) -> str:
    """The COMPLETE pnr.tcl, from its REAL builder, with the fork post-route
    repair probe POSITIVE so both SDR sites are emitted."""
    pdk = _pdk()
    out_dir_c = str(tmp_path / "out")
    (tmp_path / "out").mkdir(exist_ok=True)
    tech_lef_c = _stage_captable(tmp_path)
    plan = R._build_spare_cells_plan(2000, 0.02, (10, 10, 290, 290),
                                     liberty_path="", container="")
    antenna = R._antenna_repair_tcl(pdk)
    return R._build_pnr_tcl_text(
        tech_lef_c=tech_lef_c, cell_lef_c="/pdk/cells.lef",
        macro_lefs_tcl="", liberty_c="/pdk/lib.lib",
        macro_libs_tcl="", netlist_c="/work/netlist.v", top="chip_top",
        sdc_c="/work/chip_top.sdc",
        dont_use_block=R._dont_use_tcl(pdk),
        metal_prefix=pdk.metal_prefix, die_w=300, die_h=300,
        core_pad=10, core_w=280, core_h=280, site=pdk.site,
        out_dir_c=out_dir_c,
        tapcell_block=R._build_tapcell_tcl(pdk),
        pdn_block=R._build_pdn_tcl(pdk),
        util=0.45,
        spare_protection_tcl=R._build_spare_protection_tcl(plan, out_dir_c),
        spare_postfix_tcl=R._build_spare_postfix_tcl(
            plan, tie_lo_cell="sky130_fd_sc_hd__conb_1", tie_lo_pin="LO"),
        clk_buf="sky130_fd_sc_hd__clkbuf_4",
        clk_buf_root="sky130_fd_sc_hd__clkbuf_16",
        routing_constraint_tcl="",
        pg_cleanup_block=R._pg_net_cleanup_tcl(),
        spef_repair_block=R._post_route_spef_repair_tcl(
            out_dir_c, tech_lef_c, fork_repair_capable=True),
        antenna_repair_block=antenna,
        drv_reconverge_block=(
            R._pnr_stage_begin(SITE2) + "\n"
            + 'puts "SDR2_BEGIN"\n'
            + R._v1_8_100_signoff_drv_repair_tcl(
                out_dir_c, "sky130_fd_sc_hd__clkbuf_16", stage=SITE2)
            + 'puts "SDR2_END"\n'
            + R._pnr_stage_end(SITE2) + "\n"),
        post_reconverge_antenna_block=antenna,
        filler_block="",
    )


# ------------------------------------------------------------- the child deck


def _run_child_deck(tmp_path: Path, stage: str):
    """RUN the derived child deck in tclsh and report what it wrote.

    Behavioural, not textual: the claim is that the child publishes a
    candidate and then STOPS — and the only honest way to show a deck stops
    where it says it does is to run it and look at what came out.
    `write_def` is made real so the artifacts a shipping session would leave
    are visible if the child ever reached them.
    """
    import subprocess
    deck = _full_pnr_tcl(tmp_path)
    ckpt = tmp_path / f"ckpt_{stage}.def"
    ckpt.write_text("CHECKPOINT\n")
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(ckpt), stage=stage)
    real_write = (
        "proc write_def {path} {\n"
        "  file mkdir [file dirname $path]\n"
        "  set f [open $path w]; puts -nonewline $f DEF; close $f\n"
        "  lappend ::WROTE $path\n"
        "}\n"
        "proc write_verilog {path} { lappend ::WROTE $path }\n"
        "set ::WROTE {}\n")
    script = tmp_path / f"child_{stage}.tcl"
    script.write_text(_STUB + real_write + child)
    r = subprocess.run([tclsh, str(script)], text=True, capture_output=True)
    return r, tmp_path / "out"


@needs_tclsh
def test_child_deck_publishes_a_candidate_and_then_stops(tmp_path):
    """The child runs its own SDR pass and ends there — it never ships."""
    r, out = _run_child_deck(tmp_path, SITE1)
    assert r.returncode == 0, r.stderr
    txn = out / R._SDR_TXN_DIRS[SITE1]
    # it published exactly what the parent decides on
    assert (txn / R._SDR_CANDIDATE_DEF_NAME).is_file()
    assert (txn / R._SDR_CHILD_RECEIPT_NAME).is_file()
    assert "SDR_CHILD_DONE" in r.stdout
    # ... and NOTHING a shipping session would leave behind
    assert not (out / "routed.def").exists()
    assert not (out / "chip_top.def").exists()
    assert "SDR2_BEGIN" not in r.stdout
    # the receipt names the measurements, and never invents a placement 0
    row = (txn / R._SDR_CHILD_RECEIPT_NAME).read_text().splitlines()[1]
    assert len(row.split("\t")) == 4


@needs_tclsh
def test_child_deck_restores_the_checkpoint_instead_of_rebuilding(tmp_path):
    """The child reads the route the shipping session just wrote to disk."""
    deck = _full_pnr_tcl(tmp_path)
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c="/out/sdr_transaction/pre_repair.def",
        stage=SITE1)
    body = [ln for ln in child.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert f'set {R._SDR_ROLE_VAR} "child"' in body
    assert "read_def /out/sdr_transaction/pre_repair.def" in body
    assert not [ln for ln in body if ln.startswith("read_verilog ")]
    assert not [ln for ln in body if ln.startswith("link_design ")]
    # the work the checkpoint already contains is gone
    assert not [ln for ln in body if "global_placement" in ln]
    assert not [ln for ln in body if "clock_tree_synthesis" in ln]
    # the context the repair needs is still there
    assert [ln for ln in body if ln.startswith("read_lef ")]
    assert [ln for ln in body if ln.startswith("read_sdc ")]


@needs_tclsh
def test_child_deck_for_the_second_site_omits_what_its_checkpoint_holds(
        tmp_path):
    """The SDR2 checkpoint is written AFTER antenna repair and after SDR1.

    Re-running either in the child would make the candidate differ from the
    checkpoint by geometry THIS pass did not produce, and the accept/reject
    decision would be crediting the pass with somebody else's work.
    """
    r, out = _run_child_deck(tmp_path, SITE2)
    assert r.returncode == 0, r.stderr
    assert "PNR_STAGE_OMITTED: postroute_drv_repair" in r.stdout
    assert "PNR_STAGE_OMITTED: postroute_antenna_repair" in r.stdout
    assert "SDR2_BEGIN" in r.stdout
    txn = out / R._SDR_TXN_DIRS[SITE2]
    assert (txn / R._SDR_CANDIDATE_DEF_NAME).is_file()
    assert (txn / R._SDR_CHILD_RECEIPT_NAME).is_file()
    # the FIRST site's transaction directory is untouched by this child
    assert not (out / R._SDR_TXN_DIRS[SITE1]).exists()
    assert not (out / "routed.def").exists()


def test_child_deck_refuses_a_stage_that_is_not_an_sdr_site(tmp_path):
    deck = _full_pnr_tcl(tmp_path)
    with pytest.raises(R.PnrResumeUnavailable):
        R._build_pnr_sdr_child_tcl_text(
            deck, checkpoint_def_c="/c.def", stage="postroute_fill")


def test_child_deck_refuses_a_deck_with_no_stage_end(tmp_path):
    with pytest.raises(R.PnrResumeUnavailable):
        R._build_pnr_sdr_child_tcl_text(
            "read_verilog x\nlink_design y\n", checkpoint_def_c="/c.def",
            stage=SITE1)


@needs_tclsh
@pytest.mark.parametrize("stage", [SITE1, SITE2])
def test_child_deck_is_valid_tcl(tmp_path, stage):
    """A deck the parent `exec`s and cannot debug must parse.

    Same tclsh harness the full-template syntax test uses: every tool command
    is a no-op while the PARSER sees the real structure.
    """
    import subprocess
    deck = _full_pnr_tcl(tmp_path)
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c=str(tmp_path / "ckpt.def"), stage=stage)
    (tmp_path / "ckpt.def").write_text("CHECKPOINT\n")
    script = tmp_path / f"child_{stage}.tcl"
    script.write_text(_STUB + child.replace("\nexit\n", "\nputs CHILD_END\n"))
    r = subprocess.run([tclsh, str(script)], text=True, capture_output=True)
    assert "missing close-bracket" not in r.stderr
    assert r.returncode == 0, r.stderr


def test_the_two_sites_write_their_children_to_distinct_decks(tmp_path):
    assert (R._sdr_child_tcl_name(SITE1) != R._sdr_child_tcl_name(SITE2))
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    failures = R._write_sdr_child_decks(out / "pnr.tcl", out, container="")
    assert failures == {}
    for stage in (SITE1, SITE2):
        deck = (out / R._sdr_child_tcl_name(stage))
        assert deck.is_file()
        assert R._SDR_TXN_DIRS[stage] in deck.read_text()


def test_a_deck_whose_child_cannot_be_derived_is_disclosed_not_invented(
        tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text("read_verilog a\nlink_design b\n")
    failures = R._write_sdr_child_decks(out / "pnr.tcl", out, container="")
    assert set(failures) == set(R._SDR_CHILD_OMIT)
    for stage in R._SDR_CHILD_OMIT:
        assert not (out / R._sdr_child_tcl_name(stage)).exists()


# ------------------------------------------------------ the python adopt half


def test_adopt_is_not_requested_when_the_session_shipped_normally(tmp_path):
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=tmp_path, out_dir_c=str(tmp_path),
        pnr_tcl=tmp_path / "pnr.tcl",
        log_text="SDR_TRANSACTION_REJECTED_CANDIDATE_DISCARDED: reason=x\n",
        hard_ceiling_s=60)
    assert rec["status"] == "NOT_REQUESTED"
    assert rec["rc"] == 0


def test_adopt_refuses_when_the_named_candidate_does_not_exist(tmp_path):
    """A session that accepted and stopped, with no candidate on disk, must
    NOT be quietly treated as a run that shipped."""
    (tmp_path / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def=/out/{R._SDR_TXN_DIRS[SITE1]}/{R._SDR_CANDIDATE_DEF_NAME} "
           f"report=/out/x.rpt txn={R._SDR_TXN_DIRS[SITE1]}\n")
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=tmp_path, out_dir_c=str(tmp_path),
        pnr_tcl=tmp_path / "pnr.tcl", log_text=log, hard_ceiling_s=60)
    assert rec["status"] == "FAILED"
    assert rec["rc"] == 1
    assert "does not exist" in rec["reason"]


def test_adopt_refuses_an_unknown_stage_rather_than_guessing(tmp_path):
    (tmp_path / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    log = (f"{R._SDR_ADOPT_MARKER} stage=postroute_fill def=/x.def\n")
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=tmp_path, out_dir_c=str(tmp_path),
        pnr_tcl=tmp_path / "pnr.tcl", log_text=log, hard_ceiling_s=60)
    assert rec["status"] == "FAILED"
    assert "postroute_fill" in rec["reason"]


def test_adopt_tail_is_the_shipped_resume_transform_with_the_stage_omitted(
        tmp_path, monkeypatch):
    """The tail that ships an accepted candidate is pnr.tcl re-entered from
    that candidate — one transform, the same one the fatal-signal resume
    uses, so there is no second copy of the post-route tail in the tree."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE DEF\n")
    (txn / R._SDR_CANDIDATE_DRC_NAME).write_text("candidate report\n")

    calls = []

    def _fake_exec(container, cmd, **kw):
        calls.append(cmd)
        return 0, "tail ran\n", ""

    monkeypatch.setattr(R, "_docker_exec", _fake_exec)
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={txn / R._SDR_CANDIDATE_DEF_NAME} "
           f"report={txn / R._SDR_CANDIDATE_DRC_NAME} "
           f"txn={R._SDR_TXN_DIRS[SITE1]}\n")
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=log, hard_ceiling_s=60)
    assert rec["status"] == "ADOPTED"
    assert rec["omitted_stages"] == [SITE1]
    assert len(calls) == 1
    tail = (out / "pnr_sdr_adopt_1.tcl").read_text()
    assert f"read_def {txn / R._SDR_CANDIDATE_DEF_NAME}" in tail
    assert f"PNR_STAGE_OMITTED: {SITE1}" in tail
    # the tail DOES ship: it is the post-route tail, not another candidate run
    assert "routed.def" in tail
    # the candidate's own router report becomes the run's route report,
    # because it is the geometry that now ships
    assert (out / R.ROUTER_DRC_REPORT_NAME).read_text() == "candidate report\n"
    # the adopt transcript is folded into the log every gate reads
    assert "PNR SDR ADOPT" in (out / "openroad.log").read_text()


def test_adopt_is_bounded_by_the_number_of_sdr_sites(tmp_path, monkeypatch):
    """Each site can hand off at most once: the deck that runs after an
    adoption has that stage omitted, so it cannot reach its handoff again."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    for stage in (SITE1, SITE2):
        txn = out / R._SDR_TXN_DIRS[stage]
        txn.mkdir(parents=True)
        (txn / R._SDR_CANDIDATE_DEF_NAME).write_text(f"CANDIDATE {stage}\n")

    seq = [SITE2, SITE1]

    def _fake_exec(container, cmd, **kw):
        # every tail claims ANOTHER adoption; the bound is what stops it
        nxt = seq.pop(0) if seq else SITE1
        return 0, (f"{R._SDR_ADOPT_MARKER} stage={nxt} "
                   f"def={out / R._SDR_TXN_DIRS[nxt] / R._SDR_CANDIDATE_DEF_NAME}"
                   "\n"), ""

    monkeypatch.setattr(R, "_docker_exec", _fake_exec)
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={out / R._SDR_TXN_DIRS[SITE1] / R._SDR_CANDIDATE_DEF_NAME}\n")
    rec = R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=log, hard_ceiling_s=60)
    assert rec["status"] == "ADOPTED"
    assert len(rec["adoptions"]) <= len(R._SDR_CHILD_OMIT)
    assert rec["omitted_stages"] == [SITE1, SITE2]


# ---------------------------------------------------------- the disclosure


def test_a_refused_candidate_is_disclosed_where_a_reader_will_look(tmp_path):
    """A survivable event that lives only in openroad.log is one nobody reads.

    Before #2253 a refused candidate announced itself by taking the run down.
    Now it is survivable — which is the point — so it has to be published.
    """
    project = tmp_path / "proj"
    out = project / "pnr"
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / "receipt.tsv").write_text(
        "status\treason\tbefore_router_drc\tafter_router_drc\n"
        "REJECTED_CANDIDATE_DISCARDED\trouter_drc_not_strictly_improved\t0\t2\n")
    recs = R._disclose_sdr_transactions(project, out, [], {})
    by_stage = {r["stage"]: r for r in recs}
    assert by_stage[SITE1]["status"] == "REJECTED_CANDIDATE_DISCARDED"
    assert by_stage[SITE1]["before_router_drc"] == "0"
    assert by_stage[SITE1]["after_router_drc"] == "2"
    # a site with NO receipt did not run -- a different fact from "refused"
    assert by_stage[SITE2]["status"] == "NOT_RUN"
    published = project / "reports" / "phase3" / "sdr_transactions.json"
    assert published.is_file()
    import json
    payload = json.loads(published.read_text())
    assert payload["transactions"] == recs
    assert "checkpoint-and-child" in payload["note"]


def test_a_site_whose_child_deck_was_never_written_says_so(tmp_path):
    project = tmp_path / "proj"
    out = project / "pnr"
    out.mkdir(parents=True)
    recs = R._disclose_sdr_transactions(
        project, out, [], {SITE1: "pnr.tcl carries no begin marker"})
    by_stage = {r["stage"]: r for r in recs}
    assert by_stage[SITE1]["child_deck_not_written"]
    assert "child_deck_not_written" not in by_stage[SITE2]


def test_the_adopt_tail_asks_the_router_for_its_own_report(tmp_path,
                                                           monkeypatch):
    """The tail reroutes (antenna, the second site, named-violation), and the
    shipped geometry must not be described by a report written before any of
    them.  `_vic_drc_opt` is set inside the region the resume transform elides,
    so the tail has to re-ask."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    txn = out / R._SDR_TXN_DIRS[SITE1]
    txn.mkdir(parents=True)
    (txn / R._SDR_CANDIDATE_DEF_NAME).write_text("CANDIDATE\n")
    monkeypatch.setattr(R, "_docker_exec",
                        lambda container, cmd, **kw: (0, "", ""))
    log = (f"{R._SDR_ADOPT_MARKER} stage={SITE1} "
           f"def={txn / R._SDR_CANDIDATE_DEF_NAME}\n")
    R._pnr_adopt_sdr_candidates(
        container="", out_dir=out, out_dir_c=str(out),
        pnr_tcl=out / "pnr.tcl", log_text=log, hard_ceiling_s=60)
    tail = (out / "pnr_sdr_adopt_1.tcl").read_text()
    assert f"-output_drc {out}/{R.ROUTER_DRC_REPORT_NAME}" in tail
    # and it is asked BEFORE anything in the tail can reroute
    assert tail.index("_vic_drc_opt") < tail.index("repair_antennas")
