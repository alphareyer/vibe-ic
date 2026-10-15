#!/usr/bin/env python3
"""formal_structural_check — the structural answers, through its OWN entry points.

Step 5 reaches this program through `formal_property_run`; these arms drive it
directly (`check()` and the CLI `main()`), both ways, on generic RTL:

  sync reset, posedge                 -> clock_edge / reset_port / reset_sync PASS
  async reset declared "synchronous"  -> reset_sync REFUTED
  negedge flop declared posedge       -> clock_edge REFUTED
  a reset port that reaches no flop   -> reset_port not PASS

Needs yosys on PATH (the vibeic-eda image, as CI and falsref run it); without
it the arms are NOT_VERIFIED by name, and VIBEIC_REQUIRE_EDA_VERIFICATION=1
turns that back into a failure. chip-AGNOSTIC.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import formal_structural_check as fsc  # noqa: E402
from not_verified_tier import skip_not_verified  # noqa: E402

SYNC = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk) if (rst) q <= 4'd0; else q <= d;
endmodule
"""
ASYNC = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk or posedge rst) if (rst) q <= 4'd0; else q <= d;
endmodule
"""
NEG = SYNC.replace("posedge clk", "negedge clk")
DEAD_RST = """module m(input clk, input rst, input [3:0] d, output reg [3:0] q);
  always @(posedge clk) q <= d;
endmodule
"""
CLAIMS = [{"rule": "clock_edge", "signal": "clk", "value": "posedge"},
          {"rule": "reset_port", "signal": "rst", "value": ""},
          {"rule": "reset_sync", "signal": "rst", "value": "synchronous"}]


def _yosys():
    if shutil.which("yosys") is None:
        skip_not_verified(
            "yosys not on PATH, so no structural claim can be checked here",
            "tools/ci/run_suite_in_eda_image.sh -- programs/tests/test_formal_structural_check.py")


def _check(tmp: Path, rtl: str) -> dict:
    _yosys()
    f = tmp / "m.v"
    f.write_text(rtl)
    rec = fsc.check([f], "m", CLAIMS, tmp)
    return {c["rule"]: c["verdict"] for c in rec["claims"]} | {"_": rec["verdict"]}


def test_a_sync_posedge_design_passes_every_structural_claim(tmp_path):
    v = _check(tmp_path, SYNC)
    assert v == {"clock_edge": fsc.PASS, "reset_port": fsc.PASS,
                 "reset_sync": fsc.PASS, "_": fsc.PASS}, v


def test_an_async_reset_refutes_synchronous(tmp_path):
    v = _check(tmp_path, ASYNC)
    assert v["reset_sync"] == fsc.REFUTED and v["_"] == fsc.REFUTED, v


def test_a_negedge_flop_refutes_posedge(tmp_path):
    v = _check(tmp_path, NEG)
    assert v["clock_edge"] == fsc.REFUTED, v


def test_a_reset_that_reaches_no_flop_is_not_a_reset_port(tmp_path):
    v = _check(tmp_path, DEAD_RST)
    assert v["reset_port"] != fsc.PASS and v["_"] != fsc.PASS, v


def test_the_cli_both_ways(tmp_path, capsys):
    _yosys()
    for rtl, rc in ((SYNC, 0), (ASYNC, 1)):
        d = tmp_path / str(rc)
        d.mkdir()
        (d / "m.v").write_text(rtl)
        out = d / "rec.json"
        got = fsc.main(["--rtl", str(d / "m.v"), "--top", "m",
                        "--claim", "reset_sync:rst:synchronous",
                        "--work-dir", str(d), "--json", str(out)])
        capsys.readouterr()
        assert got == rc
        assert json.loads(out.read_text())["verdict"] == (fsc.PASS if rc == 0 else fsc.REFUTED)


# Paired next-state controls use free, shared current Q/data in native Yosys.
import pytest  # noqa: E402

_CONTROL_CASES = {
    "direct": ("if(rst) q<=0; else q<=d;", fsc.PASS, "RESET_FORCE", True),
    "inverted": ("if(!rst) q<=0; else q<=d;", fsc.REFUTED, "RESET_FORCE", True),
    "boolean-d": ("q<=d & {W{!rst}};", fsc.PASS, "RESET_FORCE", True),
    "enable-only": ("if(rst) q<=d;", fsc.NOT_DISCHARGED, "RETENTION_CONTROL", None),
    "reset-first": ("if(rst) q<=0; else if(en) q<=d;", fsc.PASS, "RESET_FORCE", True),
    "enable-first": ("if(en) begin if(rst) q<=0; else q<=d; end", fsc.PASS, "RESET_FORCE", False),
    "local-or": ("if(rst|clear) q<=0; else q<=d;", fsc.PASS, "RESET_FORCE", True),
    "local-and": ("if(rst&clear) q<=0; else q<=d;", fsc.PASS, "RESET_FORCE", False),
    "local-only": ("if(clear) q<=0; else q<=d;", fsc.NOT_DISCHARGED, "LOCAL_CLEAR", None),
    "ordinary-data": ("q<={W{rst}};", fsc.NOT_DISCHARGED, "DATA_VALUE", None),
    "literal-x": ("if(rst) q<={W{1'bx}}; else q<=d;", fsc.NOT_DISCHARGED, "UNSUPPORTED", None),
    "mixed-unclosed": ("begin if(rst) q[0]<=0; else q[0]<=d[0]; q[1]<=rst ? d[1]^clear : d[1]; end",
                       fsc.NOT_DISCHARGED, "UNRESOLVED_DATA_DEPENDENCE", None),
}


def _control_rtl(body, width=2, edge="posedge clk"):
    return ("module m #(parameter W=" + str(width) + ")"
        "(input clk,rst,en,clear,input [W-1:0] d,output reg [W-1:0] q);\n"
        "always @(" + edge + ") " + body + "\nendmodule\n")


@pytest.mark.parametrize("case", _CONTROL_CASES)
def test_complete_reset_mapping_normal_cli_preserves_force_priority_and_unknowns(tmp_path, case):
    _yosys()
    body, expected, role, total = _CONTROL_CASES[case]
    rtl = tmp_path/"m.v"
    rtl.write_text(_control_rtl(body))
    out = tmp_path/"mapping.json"
    rc = fsc.main(["--rtl",str(rtl),"--top","m","--claim",
        "reset_polarity:rst:active_high","--work-dir",str(tmp_path),"--json",str(out)])
    rec = json.loads(out.read_text())
    assert rec["verdict"] == expected and rc == (0 if expected == fsc.PASS else 1), rec
    mapping = rec["claims"][0].get("reset_control_mapping") or {}
    assert mapping.get("shared_current_q_and_data") is True, rec
    assert mapping.get("paired_domain_reachable") is True, rec
    assert len(mapping.get("raw_identities") or []) == 2, mapping
    assert all(len(r["current_q"]) == 1 for r in mapping["raw_identities"]), mapping
    assert role in {r["classification"] for r in mapping["rows"]}, mapping
    if total is not None:
        forced = [r for r in mapping["rows"] if r["classification"] == "RESET_FORCE"]
        assert forced and all(r["total_force"] is total for r in forced), mapping
        if case == "enable-first":
            # No assumption holds enable active: the blocked reset domain is
            # reachable, and the actual native total-force query is refuted.
            assert all(r["native_queries"]["zero1"] == "REFUTED" for r in forced), mapping
        if case == "reset-first":
            assert all(r["native_queries"]["zero1"] == "PROVED" for r in forced), mapping
    if case in {"literal-x", "mixed-unclosed"}:
        assert mapping["unresolved"] and not mapping["complete"], mapping


@pytest.mark.parametrize("width,value", [(1,1),(3,5)])
def test_native_reset_parameter_and_async_timing_controls(tmp_path, width, value):
    _yosys()
    rtl = tmp_path/"m.v"
    rtl.write_text(_control_rtl(f"if(!rst) q<={value}; else if(en) q<=d;",width,
                                "posedge clk or negedge rst"))
    claims = [{"rule":"reset_polarity","signal":"rst","value":"active_low"},
              {"rule":"reset_sync","signal":"rst","value":"synchronous"}]
    rec = fsc.check([rtl],"m",claims,tmp_path)
    assert rec["claims"][0]["verdict"] == fsc.PASS, rec
    assert rec["claims"][1]["verdict"] == fsc.REFUTED, rec
    mapping = rec["claims"][0].get("reset_control_mapping") or {}
    assert len(mapping.get("raw_identities") or []) == width, mapping
    assert all(r["polarity"] == "active_low" and r["timing"] == "asynchronous"
               for r in mapping["rows"]), mapping


def test_unknown_clock_is_explicit_not_full_polarity(tmp_path):
    _yosys()
    rtl = tmp_path/"m.v"
    rtl.write_text(_control_rtl("if(rst) q<=0; else q<=d;",edge="posedge (clk & en)"))
    rec = fsc.check([rtl],"m",[{"rule":"reset_polarity","signal":"rst","value":"active_high"}],tmp_path)
    assert rec["verdict"] == fsc.NOT_DISCHARGED, rec
    mapping = rec["claims"][0].get("reset_control_mapping") or {}
    assert mapping.get("unresolved") and all(r["classification"] == "UNSUPPORTED"
                                             for r in mapping["rows"]), mapping


# These are self-authored public circuits, not examples copied from a field IC.
# In particular, the Boolean masking controls leave every Q/data bit free.
_GUARDED_ROLE_CASES = {
    "and-not": ("q<=d & {W{!(rst&clear)}};", fsc.PASS, "RESET_FORCE"),
    "and-not-low": ("q<=d & {W{!(!rst&clear)}};", fsc.REFUTED, "RESET_FORCE"),
    "or-mask": ("q<=d | {W{rst&clear}};", fsc.PASS, "RESET_FORCE"),
    "nested-predicate": ("q<=d & {W{!((rst&clear)|(rst&en))}};", fsc.PASS, "RESET_FORCE"),
    "qualified-retain": ("q<=q & {W{!(rst&clear)}};", fsc.PASS, "RESET_FORCE"),
    "shared-q-data": ("if(rst) begin if(en) q<=0; end else if(en) q<=d;", fsc.PASS, "RESET_FORCE"),
    "data-mux": ("q<=rst ? d : {W{en}};", fsc.NOT_DISCHARGED, "UNRESOLVED_DATA_DEPENDENCE"),
    "lowered-data-mux": ("q<=({W{rst}}&d)|({W{!rst}}&{W{en}});", fsc.NOT_DISCHARGED, "UNRESOLVED_DATA_DEPENDENCE"),
    "data-xor": ("q<=d ^ {W{rst&clear}};", fsc.NOT_DISCHARGED, "UNRESOLVED_DATA_DEPENDENCE"),
    "mixed-sense": ("if(rst^clear) q<=0; else q<=d;", fsc.NOT_DISCHARGED, "UNRESOLVED_DATA_DEPENDENCE"),
    "impossible-control": ("if(rst & !rst & clear) q<=0; else q<=d;", fsc.NOT_DISCHARGED, "NO_DEPENDENCE"),
    "conditional-x": ("if(rst&clear) q<={W{1'bx}}; else q<=d;", fsc.NOT_DISCHARGED, "UNSUPPORTED"),
}


@pytest.mark.parametrize("case", _GUARDED_ROLE_CASES)
def test_guarded_boolean_roles_normal_cli_has_actual_feasible_domains(tmp_path, case):
    _yosys()
    body, expected, classification = _GUARDED_ROLE_CASES[case]
    rtl, output = tmp_path / "m.v", tmp_path / "mapping.json"
    rtl.write_text(_control_rtl(body))
    rc = fsc.main(["--rtl", str(rtl), "--top", "m", "--claim",
        "reset_polarity:rst:active_high", "--work-dir", str(tmp_path), "--json", str(output)])
    rec = json.loads(output.read_text())
    assert rec["verdict"] == expected and rc == (0 if expected == fsc.PASS else 1), rec
    mapping = rec["claims"][0]["reset_control_mapping"]
    assert mapping["shared_current_q_and_data"] and mapping["paired_domain_reachable"], mapping
    assert len(mapping["raw_identities"]) == 2, mapping
    assert all(len(r["current_q"]) == 1 for r in mapping["raw_identities"]), mapping
    assert classification in {r["classification"] for r in mapping["rows"]}, mapping
    if case != "conditional-x":
        for row in mapping["rows"]:
            roles = row["conditional_roles"]
            assert len(roles) == 8, row
            assert all(row["native_queries"][f"roles_cover{arm}"] == "PROVED" and
                       row["native_queries"][f"roles_disjoint{arm}"] == "PROVED"
                       for arm in (0, 1)), row
            assert all(r["domain_query"] in {"PROVED", "REFUTED"} for r in roles), row
            assert all(r["implication_query"] == "PROVED" for r in roles
                       if r["role"] != "DATA"), row
    if expected in {fsc.PASS, fsc.REFUTED}:
        assert mapping["complete"] and not mapping["unresolved"], mapping
        assert all(any(r["reachable"] and r["role"] in {"FORCE_ZERO", "FORCE_ONE"}
                       for r in row["conditional_roles"]) for row in mapping["rows"]), mapping
    if case in {"and-not", "or-mask", "nested-predicate", "qualified-retain", "shared-q-data"}:
        assert all(row["total_force"] is False for row in mapping["rows"]), mapping
    if case in {"data-mux", "lowered-data-mux", "data-xor", "impossible-control"}:
        assert not any(r["reachable"] and r["role"] in {"FORCE_ZERO", "FORCE_ONE"}
                       for row in mapping["rows"] for r in row["conditional_roles"]), mapping
    if case == "mixed-sense":
        assert all(row["native_queries"]["high"] == "REFUTED" and
                   row["native_queries"]["low"] == "REFUTED" for row in mapping["rows"]), mapping
    if case == "qualified-retain":
        assert mapping["state_model"] == "raw_pre_cleanup_current_source", mapping
        assert mapping["optimized_projection_explained"] is True, mapping
        assert len(mapping["rows"]) == len(mapping["raw_identities"]) == 2, mapping
        assert all(not p["optimized_q"] and p["optimized_constants"] == ["0"]
                   for p in mapping["optimized_identity_projection"]), mapping
        binding = mapping["source_binding"]
        assert binding["rtl_sha256"] == rec["rtl_sha256"], rec
        assert binding["raw_netlist_sha256"] and binding["optimized_netlist_sha256"], rec
        assert binding["scope"].endswith("not optimized-state coverage"), rec


def test_data_mux_value_cannot_be_promoted_to_outer_mask_control(tmp_path):
    """A native true implication under data=0 is not reset-role provenance."""
    _yosys()
    rtl, output = tmp_path / "m.v", tmp_path / "mapping.json"
    rtl.write_text("module m(input clk,rst,d,en,clear,output reg q);\n"
                   " always @(posedge clk) q <= (rst ? d : (d | en)) & clear;\nendmodule\n")
    rc = fsc.main(["--rtl", str(rtl), "--top", "m", "--claim",
        "reset_polarity:rst:active_high", "--work-dir", str(tmp_path), "--json", str(output)])
    rec = json.loads(output.read_text())
    assert rc == 1 and rec["verdict"] == fsc.NOT_DISCHARGED, rec
    mapping = rec["claims"][0]["reset_control_mapping"]
    assert len(mapping["raw_identities"]) == len(mapping["rows"]) == 1, mapping
    assert mapping["shared_current_q_and_data"] and mapping["paired_domain_reachable"], mapping
    assert mapping["unresolved"] and not mapping["complete"], mapping
    assert all(row["classification"] == "UNRESOLVED_DATA_DEPENDENCE" for row in mapping["rows"]), mapping
    assert not any(role["reachable"] and role["role"] in {"FORCE_ZERO", "FORCE_ONE"}
                   for row in mapping["rows"] for role in row["conditional_roles"]), mapping


@pytest.mark.parametrize("operator", ["and", "or"])
def test_guarded_logical_and_reduction_masks_use_native_vector_truth(tmp_path, operator):
    _yosys()
    logical, reduction = ("&&", "&") if operator == "and" else ("||", "|")
    predicate = "!(rst&&clear)" if operator == "and" else "(rst&&clear)"
    rtl, output = tmp_path / "m.v", tmp_path / "mapping.json"
    rtl.write_text("module m #(parameter W=3)(input clk,rst,clear,input[W-1:0] d,output reg[1:0] q);\n"
        f"always @(posedge clk) begin q[0]<=d {logical} {predicate}; "
        f"q[1]<={reduction}{{d,{predicate}}}; end\nendmodule\n")
    rc = fsc.main(["--rtl", str(rtl), "--top", "m", "--claim",
        "reset_polarity:rst:active_high", "--work-dir", str(tmp_path), "--json", str(output)])
    rec = json.loads(output.read_text())
    assert rc == 0 and rec["verdict"] == fsc.PASS, rec
    raw = json.loads((tmp_path / "formal_structural_netlist_raw.json").read_text())
    types = {cell["type"] for cell in raw["modules"]["m"]["cells"].values()}
    assert f"$logic_{operator}" in types and f"$reduce_{operator}" in types, types
    mapping = rec["claims"][0]["reset_control_mapping"]
    assert mapping["complete"] and not mapping["unresolved"], mapping
    assert mapping["shared_current_q_and_data"] and mapping["paired_domain_reachable"], mapping
    assert len(mapping["raw_identities"]) == 2, mapping
    assert all(len(r["current_q"]) == 1 for r in mapping["raw_identities"]), mapping
    wanted = "FORCE_ZERO" if operator == "and" else "FORCE_ONE"
    assert mapping["rows"] and all(r["classification"] == "RESET_FORCE" and
        r["polarity"] == "active_high" and r["total_force"] is False for r in mapping["rows"]), mapping
    for row in mapping["rows"]:
        assert any(r["reset_value"] == 1 and r["role"] == wanted and r["reachable"] and
                   r["implication_query"] == "PROVED" for r in row["conditional_roles"]), row
        assert row["native_queries"]["zero0"] == row["native_queries"]["one0"] == "REFUTED", row
        assert all(row["native_queries"][f"roles_cover{arm}"] == "PROVED" and
                   row["native_queries"][f"roles_disjoint{arm}"] == "PROVED" for arm in (0, 1)), row
