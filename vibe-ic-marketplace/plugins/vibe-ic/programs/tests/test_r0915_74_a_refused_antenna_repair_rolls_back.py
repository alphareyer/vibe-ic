"""R-0915-74 — a refused antenna repair shipped an unverified route.

MEASURED, sha256 x sky130A, lane icsha2 run15 (main 385445351), front door.
The post-route antenna loop inserted 4 diodes (`[INFO GRT-0015] Inserted 4
diodes.`), the incremental `detailed_route` that must re-route their nets RAISED
(`REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010`) and the loop broke.  What that
aborted call had already written stayed in the database, was written to
`routed.def`, and NOTHING verified it again: the router's last
`[INFO DRT-0702] Post-route verification:` is at log line 1530 and the refusals
are at 1957 / 1987 / 2427 / 2457.

It was not cosmetic.  Traced across this run's own checkpoints, `net4772` does
not exist at all in `routed_preantenna.def`, exists in the SDR `candidate.def`
WITHOUT its y = 417.450 um met1 segment, and carries that segment in
`routed.def` — and that segment is the conductor magic's extractor then reported
as 22 illegal overlaps across three flip-flops.

THE SHAPE OF THE FIX is the one this repo already proved.  A mid-session
rollback is measured to be unavailable — this deck's own
`ANTENNA_LOOP_BEST_NOT_RESTORED` note records that `dbChip_destroy` + `read_db`
restores the routing and then kills the STA network the rest of the session runs
on (ORD-2008), against a control that survives without it.  So the session
CHECKPOINTS the verified route with `write_db` before it touches anything, and
on a refusal it asks the parent to re-enter the post-route tail from that
checkpoint in a FRESH session with both antenna stages omitted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import phase3_one_shot_runner as R


class _Pdk:
    antenna_diode_cell = "sky130_fd_sc_hd__diode_2"


def _tcl() -> str:
    return R._antenna_repair_tcl(_Pdk(), "/w/pnr")


def _code() -> str:
    """The emitted Tcl with its COMMENT LINES removed.

    Every assertion about what the deck DOES has to be made over what the deck
    RUNS. This block documents the measurement that forbids an in-session
    rollback by quoting `dbChip_destroy` + `read_db` in a comment, and a naive
    substring scan reads its own citation as the defect — a shape this repo has
    already paid for once."""
    return "\n".join(ln for ln in _tcl().splitlines()
                      if not ln.lstrip().startswith("#"))


# --------------------------------------------------------------------------
# (a) the session checkpoints BEFORE it mutates, and asks rather than restores
# --------------------------------------------------------------------------
def test_the_verified_route_is_checkpointed_before_the_loop():
    t = _code()
    assert f"set _ant_ckpt $_ant_dir/{R._ANTENNA_CHECKPOINT_NAME}" in t
    i_ck = t.index("write_db $_ant_ckpt")
    i_loop = t.index("for {set _i 0}")
    i_repair = t.index("repair_antennas")
    assert i_ck < i_loop < i_repair, (i_ck, i_loop, i_repair)


def test_the_checkpoint_name_is_stated_once():
    """A path literal is a join key: the Tcl writes it and the Python reads it,
    so a second spelling is a rollback that restores nothing."""
    t = _code()
    assert t.count(R._ANTENNA_CHECKPOINT_NAME) == 1
    src = Path(R.__file__).read_text()
    assert f'"{R._ANTENNA_CHECKPOINT_NAME}"' in src   # the constant itself


def test_a_failed_checkpoint_is_named_and_not_assumed():
    assert "ANTENNA_PRE_REPAIR_CHECKPOINT_FAILED" in _tcl()


# The window is per-marker and is the LENGTH OF THAT EXIT'S OWN MESSAGE, not a
# loosening: every marker below still has to set `_ant_refused` in the same
# breath as it prints. Only ANTENNA_DIODE_ROLLED_BACK needs more than 260
# characters, because its message names the nets, the diode count and why no
# route is run -- 408 characters before the assignment.
@pytest.mark.parametrize("marker,window", [
    ("ANTENNA_LOOP_CHECK_NONFATAL", 260),
    ("ANTENNA_NATIVE_REROUTE_NONFATAL", 260),
    # WAS "REPAIR_ANTENNA_NONFATAL", the no-`-reroute` retry inside the
    # degraded branch. R-0915-116(2)(iii) deleted that branch together with
    # the whole-design `detailed_route` it existed to feed, so the exit no
    # longer exists to record anything. The exit that REPLACED it -- the
    # connectivity judgement's broken verdict -- takes its place here, and it
    # is the one that actually leaves a mutated route behind: it has just
    # destroyed this pass's diodes.
    ("ANTENNA_DIODE_ROLLED_BACK", 600),
    # R-0915-114(b): this exit is no longer a note. The stage that inserted
    # the diodes now answers for their wires -- MEASURED (int6): the native
    # -reroute threw DRT-0206, the fallback threw DRT-1231 TWICE, both were
    # swallowed, and the diodes were left for the PG block's whole-design
    # re-route, which R-0915-114(a) deleted. R-0915-116(2)(iii) then deleted
    # this stage's own whole-design route, so the raise is judged instead.
])
def test_every_refusal_path_records_the_refusal(marker, window):
    """Each of the exits that leaves a mutated route sets `_ant_refused`.
    A path that only prints is a path whose route ships unrolled."""
    t = _code()
    i = t.index(f'puts "{marker}')
    seg = t[i:i + window]
    assert "set _ant_refused" in seg, seg


def test_the_retired_retry_marker_is_gone_from_the_emitted_deck():
    """The counterpart to the parametrization above: REPAIR_ANTENNA_NONFATAL
    was dropped from it because R-0915-116(2)(iii) removed the exit, not
    because the exit stopped recording its refusal. Pin the removal, so the
    branch cannot come back unrecorded."""
    t = _code()
    assert "REPAIR_ANTENNA_NONFATAL" not in t
    assert "detailed_route" not in t


def _invokes(cmd: str) -> bool:
    """Is `cmd` ever INVOKED by the deck — as the first word of a statement?

    Not a substring search. This block both DOCUMENTS the ORD-2008 measurement
    in a comment and REPORTS it in a `puts` (`ANTENNA_LOOP_BEST_NOT_RESTORED`),
    so a bare `in` reads the deck's own citation of the defect as the defect.
    """
    text = _code()
    for ch in "[]{}":
        text = text.replace(ch, " \n")
    for stmt in text.replace(";", "\n").splitlines():
        head = stmt.strip().split()
        if head and head[0] == cmd:
            return True
    return False


def test_the_session_asks_and_does_not_restore_in_place():
    """ORD-2008: `dbChip_destroy` + `read_db` restores the routing and then
    kills the STA network the rest of this session runs on. The antenna block
    must not attempt it — it asks the parent instead."""
    assert "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST" in _code()
    assert not _invokes("read_db")
    assert not _invokes("odb::dbChip_destroy")
    # the instrument works: the deck DOES invoke the checkpoint write
    assert _invokes("write_db")


def test_a_run_that_did_not_refuse_says_so_and_drops_the_checkpoint():
    """NEGATIVE CONTROL, and the one the ruling names: a reroute that COMPLETES
    is not rolled back."""
    t = _code()
    assert "ANTENNA_REPAIR_APPLIED: no repair or reroute refused" in t
    i = t.index("ANTENNA_REPAIR_APPLIED")
    assert "file delete -- $_ant_ckpt" in t[i:i + 200]


def test_the_emitted_antenna_block_is_valid_tcl(tmp_path: Path):
    """The whole block, through the resolver the runner uses.

    `tmp_path`, NOT a shared directory. `_tcl_walk.walk` writes `walk.tcl` and
    `deck.tcl` into whatever it is given, so handing it `/tmp` makes two
    concurrent runs of this suite overwrite each other's scripts — which is
    exactly what a two-arm falsifier does, and it turned this test red in the
    GREEN arm while a clean serial checkout passed 34 of 34.
    """
    # `import _tcl_walk`, NOT `from tests import _tcl_walk` (icfix2, #2434).
    # `programs/tests/` carries NO `__init__.py`, so it is not a package and
    # `tests` never names it. The pinned EDA image, however, DOES ship a
    # top-level one -- `/usr/local/lib/python3.12/dist-packages/tests/`,
    # containing nothing but an empty `__init__.py` -- so inside the image the
    # bare form resolved to THAT and died:
    #
    #   ImportError: cannot import name '_tcl_walk' from 'tests'
    #   (/usr/local/lib/python3.12/dist-packages/tests/__init__.py)
    #
    # pytest prepends this file's own directory to `sys.path`, which is why the
    # five other users of this helper all spell it `import _tcl_walk` and are
    # green. These two were the only ones spelling it the way that depends on
    # what the environment happens to have installed.
    import _tcl_walk  # noqa: PLC0415
    out, err, route = _tcl_walk.walk(
        "proc _vic_check {} {\n" + _tcl() + "\n}\nputs TCL_PARSE_OK\n",
        "# deck\n", tmp_path)
    assert "TCL_PARSE_OK" in out, (out, err, route)


# --------------------------------------------------------------------------
# the request parser — pure, and it reads the marker the refusal prints
# --------------------------------------------------------------------------
_REQ = ("ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: "
        "checkpoint=/w/pnr/antenna_pre_repair.odb "
        "reason=REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010")


def test_the_request_is_parsed_with_its_reason_intact():
    r = R.antenna_rollback_request(_REQ)
    assert r == {"checkpoint": "/w/pnr/antenna_pre_repair.odb",
                 "reason": "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010"}


def test_the_reason_may_carry_spaces():
    """A tool's error text is prose; truncating it at the first space would
    lose exactly the part a reader needs."""
    r = R.antenna_rollback_request(
        "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: checkpoint=/a/b.odb "
        "reason=child process exited abnormally while routing net foo")
    assert r["reason"] == ("child process exited abnormally while routing "
                           "net foo")


def test_the_last_request_wins():
    r = R.antenna_rollback_request(
        _REQ + "\n" + _REQ.replace("/w/pnr/", "/second/"))
    assert r["checkpoint"] == "/second/antenna_pre_repair.odb"


def test_a_log_with_no_refusal_yields_no_request():
    """NEGATIVE CONTROL."""
    assert R.antenna_rollback_request(
        "ANTENNA_REPAIR_APPLIED: no repair or reroute refused\n") is None
    assert R.antenna_rollback_request("") is None


def test_a_refusal_with_no_checkpoint_is_its_own_fact():
    """"the repair refused" and "the repair refused and the verified state was
    never written down" are different facts; only the second ships unverified."""
    log = "ANTENNA_REPAIR_REFUSED_NO_CHECKPOINT: reason=X -- ships UNVERIFIED"
    assert R.antenna_refused_without_checkpoint(log).startswith("reason=X")
    assert R.antenna_rollback_request(log) is None
    assert R.antenna_refused_without_checkpoint(_REQ) is None


# --------------------------------------------------------------------------
# the parent: what it does, and what it refuses to do
# --------------------------------------------------------------------------
def _call(tmp_path, log, *, make_ckpt=True, exec_rc=0, monkeypatch=None):
    out_dir = tmp_path / "pnr"
    out_dir.mkdir(parents=True, exist_ok=True)
    if make_ckpt:
        (out_dir / R._ANTENNA_CHECKPOINT_NAME).write_bytes(b"odb")
    deck = tmp_path / "pnr.tcl"
    deck.write_text("# deck\n")
    calls = []

    def fake_exec(container, cmd, **kw):
        calls.append(cmd)
        return exec_rc, "tail ran\n", ""

    def fake_build(_deck, **kw):
        calls.append(kw)
        return "# tail\n"

    monkeypatch.setattr(R, "_docker_exec", fake_exec)
    monkeypatch.setattr(R, "_build_pnr_resume_tcl_text", fake_build)
    monkeypatch.setattr(R, "_after_restore_tcl", lambda *a, **k: "")
    monkeypatch.setattr(R, "_route_drc_report_tcl", lambda *a, **k: "")
    rec = R._pnr_rollback_refused_antenna_repair(
        container="c", out_dir=out_dir, out_dir_c="/w/pnr", pnr_tcl=deck,
        log_text=log, hard_ceiling_s=60)
    return rec, calls, out_dir


def test_a_refusal_re_enters_the_tail_with_both_antenna_stages_omitted(
        tmp_path, monkeypatch):
    rec, calls, out_dir = _call(tmp_path, _REQ, monkeypatch=monkeypatch)
    assert rec["status"] == "ROLLED_BACK"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert rec["reason"] == "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010"
    kw = [c for c in calls if isinstance(c, dict)][0]
    assert list(kw["omit_stages"]) == ["postroute_antenna_repair",
                                       "postroute_antenna_reconverge"]
    # restored from the ODB, which is what carries guides + dont_touch
    assert kw["restore_odb_c"].endswith(R._ANTENNA_CHECKPOINT_NAME)
    assert (out_dir / "pnr_antenna_rollback.tcl").is_file()


def test_no_refusal_does_nothing_at_all(tmp_path, monkeypatch):
    """NEGATIVE CONTROL — the ruling's own: a reroute that completes is not
    rolled back, and no tail is built or run."""
    rec, calls, out_dir = _call(
        tmp_path, "ANTENNA_REPAIR_APPLIED: no repair or reroute refused\n",
        monkeypatch=monkeypatch)
    assert rec["status"] == "NOT_REQUESTED"
    assert calls == []
    assert not (out_dir / "pnr_antenna_rollback.tcl").exists()


def test_a_missing_checkpoint_is_refused_by_name_not_degraded(
        tmp_path, monkeypatch):
    rec, calls, _ = _call(tmp_path, _REQ, make_ckpt=False,
                          monkeypatch=monkeypatch)
    assert rec["status"] == "FAILED"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert rec["route_verified"] is False
    assert "does not exist" in rec["reason"]
    assert calls == []


def test_a_checkpoint_this_module_did_not_write_is_refused(
        tmp_path, monkeypatch):
    """A restore point named by a log line is still a restore point: it must be
    the file this module emits, not whatever the marker says."""
    rec, calls, _ = _call(
        tmp_path,
        "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: checkpoint=/w/pnr/other.odb "
        "reason=X", monkeypatch=monkeypatch)
    assert rec["status"] == "FAILED"
    assert "not antenna_pre_repair.odb" in rec["reason"].replace(
        "which is not ", "not ")
    assert calls == []


def test_a_failed_tail_is_not_papered_over(tmp_path, monkeypatch):
    rec, _, _ = _call(tmp_path, _REQ, exec_rc=1, monkeypatch=monkeypatch)
    assert rec["status"] == "FAILED"
    assert rec["route_verified"] is False


def test_a_refusal_with_no_checkpoint_never_runs_a_tail(tmp_path, monkeypatch):
    rec, calls, _ = _call(
        tmp_path, "ANTENNA_REPAIR_REFUSED_NO_CHECKPOINT: reason=X",
        monkeypatch=monkeypatch)
    assert rec["status"] == "NO_CHECKPOINT"
    assert rec["antenna_repair"] == "NOT_APPLIED"
    assert rec["route_verified"] is False
    assert calls == []


# --------------------------------------------------------------------------
# (b) the invariant at ship
# --------------------------------------------------------------------------
_VERIFIED = "[INFO DRT-0702] Post-route verification: 0 violation(s).\n"


def _doc(tmp_path, rec, log):
    R._disclose_antenna_rollback(tmp_path, tmp_path, [rec], log)
    p = tmp_path / "reports" / "phase3" / "antenna_repair_transaction.json"
    return json.loads(p.read_text())


def test_a_rolled_back_run_ships_a_verified_route(tmp_path):
    """The rollback restores the state the router verified, so the refusal that
    came after it is no longer describing the route that ships."""
    d = _doc(tmp_path,
             {"status": "ROLLED_BACK", "antenna_repair": "NOT_APPLIED",
              "reason": "DRT-1010", "route_verified": True},
             _VERIFIED)
    assert d["route_modified_after_last_verification"] is False
    assert d["route_verified_at_ship"] is True
    assert d["antenna_repair"] == "NOT_APPLIED"


def test_an_unrolled_refusal_says_the_route_is_unverified(tmp_path):
    """NEGATIVE CONTROL for the invariant: the run15 shape, where the refusal
    comes after the last verification and nothing rolled back."""
    log = _VERIFIED + "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n"
    d = _doc(tmp_path,
             {"status": "NO_CHECKPOINT", "antenna_repair": "NOT_APPLIED",
              "reason": "X", "route_verified": False}, log)
    assert d["route_modified_after_last_verification"] is True
    assert d["route_verified_at_ship"] is False


def test_the_record_says_the_violations_are_not_laundered(tmp_path):
    d = _doc(tmp_path, {"status": "ROLLED_BACK",
                        "antenna_repair": "NOT_APPLIED",
                        "route_verified": True}, _VERIFIED)
    assert "still fails it" in d["note"]
    assert d["ruling"] == "R-0915-74"


def test_nothing_is_written_when_there_is_no_record(tmp_path):
    """NEGATIVE CONTROL. An absent artefact and an artefact saying "nothing
    refused" are different answers; this writes neither out of nothing."""
    assert R._disclose_antenna_rollback(tmp_path, tmp_path, [], "") is None
    assert not (tmp_path / "reports" / "phase3"
                / "antenna_repair_transaction.json").exists()


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------
def test_the_rollback_runs_after_the_sdr_adopt():
    """The adopt tail RE-RUNS the antenna stage, so its refusal is the one that
    decides what ships; rolling back first would undo a route the adopt is
    about to replace."""
    src = Path(R.__file__).read_text()
    i_adopt = src.index("_sdr_adopt = _pnr_adopt_sdr_candidates(")
    i_roll = src.index("_ant_roll = _pnr_rollback_refused_antenna_repair(")
    assert i_adopt < i_roll


# --------------------------------------------------------------------------
# a design that never needed a repair
# --------------------------------------------------------------------------
def test_an_already_clean_route_reaches_the_end_having_done_nothing():
    """MEASURED the moment it did not. The repair loop lives inside the
    `$_ant_pre != 0` branch and the refusal check does not, so a design that
    was ALREADY CLEAN reached `if {$_ant_refused ne ""}` with no such variable
    and the whole deck died — caught by this repo's own
    `test_clean_design_is_a_noop_no_repair_called`. The three transaction
    variables are therefore declared at the TOP of the block."""
    t = _code()
    i_decl = t.index('set _ant_refused ""')
    i_branch = t.index("if {$_ant_pre == 0}")
    assert i_decl < i_branch, (i_decl, i_branch)
    for v in ('set _ant_ckpt ""', "set _ant_ckpt_ok 0"):
        assert t.index(v) < i_branch, v


def test_a_clean_route_is_not_reported_as_a_repair_that_applied():
    """"nothing refused" and "nothing was attempted" are different answers, and
    only one of them says a repair ran."""
    t = _code()
    assert "ANTENNA_REPAIR_NOT_ATTEMPTED: the route was already clean" in t
    i_applied = t.index("ANTENNA_REPAIR_APPLIED")
    assert "elseif {$_ant_ckpt_ok}" in t[:i_applied][-60:]


# --------------------------------------------------------------------------
# (b) the invariant keys on ANY modification, not only a refused one
# --------------------------------------------------------------------------
def test_a_successful_repair_that_was_never_reverified_breaks_the_invariant():
    """The ruling says ANY route modification after the last DRT-0702. A repair
    that SUCCEEDED and was not re-verified breaks it exactly as a refused one
    does, and the narrower antenna-refusal helper cannot see it."""
    log = _VERIFIED + "REPAIR_ANTENNA_DONE: diode=d iter=0 margin=0\n"
    assert R.route_modified_after_last_verification(log) is True
    assert R.antenna_reroute_refusal_after_last_verification(log) is False


def test_a_modification_the_router_then_verified_is_not_a_violation():
    """NEGATIVE CONTROL: the order is the whole question."""
    assert R.route_modified_after_last_verification(
        "REPAIR_ANTENNA_DONE: x\n" + _VERIFIED) is False


def test_a_verified_route_nothing_touched_is_not_a_violation():
    assert R.route_modified_after_last_verification(_VERIFIED) is False


def test_no_verification_at_all_says_nothing_about_ordering():
    assert R.route_modified_after_last_verification(
        "REPAIR_ANTENNA_DONE: x\n") is False


def test_the_run15_log_shape_breaks_the_invariant():
    """The measurement this ruling was written on, INLINED at its own line
    numbers so it is a test on any host rather than one that quietly passes
    wherever the lane directory is absent."""
    lines = ["noise"] * 3000
    lines[1529] = "[INFO DRT-0702] Post-route verification: 0 violation(s)."
    lines[1956] = "ANTENNA_NATIVE_REROUTE_NONFATAL: DRT-1010"
    lines[1986] = "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010"
    lines[2426] = "ANTENNA_NATIVE_REROUTE_NONFATAL: DRT-1010"
    lines[2456] = "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010"
    log = "\n".join(lines)
    assert R.route_modified_after_last_verification(log) is True
    assert R.antenna_reroute_refusal_after_last_verification(log) is True


def test_the_invariant_helper_is_pure():
    import inspect
    src = inspect.getsource(R.route_modified_after_last_verification)
    for forbidden in ("open(", "Path(", "subprocess", "_docker_exec"):
        assert forbidden not in src, forbidden
