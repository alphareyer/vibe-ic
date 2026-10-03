"""Bounded native stream dependency check; never runs a whole IC or signoff."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import librelane_contract as lc
import librelane_step37 as streams
import _tapeout_declaration as td
import _physical_current as pc
import gds_xor_check as xor


def put(project, rel, doc):
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return path


def main():
    source, project, image, pdk_root = sys.argv[1:]
    source, project, pdk_root = map(Path, (source, project, pdk_root))
    shutil.copytree(source, project)
    spec = json.loads((project / "input/source.json").read_text())
    top, pdk = spec["design"], spec["pdk"]
    os.environ["VIBEIC_DOCKER_MEMORY"] = "6g"
    os.environ["OMP_NUM_THREADS"] = "2"
    put(project, "phase3/librelane_switch.json", {"image": image, "pdk": pdk,
                                                "pdk_root_host": str(pdk_root)})
    put(project, "phase1/generated_docs/L8_TIMING_WAVEFORM.json", {
        "clock_domains": [{"role": "primary", "period_ns": spec["clock"]["period_ns"],
                           "source_pin": spec["clock"]["port"]}]})
    put(project, "phase1/generated_docs/L9_INTEGRATION_SPEC.json", {"design_name": top, "ports": spec["ports"]})
    declaration = json.loads((project / td.DECLARATION_REL).read_text())
    for key, value in {"top_cell": top, "macro_area_um": [0, 0, *spec["die_um"]]}.items():
        declaration["answers"][key] = value
        declaration[td.PROVENANCE_KEY][key] = {
            "answered_by": "owner", "citation": "input/source.json: bounded stream fixture"}
    put(project, td.DECLARATION_REL, declaration)
    rtl = project / "phase2/stage1/rtl" / (top + ".v")
    rtl.parent.mkdir(parents=True, exist_ok=True)
    rtl.write_text((project / f"phase3/stage3/pnr/{top}_pnr.v").read_text())
    # This wrapper changes only the container launch, retaining the existing
    # native producers, configs, fingerprints, outputs and strict consumers.
    original = lc.run_container
    launches = []
    def launch(argv, **kwargs):
        argv = list(argv)
        entrypoint = None
        if "--entrypoint" in argv:
            at = argv.index("--entrypoint")
            entrypoint = argv[at + 1]
            del argv[at:at + 2]
        at = argv.index(image)
        if entrypoint:
            argv[at + 1:at + 1] = ["--skip", entrypoint]
        if argv[at + 1] != "--skip":
            raise RuntimeError("native wrapper requires --skip first after image")
        argv[at + 2:at + 2] = ["/usr/bin/time", "-v"]
        argv[2:2] = ["--cpus", "2", "--network", "none"]
        before = time.monotonic()
        cp = original(argv, **kwargs)
        launches.append({"argv": argv, "rc": cp.returncode,
                         "wall_seconds": time.monotonic() - before,
                         "max_rss_kB": re.findall(r"Maximum resident set size \(kbytes\): (\d+)", cp.stderr)})
        put(project, "NATIVE_LAUNCHES.json", launches)
        return cp
    lc.run_container = launch
    result = {"scope": "Magic.StreamOut, KLayout.StreamOut, KLayout.XOR only"}
    try:
        ids = ["Magic.StreamOut", "KLayout.StreamOut", "KLayout.XOR"]
        configs = lc.resolve_step_configs(project, image, pdk, ids, pdk_root=pdk_root,
                                          overlay={"VERILOG_FILES": (["dir::" + str(rtl.relative_to(project))],
                                                   "input/source.json buffer fixture netlist")})
        pnr = project / "phase3/stage3/pnr"
        state = streams._routed_state(project, image, pdk_root, pdk, configs[ids[0]],
                                      pnr / "routed.def", pnr / (top + "_pnr.v"),
                                      pnr / "constraint.sdc")
        magic = streams._run(project, image, pdk_root, pdk, [ids[0]], state,
                             "37-magic", configs)[-1]
        compare = streams._run(project, image, pdk_root, pdk, ids[1:],
                               magic / "state_out.json", "37-compare", configs)
        result["native_xor"] = lc.judge_step(compare[-1], ["design__xor_difference__count"],
                                           project / "phase3/librelane/37-xor.json",
                                           limits={"design__xor_difference__count": {"eq": 0}})
        final = json.loads((compare[-1] / "state_out.json").read_text())
        shipped = project / f"phase3/stage4/gds/{top}.gds"
        shutil.copyfile(final["klayout_gds"], shipped)
        reference = pnr / (top + ".prefinish.gds")
        shutil.copyfile(final["mag_gds"], reference)
        put(project, str(reference.relative_to(project)) + ".receipt.json", {
            "sha256": pc.digest(reference), "def_sha256": pc.digest(pnr / "routed.def")})
        result["producer_rc"] = xor.main([str(project), "--json", str(project / "reports/phase3/gds_xor.json")])
        result["gate_rc"] = xor.main([str(project), "--check", "reports/phase3/gds_xor.json"])
    except Exception as exc:
        result.update(verdict="NOT_MEASURED", reason=str(exc))
    put(project, "NATIVE_DEPENDENCY_RESULT.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
