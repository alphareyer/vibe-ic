#!/usr/bin/env python3
"""Design-for-ECO spare-cell PLAN (flow step 18) -- pure data, no I/O.

One builder for both producers of step 18:

  * the direct OpenROAD deck (`phase3_one_shot_runner._build_spare_cells_plan`
    emits Tcl from the plan), and
  * the LibreLane custom step `Vibeic.InsertSpareCells`
    (`librelane_plugins/librelane_plugin_vibeic`), which inserts the same plan
    into the DetailedPlacement ODB.

Two copies of this arithmetic would be two answers to "how many spares, of
which class, where", and the reserved-instance list the route deck protects is
derived from the plan's names -- so the names must come from one place.

The plan is INTENT. Neither producer may publish it as an outcome; each
measures what it actually inserted (see `_spare_tieoff_measured_from_log` and
the custom step's `spare_cells.json`). chip-AGNOSTIC: a cell library is read
only through its own cell names; no PDK, design or vendor literal.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_SPARE_DENSITY = 0.02      # 2% of placed cells
SPARE_DENSITY_MAX = 0.2           # clamp ceiling (20%)
SPARE_DENSITY_MIN = 0.0
# Canonical spare-cell function-class mix. Each entry is (class, weight):
# a balanced ECO budget needs combinational gates (inverter/nand/nor/
# aoi/oai), a 2:1 mux, and at least one sequential element so a state
# bug can be patched too. Weights sum to 1.0.
SPARE_CELL_MIX = (
    ("inverter", 0.25),
    ("nand2",    0.20),
    ("nor2",     0.15),
    ("mux2",     0.15),
    ("aoi",      0.10),
    ("oai",      0.05),
    ("dff",      0.10),
)


def compute_spare_density(raw) -> Tuple[float, Optional[str]]:
    """Normalize / clamp a --spare-density value to [0.0, 0.2].

    Returns (density_fraction, warning_or_None). Non-numeric / None
    falls back to the 2% default. Values are clamped — a request for
    50% spares is honoured as the 20% ceiling with a warning. Pure
    numeric guard, chip-AGNOSTIC."""
    warn: Optional[str] = None
    if raw is None:
        return DEFAULT_SPARE_DENSITY, None
    try:
        d = float(raw)
    except (TypeError, ValueError):
        return (DEFAULT_SPARE_DENSITY,
                f"--spare-density {raw!r} is not numeric; using default "
                f"{DEFAULT_SPARE_DENSITY}")
    if d != d:  # NaN
        return DEFAULT_SPARE_DENSITY, "--spare-density is NaN; using default"
    if d < SPARE_DENSITY_MIN:
        warn = (f"--spare-density {d:g} < 0 is invalid; clamping to "
                f"{SPARE_DENSITY_MIN}")
        d = SPARE_DENSITY_MIN
    if d > SPARE_DENSITY_MAX:
        warn = (f"--spare-density {d:g} exceeds ceiling "
                f"{SPARE_DENSITY_MAX}; clamping to {SPARE_DENSITY_MAX}")
        d = SPARE_DENSITY_MAX
    return d, warn


def spare_count_from_density(placed_cells: int, density: float) -> int:
    """Number of spare cells to insert for a given placed-cell count and
    density. At least 1 spare when density>0 and there is any placed
    logic (so even a tiny block gets an ECO budget). Uses CEIL (not
    round) so the achieved density (count/placed) always MEETS OR
    EXCEEDS the requested target — `round` could land just under the
    target (e.g. 302*0.02=6.04 -> round 6 -> 6/302=0.0199 < 0.02, which
    would fail the coverage gate). Pure math."""
    if placed_cells <= 0 or density <= 0.0:
        return 0
    scaled = placed_cells * density
    n = int(scaled)
    if scaled > n:
        n += 1
    return max(1, n)


def spare_type_distribution(count: int,
                            mix=SPARE_CELL_MIX) -> Dict[str, int]:
    """Allocate `count` spares across the canonical function-class mix
    by weight. Guarantees the integer allocation sums to `count`
    (largest-remainder rounding) so the emitted JSON `types{}` total
    equals `count`. Pure, chip-AGNOSTIC."""
    if count <= 0:
        return {}
    alloc: Dict[str, float] = {cls: count * w for cls, w in mix}
    floored: Dict[str, int] = {cls: int(v) for cls, v in alloc.items()}
    remaining = count - sum(floored.values())
    rema = sorted(
        ((cls, alloc[cls] - floored[cls]) for cls, _ in mix),
        key=lambda kv: kv[1], reverse=True,
    )
    i = 0
    while remaining > 0 and rema:
        cls = rema[i % len(rema)][0]
        floored[cls] += 1
        remaining -= 1
        i += 1
    return {cls: n for cls, n in floored.items() if n > 0}


def spare_grid_positions(count: int, core_llx: int, core_lly: int,
                         core_urx: int, core_ury: int
                         ) -> List[Tuple[int, int]]:
    """Spread `count` spare instances across a near-square grid over the
    core area so they are DISTRIBUTED (not clustered in one corner).
    Returns a list of (llx, lly) integer micron coordinates. The grid
    is sized ceil(sqrt(count)) per axis; positions are evenly spaced
    inside the core with a small inset. Pure geometry, chip-AGNOSTIC."""
    if count <= 0:
        return []
    w = max(1, core_urx - core_llx)
    h = max(1, core_ury - core_lly)
    cols = max(1, int(math.ceil(math.sqrt(count))))
    rows = max(1, int(math.ceil(count / cols)))
    out: List[Tuple[int, int]] = []
    inset_x = max(1, w // 20)
    inset_y = max(1, h // 20)
    usable_w = max(1, w - 2 * inset_x)
    usable_h = max(1, h - 2 * inset_y)
    for idx in range(count):
        r = idx // cols
        c = idx % cols
        x = core_llx + inset_x + (usable_w * c) // max(1, cols)
        y = core_lly + inset_y + (usable_h * r) // max(1, rows)
        out.append((int(x), int(y)))
    return out


# Per-class name-token patterns, matched against a library's own cell NAMES
# (a Liberty `cell(...)` list, or the masters an ODB carries -- the same
# names). Every cell library names cells with these function tokens.
_CLASS_PATTERNS = {
    "inverter": re.compile(r"(?:^|_)(?:inv|clkinv)_?\w*$", re.I),
    "nand2":    re.compile(r"(?:^|_)nand2\w*$", re.I),
    "nor2":     re.compile(r"(?:^|_)nor2\w*$", re.I),
    # A 2:1 mux is named `mux2*` (sky130/Nangate), `mx2*` / `mxi2*`
    # (inverting) in Artisan-style commercial libraries, or `muxi2*`.
    "mux2":     re.compile(r"(?:^|_)m(?:ux|x)i?2\w*$", re.I),
    # AOI / OAI cells carry an AND-OR / OR-AND topology prefix in every real
    # library (sky130 `a21oi`/`a221oi`, Nangate `AOI21`), not a literal
    # `aoi`/`oai`. Match the topology-digit form plus the literal.
    "aoi":      re.compile(r"(?:^|_)(?:a\d+oi|aoi)\w*$", re.I),
    "oai":      re.compile(r"(?:^|_)(?:o\d+ai|oai)\w*$", re.I),
    "dff":      re.compile(r"(?:^|_)(?:dff|dfxtp|dfrtp|sdff)\w*$", re.I),
}
# SECOND-TIER patterns, consulted ONLY when the primary pattern for a class
# matches nothing in this library (a broad form run first would change which
# cell a library the primary already handles resolves to -- measured: a
# generic `d(ff|f<letters>)` form flips sky130 from dfrtp_2 to dfbbn_1). The
# `dff` primary list matched NO flip-flop in IHP SG13G2 (sg13g2_dfrbp_*), the
# class was dropped and the coverage gate failed 0.019886 < 0.02 (spm x
# ihp-sg13g2, 2026-07-21). Tier 2 still rejects latches (dl*) and delays.
_CLASS_PATTERNS_TIER2 = {
    "dff": re.compile(r"(?:^|_)s?d(?:ff|f[a-z]{1,6})\w*$", re.I),
}


def discover_spare_cells(cell_names: Iterable[str],
                         used_cells: Optional[set] = None
                         ) -> Dict[str, Optional[str]]:
    """Map each canonical spare function-class to a concrete cell name.

    Prefers the first matching variant NOT in ``used_cells`` (the masters the
    design itself instantiates): a spare whose class is also in functional use
    defeats the spare-only-class LVS ignore (ORGANIC #563 round-2). Falls back
    to the first match when every variant is in use; a class with no match
    maps to None and the caller drops it from the mix."""
    out: Dict[str, Optional[str]] = {cls: None for cls, _ in SPARE_CELL_MIX}
    cells_sorted = sorted(set(cell_names), key=lambda n: (len(n), n))
    if not cells_sorted:
        return out
    used = used_cells or set()
    for cls, pat in _CLASS_PATTERNS.items():
        for _pat in (pat, _CLASS_PATTERNS_TIER2.get(cls)):
            if _pat is None:
                continue
            first_match: Optional[str] = None
            for nm in cells_sorted:
                if not _pat.search(nm):
                    continue
                if first_match is None:
                    first_match = nm
                if nm not in used:
                    out[cls] = nm
                    break
            if out[cls] is None and first_match is not None:
                out[cls] = first_match
            if out[cls] is not None:
                break  # primary tier resolved it; never consult tier 2
    return out


def build_spare_cells_plan(placed_cells: int, density: float,
                           core_box: Tuple[int, int, int, int],
                           cell_map: Dict[str, Optional[str]],
                           has_pad_ring: bool = False,
                           used_cells: Optional[set] = None
                           ) -> Dict[str, Any]:
    """Assemble the full spare-cell insertion plan (pure data — no IO).

    Returns the dict serialised to `spare_cells.json`:
      {count, density, types{class:n}, tied_off, instances:[...],
       spare_pads, cell_map{class:cell}}.

    Each instance carries name / type(class) / concrete `cell` / llx / lly /
    keep:true. Names are deterministic `spare_<class>_<idx>`. A class whose
    `cell_map` entry is None is dropped (an instance with no cell is never
    inserted, so it must not be planned as a preserved spare). An empty
    `cell_map` means no library was consulted and nothing is dropped."""
    count = spare_count_from_density(placed_cells, density)
    dist = spare_type_distribution(count)
    llx, lly, urx, ury = core_box
    positions = spare_grid_positions(count, llx, lly, urx, ury)
    instances: List[Dict[str, Any]] = []
    pos_i = 0
    per_class_idx: Dict[str, int] = {}
    flat_classes: List[str] = []
    for cls, n in dist.items():
        flat_classes.extend([cls] * n)
    dropped_classes: Dict[str, int] = {}
    for cls in flat_classes:
        concrete = cell_map.get(cls) if cell_map else None
        if cell_map and concrete is None:
            dropped_classes[cls] = dropped_classes.get(cls, 0) + 1
            continue
        idx = per_class_idx.get(cls, 0)
        per_class_idx[cls] = idx + 1
        x, y = positions[pos_i] if pos_i < len(positions) else (llx, lly)
        pos_i += 1
        instances.append({"name": f"spare_{cls}_{idx}", "type": cls,
                          "cell": concrete, "llx": x, "lly": y, "keep": True})
    # Reserve spare/ECO IO pads when a pad ring exists (one input-class, one
    # output-class — a minimal ECO IO budget).
    spare_pads: List[Dict[str, Any]] = []
    if has_pad_ring:
        spare_pads = [
            {"name": "spare_pad_in_0", "kind": "input", "keep": True},
            {"name": "spare_pad_out_0", "kind": "output", "keep": True},
        ]
    eff_types: Dict[str, int] = {}
    for inst in instances:
        eff_types[inst["type"]] = eff_types.get(inst["type"], 0) + 1
    # The sign-off audit reads `rows[]`: spare instances grouped by their lly
    # (row y-origin), per-row occupancy. Derived, never a placement change.
    rows: List[Dict[str, Any]] = []
    by_lly: Dict[Any, List[Dict[str, Any]]] = {}
    for inst in instances:
        by_lly.setdefault(inst.get("lly"), []).append(inst)
    for row_idx, row_y in enumerate(sorted(
            by_lly.keys(), key=lambda v: (v is None, v))):
        members = by_lly[row_y]
        xs = [m.get("llx") for m in members if m.get("llx") is not None]
        rows.append({"row": row_idx, "lly": row_y,
                     "spare_count": len(members),
                     "min_llx": min(xs) if xs else None,
                     "max_llx": max(xs) if xs else None,
                     "instances": [m["name"] for m in members]})
    used = used_cells or set()
    class_conflicts = sorted({inst["cell"] for inst in instances
                              if inst.get("cell") and inst["cell"] in used})
    plan: Dict[str, Any] = {
        "count": len(instances),
        "density": round(density, 6),
        "types": eff_types,
        # A CLAIM about the physical netlist, set by the producer once it
        # MEASURES the tie-off (#563 r2); never True by construction.
        "tied_off": False,
        "instances": instances,
        "rows": rows,
        "spare_pads": spare_pads,
        "cell_map": cell_map,
    }
    if class_conflicts:
        plan["class_conflicts"] = class_conflicts
    if dropped_classes:
        plan["dropped_classes_no_pdk_cell"] = dropped_classes
        plan["requested_count"] = count
    return plan
