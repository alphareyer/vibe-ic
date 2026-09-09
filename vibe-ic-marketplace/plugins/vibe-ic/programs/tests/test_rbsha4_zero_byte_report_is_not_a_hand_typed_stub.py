#!/usr/bin/env python3
"""A ZERO-BYTE report is not a hand-typed stub, and must not be accused as one.

MEASURED 2026-09-07 on 8HD-6 (lane rbsha4), sha256 x sky130A, v1.19.26,
image 0.3.49 -- reports/phase3/drc_router.json, one file, THREE findings:

    INFO  DRC_REPORT_EMPTY        "2 of 3 discovered DRC report(s) are PRESENT
                                   and EMPTY -- the tool wrote its report and
                                   had no violation to write, read as ZERO
                                   violations."
    ERROR DRC_REPORT_TOO_SMALL    "report 0 B is below minimum 2048 B --
                                   suggests a hand-typed stub, not a real drc
                                   tool output"
    ERROR DRC_NO_TOOL_SIGNATURE   "report lacks any known drc tool signature
                                   ... Hand-typed reports rejected."

about the SAME file (phase3/stage3/pnr/routed_router.drc.rpt, 0 B). The three
cannot all be true. This repo's own position is the first one -- see
test_drc_report_absent_empty_populated.py: "EMPTY means the question WAS put
and the answer is zero -- OpenROAD's `detailed_route -output_drc` writes a
zero-byte file exactly when it found no residual violation."

So a CLEAN route is, by the flow's own design, a 0-byte file, and the stub
screens accuse every clean route of being hand-typed. A 0-byte file has no
typed content to be a stub and no text to carry a signature; the screens are
about a SMALL FILE WITH CONTENT, and the empty-report rule already owns the
empty case.

WHAT THIS DOES NOT DO. It does not make an empty report count as authentic --
an empty file testifies to nothing, so it must not satisfy the authenticity
requirement either. It only stops the false ACCUSATION. The verdict logic is
untouched, which is why the negative controls below matter more than the
positive one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import eda_report_audit as era  # noqa: E402

ACCUSATIONS = ("DRC_REPORT_TOO_SMALL", "DRC_NO_TOOL_SIGNATURE")

# A real, corroborated OpenROAD router DRC report, comfortably over the floor.
REAL = ("OpenROAD detailed_route DRC report\n"
        "detailed_route\n" + ("# routing violation detail line\n" * 200))


def _accusations_against(result, path):
    return sorted(f.rule for f in result.findings
                  if getattr(f, "file", None) == str(path)
                  and f.rule in ACCUSATIONS)


def test_a_zero_byte_report_is_not_accused_of_being_a_stub(tmp_path):
    """THE DEFECT."""
    empty = tmp_path / "routed_router.drc.rpt"
    empty.write_text("")
    real = tmp_path / "routed.drc.rpt"
    real.write_text(REAL)
    res = era.AuditResult(program="eda_report_audit:drc", passed=False)
    era._check_tool_authenticity([empty, real], "drc", res)
    assert _accusations_against(res, empty) == [], (
        "a 0-byte report was accused of being hand-typed: "
        + repr([(f.rule, f.file) for f in res.findings]))


def test_negative_control_a_small_non_empty_stub_is_still_accused(tmp_path):
    """THE OTHER DIRECTION, and the one that keeps the fix honest: a SMALL FILE
    WITH CONTENT and no tool signature is exactly what the screens exist for."""
    stub = tmp_path / "routed_router.drc.rpt"
    stub.write_text("no violations found\n")     # hand-typed, 20 B, no signature
    res = era.AuditResult(program="eda_report_audit:drc", passed=False)
    era._check_tool_authenticity([stub], "drc", res)
    assert _accusations_against(res, stub) == sorted(ACCUSATIONS), (
        "a hand-typed stub stopped being rejected: "
        + repr([(f.rule, f.file) for f in res.findings]))


def test_negative_control_an_empty_report_is_not_credited_as_authentic(tmp_path):
    """An empty file must not buy authenticity either. Silence is not testimony;
    dropping the accusation must not become a free pass."""
    empty = tmp_path / "routed_router.drc.rpt"
    empty.write_text("")
    res = era.AuditResult(program="eda_report_audit:drc", passed=False)
    assert era._check_tool_authenticity([empty], "drc", res) is False, (
        "an empty report was credited as authentic tool output")


def test_negative_control_a_real_report_is_still_authentic(tmp_path):
    """And a genuine tool report still passes both screens."""
    real = tmp_path / "routed.drc.rpt"
    real.write_text(REAL)
    res = era.AuditResult(program="eda_report_audit:drc", passed=False)
    assert era._check_tool_authenticity([real], "drc", res) is True
    assert _accusations_against(res, real) == []
