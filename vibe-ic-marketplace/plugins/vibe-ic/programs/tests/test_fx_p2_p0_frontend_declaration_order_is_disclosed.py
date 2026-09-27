#!/usr/bin/env python3
"""FX_P2 (4) — the D3 rule at the P0 frontend: slang's declaration-order
refusal is retried once with `--allow-use-before-declare` and DISCLOSED; any
other refusal still FAILs.

MEASURED on subservient (reused serv 1.4.0, 8HD-4, 2026-09-28): with reference_tb,
step 4, LEC and three final-audit gates fixed and phase 1 regenerated, phase 2
still halted: the P0 umbrella FAILed on `p0_tool_frontend_check` ("Yosys
elaboration failed"). Yosys's own output:

    serv_state.v:111:52: error: identifier 'trap_pending' used before its declaration
    serv_state.v:118:37: error: identifier 'trap_pending' used before its declaration

The same portability defect D3 books for strict Icarus. In the image (0.3.83)
`read_slang --allow-use-before-declare` elaborates that shape, and a genuinely
undeclared name still fails ("use of undeclared identifier") with or without it.

Only the EDA tools' answers are faked (`_invoke`), by argv, as measured.
chip-AGNOSTIC: synthetic RTL.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import p0_tool_frontend_check as F  # noqa: E402

FLAG = "--allow-use-before-declare"
LATE = "top.v:2:21: error: identifier 'late' used before its declaration\n"
GHOST = "top.v:2:21: error: use of undeclared identifier 'ghost'\n"


def _project(tmp_path: Path) -> Path:
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "top.v").write_text(
        "module top(input a, output y);\n  assign y = late;\n"
        "  wire late = a;\nendmodule\n")
    return tmp_path


def _fake(monkeypatch, *, strict_err, relaxed_ok):
    calls = []

    def _invoke(tool, args, root, image):
        calls.append((tool, list(args)))
        if tool == "yosys":
            script = args[-1]
            if FLAG in script:
                rc, err = (0, "") if relaxed_ok else (1, strict_err)
            else:
                rc, err = 1, strict_err
            return subprocess.CompletedProcess([tool], rc, "", err)
        return subprocess.CompletedProcess([tool], 0, "", "")

    monkeypatch.setattr(F, "_invoke", _invoke)
    return calls


def test_a_declaration_order_refusal_is_disclosed_not_failed(
        monkeypatch, tmp_path):
    calls = _fake(monkeypatch, strict_err=LATE, relaxed_ok=True)
    r = F.check(_project(tmp_path))
    assert r["passed"] is True, r["findings"]
    assert any("DECLARATION_ORDER_RELAXED" in d
               for d in r.get("disclosures", [])), r
    assert "used before its declaration" in r.get("strict_refusal", "")
    yosys = [a for t, a in calls if t == "yosys"]
    assert len(yosys) == 2 and FLAG not in yosys[0][-1] and FLAG in yosys[1][-1]
    # the flag is a read_slang OPTION, before the sources
    script = yosys[1][-1]
    assert script.index(FLAG) < script.index("top.v")


def test_an_undeclared_identifier_still_fails(monkeypatch, tmp_path):
    _fake(monkeypatch, strict_err=GHOST, relaxed_ok=False)
    r = F.check(_project(tmp_path))
    assert r["passed"] is False
    assert "Yosys elaboration failed" in r["findings"]
    assert not r.get("disclosures")


def test_a_clean_strict_elaboration_makes_one_call(monkeypatch, tmp_path):
    calls = []

    def _invoke(tool, args, root, image):
        calls.append(tool)
        return subprocess.CompletedProcess([tool], 0, "", "")

    monkeypatch.setattr(F, "_invoke", _invoke)
    r = F.check(_project(tmp_path))
    assert r["passed"] is True and calls.count("yosys") == 1
    assert not r.get("disclosures")
