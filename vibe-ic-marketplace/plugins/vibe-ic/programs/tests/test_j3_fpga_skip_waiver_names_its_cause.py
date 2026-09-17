"""J3 — the FPGA cap-gap waiver names the ONE condition that held.

The synthesised reason said "no DE10-class board-pin contract for this IC class
and/or no Quartus on host". A waiver must name its actual cause; the run records
it in phase2_one_shot.json's fpga_compile detail. MEASURED on subservient r32:
qsf_gen SKIP (a memory-bus core has no DE10 board-pin contract), fpga_compile
SKIP on "fpga/<name>.qsf missing", so Quartus was never reached.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_compliance_check as F  # noqa: E402

QSF = "DE10 QSF generation SKIPPED: class 'processor_cpu' — A memory-bus/data core has no DE10 board-pin contract"


def _proj(tmp_path: Path, compile_detail=None, qsf_detail=QSF) -> Path:
    d = tmp_path / "reports" / "phase2" / "fpga"
    d.mkdir(parents=True)
    (d / "quartus_map_audit.json").write_text(
        json.dumps({"verdict": "SKIP", "sof_present": False}))
    if compile_detail is not None:
        o = tmp_path / "reports" / "orchestrator"
        o.mkdir(parents=True)
        o.joinpath("phase2_one_shot.json").write_text(json.dumps({"steps": [
            {"name": "qsf_gen", "status": "SKIP", "detail": qsf_detail},
            {"name": "fpga_compile", "status": "SKIP", "detail": compile_detail},
        ]}))
    return tmp_path


def _waivers(project: Path):
    out: dict = {}
    F._synthesise_fpga_skip_waivers(project, out)
    assert out, "the disclosed skip must still synthesise its waivers"
    return list(out.values())


@pytest.mark.parametrize("detail,cause,says,never", [
    ("fpga/<name>.qsf missing — caller must produce it",
     "board_pin_contract_absent", "board-pin contract", "Quartus is not available"),
    ("quartus_sh unavailable: not on host and not in container 'x'",
     "quartus_absent", "Quartus is not available", "board-pin contract"),
])
def test_the_recorded_condition_is_the_named_cause(tmp_path, detail, cause,
                                                   says, never):
    for w in _waivers(_proj(tmp_path, detail)):
        assert w["cause"] == cause
        assert says in w["reason"] and never not in w["reason"]
        assert "and/or" not in w["reason"]
        assert "reports/orchestrator/phase2_one_shot.json" in w["evidence"]


def test_the_board_contract_cause_quotes_why_the_qsf_was_not_made(tmp_path):
    w = _waivers(_proj(tmp_path, "fpga/<name>.qsf missing — caller must produce it"))[0]
    assert "memory-bus/data core has no DE10 board-pin contract" in w["reason"]


@pytest.mark.parametrize("detail", [None, "something else entirely"])
def test_an_unrecorded_cause_is_said_not_guessed(tmp_path, detail):
    for w in _waivers(_proj(tmp_path, detail)):
        assert w["cause"] == "cause_not_recorded"
        assert "NOT RECORDED" in w["reason"]
        assert "and/or" not in w["reason"]
        assert "board-pin contract was produced" not in w["reason"]
        assert "Quartus is not available" not in w["reason"]
