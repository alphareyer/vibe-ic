#!/usr/bin/env python3
"""FX_P2 review wave 6 — the input's gap needs a BARE VERDICT, a path names an
image by its leaf, and a case the design declared conditional is never
input_absent.

1 (MAJOR, both lenses). `testbench_gen.case_input_gap` booked a case with no
  named image as the input's gap whenever its expected half carried no number
  or hex (`states_a_checkable_answer` == False). So every expectation written
  as a behaviour in words -- "full flag asserted and further writes are
  ignored", "read value equals written value", "讀回值 = 寫入值" -- was booked
  NOT_MEASURED [EXTERNAL]/input_absent although the input states a result a
  testbench can check. The rule the code claimed was a bare verdict ("PASS").

2 (MINOR). `_STIMULUS_IMAGE_RE`'s lookbehind refused `/`, so `sw/blinky.hex`
  and `input/sim/programs/blinky.hex` were invisible, and the case was booked
  "delivers no program" while the flow's own `delivered_case_program` found it.

3 (lane fxrtl, subservient). The M / Zicsr / C cases state "(若 Plugin 選 M)"
  -- `applies_when.option` -- and were booked input_absent with that condition
  dropped from the reason. A case the design's declaration excludes is
  NOT_APPLICABLE citing that declaration (R-0915-102); while no selection is
  declared it stays in the denominator and its reason says the condition.

Driven through the real gate (`_evaluate`, `main --json`) and the real
classifier; only the testbench EXECUTION record is planted (the shape
`testbench_gen.run_unit_tbs` writes). chip-AGNOSTIC: synthetic cases.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import testbench_gen as TBG                        # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_fx_p2_review_fixture",
    Path(__file__).with_name(
        "test_fx_p2_review_input_gap_needs_positive_evidence.py"))
_FX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_FX)
_project, PASSING, PROSE = _FX._project, _FX.PASSING, _FX.PROSE


def _vec(name, stimulus, expected, **kw):
    return dict({"name": name, "kind": "functional_vector",
                 "stimulus": stimulus, "expected": expected}, **kw)


#: Behaviours stated in words, no number anywhere -- English and Chinese, and
#: the two real L10 shapes the review measured (edge_llm tc3, sha256 protocol).
WORDED = [
    _vec("fifo_full", "write words until the FIFO is full, then write once more",
         "full flag asserted and further writes are ignored"),
    _vec("rw_back", "write a word to a register then read it back",
         "read value equals written value"),
    _vec("irq_high", "enable the timer interrupt and let the counter reach compare",
         "irq output asserts high"),
    _vec("tx_char", "write the character 'A' to the TX data register",
         "the tx line sends the character 'A' framed by a start and a stop bit"),
    _vec("bank_edge", "位址/bank 邊界", "讀回值 = 寫入值;無 wrap / alias 至其他 bank"),
    _vec("led_blink", "啟動計數器", "LED 閃爍"),
    _vec("protocol", "INIT during BUSY / NEXT without prior INIT",
         "命令在 BUSY 期間被忽略"),
]


# ---- 1. a worded behaviour is a checkable result ---------------------------
@pytest.mark.parametrize("case", WORDED, ids=lambda c: c["name"])
def test_a_worded_expectation_is_never_the_inputs_gap(tmp_path, case):
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"})
    assert TBG.case_input_gap(proj, case) is None
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert case["name"] in msg


@pytest.mark.parametrize("expected", ["PASS", "pass.", "Passed", "通過",
                                      "all pass"])
def test_a_bare_verdict_is_still_the_inputs_gap(tmp_path, expected):
    """CONTROL (the measured zifencei shape): nothing but a verdict word."""
    case = _vec("verdict_only", "Zifencei 指令", expected)
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"})
    assert TBG.case_input_gap(proj, case) is not None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 2


# ---- 2. a path names an image by its leaf ----------------------------------
@pytest.mark.parametrize("stimulus,rel", [
    ("執行 input/sim/programs/blinky.hex", "input/sim/programs/blinky.hex"),
    ("run sw/blinky.hex", "input/sw/blinky.hex"),
])
def test_a_path_qualified_image_the_input_has_is_not_a_gap(tmp_path,
                                                           stimulus, rel):
    case = _vec("blinky", stimulus, "PASS")
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"},
                    input_files=[rel])
    assert TBG.case_input_gap(proj, case) is None
    rc, _msg = GATE._evaluate(proj)
    assert rc == 1


def test_a_path_qualified_image_the_input_lacks_is_named_by_leaf(tmp_path):
    case = _vec("blinky", "run sw/blinky.hex", "PASS")
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"})
    gap = TBG.case_input_gap(proj, case)
    assert gap is not None and gap["missing_from_input"] == ["blinky.hex"], gap


# ---- 3. a conditional case -------------------------------------------------
COND = _vec("plugin_m_mul_div", "(若 Plugin 選 M) Mul/Div 指令", "PASS",
            applies_when={"option": "M", "stated": "(若 Plugin 選 M)"})


def _declare(proj: Path, selected) -> None:
    out = proj / "plugin_output" / "declaration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fields": {"isa_extensions": selected}}))


def test_a_conditional_case_is_never_the_inputs_gap(tmp_path):
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    assert TBG.case_input_gap(proj, COND) is None


def test_without_a_declared_selection_the_reason_states_the_condition(
        tmp_path):
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    ran = GATE._oracles_that_actually_ran(proj)
    row = [r for r in ran["not_executed"] if r["case"] == COND["name"]][0]
    assert "applies only if the design selects option 'M'" in row["why"], row
    assert not ran["input_not_supplied"], ran["input_not_supplied"]


def test_a_declaration_that_excludes_it_makes_it_not_applicable(tmp_path):
    """The design's own declaration selects I and Zifencei: the M case is
    NOT_APPLICABLE, cited to the declaration, and blocks nothing."""
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    _declare(proj, ["I", "Zifencei"])
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    na = GATE._design_declared_na_disclosure(proj)
    assert na["decided"] is True
    assert [c["case"] for c in na["cases"]] == [COND["name"]]
    assert "plugin_output/declaration.json" in json.dumps(na)
    assert not GATE._oracles_that_actually_ran(proj)["input_not_supplied"]


def test_a_declaration_that_selects_it_still_demands_it(tmp_path):
    """TEETH: the option IS selected -- the case is demanded (blocking)."""
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    _declare(proj, ["I", "M"])
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)


def test_a_delivered_program_is_never_a_gap_even_when_the_name_is_hidden(
        tmp_path):
    """The polarity-scoped image reader drops a name whose SENTENCE carries
    an unrelated negation ("… do not reset …"), so the named-image test alone
    would call a delivered program missing. `delivered_case_program`, the
    flow's own delivery reader, finds it: never the input's gap."""
    case = _vec("blinky", "load blinky.hex; do not reset the core first",
                "PASS")
    assert TBG.named_stimulus_images(case["stimulus"]) == []
    proj = _project(tmp_path, [PASSING, case], {PASSING["name"]: "PASS"},
                    input_files=["input/sim/programs/blinky.hex"])
    assert TBG.delivered_case_program(proj, case["stimulus"]) is not None
    assert TBG.case_input_gap(proj, case) is None
