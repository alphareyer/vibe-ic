"""v0.1.50 — STA endpoint triage (Pattern-B → program).

Doctrine: `skills/sta-review/SKILL.md` enumerates STA-violation
categories with fix proposals. All deterministic given a STA report,
per-path delay breakdown, and any source-bound I/O electrical boundary.

Categories:
  constraint_boundary_limited    → repair contradictory I/O constraints first
  constraint_boundary_unresolved → obtain a current drive/load model first
  cell_delay_limited  → upsize driver / Vt swap
  net_delay_limited   → insert buffer / reroute / shorten wire
  logic_depth_limited → pipeline / restructure RTL
  clock_skew_limited  → useful skew / CTS re-balance
  hold_violation      → buffer insertion on min paths
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Measured
# on the base tree: 454 of the 1385 top-level programs died that way. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------


import argparse
import json
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import plugin_manifest_discovery as _pmd  # noqa: E402  (#800 ONE version reader)


CATEGORIES = (
    "constraint_boundary_limited",
    "constraint_boundary_unresolved",
    "cell_delay_limited",
    "net_delay_limited",
    "logic_depth_limited",
    "clock_skew_limited",
    "hold_violation",
)

FIX_STRATEGY: Dict[str, str] = {
    "constraint_boundary_limited": "resolve the I/O drive/load boundary before RTL or ECO repair",
    "constraint_boundary_unresolved": "provide a current source-bound I/O electrical model before repair",
    "cell_delay_limited":  "upsize driver cells; Vt swap (LVT)",
    "net_delay_limited":   "insert buffer; reroute or shorten wire",
    "logic_depth_limited": "pipeline path; restructure RTL",
    "clock_skew_limited":  "useful skew; CTS re-balance",
    "hold_violation":      "buffer insertion on min path; delay cell",
}


@dataclass
class EndpointFinding:
    endpoint: str
    slack_ns: float
    category: str
    fix_strategy: str
    cell_delay_pct: Optional[float] = None
    net_delay_pct: Optional[float] = None
    logic_depth: Optional[int] = None
    boundary_role: Optional[str] = None
    boundary_state: Optional[str] = None
    boundary_source_sha256: Optional[str] = None
    boundary_deck_sha256: Optional[str] = None


@dataclass
class StaReport:
    findings: List[EndpointFinding]
    wns_ns: Optional[float]
    tns_ns: Optional[float]
    counts_by_category: Dict[str, int]
    total_violations: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "findings": [asdict(f) for f in self.findings],
            "wns_ns": self.wns_ns,
            "tns_ns": self.tns_ns,
            "counts_by_category": self.counts_by_category,
            "total_violations": self.total_violations,
            "emitted_by": _pmd.emitted_by("sta_triage_classify"),
        }


def classify_endpoint(
    cell_delay_pct: Optional[float],
    net_delay_pct: Optional[float],
    logic_depth: Optional[int],
    skew_dominant: bool = False,
    is_hold: bool = False,
    boundary_state: Optional[str] = None,
) -> str:
    """Classify a single violating endpoint by delay breakdown."""
    if boundary_state == "CONFLICT":
        return "constraint_boundary_limited"
    if boundary_state == "UNRESOLVED":
        return "constraint_boundary_unresolved"
    if is_hold:
        return "hold_violation"
    if skew_dominant:
        return "clock_skew_limited"
    if logic_depth is not None and logic_depth >= 40:
        return "logic_depth_limited"
    if net_delay_pct is not None and net_delay_pct >= 60:
        return "net_delay_limited"
    if cell_delay_pct is not None and cell_delay_pct >= 60:
        return "cell_delay_limited"
    # Default: pick the bigger of cell/net
    cd = cell_delay_pct or 0
    nd = net_delay_pct or 0
    return "net_delay_limited" if nd >= cd else "cell_delay_limited"


def parse_sta_summary(text: str) -> tuple:
    """Extract WNS / TNS from a typical OpenSTA / DC summary text."""
    wns = None
    tns = None
    m = re.search(r"wns\s+(-?\d+\.?\d*)", text, re.I)
    if m:
        wns = float(m.group(1))
    m = re.search(r"tns\s+(-?\d+\.?\d*)", text, re.I)
    if m:
        tns = float(m.group(1))
    return wns, tns


def build_report(endpoints: List[EndpointFinding],
                  wns: Optional[float],
                  tns: Optional[float]) -> StaReport:
    counts: Dict[str, int] = {c: 0 for c in CATEGORIES}
    for e in endpoints:
        counts[e.category] = counts.get(e.category, 0) + 1
    return StaReport(
        findings=endpoints, wns_ns=wns, tns_ns=tns,
        counts_by_category=counts,
        total_violations=len(endpoints),
    )


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ADMITTED = {"PASS", "RESOLVED", "ADMITTED"}


def boundary_state(record: Optional[Dict[str, Any]], role: Optional[str]) -> Optional[str]:
    """Return PASS/CONFLICT/UNRESOLVED for a source-bound I/O boundary.

    The classifier derives the state from measured values. A caller cannot
    turn a contradictory boundary into PASS by supplying a verdict label.
    Missing hashes or values are unresolved and therefore cannot recommend an
    RTL/ECO edit.
    """
    if record is None or role is None:
        return None
    if role not in {"input", "output"}:
        return "UNRESOLVED"
    if record.get("schema") != "vibeic.sta_constraint_boundary.v1":
        return "UNRESOLVED"
    if not all(_SHA256.fullmatch(str(record.get(key, "")).lower())
               for key in ("source_sha256", "deck_sha256")):
        return "UNRESOLVED"
    keys = (("input_transition_ns", "library_max_transition_ns") if role == "input"
            else ("output_load_pf", "available_driver_max_capacitance_pf"))
    try:
        demand, limit = (float(record[key]) for key in keys)
    except (KeyError, TypeError, ValueError):
        return "UNRESOLVED"
    if not all(math.isfinite(value) and value >= 0 for value in (demand, limit)):
        return "UNRESOLVED"
    admitted = str(record.get("pad_driver_admission", "")).upper() in _ADMITTED
    if demand > limit and not admitted:
        return "CONFLICT"
    if not admitted:
        return "UNRESOLVED"
    return "PASS"


def make_finding(endpoint: str, slack_ns: float,
                  cell_delay_pct: Optional[float] = None,
                  net_delay_pct: Optional[float] = None,
                  logic_depth: Optional[int] = None,
                  skew_dominant: bool = False,
                  boundary_role: Optional[str] = None,
                  boundary: Optional[Dict[str, Any]] = None) -> EndpointFinding:
    is_hold = slack_ns < 0 and logic_depth is not None and logic_depth <= 2
    bstate = boundary_state(boundary, boundary_role)
    cat = classify_endpoint(cell_delay_pct, net_delay_pct,
                            logic_depth, skew_dominant=skew_dominant,
                            is_hold=is_hold, boundary_state=bstate)
    return EndpointFinding(
        endpoint=endpoint, slack_ns=slack_ns, category=cat,
        fix_strategy=FIX_STRATEGY[cat],
        cell_delay_pct=cell_delay_pct, net_delay_pct=net_delay_pct,
        logic_depth=logic_depth, boundary_role=boundary_role,
        boundary_state=bstate,
        boundary_source_sha256=(str(boundary.get("source_sha256")) if boundary else None),
        boundary_deck_sha256=(str(boundary.get("deck_sha256")) if boundary else None),
    )


def report_to_markdown(rep: StaReport) -> str:
    out = ["# STA triage",
           "",
           f"_Emitted by `sta_triage_classify.py` "
           f"(v{_pmd.running_plugin_version()})._",
           "",
           f"- WNS: {rep.wns_ns}",
           f"- TNS: {rep.tns_ns}",
           f"- Total violating endpoints: **{rep.total_violations}**",
           ""]
    out.append("## By category")
    out.append("")
    out.append("| Category | Count | Fix strategy |")
    out.append("|---|---|---|")
    for c in CATEGORIES:
        n = rep.counts_by_category.get(c, 0)
        if n > 0:
            out.append(f"| {c} | {n} | {FIX_STRATEGY[c]} |")
    out.append("")
    out.append("## Per-endpoint")
    out.append("")
    for f in rep.findings[:50]:
        out.append(f"- `{f.endpoint}` slack={f.slack_ns} ns "
                   f"[{f.category}] — {f.fix_strategy}")
    if len(rep.findings) > 50:
        out.append(f"_(+{len(rep.findings)-50} more)_")
    out.append("")
    return "\n".join(out)


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoints-json", type=Path, required=True,
                   help='JSON: [{"endpoint": ..., "slack_ns": ..., '
                        '"cell_delay_pct": ..., "net_delay_pct": ..., '
                        '"logic_depth": ...}, ...]')
    p.add_argument("--wns", type=float)
    p.add_argument("--tns", type=float)
    p.add_argument("--boundary-json", type=Path,
                   help="source-bound I/O electrical boundary record")
    p.add_argument("--out-md", type=Path)
    p.add_argument("--out-json", type=Path)
    args = p.parse_args()
    raw = json.loads(args.endpoints_json.read_text(encoding="utf-8"))
    boundary = (json.loads(args.boundary_json.read_text(encoding="utf-8"))
                if args.boundary_json else None)
    endpoints = [make_finding(boundary=boundary, **r) for r in raw]
    rep = build_report(endpoints, args.wns, args.tns)
    md = report_to_markdown(rep)
    if args.out_md:
        args.out_md.write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.out_json:
        args.out_json.write_text(
            json.dumps(rep.as_dict(), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
