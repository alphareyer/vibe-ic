#!/usr/bin/env python3
"""D9: the reused-IP manifest emits the side-granular pad pairs the design's
own records decide, and verifies every pair the pad side reads.

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

REVIEW WAVE 2 (BLOCKER, both lenses): the derived pairs used to go into
`renamed_interfaces`, which `spec_conformance_check` and
`l9_rtl_pin_consistency_check` read as DECLARED RENAMES, so the emitter relaxed
both gates by itself on every reused IP with a pad placement. An R2 pair is a
side, not a rename. The pairs now go to `derived_pad_pairs`, read only by
`_l_doc_pad_placement.declared_renames`; `renamed_interfaces` stays authored.

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
    assert _lr(mf["derived_pad_pairs"]) == EXPECTED
    rules = {r: p["rule"] for p in mf["derived_pad_pairs"] for r in p["rtl"]}
    assert rules == {"o_memory_waddr": "R1", "o_memory_raddr": "R1",
                     "o_memory_wdata": "R1", "i_memory_rdata": "R1",
                     "o_memory_wen": "R2", "o_memory_ren": "R2"}
    assert all(p["derived_by"] == "renamed_interface_derive"
               and p["side"] in ("N", "S")
               and all(e["because"] for e in p["evidence"])
               for p in mf["derived_pad_pairs"])
    # R1 carries the exact atom, R2 the shared family atom(s) and the side
    by_rtl = {e["rtl"]: e for p in mf["derived_pad_pairs"]
              for e in p["evidence"]}
    assert (by_rtl["o_memory_waddr"]["atom"],
            by_rtl["o_memory_waddr"]["side"]) == ("waddr", "S")
    assert (by_rtl["o_memory_wen"]["family_atoms"],
            by_rtl["o_memory_wen"]["side"]) == (["memory"], "S")
    assert mf["renamed_interfaces_derivation"]["verdict"] == "DERIVED"
    assert mf["renamed_interfaces_derivation"]["unresolved"] == []


def test_a_side_only_pair_is_never_written_as_a_rename(tmp_path):
    """RED on 3c5697945, which wrote {o_memory_cyc, o_memory_we} ->
    {o_memory_ren, o_memory_wen} into `renamed_interfaces`: a read-enable is
    not a cycle strobe. R2 knows only that they share `memory` and one side.
    That pair may give the pads their side; it is never a declared rename."""
    mf = _emit(_project(tmp_path))
    assert mf["renamed_interfaces"] == []
    r2 = [p for p in mf["derived_pad_pairs"] if p["rule"] == "R2"]
    assert _lr(r2) == [{"l9": ["o_memory_cyc", "o_memory_we"],
                        "rtl": ["o_memory_ren", "o_memory_wen"]}]
    assert r2[0]["side"] == "S"


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
    assert _lr(mf["derived_pad_pairs"]) == EXPECTED
    assert mf["renamed_interfaces"] == []


def test_the_derived_pairs_pass_the_check(tmp_path, capsys):
    proj = _project(tmp_path)
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert rc == 0 and res["verdict"] == "PASS", res
    assert res["pairs"] == []          # nothing authored
    assert {v["side"] for v in res["derived_pairs"]} == {"N", "S"}
    assert all(v["verdict"] == "VERIFIED" for v in res["derived_pairs"])


def test_the_direction_breaks_a_two_document_port_tie(tmp_path):
    """`o_memory_wdata` = w+{memory, data} matches BOTH o_memory_data and
    i_memory_data; the same-direction one is its pair."""
    mf = _emit(_project(tmp_path))
    by_rtl = {r: p["l9"] for p in mf["derived_pad_pairs"] for r in p["rtl"]}
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
    assert _lr(mf["derived_pad_pairs"]) == [
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
    assert mf2["derived_pad_pairs"] == mf["derived_pad_pairs"]


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
    assert mf["derived_pad_pairs"] == []
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
        mf["derived_pad_pairs"])
    assert len(mf["derived_pad_pairs"]) == 3
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


# --------------------------------------------------------------------------- #
# REVIEW WAVE 2 BLOCKER: a derived pair is a SIDE, never a declared rename
# --------------------------------------------------------------------------- #
#: The keys this program owns. Removing them leaves exactly what the landed
#: emitter wrote before D9: the GAP-E2E-8 empty scaffold.
_PROGRAM_KEYS = ("derived_pad_pairs", "renamed_interfaces_derivation",
                 "renamed_interfaces_check")


def _without_derivation(mf):
    bare = {k: v for k, v in mf.items() if k not in _PROGRAM_KEYS}
    bare["renamed_interfaces"] = [
        e for e in (mf.get("renamed_interfaces") or [])
        if e.get("derived_by") != "renamed_interface_derive"]
    return bare


def _stage_rtl(proj):
    """Where consume stages it, and where both gates look for it."""
    rtl = proj / "phase2/stage1/rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    (rtl / "core.v").write_text((proj / "input/vendor_rtl/core.v").read_text())


def _gate_verdicts(proj, capsys):
    """What the two landed phase-2 gates say about ``proj`` as it stands."""
    import l9_rtl_pin_consistency_check as G
    import spec_conformance_check as SC
    capsys.readouterr()
    # The gate's JSON contract reads `ports` (L9 names them `top_ports`); the
    # same list, so the port comparison is MEASURED rather than vacuous.
    l9 = json.loads((proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json")
                    .read_text())
    contract = proj / "spec_contract.json"
    contract.write_text(json.dumps({"module": "core",
                                    "ports": l9["top_ports"]}))
    out = proj / "spec_conformance.json"
    sc_rc = SC.main(["--spec", str(contract),
                     "--rtl-dir", str(proj / "phase2/stage1/rtl"),
                     "--top", "core", "--json", str(out)])
    sc_stdout = capsys.readouterr().out
    assert f"spec ports={len(l9['top_ports'])}(json)" in sc_stdout, sc_stdout
    pin_rc = G.main(["l9_rtl_pin_consistency_check.py", str(proj)])
    pin_stdout = capsys.readouterr().out
    return {"spec_conformance": (sc_rc, sc_stdout, json.loads(out.read_text())),
            "l9_rtl_pin_consistency": (pin_rc, pin_stdout)}


def test_the_derivation_leaves_both_phase2_gates_byte_identical(
        tmp_path, capsys):
    """RED on 3c5697945: its pairs sat in `renamed_interfaces`, so
    spec_conformance turned `port-missing` into `port-renamed-by-manifest` and
    the pin gate tied the L9 pins off, on a fixture WITH a pad placement (the
    landed GAP-E2E-8 test has none, which is why it stayed green)."""
    proj = _project(tmp_path)
    mf = _emit(proj)
    assert mf["renamed_interfaces_derivation"]["verdict"] == "DERIVED"
    _stage_rtl(proj)
    mf_path = proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json"
    with_derivation = _gate_verdicts(proj, capsys)
    mf_path.write_text(json.dumps(_without_derivation(mf), indent=2))
    without = _gate_verdicts(proj, capsys)
    assert with_derivation == without
    # and an L9 port no AUTHORED pair covers is still a hard port-missing
    missing = {f["symbol"] for f in with_derivation["spec_conformance"][2]
               if f["rule"] == "port-missing" and f["severity"] == "ERROR"}
    assert {"o_memory_cyc", "o_memory_we", "o_memory_addr"} <= missing
    assert with_derivation["spec_conformance"][0] == 1
    pin_rc, pin_stdout = with_derivation["l9_rtl_pin_consistency"]
    assert pin_rc == 1 and "o_memory_cyc" in pin_stdout, pin_stdout


def test_only_the_pad_side_reader_sees_the_derived_pairs(tmp_path):
    """`declared_renames` (steps 2 and 15.5ic) carries the derived sides; the
    parser both gates read their renames through returns nothing."""
    import _l_doc_pad_placement as LPP
    import l9_rtl_pin_consistency_check as G
    proj = _project(tmp_path)
    _emit(proj)
    seen = sorted((sorted(a), sorted(b)) for a, b in LPP.declared_renames(proj))
    assert seen == sorted((p["l9"], p["rtl"]) for p in EXPECTED)
    assert G._manifest_renamed_groups(G.load_source_manifest(proj)) == []
    # the point of D9 survives the move: the ring is whole, one side per net
    ring = LPP.derive_own_ring(proj, IMPL)
    assert ring["groups_unresolved"] == [] and ring["nets_on_two_sides"] == []
    assert {"o_memory_wen", "o_memory_ren"} <= set(ring["by_side"]["S"])


def test_a_pair_an_earlier_version_left_in_renamed_interfaces_is_moved(
        tmp_path):
    """A tree emitted by 3c5697945 holds program-stamped pairs in the rename
    key. No author wrote them; re-emitting takes them out of it."""
    proj = _project(tmp_path)
    legacy = [dict(p, derived_by="renamed_interface_derive") for p in EXPECTED]
    rtl = proj / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(
        {"reused_ip": True, "renamed_interfaces": legacy}))
    mf = _emit(proj)
    assert mf["renamed_interfaces"] == []
    assert _lr(mf["derived_pad_pairs"]) == EXPECTED
    assert _lr(mf["renamed_interfaces_derivation"][
        "moved_out_of_renamed_interfaces"]) == EXPECTED


# --------------------------------------------------------------------------- #
# --check: every implemented port ends on EXACTLY one side (review MINOR)
# --------------------------------------------------------------------------- #
def test_check_refuses_a_pair_that_moves_a_placed_port_to_a_second_side(
        tmp_path, capsys):
    """RED on 3c5697945 (rc 0 PASS). `o_status` is on W by its group row; a
    pair onto S would put it on two sides, which 15.5ic refuses."""
    import _l_doc_pad_placement as LPP
    proj = _project(tmp_path, impl=_NO_DATA_IMPL)
    mf = _emit(proj)
    mf["renamed_interfaces"] = [
        {"l9": ["o_memory_we", "o_memory_cyc"],
         "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]},
        {"l9": ["o_memory_we"], "rtl": ["o_status"]}]
    (proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json").write_text(json.dumps(mf))
    assert LPP.derive_own_ring(proj, _NO_DATA_IMPL)["nets_on_two_sides"] == [
        "o_status"]
    rc, res = _check(proj, capsys)
    assert rc == 1, res
    refused = [v for v in res["pairs"] if v["verdict"] == "REFUSED"]
    assert [v["pair"]["rtl"] for v in refused] == [["o_status"]]
    assert "already have a side" in refused[0]["reason"]


def test_check_fails_a_port_two_verified_pairs_put_on_two_sides(
        tmp_path, capsys):
    """RED on 3c5697945. Each pair verifies on its own; together they put
    `o_memory_ack` on N and on S. "Covered" is not "exactly one side"."""
    doc_only = [q if q["name"] != "o_memory_data" else
                _p("o_memory_data", "output", False, 1) for q in DOC_ONLY]
    proj = _project(tmp_path, impl=_NO_DATA_IMPL, doc_only=doc_only, manifest={
        "reused_ip": True, "renamed_interfaces": [
            {"l9": ["o_memory_addr"],
             "rtl": ["o_memory_raddr", "o_memory_waddr"]},
            {"l9": ["o_memory_we", "o_memory_cyc"],
             "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]},
            {"l9": ["o_memory_data"], "rtl": ["o_memory_ack"]}]})
    rc, res = _check(proj, capsys)
    assert all(v["verdict"] == "VERIFIED" for v in res["pairs"]), res["pairs"]
    assert rc == 1 and res["verdict"] == "FAIL"
    assert res["nets_on_two_sides"] == {"o_memory_ack": ["N", "S"]}


def test_a_port_an_exact_row_names_is_placed_even_if_its_range_is_open(
        tmp_path):
    """RED on 3c5697945. W names `o_memory_waddr[AW-1:0]` with AW undeclared.
    The port is still the document's W port; deriving it onto S would put it
    on two sides the moment AW is declared."""
    import renamed_interface_derive as RID
    doc = DOC.replace("| **West (W)** | status pin(s) |",
                      "| **West (W)** | `o_status` / `o_memory_waddr[AW-1:0]` |")
    assert doc != DOC
    d = RID.derive(_project(tmp_path, doc=doc))
    assert all("o_memory_waddr" not in p["rtl"] for p in d["pairs"]), d["pairs"]
    assert "o_memory_waddr" not in d["unplaced_implemented_ports"]


# --------------------------------------------------------------------------- #
# re-emit refreshes the derivation, and the runner's note reads it
# --------------------------------------------------------------------------- #
def test_a_re_emit_refreshes_the_derivation(tmp_path):
    """RED on 3c5697945: once its list was non-empty the second emit took the
    "authored" branch and the first run's derivation stayed forever."""
    doc_only = [q if q["name"] != "o_memory_addr" else
                _p("o_memory_addr", "output", False, 4) for q in DOC_ONLY]
    proj = _project(tmp_path, doc_only=doc_only)
    first = _emit(proj)["renamed_interfaces_derivation"]
    assert {u["port"] for u in first["unresolved"]} == {"o_memory_raddr",
                                                        "o_memory_waddr"}
    # the document is corrected: the address is 3 bits, as built
    spec = proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    spec.write_text(json.dumps({"top_module": "core",
                                "top_ports": IMPL + DOC_ONLY}))
    mf = _emit(proj)
    assert mf["renamed_interfaces_derivation"]["verdict"] == "DERIVED"
    assert mf["renamed_interfaces_derivation"]["unresolved"] == []
    assert mf["renamed_interfaces_check"]["verdict"] == "PASS"


def _waive(proj):
    import design_one_shot_runner as R
    res = R.step_rtl_gen(proj, "digital_cmd_driven")
    assert res.extras.get("fallback_skill") == "catalog-glue-author"
    return res.detail


def test_the_waive_does_not_name_a_port_the_author_has_since_paired(tmp_path):
    """RED on 3c5697945: the note read the first run's derivation, so it kept
    naming o_memory_ack/ren/wen after the author paired them."""
    proj = _project(tmp_path, impl=_NO_DATA_IMPL)
    assert "renamed_interfaces: 3 implemented port(s)" in _waive(proj)
    mf_path = proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json"
    mf = json.loads(mf_path.read_text())
    mf["renamed_interfaces"] = [
        {"l9": ["o_memory_we", "o_memory_cyc"],
         "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]}]
    mf_path.write_text(json.dumps(mf))
    detail = _waive(proj)
    assert "implemented port(s) have" not in detail, detail[-600:]
    assert "o_memory_ack" not in detail


def test_the_waive_names_a_refused_authored_pair(tmp_path):
    """RED on 3c5697945: a REFUSED authored pair sat in the manifest and the
    hand-off said nothing until 15.5ic refused."""
    proj = _project(tmp_path, impl=_NO_DATA_IMPL)
    _waive(proj)
    mf_path = proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json"
    mf = json.loads(mf_path.read_text())
    mf["renamed_interfaces"] = [{"l9": ["i_memory_data"],
                                 "rtl": ["o_memory_ack"]}]
    mf_path.write_text(json.dumps(mf))
    detail = _waive(proj)
    assert "REFUSED" in detail and "o_memory_ack" in detail, detail[-600:]


def test_the_waive_names_a_derivation_that_did_not_run(tmp_path, monkeypatch):
    """RED on 3c5697945: an emitter exception was recorded as NOT_MEASURED in
    the manifest and nowhere in the hand-off."""
    import renamed_interface_derive as RID

    def _boom(project, mf):
        raise RuntimeError("derivation unavailable")

    monkeypatch.setattr(RID, "apply_to_manifest", _boom)
    detail = _waive(_project(tmp_path))
    assert "not measured" in detail and "derivation unavailable" in detail, \
        detail[-600:]


# --------------------------------------------------------------------------- #
# REVIEW WAVE 4b: sides are per BIT NET, as 15.5ic places them
# --------------------------------------------------------------------------- #
_GROUP_ROWS = DOC.split("| **East (E)**")[0]
_IMPL_STATUS4 = [q if q["name"] != "o_status" else
                 _p("o_status", "output", True, 4) for q in IMPL]


def _ring_nets(proj, impl):
    """What step 2 / 15.5ic see: (nets with no side, nets on two sides)."""
    import _l_doc_pad_placement as LPP
    ring = LPP.derive_own_ring(proj, impl)
    placed = {n for nets in ring["by_side"].values() for n in nets}
    every = [n for q in impl for n in LPP.bit_names(q)]
    return [n for n in every if n not in placed], ring["nets_on_two_sides"]


def test_a_bus_the_document_splits_across_two_sides_passes(tmp_path, capsys):
    """RED on 6df205cdc: `--check` counted sides per PORT and FAILed
    o_status as on two sides, while the ring (one pad per bit) is whole."""
    doc = (_GROUP_ROWS + "| **East (E)** | `clk` / `rst` / `o_status[1:0]` |\n"
           "| **West (W)** | `o_status[3:2]` |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    mf = _emit(proj)
    assert _ring_nets(proj, _IMPL_STATUS4) == ([], [])
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res
    assert res["nets_on_two_sides"] == {} and res["nets_without_side"] == []
    assert mf["renamed_interfaces_check"]["verdict"] == "PASS"


def test_a_bus_the_document_places_only_in_part_fails(tmp_path, capsys):
    """RED on 6df205cdc (PASS): only o_status[1:0] has a side, and 15.5ic
    refuses the other two bits as PORT_WITHOUT_A_SIDE."""
    doc = _GROUP_ROWS + "| **East (E)** | `clk` / `rst` / `o_status[1:0]` |\n"
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    mf = _emit(proj)
    no_side, _two = _ring_nets(proj, _IMPL_STATUS4)
    assert no_side == ["o_status[3]", "o_status[2]"]
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (1, "FAIL"), res
    assert res["nets_without_side"] == no_side
    gap = {u["port"]: u for u in
           mf["renamed_interfaces_derivation"]["unresolved"]}["o_status"]
    assert "placement gap" in gap["reason"], gap


def test_one_mismatched_port_does_not_take_the_side_from_its_siblings(
        tmp_path):
    """RED on 6df205cdc: R2 grouped wen, ren and a 4-bit sel under one l9
    tuple BEFORE acceptance, so sel's width rejected all three."""
    impl = IMPL + [_p("o_memory_sel", "output", True, 4)]
    mf = _emit(_project(tmp_path, impl=impl))
    r2 = [p for p in mf["derived_pad_pairs"] if p["rule"] == "R2"]
    assert _lr(r2) == [{"l9": ["o_memory_cyc", "o_memory_we"],
                        "rtl": ["o_memory_ren", "o_memory_wen"]}]
    der = mf["renamed_interfaces_derivation"]
    assert [r["rtl"] for r in der["rejected"]] == [["o_memory_sel"]]
    unresolved = {u["port"]: u for u in der["unresolved"]}
    assert set(unresolved) == {"o_memory_sel"}
    assert "o_memory_sel" in unresolved["o_memory_sel"]["reason"]


# --------------------------------------------------------------------------- #
# an author's copy stays; only what an earlier version wrote is moved
# --------------------------------------------------------------------------- #
def test_an_authors_copy_of_a_derived_pair_is_kept(tmp_path, capsys):
    """RED on 6df205cdc: the SKILL lets an author declare a rename by copying
    a derived entry; the next emit deleted it, stamp and all, every run."""
    import l9_rtl_pin_consistency_check as G
    proj = _project(tmp_path)
    mf = _emit(proj)
    addr = [p for p in mf["derived_pad_pairs"] if p["l9"] == ["o_memory_addr"]]
    mf["renamed_interfaces"] = addr
    (proj / "phase2/stage1/rtl/SOURCE_MANIFEST.json").write_text(json.dumps(mf))
    mf2 = _emit(proj)
    assert mf2["renamed_interfaces"] == addr
    assert "moved_out_of_renamed_interfaces" not in \
        mf2["renamed_interfaces_derivation"]
    assert G._manifest_renamed_groups(G.load_source_manifest(proj)) == [
        ({"o_memory_addr"}, {"o_memory_raddr", "o_memory_waddr"})]
    rc, res = _check(proj, capsys)
    assert rc == 0 and all(v["verdict"] == "VERIFIED" for v in res["pairs"])


def test_the_waive_names_the_pairs_it_moved(tmp_path):
    """RED on 6df205cdc: the move was recorded only inside the manifest."""
    proj = _project(tmp_path)
    legacy = [dict(p, derived_by="renamed_interface_derive") for p in EXPECTED]
    rtl = proj / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps(
        {"reused_ip": True, "renamed_interfaces": legacy}))
    detail = _waive(proj)
    assert "moved 4 pair(s)" in detail, detail[-900:]


# --------------------------------------------------------------------------- #
# --check reads authored pairs the way the pad side does (all rename keys)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alias", ["renamed_buses", "interface_renames"])
def test_check_verifies_a_pair_under_an_alias_rename_key(
        tmp_path, capsys, alias):
    """RED on 6df205cdc (rc 0): `declared_renames` reads all three rename
    keys, so the alias pair puts o_memory_ack on N as well as S."""
    import _l_doc_pad_placement as LPP
    doc_only = [q if q["name"] != "o_memory_data" else
                _p("o_memory_data", "output", False, 1) for q in DOC_ONLY]
    proj = _project(tmp_path, impl=_NO_DATA_IMPL, doc_only=doc_only, manifest={
        "reused_ip": True, "renamed_interfaces": [
            {"l9": ["o_memory_we", "o_memory_cyc"],
             "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]}],
        alias: [{"l9": ["o_memory_data"], "rtl": ["o_memory_ack"]}]})
    _emit(proj)
    assert LPP.derive_own_ring(proj, _NO_DATA_IMPL)["nets_on_two_sides"] == [
        "o_memory_ack"]
    rc, res = _check(proj, capsys)
    assert rc == 1 and res["nets_on_two_sides"] == {"o_memory_ack": ["N", "S"]}
    assert {v["key"] for v in res["pairs"]} == {"renamed_interfaces", alias}


def test_a_port_paired_only_under_an_alias_key_is_not_unpaired(
        tmp_path, capsys):
    """RED on 6df205cdc: the reverse, a false FAIL."""
    proj = _project(tmp_path, impl=_NO_DATA_IMPL, manifest={
        "reused_ip": True, "interface_renames": [
            {"l9": ["o_memory_we", "o_memory_cyc"],
             "rtl": ["o_memory_wen", "o_memory_ren", "o_memory_ack"]}]})
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res


# --------------------------------------------------------------------------- #
# "could not read the placement" is not "the question does not arise"
# --------------------------------------------------------------------------- #
def test_an_unreadable_placement_is_not_measured(tmp_path, capsys):
    """RED on 6df205cdc: a document stating side N twice was labelled
    NOT_APPLICABLE / rc 2, and the hand-off said nothing."""
    proj = _project(tmp_path, doc=DOC + "| **North (N)** | status pin(s) |\n")
    der = _emit(proj)["renamed_interfaces_derivation"]
    assert der["verdict"] == "NOT_MEASURED", der
    assert "could not be read" in der["reason"]
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (3, "NOT_MEASURED"), res
    detail = _waive(proj)
    assert "not measured" in detail and "could not be read" in detail, \
        detail[-900:]


def test_the_waive_says_a_reported_gap_keeps_rc_1(tmp_path):
    """RED on 6df205cdc: the note asked for rc 0 even for a port with no
    document counterpart, where only an invented pair reaches rc 0."""
    detail = _waive(_project(tmp_path, impl=_NO_DATA_IMPL))
    assert "keeps rc 1" in detail, detail[-900:]
    assert "must exit 0" not in detail


def test_an_open_range_token_leaves_the_check_not_measured(tmp_path, capsys):
    """RED on 6df205cdc (PASS). `o_memory_waddr[AW-1:0]` with AW undeclared
    still places its port (so no pair moves it), but 15.5ic refuses the
    partition as PARTITION_UNRESOLVED: which bit lands where is unknown."""
    doc = DOC.replace("| **West (W)** | status pin(s) |",
                      "| **West (W)** | `o_status` / `o_memory_waddr[AW-1:0]` |")
    proj = _project(tmp_path, doc=doc)
    mf = _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (3, "NOT_MEASURED"), res
    assert res["unresolved_tokens"] == ["o_memory_waddr[AW-1:0]"]
    assert not any(n.startswith("o_memory_waddr") for n in
                   res["nets_without_side"] + list(res["nets_on_two_sides"]))
    assert mf["renamed_interfaces_check"]["verdict"] == "NOT_MEASURED"


# --------------------------------------------------------------------------- #
# REVIEW WAVE 6: an unknown bit extent is never a definite verdict
# --------------------------------------------------------------------------- #
_MACRO_STATUS_RTL = (lambda impl: "`define STATUS_W 4\n" + _verilog(impl).replace(
    "[3:0] o_status", "[`STATUS_W-1:0] o_status"))


def _rtl_width_unreadable(proj, impl, rewrite):
    v = proj / "input/vendor_rtl/core.v"
    v.write_text(rewrite(impl))
    import renamed_interface_derive as RID
    widths = {p["name"]: p["width"] for p in RID._rtl_top_ports(proj, "core")[1]}
    return widths


def test_an_rtl_width_the_header_cannot_state_takes_the_l9_width(
        tmp_path, capsys):
    """RED on ed2d863c7 (reviewer scen2, the Wishbone `sel` shape). The RTL
    says `[DW/8-1:0]`, which the header reader cannot state; it was expanded
    as ONE scalar net, so a port the document places in full (`[3:0]`) was a
    FAIL and a "placement gap in the document". 15.5ic places by L9's width."""
    impl = IMPL + [_p("o_status_sel", "output", True, 4)]
    doc = (_GROUP_ROWS + "| **East (E)** | `clk` / `rst` |\n"
           "| **West (W)** | `o_status` / `o_status_sel[3:0]` |\n")
    proj = _project(tmp_path, impl=impl, doc=doc)
    widths = _rtl_width_unreadable(proj, impl, lambda i: _verilog(i).replace(
        "module core (", "module core #(parameter DW = 32) (").replace(
        "[3:0] o_status_sel", "[DW/8-1:0] o_status_sel"))
    assert widths["o_status_sel"] is None, widths      # the premise
    mf = _emit(proj)
    assert "o_status_sel" not in {u["port"] for u in
                                  mf["renamed_interfaces_derivation"]["unresolved"]}
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res
    assert res["widths_from_l9"] == {"o_status_sel": 4}


def test_a_macro_width_placed_in_full_is_not_a_document_gap(tmp_path, capsys):
    """RED on ed2d863c7 (reviewer scenU): `[`STATUS_W-1:0]`, and the document
    places all four bits on W."""
    doc = _GROUP_ROWS + ("| **East (E)** | `clk` / `rst` |\n"
                         "| **West (W)** | `o_status[3:0]` |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    assert _rtl_width_unreadable(proj, _IMPL_STATUS4,
                                 _MACRO_STATUS_RTL)["o_status"] is None
    mf = _emit(proj)
    assert all("placement gap" not in u["reason"] for u in
               mf["renamed_interfaces_derivation"]["unresolved"])
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res


def test_a_macro_width_on_two_sides_is_not_a_pass(tmp_path, capsys):
    """RED on ed2d863c7 (reviewer scenV, rc 0 PASS). An exact row puts
    o_status[3:0] on E and the group row `status pin(s)` puts o_status on W:
    15.5ic refuses PORT_ON_TWO_SIDES."""
    import _l_doc_pad_placement as LPP
    doc = _GROUP_ROWS + ("| **East (E)** | `clk` / `rst` / `o_status[3:0]` |\n"
                         "| **West (W)** | status pin(s) |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _rtl_width_unreadable(proj, _IMPL_STATUS4, _MACRO_STATUS_RTL)
    _emit(proj)
    assert LPP.derive_own_ring(proj, _IMPL_STATUS4)["nets_on_two_sides"]
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (1, "FAIL"), res
    assert res["nets_on_two_sides"] == {f"o_status[{b}]": ["E", "W"]
                                        for b in range(4)}


def test_a_width_nobody_states_is_not_measured_never_a_gap(tmp_path, capsys):
    """RED on ed2d863c7. Neither the RTL header nor L9 states a number, so the
    bits cannot be counted: NOT_MEASURED rc 3 with the port named, never a
    FAIL that blames the document."""
    doc = _GROUP_ROWS + ("| **East (E)** | `clk` / `rst` |\n"
                         "| **West (W)** | `o_status[3:0]` |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _rtl_width_unreadable(proj, _IMPL_STATUS4, _MACRO_STATUS_RTL)
    spec = proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9 = json.loads(spec.read_text())
    for q in l9["top_ports"]:
        if q["name"] == "o_status":
            q["width"] = "`STATUS_W"
    spec.write_text(json.dumps(l9))
    mf = _emit(proj)
    assert all("placement gap" not in u["reason"] for u in
               mf["renamed_interfaces_derivation"]["unresolved"])
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (3, "NOT_MEASURED"), res
    assert res["unresolved_width_ports"] == ["o_status"]


def test_an_open_range_token_split_is_not_measured_not_fail(tmp_path, capsys):
    """RED on ed2d863c7 (reviewer scen (b): rc 1, nets on two sides). E names
    `o_status[SW-1:2]` with SW undeclared and W names `o_status[1:0]`. Which
    bits E means is unknown, so the overlap is not a defect yet."""
    doc = (_GROUP_ROWS + "| **East (E)** | `clk` / `rst` / `o_status[SW-1:2]` |\n"
           "| **West (W)** | `o_status[1:0]` |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (3, "NOT_MEASURED"), res
    assert res["nets_on_two_sides"] == {} and res["nets_without_side"] == []
    # declare SW and the same layout is decided: a legal split, PASS
    declared = proj / "input/docs/L3_external_interface.md"
    declared.write_text(declared.read_text() + "\n## Parameters\n\n| Parameter "
                        "| Default |\n|---|---|\n| `SW` | 4 |\n")
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res


def test_a_group_whole_and_open_token_on_another_side_fail(tmp_path, capsys):
    """The open endpoint is unknown in width, but the token names this port;
    a group row places every bit on W, so E overlaps W for every SW."""
    doc = (_GROUP_ROWS + "| **East (E)** | `clk` / `rst` / `o_status[SW-1:0]` |\n"
           "| **West (W)** | status pin(s) |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (1, "FAIL"), res
    assert any(set(sides) == {"E", "W"} for sides in
               res["nets_on_two_sides"].values()), res


@pytest.mark.parametrize("token", ["`o_status[3:0]`", "`o_status`"],
                         ids=["part-range", "bare-port"])
def test_a_group_whole_and_exact_token_fail_without_a_width(
        tmp_path, capsys, token):
    """A named port plus a whole-port group contradict even when no source
    states how many bits the port has."""
    doc = (_GROUP_ROWS + f"| **East (E)** | `clk` / `rst` / {token} |\n"
           "| **West (W)** | status pin(s) |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _rtl_width_unreadable(proj, _IMPL_STATUS4, _MACRO_STATUS_RTL)
    spec = proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9 = json.loads(spec.read_text())
    next(q for q in l9["top_ports"] if q["name"] == "o_status")["width"] = "`STATUS_W"
    spec.write_text(json.dumps(l9))
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (1, "FAIL"), res
    assert res["nets_on_two_sides"]["o_status"] == ["E", "W"]


@pytest.mark.parametrize("rows,verdict", [
    ("| **East (E)** | `clk` / `rst` |\n| **West (W)** | status pin(s) |\n",
     "PASS"),
    ("| **East (E)** | `clk` / `rst` |\n", None),
    ("| **East (E)** | `clk` / `rst` |\n"
     "| **West (W)** | status pin(s) |\n", "TWO"),
], ids=["whole-port-one-side", "no-row-for-it", "two-whole-port-sides"])
def test_a_port_placed_whole_needs_no_width(tmp_path, capsys, rows, verdict):
    """The paired half of 'a width nobody states': a group row places a port
    WHOLE, so its answer does not depend on the width. One whole-port side is
    PASS. The second id keeps the no-side case a FAIL whatever the width."""
    doc = _GROUP_ROWS + rows
    if verdict == "TWO":
        doc = doc.replace("memory data bus", "memory data bus + status")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    _rtl_width_unreadable(proj, _IMPL_STATUS4, _MACRO_STATUS_RTL)
    spec = proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9 = json.loads(spec.read_text())
    for q in l9["top_ports"]:
        if q["name"] == "o_status":
            q["width"] = "`STATUS_W"
    spec.write_text(json.dumps(l9))
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert res["unresolved_width_ports"] == ["o_status"]
    if verdict == "PASS":
        assert (rc, res["verdict"]) == (0, "PASS"), res
    elif verdict == "TWO":
        assert (rc, res["verdict"]) == (1, "FAIL"), res
        assert res["nets_on_two_sides"]["o_status"] == ["N", "W"]
    else:
        assert (rc, res["verdict"]) == (1, "FAIL"), res
        assert "o_status" in res["nets_without_side"]


def test_l9_nonzero_lsb_is_used_when_rtl_width_is_unreadable(tmp_path, capsys):
    doc = (_GROUP_ROWS + "| **East (E)** | `clk` / `rst` |\n"
           "| **West (W)** | `o_status[7:4]` |\n")
    proj = _project(tmp_path, impl=_IMPL_STATUS4, doc=doc)
    spec = proj / "phase1/generated_docs/L9_INTEGRATION_SPEC.json"
    l9 = json.loads(spec.read_text())
    status = next(q for q in l9["top_ports"] if q["name"] == "o_status")
    status.update(msb=7, lsb=4)
    spec.write_text(json.dumps(l9))
    assert _rtl_width_unreadable(proj, _IMPL_STATUS4, lambda i:
                                 _verilog(i).replace("[3:0] o_status",
                                                     "[B+3:B] o_status"))["o_status"] is None
    _emit(proj)
    rc, res = _check(proj, capsys)
    assert (rc, res["verdict"]) == (0, "PASS"), res
    assert res["widths_from_l9"] == {"o_status": 4}
