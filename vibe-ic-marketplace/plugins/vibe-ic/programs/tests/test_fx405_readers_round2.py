"""FX_405 round 2 -- the 30 readers round 1 could not drive, driven.

Round 1 (test_fx405_default_readers_never_open_oracle_inputs.py) drove every
design-input reader callable with a project alone. Thirty needed more
arguments. Here each is driven with them, the same way (a child process, an
audit hook on `open`, a project with an oracle tree beside its design input),
and the ones that opened an oracle file now judge it by NAME through
`_reference_flow_boundary.design_input_denial` before the open. MEASURED on
the round-1 branch (9739212d8), the leaks were in shared inner readers:

  * `clock_target_provenance._staged_sdc` (reached from `phase1_doc.main` and
    `phase1_one_shot_runner._run_docs_mode` through the L19 clock record) took
    the period from `input/constraints/golden_timing.sdc`: 2.5 ns, tier
    `staged_sdc`, where the design states 10 ns (round 1, rebased, closes it
    through `sdc_constraints.collect_sdc_files`; pinned here);
  * `phase1_doc._post_emit_crosswalk_l9_ports_to_l1_pin_table_v1_6_555`
    handed `input/rtl/top_ref.v` and `input/rtl/verified_top.v` to the port
    recoverer;
  * `canonical_run_admission._tree_digest` (from `design_one_shot_runner.main`)
    hashed every oracle file into the run's source identity, so editing a
    golden file re-admitted an expensive run;
  * `testbench_gen._resolve_from_design_input` skipped golden/ and oracle/ by a
    hand list, and opened expected/ and canonical_samples/;
  * `pdk_revision_resolve.candidate_trees_from_run` read a golden run's log
    under input/ and adopted the PDK tree the ORACLE loaded;
  * `known_answer_vector_tb_gen._bus_ports_from_rtl` read a golden DUT under
    `input/design_src/` and returned ITS bus port names;
  * `sdf_gate_sim.find_pdk_verilog` scored a golden cell model under
    `input/pdk/verilog/` above the design's;
  * `spec_conformance_check.main` read every `input/docs/**/*.md`, including
    an `input/docs/golden/` tree.

The oracle list is written out, not computed from the authority the fix uses.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_fx405_default_readers_never_open_oracle_inputs as R1  # noqa: E402

ORACLE = {
    **R1.ORACLE,
    "input/golden/golden_run.log": "",           # body written per run (abs path)
    "input/docs/golden/L3_alt.md": "| Opcode | Name |\n|---|---|\n| 0x7E | GOLD |\n",
    "input/design_src/golden/dut.sv":
        "module dut(input h2d_t gold_h2d, output d2h_t gold_d2h);\nendmodule\n",
    "input/pdk/verilog/golden/cells.v":
        "module g(input a, output y);\nspecify\nendspecify\nendmodule\n",
    "input/pdk/liberty/typ_ref.lib": "library(oracle_cells) {}\n",
}

LEGIT = {
    **R1.LEGIT,
    "input/docs/L3_protocol.md": "| Opcode | Name |\n|---|---|\n| 0x01 | READ |\n",
    "input/design_src/rtl/dut.sv":
        "module dut(input h2d_t h2d_i, output d2h_t d2h_o);\nendmodule\n",
    "input/pdk/verilog/cells.v": "module g(input a, output y);\nendmodule\n",
    "input/pdk/liberty/typ.lib": "library(design_cells) {}\n",
    "phase3/stage3/pnr/openroad.log": "",         # body written per run
    "phase2/stage1/rtl/top.v":
        "module top(input clk, output q);\nassign q = clk;\nendmodule\n",
    "phase1/generated_docs/L9_INTEGRATION_SPEC.json": json.dumps({
        "top_module": "top", "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "q", "direction": "output", "width": 1}]}),
    "phase1/generated_docs/L1_DATASHEET.json": json.dumps({"ic_name": "top"}),
    "waivers.json": json.dumps({"oracle_referenced_fix": "x"}),
    "input/oracle/x_bytewise_dump.json": "[126]\n",
    "reports/.keep": "",
}
# `input/oracle/x_bytewise_dump.json` is an oracle by the authority; it sits in
# LEGIT only so the fixture has it. The one reader that looks for it
# (oracle_dump_required_check) checks it EXISTS by name and must never open it.
ORACLE_NAMES = set(ORACLE) | {"input/oracle/x_bytewise_dump.json"}

_IDENT = ("m.build_identity(P, 'phase2', container_image='i', config={}, "
          "program_paths=())")

#: reader -> (module, expression, design-input files it must still open)
READERS = {
    # --- fixed: they opened an oracle file --------------------------------
    "clock_target_provenance.resolve": (
        "clock_target_provenance",
        "{k: m.resolve(P).get(k) for k in ('period_ns', 'tier')}",
        {"input/constraints/top.sdc"}),
    "_post_emit_crosswalk_l9_ports_to_l1_pin_table": (
        "phase1_doc_one_shot_runner",
        "(W('phase1/generated_docs/L9_INTEGRATION_SPEC.json', "
        "json.dumps({'top_module': 'top', 'ports': []})), "
        "m._post_emit_crosswalk_l9_ports_to_l1_pin_table_v1_6_555(P), "
        "sorted(json.dumps(r, sort_keys=True) for r in json.loads((P / 'phase1' "
        "/ 'generated_docs' / 'L1_DATASHEET.json').read_text()).get('pin_table') or []))[-1]",
        {"input/rtl/top.v"}),
    "build_identity": (
        "canonical_run_admission",
        f"(lambda a: (W('input/golden/top.v', 'module top(); endmodule\\n'), "
        f"W('input/rtl/top_ref.v', 'module x(); endmodule\\n'), "
        f"{{'unchanged_by_oracle_edit': a['source_input_sha256'] == "
        f"{_IDENT}['source_input_sha256'], "
        f"'excluded': a.get('source_input_excluded_oracle')}})[-1])({_IDENT})",
        {"input/rtl/top.v", "input/constraints/top.sdc", "input/docs/L1_product.md"}),
    "_resolve_from_design_input": (
        "testbench_gen",
        "[str(p.relative_to(P)) if p else None for p in "
        "(m._resolve_from_design_input(P, 's'), m._resolve_from_design_input(P, 'top'))]",
        {"input/rtl/top.v"}),
    "candidate_trees_from_run": (
        "pdk_revision_resolve",
        "(PDKTREE('gold', 'input/golden/golden_run.log'), "
        "PDKTREE('design', 'phase3/stage3/pnr/openroad.log'), "
        "sorted(Path(t).name for t in m.candidate_trees_from_run(P, m.Fs())[0]))[-1]",
        set()),                                   # the log it must read is not under input/
    "_bus_ports_from_rtl": (
        "known_answer_vector_tb_gen",
        "list(m._bus_ports_from_rtl(P, 'dut', 'h2d_t', 'd2h_t'))",
        {"input/design_src/rtl/dut.sv"}),
    "find_pdk_verilog": (
        "sdf_gate_sim",
        "str(m.find_pdk_verilog(P, {'g'}).relative_to(P))",
        {"input/pdk/verilog/cells.v"}),
    "spec_conformance_check.main": (
        "spec_conformance_check",
        "m.main(['--rtl-dir', str(P / 'phase2/stage1/rtl'), '--spec', "
        "str(P / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json'), "
        "'--json', str(P / 'reports/sc.json')])",
        {"input/docs/L1_product.md", "input/docs/L3_protocol.md"}),
    # --- entry points that reached the fixed readers ----------------------
    "phase1_doc.main": (
        "phase1_doc_one_shot_runner", "MAIN(['x', str(P)])",
        {"input/docs/L1_product.md", "input/constraints/top.sdc"}),
    "_run_docs_mode": (
        "phase1_one_shot_runner", "m._run_docs_mode(P, 'top')",
        {"input/docs/L1_product.md", "input/constraints/top.sdc"}),
    "design_one_shot_runner.main": (
        "design_one_shot_runner",
        "MAIN(['x', str(P), '--top-name', 'top', '--container', 'none'])",
        {"input/constraints/top.sdc"}),
    # --- driven, opened no oracle file on the round-1 branch either -------
    "_docs_name_no_further_fsm_states": (
        "flow_compliance_check",
        "m._docs_name_no_further_fsm_states(P, {'fsm_states': ['IDLE']})",
        {"input/docs/L1_product.md", "input/docs/L3_protocol.md"}),
    "emit_case_register_bus": (
        "known_answer_vector_tb_gen",
        "m.emit_case_register_bus(P, {'name': 'c', 'steps': []}, 'top', "
        "[('input', 'clk', '1')])",
        {"input/docs/L1_product.md"}),
    "_l3_iface_illustrative": (
        "l9_rtl_pin_consistency_check", "m._l3_iface_illustrative(P, 'spi')",
        {"input/docs/L3_protocol.md"}),
    "_backfill_auto_literals_into_typed": (
        "phase1_doc_one_shot_runner",
        "m._backfill_auto_literals_into_typed(P, {})",
        {"input/docs/L1_product.md"}),
    "gen_l9_integration_spec": (
        "phase1_doc_one_shot_runner", "m.gen_l9_integration_spec(P, {}, {})",
        {"input/rtl/top.v"}),
    "step_canonicalize_artefacts": (
        "phase3_one_shot_runner",
        "m.step_canonicalize_artefacts(P, 'top', PDK(P), 'none')",
        {"input/reference_flow/config.tcl"}),
    "oracle_dump_required_check.main": (
        "oracle_dump_required_check", "MAIN(['x', str(P)])", set()),
    "extraction_coverage_check.main": (
        "extraction_coverage_check", "MAIN(['x', str(P)])", set()),
    "foundry_handoff_pack_gen.main": (
        "foundry_handoff_pack_gen", "m.main([str(P)])", set()),
    "l_doc_structured_field_count_check.main": (
        "l_doc_structured_field_count_check", "m.main([str(P)])", set()),
    "gen_l3_cmd_protocol": (
        "phase1_doc_one_shot_runner", "m.gen_l3_cmd_protocol(P, {}, {})", set()),
    "gen_l11_otp_content": (
        "phase1_doc_one_shot_runner", "m.gen_l11_otp_content(P, {})", set()),
    "_apply_alias_normalization": (
        "phase1_doc_one_shot_runner", "m._apply_alias_normalization(P, {})", set()),
    # These read products or use non-recursive PDK globs. The two liberty
    # resolvers below return paths rather than opening them, so their values
    # are pinned after the reader matrix against an oracle-named sibling.
    "_v629_rtl_top_ports": (
        "design_one_shot_runner", "m._v629_rtl_top_ports(P, 'top')", set()),
    "step_synth": (
        "phase3_one_shot_runner", "m.step_synth(P, 'top', PDK(P), 'none')", set()),
    "_augment_defaults": (
        "sdd_atpg_run",
        "vars((lambda a: (m._augment_defaults(P, a), a)[1])(__import__('types')"
        ".SimpleNamespace(**{k: None for k in ('liberty', 'netlist', 'top', "
        "'sta_netlist', 'spef')})))", set()),
    "_resolve_design_liberty": (
        "transition_fault_atpg_run", "m._resolve_design_liberty(P, None)", set()),
}

# These five returned before reaching a read on this fixture. They are
# reviewed statically, not counted among the exercised READERS above.
STATIC_ONLY = {
    "_autoemit_chip_top_wrapper": "no wrapper source to emit",
    "step_dft_lec_chain": "container none returns NOT_MEASURED first",
    "_design_top_input_ports": "no qualifying top port source",
    "step_prelayout_signoff": "container none returns NOT_MEASURED first",
    "_emit_declared_process_sta": "no process STA input to execute",
}

#: Readers that raise or exit by design on this fixture; their opens still count.
EXITS = {"oracle_dump_required_check.main": "SystemExit: 0"}

_CHILD = r'''
import json, os, shutil, sys, tempfile, importlib
from pathlib import Path
programs, template, name, mod, expr = sys.argv[1:6]
sys.path.insert(0, programs); sys.path.insert(0, programs + "/tests")
os.environ["EDA_CONTAINER"] = "none"
m = importlib.import_module(mod)
P = Path(tempfile.mkdtemp(prefix="fx405r2_")) / "proj"
shutil.copytree(template, P)
state = {"on": True, "opened": []}
def _hook(event, args):
    if state["on"] and event == "open" and isinstance(args[0], str) \
            and args[0].startswith(str(P) + "/"):
        state["opened"].append(args[0][len(str(P)) + 1:])
def W(rel, text):                      # a fixture edit, not a read
    state["on"] = False
    try:
        q = P / rel; q.parent.mkdir(parents=True, exist_ok=True); q.write_text(text)
    finally:
        state["on"] = True
def PDKTREE(tag, log_rel):             # a PDK tree the log's library lives in
    state["on"] = False
    try:
        tree = P.parent / f"{tag}pdk"
        lib = tree / "libs.ref" / "sc" / "lib" / f"{tag}_tt.lib"
        lib.parent.mkdir(parents=True, exist_ok=True); lib.write_text("library(x) {}\n")
        (tree / "SOURCES").write_text(f"{tag}-rev 1.0\n")
        q = P / log_rel; q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(f"read_liberty {lib}\n")
    finally:
        state["on"] = True
def MAIN(argv):
    sys.argv = argv
    return m.main()
def PDK(project):
    from test_phase3_postpnr_disclosure_and_gds_guard import _pdk
    state["on"] = False
    try:
        return _pdk(project)
    finally:
        state["on"] = True
sys.addaudithook(_hook)
try:
    value = eval(expr, {"m": m, "P": P, "Path": Path, "json": json, "W": W,
                        "MAIN": MAIN, "PDK": PDK, "PDKTREE": PDKTREE})
    err = None
except BaseException as exc:           # noqa: BLE001 - reported, asserted
    value, err = None, f"{type(exc).__name__}: {str(exc)[:300]}"
state["on"] = False
try:
    json.dumps(value)
except TypeError:
    value = repr(value)[:400]
print("FX405R2" + json.dumps({"opened": sorted(set(state["opened"])),
                              "value": value, "error": err}))
'''


@pytest.fixture(scope="module")
def observed(tmp_path_factory):
    template = tmp_path_factory.mktemp("fx405r2_template")
    for rel, body in {**ORACLE, **LEGIT}.items():
        p = template / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    out = {}
    for name, (mod, expr, _must) in READERS.items():
        try:
            done = subprocess.run(
                [sys.executable, "-c", _CHILD, str(PROGRAMS), str(template),
                 name, mod, expr],
                capture_output=True, text=True, timeout=300,
                stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            out[name] = {"opened": [], "value": None, "error": "TIMEOUT 300s"}
            continue
        line = next((ln for ln in done.stdout.splitlines()
                     if ln.startswith("FX405R2")), None)
        out[name] = (json.loads(line[len("FX405R2"):]) if line else
                     {"opened": [], "value": None,
                      "error": f"no result rc={done.returncode}: "
                               f"{done.stderr[-2000:]}"})
    return out


@pytest.mark.parametrize("reader", sorted(READERS))
def test_reader_opens_no_oracle_file(observed, reader):
    got = observed[reader]
    assert got["error"] in (None, EXITS.get(reader)), got["error"]
    leaked = sorted(set(got["opened"]) & ORACLE_NAMES)
    assert leaked == [], f"{reader} opened oracle files {leaked}"


@pytest.mark.parametrize("reader", sorted(READERS))
def test_reader_still_opens_the_design_input(observed, reader):
    missing = sorted(READERS[reader][2] - set(observed[reader]["opened"]))
    assert missing == [], f"{reader} no longer opens {missing}"


def test_clock_target_is_the_design_period(observed):
    # 9739212d8: 2.5 ns from input/constraints/golden_timing.sdc.
    got = observed["clock_target_provenance.resolve"]["value"]
    assert got["period_ns"] == pytest.approx(10.0)
    assert got["tier"] == "staged_sdc"


def test_port_crosswalk_takes_no_port_from_ref_or_verified_rtl(observed):
    rows = observed["_post_emit_crosswalk_l9_ports_to_l1_pin_table"]["value"]
    assert rows == [], rows            # the design RTL declares no port


def test_run_identity_ignores_oracle_edits_and_discloses_them(observed):
    got = observed["build_identity"]["value"]
    assert got["unchanged_by_oracle_edit"] is True
    assert "input/golden/top.v" in got["excluded"]
    assert "input/constraints/golden_timing.sdc" in got["excluded"]
    assert not any(e.startswith(("input/docs/L1", "input/rtl/top.v"))
                   for e in got["excluded"])


def test_liberty_resolvers_return_design_path_with_oracle_sibling(
        observed, tmp_path):
    import _reference_flow_boundary as rfb
    expected = "input/pdk/liberty/typ.lib"
    denied = "input/pdk/liberty/typ_ref.lib"
    assert rfb.design_input_denial(tmp_path, tmp_path / denied)
    assert observed["_augment_defaults"]["value"]["liberty"] == expected
    assert observed["_resolve_design_liberty"]["value"] == expected
    assert rfb.design_input_denial(tmp_path, tmp_path / expected) is None


def test_tb_resolves_modules_from_the_design_input_only(observed):
    # `s` is defined only in canonical_samples/; `top` in the design RTL.
    assert observed["_resolve_from_design_input"]["value"] == [
        None, "input/rtl/top.v"]


def test_pdk_tree_is_the_one_the_design_run_loaded(observed):
    assert observed["candidate_trees_from_run"]["value"] == ["designpdk"]


def test_bus_ports_are_the_design_dut_ones(observed):
    assert observed["_bus_ports_from_rtl"]["value"] == ["h2d_i", "d2h_o"]


def test_cell_model_is_the_design_pdk_one(observed):
    assert observed["find_pdk_verilog"]["value"] == "input/pdk/verilog/cells.v"


def test_oracle_dump_gate_finds_the_dump_by_name_without_opening_it(observed):
    got = observed["oracle_dump_required_check.main"]
    assert got["error"] == "SystemExit: 0"          # PASS: the dump is present
    assert "input/oracle/x_bytewise_dump.json" not in got["opened"]


# ---- (b): a design document NAMED golden/expected is design input ---------

GOLDEN_NAMED_DOCS = ("input/docs/golden_waveform.md",
                     "input/docs/expected_results.md",
                     "input/docs/golden_ratio_divider.md",
                     "input/docs/L3_expected_protocol.md")


@pytest.mark.parametrize("rel", GOLDEN_NAMED_DOCS)
def test_the_authority_reads_a_golden_named_design_document(tmp_path, rel):
    """The word rule is scoped to the staged tool-config trees; a design
    document merely NAMED golden_* / expected_* under input/docs is design
    input by the repo's authority, and so by the one check readers call."""
    import _reference_flow_boundary as rfb
    import step_input_scope as sis
    assert sis.oracle_reason(rel, tmp_path) is None
    assert rfb.design_input_denial(tmp_path, tmp_path / rel) is None
    # ... while an oracle TREE under docs, and the same name in a staged
    # tool-config tree, are denied (so the pin is not vacuous).
    assert rfb.design_input_denial(
        tmp_path, tmp_path / "input/docs/golden" / Path(rel).name)
    assert rfb.design_input_denial(
        tmp_path, tmp_path / "input/reference_flow" / Path(rel).name)


_DOCS_READERS = {
    "_v1_6_collect_input_docs_text": (
        "phase1_doc_one_shot_runner", "m._v1_6_collect_input_docs_text(P)"),
    "extract_text_pipeline": (
        "phase1_doc_one_shot_runner", "m.extract_text_pipeline(P)"),
    "_input_haystack": (
        "phase1_evidence_grounding_check", "m._input_haystack(P)"),
    "spec_conformance_check.main": READERS["spec_conformance_check.main"][:2],
}


@pytest.mark.parametrize("reader", sorted(_DOCS_READERS))
def test_a_golden_named_design_document_is_READ(tmp_path, reader):
    """A design document called golden_*.md under input/docs is READ."""
    template = tmp_path / "t"
    for rel, body in {**LEGIT, "input/docs/golden_waveform.md":
                      "# Golden waveform\n\nThe clock toggles every 5 ns.\n"}.items():
        p = template / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    mod, expr = _DOCS_READERS[reader]
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(PROGRAMS), str(template), reader,
         mod, expr], capture_output=True, text=True, timeout=300,
        stdin=subprocess.DEVNULL)
    line = next((ln for ln in done.stdout.splitlines()
                 if ln.startswith("FX405R2")), None)
    assert line, done.stderr[-2000:]
    got = json.loads(line[len("FX405R2"):])
    assert got["error"] is None, got["error"]
    assert "input/docs/golden_waveform.md" in got["opened"], got["opened"]
