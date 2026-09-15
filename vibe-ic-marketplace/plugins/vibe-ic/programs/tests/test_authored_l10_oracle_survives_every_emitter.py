#!/usr/bin/env python3
"""An authored L10 case oracle must survive EVERY producer emitter, not one.

MEASURED (lane icsub3, 2026-09-16, subservient x gf180mcuD, main 524f538b7):
`testbench_gen.emit_unit_tbs` tries four emitters per declared case, in order —

    1. _emit_case_known_answer_vector
    2. _emit_case_golden_oracle
    3. _emit_case_boot_latency_oracle
    4. emit_unit_tb                      (the substance-floor scaffold)

`emit_unit_tb` has carried the v1.15.45 rule "NEVER regenerate over an authored
oracle" since the same clobber was closed for `professional_tb_gen`.  The three
emitters that run BEFORE it — the ones that reach a case FIRST — called
`f.write_text(text)` unconditionally.  ORGANIC #761 widened all three to run for
EVERY case precisely so a case Phase 1 mistyped could still get a real golden,
which means the set of cases they reach grows over time: the first invocation
after an emitter learns to ground a case silently erases the oracle an author
already wrote for it, and the evidence that it ever existed.

The fix distinguishes PROVENANCE rather than freezing the files: this producer
stamps `ORACLE_GENERATED_MARKER` into its own output, so its own output stays
refreshable and only a file carrying NEITHER marker — i.e. one somebody else
authored — is preserved.

Chip-AGNOSTIC: synthetic case names, a synthetic two-port DUT, no chip, vendor,
node or design-name literal anywhere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import testbench_gen as TBG                        # noqa: E402
import arith_oracle_tb_gen as AOG                  # noqa: E402
import cpu_boot_latency_oracle_tb_gen as CLG       # noqa: E402
import known_answer_vector as KAV                  # noqa: E402
import known_answer_vector_tb_gen as KTB           # noqa: E402

DUT = """`timescale 1ns/1ps
module unit_under_test (
  input  wire       clk_in,
  input  wire       rst_in,
  output wire       busy_out
);
  reg r = 1'b0;
  always @(posedge clk_in) r <= rst_in ? 1'b0 : ~r;
  assign busy_out = r;
endmodule
"""

L10 = {
    "schema_version": 2,
    "doc_class": "test_cases",
    "ic_name": "unit_under_test",
    "test_cases": [{"name": "case_alpha", "kind": "functional_vector",
                    "stimulus": "drive the declared vector",
                    "expected": "the declared response"}],
}

PORTS = [("clk_in", "input", ""), ("rst_in", "input", ""),
         ("busy_out", "output", "")]

# A real oracle has no ORACLE_NONE_MARKER in it — that is the whole point.
GENERATED_TEXT = ("// a real per-case oracle\n"
                  "module case_alpha;\n  initial $finish;\nendmodule\n")
GENERATED_TEXT_V2 = GENERATED_TEXT.replace("a real", "a BETTER real")
AUTHORED_TEXT = ("// hand-authored by the testbench-author role\n"
                 "module case_alpha;\n"
                 "  initial begin if (1'b0) $fatal(1); $finish; end\n"
                 "endmodule\n")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L10_TEST_CASES.json").write_text(json.dumps(L10))
    rtl = project / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "unit_under_test.v").write_text(DUT)
    return project


def _tb_dir(project: Path) -> Path:
    d = project / "phase2" / "stage1" / "sim" / "tb"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# the predicate itself
# ---------------------------------------------------------------------------
def test_preserve_predicate_separates_authored_from_producer_output(tmp_path):
    d = _tb_dir(tmp_path / "proj")
    tb = d / "case_alpha.v"

    assert TBG.authored_oracle_preserved(d, "case_alpha", None) is None, \
        "no file at all is not something to preserve"

    tb.write_text(f"// {TBG.ORACLE_NONE_MARKER}\nmodule case_alpha; endmodule\n")
    assert TBG.authored_oracle_preserved(d, "case_alpha", None) is None, \
        "the substance-floor scaffold must stay refreshable"

    tb.write_text(TBG.stamp_generated(GENERATED_TEXT, "_emit_case_golden_oracle"))
    assert TBG.authored_oracle_preserved(d, "case_alpha", None) is None, \
        "this producer's OWN output must stay refreshable across a plugin upgrade"

    tb.write_text(AUTHORED_TEXT)
    report: dict = {}
    kept = TBG.authored_oracle_preserved(d, "case_alpha", report)
    assert kept == tb
    assert report["preserved_authored"][0]["case"] == "case_alpha"


# ---------------------------------------------------------------------------
# each of the three real-oracle emitters, both directions
# ---------------------------------------------------------------------------
def _pin_golden(monkeypatch, text):
    monkeypatch.setattr(AOG, "emit_case_oracle",
                        lambda project, ic_class, name, profile: text)


def _pin_boot(monkeypatch, text):
    monkeypatch.setattr(CLG, "emit_case_oracle_from_ports",
                        lambda case, dut, i, o, io: text)


def _pin_kav(monkeypatch, text):
    monkeypatch.setattr(KAV, "is_known_answer_vector", lambda case: True)
    monkeypatch.setattr(KTB, "emit_case_oracle_from_ports",
                        lambda case, dut, ports: (text, ""))


EMITTERS = [
    ("_emit_case_known_answer_vector", _pin_kav,
     lambda project, case, out, report: TBG._emit_case_known_answer_vector(
         project, case, "unit_under_test", PORTS, out, report)),
    ("_emit_case_golden_oracle", _pin_golden,
     lambda project, case, out, report: TBG._emit_case_golden_oracle(
         project, None, case, out, report)),
    ("_emit_case_boot_latency_oracle", _pin_boot,
     lambda project, case, out, report: TBG._emit_case_boot_latency_oracle(
         project, case, "unit_under_test", PORTS, out, report)),
]


@pytest.mark.parametrize("emitter_name,pin,call", EMITTERS,
                         ids=[e[0] for e in EMITTERS])
def test_emitter_writes_then_refreshes_its_own_output(tmp_path, monkeypatch,
                                                      emitter_name, pin, call):
    """The emitter still OWNS the file it wrote: a later plugin version's text
    replaces it.  Preservation must not freeze the producer's own artefacts."""
    project = _project(tmp_path)
    out = _tb_dir(project)
    case = L10["test_cases"][0]

    pin(monkeypatch, GENERATED_TEXT)
    wrote = call(project, case, out, {})
    assert wrote is not None and wrote.is_file()
    first = wrote.read_text()
    assert TBG.ORACLE_GENERATED_MARKER in first, \
        f"{emitter_name} must stamp its own output so it stays refreshable"
    assert GENERATED_TEXT in first

    pin(monkeypatch, GENERATED_TEXT_V2)
    again = call(project, case, out, {})
    assert again is not None
    assert GENERATED_TEXT_V2 in again.read_text(), \
        f"{emitter_name} refused to refresh a file it wrote itself"


@pytest.mark.parametrize("emitter_name,pin,call", EMITTERS,
                         ids=[e[0] for e in EMITTERS])
def test_emitter_never_regenerates_over_an_authored_oracle(tmp_path, monkeypatch,
                                                           emitter_name, pin,
                                                           call):
    """THE DEFECT.  An oracle authored for this case — carrying neither marker —
    must come back byte-identical, and the preservation must be REPORTED so the
    run can say the case's evidence is somebody's authored work."""
    project = _project(tmp_path)
    out = _tb_dir(project)
    case = L10["test_cases"][0]
    tb = out / "case_alpha.v"
    tb.write_text(AUTHORED_TEXT)

    pin(monkeypatch, GENERATED_TEXT)
    report: dict = {}
    wrote = call(project, case, out, report)

    assert tb.read_text() == AUTHORED_TEXT, (
        f"{emitter_name} regenerated over an authored oracle and erased it")
    assert wrote == tb, (
        f"{emitter_name} must claim the case with the authored file, so the "
        f"caller does not fall through and emit a substance-floor scaffold "
        f"over it instead")
    assert report.get("preserved_authored"), (
        f"{emitter_name} preserved the file without saying so — a run cannot "
        f"report whose evidence this is")
    assert report["preserved_authored"][0]["case"] == "case_alpha"


# ---------------------------------------------------------------------------
# end to end, through the producer entry point the runner actually calls
# ---------------------------------------------------------------------------
def test_emit_unit_tbs_preserves_an_authored_oracle_a_real_emitter_would_claim(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    out = _tb_dir(project)
    tb = out / "case_alpha.v"
    tb.write_text(AUTHORED_TEXT)
    # the widest of the three: a case the boot-latency emitter now grounds
    _pin_boot(monkeypatch, GENERATED_TEXT)

    report: dict = {}
    assert TBG.emit_unit_tbs(project, "unit_under_test", report=report) == 1
    assert tb.read_text() == AUTHORED_TEXT, \
        "the producer entry point erased an authored oracle"
    assert report.get("preserved_authored")
    assert TBG.ORACLE_NONE_MARKER not in tb.read_text(), \
        "no substance-floor scaffold may be written over an authored oracle"
