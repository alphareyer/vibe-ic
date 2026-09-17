"""R-0915-94: the post-route SDR loop is r27-EXACT.

One grader SPEF read, at the DRV census; `repair_design` and `repair_timing` run
under that annotation, with no further `read_spef` and no `estimate_parasitics`
between the census and a repair. The only later read/estimate is the EST-0104
recovery inside a failed `repair_design`, which r27 also emits.

Measured: R-0915-84(a)'s estimate hid every violation (grader -5.924 ns vs
estimate +5.500 ns; SS -0.71/-0.62); R-0915-93's re-read before each repair
re-annotated rebuffered nets with pre-repair parasitics (22 violators vs r27's
21, SS -0.41); r27's single census read closed at +0.03 and was reproduced
byte-identically. The emitted statement sequence below was measured from the
f145d8133 (r27) builder and is identical from this tree's builder.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phase3_one_shot_runner as R                       # noqa: E402
from test_sdr_checkpoint_and_child import _full_pnr_tcl  # noqa: E402

TOKENS = r"\b(write_spef|read_spef|estimate_parasitics|repair_design|repair_timing)\b"
R27_SEQUENCE = {
    "postroute_drv_repair": ["write_spef", "read_spef", "repair_design",
                             "estimate_parasitics", "read_spef", "repair_design",
                             "repair_design", "repair_timing"],
    "postroute_drv_reconverge": ["write_spef", "write_spef", "read_spef",
                                 "repair_design", "estimate_parasitics", "read_spef",
                                 "repair_design", "repair_design", "repair_timing"],
}


def _region(tmp_path, stage):
    deck = _full_pnr_tcl(tmp_path)
    ck = tmp_path / "ckpt.def"
    ck.write_text("C\n")
    t = R._build_pnr_sdr_child_tcl_text(deck, checkpoint_def_c=str(ck), stage=stage)
    lo = t.index("write_spef")
    return t[lo:t.index("SDR_CHILD_RECEIPT", lo)]


def _code(text):
    return re.sub(r'"[^"\n]*"', '""', text)


def _sequence(region):
    return [m.group(1) for m in re.finditer(TOKENS, _code(region))]


def _main_path(region):
    """The loop with the EST-0104 recovery branch removed."""
    return re.sub(r"# --- R9 EST-0104 recovery.*?EST0104_RECOVERED[^\n]*\n",
                  "", region, flags=re.S)


def violations(region):
    out = []
    code = _code(_main_path(region))
    census = code.find("read_spef")
    if census < 0:
        return ["no census read_spef"]
    for call in ("repair_design", "repair_timing"):
        i = code.find(call, census)
        if i < 0:
            out.append(f"{call} missing")
            continue
        between = code[census + len("read_spef"):i]
        for bad in ("read_spef", "estimate_parasitics"):
            if bad in between:
                out.append(f"{bad} between the census read and {call}")
    return out


def test_the_emitted_sequence_is_r27s(tmp_path):
    for stage, expected in R27_SEQUENCE.items():
        assert _sequence(_region(tmp_path, stage)) == expected, stage


def test_census_read_present_and_nothing_between_it_and_a_repair(tmp_path):
    for stage in R27_SEQUENCE:
        assert violations(_region(tmp_path, stage)) == [], stage


def test_a_read_before_repair_timing_is_caught(tmp_path):
    """NEGATIVE CONTROL: R-0915-93's shape."""
    reg = _region(tmp_path, "postroute_drv_repair")
    bad = reg.replace("if {[catch {repair_timing -setup}",
                      "read_spef /x/sdr_pass.spef\n    if {[catch {repair_timing -setup}", 1)
    assert bad != reg and violations(bad)


def test_an_estimate_before_repair_design_is_caught(tmp_path):
    """NEGATIVE CONTROL: R-0915-84(a)'s shape."""
    reg = _region(tmp_path, "postroute_drv_repair")
    bad = reg.replace("if {[catch {repair_design -max_wire_length",
                      "estimate_parasitics -global_routing\n    if {[catch {repair_design -max_wire_length", 1)
    assert bad != reg and violations(bad)


def test_a_missing_census_read_is_caught(tmp_path):
    reg = _region(tmp_path, "postroute_drv_repair")
    first = reg.index("read_spef")
    assert violations(reg[:first] + "noop_spef" + reg[first + len("read_spef"):])


def test_no_parasitics_disclosure_line_is_emitted_into_the_tcl(tmp_path):
    for stage in R27_SEQUENCE:
        assert "_RSZ_PARASITICS" not in _region(tmp_path, stage), stage


def test_the_disclosure_is_recorded_not_emitted(tmp_path):
    out = tmp_path / "pnr"
    for stage, dirname in R._SDR_TXN_DIRS.items():
        (out / dirname).mkdir(parents=True, exist_ok=True)
        (out / dirname / "receipt.tsv").write_text(
            "status\treason\tbefore_router_drc\tafter_router_drc\n"
            "ACCEPTED\trouter_drc_preserved_clean\t0\t0\n")
    (out / "sdr_child_postroute_drv_repair.log").write_text(
        "[WARNING EST-0027] no estimated parasitics.\n[WARNING EST-0027] again\n")
    recs = R._disclose_sdr_transactions(tmp_path / "proj", out, [], {})
    rec = next(r for r in recs if r["stage"] == "postroute_drv_repair")
    assert rec["repair_parasitics"]["est0027_warnings"] == 2
    assert rec["repair_parasitics"]["estimate_parasitics"] == "not used"


def test_the_why_is_written_where_the_code_is():
    doc = R._sdr_repair_parasitics_disclosure.__doc__
    for n in ("-5.924", "+5.500", "5.8x", "-0.41", "+0.03"):
        assert n in doc, n
