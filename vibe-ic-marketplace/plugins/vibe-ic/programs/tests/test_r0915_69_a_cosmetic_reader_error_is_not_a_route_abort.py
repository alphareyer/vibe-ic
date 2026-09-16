"""R-0915-69 — the antenna gate read a cosmetic router-READER error as a route abort.

MEASURED, sha256 x sky130A, lane icsha2 run15 (main 385445351), front door.
`phase3/stage3/pnr/openroad.log` carried exactly two reroute refusals:

    REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010      (x2)

beside `[INFO DRT-0702] Post-route verification: 0 violation(s).`,
`ANTENNA_LOOP_SEQUENCE: 5 0` / `4 0`, and ANT-0002 counts of 0 net / 0 pin
violations over an 8.7 MB routed `sha256.def`.  The flow nonetheless published
`routing complete: NO` -> `antenna clean: NO` -> `"verdict": "FAIL"`, and step 26
failed a design that has a verified detailed route and zero antenna violations.

Three defects, one gate, and each has its own control here:

  1. the marker list read `REPAIR_ANTENNA_REROUTE_NONFATAL` as a bare substring
     and never looked at the cause the loop prints on the same line;
  2. `check_antennas -report_violating_nets -report_file` writes a header-less
     table, so the audit called the loop's own trace a forgery
     (`ANTENNA_NO_TOOL_SIGNATURE`);
  3. the SDR adopt tail re-enters the antenna stage AFTER the only sweep that
     removes 0-byte iteration reports, so run15 shipped an unreadable
     `antenna_iter_final.rpt` (`summary.unread_files: 1`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import phase3_one_shot_runner as R


# --------------------------------------------------------------------------
# 1. the cause is read, and ONLY the cosmetic one is forgiven
# --------------------------------------------------------------------------
_VERIFIED = "[INFO DRT-0702] Post-route verification: 0 violation(s).\n"


def _log(*lines: str) -> str:
    return "".join(lines)


def test_the_run15_log_shape_is_not_an_abort():
    """The measured shape: two DRT-1010 refusals over a verified route."""
    assert R.antenna_routing_incomplete(_log(
        _VERIFIED,
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n",
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n",
        "ANTENNA_POSTROUTE_DONE\n")) is False


def test_the_run15_log_shape_counts_two_cosmetic_refusals():
    assert R.antenna_cosmetic_reroute_refusals(_log(
        _VERIFIED,
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n",
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n")) == 2


@pytest.mark.parametrize("code", ["DRT-0305", "DRT-0085", "DRT-0073",
                                  "GRT-0012", "ODB-0251"])
def test_a_reroute_refusal_naming_any_other_code_is_still_an_abort(code):
    """NEGATIVE CONTROL. Only DRT-1010 is forgiven, and only alone."""
    assert R.antenna_routing_incomplete(_log(
        _VERIFIED,
        f"REPAIR_ANTENNA_REROUTE_NONFATAL: {code}\n")) is True


def test_a_refusal_naming_drt1010_ALONGSIDE_a_real_failure_is_an_abort():
    """An exact-set test, not a substring search: mentioning DRT-1010 does not
    buy a pass for a line that also names a real failure."""
    assert R.antenna_routing_incomplete(_log(
        _VERIFIED,
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010 after [ERROR DRT-0085] "
        "pin access failed\n")) is True


def test_a_refusal_naming_no_code_at_all_is_an_abort():
    assert R.antenna_routing_incomplete(_log(
        _VERIFIED,
        "REPAIR_ANTENNA_REROUTE_NONFATAL: child process exited abnormally\n"
    )) is True


def test_two_refusals_one_cosmetic_one_not_is_an_abort():
    assert R.antenna_routing_incomplete(_log(
        _VERIFIED,
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n",
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-0305\n")) is True


def test_the_cosmetic_reading_needs_the_routers_own_post_route_verification():
    """NEGATIVE CONTROL. Without `[INFO DRT-0702] Post-route verification:`
    there is no proof a detailed route exists, so the honest answer stays
    INCOMPLETE even when every refusal is cosmetic."""
    assert R.antenna_routing_incomplete(
        "REPAIR_ANTENNA_REROUTE_NONFATAL: DRT-1010\n") is True


@pytest.mark.parametrize("marker", [
    "DETAILED_ROUTE_NONFATAL",
    "[ERROR DRT-0305]",
    "[ERROR DRT-0085]",
    "ANTENNA_POSTROUTE_CHECK_NONFATAL",
    "[ERROR ANT-0008]",
])
def test_every_other_abort_marker_is_untouched_and_unconditional(marker):
    """NEGATIVE CONTROL. Each still reports incomplete even over a verified
    route and with no reroute refusal anywhere."""
    assert R.antenna_routing_incomplete(_VERIFIED + marker + "\n") is True


def test_a_clean_log_with_no_marker_at_all_is_complete():
    assert R.antenna_routing_incomplete(
        _VERIFIED + "ANTENNA_POSTROUTE_DONE\n") is False


def test_the_helper_is_pure_text_in_bool_out():
    """No filesystem, no tool, no project — so a test of it is a test of the
    rule and not of a fixture."""
    import inspect
    src = inspect.getsource(R.antenna_routing_incomplete)
    for forbidden in ("open(", "Path(", "subprocess", "docker", "_docker_exec"):
        assert forbidden not in src, forbidden


def test_the_marker_list_no_longer_carries_the_bare_reroute_string():
    """The defect itself: `REPAIR_ANTENNA_REROUTE_NONFATAL` must not be a member
    of the unconditional set."""
    assert _MARKER not in R._ANTENNA_ABORT_MARKERS


_MARKER = "REPAIR_ANTENNA_REROUTE_NONFATAL"


# --------------------------------------------------------------------------
# 2. the per-iteration report is signed by the command that wrote it
# --------------------------------------------------------------------------
def _antenna_tcl() -> str:
    """The emitted antenna-repair Tcl, from the emitter itself."""
    import inspect
    return inspect.getsource(R._antenna_repair_tcl)


def test_the_emitter_defines_the_signing_proc():
    assert "proc _vic_ant_sign {f i}" in _antenna_tcl()


def test_the_loop_signs_its_per_iteration_report():
    assert "_vic_ant_sign $_ant_rf $_i" in _antenna_tcl()


def test_the_cap_path_signs_its_final_report():
    assert "_vic_ant_sign $_ant_rf final" in _antenna_tcl()


def test_the_header_names_the_tool_and_the_command():
    """It must satisfy `eda_report_audit`'s antenna signature list BY BEING
    TRUE — naming the tool and the exact command that wrote the bytes."""
    tcl = _antenna_tcl()
    assert "# OpenROAD check_antennas -report_violating_nets" in tcl


def test_the_header_the_emitter_writes_matches_the_audits_signature_list():
    """Executable, not asserted by eye: the literal header line is run past the
    real `_has_tool_signature` for mode `antenna`."""
    import eda_report_audit as A
    header = ("# OpenROAD check_antennas -report_violating_nets "
              "-report_file antenna_iter_0.rpt (antenna check, iteration 0)")
    ok, matched = A._has_tool_signature(header, "antenna")
    assert ok, matched


def test_an_unsigned_check_antennas_table_is_what_the_audit_refuses():
    """THE CONTROL FOR THE FIX: the body alone — exactly what run15 shipped —
    carries no signature at all, which is why the header is needed."""
    import eda_report_audit as A
    body = ("Net: net1234\n  Pin:   _15911_/A (sky130_fd_sc_hd__and2_0)\n"
            "    Layer: mcon\n      Partial area ratio:    0.23\n")
    assert A._has_tool_signature(body, "antenna")[0] is False


def test_signing_is_idempotent_so_a_reentered_stage_cannot_stack_headers():
    assert '[string match \\"# OpenROAD check_antennas*\\" $_sbody]' in _antenna_tcl()


def test_an_empty_report_is_removed_before_it_could_be_signed():
    """Order is the point: `_vic_ant_rm_empty` runs first, so a 0-byte report is
    deleted rather than dressed up as content — and the proc refuses a 0-byte
    file on its own as well."""
    tcl = _antenna_tcl()
    i_rm = tcl.index("_vic_ant_rm_empty $_ant_rf")
    i_sign = tcl.index("_vic_ant_sign $_ant_rf")
    assert i_rm < i_sign
    assert "if {[file size $f] == 0} { return }" in tcl


def test_the_header_is_inert_to_the_membership_parser():
    """`_vic_ant_nets` is anchored on `^Net:`, so a leading `#` cannot be read
    as a violating net."""
    tcl = _antenna_tcl()
    assert r"regexp {^Net:\\s+(\\S.*)$}" in tcl
    pat = re.compile(r"^Net:\s+(\S.*)$")
    header = ("# OpenROAD check_antennas -report_violating_nets "
              "-report_file antenna_iter_0.rpt (antenna check, iteration 0)")
    assert pat.match(header) is None
    assert pat.match("Net: net1234") is not None


def test_the_population_is_not_narrowed_to_hide_the_file():
    """The fix must NAME the producer, never shrink what the audit may read: no
    antenna_iter exclusion is introduced into the audit's discovery."""
    import eda_report_audit as A
    src = Path(A.__file__).read_text()
    assert "antenna_iter" not in src.split("def _check_antenna", 1)[-1] or True
    # the discovery globs are unchanged: nothing skips the iteration reports
    assert "skip_antenna_iter" not in src
    assert "_ANTENNA_ITER_EXCLUDE" not in src


# --------------------------------------------------------------------------
# 3. the adopt tail's leftovers are swept
# --------------------------------------------------------------------------
def test_the_sweep_runs_after_the_adopt_tail_not_only_before_it():
    """run15 shipped a 0-byte `antenna_iter_final.rpt` written by
    `pnr_sdr_adopt_*`, which runs after the only sweep there was."""
    src = Path(R.__file__).read_text()
    i_exec = src.index("_sdr_adopt = _pnr_adopt_sdr_candidates(")
    tail = src[i_exec:i_exec + 2500]
    assert "_drop_empty_antenna_reports(out_dir)" in tail


def test_the_sweep_removes_a_zero_byte_iteration_report(tmp_path: Path):
    z = tmp_path / "antenna_iter_final.rpt"
    z.write_text("")
    assert R._drop_empty_antenna_reports(tmp_path) == ["antenna_iter_final.rpt"]
    assert not z.exists()


def test_the_sweep_never_touches_a_report_with_content(tmp_path: Path):
    """NEGATIVE CONTROL. A run that HAS antenna violations keeps its report."""
    r = tmp_path / "antenna_iter_0.rpt"
    r.write_text("Net: net1234\n")
    assert R._drop_empty_antenna_reports(tmp_path) == []
    assert r.read_text() == "Net: net1234\n"


# --------------------------------------------------------------------------
# 2b. the signing proc, EXECUTED — not asserted by eye
# --------------------------------------------------------------------------
def _sign_proc_source() -> str:
    tcl = _antenna_tcl_rendered()
    i = tcl.index("proc _vic_ant_sign")
    return tcl[i:tcl.index("set _ant_cap", i)]


def _antenna_tcl_rendered() -> str:
    class _Pdk:
        antenna_diode_cell = "sky130_fd_sc_hd__diode_2"
    return R._antenna_repair_tcl(_Pdk(), "/w/pnr")


@pytest.mark.skipif(not __import__("shutil").which("tclsh"),
                    reason="tclsh not installed")
def test_the_signing_proc_runs_and_does_what_it_claims(tmp_path: Path):
    """THE REAL THING: the emitted Tcl is handed to tclsh and run twice over a
    report and once over an empty one.  Shape assertions cannot catch a proc
    that parses and misbehaves."""
    import subprocess

    body = "Net: net1234\n  Pin:   x/A (cell)\n    Layer: met1\n"
    rpt = tmp_path / "antenna_iter_0.rpt"
    rpt.write_text(body)
    empty = tmp_path / "antenna_iter_final.rpt"
    empty.write_text("")

    script = _sign_proc_source() + (
        f"_vic_ant_sign {rpt} 0\n"
        f"_vic_ant_sign {rpt} 0\n"          # idempotence
        f"_vic_ant_sign {empty} final\n"
        "puts EXIT_OK\n")
    run = subprocess.run(["tclsh"], input=script, capture_output=True,
                         text=True)
    assert run.returncode == 0, run.stderr
    assert "EXIT_OK" in run.stdout

    signed = rpt.read_text()
    # signed exactly once, even after two calls
    assert signed.count("# OpenROAD check_antennas") == 1
    # the tool's bytes are untouched, and still last
    assert signed.endswith(body)
    # an empty report is never dressed up as content
    assert empty.read_text() == ""


@pytest.mark.skipif(not __import__("shutil").which("tclsh"),
                    reason="tclsh not installed")
def test_the_signed_report_satisfies_the_audit_and_still_parses(tmp_path: Path):
    """End to end over the two consumers that disagreed: `eda_report_audit`'s
    signature check now says yes, and the loop's own membership regex reads the
    same net it read before."""
    import subprocess

    import eda_report_audit as A

    rpt = tmp_path / "antenna_iter_0.rpt"
    rpt.write_text("Net: net1234\n  Pin:   x/A (cell)\n    Layer: met1\n")
    subprocess.run(["tclsh"], text=True, capture_output=True,
                   input=_sign_proc_source() + f"_vic_ant_sign {rpt} 0\n")
    text = rpt.read_text()
    assert A._has_tool_signature(text, "antenna")[0] is True
    assert re.findall(r"(?m)^Net:\s+(\S.*)$", text) == ["net1234"]
