#!/usr/bin/env python3
"""retap_audit_check.py — Step 19 audit of `Vibeic.ExternalCaptureLaunchRetap`.

The retap step (audit §5.2: OWN_NECESSARY, OpenROAD has no useful-skew
scheduler) moves a launch register's clock pin from its CTS leaf net to an
upstream CTS net, one measured trial at a time, and keeps a trial only when its
own policy (`external_capture_retap_policy.tcl`) says the measurement improved.
Nothing checked that claim from outside the session that made it. This gate
does, from the step's own two artefacts:

  1. the step log's `VIC_RETAP` rows (the fixed grammar the step prints:
     `none: ...`, `margin=... candidates=N`, `keep <inst> <pin> <old> -> <new>
     local=a->b setup=a->b tns=a->b hold=a->b`, `reject <inst> reason=R ...`);
  2. the netlist the step read (`state_in.json` `nl`) and the one it wrote
     (`state_out.json` `nl`).

Rules (TOOL_DUPLICATION_AUDIT §2 row 19 "retap 要有稽核"):
  * every `keep` row must satisfy the retap policy when RE-DECIDED here from
    the numbers it printed: the local path improved, the design's worst setup
    slack and TNS did not regress, and hold is non-negative (or, when it was
    already negative, did not get worse) -> else FAIL `RETAP_KEEP_UNSUPPORTED`;
  * the netlist change the step made must be EXACTLY its `keep` rows: the same
    instance, pin, old net and new net; no instance added, removed or
    re-mastered, no other connection moved -> else FAIL
    `RETAP_UNEXPLAINED_NETLIST_CHANGE` / `RETAP_KEEP_NOT_IN_NETLIST`;
  * a log with no `VIC_RETAP` row, or a netlist that cannot be read, is
    NOT_MEASURED (rc 2), never PASS.

Exit: 0 PASS, 1 FAIL, 2 NOT_MEASURED. chip-AGNOSTIC: no design, PDK or cell
literal; the only literals are the step's own log grammar.
"""
from __future__ import annotations

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402
if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _atomic_artefact import write_json as _atomic_write_json

PROGRAM = "retap_audit_check"
STEP_ID = "Vibeic.ExternalCaptureLaunchRetap"
HANDOFF = "reports/phase3/librelane_cts_hold_handoff.json"
LOG_NAME = "vibeic-externalcapturelaunchretap.log"

_NUM = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
_PAIR = _NUM + r"->" + _NUM
_KEEP = re.compile(
    r"^VIC_RETAP keep (\S+) (\S+) (\S+) -> (\S+) local=" + _PAIR
    + r" setup=" + _PAIR + r" tns=" + _PAIR + r" hold=" + _PAIR + r"\s*$")
_REJECT = re.compile(r"^VIC_RETAP reject (\S+) reason=(\S+)")
_NONE = re.compile(r"^VIC_RETAP none:")
_MARGIN = re.compile(r"^VIC_RETAP margin=\S+ candidates=(\d+)")


def parse_retap_log(text: str) -> Dict[str, Any]:
    """The step's `VIC_RETAP` rows. `state` is `none` (no near-critical
    output path), `trials` (candidates were tried) or `absent` (no row: the
    step's grammar is not in this log)."""
    keeps: List[Dict[str, Any]] = []
    rejects: List[Dict[str, str]] = []
    state = "absent"
    for line in text.splitlines():
        line = line.strip()
        if _NONE.match(line):
            state = "none"
        elif _MARGIN.match(line):
            state = "trials"
        elif line.startswith("VIC_RETAP keep "):
            m = _KEEP.match(line)
            if not m:
                keeps.append({"unparsed": line})
                continue
            g = m.groups()
            keeps.append({"inst": g[0], "pin": g[1], "old_net": g[2],
                          "new_net": g[3],
                          "local": (float(g[4]), float(g[5])),
                          "setup": (float(g[6]), float(g[7])),
                          "tns": (float(g[8]), float(g[9])),
                          "hold": (float(g[10]), float(g[11]))})
        else:
            m = _REJECT.match(line)
            if m:
                rejects.append({"inst": m.group(1), "reason": m.group(2)})
    return {"state": state, "keeps": keeps, "rejects": rejects}


def redecide(keep: Dict[str, Any]) -> Optional[str]:
    """The retap policy, re-evaluated from the numbers the step printed.
    None when the kept trial satisfies it, else the rule it breaks. Mirrors
    `external_capture_retap_policy.tcl::vic_retap_decide`."""
    if "unparsed" in keep:
        return "KEEP_ROW_UNPARSEABLE"
    l0, l1 = keep["local"]
    w0, w1 = keep["setup"]
    t0, t1 = keep["tns"]
    h0, h1 = keep["hold"]
    if not l1 > l0 + 0.0001:
        return "LOCAL_SETUP_NOT_IMPROVED"
    if not (w1 >= w0 - 0.0001 and t1 >= t0 - 0.01 * abs(t0)):
        return "DESIGN_SETUP_REGRESSED"
    if not (h1 >= 0 or (h0 < 0 and h1 >= h0)):
        return "HOLD_REGRESSED"
    return None


def audit_retap_log(text: str) -> Optional[str]:
    """Calibrated reader (instrument_calibration): the named outcome when the
    log carries at least one retained retap, None when it carries none."""
    import instrument_calibration as _ic
    _ic.assert_calibrated("retap_audit_check::audit_retap_log")
    parsed = parse_retap_log(text)
    kept = [k.get("inst", "?") for k in parsed["keeps"]]
    return f"RETAP_KEPT {len(kept)}" if kept else None


_CELL = re.compile(r"^\s*(\S+)\s+(\S+)\s*\((.*?)\);", re.M | re.S)
_PIN = re.compile(r"\.(\w+)\(\s*([^()]*?)\s*\)")


def netlist_cells(text: str) -> Dict[str, Tuple[str, Dict[str, str]]]:
    """`{instance: (master, {pin: net})}` of a flat structural netlist
    (OpenROAD `write_verilog`): one instantiation per statement."""
    out: Dict[str, Tuple[str, Dict[str, str]]] = {}
    for m in _CELL.finditer(text):
        master, inst, body = m.groups()
        if master in ("module", "input", "output", "inout", "wire", "assign"):
            continue
        pins = {pin: net.lstrip("\\").strip() for pin, net in _PIN.findall(body)}
        out[inst.lstrip("\\")] = (master, pins)
    return out


def netlist_delta(before: str, after: str) -> Dict[str, Any]:
    a, b = netlist_cells(before), netlist_cells(after)
    moved: List[Tuple[str, str, str, str]] = []
    other: List[str] = []
    for inst in sorted(set(a) | set(b)):
        if inst not in a:
            other.append(f"added {inst}")
            continue
        if inst not in b:
            other.append(f"removed {inst}")
            continue
        (ma, pa), (mb, pb) = a[inst], b[inst]
        if ma != mb:
            other.append(f"remastered {inst} {ma} -> {mb}")
        for pin in sorted(set(pa) | set(pb)):
            if pa.get(pin) != pb.get(pin):
                moved.append((inst, pin, pa.get(pin) or "", pb.get(pin) or ""))
    return {"moved": moved, "other": other, "instances": len(b)}


def audit(project: Path) -> Tuple[str, Dict[str, Any]]:
    doc: Dict[str, Any] = {"program": PROGRAM, "step": STEP_ID, "findings": []}
    handoff = project / HANDOFF
    try:
        chain = json.loads(handoff.read_text()).get("chain") or {}
    except (OSError, ValueError) as exc:
        doc["reason"] = f"{HANDOFF} unreadable: {exc}"
        return "NOT_MEASURED", doc
    rel = chain.get(STEP_ID)
    if not rel:
        doc["reason"] = f"{HANDOFF} names no {STEP_ID} step in its chain"
        return "NOT_MEASURED", doc
    folder = project / rel
    doc["folder"] = rel
    try:
        text = (folder / LOG_NAME).read_text(errors="replace")
    except OSError as exc:
        doc["reason"] = f"step log unreadable: {exc}"
        return "NOT_MEASURED", doc
    parsed = parse_retap_log(text)
    doc["log_state"] = parsed["state"]
    doc["keeps"] = [{k: v for k, v in keep.items()} for keep in parsed["keeps"]]
    doc["rejects"] = parsed["rejects"]
    if parsed["state"] == "absent":
        doc["reason"] = f"{LOG_NAME} carries no VIC_RETAP row"
        return "NOT_MEASURED", doc
    audit_retap_log(text)
    try:
        nl_in = Path(json.loads((folder / "state_in.json").read_text())["nl"])
        nl_out = Path(json.loads((folder / "state_out.json").read_text())["nl"])
        delta = netlist_delta(nl_in.read_text(errors="replace"),
                              nl_out.read_text(errors="replace"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        doc["reason"] = f"the step's input/output netlist is unreadable: {exc}"
        return "NOT_MEASURED", doc
    if delta["instances"] == 0:
        doc["reason"] = f"{nl_out} holds no instance the netlist reader could parse"
        return "NOT_MEASURED", doc
    findings = doc["findings"]
    for keep in parsed["keeps"]:
        broken = redecide(keep)
        if broken:
            findings.append({"code": "RETAP_KEEP_UNSUPPORTED",
                             "inst": keep.get("inst"), "rule": broken})
    claimed = {(k["inst"], k["pin"], k["old_net"], k["new_net"])
               for k in parsed["keeps"] if "unparsed" not in k}
    moved = set(delta["moved"])
    for row in sorted(moved - claimed):
        findings.append({"code": "RETAP_UNEXPLAINED_NETLIST_CHANGE",
                         "change": list(row)})
    for row in sorted(claimed - moved):
        findings.append({"code": "RETAP_KEEP_NOT_IN_NETLIST", "keep": list(row)})
    for change in delta["other"]:
        findings.append({"code": "RETAP_UNEXPLAINED_NETLIST_CHANGE",
                         "change": change})
    doc["netlist"] = {"in": str(nl_in), "out": str(nl_out),
                      "moved_connections": len(moved),
                      "instances": delta["instances"]}
    return ("FAIL" if findings else "PASS"), doc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    verdict, doc = audit(project)
    doc["verdict"] = verdict
    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = project / out
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out, doc)
    detail = doc.get("reason") or "; ".join(
        f"{f['code']} {f.get('inst') or f.get('change') or f.get('keep')}"
        for f in doc["findings"]) or (
        f"{len(doc.get('keeps', []))} retained retap(s) re-decided and matched "
        "to the netlist change")
    print(f"[{verdict}] {PROGRAM}: {detail}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
