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


def _gate(tmp_path: Path, l8):
    proj = _project(tmp_path, l8)
    rep = proj / "gate.json"
    r = subprocess.run([sys.executable, str(GATE), str(proj / L8_REL),
                        "--json", str(rep)], capture_output=True, text=True)
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


def _assert_fails(tmp_path, l8, *rules):
    rc, rep = _gate(tmp_path, l8)
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


def test_the_escape_has_no_vocabulary_of_its_own():
    """ONE CLASSIFIER. The escape must ask check()'s functions, not a second
    token list that can drift from RX_NAME_HINTS / TX_NAME_HINTS."""
    src = GATE.read_text()
    assert "_PROTO_GROUP_TOKENS =" not in src
    assert "classified_groups(waveform)" in src.split("def main(", 1)[1]


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
