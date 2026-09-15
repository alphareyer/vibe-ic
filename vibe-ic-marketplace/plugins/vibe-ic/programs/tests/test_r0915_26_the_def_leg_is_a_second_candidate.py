"""R-0915-26 — when the ODB leg's child is REJECTED, try the DEF leg.

MEASURED on sha256 (lane icsha2): the ODB leg's first transaction was REJECTED
— `[ERROR DRT-0206] checkConnectivity error` on `spare_tielo_spare_inverter_52`
— from the very checkpoint the DEF leg had accepted the same site from in run7
and run8. The two restores are not equivalent and neither dominates, so a
rejection on one carrier is not evidence that the repair is impossible; it is
evidence that THAT carrier could not deliver it.

So the transaction now runs a SECOND candidate, and the whole point is that it
is the same recipe judged by the same judge:

  * the ODB leg runs FIRST and ALWAYS — it carries the route guides, the
    `dont_touch` and the wire geometry a DEF round-trip does not (R-0915-13,
    R-0915-18). Never the other way round;
  * the DEF leg runs ONLY after a JUDGED rejection. A checkpoint ODB that was
    never written is still the named refusal of R-0915-18, because falling back
    there would make a DEF the ONLY restore anybody tried — the silent degrade
    that ruling exists to stop;
  * both legs are DISCLOSED by name and receipt. They write the same fixed
    names into one transaction directory, so the first leg's evidence is copied
    aside under `odb_leg_` before the second runs.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

STAGE = "postroute_drv_repair"
TXN = R._SDR_TXN_DIRS[STAGE]


def _drc(n: int) -> str:
    return "".join("violation type: spacing\n" for _ in range(n))


def _leg_writes(txn: Path, *, route_ok: bool, nonfatal: bool, after: int):
    """The Tcl one simulated child runs: candidate, report, receipt."""
    return (
        f'    set f [open {{{txn / R._SDR_CANDIDATE_DEF_NAME}}} w]; '
        f'puts -nonewline $f CANDIDATE; close $f\n'
        f'    set f [open {{{txn / R._SDR_CANDIDATE_DRC_NAME}}} w]; '
        f'puts -nonewline $f {{{_drc(after)}}}; close $f\n'
        f'    set f [open {{{txn / R._SDR_CHILD_RECEIPT_NAME}}} w]; '
        f'puts $f "mutated\\terror\\troute_ok\\tplacement_violations"; '
        f'puts $f "1\\t{1 if nonfatal else 0}\\t{1 if route_ok else 0}\\t0"; '
        f'close $f\n')


def _run(tmp_path: Path, *, before: int = 0,
         odb=(True, False, 0), leg_def=(True, False, 0),
         def_deck: bool = True, checkpoint_odb: bool = True):
    """begin -> parent child-call -> finish, with BOTH children simulated.

    ``odb`` / ``leg_def`` are (route_ok, nonfatal, after). The simulated `exec`
    dispatches on the deck path, which is the only thing that distinguishes the
    two legs in the emitted Tcl.
    """
    out = tmp_path / "pnr"
    out.mkdir()
    txn = out / TXN
    (out / R.ROUTER_DRC_REPORT_NAME).write_text(_drc(before))
    def_deck_path = out / R._sdr_child_def_leg_tcl_name(STAGE)
    if def_deck:
        def_deck_path.write_text("# simulated DEF-leg child deck\n")

    # Dispatch on the EXACT deck path. A substring match is not safe here:
    # pytest names one of these tmp dirs ".../test_a_missing_def_leg_deck_is0",
    # so `string match *def_leg*` matched the DIRECTORY and silently ran the
    # wrong leg — the harness reporting a pass the code had not earned.
    body = (
        f'  if {{[lsearch -exact $args {{{def_deck_path}}}] >= 0}} {{\n'
        + _leg_writes(txn, route_ok=leg_def[0], nonfatal=leg_def[1],
                      after=leg_def[2])
        + '  } else {\n'
        + _leg_writes(txn, route_ok=odb[0], nonfatal=odb[1], after=odb[2])
        + '  }\n')

    # `begin` writes the checkpoint ODB; dropping it models a session whose
    # `write_db` failed, which must stay the R-0915-18 refusal and NOT fall back.
    write_db = ("proc write_db {path} {\n"
                "  set f [open $path w]; puts -nonewline $f $::db; close $f\n"
                "}\n") if checkpoint_odb else "proc write_db {path} { }\n"

    script = tmp_path / "trial.tcl"
    script.write_text(
        "set ::db BASE\n"
        "proc write_def {path} {\n"
        "  set f [open $path w]; puts -nonewline $f $::db; close $f\n"
        "}\n"
        "proc read_def {path} {\n"
        "  set f [open $path r]; set ::db [read $f]; close $f\n"
        "}\n"
        + write_db
        + "proc check_connectivity {} { return }\n"
        "proc exec {args} {\n" + body + "}\n"
        + R._postroute_sdr_transaction_begin_tcl(str(out), STAGE)
        + R._postroute_sdr_parent_child_call_tcl(str(out), STAGE)
        + R._postroute_sdr_transaction_finish_tcl(STAGE)
        + '# Only a REJECTED pass reaches here: an acceptance exits above.\n'
        + 'write_def ' + str(out / "shipped.def") + '\n'
        + 'puts "FINAL_REACHED"\n')
    return subprocess.run([tclsh, str(script)], text=True,
                          capture_output=True), out


def _receipt(out: Path) -> str:
    return (out / TXN / "receipt.tsv").read_text()


# ------------------------------------------- direction 1: ODB leg accepted

@needs_tclsh
def test_an_accepted_odb_leg_never_tries_the_def_leg(tmp_path):
    """The fallback is a fallback. A candidate that was accepted is shipped,
    and the second child is never spawned — one repair, one session."""
    run, out = _run(tmp_path, before=4, odb=(True, False, 2))
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED: router_drc=4 -> 2" in run.stdout
    assert "SDR_CANDIDATE_ODB_REJECTED" not in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" not in run.stdout
    assert "SDR_CANDIDATE_LEG: odb" in run.stdout
    # nothing was moved aside, because nothing had to be
    assert not (out / TXN /
                f"{R._SDR_ODB_LEG_PREFIX}{R._SDR_CHILD_RECEIPT_NAME}").exists()


# ------------------------------------------- direction 2: ODB leg rejected

@needs_tclsh
def test_a_rejected_odb_leg_tries_the_def_leg_and_judges_it(tmp_path):
    """The sha256 case: the ODB child's route fails, the DEF child's does not,
    and the SAME judge then accepts the second candidate."""
    run, out = _run(tmp_path, before=4,
                    odb=(False, False, 0), leg_def=(True, False, 2))
    assert run.returncode == 0, run.stderr
    assert "SDR_CANDIDATE_ODB_REJECTED: error=0 route_ok=0" in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" in run.stdout
    assert "SDR_CANDIDATE_DEF_RECEIPT: mutated=1 error=0 route_ok=1" \
        in run.stdout
    assert "SDR_CANDIDATE_LEG: def" in run.stdout
    # judged by the UNCHANGED judge, on the router's own before/after
    assert "SDR_TRANSACTION_ACCEPTED: router_drc=4 -> 2" in run.stdout
    assert "ACCEPTED\tstrict_router_drc_improvement\t4\t2" in _receipt(out)


@needs_tclsh
def test_a_nonfatal_in_the_odb_leg_also_reaches_the_def_leg(tmp_path):
    """`error=1` is the other half of the rejection condition, and it must
    trigger the retry exactly as a failed route does."""
    run, _ = _run(tmp_path, before=4,
                  odb=(True, True, 9), leg_def=(True, False, 1))
    assert run.returncode == 0, run.stderr
    assert "SDR_CANDIDATE_ODB_REJECTED: error=1 route_ok=1" in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" in run.stdout
    assert "SDR_CANDIDATE_LEG: def" in run.stdout


# ------------------------------------------- direction 3: both legs rejected

@needs_tclsh
def test_both_legs_rejected_rejects_the_transaction_and_keeps_both_receipts(
        tmp_path):
    """Trying twice must not become accepting once. When neither carrier can
    deliver the repair the transaction is REJECTED, the session keeps its own
    clean route, and BOTH children's evidence is on disk to show why."""
    run, out = _run(tmp_path, before=4,
                    odb=(False, False, 0), leg_def=(False, False, 0))
    assert run.returncode == 0, run.stderr
    assert "SDR_CANDIDATE_ODB_REJECTED" in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" in run.stdout
    assert "SDR_TRANSACTION_REJECTED" in run.stdout
    assert "ACCEPTED" not in _receipt(out)
    # the ODB leg's own receipt survived the DEF leg writing over the live one
    kept = out / TXN / f"{R._SDR_ODB_LEG_PREFIX}{R._SDR_CHILD_RECEIPT_NAME}"
    assert kept.is_file(), "the first leg's receipt was erased by the second"
    assert "\t0\t0\t" in kept.read_text()
    # and the rejection did not stop the run: the shipping session carries on
    assert "FINAL_REACHED" in run.stdout


@needs_tclsh
def test_the_odb_legs_evidence_is_kept_before_the_def_leg_overwrites_it(
        tmp_path):
    """Both children write the SAME fixed names into ONE directory. Without the
    copy-aside the retry would erase the evidence for its own justification."""
    run, out = _run(tmp_path, before=4,
                    odb=(False, False, 0), leg_def=(True, False, 1))
    assert run.returncode == 0, run.stderr
    assert "SDR_CANDIDATE_ODB_EVIDENCE_KEPT" in run.stdout
    for name in (R._SDR_CHILD_RECEIPT_NAME, R._SDR_CANDIDATE_DEF_NAME,
                 R._SDR_CANDIDATE_DRC_NAME):
        assert (out / TXN / f"{R._SDR_ODB_LEG_PREFIX}{name}").is_file(), name


# ------------------------------------------- the refusals that must NOT bend

@needs_tclsh
def test_a_missing_checkpoint_odb_is_refused_by_name_and_never_falls_back(
        tmp_path):
    """R-0915-18 stands. If `begin` wrote no ODB the ODB leg never ran, and
    building the ONLY candidate from the DEF is the silent degrade that
    refusal exists to stop. The retry fires on a JUDGED rejection, not on a
    checkpoint that was never written."""
    run, _ = _run(tmp_path, before=4, checkpoint_odb=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_CHECKPOINT_ODB_ABSENT" in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" not in run.stdout
    assert "SDR_TRANSACTION_REJECTED" in run.stdout


@needs_tclsh
def test_a_missing_def_leg_deck_is_disclosed_and_decided_on_the_odb_leg_alone(
        tmp_path):
    """A deck that could not be written costs the retry and nothing else — the
    behaviour is then exactly what it was before this ruling, said out loud."""
    run, _ = _run(tmp_path, before=4, odb=(False, False, 0), def_deck=False)
    assert run.returncode == 0, run.stderr
    assert "SDR_CANDIDATE_ODB_REJECTED" in run.stdout
    assert "SDR_CANDIDATE_DEF_UNAVAILABLE" in run.stdout
    assert "SDR_CANDIDATE_DEF_TRIED" not in run.stdout
    assert "SDR_CANDIDATE_LEG: odb" in run.stdout
    assert "SDR_TRANSACTION_REJECTED" in run.stdout


# ------------------------------------------------------- shape of the decks

def test_the_two_legs_get_their_own_deck_and_their_own_log():
    """One transaction directory, two children: if they shared a log the second
    would overwrite the first's transcript, which is the evidence for the
    retry."""
    assert R._sdr_child_tcl_name(STAGE) != R._sdr_child_def_leg_tcl_name(STAGE)
    assert R._sdr_child_log_name(STAGE) != R._sdr_child_def_leg_log_name(STAGE)
    parent = R._postroute_sdr_parent_child_call_tcl("/o", STAGE)
    assert f"/o/{R._sdr_child_log_name(STAGE)}" in parent
    assert f"/o/{R._sdr_child_def_leg_log_name(STAGE)}" in parent


def test_the_odb_leg_runs_first_and_always(tmp_path):
    """Never the other way round: the ODB deck is `exec`d unconditionally and
    the DEF deck only inside the rejection branch."""
    parent = R._postroute_sdr_parent_child_call_tcl("/o", STAGE)
    i_odb = parent.index(f"_sdr_exec_child /o/{R._sdr_child_tcl_name(STAGE)}")
    i_rej = parent.index("SDR_CANDIDATE_ODB_REJECTED")
    i_def = parent.index(
        f"_sdr_exec_child /o/{R._sdr_child_def_leg_tcl_name(STAGE)}")
    assert i_odb < i_rej < i_def


def test_the_def_leg_deck_restores_from_the_def_and_the_odb_leg_from_the_odb(
        tmp_path):
    """The two decks differ in ONE thing: which carrier they read."""
    import test_sdr_checkpoint_and_child as H
    out = tmp_path / "pnr"
    out.mkdir()
    pnr = out / "pnr.tcl"
    pnr.write_text(H._full_pnr_tcl(tmp_path))
    failures = R._write_sdr_child_decks(pnr, out, "", None)
    assert not failures, failures
    odb_deck = (out / R._sdr_child_tcl_name(STAGE)).read_text()
    def_deck = (out / R._sdr_child_def_leg_tcl_name(STAGE)).read_text()
    def _cmds(deck, name):
        # statement position only: both decks carry COMMENTS about the other
        # carrier, and a substring test reads those as the command.
        return [ln for ln in deck.splitlines()
                if ln.strip().startswith(name + " ")]
    assert _cmds(odb_deck, "read_db"), "the ODB leg must restore from the db"
    assert not _cmds(def_deck, "read_db"), _cmds(def_deck, "read_db")[:2]
    assert _cmds(def_deck, "read_def"), "the DEF leg must restore from the DEF"
    # and they are otherwise the same recipe: same role, same site
    for deck in (odb_deck, def_deck):
        assert f'set {R._SDR_ROLE_VAR} "child"' in deck
        assert R._pnr_stage_begin(STAGE) in deck


def test_the_adopt_marker_names_the_leg_that_produced_the_candidate():
    """A reader must be able to tell which carrier the shipped repair came
    from without re-deriving it from two logs."""
    fin = R._postroute_sdr_transaction_finish_tcl(STAGE)
    assert "leg=$_sdr_tx_leg" in fin
