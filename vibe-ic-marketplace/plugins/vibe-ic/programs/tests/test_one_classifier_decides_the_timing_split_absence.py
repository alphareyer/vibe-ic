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


def test_the_escape_decides_by_the_l8_schema_not_by_words():
    """R-0915-153. Three rounds of deciding by words traded a false FAIL for a
    false NABS. The escape reads the L8 SCHEMA the emitters declare, and no
    token vocabulary at all."""
    src = GATE.read_text().split("def main(", 1)[1]
    assert "import l8_timing_schema as _schema" in src
    for gone in ("timing_content_probe", "_PROTO_GROUP_TOKENS",
                 "_TIMING_SIDE_WORDS", "_PROVENANCE_FIELDS"):
        assert gone not in GATE.read_text(), gone


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


# ── round 3 (review ictier1c-r2): decide by FIELD, tokenize identifiers ─────

def _spm_plus(**extra):
    doc = json.loads(SPM_L8.read_text())
    doc.update(extra)
    return doc


#: FALSE-FAIL shapes: naming / provenance, where main returned VACUOUS_PASS.
_NAMING_SHAPES = {
    "waveform_caption": _spm_plus(waveforms=[
        {"name": "Wishbone master read cycle",
         "signal": [{"name": "clk", "wave": "p...."}]}]),
    "constant_named_master_clk": _spm_plus(timing_constants=[
        {"name": "master_clk", "value": 10.0, "unit": "MHz"}]),
    "asciidoc_source_document": _spm_plus(source_documents=[
        "L3_external_interface.asciidoc"]),
}
#: NARROW shapes: timing on the page that round 2 missed.
_TIMING_SHAPES = {
    "slash_joined_key": dict(_EMPTY_CANON, **{"H0/H1/BR/IBT_low_us": 7.2}),
    "list_sentence": dict(_EMPTY_CANON, timing_parameters=["H0 low 7.2us"]),
    "fused_TIBT_US": dict(_EMPTY_CANON, TIBT_US=22.0),
    "fused_TBR_MIN_US": dict(_EMPTY_CANON, TBR_MIN_US=13.0),
    "fused_IBTmin": dict(_EMPTY_CANON, IBTmin=20.0),
    "fused_H0low": dict(_EMPTY_CANON, H0low=7.2),
    "fused_tbreak_us": dict(_EMPTY_CANON, tbreak_us=13.8),
}


def _wrapper(tmp_path, l8, layer=None):
    proj = _project(tmp_path, l8)
    lp = proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json"
    lp.write_text(json.dumps(layer if layer is not None else {}))
    return F._check_program_exit_zero(proj, _CMD_LAYER)


import pytest                                                 # noqa: E402


@pytest.mark.parametrize("shape", sorted(_NAMING_SHAPES))
def test_naming_and_provenance_never_block_the_absence(tmp_path, shape):
    rc, rep = _gate(tmp_path / "g", _NAMING_SHAPES[shape])
    assert (rc, rep.get("reason_class")) == (0, NABS), (shape, rep)
    res = _wrapper(tmp_path / "w", _NAMING_SHAPES[shape])
    assert (res.verdict, res.reason_class) == ("NOT_APPLICABLE", NABS), (
        shape, res.verdict, res.reason_class, res[1])


@pytest.mark.parametrize("shape", sorted(_TIMING_SHAPES))
def test_timing_the_gate_reads_is_never_certified_absent(tmp_path, shape):
    rc, rep = _gate(tmp_path / "g", _TIMING_SHAPES[shape])
    assert rep.get("reason_class") != NABS and rc == 1, (shape, rep)
    res = _wrapper(tmp_path / "w", _TIMING_SHAPES[shape])
    assert res.verdict == "FAIL", (shape, res.verdict, res.reason_class)


def test_fused_layer_constants_are_read_the_way_check_reads_them(tmp_path):
    """TBR_MIN_US is what check()'s _find_numeric_us reads from --layer."""
    rc, rep = _gate(tmp_path, dict(_EMPTY_CANON),
                    layer={"TX_IBT_us": 70, "TBR_MIN_US": 62})
    assert rep.get("reason_class") != NABS and rc == 1, rep


def test_fused_symbols_count_for_check_too():
    import internal_vs_external_timing_check as G
    assert G._symbols_in({"TIBT_US": 1, "TBR_MIN_US": 1, "H0low": 1,
                          "H1_ns": 1}) == {"IBT", "BR", "H0", "H1"}
    assert G._symbols_in({"LIBRARY": 1, "BRAM": 1, "CH0": 1}) == set()


# ── R-0915-153: the L8 schema decides; --layer only as check() reads it ─────

def test_an_unknown_l8_key_fails_closed(tmp_path):
    """A key no emitter declares non-protocol is not evidence of absence."""
    _assert_fails(tmp_path, dict(_EMPTY_CANON, frame_waveform={"a": 1}),
                  "missing_rx_group")


_LAYER_WORDS_THAT_ARE_NOT_TIMING = {
    # wishbone_protocol_synth (review wttwkqmyu): "master" on a list key
    "key_constants_for_RTL_authoring": {
        "wishbone_min_master_signals": ["CLK_I", "RST_I", "ADR_O"]},
    "SHA256_H0_0": "0x6a09e667",      # SHA-2 H(0)
    "TH1_RELOAD": 253,                # 8051 timer reload
    "OPCODE_BR": 22,                  # a branch opcode
}


def test_the_layer_is_read_only_as_check_reads_it(tmp_path):
    """check() reads --layer ONLY through _find_numeric_us TX_IBT / BR_MIN;
    the escape may not walk it with any vocabulary. Red on f75e3161b."""
    proj = _project(tmp_path, SPM_L8)
    lp = proj / "phase1/generated_docs/L8_RTL_CONSTANTS.json"
    lp.write_text(json.dumps(_LAYER_WORDS_THAT_ARE_NOT_TIMING))
    rep = proj / "gate.json"
    r = subprocess.run([sys.executable, str(GATE), str(proj / L8_REL),
                        "--layer", str(lp), "--json", str(rep)],
                       capture_output=True, text=True)
    doc = json.loads(rep.read_text())
    assert (r.returncode, doc.get("reason_class")) == (0, NABS), doc
    res = _wrapper(tmp_path / "w", SPM_L8, layer=_LAYER_WORDS_THAT_ARE_NOT_TIMING)
    assert (res.verdict, res.reason_class) == ("NOT_APPLICABLE", NABS), (
        res.verdict, res.reason_class)


def test_the_schema_is_the_emitters(tmp_path):
    """The allowlist is checked against the emitters, not trusted: the base
    L8 emitter's document holds only schema keys, and the clock emitters'
    key names are the schema's."""
    import l8_timing_schema as S
    import l8_clock_reset_waveform_emit as crw
    import l8_doc_clock_freq_synth as clk
    import l_doc_generator_stamp as stamp
    import phase1_doc_one_shot_runner as P1
    proj = tmp_path / "proj"
    (proj / "phase1" / "generated_docs").mkdir(parents=True)
    P1.gen_l8_timing_waveform_doc(proj, {})
    doc = json.loads((proj / L8_REL).read_text())
    known = set(S.NON_PROTOCOL_KEYS) | set(S.PROTOCOL_TIMING_CONTAINERS)
    assert set(doc) <= known, sorted(set(doc) - known)
    assert {crw.L8_KEY, clk.SCALAR_KEY, stamp.STAMP_KEY,
            *clk._CLOCK_LIST_KEYS} <= S.NON_PROTOCOL_KEYS
