"""R-0915-119 rework (lane ictier1c) — the escape certifies "no protocol timing
here" only when check()'s OWN classifier finds nothing to split.

`internal_vs_external_timing_check`'s structural-absence escape used a second
vocabulary (`_PROTO_GROUP_TOKENS`) over key NAMES only, and required non-empty
values where `check()` accepts an empty group. The pre-landing review of
next/icspm5-s2 (95c9184a1) CONFIRMED three inputs on which the escape published
NOT_APPLICABLE_BY_STRUCTURE while `check()` over the SAME document FAILs:

  (a) the v068 flat shape  {"timing_parameters": {"tDW0_us", "tB_break_us",
      "tIBT_us"}}            -> check(): missing_rx_group + missing_tx_group
  (b) timing_groups {detect_thresholds, drive_widths} with no IBT on the drive
      side                   -> check(): tx_missing_symbols ['IBT']
  (c) timing_groups {rx_timing: {}, tx_timing: {}}
                             -> check(): rx/tx_missing_symbols

Each is pinned here as a gate run that must end FAIL (rc 1), never NABS. The
real spm L8 (run22) still reads NABS {scanned 13, found 0}.

AND THE WRAPPER'S HALF, which no test drove: the rc-0 site of
`_check_program_exit_zero` hands the guard the report's structural-absence
enumeration, and the ledger row is written with the same evidence. The tests
below drive the REAL gate through the wrapper and through `check_step`; they go
red when either `evidence=` kwarg is removed (measured, see the commit).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F                             # noqa: E402
import _flow_reason_taxonomy as TAX                          # noqa: E402

GATE = PROGRAMS / "internal_vs_external_timing_check.py"
SPM_L8 = PROGRAMS / "tests" / "fixtures" / "spm_run22_L8_TIMING_WAVEFORM.json"
SPM_LAYER = PROGRAMS / "tests" / "fixtures" / "spm_run22_L8_RTL_CONSTANTS.json"
L8_REL = "phase1/generated_docs/L8_TIMING_WAVEFORM.json"
NABS = TAX.NOT_APPLICABLE_BY_STRUCTURE
CMD = (f"internal_vs_external_timing_check {L8_REL} "
       "--json reports/phase2/gates/int_vs_ext_timing.json")


def _project(tmp_path: Path, l8) -> Path:
    proj = tmp_path / "proj"
    doc = proj / L8_REL
    doc.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(l8, Path):
        shutil.copyfile(l8, doc)
    else:
        doc.write_text(json.dumps(l8))
    return proj


def _gate(tmp_path: Path, l8, layer=None):
    proj = _project(tmp_path, l8)
    rep = proj / "gate.json"
    argv = [sys.executable, str(GATE), str(proj / L8_REL), "--json", str(rep)]
    if layer is not None:
        lp = proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json"
        lp.write_text(json.dumps(layer))
        argv += ["--layer", str(lp)]
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, json.loads(rep.read_text())


# ── the three confirmed inputs: FAIL, never NABS ───────────────────────────

_V068_FLAT = {"clock_specification": {"main_clk_hz": 5000000},
              "timing_parameters": {"tDW0_us": {"nom": 7.2},
                                    "tB_break_us": {"nom": 13.8},
                                    "tIBT_us": {"nom": 22}}}
_DETECT_DRIVE = {"timing_groups": {
    "detect_thresholds": {"H0": [10, 30], "H1": [1, 9], "BR": [31, 65],
                          "IBT": 5},
    "drive_widths": {"H0": 35, "H1": 9, "BR": 69}}}
_EMPTY_RX_TX = {"timing_groups": {"rx_timing": {}, "tx_timing": {}}}


def _assert_fails(tmp_path, l8, *rules, layer=None):
    rc, rep = _gate(tmp_path, l8, layer=layer)
    assert rep.get("reason_class") != NABS, rep
    assert rep["verdict"] == "FAIL", rep
    assert rc == 1, rep
    got = {f["rule"] for f in rep["findings"]}
    for rule in rules:
        assert rule in got, (rule, got)


def test_v068_flat_shape_fails_instead_of_certifying_an_absence(tmp_path):
    _assert_fails(tmp_path, _V068_FLAT, "missing_rx_group", "missing_tx_group")


def test_detect_drive_groups_missing_ibt_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _DETECT_DRIVE, "tx_missing_symbols")


def test_empty_named_rx_tx_groups_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _EMPTY_RX_TX, "rx_missing_symbols",
                  "tx_missing_symbols")


def test_spm_run22_l8_still_reads_a_structural_absence(tmp_path):
    rc, rep = _gate(tmp_path, SPM_L8)
    assert rc == 0, rep
    assert rep["reason_class"] == NABS, rep
    sa = rep["structural_absence"]
    assert (sa["scanned"], sa["found"]) == (13, 0), sa


def test_the_escape_consults_the_classifier_and_the_full_probe():
    """ONE CLASSIFIER, AND NOTHING NARROWER THAN WHAT THE GATE READS. Round 1
    pinned only "no second token list", which the round-2 review showed pins a
    NARROWING. The escape must ask check()'s classifier AND the full probe
    over the L8 and the --layer constants."""
    src = GATE.read_text().split("def main(", 1)[1]
    assert "classified_groups(waveform)" in src
    assert "timing_content_probe(waveform, rtl_constants)" in src


# ── the wrapper: the rc-0 site honours the enumeration ─────────────────────

def test_the_rc0_site_publishes_the_gates_structural_absence(tmp_path):
    proj = _project(tmp_path, SPM_L8)
    before = len(F._GATE_LEDGER)
    res = F._check_program_exit_zero(proj, CMD)
    assert res.exit_code == 0, res
    assert res.verdict == "NOT_APPLICABLE", (res.verdict, res[1])
    assert res.reason_class == NABS, res.reason_class
    row = F._GATE_LEDGER[before]
    assert (row["verdict"], row["reason_class"]) == ("NOT_APPLICABLE", NABS), row


def test_step_2_clause_is_not_filed_incomplete(tmp_path):
    """The run22 defect: step 2 read NOT_MEASURED/partial_population over a
    checker that had enumerated thirteen containers and said so."""
    proj = _project(tmp_path, SPM_L8)
    step = {"id": 2, "name": "probe", "stage": "stage1",
            "gate": {"all_of": [{"optional_program_exit_zero": {
                "command": CMD, "condition_files_exist": [L8_REL],
                "absent_condition_reason": "Scoped to a Phase-1 declaration "
                                           "that this probe always carries."}}]}}
    r = F.check_step(proj, step, {})
    assert r.status == "PASS", (r.status, r.reason_class, r.reasons)
    assert not any(str(x).startswith("INCOMPLETE") for x in r.reasons), r.reasons


# ── round 2 (review of 8646e1862): the escape may not be NARROWER than the gate ──

_EMPTY_CANON = {"timing_windows": [], "timing_constants": [], "waveforms": []}
_H1_SCALARS = dict(_EMPTY_CANON, H0_low_us=7.2, H1_low_us=1.8, BR_low_us=13.8,
                   IBT_us=22.0)
_H1_SYMBOL_LIST = dict(_EMPTY_CANON, symbol_timing=[
    {"name": "H0", "low_us": 7.2}, {"name": "H1", "low_us": 1.8},
    {"name": "BR", "low_us": 13.8}, {"name": "IBT", "low_us": 22.0}])
_H1_WINDOWS = dict(_EMPTY_CANON, break_window={"min_us": 13, "max_us": 20},
                   ibt_window={"min_us": 20, "max_us": 30})
_H2_MASTER_SLAVE = {"timing_groups": {
    "master_side": {"bit0_low_us": 7.2, "bit1_low_us": 1.8},
    "slave_side": {"bit0_low_us": 7.0, "bit1_low_us": 2.0}}}
_H2_PROTOCOL = {"protocol_timing": {"rx_side": {"H0": 7, "H1": 2},
                                    "tx_side": {"H0": 7, "H1": 2}}}
_H2_LAYER = {"TX_IBT_us": 70, "BR_MIN_us": 62}


def test_scalar_symbol_keys_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _H1_SCALARS, "missing_rx_group")


def test_a_symbol_timing_list_fails_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _H1_SYMBOL_LIST, "missing_rx_group")


def test_break_and_ibt_windows_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _H1_WINDOWS, "missing_rx_group")


def test_master_and_slave_sides_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _H2_MASTER_SLAVE, "missing_rx_group")


def test_nested_rx_tx_sides_fail_instead_of_nabs(tmp_path):
    _assert_fails(tmp_path, _H2_PROTOCOL, "missing_rx_group")


def test_the_layer_constants_are_read_before_any_absence(tmp_path):
    """The clause passes --layer; check() falls back to it for IBT<BR. An L8
    with nothing in it and constants TX_IBT_us=70 >= BR_MIN_us=62 must go to
    check(), not be certified absent."""
    _assert_fails(tmp_path, dict(_EMPTY_CANON), "missing_rx_group",
                  layer=_H2_LAYER)


def test_whole_symbols_not_substrings():
    import internal_vs_external_timing_check as G
    assert G._symbols_in({"LIBRARY": 1, "CALIBRATION": 1, "FABRIC": 1,
                          "CH0": 1}) == set()
    assert G._symbols_in({"tIBT_us": 1, "tB_break_us": 1, "H0_low": 1,
                          "H1": 1}) == {"H0", "H1", "BR", "IBT"}
    assert G.timing_content_probe({"source_documents": ["L2_fabric.md"],
                                   "calibration": {"LIBRARY": 3}}) == []


def test_spm_run22_with_its_layer_constants_still_reads_nabs(tmp_path):
    """The flow clause passes --layer; spm's constants carry
    `rx_classifier_ticks: null` and `no_rx_classifier_ticks_in_input: true`
    -- declarations of absence, not timing."""
    proj = _project(tmp_path, SPM_L8)
    lp = proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json"
    shutil.copyfile(SPM_LAYER, lp)
    rep = proj / "gate.json"
    r = subprocess.run([sys.executable, str(GATE), str(proj / L8_REL),
                        "--layer", str(lp), "--json", str(rep)],
                       capture_output=True, text=True)
    doc = json.loads(rep.read_text())
    assert r.returncode == 0, doc
    assert doc["reason_class"] == NABS, doc
    assert doc["structural_absence"]["found"] == 0, doc


# ── both directions through the wrapper ────────────────────────────────────

_CMD_LAYER = (f"internal_vs_external_timing_check {L8_REL} --layer "
              "phase1/generated_docs/L8_RTL_CONSTANTS.json "
              "--json reports/phase2/gates/int_vs_ext_timing.json")


def test_through_the_wrapper_timing_on_the_page_is_never_not_applicable(
        tmp_path):
    proj = _project(tmp_path, _H1_SCALARS)
    (proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json").write_text("{}")
    res = F._check_program_exit_zero(proj, _CMD_LAYER)
    assert res.verdict == "FAIL", (res.verdict, res.reason_class, res[1])
    assert res.reason_class != NABS


def test_through_the_wrapper_spm_with_its_layer_is_not_applicable(tmp_path):
    proj = _project(tmp_path, SPM_L8)
    shutil.copyfile(SPM_LAYER,
                    proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json")
    res = F._check_program_exit_zero(proj, _CMD_LAYER)
    assert (res.verdict, res.reason_class) == ("NOT_APPLICABLE", NABS), (
        res.verdict, res.reason_class, res[1])


def test_through_the_wrapper_layer_timing_is_never_not_applicable(tmp_path):
    """H2(a) end to end: an L8 with nothing in it, the clause's own --layer
    constants carrying TX_IBT_us >= BR_MIN_us. Base fail-closed (INCOMPLETE);
    8646e1862 published NOT_APPLICABLE; the gate must be asked instead."""
    proj = _project(tmp_path, dict(_EMPTY_CANON))
    (proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json").write_text(
        json.dumps(_H2_LAYER))
    res = F._check_program_exit_zero(proj, _CMD_LAYER)
    assert res.verdict == "FAIL", (res.verdict, res.reason_class, res[1])
    assert res.reason_class != NABS
