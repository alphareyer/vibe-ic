"""Which stage takes a pin's last access point is MEASURED, not reconstructed.

R-0915-110. MEASURED on subservient x gf180mcuD as a DIE (int4 arm,
2026-09-21, image 0.3.67): the SDR candidate check answered 0 inaccessible
pins -- truthfully, for the context it was asked in -- and the adopt path's PG
re-route then died on

    [ERROR DRT-1231] Pin u_core/_3245_/ZN does not have access point   (x6)

FIVE stages run between those two points (postroute_spef_extract,
postroute_antenna_repair which inserts diodes, postroute_drv_reconverge,
postroute_antenna_reconverge, postroute_fill), and an offline probe of
`antenna_pre_repair.odb` found the design still CLEAN there -- 0 of 14374
signal ITerms without access -- so the loss is in a later stage and nothing in
the run said which.

The probe is that answer, in the run's own log, at every boundary.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402


def test_the_probe_asks_the_router_not_the_database():
    tcl = R._pin_access_probe_tcl("t")
    assert "pin_access" in tcl
    assert "getAccessPoints" in tcl


def test_the_probe_names_the_boundary_it_measured():
    for tag in ("after_postroute_antenna_repair", "after_postroute_fill"):
        tcl = R._pin_access_probe_tcl(tag)
        assert f"{R._PIN_ACCESS_PROBE_MARKER}: {tag} no_access=" in tcl


def test_the_probe_names_the_offending_pins_not_only_a_count():
    tcl = R._pin_access_probe_tcl("t")
    assert "[[$_pap_t getMTerm] getName]" in tcl
    assert "llength $_pap_names] < 8" in tcl


def test_not_asked_reports_minus_one_never_zero():
    """'Nobody asked' and 'nothing is wrong' are different facts."""
    tcl = R._pin_access_probe_tcl("t")
    assert "set _pap_n -1" in tcl
    assert "this OpenROAD exposes no pin_access" in tcl
    assert "pin_access threw" in tcl
    assert "probe failed" in tcl


def test_the_probe_changes_no_placement_and_no_verdict():
    """Measure-only: a run that was going to pass still passes."""
    tcl = R._pin_access_probe_tcl("t")
    for forbidden in ("dbInst_create", "dbInst_destroy", "detailed_placement",
                      "setLocation", "_sdr_tx_reject_candidate", "error "):
        assert forbidden not in tcl, forbidden


def test_the_probe_is_balanced_tcl():
    for tag in ("t", "after_postroute_fill_before_pg_reconnect"):
        tcl = R._pin_access_probe_tcl(tag)
        bal = sum(l.count("{") - l.count("}") for l in tcl.splitlines())
        assert bal == 0, f"{tag}: {bal:+d}"


def test_every_boundary_between_the_check_and_the_reroute_is_probed():
    """The five stages the int4 arm named, plus the PG reconnect that follows
    them -- the last moment before the re-route, so a future regression dies
    legibly with the pin named."""
    src = (Path(__file__).resolve().parents[1]
           / "phase3_one_shot_runner.py").read_text()
    for tag in ("after_postroute_spef_extract",
                "after_postroute_antenna_repair",
                "after_postroute_drv_reconverge",
                "after_postroute_antenna_reconverge",
                "after_postroute_fill_before_pg_reconnect",
                "after_pg_connect"):
        assert f'_pin_access_probe_tcl("{tag}")' in src, tag


def test_the_last_probe_reports_the_state_after_the_connect():
    """R-0915-114(a) DELETED the re-route this probe used to precede.

    It was moved inside the PG block because the version emitted after the
    whole block never executed (int5: 0 occurrences; the run died at the
    re-route). int6 then read it `before_pg_reroute no_access=0`, which is
    what proved no placement takes the pin -- and that reading is why the
    re-route is gone. With nothing left to precede, the probe is retagged for
    what it now measures: the state after the connect, the last reading this
    block takes.
    """
    src = (Path(__file__).resolve().parents[1]
           / "phase3_one_shot_runner.py").read_text()
    assert '_pin_access_probe_tcl("after_pg_connect")' in src
    assert '_pin_access_probe_tcl("before_pg_reroute")' not in src
    tcl = R._build_pg_reconnect_tcl(reroute=True)
    assert "detailed_route -verbose" not in tcl, \
        "there is no re-route left in this block for a probe to precede"
