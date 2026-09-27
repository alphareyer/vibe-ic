#!/usr/bin/env python3
"""D9: the reused-IP manifest emits side-granular `renamed_interfaces` pairs
the design's own records decide, and verifies every authored pair.

MEASURED on subservient x gf180mcuD (v1.25.64): both manifest emitters wrote
`renamed_interfaces: []`, so after D2 step 15.5ic refused PAD_GROUP_UNRESOLVED
and four pairs had to be authored by hand. Here, program first:
  R1 read/write split: `o_memory_waddr` = {memory, w+addr} against the
     document's `o_memory_addr` = {memory, addr}; same-direction tie-break;
  R2 sole remaining side: `o_memory_wen` shares only {memory}, and after R1
     the only unexplained document members of that family are on S.
A port no rule decides is listed UNRESOLVED for catalog-glue-author, whose
pairs `--check` verifies (exact-atom rule, implemented ports) and refuses with
a reason. An authored list is never rewritten (the landed merge contract).

Also here: step 2's `aw = $clog2(memsize)` width, which masked the pad budget
as UNDECIDED (ZERO_DENOMINATOR) on the same run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PROGRAMS = TESTS.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(TESTS))

import staged_rtl_reused_ip_manifest_emit as SRM        # noqa: E402
from _staged_top_module import EXTRACTION_STRATEGY as STAGED  # noqa: E402

DOC = """# L3 — External Interface

The I/O cell library is delegated to the PDK i/o pad defaults.

## Physical Pad Placement

| Pad side | signals |
|---|---|
| **North (N)** | memory data bus |
| **South (S)** | memory addr + control(we / cyc) |
| **East (E)** | `clk` / `rst` |
| **West (W)** | status pin(s) |
"""


def _p(name, direction, staged, width=1):
    """An L9 top_ports entry as phase 1 writes it: a staged-top port carries
    the staged-top harvest's extraction strategy (D2's `accept_renames` reads
    that as the RTL speaking, not the document)."""
    port = {"name": name, "direction": direction, "width": width,
            "declared_by_staged_top": staged}
    if staged:
        port["extraction_strategy"] = STAGED
    return port


IMPL = [_p("clk", "input", True), _p("rst", "input", True),
        _p("o_status", "output", True),
        _p("o_memory_waddr", "output", True, 3),
        _p("o_memory_wdata", "output", True, 2),
        _p("o_memory_wen", "output", True),
        _p("o_memory_raddr", "output", True, 3),
        _p("i_memory_rdata", "input", True, 2),
        _p("o_memory_ren", "output", True)]
DOC_ONLY = [_p("o_memory_data", "output", False, 2),
            _p("i_memory_data", "input", False, 2),
            _p("o_memory_addr", "output", False, 3),
            _p("o_memory_we", "output", False),
            _p("o_memory_cyc", "output", False)]

EXPECTED = [
    {"l9": ["i_memory_data"], "rtl": ["i_memory_rdata"]},
    {"l9": ["o_memory_addr"], "rtl": ["o_memory_raddr", "o_memory_waddr"]},
    {"l9": ["o_memory_cyc", "o_memory_we"],
     "rtl": ["o_memory_ren", "o_memory_wen"]},
    {"l9": ["o_memory_data"], "rtl": ["o_memory_wdata"]},
]


def _verilog(ports):
    decl = []
    for p in ports:
        rng = f"[{p['width'] - 1}:0] " if p["width"] > 1 else ""
        decl.append(f"  {p['direction']} wire {rng}{p['name']}")
    return "module core (\n" + ",\n".join(decl) + "\n);\nendmodule\n"


def _project(tmp_path, *, impl=IMPL, doc_only=DOC_ONLY, doc=DOC,
             manifest=None):
    proj = tmp_path / "proj"
    (proj / "input/docs").mkdir(parents=True)
    if doc is not None:
        (proj / "input/docs/L3_external_interface.md").write_text(doc)
    (proj / "phase1/generated_docs").mkdir(parents=True)
    (proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps({"top_module": "core", "top_ports": impl + doc_only}))
    (proj / "input/vendor_rtl").mkdir(parents=True)
    (proj / "input/vendor_rtl/core.v").write_text(_verilog(impl))
    if manifest is not None:
        rtl = proj / "phase2/stage1/rtl"
        rtl.mkdir(parents=True)
        (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
    return proj


def _emit(proj):
    out = SRM.emit_prestaged_reused_ip_manifest(proj)
    return json.loads(out.read_text())


def _lr(pairs):
    return [{"l9": p["l9"], "rtl": p["rtl"]} for p in pairs]


def _check(proj, capsys):
    import renamed_interface_derive as RID
    rc = RID.main([str(proj), "--check"])
    return rc, json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# program first — RED on main (the emitter writes [])
# --------------------------------------------------------------------------- #
def test_the_emitter_derives_the_side_granular_pairs(tmp_path):
    mf = _emit(_project(tmp_path))
    assert _lr(mf["renamed_interfaces"]) == EXPECTED
    rules = {e["rtl"]: e["rule"] for p in mf["renamed_interfaces"]
             for e in p["evidence"]}
    assert rules == {"o_memory_waddr": "R1_read_write_split",
                     "o_memory_raddr": "R1_read_write_split",
                     "o_memory_wdata": "R1_read_write_split",
                     "i_memory_rdata": "R1_read_write_split",
                     "o_memory_wen": "R2_sole_remaining_side",
                     "o_memory_ren": "R2_sole_remaining_side"}
    assert all(p["derived_by"] == "renamed_interface_derive"
               and all(e["evidence"] for e in p["evidence"])
               for p in mf["renamed_interfaces"])
    assert mf["renamed_interfaces_derivation"]["verdict"] == "DERIVED"
    assert mf["renamed_interfaces_derivation"]["unresolved"] == []


def test_the_consume_emitter_derives_them_too(tmp_path):
    import reused_ip_rtl_consume as C
    proj = _project(tmp_path)
    src = proj / "input/design_src/verilog/rtl"
    src.mkdir(parents=True)
    (src / "core.v").write_text((proj / "input/vendor_rtl/core.v").read_text())
    (proj / "input/vendor_rtl/core.v").unlink()
    (proj / "input/vendor_rtl").rmdir()
    C.consume_reused_ip_rtl(proj)
    mf = json.loads((proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json").read_text())
    assert _lr(mf["renamed_interfaces"]) == EXPECTED


def test_the_derived_pairs_pass_the_check(tmp_path, capsys):
    proj = _project(tmp_path)
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert rc == 0 and res["verdict"] == "PASS", res
    assert {v["side"] for v in res["pairs"]} == {"N", "S"}


def test_the_direction_breaks_a_two_document_port_tie(tmp_path):
    """`o_memory_wdata` = w+{memory, data} matches BOTH o_memory_data and
    i_memory_data; the same-direction one is its pair."""
    mf = _emit(_project(tmp_path))
    by_rtl = {r: p["l9"] for p in mf["renamed_interfaces"] for r in p["rtl"]}
    assert by_rtl["o_memory_wdata"] == ["o_memory_data"]
    assert by_rtl["i_memory_rdata"] == ["i_memory_data"]


# --------------------------------------------------------------------------- #
# AI backup: an undecided port is named, and an authored pair is verified
# --------------------------------------------------------------------------- #
_NO_DATA_IMPL = [p for p in IMPL if "data" not in p["name"]] + [
    _p("o_memory_ack", "output", True)]


def test_an_undecided_port_is_listed_for_the_glue_author(tmp_path, capsys):
    proj = _project(tmp_path, impl=_NO_DATA_IMPL)
    mf = _emit(proj)
    assert _lr(mf["renamed_interfaces"]) == [
        {"l9": ["o_memory_addr"], "rtl": ["o_memory_raddr", "o_memory_waddr"]}]
    der = mf["renamed_interfaces_derivation"]
    assert der["verdict"] == "UNRESOLVED"
    unresolved = {u["port"]: u for u in der["unresolved"]}
    assert set(unresolved) == {"o_memory_ack", "o_memory_ren", "o_memory_wen"}
    assert unresolved["o_memory_ack"]["candidate_sides"] == ["N", "S"]
    assert "catalog-glue-author" in der["owed_by"]
    rc, res = _check(proj, capsys)
    assert rc == 1
    assert set(res["unpaired_implemented_ports"]) == set(unresolved)


def test_an_authored_pair_that_verifies_completes_the_check(tmp_path, capsys):
    proj = _project(tmp_path, impl=_NO_DATA_IMPL)
    mf = _emit(proj)
    mf["renamed_interfaces"].append(
        {"l9": ["o_memory_we", "o_memory_cyc"],
         "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]})
    (proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json").write_text(json.dumps(mf))
    rc, res = _check(proj, capsys)
    assert rc == 0, res
    # re-emitting never rewrites an authored list; it records the check
    mf2 = _emit(proj)
    assert mf2["renamed_interfaces"] == mf["renamed_interfaces"]
    assert mf2["renamed_interfaces_check"]["verdict"] == "PASS"


@pytest.mark.parametrize("pair,why", [
    ({"l9": ["o_memory_data", "o_memory_addr"],
      "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]}, "2 groups"),
    ({"l9": ["o_memory_we"], "rtl": ["o_memory_ack", "o_memory_nope"]},
     "not ports of the implemented top"),
    ({"l9": ["o_memory_bogus"], "rtl": ["o_memory_ack"]},
     "are not L9 top_ports"),
    ({"l9": ["clk"], "rtl": ["o_memory_ack"]},
     "fall in no group row"),
], ids=["spans-groups", "rtl-not-implemented", "l9-unknown", "no-group"])
def test_an_unverifiable_authored_pair_is_refused_with_its_reason(
        tmp_path, capsys, pair, why):
    proj = _project(tmp_path, impl=_NO_DATA_IMPL, manifest={
        "reused_ip": True, "renamed_interfaces": [
            {"l9": ["o_memory_addr"],
             "rtl": ["o_memory_raddr", "o_memory_waddr"]},
            {"l9": ["o_memory_we", "o_memory_cyc"],
             "rtl": ["o_memory_wen", "o_memory_ren"]}, pair]})
    rc, res = _check(proj, capsys)
    assert rc == 1
    refused = [v for v in res["pairs"] if v["verdict"] == "REFUSED"]
    assert len(refused) == 1 and why in refused[0]["reason"], refused


def test_an_all_carrier_word_name_carries_no_side(tmp_path, capsys):
    """`o_bus` has no semantic atom: it is in no group, never in every one."""
    proj = _project(tmp_path, impl=_NO_DATA_IMPL,
                    doc_only=DOC_ONLY + [_p("o_bus", "output", False)],
                    manifest={"reused_ip": True, "renamed_interfaces": [
                        {"l9": ["o_bus"], "rtl": ["o_memory_ack"]}]})
    rc, res = _check(proj, capsys)
    assert rc == 1 and "fall in no group row" in res["pairs"][0]["reason"]


# --------------------------------------------------------------------------- #
# the question does not arise: the landed empty scaffold is unchanged
# --------------------------------------------------------------------------- #
def test_no_pad_placement_keeps_the_empty_scaffold(tmp_path, capsys):
    proj = _project(tmp_path, doc=None)
    mf = _emit(proj)
    assert mf["renamed_interfaces"] == []
    assert mf["renamed_interfaces_derivation"]["verdict"] == "NOT_APPLICABLE"
    rc, res = _check(proj, capsys)
    assert rc == 2 and res["verdict"] == "NOT_APPLICABLE"


# --------------------------------------------------------------------------- #
# step 2: an exact expression default is a width, not ZERO_DENOMINATOR
# --------------------------------------------------------------------------- #
_CLOG2_RTL = ("module widget #(parameter memsize = 1024,\n"
              "  parameter aw = $clog2(memsize))\n"
              " (input wire i_clk, input wire i_rst,\n"
              "  output wire [aw-1:0] o_sensor_addr, output wire [7:0] o_sensor_data,\n"
              "  input wire [7:0] i_sensor_data, output wire o_sensor_we,\n"
              "  output wire o_sensor_cyc, output wire o_gpio);\n"
              "endmodule\n")


def test_step2_resolves_a_clog2_width_and_decides():
    import test_r0915101_a_die_budgets_against_its_own_pad_ring as R0915
    rc, rep = R0915._run(R0915._project(rtl=_CLOG2_RTL))
    assert (rc, rep["verdict"]) == (0, "FITS"), rep.get("reason")
    assert rep["own_ring_arithmetic"]["signal_pads_owed"] == 29
    assert rep["params_from_top_module_exact_expressions"]["aw"] == {
        "value": 10, "expression": "$clog2(memsize)",
        "operand": {"memsize": 1024}}


def test_step2_an_inexact_expression_stays_undecided():
    import test_r0915101_a_die_budgets_against_its_own_pad_ring as R0915
    rtl = _CLOG2_RTL.replace("$clog2(memsize)", "memsize / 100")
    rc, rep = R0915._run(R0915._project(rtl=rtl))
    assert (rc, rep["verdict"]) == (2, "UNDECIDED")
    assert "o_sensor_addr" in rep["unresolved_width_ports"]


@pytest.mark.parametrize("n,want", [(0, 0), (1, 0), (2, 1), (512, 9),
                                    (513, 10), (1024, 10)])
def test_clog2_is_the_ieee_definition(n, want):
    import slot_pad_budget_check as S
    assert S._clog2(n) == want


# --------------------------------------------------------------------------- #
# the runner's hand-off to catalog-glue-author names what it still owes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("impl,owed", [(_NO_DATA_IMPL, True), (IMPL, False)],
                         ids=["unresolved", "all-derived"])
def test_the_waive_names_the_ports_the_glue_author_must_pair(
        tmp_path, impl, owed):
    import design_one_shot_runner as R
    proj = _project(tmp_path, impl=impl)
    res = R.step_rtl_gen(proj, "digital_cmd_driven")
    assert res.extras.get("fallback_skill") == "catalog-glue-author"
    said = "renamed_interfaces: 3 implemented port(s)" in res.detail
    assert said is owed, res.detail[-600:]
    if owed:
        assert "o_memory_ack" in res.detail and "--check" in res.detail



# --------------------------------------------------------------------------- #
# D2's acceptance rule: a derived pair gives a side only if phase 2 accepts it
# --------------------------------------------------------------------------- #
def test_a_derived_pair_with_a_width_disagreement_is_rejected_not_written(
        tmp_path, capsys):
    """The subservient shape: the document says a 4-bit address (memsize it
    declares), the RTL was built 3 bits wide. R1 still DERIVES the pair, and
    the acceptance rule rejects it: it is reported with the reason, never
    written, and its ports stay unresolved."""
    doc_only = [q if q["name"] != "o_memory_addr" else
                _p("o_memory_addr", "output", False, 4) for q in DOC_ONLY]
    proj = _project(tmp_path, doc_only=doc_only)
    mf = _emit(proj)
    assert {"l9": ["o_memory_addr"],
            "rtl": ["o_memory_raddr", "o_memory_waddr"]} not in _lr(
        mf["renamed_interfaces"])
    assert len(mf["renamed_interfaces"]) == 3
    der = mf["renamed_interfaces_derivation"]
    assert [r["l9"] for r in der["rejected"]] == [["o_memory_addr"]]
    assert any("3 bit(s)" in x and "is 4" in x
               for x in der["rejected"][0]["reasons"])
    unresolved = {u["port"]: u for u in der["unresolved"]}
    assert set(unresolved) == {"o_memory_raddr", "o_memory_waddr"}
    assert "accept_renames" in unresolved["o_memory_waddr"]["reason"]
    rc, res = _check(proj, capsys)
    assert rc == 1
    assert set(res["unpaired_implemented_ports"]) == {"o_memory_raddr",
                                                      "o_memory_waddr"}


def test_an_authored_pair_the_acceptance_rule_rejects_is_refused(
        tmp_path, capsys):
    proj = _project(tmp_path, impl=_NO_DATA_IMPL, manifest={
        "reused_ip": True, "renamed_interfaces": [
            {"l9": ["o_memory_addr"],
             "rtl": ["o_memory_raddr", "o_memory_waddr"]},
            {"l9": ["o_memory_we", "o_memory_cyc"],
             "rtl": ["o_memory_wen", "o_memory_ren"]},
            # same group, but i_memory_data is an INPUT and o_memory_ack an output
            {"l9": ["i_memory_data"], "rtl": ["o_memory_ack"]}]})
    rc, res = _check(proj, capsys)
    assert rc == 1
    refused = [v for v in res["pairs"] if v["verdict"] == "REFUSED"]
    assert len(refused) == 1 and "is output, l9" in refused[0]["reason"]


def test_a_parameterised_rtl_width_is_read_exactly_before_acceptance(tmp_path):
    """subservient's own shape: `[aw-1:0]` with `aw = $clog2(memsize)`. The
    width comes from the header's own defaults (step 2's exact readers), so
    the derivation rejects the pair 15.5ic would reject, instead of accepting
    it on an unknown width."""
    doc_only = [q if q["name"] != "o_memory_addr" else
                _p("o_memory_addr", "output", False, 4) for q in DOC_ONLY]
    proj = _project(tmp_path, doc_only=doc_only)
    v = proj / "input/vendor_rtl/core.v"
    v.write_text(v.read_text()
                 .replace("module core (", "module core #(parameter memsize = 8,\n"
                          "  parameter aw = $clog2(memsize)) (")
                 .replace("[2:0] o_memory_waddr", "[aw-1:0] o_memory_waddr")
                 .replace("[2:0] o_memory_raddr", "[aw-1:0] o_memory_raddr"))
    assert "[aw-1:0] o_memory_waddr" in v.read_text()
    der = _emit(proj)["renamed_interfaces_derivation"]
    assert [r["l9"] for r in der["rejected"]] == [["o_memory_addr"]]
    assert any("3 bit(s)" in x for x in der["rejected"][0]["reasons"])
