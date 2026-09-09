#!/usr/bin/env python3
"""A required port the candidate omits is not the same defect as an invented one.

Both produce the SAME compiler diagnostic at the SAME location -- the test's
named-port instantiation -- so `_joint_compile_attribution`, which reads WHICH
FILE the error lines cite, cannot separate them and both become INVALID. A real
interface defect was therefore held with no admissible candidate-side proof.

MEASURED on the pinned image (Icarus Verilog 14.0 (devel)
s20260301-462-ga5d8d1781), synthetic fixtures, both standalone compiles clean:

    candidate omits required `io_line`   joint rc 2   INVALID
    conforming candidate, test typo      joint rc 2   INVALID
    conforming candidate, correct test   joint rc 0   PASS

Compiler source location is the wrong instrument, and no stronger one can be
built from the compile alone: WHICH interface was required is a fact about the
public input, which the joint compile never reads. So the semantic authority
supplies the claim and the PROGRAM validates every binding it rests on -- the
same shape `_validate_ai_review` already uses for override authority.

THE NEGATIVE CONTROL HOLDS BY CONSTRUCTION, not by a rule about typos: the
excerpt must be VERBATIM public input, and a public input that says `io_line`
contains no excerpt naming `io_lien`. Everything else the issue names invalid
-- ambiguous requirements, wrong-top bindings, stale hashes, wrong direction,
test syntax errors -- is pinned below, each failing closed.

chip-AGNOSTIC: synthetic module and port names only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import benchmark_dispatch as bd                          # noqa: E402


def _simulator_absent() -> str:
    """Why this host cannot EXECUTE a joint compile, or "" if it can.

    A live probe of the two binaries the production code itself looks for.
    The freeze-time validator and the diagnostic reader are both pinned
    host-independently below, so nothing goes quiet on a bare host."""
    missing = [t for t in ("iverilog", "vvp") if shutil.which(t) is None]
    if not missing:
        return ""
    return ("NOT_MEASURED: this host has no " + " and no ".join(missing)
            + "; a joint compile cannot be executed here")


_NEEDS_SIMULATOR = pytest.mark.skipif(
    bool(_simulator_absent()),
    reason=_simulator_absent() or "iverilog and vvp are both present")


PROMPT = (
    "Implement module sample_link with input wire clk, output wire ready, and\n"
    "inout wire io_line. The ready output must always be high. The io_line port\n"
    "must be high impedance so an external device can drive either logic value.\n"
    "These three named ports are the complete public module interface.\n")

CANDIDATE_MISSING = (
    "module sample_link (\n"
    "    input wire clk,\n"
    "    output wire ready,\n"
    "    input wire sampled_line,\n"
    "    output wire driven_line\n"
    ");\n"
    "    assign ready = 1'b1;\n"
    "    assign driven_line = 1'b0;\n"
    "endmodule\n")

CANDIDATE_VALID = (
    "module sample_link (\n"
    "    input wire clk,\n"
    "    output wire ready,\n"
    "    inout wire io_line\n"
    ");\n"
    "    assign ready = 1'b1;\n"
    "    assign io_line = 1'bz;\n"
    "endmodule\n")


def _challenge_source(port: str) -> str:
    return f"""`timescale 1ns/1ps
module vibeic_ai_challenge_tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg drive = 0;
    tri line;
    assign line = drive;
    wire ready;
    sample_link dut (.clk(clk), .ready(ready), .{port}(line));
    initial begin
        #1;
        if (ready !== 1'b1 || line !== 1'b0) begin
            $display("VIBEIC_AI_CHALLENGE=FAIL");
            $fatal(1, "external low drive");
        end
        drive = 1;
        #1;
        if (ready !== 1'b1 || line !== 1'b1) begin
            $display("VIBEIC_AI_CHALLENGE=FAIL");
            $fatal(1, "external high drive");
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


def _candidate(tmp_path: Path, name: str, source: str) -> dict:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    rtl = d / "sample_link.v"
    rtl.write_text(source)
    record = {
        "schema": "vibeic.benchmark.candidate_snapshot.v1",
        "id": name,
        "rtl_paths": [str(rtl)],
        "rtl_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "completion_path": str(d / "completion.txt"),
        "response_payload_path": str(d / "payload.json"),
        "manifest_path": str(d / "manifest.json"),
    }
    (d / "completion.txt").write_text(source)
    (d / "payload.json").write_text(json.dumps({"completion": source}))
    (d / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    assert bd._validate_candidate_snapshot(record, name) == []
    return record


def _task(candidate: dict) -> dict:
    return {
        "id": candidate["id"],
        "prompt_sha256": bd._sha256_text(PROMPT),
        "rtl_sha256": candidate["rtl_sha256"],
        "rtl_paths": candidate["rtl_paths"],
        "public_original_input": None,
    }


def _raw_proof(task: dict, challenge_source: str, **over) -> dict:
    proof = {
        "schema": bd._INTERFACE_PROOF_SCHEMA,
        "module": "sample_link",
        "port": "io_line",
        "direction": "inout",
        "width": 1,
        "excerpt": "inout wire io_line",
        "supports": "The public input names this port and states its direction.",
        "prompt_sha256": task["prompt_sha256"],
        "candidate_rtl_sha256": task["rtl_sha256"],
        "challenge_sha256": bd._sha256_text(challenge_source),
    }
    proof.update(over)
    return proof


def _freeze(task: dict, source: str, **over):
    return bd._interface_proof_from_review(
        _raw_proof(task, source, **over), task, PROMPT, source)


# --------------------------------------------------------------------------- #
# the freeze-time bindings -- host-independent
# --------------------------------------------------------------------------- #
def test_a_complete_proof_is_admitted(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"))
    assert reasons == []
    assert proof["port"] == "io_line" and proof["direction"] == "inout"


def test_absent_is_the_default_and_costs_nothing(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    assert bd._interface_proof_from_review(
        None, task, PROMPT, _challenge_source("io_line")) == (None, [])


def test_a_port_the_public_input_never_names_cannot_be_proven(tmp_path):
    """The invented-port control, and it holds by construction: the excerpt
    must be verbatim public input, and this input contains no `io_lien`."""
    task = _task(_candidate(tmp_path, "valid", CANDIDATE_VALID))
    source = _challenge_source("io_lien")
    proof, reasons = _freeze(task, source, port="io_lien",
                             excerpt="inout wire io_lien")
    assert proof is None
    assert any("exact prompt excerpt" in r for r in reasons)


def test_a_mention_without_a_direction_is_ambiguous_not_a_requirement(tmp_path):
    """"the io_line port" is real public input and states no direction, so it
    does not establish an interface requirement."""
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             excerpt="The io_line port")
    assert proof is None
    assert any("states no direction" in r for r in reasons)


def test_an_excerpt_that_does_not_name_the_port_is_refused(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             excerpt="output wire ready")
    assert proof is None
    assert any("does not name the port" in r for r in reasons)


@pytest.mark.parametrize("field, value, expected", [
    ("prompt_sha256", "0" * 64, "not the reviewed prompt"),
    ("candidate_rtl_sha256", "0" * 64, "not the reviewed candidate"),
    ("challenge_sha256", "0" * 64, "not this challenge"),
])
def test_a_stale_hash_fails_closed(tmp_path, field, value, expected):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             **{field: value})
    assert proof is None
    assert any(expected in r for r in reasons)


def test_a_stale_public_source_hash_fails_closed(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    task["public_original_input"] = {"status": "PRESENT",
                                     "source_sha256": "a" * 64}
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             public_source_sha256="b" * 64)
    assert proof is None
    assert any("not the staged public source" in r for r in reasons)


def test_a_wrong_top_binding_is_refused(tmp_path):
    """The candidate declares no such module; that is a wrong-top binding, not
    a port omission, and it must not become a candidate-side defect."""
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             module="some_other_module")
    assert proof is None
    assert any("wrong-top binding" in r for r in reasons)


def test_a_port_the_candidate_already_declares_is_refused(tmp_path):
    task = _task(_candidate(tmp_path, "valid", CANDIDATE_VALID))
    proof, reasons = _freeze(task, _challenge_source("io_line"))
    assert proof is None
    assert any("already declares port" in r for r in reasons)


def test_a_port_the_challenge_never_connects_is_refused(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             port="some_unconnected_port",
                             excerpt="inout wire io_line")
    assert proof is None
    assert reasons


def test_a_direction_the_public_input_does_not_state_is_refused(tmp_path):
    """The excerpt says `inout`; a proof claiming `input` is not evidenced by
    it, and an unevidenced direction is a guessed interface."""
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             direction="input")
    assert proof is None
    assert any("states no direction" in r for r in reasons)


@pytest.mark.parametrize("field, value", [
    ("schema", "vibeic.wrong.v1"),
    ("direction", "sideways"),
    ("width", 0),
    ("width", "one"),
    ("module", "9bad"),
    ("port", "not an identifier"),
])
def test_a_malformed_proof_fails_closed(tmp_path, field, value):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = _freeze(task, _challenge_source("io_line"),
                             **{field: value})
    assert proof is None and reasons


def test_a_non_object_proof_is_refused(tmp_path):
    task = _task(_candidate(tmp_path, "missing", CANDIDATE_MISSING))
    proof, reasons = bd._interface_proof_from_review(
        "io_line", task, PROMPT, _challenge_source("io_line"))
    assert proof is None and reasons


# --------------------------------------------------------------------------- #
# the module-header reader
# --------------------------------------------------------------------------- #
def test_ports_are_read_from_the_header_in_both_declaration_styles():
    ansi = bd._declared_ports(CANDIDATE_VALID, "sample_link")
    non_ansi = bd._declared_ports(
        "module m(a, b);\n  input a;\n  output b;\nendmodule\n", "m")
    assert {"clk", "ready", "io_line"} <= ansi
    assert non_ansi == {"a", "b"}


def test_an_undeclared_module_is_none_not_an_empty_set():
    """"this file declares no such module" and "that module declares no ports"
    are different answers, and only the first is a wrong-top binding."""
    assert bd._declared_ports(CANDIDATE_VALID, "other") is None
    assert bd._declared_ports("module m;\nendmodule\n", "m") == set()


def test_a_commented_out_port_is_not_a_declared_port():
    source = ("module m (\n  input wire a\n  // , inout wire io_line\n);\n"
              "endmodule\n")
    assert "io_line" not in (bd._declared_ports(source, "m") or set())


# --------------------------------------------------------------------------- #
# the run-time half -- the compiler must say it itself
# --------------------------------------------------------------------------- #
_MISSING_PORT_DIAGNOSTIC = (
    "tb.sv:9: error: port ``io_line'' is not a port of dut.\n")


def _bound_challenge(candidate: dict) -> dict:
    return {"interface_proof": {
        "schema": bd._INTERFACE_PROOF_SCHEMA, "module": "sample_link",
        "port": "io_line", "direction": "inout", "width": 1,
        "excerpt": "inout wire io_line",
        "candidate_rtl_sha256": candidate["rtl_sha256"]}}


def test_the_diagnostic_must_name_this_port(tmp_path):
    candidate = _candidate(tmp_path, "missing", CANDIDATE_MISSING)
    challenge = _bound_challenge(candidate)
    hit = bd._interface_omission_reason(
        candidate, challenge, _MISSING_PORT_DIAGNOSTIC)
    assert hit and "io_line" in hit
    # a rejection of some OTHER port is not evidence about this one
    assert bd._interface_omission_reason(
        candidate, challenge,
        "tb.sv:9: error: port ``other'' is not a port of dut.\n") is None
    # nor is any other diagnostic that happens to mention the name
    assert bd._interface_omission_reason(
        candidate, challenge,
        "rtl.v:2: error: io_line is not declared.\n") is None


def test_no_proof_means_no_attribution(tmp_path):
    candidate = _candidate(tmp_path, "missing", CANDIDATE_MISSING)
    assert bd._interface_omission_reason(
        candidate, {}, _MISSING_PORT_DIAGNOSTIC) is None


def test_a_proof_bound_to_another_candidate_is_not_spent_on_this_one(tmp_path):
    """Challenges are INHERITED across repair rounds and re-run against the new
    candidate. A proof frozen against the parent is evidence about the parent's
    bytes; it is re-earned here or it is not used."""
    parent = _candidate(tmp_path, "parent", CANDIDATE_MISSING)
    child = _candidate(tmp_path, "child",
                       CANDIDATE_MISSING.replace("1'b0", "1'b1"))
    stale = _bound_challenge(parent)                     # bound to the parent
    assert bd._interface_omission_reason(
        child, stale, _MISSING_PORT_DIAGNOSTIC) is None
    # and the child earns its own attribution once the proof names it
    assert bd._interface_omission_reason(
        child, _bound_challenge(child), _MISSING_PORT_DIAGNOSTIC) is not None


def test_a_candidate_that_now_declares_the_port_earns_no_attribution(tmp_path):
    """The repaired candidate declares `io_line`. Whatever the diagnostics say,
    this is no longer an omission -- the run-time half checks the bytes it is
    actually compiling, not the ones the proof was written about."""
    repaired = _candidate(tmp_path, "repaired", CANDIDATE_VALID)
    assert bd._interface_omission_reason(
        repaired, _bound_challenge(repaired), _MISSING_PORT_DIAGNOSTIC) is None


def test_a_proof_about_a_module_this_candidate_lacks_earns_nothing(tmp_path):
    candidate = _candidate(tmp_path, "missing", CANDIDATE_MISSING)
    challenge = _bound_challenge(candidate)
    challenge["interface_proof"]["module"] = "some_other_module"
    assert bd._interface_omission_reason(
        candidate, challenge, _MISSING_PORT_DIAGNOSTIC) is None


# --------------------------------------------------------------------------- #
# end to end, through the production entry point
# --------------------------------------------------------------------------- #
def _run(tmp_path, name, candidate_source, port, proof: bool):
    candidate = _candidate(tmp_path, name, candidate_source)
    source = _challenge_source(port)
    path = tmp_path / f"{name}_challenge.sv"
    path.write_text(source)
    challenge = {"path": str(path), "sha256": bd._sha256_text(source)}
    if proof:
        frozen, reasons = _freeze(_task(candidate), source)
        assert reasons == [], reasons
        challenge["interface_proof"] = frozen
    return bd._run_verification_challenge(candidate, challenge)


@_NEEDS_SIMULATOR
def test_a_proven_required_omission_becomes_an_attributable_defect(tmp_path):
    result = _run(tmp_path, "missing", CANDIDATE_MISSING, "io_line", True)
    assert result["status"] == bd._CHALLENGE_CANDIDATE_BROKEN
    assert "REQUIRED public interface" in result["reasons"][0]
    assert result["interface_proof"]["port"] == "io_line"
    # an interface result stays an interface result
    assert result["status"] not in ("FAIL", "PASS")


@_NEEDS_SIMULATOR
def test_the_same_omission_without_a_proof_is_still_invalid(tmp_path):
    result = _run(tmp_path, "missing", CANDIDATE_MISSING, "io_line", False)
    assert result["status"] == "INVALID"
    assert any("only the challenge file" in r for r in result["reasons"])


@_NEEDS_SIMULATOR
def test_the_invented_port_control_stays_invalid(tmp_path):
    result = _run(tmp_path, "valid", CANDIDATE_VALID, "io_lien", False)
    assert result["status"] == "INVALID"
    assert any("only the challenge file" in r for r in result["reasons"])


@_NEEDS_SIMULATOR
def test_a_test_syntax_error_is_never_an_interface_defect(tmp_path):
    """A broken test also cites only the challenge file. It buys no
    attribution, because the compiler never says the proven port is not a
    port -- it never gets that far."""
    candidate = _candidate(tmp_path, "missing", CANDIDATE_MISSING)
    good = _challenge_source("io_line")
    broken = good.replace("reg clk = 0;", "reg clk = 0")     # dropped `;`
    path = tmp_path / "broken_challenge.sv"
    path.write_text(broken)
    frozen, reasons = _freeze(_task(candidate), broken)
    assert reasons == [], reasons
    result = bd._run_verification_challenge(
        candidate, {"path": str(path), "sha256": bd._sha256_text(broken),
                    "interface_proof": frozen})
    assert result["status"] == "INVALID"
    assert result["status"] != bd._CHALLENGE_CANDIDATE_BROKEN


@_NEEDS_SIMULATOR
def test_the_valid_interface_control_still_runs_its_assertions(tmp_path):
    """Both externally driven values are checked, so this proves the test is
    executable once its public interface is supplied."""
    result = _run(tmp_path, "valid", CANDIDATE_VALID, "io_line", False)
    assert result["status"] == "PASS"
    assert "VIBEIC_AI_CHALLENGE=PASS" in result["output"]
