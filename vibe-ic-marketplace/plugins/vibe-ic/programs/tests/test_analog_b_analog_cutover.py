"""Criterion b-analog (q7, T109): an analog observer step's cut-over is judged
on the three channels it can reach downstream through — files (b1), verdicts
(b2) and closed-loop re-entry (b3). Each channel is shown to FAIL on the
defect it exists for and to PASS on two arms that differ only by the step's
own records.
"""
from __future__ import annotations

import json
from pathlib import Path

import _plugin_tree  # noqa: F401 — puts programs/ on sys.path
import analog_b_analog_cutover as B

FLOW = Path(_plugin_tree.plugin_path("flow")) / "phase1_phase2_phase3.yaml"


def _arm(root: Path, name: str, extra: dict = None) -> Path:
    p = root / name
    files = {"phase3/analog/blk/blk.sp": "x",
             "phase3/analog/blk/blk.gds": "g",
             "phase3/analog/hardmacro/blk/blk.gds": "g",
             "phase3/analog/hardmacro/blk/blk.lef": "l",
             "phase3/analog/hardmacro/blk/blk.lib": "b",
             "phase3/analog/hardmacro/blk/blk.v": "v"}
    files.update(extra or {})
    for rel, text in files.items():
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return p


def _drive(statuses: dict, comp: dict = None) -> dict:
    return {"blocks": ["blk"],
            "runner": [{"block": "blk", "step": s, "status": v}
                       for s, v in statuses.items()],
            "compliance": (comp if comp is not None
                           else {"A7": {"status": "NOT_MEASURED"}})}


_D = {"A6": "FAIL", "A7": "WAIVED", "A8": "PASS", "A9": "PASS"}
_T = {"A6": "FAIL", "A7": "PASS", "A8": "PASS", "A9": "PASS"}


def _pvp(delta: float) -> str:
    return json.dumps({"specs": [{"name": "vout@ngspice()",
                                  "delta_pct": delta}]})


def test_two_arms_that_differ_only_by_the_steps_own_records_pass(tmp_path):
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T", {"phase3/analog/blk/pre_vs_post.json": _pvp(0.1),
                             "phase3/analog/blk/a7_post_layout.json": "{}"})
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(_T), None, FLOW)
    assert rec["result"] == "PASS", rec
    assert rec["b2"]["differences"][0]["allowed"].startswith("WAIVED->PASS")
    # the same allowance in the collapsed vocabulary (R-0915-85)
    rec = B.compare(base, d, t, "A7",
                    _drive(dict(_D, A7="PASS_WITH_WAIVERS")), _drive(_T),
                    None, FLOW)
    assert rec["b2"]["pass"] is True
    # and never the other way round
    rec = B.compare(base, d, t, "A7", _drive(_T),
                    _drive(dict(_D, A7="PASS_WITH_WAIVERS")), None, FLOW)
    assert rec["b2"]["pass"] is False


def test_b1_a_deck_the_tool_arm_writes_beside_the_block_fails(tmp_path):
    """The measured defect: 11 resimulation decks under phase3/analog, which
    A3's gates rglob."""
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T", {"phase3/analog/blk/post_layout/tb_post.sp": "x"})
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(_T), None, FLOW)
    assert rec["result"] == "FAIL"
    assert rec["b1"]["population_only_tool"] == [
        "phase3/analog/blk/post_layout/tb_post.sp"]


def test_b1_a_changed_pre_existing_file_or_a8_product_fails(tmp_path):
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T", {"phase3/analog/hardmacro/blk/blk.lef": "L2"})
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(_T), None, FLOW)
    assert rec["b1"]["pass"] is False
    assert "phase3/analog/hardmacro/blk/blk.lef" in \
        rec["b1"]["pre_existing_changed_across_arms"]
    assert rec["b1"]["a8_products"][
        "phase3/analog/hardmacro/blk/blk.lef"]["identical"] is False


def test_b2_a_new_fail_needs_an_independent_control(tmp_path):
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T")
    bad = dict(_T, A8="FAIL")
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(bad), None, FLOW)
    assert rec["b2"]["pass"] is False
    ctl = {"confirmed": {"blk/A8": "LEF pin count re-measured by klayout"}}
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(bad), ctl, FLOW)
    assert rec["b2"]["pass"] is True
    # WAIVED->PASS is allowed only at the step under test
    rec = B.compare(base, d, t, "A7", _drive(dict(_D, A9="WAIVED")),
                    _drive(_T), None, FLOW)
    assert rec["b2"]["pass"] is False


def test_b2_a_missing_compliance_record_is_not_a_pass(tmp_path):
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T")
    rec = B.compare(base, d, t, "A7", _drive(_D, comp={}), _drive(_T), None,
                    FLOW)
    assert rec["b2"]["pass"] is False


def test_b3_a_degradation_past_the_declared_threshold_only_in_the_tool_arm_fails(
        tmp_path):
    d = _arm(tmp_path, "D")
    base = B.manifest(d)
    t = _arm(tmp_path, "T", {"phase3/analog/blk/pre_vs_post.json": _pvp(12.5)})
    rec = B.compare(base, d, t, "A7", _drive(_D), _drive(_T), None, FLOW)
    assert rec["b3"]["threshold_pct"] == 10.0     # read from the flow YAML
    assert rec["b3"]["tool_only"] == {"blk": ["vout@ngspice()"]}
    assert rec["result"] == "FAIL"
