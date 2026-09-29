#!/usr/bin/env python3
"""Step 13 arm B: LibreLane Yosys.EQY beside lec_run (arm A).

lec_run (yosys equiv_*) stays arm A. This module runs EQY through the contract
with a vibe-ic-generated EQY_SCRIPT and judges EQY's own per-partition status
files, then `combine` keeps the conclusive arm.

The generated script differs from LibreLane's generic one in three measured ways:

* No `bitwuzla` strategy — the solver is not in the image (review correction).
* The gate side reads the cells from the PDK liberty's `function` attributes
  (`read_liberty`), not from LibreLane's `formal_pdk.v`: for a PDK other than
  sky130A that file is the PDK's Verilog models run through `iverilog -E`, and
  gf180mcu's models are UDP-based, which Yosys cannot parse (measured: syntax
  error at the first UDP instance). EQY_FORCE_ACCEPT_PDK is still required, or
  the step skips itself.
* `read_verilog -icells`, so a netlist that still carries Yosys internal cells
  (`$_DFF_P_`) reads.
* The gate is flattened and its private names purged BEFORE EQY partitions.
  A liberty flop reads as `IQ` state behind a buffer to `Q`, so EQY matched a
  gold register (`pr`) to a gate alias the partition never cut at: on spm x
  gf180mcuD (CUT_W4 proof, 2026-09-29) it drove the gate's `p` from that
  flop's own zero-initialised state while the gold `p` read a free `pr`, and
  returned `spm.p`/`spm.c` NOT_EQUIVALENT on an unreachable state beside
  lec_run's 65/65 PASS. With no gate-internal cut point EQY proves from the
  equal initial state, so a counterexample it reports is reachable (the same
  gate with `p` inverted, one xor2->xnor2, one nand2->and2: each FAIL).

EQY's top-level status folds "could not decide" into FAIL. The reader therefore
reads every partition's strategy status files: a partition is PROVEN when a
strategy says PASS, NOT_EQUIVALENT when a strategy says FAIL, and otherwise
UNKNOWN. UNKNOWN is INCONCLUSIVE, never PASS and never a counterexample.

EQY's miter treats an `x` on the gold side as don't-care, so a compared point
that is x on the gold side (a register with no reset and no init) is vacuous.
For that reason EQY is never the sole evidence: `combine` requires arm A not to
have found a counterexample, and arms that disagree stop the step.

chip-AGNOSTIC: no design, PDK or cell literal selects a branch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional
import fnmatch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_json  # noqa: E402
from librelane_contract import (Refusal, _load, digest,  # noqa: E402
                                resolve_step_config, run_chain)
import instrument_calibration  # noqa: E402

STEP = "Yosys.EQY"
#: EQY's own status words (eqy_job.py `possible_statuses`).
_EQY_STATUSES = ("PASS", "FAIL", "UNKNOWN", "ERROR", "TIMEOUT")


def eqy_script(top: str, rtl: list[Path], netlist: Path, liberty: str) -> str:
    gold = " ".join(str(p) for p in rtl)
    return "\n".join([
        "[gold]", f"read_verilog -formal -sv {gold}", "",
        "[gate]", f"read_liberty -ignore_miss_func {liberty}",
        f"read_verilog -formal -sv -icells {netlist}",
        f"hierarchy -top {top}", "flatten", "opt_clean -purge", "",
        "[script]", f"hierarchy -top {top}", "proc", f"prep -top {top} -flatten",
        "memory -nomap", "async2sync", "setundef -init -zero", "",
        "[strategy sat]", "use sat", "depth 5", "",
        "[strategy pdr]", "use sby", "engine abc pdr -rfi", ""])


def _partition_population(scratch: Path) -> Optional[dict[str, bool]]:
    """Declared partition names and gold-x tags; unknown population is None."""
    try:
        lines = (scratch / "partition.list").read_text(errors="replace").splitlines()
    except OSError:
        return None
    found: dict[str, bool] = {}
    for line in lines:
        if not line.strip():
            continue
        head, separator, _ = line.partition(":")
        words = head.split()
        if not separator or len(words) < 2 or words[1] in found:
            return None
        found[words[1]] = "xbits" in words[2:]
    return found


def partition_status(scratch: Path) -> Optional[dict[str, dict[str, str]]]:
    """Every declared partition, including those whose strategies never finished."""
    instrument_calibration.assert_calibrated("librelane_eqy::partition_status")
    root = scratch / "strategies"
    declared = _partition_population(scratch)
    if not root.is_dir() and declared is None:
        return None
    found: dict[str, dict[str, str]] = {name: {} for name in declared or {}}
    for status in sorted(root.glob("*/*/status")):
        try:
            words = status.read_text(errors="replace").split()
        except OSError:
            words = []
        word = words[0] if words else ""
        found.setdefault(status.parent.parent.name, {})[status.parent.name] = (
            word if word in _EQY_STATUSES else "UNREADABLE")
    return found


def xbit_partitions(scratch: Path) -> Optional[list[str]]:
    """Partitions EQY tags `xbits` in partition.list; None if it was not written.

    Such a partition compares signals that are x on the gold side, and EQY's
    miter treats gold x as don't-care — its PASS says nothing (measured: a buf
    in place of an inv behind a resetless flop is `DONE (PASS)`).
    """
    instrument_calibration.assert_calibrated("librelane_eqy::xbit_partitions")
    declared = _partition_population(scratch)
    if declared is None:
        return None
    return [name for name, xbits in declared.items() if xbits]


def _fingerprint(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": "sha256:" + digest(path)}


def _selected_liberty(config: dict) -> str:
    libs = config.get("CELL_LIBS") or config.get("LIB") or {}
    corner = config.get("DEFAULT_CORNER") or ""
    match = [lib for pattern, group in libs.items() if fnmatch.fnmatch(corner, pattern)
             for lib in (group if isinstance(group, list) else [group])]
    if len(match) != 1 or not isinstance(match[0], str):
        raise Refusal("LL_EQY_LIBERTY_UNRESOLVED", f"{corner}: {match}")
    return match[0]


def _liberty_binding(project: Path, config: dict, mounts: list) -> dict:
    """Resolve the library EQY reads through the same mounts as run_chain."""
    guest = Path(_selected_liberty(config))
    if not guest.is_absolute():
        raise Refusal("LL_EQY_LIBERTY_UNBOUND", str(guest))
    roots = [(project.resolve(), project.resolve()),
             *((Path(host).resolve(), Path(target)) for host, target in mounts)]
    # A more specific mount wins; the last explicit mount wins a duplicate.
    roots = sorted(enumerate(roots), key=lambda row: (len(row[1][1].parts), row[0]),
                   reverse=True)
    for _, (host_root, guest_root) in roots:
        if guest.is_relative_to(guest_root):
            host = host_root / guest.relative_to(guest_root)
            if host.is_file():
                return dict(_fingerprint(host), guest_path=str(guest))
            break
    raise Refusal("LL_EQY_LIBERTY_UNBOUND", f"no readable mounted bytes for {guest}")


def _native_snapshot(folder: Path) -> dict:
    """Bind the native run records and the entire declared/status population."""
    names = ["config.json", "input_fingerprint.json", "vibeic_receipt.json",
             "pdk_root.json", "state_out.json", "invocation.log", "scratch/partition.list"]
    names += [str(path.relative_to(folder)) for path in
              sorted((folder / "scratch/strategies").glob("*/*/status"))]
    return {name: "sha256:" + digest(folder / name) for name in names}


def proof_binding_current(folder: Path, identity: dict) -> bool:
    """A cached native proof keeps its producing inputs, not a new request's."""
    try:
        project = Path(identity["project"])
        config = _load(Path(identity["resolved_config"]["path"]))
        native_config = _load(folder / "config.json")
        native_input = _load(folder / "input_fingerprint.json")
        receipt = _load(folder / "vibeic_receipt.json")
        if (identity["native_run"]["sha256"] != _native_snapshot(folder)
                or identity["native_run"]["input"] != native_input
                or receipt["input"] != native_input
                or config["meta"]["step"] != STEP
                or native_input["step"] != STEP
                or native_config["meta"]["step"] != STEP
                or native_input["image"] != identity["image"]
                or native_input["config"] != identity["resolved_config"]["sha256"].removeprefix("sha256:")
                or native_input["state"] != identity["initial_state"]["sha256"].removeprefix("sha256:")
                or native_config["DESIGN_NAME"] != identity["top"]
                or Path(native_config["EQY_SCRIPT"]).resolve() != Path(identity["equivalence_script"]["path"])
                or _selected_liberty(native_config) != identity["liberty"]["guest_path"]
                or _liberty_binding(project, config, identity["mounts"]) != identity["liberty"]):
            return False
        pdk = _load(folder / "pdk_root.json")
        root = identity["pdk_root"].rstrip("/") or "/"
        if (pdk["cli_pdk_root"] != identity["pdk_root"] or pdk["mounts_under_it"] !=
                [[host, guest] for host, guest in identity["mounts"]
                 if guest == root or guest.startswith(root + "/")]):
            return False
        files = [identity[key] for key in ("gate_netlist", "equivalence_script",
                                         "resolved_config", "initial_state", "producer_source")]
        return all(_fingerprint(Path(item["path"])) == item
                   for item in files + identity["gold_rtl"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, Refusal):
        return False


def read_eqy(folder: Path) -> dict:
    scratch = folder / "scratch"
    declared = _partition_population(scratch)
    parts = partition_status(scratch)
    unexpected = sorted(set(parts or {}) - set(declared or {}))
    xbits = xbit_partitions(scratch) or []
    proven, failed, unknown = [], [], []
    for name, strategies in (parts or {}).items():
        words = set(strategies.values())
        if "FAIL" in words:
            failed.append(name)
        elif "PASS" in words:
            proven.append(name)
        else:
            unknown.append(name)
    if failed:
        verdict, why = "FAIL", f"{len(failed)} partition(s) not equivalent: {', '.join(failed[:5])}"
    elif not parts:
        verdict, why = "NOT_MEASURED", "EQY wrote no partition status (read/partition stage failed)"
    elif not declared or unexpected:
        verdict, why = "INCONCLUSIVE", ("EQY_PARTITION_POPULATION_UNBOUND: missing, empty or "
                                        "unreadable partition.list, or undeclared status "
                                        f"partitions: {unexpected}")
    elif unknown:
        verdict, why = "INCONCLUSIVE", f"{len(unknown)} partition(s) undecided: {', '.join(unknown[:5])}"
    elif xbits:
        verdict, why = "INCONCLUSIVE", (f"EQY_XBITS_VACUOUS: {len(xbits)} partition(s) compare "
                                        f"gold x as don't-care: {', '.join(xbits[:5])}")
    else:
        verdict, why = "PASS", f"all {len(proven)} partition(s) proven"
    report = {"program": "librelane_eqy", "engine": "eqy (LibreLane Yosys.EQY)",
              "verdict": verdict, "explanation": why,
              "compared_points": len(declared or {}), "proven_points": len(proven),
              "non_equivalent_points": failed, "unproven_points": unknown,
              "declared_partitions": list(declared) if declared is not None else None,
              "unexpected_partitions": unexpected,
              "xbits_partitions": xbits, "partitions": parts, "source": str(folder)}
    identity_path = folder / "proof_identity.json"
    if identity_path.is_file():
        try:
            report["proof_identity"] = _load(identity_path)
        except (OSError, ValueError, Refusal):
            report["proof_identity"] = None
        report["proof_binding_current"] = proof_binding_current(
            folder, report.get("proof_identity") or {})
        if verdict == "PASS" and not report["proof_binding_current"]:
            report.update(verdict="INCONCLUSIVE", explanation="EQY_NATIVE_PROOF_BINDING_STALE")
    refusal = folder / "proof_cache_refusal.json"
    if refusal.is_file():
        report["cache_refusal"] = _load(refusal)
        if report["verdict"] == "PASS":
            report.update(verdict="INCONCLUSIVE", explanation="EQY_CACHE_REUSE_REFUSED")
    if (folder / "config.json").is_file():
        report["config_sha256"] = digest(folder / "config.json")
    return report


def judge_eqy(folder: Path, output: Path) -> dict:
    report = read_eqy(folder)
    write_json(output, report)
    return report


def run_eqy(project: Path, image: str, pdk: str, top: str, rtl: list[Path],
            netlist: Path, *, mounts: Optional[list[tuple[Path, str]]] = None,
            pdk_root: str, std_cell_library: Optional[str] = None,
            namespace: str = "lec_eqy") -> Path:
    """Resolve the PDK config, write EQY_SCRIPT, run Yosys.EQY; return its folder.

    A failing EQY job exits nonzero, which the contract reports as a step
    failure; the folder is still judged, because a counterexample IS the answer.
    """
    if not netlist.is_file() or not rtl or any(not p.is_file() for p in rtl):
        raise Refusal("LL_EQY_INPUT_MISSING", f"{netlist} / {rtl}")
    if Path(namespace).is_absolute() or ".." in Path(namespace).parts:
        raise Refusal("LL_INVALID_NAMESPACE", namespace)
    # The producing config/state snapshots must not be overwritten by a
    # second EQY arm (for example, the post-DFT handoff namespace).
    root = project / "phase3/librelane/lec-eqy-config" / namespace
    root.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {"DESIGN_NAME": top, "PDK": pdk,
                              "VERILOG_FILES": [str(p.resolve()) for p in rtl],
                              "EQY_FORCE_ACCEPT_PDK": True, "meta": {"step": STEP}}
    if std_cell_library:
        config["STD_CELL_LIBRARY"] = std_cell_library
    raw = root / "eqy.json"
    write_json(raw, config)
    probe = resolve_step_config(project, image, raw, root / "eqy.probe.json",
                                mounts=mounts, pdk_root=pdk_root)
    resolved = _load(probe)
    corner = resolved.get("DEFAULT_CORNER") or ""
    liberty = _selected_liberty(resolved)
    script = root / f"{namespace}-{top}.eqy"
    script.write_text(eqy_script(top, rtl, netlist.resolve(), liberty))
    config["EQY_SCRIPT"] = str(script)
    write_json(raw, config)
    write_json(root / "eqy.provenance.json", {
        "VERILOG_FILES": "step 13 gold RTL (phase2/stage1/rtl)",
        "EQY_SCRIPT": "librelane_eqy.eqy_script (no bitwuzla; liberty cell functions)",
        "liberty": f"resolved CELL_LIBS for DEFAULT_CORNER {corner}",
        "EQY_FORCE_ACCEPT_PDK": "required for a PDK other than sky130A"})
    step_config = resolve_step_config(project, image, raw, root / "eqy.resolved.json",
                                      mounts=mounts, pdk_root=pdk_root)
    state = root / "state_in.json"
    write_json(state, {"nl": str(netlist.resolve())})
    # Snapshot BEFORE the tool starts: a caller's digest after EQY finishes
    # cannot identify the bytes EQY read if an input changed during the run.
    effective = _load(step_config)
    if (effective["meta"]["step"] != STEP or effective["DESIGN_NAME"] != top
            or _selected_liberty(effective) != liberty
            or Path(effective["EQY_SCRIPT"]).resolve() != script.resolve()):
        raise Refusal("LL_EQY_EFFECTIVE_CONFIG_CHANGED", str(step_config))
    identity = {"top": top, "project": str(project.resolve()), "image": image,
                "mounts": [[str(host.resolve()), guest] for host, guest in mounts or []],
                "pdk_root": pdk_root, "gate_netlist": _fingerprint(netlist),
                "gold_rtl": [_fingerprint(path) for path in rtl],
                "equivalence_script": _fingerprint(script),
                "resolved_config": _fingerprint(step_config), "initial_state": _fingerprint(state),
                "liberty": _liberty_binding(project, effective, mounts or []),
                "producer_source": _fingerprint(Path(__file__))}
    folder = project / "phase3/librelane" / namespace / "01-yosys-eqy"
    old = _load(folder / "proof_identity.json") if (folder / "proof_identity.json").is_file() else {}
    def refuse_cache(reason: str) -> Path:
        write_json(folder / "proof_cache_refusal.json", {
            "verdict": "NOT_MEASURED", "reason": reason,
            "native_source": str(folder), "producing_liberty": old.get("liberty"),
            "requested_liberty": identity["liberty"]})
        return folder
    previous_receipt = folder / "vibeic_receipt.json"
    previous_hash = digest(previous_receipt) if previous_receipt.is_file() else None
    if previous_hash:
        if not old.get("native_run") or old.get("liberty") != identity["liberty"]:
            return refuse_cache("LL_EQY_CACHE_LIBERTY_UNBOUND_OR_CHANGED")
        if {key: value for key, value in old.items() if key != "native_run"} == identity:
            if not proof_binding_current(folder, old):
                return refuse_cache("LL_EQY_CACHE_NATIVE_CAPTURE_CHANGED")
            (folder / "proof_cache_refusal.json").unlink(missing_ok=True)
            return folder
    try:
        run_chain(project, image, [(STEP, step_config, state)], mounts=mounts,
                  pdk_root=pdk_root, namespace=namespace)
    except Refusal as exc:
        if exc.code != "LL_STEP_FAILED" or not (folder / "scratch").is_dir():
            raise
    if previous_hash and previous_receipt.is_file() and digest(previous_receipt) == previous_hash:
        return refuse_cache("LL_EQY_CACHE_CANNOT_REBIND_OLD_RUN")
    try:
        identity["native_run"] = {"input": _load(folder / "input_fingerprint.json"),
                                  "sha256": _native_snapshot(folder)}
    except (OSError, ValueError, Refusal):
        # An actual tool FAIL can have no successful step receipt. Its native
        # counterexample remains FAIL; missing binding can never earn PASS.
        identity["native_run"] = None
    write_json(folder / "proof_identity.json", identity)
    return folder


def combine(lec: dict, eqy: dict) -> dict:
    """Keep the conclusive arm. A counterexample in either arm is never outvoted."""
    a = str(lec.get("verdict") or "NOT_MEASURED").upper()
    b = str(eqy.get("verdict") or "NOT_MEASURED").upper()
    if {a, b} == {"PASS", "FAIL"}:
        return {"verdict": "FAIL", "reason": "LEC_ARMS_DISAGREE",
                "detail": f"lec_run={a}, eqy={b}: one arm proves, the other finds a "
                          "counterexample — stop and investigate", "arms": {"lec_run": a, "eqy": b}}
    if "FAIL" in (a, b):
        return {"verdict": "FAIL", "reason": "NOT_EQUIVALENT",
                "selected": "lec_run" if a == "FAIL" else "eqy", "arms": {"lec_run": a, "eqy": b}}
    if a == "PASS":
        return {"verdict": "PASS", "selected": "lec_run",
                "corroborated_by_eqy": b == "PASS", "arms": {"lec_run": a, "eqy": b}}
    if b == "PASS":
        # Arm A undecided (budget, SKIPPED-CONDITION, abort) and did not
        # disprove; EQY proved every partition.
        return {"verdict": "PASS", "selected": "eqy", "arms": {"lec_run": a, "eqy": b}}
    return {"verdict": "INCONCLUSIVE", "reason": "NO_CONCLUSIVE_ARM",
            "arms": {"lec_run": a, "eqy": b}}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="Yosys.EQY step directory")
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    report = judge_eqy(args.folder, args.json)
    print(f"[{report['verdict']}] {report['explanation']}")
    return {"PASS": 0, "FAIL": 1}.get(report["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
