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
from librelane_contract import (Refusal, digest, judge_step, resolve_step_configs,
                                run_chain, select_arms, state_from_direct)

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
        raise Refusal("LL_STREAM_MISSING", str(gds))
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


def run(project: Path, image: str, pdk_root: Path, pdk: str,
        routed_def: Path, netlist: Path, sdc: Path,
        canonical_gds: Path) -> dict:
    """Run two streams, require XOR zero, measure both, then finish the winner."""
    configs = resolve_step_configs(project, image, pdk, list(STEPS), pdk_root=pdk_root)
    die = json.loads(configs["KLayout.SealRing"].read_text()).get("DIE_AREA")
    if die and [float(die[0]), float(die[1])] != [0.0, 0.0]:
        # Upstream SealRing currently treats x1/y1 as width/height.  Until its
        # fork fix is in the image, a nonzero-origin die cannot be signed off.
        raise Refusal("LL_SEALRING_ORIGIN_UNSUPPORTED", str(die))
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
    for arm, path in paths.items():
        state_path = _gds_state(compare[-1] / "state_out.json", path,
                                root / f"37-{arm}-finish-state.json")
        finished = _run(project, image, pdk_root, pdk,
                        ["KLayout.SealRing", "KLayout.Filler", "KLayout.Density"],
                        state_path, f"37-{arm}-finish", configs)[-1]
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
                all(row["rc"] == 0 and row["sha256"] is not None
                    for row in gates[arm].values())}
    if not feasible:
        write_json(root / "37-feasibility.json", {"density_errors": density_errors,
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
               "source": str(final_gds), "source_sha256": digest(final_gds),
               "canonical": str(canonical_gds), "canonical_sha256": digest(canonical_gds)})
    return {"engine": winner, "gds": canonical_gds, "drc": counts,
            "state": root / f"37-{winner}-finish/03-klayout-density/state_out.json",
            "promotion": root / "37-promotion.json"}
