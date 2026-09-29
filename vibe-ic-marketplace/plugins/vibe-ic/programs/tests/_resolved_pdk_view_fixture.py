"""Fake ONLY the EDA tool's file writes for `librelane_prelayout.resolve_pdk_view`.

The real resolver path runs: the run-image record is read, the declared PDK
root is used, `librelane_contract.resolve_step_configs` emits the design
config and launches the image's LibreLane resolver through `run_container`.
This fixture stands in for that one container: it writes what the installed
resolver writes (`<step>.json` with `meta.step`, `<step>.views.json`,
`flow_gates.json`) from the values the test declares the PDK resolves to, and
answers the capability probes. Nothing else is stubbed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import librelane_contract as LLC
import librelane_prelayout as LLP

RUN_IMAGE = "ghcr.io/vibeic/vibeic-eda@sha256:" + "ab" * 32


def install(monkeypatch, project: Path, pdk: str, resolved: dict | None,
            *, pdk_root: Path | None = None, record_image: bool = True) -> list:
    """Make `project` a run whose image resolves `pdk` to `resolved`.

    `resolved=None` makes the resolver container fail (rc 1). Returns the list
    of argv the fake container saw."""
    LLP._PDK_VIEWS.clear()
    LLC._CAPABILITY.clear()
    if record_image:
        rec = project / LLP.RUN_IMAGE_RECORD
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"image_ref": RUN_IMAGE, "verdict": "PASS"}))
    docs = project / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    # emit_config reads these three by name (the design's declared inputs).
    for name, body in (("L8_TIMING_WAVEFORM.json", {"clock_domains": [
                            {"name": "clk", "source_pin": "clk", "role": "primary",
                             "period_ns": 25.0}]}),
                       ("L9_INTEGRATION_SPEC.json", {}), ("L19_CONSTRAINTS_PDK.json", {})):
        if not (docs / name).is_file():
            (docs / name).write_text(json.dumps(body))
    decl = project / LLC.DECLARATION_REL
    if not decl.is_file():
        decl.parent.mkdir(parents=True, exist_ok=True)
        decl.write_text(json.dumps({"answers": {}}))
    root = pdk_root or (project.parent / f"{project.name}_pdk_root")
    (root / pdk).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBEIC_LIBRELANE_PDK_ROOT", str(root))
    seen: list = []

    def run_container(argv, **_kw):
        seen.append(list(argv))
        joined = " ".join(map(str, argv))
        if "Chip(config=design" not in joined:
            return subprocess.CompletedProcess(argv, 0, "", "")   # capability probes
        if resolved is None:
            return subprocess.CompletedProcess(argv, 1, "", "resolver failed")
        design, requested, out = argv[-5], argv[-4], Path(argv[-3])
        for step in json.loads(Path(requested).read_text()):
            (out / f"{step}.json").write_text(json.dumps(
                {**resolved, "meta": {"step": step, "librelane_version": "fake"}}))
            (out / f"{step}.views.json").write_text(json.dumps(
                {"step": step, "inputs": [], "outputs": []}))
        (out / "flow_gates.json").write_text("{}")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(LLC, "run_container", run_container)
    return seen
