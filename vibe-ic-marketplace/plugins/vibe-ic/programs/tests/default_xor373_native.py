"""Declared bounded native stream fixture, followed by the ordinary caller."""
import json
import shutil
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _native373_current as streams
import _physical_current as pc
import _tapeout_declaration as td
import phase3_one_shot_runner as runner
import gds_xor_check as xor


def put(project, rel, value):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))
    return path


def main():
    project, image, pdk_root = sys.argv[1:]
    project = Path(project).resolve()
    project.mkdir(parents=True)
    source = Path(__file__).parent / "fixtures/default_physical/input.json"
    spec = json.loads(source.read_text())
    top = spec["design"]
    put(project, "input/source.json", source.read_text())
    put(project, "reports/phase3/technology_units.json", {"pdk": spec["pdk"]})
    put(project, "phase3/librelane_switch.json", {"image": image, "pdk": spec["pdk"], "pdk_root_host": pdk_root})
    put(project, "phase1/generated_docs/L19_CONSTRAINTS_PDK.json", {"fields": {"pdk_target": spec["pdk"]}})
    put(project, "phase1/generated_docs/L8_TIMING_WAVEFORM.json", {
        "clock_domains": [{"role": "primary", "period_ns": spec["clock"]["period_ns"], "source_pin": spec["clock"]["port"]}]})
    put(project, "phase1/generated_docs/L9_INTEGRATION_SPEC.json", {"design_name": top, "ports": spec["ports"]})
    declaration = td.blank_declaration()
    declaration[td.PROVENANCE_KEY] = {}
    for key, value in {"deliverable": spec["route"], "top_cell": top,
                       "macro_area_um": [0, 0, *spec["die_um"]]}.items():
        declaration["answers"][key] = value
        declaration[td.PROVENANCE_KEY][key] = {"answered_by": "owner", "citation": "input/source.json: declared bounded native stream fixture"}
    put(project, td.DECLARATION_REL, declaration)
    pins = [f"- {name} + NET {name} + DIRECTION {direction} + USE {use}\n"
            f" + PORT + LAYER {layer} ( -200 -200 ) ( 200 200 )\n"
            f" + PLACED ( {round(x*1000)} {round(y*1000)} ) N ;"
            for name, direction, use, layer, x, y in spec["ports"]]
    definition = (f'VERSION 5.8 ;\nDIVIDERCHAR "/" ;\nBUSBITCHARS "[]" ;\nDESIGN {top} ;\n'
                  f'UNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( {round(spec["die_um"][0]*1000)} {round(spec["die_um"][1]*1000)} ) ;\n'
                  'COMPONENTS 0 ;\nEND COMPONENTS\n' + f'PINS {len(pins)} ;\n'
                  + "\n".join(pins) + '\nEND PINS\nEND DESIGN\n')
    pnr = project / "phase3/stage3/pnr"
    put(project, "phase3/stage3/pnr/routed.def", definition)
    ports = [f"{direction.lower()} {name}" for name, direction, *_ in spec["ports"]]
    put(project, f"phase3/stage3/pnr/{top}_pnr.v", f"module {top}({', '.join(ports)});\nendmodule\n")
    put(project, "phase3/stage3/pnr/constraint.sdc", f'create_clock -name {spec["clock"]["name"]} -period {spec["clock"]["period_ns"]} [get_ports {spec["clock"]["port"]}]\n')
    # Select actual producer outputs as the declared fixture's shipped view.
    # No promotion/signoff document is authored. The ordinary producer below
    # invokes and validates the same chain and measures the current pair.
    produced = streams.produce(project, pnr / "routed.def")
    shipped = project / f"phase3/stage4/gds/{top}.gds"
    shipped.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / produced["current"]["outputs"]["klayout_gds"]["path"], shipped)
    rows = runner.run_pre_audit_producers(project, only_names=("gds_xor",))
    gate_rc = xor.main([str(project), "--check", "reports/phase3/gds_xor.json"])
    result = {"input_sha256": pc.digest(source), "route": spec["route"],
              "scope": "declared empty-component pin geometry; two real streams, no functional/LVS/signoff claim",
              "ordinary_rows": [vars(row) for row in rows], "gate_rc": gate_rc,
              "native_count": produced["count"], "dependency_sha256": pc.digest(project / streams.REL),
              "shipped_sha256": pc.digest(shipped)}
    put(project, "NATIVE_RESULT.json", result)
    print(json.dumps(result, indent=2))
    return 0 if gate_rc == 0 and rows and rows[0].status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
