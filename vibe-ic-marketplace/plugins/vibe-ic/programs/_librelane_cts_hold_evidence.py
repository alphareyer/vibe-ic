#!/usr/bin/env python3
"""_librelane_cts_hold_evidence.py — what steps 19/20 measured on LibreLane.

When `phase3/librelane_switch.json` selects LibreLane (or `dual`) for steps 19
(CTS) and 20 (post-CTS hold repair), `phase3_one_shot_runner` runs
`OpenROAD.CTS` -> `Vibeic.ClockPathDriveSizing` -> `OpenROAD.ResizerTimingPostCTS`
and then `OpenROAD.STAMidPNR` once per STA corner, and writes one receipt,
`reports/phase3/librelane_cts_hold_handoff.json`, binding every handed-over
view (post_cts.def, post_hold.def, post_hold.odb, cts/clock_tree.rpt) and the
State whose metrics measured the selected arm, by sha256.

The step gates (`cts_quality_check`, `hold_closure_check`,
`def_stage_progression_check`, `hold_area_budget_check`) read the TOOL's
structured metrics through this one module instead of a report the runner
relabelled for them (review70 step 20: "Read the tool's structured metrics
instead of making the tool print a gate-shaped sentence").

`evidence(project)` returns None when neither step is switched (the direct
path's own evidence then applies, unchanged). When a step IS switched the
receipt is required: an absent, unreadable or stale receipt is reported as
such, never read as "no LibreLane evidence".

chip-AGNOSTIC: metric keys are LibreLane's own (`timing__hold__ws__corner:*`,
`clock__skew__worst_*__corner:*`, `design__instance__count__hold_buffer`);
no design, PDK or corner literal.
"""
from __future__ import annotations

import hashlib
import json
import os as _os
import sys as _sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

RECEIPT_REL = "reports/phase3/librelane_cts_hold_handoff.json"
STEPS = ("19", "20")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def modes(project: Path) -> Dict[str, str]:
    """The contract's selection for 19 and 20 (`direct` when unreadable is
    NOT assumed: an invalid switch raises, as `selected_mode` does)."""
    import librelane_contract as _ll
    return {step: _ll.selected_mode(project, step) for step in STEPS}


def evidence(project: Path) -> Optional[Dict[str, Any]]:
    """None when both steps are direct; else a dict with ``problem`` (str or
    None), the receipt, and the measured metrics of the selected arm."""
    selected = modes(project)
    if set(selected.values()) == {"direct"}:
        return None
    out: Dict[str, Any] = {"modes": selected, "problem": None,
                           "receipt_path": RECEIPT_REL}
    path = project / RECEIPT_REL
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        out["problem"] = f"RECEIPT_UNREADABLE: {RECEIPT_REL}: {exc}"
        return out
    out["receipt"] = receipt
    stale: List[str] = []
    for name, view in (receipt.get("views") or {}).items():
        dest = project / str(view.get("dest", ""))
        if not dest.is_file() or _sha(dest) != view.get("dest_sha256"):
            stale.append(f"{name} ({view.get('dest')})")
    if stale:
        out["problem"] = ("HANDOFF_STALE: these files no longer carry the bytes "
                          f"LibreLane handed over: {', '.join(stale)}")
        return out
    measured = project / str(receipt.get("measured_state", ""))
    try:
        state = json.loads(measured.read_text())
        if _sha(measured) != receipt.get("measured_state_sha256"):
            raise ValueError("sha256 differs from the receipt")
    except (OSError, ValueError) as exc:
        out["problem"] = f"MEASURED_STATE_UNREADABLE: {measured}: {exc}"
        return out
    out["metrics"] = state.get("metrics") or {}
    out["corners"] = list(receipt.get("corners") or [])
    out["selected"] = receipt.get("selected")
    return out


def per_corner(ev: Dict[str, Any], key: str) -> Dict[str, Optional[float]]:
    """``{corner: value}`` for ``<key>__corner:<corner>``; None = not measured."""
    metrics = ev.get("metrics") or {}
    result: Dict[str, Optional[float]] = {}
    for corner in ev.get("corners") or []:
        value = metrics.get(f"{key}__corner:{corner}")
        result[corner] = (float(value) if isinstance(value, (int, float))
                          and not isinstance(value, bool) else None)
    return result


def hold_verdict(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Per-corner hold worst slack from the tool: every corner measured and
    >= 0 is clean. Returns ``{"clean": bool|None, "worst": .., "by_corner":
    .., "unmeasured": [...], "violating": [...]}``; ``clean`` is None when any
    corner is unmeasured (or none is declared)."""
    by_corner = per_corner(ev, "timing__hold__ws")
    unmeasured = [c for c, v in by_corner.items() if v is None]
    violating = [c for c, v in by_corner.items() if v is not None and v < 0]
    values = [v for v in by_corner.values() if v is not None]
    clean: Optional[bool]
    if not by_corner or unmeasured:
        clean = None
    else:
        clean = not violating
    return {"clean": clean, "worst": min(values) if values else None,
            "by_corner": by_corner, "unmeasured": unmeasured,
            "violating": violating,
            "hold_buffers": (ev.get("metrics") or {}).get(
                "design__instance__count__hold_buffer")}


def worst_skew(ev: Dict[str, Any]) -> Optional[float]:
    """The largest |clock skew| over every corner and both checks (ns)."""
    values = [abs(v) for key in ("clock__skew__worst_setup", "clock__skew__worst_hold")
              for v in per_corner(ev, key).values() if v is not None]
    return max(values) if values else None


def clock_tree_fanout(project: Path, ev: Dict[str, Any]) -> Dict[str, Any]:
    """The tool-measured clock-tree fanout (`Vibeic.ClockPathDriveSizing`'s
    `vibeic__cts__max_fanout`) and the cap the CTS step was configured with
    (`MAX_FANOUT_CONSTRAINT` in its own resolved config.json). Either may be
    None; the caller decides what an absent one means."""
    metrics = ev.get("metrics") or {}
    value = metrics.get("vibeic__cts__max_fanout")
    cap = None
    folder = ((ev.get("receipt") or {}).get("chain") or {}).get("OpenROAD.CTS")
    if folder:
        try:
            cap = json.loads((project / folder / "config.json").read_text()).get(
                "MAX_FANOUT_CONSTRAINT")
        except (OSError, ValueError):
            cap = None
    return {"max_fanout": value if isinstance(value, int) else None,
            "cap": cap if isinstance(cap, (int, float)) else None,
            "created": metrics.get("vibeic__cts__created_instance__count")}

