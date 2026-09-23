#!/usr/bin/env python3
"""R-0924-2 — a NAME obligation is discharged by BINDING, never by a trace.

`L8 resets.N.name = rst` states which port is the reset. No assertion can
discharge a name, so Step 5 sat on PROOF_CHAIN_PARTIAL forever (spm run23: 4 of
5 obligations proved unbounded, the fifth `resets.0.name`).
`formal_proof_evidence_check` now answers it structurally: the harness binds
the declared port as the reset AND a property guarded by it is asserted AND
this run PROVED it -> DISCHARGED_BY_BINDING with that evidence (port, property,
proof status). Anything less stays outstanding, with the reason.

Through the real programs (`formal_harness_gen.generate` ->
`formal_property_run.run` -> `formal_proof_evidence_check.audit`), in-image:

  bound + proven               -> DISCHARGED_BY_BINDING, gate PASS
  declared port not the reset  -> BINDING_OUTSTANDING, gate not PASS
  covering property refuted    -> BINDING_OUTSTANDING, gate not PASS

Needs yosys + sby on PATH (the vibeic-eda image, as CI and falsref run them);
without them the engine arms FAIL with the reason, never skip. chip-AGNOSTIC.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import formal_harness_gen as fhg  # noqa: E402
import formal_property_run as fpr  # noqa: E402
import formal_proof_evidence_check as gate  # noqa: E402

NAME_ID = "L8.clock_and_reset_waveform.resets.0.name"
RTL = """module ctr(input clk, input rst, input en, output reg [3:0] q);
  always @(posedge clk) if (rst) q <= 4'd0; else if (en) q <= q + 4'd1;
endmodule
"""


def _project(tmp: Path, reset_name: str = "rst") -> Path:
    docs = tmp / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L3_PROTOCOL.json").write_text(json.dumps({"no_opcodes_in_input": True}))
    (docs / "L6_FSM.json").write_text(json.dumps({"no_fsm_in_input": True}))
    (docs / "L8_TIMING_WAVEFORM.json").write_text(json.dumps(
        {"clock_and_reset_waveform": {"resets": [{"name": reset_name}]}}))
    rd = tmp / "phase2/stage1/rtl"
    rd.mkdir(parents=True)
    (rd / "ctr.v").write_text(RTL)
    return tmp


def _step5(project: Path, harness_edit=None):
    missing = [t for t in ("yosys", "sby") if shutil.which(t) is None]
    assert not missing, f"{missing} not on PATH — run inside the vibeic-eda image"
    gen = fhg.generate(project=project, top="ctr")
    assert gen["verdict"] == "EMITTED", gen
    h = Path(gen["harness_path"])
    if harness_edit:
        edited = harness_edit(h.read_text())
        assert edited != h.read_text(), "the harness edit matched nothing"
        h.write_text(edited)
    res = fpr.run(project, harness=h, rtl=[Path(p) for p in gen["rtl_files"]],
                  top=gen["harness_module"], container=None, timeout=300)
    return res, gate.audit(project)


def _binding(rep: dict) -> dict:
    rows = [b for b in rep.get("binding_obligations") or [] if b["id"] == NAME_ID]
    assert len(rows) == 1, rep.get("findings")
    return rows[0]


def test_the_name_obligation_is_classified_as_a_binding():
    row = {"id": NAME_ID, "description":
           "declared temporal behavior clock_and_reset_waveform.resets.0.name=rst"}
    assert fhg.binding_obligation(row) == {"role": "reset", "port": "rst"}
    # behaviour is not a name; prose is not an identifier
    assert fhg.binding_obligation(dict(row, id=NAME_ID.replace("name", "sync"))) is None
    assert fhg.binding_obligation(dict(row, description="x=the reset pin")) is None


def test_bound_and_proven_is_discharged_by_binding(tmp_path):
    res, rep = _step5(_project(tmp_path))
    assert res["all_proved"] is True
    assert [o["id"] for o in res["unresolved_obligations"]] == [NAME_ID]
    b = _binding(rep)
    assert b["status"] == gate.DISCHARGED_BY_BINDING, b
    assert b["port"] == "rst" and b["property"] == "p_reset_safety_1"
    assert "PASS(unbounded)" in b["proof_status"]
    assert rep["discharged_by_binding"] == [NAME_ID]
    assert rep["verdict"] == "PASS", rep["findings"]
    assert "by binding" in rep["findings"][-1]
    # recorded as a binding, never as an authored (trace) property
    results = json.loads((tmp_path / "phase2/stage1/formal/results.json").read_text())
    assert results["authored_property_count"] == res["authored_property_count"]


def test_a_declared_port_that_is_not_the_reset_stays_outstanding(tmp_path):
    res, rep = _step5(_project(tmp_path, reset_name="arst"))
    b = _binding(rep)
    assert b["status"] == gate.BINDING_OUTSTANDING, b
    assert "'arst'" in b["reason"]
    assert rep["verdict"] != "PASS"
    assert NAME_ID in [o["id"] for o in rep.get("unresolved_obligations") or []]


def test_a_refuted_covering_property_stays_outstanding(tmp_path):
    res, rep = _step5(_project(tmp_path),
                      harness_edit=lambda t: t.replace("(q == '0)", "(q == 4'd5)"))
    assert res["all_proved"] is False
    b = _binding(rep)
    assert b["status"] == gate.BINDING_OUTSTANDING, b
    assert "not proven" in b["reason"]
    assert rep["verdict"] != "PASS"


def test_a_real_port_that_is_not_bound_as_the_reset_stays_outstanding(tmp_path):
    # `en` IS a connected DUT port — but the harness's reset is `rst`
    res, rep = _step5(_project(tmp_path, reset_name="en"))
    assert res["all_proved"] is True
    b = _binding(rep)
    assert b["status"] == gate.BINDING_OUTSTANDING, b
    assert "does not bind 'en' as the reset" in b["reason"]
    assert "does not connect" not in b["reason"]
    assert rep["verdict"] != "PASS"
