#!/usr/bin/env python3
"""The run-time half of #2210's interface proof stated what it had not established.

v1.20.20 landed the attribution half and its message claims, in capitals, "AND THE
RUN-TIME HALF TAKES NOTHING ON TRUST FROM THE FREEZE". That claim was incomplete.
`_interface_omission_reason` re-earned the CANDIDATE binding, the module
declaration and the compiler's own evidence -- and then interpolated
`direction`, `width` and `excerpt` into the attributed reason verbatim, and never
compared the proof's `challenge_sha256` to the challenge it was riding on.

MEASURED against v1.20.29 (48ef2aa29) on the pinned image, driving
`_run_verification_challenge` with the issue's own synthetic bundle -- the same
boundary the issue's shipped `repro.py` drives. Four of the seven ways of being
wrong that #2210's acceptance section requires to fail closed still produced an
ATTRIBUTED CANDIDATE_BROKEN:

    stale candidate hash       -> INVALID           (already closed)
    stale challenge hash       -> CANDIDATE_BROKEN  *** still attributed ***
    unsupported port name      -> INVALID           (already closed)
    wrong module / wrong-top   -> INVALID           (already closed)
    fabricated excerpt         -> CANDIDATE_BROKEN  *** still attributed ***
    wrong direction            -> CANDIDATE_BROKEN  *** still attributed ***
    wrong width                -> CANDIDATE_BROKEN  *** still attributed ***

WHY IT IS A DEFECT EVEN THOUGH PRODUCTION IS GATED. `_challenge_from_review`
attaches a proof only after `_interface_proof_from_review` validates it, so a
single review round cannot reach this. What can is any consumer of the runtime
boundary -- the issue's own reproducer is one -- and the record it writes then
asserts a direction, a width and an excerpt that nothing in that run supports.
An attributed interface defect is a certificate; a certificate must not state a
binding it did not check.

THE FIX ASKS ONLY WHAT IT CAN ASK. Whether the excerpt is truly the public input
is answerable only where the prompt is, so that provenance is now NAMED in the
reason instead of implied. Everything else -- that the excerpt names the port,
states the claimed direction, and states a claimed width above 1 -- is a property
of the proof itself, needs no prompt, and is re-asked through the SAME helper the
freeze uses, so a rule cannot be tightened in one half and left loose in the other.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent


def _dispatch():
    spec = importlib.util.spec_from_file_location(
        "bd_2210_runtime", PROGRAMS / "benchmark_dispatch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BD = _dispatch()

pytestmark = pytest.mark.skipif(
    not (shutil.which("iverilog") and shutil.which("vvp")),
    reason="iverilog/vvp unavailable; the joint compile is the subject here")

PROMPT = (
    "Implement module sample_link with input wire clk, output wire ready, and\n"
    "inout wire io_line. The ready output must always be high. The io_line port\n"
    "must be high impedance so an external device can drive either logic value.\n"
    "These three named ports are the complete public module interface.\n")

CANDIDATE_MISSING = """module sample_link (
    input wire clk,
    output wire ready,
    input wire sampled_line,
    output wire driven_line
);
    assign ready = 1'b1;
    assign driven_line = 1'b0;
endmodule
"""

CHALLENGE = """`timescale 1ns/1ps
module vibeic_ai_challenge_tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg drive = 0;
    tri line;
    assign line = drive;
    wire ready;
    sample_link dut (.clk(clk), .ready(ready), .io_line(line));
    initial begin
        #1;
        if (ready !== 1'b1) begin
            $display("VIBEIC_AI_CHALLENGE=FAIL");
            $fatal(1, "ready");
        end
        $display("VIBEIC_AI_CHALLENGE=PASS");
        $finish;
    end
    initial begin
        #100;
        $display("VIBEIC_AI_CHALLENGE=FAIL");
        $fatal(1, "watchdog");
    end
endmodule
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


#: What an unfixed tree returns for a proof whose prompt-derived claims are
#: unsupported: it ATTRIBUTES anyway. Named so each assertion below reads as "not
#: this any more" and fails on the comparison rather than on a missing symbol.
UNFIXED_ATTRIBUTES = "CANDIDATE_BROKEN"


@pytest.fixture()
def bench(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    rtl = d / "candidate.v"
    rtl.write_text(CANDIDATE_MISSING)
    ch = d / "challenge_tb.sv"
    ch.write_text(CHALLENGE)
    snap = {
        "schema": "vibeic.benchmark.candidate_snapshot.v1",
        "id": "b",
        "rtl_paths": [str(rtl)],
        "rtl_sha256": _sha(CANDIDATE_MISSING),
        "completion_path": str(d / "completion.txt"),
        "response_payload_path": str(d / "payload.json"),
        "manifest_path": str(d / "manifest.json"),
    }
    (d / "completion.txt").write_text(CANDIDATE_MISSING)
    (d / "payload.json").write_text(json.dumps({"completion": CANDIDATE_MISSING}))
    (d / "manifest.json").write_text(json.dumps(snap, indent=2) + "\n")
    assert BD._validate_candidate_snapshot(snap, "b") == []
    return snap, {"path": str(ch), "sha256": _sha(CHALLENGE)}


def _proof(snap, challenge, **over):
    body = {
        "schema": BD._INTERFACE_PROOF_SCHEMA,
        "module": "sample_link",
        "port": "io_line",
        "direction": "inout",
        "width": 1,
        "excerpt": "inout wire io_line",
        "supports": "io_line is a required public port of sample_link",
        "candidate_rtl_sha256": snap["rtl_sha256"],
        "challenge_sha256": challenge["sha256"],
        "prompt_sha256": _sha(PROMPT),
        "public_source_sha256": _sha(PROMPT),
    }
    body.update(over)
    return body


def _run(snap, challenge, **over):
    ch = dict(challenge)
    ch["interface_proof"] = _proof(snap, challenge, **over)
    return BD._run_verification_challenge(snap, ch)


# --- the case this exists for: what the run must re-earn ---------------------

def test_a_proof_frozen_for_another_challenge_is_not_spent_on_this_one(bench):
    """THE MISSING BINDING. The candidate binding stops a parent's proof being
    spent on its child; nothing stopped a proof frozen for one CHALLENGE riding
    on another, and a challenge is inherited across repair rounds."""
    snap, challenge = bench
    res = _run(snap, challenge, challenge_sha256="0" * 64)
    assert res["status"] != UNFIXED_ATTRIBUTES, (
        "a proof bound to a different challenge still attributed", res)
    assert res["status"] == "INVALID", res


@pytest.mark.parametrize("label,over", [
    ("fabricated excerpt", {"excerpt": "io_line must never float"}),
    ("wrong direction", {"direction": "input"}),
    ("wrong width", {"width": 8}),
])
def test_the_reason_cannot_state_an_interface_the_excerpt_does_not_support(
        bench, label, over):
    """These three values are interpolated into the attributed reason verbatim.
    Each is a property of the PROOF, needs no prompt to check, and was not
    checked -- so the certificate could assert an interface nothing supported."""
    snap, challenge = bench
    res = _run(snap, challenge, **over)
    assert res["status"] != UNFIXED_ATTRIBUTES, (label, res)
    assert res["status"] == "INVALID", (label, res)


def test_the_bindings_that_already_failed_closed_still_do(bench):
    """CONTROL. v1.20.20 already refused these three; a change that made the
    run-time half stricter must not have reached them by accident, or this
    branch would be measuring its own rewrite rather than a residual."""
    snap, challenge = bench
    for over in ({"candidate_rtl_sha256": "0" * 64},
                 {"port": "io_lien"},
                 {"module": "other_module"}):
        assert _run(snap, challenge, **over)["status"] == "INVALID", over


def test_a_conforming_proof_still_attributes(bench):
    """THE OTHER TAIL, and the one that matters most: closing the holes must not
    close the capability. A whole and honest proof still produces the attributed
    interface result v1.20.20 landed."""
    snap, challenge = bench
    res = _run(snap, challenge)
    assert res["status"] == UNFIXED_ATTRIBUTES, res
    joined = " ".join(str(r) for r in res["reasons"])
    assert "REQUIRED public interface" in joined, joined
    assert "io_line" in joined and "inout" in joined, joined


def test_no_proof_at_all_is_still_the_undetermined_disclosure(bench):
    """v1.20.9's fall-through is untouched."""
    snap, challenge = bench
    res = BD._run_verification_challenge(snap, challenge)
    assert res["status"] == "INVALID", res
    assert "ATTRIBUTION UNDETERMINED" in " ".join(
        str(r) for r in res["reasons"]), res


def test_the_reason_names_the_one_binding_this_run_cannot_re_earn(bench):
    """Whether the excerpt is truly the public input is answerable only where the
    prompt is. Naming that provenance is the honest form; asserting it flatly is
    what let the record overstate what the run checked."""
    snap, challenge = bench
    joined = " ".join(str(r) for r in _run(snap, challenge)["reasons"])
    assert "provenance" in joined, joined
    assert "re-earned on this run" in joined, joined


# --- one rule set, not two --------------------------------------------------

def test_both_halves_ask_the_same_prompt_free_questions():
    """The freeze and the compile must not be able to drift apart. Asserted over
    the AST: exactly one helper holds the prompt-free rules and BOTH halves call
    it, so tightening one tightens both."""
    import ast
    tree = ast.parse((PROGRAMS / "benchmark_dispatch.py").read_text())
    callers = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_interface_proof_internal_reasons"):
                callers.add(node.name)
    assert callers == {"_interface_proof_from_review",
                       "_interface_omission_reason"}, callers


def test_the_helper_is_prompt_free_by_construction():
    """It must not grow a prompt argument: the compile-time half has no prompt to
    give it, so a prompt parameter would make one caller pass None and the shared
    rule would quietly become two rules again."""
    import ast
    tree = ast.parse((PROGRAMS / "benchmark_dispatch.py").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == "_interface_proof_internal_reasons"):
            names = [a.arg for a in node.args.args]
            assert names == ["proof"], names
            return
    pytest.fail("_interface_proof_internal_reasons is not defined")
