"""First-pass PDN strap pitch from declared current and technology limits.

This is a sizing candidate. OpenROAD's generated DEF and PSM IR/EM reports
remain the authority for connectivity and sign-off. A missing input leaves the
PDK pitch intact; no number is borrowed from a prior design or run.
"""
from __future__ import annotations

import math
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


def plan_for_core(project: Path, tech_lef_text: str | None,
                  core_side_um: float, detail: Mapping[str, Any],
                  current_A: float | None, current_source: str | None,
                  nominal_voltage_v: float | None,
                  ir_budget_pct: float | None) -> dict[str, Any]:
    """Resolve the design switch and PDK limits, then size this core."""
    policy = project / "input/pdn_budget_pitch_policy.json"
    try:
        mode = json.loads(policy.read_text()).get("mode") if policy.is_file() else "advisory"
    except (OSError, ValueError, AttributeError):
        mode = "invalid"
    if mode not in ("advisory", "apply"):
        return {"verdict": "NOT_MEASURED", "reason": "invalid pdn_budget_pitch_policy mode",
                "mode": mode, "override": {}}
    if current_A is None or not tech_lef_text:
        return {"verdict": "NOT_MEASURED", "reason":
                "declared current or technology LEF absent", "mode": mode,
                "override": {}}
    from em_current_density_check import parse_lef_jmax
    table = parse_lef_jmax(tech_lef_text)
    jmax = {key: float(row["jmax_per_width_A_per_um"])
            for key, row in table.items() if row.get("jmax_per_width_A_per_um")}
    sheet = {}
    spaces = {}
    for st in detail.get("stripes") or []:
        layer = str(st.get("layer") or "")
        match = re.search(r"^\s*LAYER\s+" + re.escape(layer)
                          + r"\s*;?\s*$(.*?)^\s*END\s+" + re.escape(layer)
                          + r"\b", tech_lef_text, re.M | re.S | re.I)
        body = re.sub(r"#[^\n]*", "", match.group(1)) if match else ""
        resistance = re.search(r"^\s*RESISTANCE\s+RPERSQ\s+([0-9.eE+-]+)\s*;",
                               body, re.M | re.I)
        spacing = re.search(r"^\s*SPACING\s+([0-9.eE+-]+)\s*;",
                            body, re.M | re.I)
        if resistance:
            sheet[layer.lower()] = float(resistance.group(1))
        spaces[layer.lower()] = float(spacing.group(1)) if spacing else 0.0
    ir_v = (float(ir_budget_pct) * nominal_voltage_v / 100.0
            if isinstance(ir_budget_pct, (int, float)) and nominal_voltage_v
            and ir_budget_pct > 0 else None)
    grid_match = re.search(r"^\s*MANUFACTURINGGRID\s+([0-9.eE+-]+)\s*;",
                           tech_lef_text, re.M | re.I)
    grid = float(grid_match.group(1)) if grid_match else 0.0
    candidate = plan(
        detail.get("stripes") or [], core_w_um=core_side_um,
        core_h_um=core_side_um, current_A=current_A, jmax_A_per_um=jmax,
        ir_budget_v=ir_v, sheet_ohm_per_sq=sheet, spacing_um=spaces,
        directions=detail.get("directions"),
        site_dims_um=detail.get("site_dims_um"), grid_um=grid,
        routing_fraction_max=float(detail.get("routing_budget") or 0.5))
    return dict(candidate, mode=mode, current_source=current_source,
                ir_budget_source=("pdk.ir_budget_pct" if ir_v is not None else None),
                design_input=str(policy.relative_to(project)) if policy.is_file() else None)


def plan(straps: Sequence[Mapping[str, Any]], *, core_w_um: float,
         core_h_um: float, current_A: float | None,
         jmax_A_per_um: Mapping[str, float],
         ir_budget_v: float | None = None,
         sheet_ohm_per_sq: Mapping[str, float] | None = None,
         spacing_um: Mapping[str, float] | None = None,
         directions: Mapping[str, str] | None = None,
         site_dims_um: Sequence[float] | None = None,
         grid_um: float = 0.0, routing_fraction_max: float = 0.5
         ) -> dict[str, Any]:
    """Size each two-rail strap group; retain the PDK pitch if it already fits.

    EM count is ceil(I / (width * Jmax)); the optional IR count uses a simple
    parallel-strip resistance estimate I*Rs*L/(width*N) <= drop budget. Both
    are initial candidates only because current distribution is not known
    before placement. A real PSM run must establish the resulting IR/EM.
    """
    if (not isinstance(current_A, (int, float)) or isinstance(current_A, bool)
            or not math.isfinite(current_A) or current_A <= 0 or not straps):
        return {"verdict": "NOT_MEASURED", "reason": "declared current or straps absent",
                "override": {}}
    if not 0 < routing_fraction_max <= 1:
        return {"verdict": "NOT_MEASURED", "reason": "routing budget invalid",
                "override": {}}
    sw, sh = (list(site_dims_um)[:2] if site_dims_um and len(site_dims_um) >= 2
              else (0.0, 0.0))
    ext_w = math.floor(core_w_um / sw + 1e-9) * sw if sw > 0 else core_w_um
    ext_h = math.floor(core_h_um / sh + 1e-9) * sh if sh > 0 else core_h_um
    overrides: dict[str, dict[str, float]] = {}
    rows = []
    for st in straps:
        layer = str(st.get("layer") or "")
        key = layer.lower()
        try:
            w, p, off = (float(st["width"]), float(st["pitch"]),
                         float(st.get("offset") or 0.0))
            j = float(jmax_A_per_um[key])
            space = float((spacing_um or {}).get(key, 0.0))
        except (KeyError, TypeError, ValueError):
            return {"verdict": "NOT_MEASURED", "reason": f"{layer}: width/pitch/Jmax absent",
                    "override": {}}
        if min(w, p, j) <= 0 or space < 0 or off < 0:
            return {"verdict": "NOT_MEASURED", "reason": f"{layer}: invalid technology geometry",
                    "override": {}}
        direction = str((directions or {}).get(layer, "")).upper()
        extent = (ext_w if direction == "VERTICAL" else ext_h
                  if direction == "HORIZONTAL" else min(ext_w, ext_h))
        # The N4 auto-growth floor reserves one row/site snapping step (or
        # 5 % when the site is unknown). Leave that reserve inside the target
        # core so the candidate does not ask auto sizing to undo its benefit.
        reserve = (sw if direction == "VERTICAL" else sh
                   if direction == "HORIZONTAL" else max(sw, sh))
        usable = extent - reserve if reserve > 0 else extent / 1.05
        em_groups = max(1, math.ceil(current_A / (w * j) - 1e-12))
        ir_groups = 1
        resistance = (sheet_ohm_per_sq or {}).get(key)
        if ir_budget_v is not None:
            if (not isinstance(resistance, (int, float)) or resistance <= 0
                    or not isinstance(ir_budget_v, (int, float))
                    or ir_budget_v <= 0):
                return {"verdict": "NOT_MEASURED", "reason": f"{layer}: IR sheet resistance/budget absent",
                        "override": {}}
            ir_groups = max(1, math.ceil(
                current_A * float(resistance) * extent / (w * ir_budget_v) - 1e-12))
        groups = max(em_groups, ir_groups)
        max_pitch = (usable - off - w) / (groups - 0.5)
        candidate = min(p, max_pitch)
        if grid_um > 0:
            candidate = math.floor(candidate / grid_um + 1e-9) * grid_um
        candidate = round(candidate, 6)
        min_pitch = max(2 * (w + space), 2 * w / routing_fraction_max)
        if candidate + 1e-9 < min_pitch:
            return {"verdict": "INFEASIBLE", "reason":
                    f"{layer}: {groups} groups need pitch <= {max_pitch:g} um but legal pitch >= {min_pitch:g} um",
                    "required_groups": groups, "override": {}}
        overrides[layer] = {"width": w, "pitch": candidate, "offset": off}
        rows.append({"layer": layer, "required_groups": groups,
                     "em_groups": em_groups, "ir_groups": ir_groups,
                     "pdk_pitch_um": p, "chosen_pitch_um": candidate,
                     "core_extent_um": extent,
                     "small_core": off + w + (groups - 0.5) * p > extent + 1e-9})
    return {"verdict": "CANDIDATE", "basis": "declared current / LEF Jmax and optional IR sheet resistance",
            "current_A": current_A, "ir_budget_v": ir_budget_v,
            "rows": rows, "override": overrides}
