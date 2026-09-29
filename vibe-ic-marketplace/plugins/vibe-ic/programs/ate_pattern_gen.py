#!/usr/bin/env python3
"""ate_pattern_gen.py — ATE patterns from the Step-5 full-stack traces (Step 38).

ENFORCEMENT: producer — writes the patterns, decides no step. The Step-38 gate
`foundry_handoff_package_check` reads the kit member that lists them and
re-hashes every file it names.

WHY (U18, IC_BLOCKER_AUDIT 2026-09-29)
======================================
The Step-38 kit carried `PENDING_FOUNDRY_test_patterns` with `owner:
this_flow` — "conversion of the L10 seeds already listed in this member into
ATE patterns from the cocotb / Verilator traces this flow produced" — and the
gate passed over it as if it were a foundry item. It is ours: the flow owns the
simulation that knows what every pin should do. This program is the conversion.

WHAT A PATTERN IS HERE
======================
The trace is the one Step 5 (`full_stack_functional_tb`) records for each L10
case it executed and PASSED: a VCD of the device-under-test scope at depth 1,
hashed in the Step-5 record. On a DIE route that DUT is the pad-ring chip top,
so the columns are the die's own pins.

Format `event_timed_vcd_replay`: one vector per VCD timestamp at which a PORT
changed, carrying the state of every port AFTER the changes at that timestamp — inputs as the drive
column, outputs as the expect column (strobe at that timestamp; the RTL/gate
simulation is zero-delay at the pins), bidirectional ports as observed. Values
are 0/1/x/z per bit, MSB first. `x` in an expect column is a mask (the
simulation did not define the pin there), never a compare against X.
Per case: `<case>.vec.gz` (the rows, gzip with mtime 0 so equal traces give
equal bytes) and `<case>.json` (columns, timescale, row count, and the sha256
of the vector file and of the source trace).

Refusals are never silent: a port the trace does not carry, a scope the VCD
does not declare, or a trace whose bytes no longer match the Step-5 record
yields NO pattern for that case and a named reason.

chip-AGNOSTIC: port names and directions come from the DUT's own source (via
the Step-5 record), widths from the VCD the simulator wrote.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import _atomic_artefact as _aa

PROGRAM = "ate_pattern_gen"
SCHEMA = "vibeic.ate_pattern.v1"
FORMAT = "event_timed_vcd_replay"
#: Where Step 38 puts the patterns, inside the hand-off kit.
PATTERN_SUBDIR = "ate_patterns"
#: The Step-5 record (the producer's own constant, read here as a path).
STEP5_RECORD_REL = ("phase2/stage1/sim_full_stack/functional/"
                    "functional_cases.json")
_DIRS = {"input": "drive", "output": "expect", "inout": "bidir"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def vcd_header(path: Path, scope: str) -> Tuple[str, Dict[str, Tuple[str, int]]]:
    """(timescale, {name: (id, width)}) for the vars declared DIRECTLY in
    `scope` (`a/b`; sub-scopes are not read)."""
    want = scope.split("/")
    stack: List[str] = []
    timescale: List[str] = []
    names: Dict[str, Tuple[str, int]] = {}
    in_ts = False
    with Path(path).open("r", errors="replace") as fh:
        for line in fh:
            words = line.split()
            if not words:
                continue
            if in_ts or words[0] == "$timescale":
                timescale += [w for w in words if w not in ("$timescale",
                                                            "$end")]
                in_ts = "$end" not in words
            elif words[0] == "$scope" and len(words) >= 3:
                stack.append(words[2])
            elif words[0] == "$upscope" and stack:
                stack.pop()
            elif words[0] == "$var" and len(words) >= 5 and stack == want:
                names[words[4]] = (words[3], int(words[2]))
            elif words[0] == "$enddefinitions":
                break
    return " ".join(timescale), names


def vcd_changes(path: Path, ids: set) -> Iterator[Tuple[int, Dict[str, str]]]:
    """(time, {id: value}) per VCD timestamp, for the ids in `ids` only.

    Streams: a trace of millions of timestamps is never held in memory. A
    timestamp at which none of `ids` changed yields an empty dict."""
    header = True
    t: Optional[int] = None
    cur: Dict[str, str] = {}
    with Path(path).open("r", errors="replace") as fh:
        for line in fh:
            if header:
                if line.lstrip().startswith("$enddefinitions"):
                    header = False
                continue
            s = line.strip()
            if not s:
                continue
            c = s[0]
            if c == "#":
                if t is not None:
                    yield t, cur
                t, cur = int(s[1:]), {}
            elif c in "01xzXZ":
                if s[1:] in ids:
                    cur[s[1:]] = c.lower()
            elif c in "bB":
                val, _sp, vid = s[1:].partition(" ")
                vid = vid.strip()
                if vid in ids:
                    cur[vid] = val.lower()
    if t is not None:
        yield t, cur


def _extend(val: str, width: int) -> str:
    if len(val) >= width:
        return val[-width:]
    fill = val[0] if val and val[0] in "xz" else "0"
    return fill * (width - len(val)) + val


def columns_for(names: Dict[str, Tuple[str, int]], scope: str,
                ports: List[Dict[str, str]]
                ) -> Tuple[Optional[Dict[str, List[Tuple[str, str, int]]]], str]:
    """({drive|expect|bidir: [(name, id, width)]}, "") or (None, refusal)."""
    if not names:
        return None, f"the VCD declares no variable in scope {scope!r}"
    cols: Dict[str, List[Tuple[str, str, int]]] = {
        "drive": [], "expect": [], "bidir": []}
    absent = []
    for p in ports:
        kind = _DIRS.get(str(p.get("direction")))
        if kind is None:
            return None, (f"port {p.get('name')!r} has direction "
                          f"{p.get('direction')!r}")
        hit = names.get(str(p.get("name")))
        if hit is None:
            absent.append(p.get("name"))
            continue
        cols[kind].append((str(p["name"]), hit[0], hit[1]))
    if absent:
        return None, (f"the trace carries no value for DUT port(s) {absent} "
                      f"in scope {scope!r}")
    if not (cols["drive"] or cols["bidir"]) \
            or not (cols["expect"] or cols["bidir"]):
        return None, "the DUT has no drivable or no observable port"
    return cols, ""


def vector_rows(vcd: Path, cols: Dict[str, List[Tuple[str, str, int]]]
                ) -> Iterator[str]:
    """One text row per timestamp at which a PORT changed:
    `<time> <drive> <expect> <bidir>`, each column the ports' bit strings
    joined by `_` (MSB first, `-` for an empty column)."""
    ids = {vid for v in cols.values() for _n, vid, _w in v}
    state: Dict[str, str] = {}
    first = True
    for t, changes in vcd_changes(vcd, ids):
        if not changes and not first:
            continue
        first = False
        state.update(changes)
        parts = [str(t)]
        for kind in ("drive", "expect", "bidir"):
            parts.append("_".join(_extend(state.get(vid, "x"), w)
                                  for _n, vid, w in cols[kind]) or "-")
        yield " ".join(parts) + "\n"


def write_pattern(vcd: Path, scope: str, ports: List[Dict[str, str]],
                  vec_path: Path) -> Dict[str, Any]:
    """Write the gzip vector file; return the header, or {"status": "REFUSED"}.

    gzip with mtime 0 so the same trace always gives the same bytes (and the
    same sha256)."""
    timescale, names = vcd_header(vcd, scope)
    cols, why = columns_for(names, scope, ports)
    if cols is None:
        return {"status": "REFUSED", "reason": why}
    n = 0
    with _aa.writing(vec_path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           mtime=0) as gz:
            for row in vector_rows(vcd, cols):
                gz.write(row.encode())
                n += 1
    if n == 0:
        vec_path.unlink(missing_ok=True)
        return {"status": "REFUSED", "reason": "the VCD records no timestamp"}
    return {
        "status": "WROTE",
        "schema": SCHEMA, "program": PROGRAM, "format": FORMAT,
        "timescale": timescale, "dut_scope": scope,
        "columns": {k: [{"name": nm, "width": w} for nm, _v, w in v]
                    for k, v in cols.items()},
        "row_layout": "<time> <drive> <expect> <bidir>",
        "strobe": ("expect columns are the settled pin state at the row's "
                   "timestamp in the zero-delay simulation; 'x' masks the "
                   "compare"),
        "vector_count": n,
    }


def _rel(project: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(project).resolve()))
    except ValueError:
        return str(path)


def load_step5(project: Path) -> Optional[dict]:
    try:
        return json.loads((Path(project) / STEP5_RECORD_REL).read_text())
    except (OSError, ValueError):
        return None


def emit(project: Path, out_dir: Path, seeds: List[str]) -> Dict[str, Any]:
    """Convert every seed's Step-5 trace into a pattern file under `out_dir`.

    Returns {"patterns": [...], "not_converted": [...], "excluded": [...],
    "step5_record": {...}}. A seed is converted only when Step 5 PASSED it and
    recorded a verified trace whose bytes still hash to the record. A seed Step
    5 EXCLUDED as a non-functional acceptance (a coverage figure) is listed with
    Step 5's reason; every other seed without a pattern is `not_converted`."""
    project = Path(project)
    res: Dict[str, Any] = {"patterns": [], "not_converted": [],
                           "excluded": [], "step5_record": None}
    rec = load_step5(project)
    if rec is None:
        res["not_converted"] = [{"case": s, "reason": (
            f"no Step-5 functional record at {STEP5_RECORD_REL}: no trace "
            f"this flow produced exists to convert")} for s in seeds]
        return res
    rpath = project / STEP5_RECORD_REL
    res["step5_record"] = {"path": STEP5_RECORD_REL,
                           "sha256": sha256_file(rpath),
                           "verdict": rec.get("verdict")}
    by_name = {str(c.get("name")): c for c in rec.get("cases") or []
               if isinstance(c, dict)}
    ports = rec.get("dut_ports")
    for seed in seeds:
        case = by_name.get(seed)
        if case is None:
            res["not_converted"].append({"case": seed, "reason": (
                "Step 5 recorded no row for this L10 case")})
            continue
        if case.get("state") == "excluded":
            res["excluded"].append({"case": seed, "reason": case.get("reason")})
            continue
        if case.get("state") != "passed":
            res["not_converted"].append({"case": seed, "reason": (
                f"Step 5 state {case.get('state')!r}: only a case the "
                f"design passed has an expected response to put on a tester")})
            continue
        tr = case.get("trace") or {}
        if not tr.get("scope_verified") or not tr.get("vcd"):
            res["not_converted"].append({"case": seed, "reason": (
                f"Step 5 recorded no verified trace for this case: "
                f"{tr.get('reason') or 'no trace entry'}")})
            continue
        if not isinstance(ports, list) or not ports:
            res["not_converted"].append({"case": seed, "reason": (
                "the Step-5 record carries no DUT port list")})
            continue
        vcd = project / str(tr["vcd"])
        if not vcd.is_file() or sha256_file(vcd) != tr.get("sha256"):
            res["not_converted"].append({"case": seed, "reason": (
                f"the trace {tr['vcd']} is absent or no longer hashes to the "
                f"Step-5 record")})
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        vec = out_dir / f"{seed}.vec.gz"
        doc = write_pattern(vcd, str(tr.get("scope")), ports, vec)
        if doc.get("status") != "WROTE":
            res["not_converted"].append({"case": seed,
                                         "reason": doc.get("reason")})
            continue
        doc.pop("status", None)
        doc["case"] = seed
        doc["dut_module"] = case.get("instantiates")
        doc["vectors_file"] = {"path": _rel(project, vec),
                               "sha256": sha256_file(vec)}
        doc["source_trace"] = {"path": str(tr["vcd"]), "sha256": tr["sha256"]}
        doc["step5_record"] = res["step5_record"]
        out = out_dir / f"{seed}.json"
        _aa.write_text(out, json.dumps(doc, indent=2) + "\n")
        res["patterns"].append({
            "case": seed,
            "path": _rel(project, out),
            "sha256": sha256_file(out),
            "vector_count": doc["vector_count"],
            "source_trace": doc["source_trace"]})
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--case", action="append", default=[],
                    help="L10 case id to convert (repeatable)")
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    project = args.project.resolve()
    out_dir = args.out_dir or (project / "phase3/stage4/foundry_handoff"
                               / PATTERN_SUBDIR)
    seeds = args.case
    if not seeds:
        rec = load_step5(project) or {}
        seeds = [str(c.get("name")) for c in rec.get("cases") or []]
    res = emit(project, out_dir, seeds)
    print(json.dumps({k: res[k] for k in ("patterns", "not_converted",
                                          "excluded")}, indent=2))
    if res["not_converted"] or not res["patterns"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
