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
    # R-0915-18: the RESTORE POINT is the candidate ODB; the DEF beside it is
    # the disclosure artefact. Both exist on the happy path.
    (txn / R._SDR_CANDIDATE_ODB_NAME).write_bytes(b"CANDIDATE ODB\n")
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
    # R-0915-18: restored from the ODB, not the DEF — the tail routes again
    # and only the ODB carries the guides and dont_touch across the process
    # boundary. The DEF must NOT be the restore point any more.
    assert f"read_db {txn / R._SDR_CANDIDATE_ODB_NAME}" in tail
    assert f"read_def {txn / R._SDR_CANDIDATE_DEF_NAME}" not in tail
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
        (txn / R._SDR_CANDIDATE_ODB_NAME).write_bytes(
            f"CANDIDATE ODB {stage}\n".encode())

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
    (txn / R._SDR_CANDIDATE_ODB_NAME).write_bytes(b"CANDIDATE ODB\n")
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


# -------------------------------- session state the DEF does not carry


_SPARE_PLAN = {"instances": [
    {"name": "spare_inv_0", "cell": "sky130_fd_sc_hd__inv_2", "llx": 0, "lly": 0},
    {"name": "spare_nand_1", "cell": "sky130_fd_sc_hd__nand2_1", "llx": 8, "lly": 0},
    {"name": "spare_noclass_2", "cell": None, "llx": 16, "lly": 0},
]}


def test_a_restored_session_reasserts_the_spare_protection():
    """MEASURED on the first real run of the child (sha256 x sky130A): the
    shipping session cleared routing with `spare_preserved=236` and the CHILD
    with `spare_preserved=0`.

    The spare INSTANCES come back with the checkpoint DEF; the `dont_touch`
    ATTRIBUTE does not, because it is session state, and the block that sets it
    lives inside the region a checkpoint-seeded deck elides.  A restored
    session was therefore free to resize, rebuffer and rip up the very pool
    design-for-ECO exists to preserve.
    """
    tcl = R._spare_reassert_dont_touch_tcl(_SPARE_PLAN)
    assert "set_dont_touch spare_inv_0" in tcl
    assert "set_dont_touch spare_nand_1" in tcl
    # an entry with no PDK cell was never physically inserted, so there is
    # nothing in the DEF to protect and nothing is claimed about it
    assert "spare_noclass_2" not in tcl
    assert "SPARE_DONTTOUCH_REASSERTED: $_spare_reasserted of 2" in tcl
    # a name the checkpoint does not carry is DISCLOSED, never fatal: the DEF
    # is the authority on what exists, not the plan
    assert "SPARE_DONTTOUCH_REASSERT_NONFATAL" in tcl


def test_a_design_with_no_physical_spares_emits_nothing():
    """A design that plans no spares must produce a byte-identical deck."""
    for plan in (None, {}, {"instances": []},
                 {"instances": [{"name": "x", "cell": None}]}):
        assert R._spare_reassert_dont_touch_tcl(plan) == ""


def test_every_checkpoint_seeded_deck_carries_the_reassertion(tmp_path):
    """Child deck, adopt tail and fatal-signal resume are all restored
    sessions, and all three mutate.  None of them may run without it."""
    deck = _full_pnr_tcl(tmp_path)
    reassert = R._spare_reassert_dont_touch_tcl(_SPARE_PLAN)
    for stage in (SITE1, SITE2):
        child = R._build_pnr_sdr_child_tcl_text(
            deck, checkpoint_def_c="/c.def", stage=stage,
            after_restore_tcl=reassert)
        assert "set_dont_touch spare_inv_0" in child
        # and it is in force BEFORE anything that could touch a spare
        assert child.index("set_dont_touch spare_inv_0") < child.index(
            "repair_design")
    tail = R._build_pnr_resume_tcl_text(
        deck, checkpoint_def_c="/c.def", omit_stages=[SITE1],
        after_restore_tcl=reassert)
    assert "set_dont_touch spare_inv_0" in tail
    assert tail.index("read_def /c.def") < tail.index("set_dont_touch spare_inv_0")


def test_the_reassertion_is_absent_when_no_plan_is_supplied(tmp_path):
    """The default keeps a caller that passes nothing byte-identical."""
    deck = _full_pnr_tcl(tmp_path)
    a = R._build_pnr_sdr_child_tcl_text(deck, checkpoint_def_c="/c.def",
                                        stage=SITE1)
    b = R._build_pnr_sdr_child_tcl_text(deck, checkpoint_def_c="/c.def",
                                        stage=SITE1, after_restore_tcl="")
    assert a == b
    assert "SPARE_DONTTOUCH_REASSERTED" not in a


def test_the_child_deck_writer_puts_the_plan_in_both_decks(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "pnr.tcl").write_text(_full_pnr_tcl(tmp_path))
    assert R._write_sdr_child_decks(out / "pnr.tcl", out, container="",
                                    spare_plan=_SPARE_PLAN) == {}
    for stage in (SITE1, SITE2):
        assert "set_dont_touch spare_inv_0" in (
            out / R._sdr_child_tcl_name(stage)).read_text()


def test_the_disclosure_does_not_republish_the_log_it_already_folded(tmp_path):
    """MEASURED on the first real adopt run: `sdr_transactions.json` came out
    294 KB, because the adopt record carries the full tail transcript and the
    disclosure published it verbatim — a second copy of a log the same function
    had already appended to `openroad.log`.  The PATH to that log stays, so
    nothing becomes unreachable; only the duplicate goes."""
    import json
    project = tmp_path / "proj"
    out = project / "pnr"
    out.mkdir(parents=True)
    adoption = {"status": "ADOPTED", "rc": 0,
                "omitted_stages": [SITE1],
                "adoptions": [{"stage": SITE1, "log": "pnr_sdr_adopt_1.log"}],
                "combined_log": "X" * 300_000}
    R._disclose_sdr_transactions(project, out, [adoption], {})
    published = (project / "reports" / "phase3" / "sdr_transactions.json")
    payload = json.loads(published.read_text())
    assert "combined_log" not in payload["adoptions"][0]
    # what a reader needs to FIND the transcript is still there
    assert payload["adoptions"][0]["adoptions"][0]["log"] == "pnr_sdr_adopt_1.log"
    assert payload["adoptions"][0]["status"] == "ADOPTED"
    assert published.stat().st_size < 4096


# ---------------- R-0915-13: what a DEF restore gets WRONG, both halves


def test_a_rerouting_deck_drops_the_round_tripped_spare_wiring(tmp_path):
    """(A) MEASURED (sha256 run3/run4, the child's own transcript):
        SDR_ROUTING_CLEARED: 13805 (spare_preserved=236)
        [ERROR DRT-1010] Unsupported non-orthogonal wire ... on net
            spare_tielo_spare_dff_14
        SDR_DR_NONFATAL: DRT-1010  ->  route_ok=0  ->  REJECTED after=-1
    The wire is orthogonal IN THE DEF (`NEW met1 ( 109250 520710 ) ( * 521050 0 )`)
    and comes back from `read_def` diagonal. The shared clear returns PROTECTED
    for any net touching a dont_touch instance, so the router never gets to lay
    it again -- and refuses the whole design instead.
    """
    tcl = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=True)
    assert "SPARE_WIRING_RELAID" in tcl
    # the instances KEEP their protection -- that is what design-for-ECO needs
    assert "set_dont_touch spare_inv_0" in tcl
    # and the drop goes through the ONE filtered helper, never a second destroy
    assert "_vibeic_spare_safe_clear_net $_spare_n 1" in tcl
    assert tcl.count("odb::dbWire_destroy") == 1


def test_a_shipping_deck_keeps_that_wiring_and_gets_its_guides_instead(tmp_path):
    """(B) MEASURED (subservient, lane icsub2): the ADOPT TAIL restores the
    candidate DEF, every `detailed_route` after it fails DRT-0047 swallowed as
    PG_REROUTE_NONFATAL, and the post-route repair resizes NOTHING.  A DEF
    carries wires, not the global router's guides, and the deck's own
    `global_route` calls are all inside the elided region.

    This deck must NOT drop wiring: it does not necessarily detailed-route, so
    a dropped tie-off net would SHIP with no conductor.
    """
    tcl = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=False)
    assert "SPARE_WIRING_RELAID" not in tcl
    assert "odb::dbWire_destroy" not in tcl
    assert "set_dont_touch spare_inv_0" in tcl
    # The GUIDES half moved: a one-shot at restore bought exactly one routing
    # call, because `detailed_route` consumes them (subservient r8, DRT-0047
    # 819 lines after the re-establish). It is attached to the COMMANDS in the
    # pnr.tcl header now, so this deck gets it by construction -- asserted at
    # the deck in `test_the_discipline_is_in_the_header_so_restored_decks_inherit_it`.
    assert "global_route" not in tcl


def test_the_two_deck_kinds_are_exclusive_and_neither_is_empty():
    """Getting `reroutes_immediately` backwards is what each half of R-0915-13
    was, so the two shapes are pinned against each other."""
    child = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=True)
    ship = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=False)
    assert child != ship
    # the ONE thing that still differs here is the wiring relay: only a deck
    # that reroutes immediately may drop wiring, because only it lays it again
    assert ("SPARE_WIRING_RELAID" in child) and ("SPARE_WIRING_RELAID" not in ship)
    # guides are neither deck's business any more -- they are the commands'
    assert ("global_route" not in ship) and ("global_route" not in child)


@needs_tclsh
def test_the_relay_never_reaches_supply_wiring(tmp_path):
    """The PG skip is NOT the exception `_drop_protected` lifts.

    DRIVEN, not read: the proc is executed with the dont_touch skip dropped,
    once on a SIGNAL net and once on a POWER net. The signal wire goes; the
    supply wire must not, because a caller that could destroy PG wiring would
    take the straps out of the die it is about to ship. That is the one refusal
    in this filter with no opt-out, and the only honest way to show it is to
    ask for the exception and be refused.
    """
    import subprocess
    stub = (
        'namespace eval odb { proc dbWire_destroy {w} { lappend ::D $w } }\n'
        'set ::D {}\n'
        'proc SIG {m args} {\n'
        '  switch -- $m { getSigType {return SIGNAL} getITerms {return {IT}}\n'
        '                 getWire {return WSIG} }\n'
        '}\n'
        'proc PWR {m args} {\n'
        '  switch -- $m { getSigType {return POWER} getITerms {return {IT}}\n'
        '                 getWire {return WPWR} }\n'
        '}\n'
        'proc IT {m args} { if {$m eq "getInst"} { return INST } }\n'
        'proc INST {m args} { if {$m eq "isDoNotTouch"} { return 1 } }\n')
    drive = ('puts "SIG_FORCED=[_vibeic_spare_safe_clear_net SIG 1]"\n'
             'puts "PWR_FORCED=[_vibeic_spare_safe_clear_net PWR 1]"\n'
             'puts "SIG_DEFAULT=[_vibeic_spare_safe_clear_net SIG]"\n'
             'puts "DESTROYED=$::D"\n')
    s = tmp_path / "pg.tcl"
    s.write_text(stub + R._spare_safe_clear_net_proc_tcl() + drive)
    r = subprocess.run([tclsh, str(s)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    # the exception reaches a protected SIGNAL net ...
    assert "SIG_FORCED=CLEARED" in r.stdout, r.stdout
    # ... and never a supply net, however it is asked
    assert "PWR_FORCED=PG" in r.stdout, r.stdout
    # ... and the default caller is unchanged: still PROTECTED
    assert "SIG_DEFAULT=PROTECTED" in r.stdout, r.stdout
    assert "DESTROYED=WSIG" in r.stdout, r.stdout


def test_a_design_with_no_spares_still_gets_its_guides(tmp_path):
    """The SPARE relay is empty without spares; the guide discipline does not
    depend on them, because it is attached to the router commands and not to
    the spare plan.

    PREMISE UPDATED by R-0915-25, and tightened rather than relaxed. This used
    to assert that a spare-less child destroys no wire at all. That is no
    longer the design: a design with no spares can still carry EXTENSION nets
    -- on sha256 77 of the 101 are top-level IO, nothing to do with the spare
    pool -- and the child must be able to re-lay those. So what is pinned now
    is the property that actually matters and was only ever implied before:
    EXACTLY ONE destroy site, reached only from the extension census, and NO
    spare loop when there are no spares."""
    ship = R._after_restore_tcl("", None, reroutes_immediately=False)
    child = R._after_restore_tcl("", None, reroutes_immediately=True)
    assert "SPARE_WIRING_RELAID" not in child
    # never a second destroy site, with or without a spare plan
    assert child.count("odb::dbWire_destroy") == 1
    # and it is the EXTENSION census that reaches it, not a spare loop
    assert "EXT_WIRE_RELAID" in child
    assert "_vibeic_spare_safe_clear_net $_spare_n 1" not in child
    # a deck that may not re-route still destroys nothing at all
    assert "odb::dbWire_destroy" not in ship
    assert ship == "" or "SPARE" not in ship
    # and the guides are there for it regardless, in the deck
    deck = _full_pnr_tcl(tmp_path)
    assert "ROUTE_GUIDE_DISCIPLINE" in deck


@needs_tclsh
def test_the_relay_executes_and_reports_what_it_dropped(tmp_path):
    """DRIVEN, not read: the fragment runs under the odb stubs and reports the
    count, and a net it could not touch is DISCLOSED rather than silent."""
    import subprocess
    tcl = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=True)
    stub = (
        'namespace eval ord { proc get_db_block {} { return BLK } }\n'
        'namespace eval odb { proc dbWire_destroy {w} { lappend ::DESTROYED $w } }\n'
        'set ::DESTROYED {}\n'
        'proc BLK {m args} {\n'
        '  if {$m eq "findInst"} {\n'
        '    if {[lindex $args 0] eq "spare_nand_1"} { return NULL }\n'
        '    return INST\n'
        '  }\n'
        '}\n'
        'proc INST {m args} { if {$m eq "getITerms"} { return {IT} } }\n'
        'proc IT {m args} { if {$m eq "getNet"} { return NET } }\n'
        'proc NET {m args} {\n'
        '  switch -- $m {\n'
        '    getSigType { return SIGNAL }\n'
        '    getITerms  { return {IT} }\n'
        '    getWire    { return W }\n'
        '    getName    { return spare_tielo_x }\n'
        '  }\n'
        '}\n'
        'proc set_dont_touch {args} {}\n')
    s = tmp_path / "relay.tcl"
    s.write_text(stub + tcl + '\nputs "DESTROYED=[llength $::DESTROYED]"\n')
    r = subprocess.run([tclsh, str(s)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    # one spare resolves to an instance, one is absent from the DEF (NULL),
    # one has no PDK cell and was never in the plan's emitted list
    assert "SPARE_WIRING_RELAID: 1 net(s)" in r.stdout, r.stdout
    assert "DESTROYED=1" in r.stdout


# ------- the child clears its own residual before the transaction judges it


def _child_half(stage=SITE1, reserved=None):
    t = R._v1_8_100_signoff_drv_repair_tcl(
        "/o", "BUF", stage=stage, reserved_instance_names=reserved)
    return t.partition("# ===== PARENT ROLE")[0]


def test_the_child_runs_the_flows_own_reroute_on_its_own_residual():
    """MEASURED (sha256 run5): the child's repair took DRV 14,489 -> 250, its
    route closed (`route_ok=1`), and the transaction refused the candidate at
    router DRC 0 -> 2 -- correctly, as measured.  But the flow's OWN post-route
    remedy had never been aimed at that candidate: the parent runs the
    named-violation reroute after ITS route, and the child never did.

    So the child runs THE SAME loop, on ITS OWN report.  Not a second loop --
    the same emitter, so its bound, its re-measurement and its
    no-improvement stop cannot drift from the parent's.
    """
    child = _child_half()
    assert "SDR_CHILD_RESIDUAL_BEFORE_REROUTE" in child
    assert "SDR_CHILD_RESIDUAL_AFTER_REROUTE" in child
    # it is the FLOW'S loop, identified by that loop's own markers
    assert "NAMED_VIOL_REROUTE_PASS" in child
    assert "NAMED_VIOL_REROUTE_CLEAN" in child
    assert "NAMED_VIOL_REROUTE_NO_IMPROVEMENT" in child
    # aimed at the CHILD's report, never the shipped route's
    assert R._SDR_CANDIDATE_DRC_NAME in child
    assert f"/o/{R.ROUTER_DRC_REPORT_NAME}" not in child


def test_the_residual_reroute_is_gated_on_having_routed_at_all():
    """A child that never routed has no residual to clear and nothing to
    re-measure -- it must stay UNMEASURABLE, not be handed a reroute that
    would invent a report."""
    child = _child_half()
    i_gate = child.index("if {$_sdr_tx_mutated && $_sdr_tx_route_ok} {")
    i_loop = child.index("SDR_CHILD_RESIDUAL_BEFORE_REROUTE")
    assert i_gate < i_loop
    # and the reroute itself only runs when the residual is actually non-zero
    assert "if {$_sdr_cand_before > 0} {" in child


def test_the_decision_rule_is_untouched_by_the_residual_work():
    """#2240/#2247 stay exactly as they were: the PARENT still counts the
    candidate's report itself and still applies the same four outcomes. The
    child improving its candidate changes how GOOD the candidate is, never
    what the rule accepts."""
    parent = R._v1_8_100_signoff_drv_repair_tcl(
        "/o", "BUF", stage=SITE1).partition("# ===== PARENT ROLE")[2]
    for unchanged in ("router_drc_not_strictly_improved",
                      "unreadable_candidate_router_drc",
                      "nonfatal_or_route_error",
                      "router_drc_preserved_clean"):
        assert unchanged in parent
    # the parent does not reroute; improving the candidate is the child's job
    assert "NAMED_VIOL_REROUTE" not in parent


def test_the_counting_rule_has_one_definition_for_both_roles():
    """Parent and child both turn the router's report into a number. Two copies
    of "what counts as a violation, and what counts as unreadable" is the drift
    this file keeps paying for."""
    t = R._v1_8_100_signoff_drv_repair_tcl("/o", "BUF", stage=SITE1)
    # emitted in both halves (Tcl proc redefinition is idempotent) ...
    child, _, parent = t.partition("# ===== PARENT ROLE")
    assert "proc _sdr_tx_count_router_drc" in child
    assert "proc _sdr_tx_count_router_drc" in parent
    # ... from ONE emitter, so the bodies cannot differ
    proc = R._sdr_router_drc_count_proc_tcl()
    assert proc in R._postroute_sdr_transaction_begin_tcl("/o", SITE1)
    assert t.count("UNREADABLE_ROUTER_DRC_REPORT") == t.count(
        "proc _sdr_tx_count_router_drc")


def test_the_childs_reroute_protects_the_same_reserved_bindings(tmp_path):
    """The parent's post-route reroute is given the spares and spare pads as
    reserved; the child's must be given the same, or it can rip up a binding
    the parent holds immutable."""
    named = _child_half(reserved=["spare_inv_0", "spare_pad_3"])
    plain = _child_half(reserved=None)
    assert "spare_inv_0" in named and "spare_pad_3" in named
    assert "spare_inv_0" not in plain


# ------------- guides: detailed_route eats them, so each one gets its own


@needs_tclsh
def test_every_detailed_route_gets_guides_and_a_held_one_is_not_redone(tmp_path):
    """MEASURED (subservient r8): the adopt tail re-established guides at
    restore (`…:670`) and the PG reroute's `detailed_route` still died
    `[ERROR DRT-0047]` 819 lines later (`…:1489`), because `detailed_route`
    CONSUMES them. A one-shot at restore buys exactly ONE routing call.

    Both halves are driven here: a route with no guides gets them, and a route
    that still holds them is NOT re-global-routed — the second half is what
    keeps this from costing a global_route per detailed_route forever.
    """
    import subprocess
    script = (
        "proc detailed_route {args} { lappend ::DR $args }\n"
        "proc global_route {args} { incr ::GR }\n"
        "set ::GR 0\nset ::DR {}\n"
        + R._route_guide_discipline_tcl()
        + "detailed_route a\n"      # no guides yet -> must re-establish
        + "detailed_route b\n"      # previous one consumed them -> again
        + "global_route\n"          # explicit guides ...
        + "detailed_route c\n"      # ... so this one must NOT re-establish
        + 'puts "GR=$::GR DR=[llength $::DR]"\n')
    s = tmp_path / "guides.tcl"
    s.write_text(script)
    r = subprocess.run([tclsh, str(s)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert "ROUTE_GUIDE_DISCIPLINE_ARMED" in r.stdout
    # three routes happened, and the real command was reached every time
    assert "DR=3" in r.stdout, r.stdout
    # two auto re-establishes + the one explicit global_route = 3
    assert "GR=3" in r.stdout, r.stdout
    assert r.stdout.count("ROUTE_GUIDES_REESTABLISHED") == 2, r.stdout
    assert r.stdout.count("ROUTE_GUIDES_HELD") == 1, r.stdout


@needs_tclsh
def test_a_global_route_that_regenerated_nothing_is_not_announced_as_success(
        tmp_path):
    """MEASURED (subservient r10): `ROUTE_GUIDES_REESTABLISHED` at
    `pnr_sdr_adopt_1.log:1463` and `[ERROR DRT-0047]` 28 lines later at :1491 --
    `global_route` returned with no Tcl error and `detailed_route` still found
    no guides.

    This repo already records why: "OpenROAD's `global_route` NO-OPS on
    already-routed nets (regenerates 0 guides)". In a DEF-restored session
    every signal net IS already routed, so the call succeeds and produces
    nothing. Announcing REESTABLISHED there claims work that did not happen,
    and a reader who greps for it reads a fixed run.

    So the wrapper ASKS THE DATABASE. Both arms are driven: a design that has
    guides reports REESTABLISHED, a design that has none reports UNAVAILABLE
    and says why.
    """
    import subprocess
    common = (
        "proc detailed_route {args} { }\n"
        "proc global_route {args} { }\n"
        "namespace eval ord { proc get_db_block {} { return BLK } }\n")
    def _run(guides):
        blk = ('proc BLK {m args} { if {$m eq "getNets"} { return {N} } }\n'
               'proc N {m args} { if {$m eq "getGuides"} { return %s } }\n'
               % ("{g1 g2}" if guides else "{}"))
        s_ = tmp_path / f"g{guides}.tcl"
        s_.write_text(common + blk + R._route_guide_discipline_tcl()
                      + "detailed_route x\n")
        return subprocess.run([tclsh, str(s_)], text=True, capture_output=True)
    r_yes = _run(True)
    assert r_yes.returncode == 0, r_yes.stderr
    assert "ROUTE_GUIDES_REESTABLISHED" in r_yes.stdout
    assert "ROUTE_GUIDES_UNAVAILABLE" not in r_yes.stdout
    r_no = _run(False)
    assert r_no.returncode == 0, r_no.stderr
    assert "ROUTE_GUIDES_UNAVAILABLE" in r_no.stdout, r_no.stdout
    assert "ROUTE_GUIDES_REESTABLISHED" not in r_no.stdout
    # and it names the cause rather than just the symptom
    assert "already carries" in r_no.stdout and "DRT-0047" in r_no.stdout


@needs_tclsh
def test_a_build_that_cannot_answer_is_unknown_never_present(tmp_path):
    """-1 is not 0. A build that does not expose guides must not be reported as
    guide-less (which would print a cause that may be false), nor as fixed."""
    import subprocess
    s_ = tmp_path / "unknown.tcl"
    s_.write_text(
        "proc detailed_route {args} { }\nproc global_route {args} { }\n"
        + R._route_guide_discipline_tcl()
        + "detailed_route x\n")
    r = subprocess.run([tclsh, str(s_)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    # no ord::get_db_block at all -> -1 -> neither claim is made
    assert "ROUTE_GUIDES_UNAVAILABLE" not in r.stdout


@needs_tclsh
def test_an_interpreter_with_no_router_to_wrap_says_so(tmp_path):
    """DEGRADE LOUDLY. A silent skip reads downstream exactly like a deck whose
    routing calls all had guides."""
    import subprocess
    s = tmp_path / "noroute.tcl"
    s.write_text(R._route_guide_discipline_tcl())
    r = subprocess.run([tclsh, str(s)], text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert "ROUTE_GUIDE_DISCIPLINE_UNAVAILABLE" in r.stdout
    assert "ROUTE_GUIDE_DISCIPLINE_ARMED" not in r.stdout


def test_the_discipline_is_in_the_header_so_restored_decks_inherit_it(tmp_path):
    """It is attached to the COMMANDS, above the resume-elide region, so a
    checkpoint-seeded deck gets it by construction instead of by a second
    emitter remembering. The list-of-call-sites approach has been wrong twice."""
    deck = _full_pnr_tcl(tmp_path)
    i_disc = deck.index("ROUTE_GUIDE_DISCIPLINE")
    i_elide = deck.index(R._PNR_RESUME_ELIDE_BEGIN)
    assert i_disc < i_elide, "the discipline must survive the elision"
    for stage in (SITE1, SITE2):
        child = R._build_pnr_sdr_child_tcl_text(
            deck, checkpoint_def_c="/c.def", stage=stage)
        assert "ROUTE_GUIDE_DISCIPLINE" in child
    tail = R._build_pnr_resume_tcl_text(
        deck, checkpoint_def_c="/c.def", omit_stages=[SITE1])
    assert "ROUTE_GUIDE_DISCIPLINE" in tail


def test_the_one_shot_restore_time_global_route_is_gone():
    """It bought exactly one routing call. Keeping it as well would
    global_route once for nothing before the first detailed_route did it
    again."""
    ship = R._after_restore_tcl("", _SPARE_PLAN, reroutes_immediately=False)
    assert "RESTORED_ROUTE_GUIDES_REESTABLISHED" not in ship
    assert "global_route" not in ship


def test_the_childs_transcript_never_lands_in_the_parents_log():
    """MEASURED (sha256 run5): echoing the child's transcript into the parent's
    `openroad.log` made the PARENT'S ROUTE UNREADABLE --

        pnr FAIL ROUTE_DRC_NOT_MEASURED: route__drc_errors: METRIC=0 but LOG=5133

    The parent routed clean (`DRT-0702 … 0 violation(s)`, metric 0); 5133 was
    the CHILD's last in-loop DRT-0199 from its own repair passes, and the log
    scraper takes the LAST one. The gate refused to choose between the tool's
    metric and its log, which is exactly right -- preferring either side is how
    a measurement quietly becomes a guess -- and is why this was caught rather
    than published.

    `openroad.log` is the record of what THIS session routed. The child gets
    its own per-site file, and `SDR_CHILD_SESSION_DONE` names it.
    """
    parent = R._postroute_sdr_parent_child_call_tcl("/o", SITE1)
    exec_line = [l for l in parent.splitlines() if "exec openroad" in l]
    assert len(exec_line) == 1, exec_line
    line = exec_line[0]
    # PREMISE UPDATED by R-0915-26 and TIGHTENED. There are now TWO child
    # sessions per site -- the ODB leg and, only if that one is rejected, the
    # DEF leg -- so the single `exec` is a shared runner taking the log as an
    # argument, and the literal path lives at the CALL sites instead of on the
    # exec line. The property this test exists for is unchanged and is now
    # pinned harder: one exec site, redirected to a per-call file, and the two
    # legs must not share a log or one would overwrite the other's transcript.
    assert ">& $log" in line
    # redirected AWAY from the parent's stdout, not tee'd through it. Checked
    # positively as well as negatively: an earlier version of this guard looked
    # for the ABSENCE of ">&@ stdout" while the live form was ">@ stdout".
    assert ">@ stdout" not in line and ">&@ stdout" not in line
    assert "tee" not in line
    odb_log = f"/o/{R._sdr_child_log_name(SITE1)}"
    def_log = f"/o/{R._sdr_child_def_leg_log_name(SITE1)}"
    assert odb_log != def_log
    for want in (odb_log, def_log):
        assert f"_sdr_exec_child /o/" in parent
        assert want in parent, want
    # and the reader is still told where each transcript went
    assert "log=$log" in parent


# ------------------------- R-0915-16: the restore point is the ODB


def test_the_child_restores_from_the_odb_not_the_def(tmp_path):
    """MEASURED (R-0915-16, probes A–F on the real 14 MB checkpoint): a FRESH
    process `read_db`s it in under a second and gets back the design, the libs,
    the tech, the routing, the global router's GUIDES (14,041 nets) and the
    `dont_touch` attribute — and then runs `read_liberty`, `read_sdc`, STA,
    `estimate_parasitics` and `repair_design` on it.  A DEF carries none of the
    last three, which is what R-0915-13 was made of.
    """
    deck = _full_pnr_tcl(tmp_path)
    child = R._build_pnr_sdr_child_tcl_text(
        deck, checkpoint_def_c="/c/pre_repair.def", stage=SITE1,
        restore_odb_c="/c/pre_repair.odb")
    body = [ln for ln in child.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert "read_db /c/pre_repair.odb" in body
    assert not [ln for ln in body if ln.startswith("read_def ")]
    # the ODB carries libs + tech, so re-reading the LEFs would be a second
    # definition of the same library
    assert not [ln for ln in body if ln.startswith("read_lef ")]
    # timing libraries are NOT in the ODB and must still be read
    assert [ln for ln in body if ln.startswith("read_liberty ")]
    assert [ln for ln in body if ln.startswith("read_sdc ")]


def test_a_def_only_restore_is_refused_by_name_not_degraded(tmp_path):
    """The one thing that must never happen quietly: falling back to the DEF.
    That restore loses the guides, the dont_touch and the wire fidelity the
    transaction depends on — it IS the shape R-0915-13 was — so the parent
    refuses it, and says which file is missing and what it would have cost."""
    parent = R._postroute_sdr_parent_child_call_tcl("/o", SITE1)
    assert "SDR_CHECKPOINT_ODB_ABSENT" in parent
    assert "route guides" in parent and "dont_touch" in parent
    # refused through the SAME flags the tool's own numbers go through
    i = parent.index("SDR_CHECKPOINT_ODB_ABSENT")
    tail = parent[i:i + 900]
    assert "set _sdr_tx_error 1" in tail
    assert "set _sdr_tx_route_ok 0" in tail


def test_the_checkpoint_writes_the_odb_and_keeps_the_def(tmp_path):
    """The DEF stays: it is the human-readable disclosure artefact beside the
    restore point, and every existing consumer of `pre_repair.def` keeps it."""
    begin = R._postroute_sdr_transaction_begin_tcl("/o", SITE1)
    assert "write_def $_sdr_tx_dir/pre_repair.def" in begin
    assert "write_db $_sdr_tx_ckpt_odb" in begin
    assert f"/o/{R._SDR_TXN_DIRS[SITE1]}/{R._SDR_CHECKPOINT_ODB_NAME}" in begin
    # a checkpoint that cannot be written is DISCLOSED, never silent
    assert "SDR_CHECKPOINT_ODB_NONFATAL" in begin


def test_a_deck_with_no_odb_still_restores_from_the_def(tmp_path):
    """The fatal-signal resume has only a stage DEF checkpoint on disk, so the
    DEF path must remain exactly as it was for it — passing no ODB is not a
    silent downgrade there, it is the only checkpoint that exists."""
    deck = _full_pnr_tcl(tmp_path)
    tail = R._build_pnr_resume_tcl_text(
        deck, checkpoint_def_c="/c/routed_preantenna.def", omit_stages=[SITE1])
    body = [ln for ln in tail.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    assert "read_def /c/routed_preantenna.def" in body
    assert not [ln for ln in body if ln.startswith("read_db ")]
    # and it keeps its LEFs, because nothing restored them
    assert [ln for ln in body if ln.startswith("read_lef ")]
