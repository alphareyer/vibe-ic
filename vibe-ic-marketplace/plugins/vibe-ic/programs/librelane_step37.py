"""Opt-in step-37 LibreLane stream-out from vibe-ic's admitted routed DEF.

All tool views stay in immutable step directories.  A failure leaves the old
canonical GDS untouched; callers must not promote it as this run's output.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json
import _docker_memory as _dmem  # noqa: E402 — every `docker run` carries the ceiling
from librelane_contract import (Refusal, declaration_config, digest, judge_step,
                                resolve_step_configs, run_chain, select_arms,
                                state_from_direct)

STEPS = ("Magic.StreamOut", "KLayout.StreamOut", "KLayout.XOR",
         "Magic.DRC", "KLayout.DRC", "KLayout.SealRing",
         "KLayout.Filler", "KLayout.Density")


def _run(project: Path, image: str, pdk_root: Path, pdk: str,
         steps: list[str], state: Path, lane: str, configs: dict[str, Path]) -> list[Path]:
    mount = [(pdk_root / pdk, f"/pdk/{pdk}")]
    return run_chain(project, image, [(step, configs[step], state) for step in steps],
                     mounts=mount, lane=lane)


def _routed_state(project: Path, image: str, pdk_root: Path, pdk: str,
                  config: Path, routed_def: Path, netlist: Path,
                  sdc: Path) -> Path:
    """Read the admitted DEF with OpenROAD and pin an equivalent ODB view.

    The shared contract bridge does the conversion, with the resolved step
    config's own LEFs; a missing view refuses by name.
    """
    for source in (routed_def, netlist, sdc):
        if not source.is_file():
            raise Refusal("LL_ROUTE_VIEW_MISSING", str(source))
    return state_from_direct(
        project, image, config,
        {"def": routed_def, "nl": netlist, "sdc": sdc},
        project / "phase3/librelane/37-config/bridge",
        mounts=[(pdk_root / pdk, f"/pdk/{pdk}")])


def _gds_state(state_path: Path, gds: Path, out: Path) -> Path:
    state = json.loads(state_path.read_text())
    if not gds.is_file():
        raise Refusal("LL_STREAM_MISSING", f"no streamed GDS file at {gds}")
    state["gds"] = str(gds)
    write_json(out, state)
    return out


def _measured_drc(project: Path, image: str, pdk_root: Path, pdk: str,
                  source: Path, lane: str, configs: dict[str, Path]) -> dict:
    folders = _run(project, image, pdk_root, pdk,
                   ["Magic.DRC", "KLayout.DRC"], source, lane, configs)
    report = project / "phase3/librelane" / lane / "drc_judgment.json"
    result = judge_step(folders[-1], ["magic__drc_error__count",
                                       "klayout__drc_error__count"], report)
    if result["verdict"] != "PASS":
        raise Refusal("LL_DRC_NOT_MEASURED", str(report))
    values = [result["metrics"][key]["value"] for key in
              ("magic__drc_error__count", "klayout__drc_error__count")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
           for value in values):
        raise Refusal("LL_DRC_INVALID", str(report))
    return {"magic": values[0], "klayout": values[1], "total": sum(values),
            "report": str(report)}


def _vibeic_gds_gates(project: Path, image: str, pdk_root: Path, pdk: str,
                      gds: Path, routed_def: Path, arm: str,
                      magic_config: Path) -> dict:
    """Judge each finished tool GDS with the existing step-37 gate programs."""
    programs = Path(__file__).resolve().parent
    base = project / "phase3/librelane"
    tech = json.loads(magic_config.read_text()).get("MAGIC_TECH")
    results = {}
    for name, script in (("substance", "gds_substance_check.py"),
                         ("port_labels", "gds_port_label_check.py")):
        report = base / f"37-{arm}-{name}.json"
        cmd = ["docker", "run", "--rm", *_dmem.docker_memory_flags(), "-v", f"{project.resolve()}:{project.resolve()}",
               "-v", f"{programs.resolve()}:{programs.resolve()}:ro",
               "-v", f"{(pdk_root / pdk).resolve()}:/pdk/{pdk}:ro",
               image, "--skip", "python3", str(programs / script), str(project),
               "--gds-file", str(gds), "--def-file", str(routed_def),
               "--json", str(report)]
        if name == "port_labels" and tech:
            cmd.extend(["--pdk-tech", tech])
        completed = subprocess.run(cmd, capture_output=True, text=True)
        (base / f"37-{arm}-{name}.log").write_text(
            completed.stdout + "\n" + completed.stderr)
        results[name] = {"rc": completed.returncode, "report": str(report),
                         "sha256": digest(report) if report.is_file() else None}
    return results


#: The image's KLayout.SealRing handles a die off the origin only when it (1)
#: sizes the ring from the die's spans, (2) moves what the PDK script drew at
#: (0,0) onto DIE_AREA (`place_sealring.py`), and (3) fails the step unless a
#: ring encloses DIE_AREA (`verify_ring`). MEASURED on the released images:
#: 0.3.79 has none of the three, 0.3.83 has all three (llv1 W5).
_SEALRING_ORIGIN_PROBE = (
    'import os, librelane\n'
    'from librelane.steps.klayout import SealRing\n'
    'place = os.path.join(os.path.dirname(librelane.__file__), "scripts", "klayout",'
    ' "place_sealring.py")\n'
    'print(hasattr(SealRing, "die_dimensions") and hasattr(SealRing, "verify_ring")'
    ' and os.path.isfile(place))\n')


def sealring_origin_supported(image: str) -> bool:
    """Read from the resolved image at run time, never assumed from a version."""
    completed = subprocess.run(["docker", "run", "--rm", *_dmem.docker_memory_flags(),
                                "--network", "none", "--entrypoint", "python3",
                                image, "-c", _SEALRING_ORIGIN_PROBE],
                               capture_output=True, text=True, timeout=300)
    return completed.returncode == 0 and completed.stdout.strip() == "True"


def run(project: Path, image: str, pdk_root: Path, pdk: str,
        routed_def: Path, netlist: Path, sdc: Path,
        canonical_gds: Path) -> dict:
    """Run two streams, require XOR zero, measure both, then finish the winner."""
    import librelane_pv_signoff as _pv
    # The stream renders the DEF's vias from the tech LEF the route read (a
    # derived via-legalized LEF when the flow staged one), never the PDK's.
    configs = resolve_step_configs(project, image, pdk, list(STEPS), pdk_root=pdk_root,
                                   overlay=_pv.tech_lef_overlay(project))
    die = json.loads(configs["KLayout.SealRing"].read_text()).get("DIE_AREA")
    if die and [float(die[0]), float(die[1])] != [0.0, 0.0] \
            and not sealring_origin_supported(image):
        # An image whose SealRing treats x1/y1 as width/height, or draws at
        # (0,0) and never checks, cannot seal a die off the origin.
        raise Refusal("LL_SEALRING_ORIGIN_UNSUPPORTED",
                      f"{die}: {image} neither sizes, places nor verifies a ring off the origin")
    declared, sources = declaration_config(project)
    core = declared.get("CORE_AREA")
    if not core:
        # Step 37.3's finishing XOR needs the declared core; a stream whose
        # finishing cannot be checked is not promoted (never a silent pass).
        raise Refusal("LL_FINISHING_CORE_UNDECLARED",
                      "tape-out declaration answers.core_area_um")
    state = _routed_state(project, image, pdk_root, pdk,
                          configs["Magic.StreamOut"], routed_def, netlist, sdc)
    magic = _run(project, image, pdk_root, pdk,
                 ["Magic.StreamOut"], state, "37-magic", configs)[-1]
    compare = _run(project, image, pdk_root, pdk,
                   ["KLayout.StreamOut", "KLayout.XOR"],
                   magic / "state_out.json", "37-compare", configs)
    compared = json.loads((compare[-1] / "state_out.json").read_text())
    xor = judge_step(compare[-1], ["design__xor_difference__count"],
                     project / "phase3/librelane/37-xor.json",
                     limits={"design__xor_difference__count": {"eq": 0}})
    if xor["verdict"] != "PASS":
        raise Refusal("LL_XOR_NOT_ZERO", str(project / "phase3/librelane/37-xor.json"))
    paths = {"magic": Path(compared["mag_gds"]),
             "klayout": Path(compared["klayout_gds"])}
    root = project / "phase3/librelane"
    reports = {}
    counts = {}
    final_paths = {}
    density_errors = {}
    gates = {}
    finishing = {}
    for arm, path in paths.items():
        state_path = _gds_state(compare[-1] / "state_out.json", path,
                                root / f"37-{arm}-finish-state.json")
        finish = _run(project, image, pdk_root, pdk,
                      ["KLayout.SealRing", "KLayout.Filler", "KLayout.Density"],
                      state_path, f"37-{arm}-finish", configs)
        finished = finish[-1]
        density_report = root / f"37-{arm}-density.json"
        density = judge_step(finished, ["klayout__density_error__count"],
                             density_report,
                             limits={"klayout__density_error__count": {"eq": 0}})
        density_row = density["metrics"]["klayout__density_error__count"]
        value = density_row.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise Refusal("LL_DENSITY_NOT_MEASURED", str(density_report))
        density_errors[arm] = value
        finished_state = json.loads((finished / "state_out.json").read_text())
        final_paths[arm] = Path(finished_state["gds"])
        if not final_paths[arm].is_file():
            raise Refusal("LL_FINAL_GDS_MISSING", str(final_paths[arm]))
        # Step 37.3 (mig105): finishing never removes, covers or touches the
        # design geometry of the stream it started from.
        sealed = Path(json.loads((finish[0] / "state_out.json").read_text())["gds"])
        finishing[arm] = _pv.run_finishing_xor(
            project, image, pdk_root, pdk, pre=path, sealed=sealed,
            final=final_paths[arm], core=core, core_source=sources["CORE_AREA"],
            lane=f"37.3-{arm}", record=root / f"37.3-{arm}-finishing-xor.json")
        gates[arm] = _vibeic_gds_gates(project, image, pdk_root, pdk,
                                      final_paths[arm], routed_def, arm,
                                      configs["Magic.StreamOut"])
        counts[arm] = _measured_drc(project, image, pdk_root, pdk,
                                    finished / "state_out.json", f"37-{arm}-final-drc", configs)
        report = root / f"37-{arm}-selection.json"
        write_json(report, {"verdict": "PASS", "scope": {"def_sha256": digest(routed_def),
                          "xor_difference_count": 0},
                            "metrics": {"drc_total": {"status": "MEASURED",
                                                      "value": counts[arm]["total"]}}})
        reports[arm] = report
    feasible = {arm: report for arm, report in reports.items()
                if density_errors[arm] == 0 and
                finishing[arm]["verdict"] == "PASS" and
                all(row["rc"] == 0 and row["sha256"] is not None
                    for row in gates[arm].values())}
    if not feasible:
        write_json(root / "37-feasibility.json", {"density_errors": density_errors,
                                                   "finishing_xor": {
                                                       arm: row["verdict"] for arm, row
                                                       in finishing.items()},
                                                   "gates": gates, "drc": counts})
        raise Refusal("LL_NO_FEASIBLE_STREAM", str(root / "37-feasibility.json"))
    selection = select_arms(feasible, {"drc_total": "min"}, root / "37-selection.json")
    winner = selection["selection"]
    if winner == "UNDETERMINED":
        if selection.get("reason") != "LL_PARETO_TIE" or counts["magic"]["total"] != counts["klayout"]["total"]:
            raise Refusal("LL_STREAM_SELECTION_UNDETERMINED", str(root / "37-selection.json"))
        winner = "magic"  # XOR zero and equal measured DRC: stable primary.
        selection["tie_break"] = "magic_primary_after_xor_zero_and_equal_drc"
    final_gds = final_paths[winner]
    # The runner's substance, label, DBU and strict provenance gates consume
    # these exact bytes.  Preserve the LibreLane source and SHA in the receipt.
    shutil.copy2(final_gds, canonical_gds)
    write_json(root / "37-promotion.json", {"selection": winner,
               "selection_detail": selection, "streams": {k: str(v) for k, v in paths.items()},
               "finished": {k: str(v) for k, v in final_paths.items()},
               "drc": counts, "xor": 0, "density": density_errors, "gates": gates,
               "finishing_xor": {arm: row["verdict"] for arm, row in finishing.items()},
               "source": str(final_gds), "source_sha256": digest(final_gds),
               "canonical": str(canonical_gds), "canonical_sha256": digest(canonical_gds)})
    return {"engine": winner, "gds": canonical_gds, "drc": counts,
            "state": root / f"37-{winner}-finish/03-klayout-density/state_out.json",
            "promotion": root / "37-promotion.json"}
