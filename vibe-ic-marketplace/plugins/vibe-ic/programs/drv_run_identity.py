#!/usr/bin/env python3
"""The run identity an in-flow DRV capture binds (ruling R-0929-DRV-IDENTITY).

An in-flow capture (step 23, step 32, pre-stream) can bind only identity that
exists at that point: the digests of the design state it judges (the plan
records those), the run's code identity recorded when the run started, and
the specification version.  This module holds the last two:

* :func:`record` is called when a Phase-3 run starts.  It writes
  ``reports/phase3/drv_run_identity.json``: a fresh run id, the plugin's git
  commit (the flow's existing source identity, ``_plugin_source_sha``'s
  answer) and a content digest of the installed plugin tree, which exists
  whether or not the plugin is git-backed.
* :func:`spec_version` is the content digest of the Phase-1 documents the run
  implements (``phase1/generated_docs``).  Absent documents give None, which
  the judge reports NOT_MEASURED.

GDS and LVS-netlist identity are not here: they exist only after stream-out
and are bound by the final post-stream capture.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _atomic_artefact import write_text  # noqa: E402

RECORD = Path("reports/phase3/drv_run_identity.json")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SPEC_DOCS = Path("phase1/generated_docs")


def _tree_digest(root: Path) -> str:
    """sha256 over every file's relative path and bytes (caches excluded)."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        h.update(str(rel).encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()


def _git_commit(root: Path) -> Optional[str]:
    try:
        cp = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (cp.stdout or "").strip()
    return sha if cp.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def record(project: Path, *, plugin_root: Path = PLUGIN_ROOT) -> dict:
    """Record this run's identity at run start (replaces any earlier run's)."""
    doc = {"run_id": uuid.uuid4().hex,
           "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
           "plugin_source_commit": _git_commit(plugin_root),
           "plugin_tree_sha256": _tree_digest(plugin_root),
           "plugin_root": str(plugin_root),
           "spec_version": spec_version(project)}
    write_text(project / RECORD, json.dumps(doc, indent=2) + "\n")
    return doc


def _physical_spec_continuation(project: Path, previous: dict, step_ids) -> bool:
    """Rebuild from Floorplan after a downstream-only declared setting change.

    The original D1 snapshots must prove the complete old specification. The
    normal emitter and the measured Yosys config own the setting projection;
    a self-consistent old receipt alone cannot prove current design intent.
    """
    if not step_ids or str(step_ids[0]) != "15":
        return False
    import librelane_contract as ll
    import step_output_collector as versions
    import synth_handoff_netlist_check as handoff
    import _phase1_producer_identity as p1

    try:
        original = versions.latest_pass(project, "D1")
        if (not original or not original.get("canonical_pass")
                or not original.get("current_inputs") or original.get("capture_errors")):
            return False
        folder = project / "steps" / original["folder"]
        entries = [r for r in original["outputs"]
                   if Path(r["rel"]).parent == SPEC_DOCS]
        current = {p.name: p for p in (project / SPEC_DOCS).glob("*.json") if p.is_file()}
        if not entries or {Path(r["rel"]).name for r in entries} != set(current):
            return False
        old_docs = {}
        digest = hashlib.sha256()
        for row in sorted(entries, key=lambda r: Path(r["rel"]).name):
            name = Path(row["rel"]).name
            data = (folder / row["snapshot"]).read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != row["sha256"] or name in old_docs:
                return False
            digest.update(name.encode() + b"\0" + sha.encode() + b"\n")
            old_docs[name] = data
            if name != "L19_CONSTRAINTS_PDK.json" and current[name].read_bytes() != data:
                return False
        if "phase1-docs-sha256:" + digest.hexdigest() != previous.get(
                "initial_spec_version", previous["spec_version"]):
            return False
        name = "L19_CONSTRAINTS_PDK.json"
        before = json.loads(old_docs[name])
        after = json.loads(current[name].read_bytes())
        old_rows = before["fields"].get("constraint_declarations", [])
        new_rows = after["fields"].get("constraint_declarations", [])
        for doc in (before, after):
            doc.pop("_generator", None)
            doc["fields"].pop("constraint_declarations", None)
        if before != after or not isinstance(old_rows, list) or not isinstance(new_rows, list):
            return False
        # A normal L-doc derivation preserves the old producer; it does not
        # authorize an unrecorded edit to a generated document.
        authority = p1._si.read_sidecar(p1.sidecar_dir(project), p1.KIND) or {}
        old_sha = hashlib.sha256(old_docs[name]).hexdigest()
        current_sha = ll.digest(current[name])
        linked = old_sha
        for change in authority.get("derivations", []):
            if change.get("doc") == name and change.get("from") == linked:
                linked = change.get("to")
        if linked != current_sha or authority.get("outputs", {}).get(name) != current_sha:
            return False
        binding = json.loads((project / "phase2/stage2/synth/synth_inputs.json").read_text())["librelane_synthesis"]
        native = project / binding["folder"]
        receipt = ll.validate_step_receipt(native, "Yosys.Synthesis")
        if (receipt["sha256"].get("config.json") != ll.digest(native / "config.json")
                or handoff.bound_handoff(project).get("verdict") != "PASS"):
            return False
        config = json.loads((native / "config.json").read_text())
        pdk, _ = ll.phase2_pdk(project)
        if config.get("PDK") != pdk:
            return False
        old_settings, _ = ll._declared_flow_settings(
            {"fields": {"constraint_declarations": old_rows}}, pdk)
        settings, _ = ll._declared_flow_settings(
            {"fields": {"constraint_declarations": new_rows}}, pdk)
        for key in (set(old_settings) | set(settings)) & set(config):
            if old_settings.get(key) != settings.get(key) or settings.get(key) != config[key]:
                return False
        changed = ([r for r in old_rows if r not in new_rows]
                   + [r for r in new_rows if r not in old_rows])
        if not changed:
            return False
        for row in changed:
            owned, _ = ll._declared_flow_settings(
                {"fields": {"constraint_declarations": [row]}}, pdk)
            if not owned or set(owned) & set(config):
                return False  # unknown/default/other-scope is not downstream proof
        # The complete preceding acceptances retain the actual RTL, deck,
        # selected PDK/corner and mapped outputs, not just a spec filename.
        accepted = {str(r["id"]): r for r in versions.current_acceptance(project, 0)}
        for sid in ("7", "9", "10", "13", "14"):
            row = accepted.get(sid) or {}
            if (row.get("status") != "PASS" or not row.get("canonical_pass")
                    or row.get("capture_errors") or not row.get("inputs") or not row.get("outputs")):
                return False
            for rel, expected in row["inputs"].items():
                sha = expected.get("sha256") if isinstance(expected, dict) else expected
                if not sha or ll.digest(project / rel) != sha:
                    return False
            for output in row["outputs"]:
                if not output.get("sha256") or ll.digest(project / output["rel"]) != output["sha256"]:
                    return False
        for kind in ("state_files", "config_files"):
            if any(ll.digest(Path(path)) != sha for path, sha in receipt["input"].get(kind, {}).items()):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ll.Refusal):
        return False


def start(project: Path, *, continue_steps=None) -> dict:
    """Start a fresh run, or continue one local Default run's checkpoints.

    A fresh run claims its own receipts. An explicit Default continuation
    retains the logical run ID and preceding tool evidence, records the current
    code identity, and withdraws the selected and downstream stage receipts.
    Original start time and code identity remain available for provenance.
    """
    import drv_stage_receipts
    if continue_steps is not None:
        previous = load(project)
        run_id = previous.get("run_id")
        if (isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id)
                and drv_stage_receipts.current_run(project) == run_id):
            # Use the same consumer that will judge these receipts. A directory
            # claim or a 'not run' row alone is not a completed checkpoint.
            import drv_capture_plan
            rows, _ = drv_capture_plan._stages(project, True)
            # Step 9 keeps ABC's executed script before Step 15's SDC exists.
            # Its census is completed at Step 15, so that mapping checkpoint
            # must survive even though there is not yet a synth-stage row.
            abc = drv_stage_receipts.abc_script_path(project)
            mapped = abc.is_file() and abc.stat().st_size > 0
            if mapped or any(row.get("ran") for row in rows):
                current_spec = spec_version(project)
                if (previous.get("spec_version") is not None
                        and previous["spec_version"] != current_spec
                        and not _physical_spec_continuation(project, previous, continue_steps)):
                    raise ValueError("Default checkpoint specification changed; "
                                     "preceding stages cannot be reused")
                doc = dict(previous)
                doc.setdefault("initial_spec_version", previous.get("spec_version"))
                doc.setdefault("initial_code_identity", {k: previous.get(k) for k in (
                    "plugin_source_commit", "plugin_tree_sha256", "recorded_at")})
                doc.update(plugin_source_commit=_git_commit(PLUGIN_ROOT),
                           plugin_tree_sha256=_tree_digest(PLUGIN_ROOT),
                           plugin_root=str(PLUGIN_ROOT), spec_version=current_spec,
                           continued_at=_dt.datetime.now(_dt.timezone.utc).isoformat())
                drv_stage_receipts.invalidate_from_steps(project, continue_steps)
                write_text(project / RECORD, json.dumps(doc, indent=2) + "\n")
                return doc
    doc = record(project)
    drv_stage_receipts.claim(project)
    return doc


def load(project: Path) -> dict:
    try:
        doc = json.loads((project / RECORD).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def spec_version(project: Path) -> Optional[str]:
    """Content digest of the Phase-1 documents the run implements."""
    root = project / SPEC_DOCS
    files = sorted(p for p in root.glob("*.json") if p.is_file()) if root.is_dir() else []
    if not files:
        return None
    h = hashlib.sha256()
    for path in files:
        h.update(path.name.encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return "phase1-docs-sha256:" + h.hexdigest()
