"""Sequential native proof for the input-derived open physical fixture."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _physical_current as pc
import _tapeout_declaration as td
import phase3_one_shot_runner as runner
import clock_plan_check
import gds_xor_check


def put(project, rel, data):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path


def main():
    project = Path(sys.argv[1]).resolve()
    project.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "fixtures/default_physical/input.json"
    spec = json.loads(source.read_text())
    top = spec["design"]
    pdk = runner._detect_pdk(project, spec["pdk"])
    runner.set_invocation_provenance_sink(project)
    runner._RUN_STARTED_AT = __import__("time").time()
    put(project, "input/README.md", spec["scope"] + "\n")
    put(project, "input/source.json", source.read_text())
    put(project, "reports/phase3/technology_units.json", json.dumps({"pdk": spec["pdk"]}))
    declaration = td.blank_declaration()
    declaration["answers"]["deliverable"] = spec["route"]
    declaration[td.PROVENANCE_KEY] = {"deliverable": {
        "declares": True, "answered_by": "owner", "citation": "input/source.json: route=HARDMACRO (tool fixture)"}}
    put(project, td.DECLARATION_REL, json.dumps(declaration))
    put(project, "phase1/generated_docs/L1_IC_SPEC.json", json.dumps({"pdk": spec["pdk"], "design_name": top}))
    put(project, "phase1/generated_docs/L19_CONSTRAINTS_PDK.json", json.dumps({"fields": {"pdk_target": spec["pdk"]}}))
    put(project, "config.json", json.dumps({"pdk": spec["pdk"]}))
    pins = []
    for name, direction, use, layer, x, y in spec["ports"]:
        pins.append(f"- {name} + NET {name} + DIRECTION {direction} + USE {use}\n"
                    f" + PORT + LAYER {layer} ( -200 -200 ) ( 200 200 )\n"
                    f" + PLACED ( {round(x*1000)} {round(y*1000)} ) N ;")
    definition = (f'VERSION 5.8 ;\nDIVIDERCHAR "/" ;\nBUSBITCHARS "[]" ;\nDESIGN {top} ;\n'
                  f'UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 100000 50000 ) ;\n'
                  'COMPONENTS 0 ;\nEND COMPONENTS\n'
                  f'PINS {len(pins)} ;\n' + "\n".join(pins) + '\nEND PINS\nEND DESIGN\n')
    pnr = project / "phase3/stage3/pnr"
    floor = put(project, "phase3/stage3/pnr/floorplan.def", definition)
    put(project, "phase3/stage3/pnr/routed.def", definition)
    put(project, f"phase3/stage3/pnr/{top}.def", definition)
    sdc = put(project, "phase2/stage2/constraints/current.sdc",
              f'create_clock -name {spec["clock"]["name"]} -period {spec["clock"]["period_ns"]} [get_ports A]\n')
    put(project, "phase3/stage3/pnr/constraint.sdc", sdc.read_text() +
        'set_input_delay 1 -clock system [get_ports A]\nset_output_delay 1 -clock system [get_ports Y]\n')
    plan = project / "phase3/stage3/cts/clock_plan.json"
    results = {}
    notes = []
    produced = runner.emit_clock_plan(project, plan, floor, pnr, notes)
    results["16"] = {"produced": produced, "notes": notes,
                     "gate_rc": clock_plan_check.main([str(project), "--json", str(project / "clock_gate.json")])}
    if produced:
        saved = sdc.read_text()
        sdc.write_text(saved.replace("10.0", "13.0"))
        results["16"]["stale_sdc_rc"] = clock_plan_check.main([str(project)])
        sdc.write_text(saved)
    # KLayout writes both physical subjects from the supplied geometry. The
    # reference has a different library name, preserving independently named bytes.
    import klayout.db as db
    layout = db.Layout()
    layout.dbu = 0.001
    cell = layout.create_cell(top)
    for layer, dtype, x1, y1, x2, y2 in spec["geometry"]:
        cell.shapes(layout.layer(layer, dtype)).insert(db.Box(
            round(x1*1000), round(y1*1000), round(x2*1000), round(y2*1000)))
    shipped = project / f"phase3/stage4/gds/{top}.gds"
    shipped.parent.mkdir(parents=True, exist_ok=True)
    options = db.SaveLayoutOptions()
    options.gds2_libname = "physical_final"
    layout.write(str(shipped), options)
    reference = pnr / f"{top}.prefinish.gds"
    options.gds2_libname = "physical_boundary"
    layout.write(str(reference), options)
    (pnr / f"{top}.gds").write_bytes(shipped.read_bytes())
    put(project, str(reference.relative_to(project)) + ".receipt.json", json.dumps({
        "sha256": pc.digest(reference), "def_sha256": pc.digest(pnr / "routed.def")}))
    xor_rc = gds_xor_check.main([str(project), "--json", str(project / "reports/phase3/gds_xor.json")])
    results["37.3"] = {"producer_rc": xor_rc,
                         "gate_rc": gds_xor_check.main([str(project), "--check", "reports/phase3/gds_xor.json"])}
    # A real geometric tool comparison cannot certify missing connectivity.
    put(project, f"phase3/stage3/pnr/{top}_pnr.v",
        f"module {top}(input A, output Y);\n"
        "sky130_fd_sc_hd__buf_1 u_buffer (.A(A), .X(Y));\nendmodule\n")
    # The open width deck is a direct-native screen of this fixture's input
    # property. It is not a replacement for any foundry deck.
    deck = put(project, "input/pdk/open_width.drc",
               'source($input, $top_cell)\nreport("open fixture width", $report)\n'
               f'input(68,20).width({spec["drc_min_width_um"]}).output("met1.width", "input width")\n'
               'puts "DIRECT_DRC_NATIVE_DONE"\n')
    pdk.drc_deck = str(deck)
    row = runner.step_drc(project, top, pdk, "")
    results["31"] = {"drc_status": row.status, "detail": row.detail,
                      "current_reader": pc.check_direct_half(project, "drc")}
    cp = subprocess.run([sys.executable, str(PROGRAMS / "drc_report_check.py"), str(project),
                         "--mode", "drc", "--signoff", "--under", "reports/phase3/drc_signoff.rpt",
                         "--json", str(project / "drc_gate.json")], capture_output=True, text=True)
    put(project, "drc_gate.log", cp.stdout + "\n" + cp.stderr)
    results["31"]["drc_gate_rc"] = cp.returncode
    notes = []
    results["31"]["erc_produced"] = runner._emit_erc_report(
        project, top, pdk, "", project / "reports/phase3/erc.rpt", notes)
    results["31"]["erc_notes"] = notes
    # Current post-route inputs for OpenSTA's timing-model producer.
    put(project, f"phase3/stage3/extracted/spef_corners/{top}.max.spef",
        f'*SPEF "IEEE 1481-1998"\n*DESIGN "{top}"\n*DATE "input fixture"\n'
        '*VENDOR "open fixture"\n*PROGRAM "input fixture"\n*VERSION "1"\n'
        '*DESIGN_FLOW "NAME_SCOPE LOCAL"\n*DIVIDER /\n*DELIMITER :\n*BUS_DELIMITER [ ]\n'
        '*T_UNIT 1 NS\n*C_UNIT 1 PF\n*R_UNIT 1 OHM\n*L_UNIT 1 HENRY\n'
        '*PORTS\nA I\nY O\n'
        '*D_NET A 0.001\n*CONN\n*P A I\n*I u_buffer:A I\n*CAP\n1 A 0.001\n*END\n'
        '*D_NET Y 0.001\n*CONN\n*P Y O\n*I u_buffer:X O\n*CAP\n1 Y 0.001\n*END\n')
    sta_script = put(project, "reports/phase3/fixture_sta.tcl",
        f"read_liberty {{{pdk.liberty}}}\nread_verilog {{{pnr / (top + '_pnr.v')}}}\n"
        f"link_design {top}\nread_sdc {{{pnr / 'constraint.sdc'}}}\n"
        f"read_spef {{{project / ('phase3/stage3/extracted/spef_corners/' + top + '.max.spef')}}}\n"
        f'puts "=== SETUP corner: process=tt liberty={pdk.liberty}, SPEF={top}.max.spef ==="\n'
        f'puts "STA_BASIS_NETLIST: {top}_pnr.v"\nputs "STA_BASIS_SPEF: {top}.max.spef"\n'
        'puts "STA_BASIS: POST_ROUTE_SPEF"\nreport_checks -path_delay max\n')
    sta_rc, _ = pc.run_native(project, "sta", ["-exit", str(sta_script)],
                               project / "reports/phase3/sta_mcorner_ocv.rpt")
    results["37.5ip_sta_rc"] = sta_rc
    kit_row = runner.step_digital_hardmacro_gen(project, pdk, "")
    results["37.5ip"] = {"status": kit_row.status, "detail": kit_row.detail}
    docs_row = runner.step_ip_release_docs_gen(project, top, pdk.name)
    results["37.5ip"]["documents_status"] = docs_row.status
    results["37.5ip"]["documents_detail"] = docs_row.detail
    results["input_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    put(project, "NATIVE_RESULTS.json", json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
