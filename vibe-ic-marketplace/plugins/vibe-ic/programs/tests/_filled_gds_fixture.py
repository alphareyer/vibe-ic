"""Shared fixture: a streamed (filled) GDS and its per-layer density
measurement bound by sha256 — the evidence Step 34 judges since U14.

Shape copied, minimized, from a real IC-path run's
reports/phase3/metal_density.json (the runner's KLayout recipe output): per-layer
fractions under `layers`, the measured GDS under `gds` (project-relative) and the
PDK under `pdk`. `gds_sha256` is the binding the producer now records. The GDS
bytes here are an opaque stand-in: the gate hashes them, it never parses them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

GDS_REL = "phase3/stage4/gds/top.gds"
DENSITY_REL = "reports/phase3/metal_density.json"
# The real spm IC run's measured layers (all above a 0.30 whole-die floor).
IN_WINDOW = {"metal1": 0.438939, "metal2": 0.386965, "metal3": 0.362177,
             "metal4": 0.359382, "metal5": 0.35929}


def write_filled_gds(project: Path, layers: Optional[Dict[str, float]] = None,
                     *, bind: bool = True, gds_bytes: bytes = b"GDSII-filled",
                     measured_rel: str = GDS_REL,
                     pdk: Optional[str] = None) -> Path:
    """Write the shipped GDS and a metal_density.json that measured
    `measured_rel`. `bind=False` omits gds_sha256 (the pre-U14 producer)."""
    gds = project / GDS_REL
    gds.parent.mkdir(parents=True, exist_ok=True)
    gds.write_bytes(gds_bytes)
    measured = project / measured_rel
    if not measured.is_file():
        measured.parent.mkdir(parents=True, exist_ok=True)
        measured.write_bytes(gds_bytes)
    doc = {"tool": "klayout",
           "measurement": "per_layer_drawn_area_over_die_bbox_area",
           "gds": measured_rel,
           "layers": dict(IN_WINDOW if layers is None else layers)}
    if pdk:
        doc["pdk"] = pdk
    if bind:
        doc["gds_sha256"] = hashlib.sha256(measured.read_bytes()).hexdigest()
    rpt = project / DENSITY_REL
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(json.dumps(doc))
    return rpt
