#!/usr/bin/env python3
"""analog_b_analog_cutover.py — criterion b-analog for an analog step's
cut-over from `direct` to the tool path (owner decision q7, T109).

WHY THE DIGITAL CRITERION (b) DOES NOT CARRY OVER
The migration's criterion (b) exists because a digital step's product (an
ODB, a DEF) is the next step's input, so a new producer can hurt downstream
DRC/LVS/timing/antenna. A6 and A7 are OBSERVERS: A6 writes DRC/LVS reports,
A7 writes `pre_vs_post.json`; the only downstream producer, A8, reads A5's
GDS/.mag and A2's topology and copies the GDS byte for byte. An observer can
reach downstream through exactly three channels, and b-analog measures each:

  (b1) NON-INTERFERENCE — files. Every file under `phase3/analog/**` that
       existed before the chain has the same sha256 in both arms; A8's
       products (`hardmacro/<block>/*.gds` by bytes, `.lef/.lib/.v` by text)
       are identical; and the FILE POPULATION under `phase3/analog` is the
       same member for member in both arms, apart from the step's own
       declared records (`STEP_OWN_RECORDS`) — a superset of "every gate that
       globs phase3/analog sees the same files", derived from the tree rather
       than from a hand list of gates.
  (b2) VERDICT EQUIVALENCE. The runner's A6..A9 verdicts and
       `flow_compliance_check --strict`'s A1..A9 and M1..M4 verdicts are equal
       across arms, except: the step under test may turn WAIVED into PASS in
       the tool arm; and the tool arm may FAIL where direct did not ONLY on a
       rule an independent control confirms is a defect of the deliverable
       (`--control`, a JSON file naming the step and the confirming control).
  (b3) NO EXTRA CLOSED-LOOP RE-ENTRY. The step's declared `closed_loop`
       trigger (A7: degradation > 10 % post-extraction -> A3) fires in the
       tool arm only if it fires in direct too. Evaluated on the arm's own
       `pre_vs_post.json` with the threshold the flow YAML declares.

M1..M4 are covered by (b1): M1 reads only A8's products and step 37's
digital GDS, and neither A6 nor A7 writes anything a downstream producer
reads.

Sub-commands
  drive    <project> --blocks B1,B2 [--steps A6,A7,A8,A9] [--container C]
           --json OUT     run the steps through the runner's own
                          `_spf.gate(..., _preflight_refusal(...),
                          step_for_block, ...)` path, then
                          `flow_compliance_check --strict`
  manifest <project> --json OUT   sha256 of every file under phase3/analog
  compare  --base M --direct D --tool T --step S [--control C] --json OUT

Exit codes (compare): 0 b-analog PASSES; 1 it FAILS (named); 2 NOT_MEASURED
(an arm's record is missing). chip-AGNOSTIC.
"""
from __future__ import annotations

import os as _os
import sys as _sys

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from _atomic_artefact import write_json

PROGRAM = "analog_b_analog_cutover"
PROGRAMS_DIR = Path(__file__).resolve().parent

#: Each step's own declared records beside the block: the only files the arms
#: may differ by under phase3/analog. A7: the flow YAML's required output and
#: the producer's own record. A6: the LibreLane arm's record and work dir.
STEP_OWN_RECORDS = {
    "A6": ("a6_librelane_drc.json", "a6_librelane/"),
    "A7": ("pre_vs_post.json", "a7_post_layout.json"),
}
#: The runner's step names, in the order the runner dispatches them.
RUNNER_STEP = {"A6": "A6_block_pv", "A7": "A7_post_layout_resim",
               "A8": "A8_hardmacro_gen", "A9": "A9_hw_verify"}
COMPLIANCE_STEPS = tuple([f"A{i}" for i in range(1, 10)] +
                         [f"M{i}" for i in range(1, 5)])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(project: Path) -> Dict[str, str]:
    root = project / "phase3" / "analog"
    return {str(p.relative_to(project)): _sha256(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── drive ─────────────────────────────────────────────────────────────────
def drive(project: Path, blocks: List[str], steps: List[str],
          container: str) -> dict:
    import analog_one_shot_runner as R
    import step_preflight as _spf
    args = argparse.Namespace(project=project, container=container,
                              allow_deterministic_stubs=False,
                              blocks=",".join(blocks), pdk="")
    listed, _status = R._load_block_list_with_status(project)
    by_name = {(b.get("name") or b.get("type")): b for b in listed}
    rows = []
    for bname in blocks:
        blk = by_name.get(bname)
        if blk is None:
            rows.append({"block": bname, "step": None,
                         "status": "NOT_MEASURED",
                         "detail": "block not in the project's block list"})
            continue
        for sid in steps:
            name = RUNNER_STEP[sid]
            t0 = time.time()
            sr = _spf.gate(project, "analog_one_shot_runner", sid,
                           R._preflight_refusal(name, bname),
                           R.step_for_block, project, blk, name, args=args,
                           _preflight_note=f"block={bname}")
            rows.append({"block": bname, "step": sid, "status": sr.status,
                         "detail": sr.detail,
                         "reason_class": getattr(sr, "reason_class", ""),
                         "extras": getattr(sr, "extras", None) or {},
                         "wall_s": round(time.time() - t0, 1)})
            print(f"[{PROGRAM}] {bname} {sid} {sr.status} "
                  f"({rows[-1]['wall_s']} s)", flush=True)
    comp_json = project / "reports" / "b_analog_compliance.json"
    cp = subprocess.run([sys.executable,
                         str(PROGRAMS_DIR / "flow_compliance_check.py"),
                         str(project), "--strict", "--json", str(comp_json)],
                        capture_output=True, text=True)
    comp = {}
    try:
        doc = json.loads(comp_json.read_text())
        comp = {str(s["id"]): {"status": s.get("status"),
                               "reason_class": s.get("reason_class", "")}
                for s in doc.get("steps") or []
                if str(s.get("id")) in COMPLIANCE_STEPS}
    except (OSError, ValueError):
        comp = {}
    return {"program": PROGRAM, "project": str(project), "blocks": blocks,
            "steps": steps, "runner": rows, "compliance": comp,
            "compliance_rc": cp.returncode}


# ── compare ───────────────────────────────────────────────────────────────
def _own(step: str, rel: str) -> bool:
    name = rel.split("/", 3)[-1] if rel.count("/") >= 3 else rel
    return any(name == o or (o.endswith("/") and name.startswith(o))
               for o in STEP_OWN_RECORDS.get(step, ()))


def closed_loop_threshold(step: str, flow_yaml: Path) -> Optional[float]:
    """The percentage the flow YAML's `closed_loop.trigger` names for the
    step, or None when the step declares none."""
    import yaml
    doc = yaml.safe_load(flow_yaml.read_text())
    for st in (doc.get("steps") or []):
        if str(st.get("id")) == step:
            trig = str(((st.get("closed_loop") or {}).get("trigger")) or "")
            m = re.search(r">\s*([0-9.]+)\s*%", trig)
            return float(m.group(1)) if m else None
    return None


def reentries(project: Path, blocks: List[str], threshold: Optional[float]
              ) -> Dict[str, List[str]]:
    """Per block, the pre_vs_post rows whose |delta| exceeds the declared
    closed-loop threshold (A7 -> A3)."""
    out: Dict[str, List[str]] = {}
    if threshold is None:
        return out
    for b in blocks:
        p = project / "phase3" / "analog" / b / "pre_vs_post.json"
        try:
            specs = json.loads(p.read_text()).get("specs") or []
        except (OSError, ValueError):
            continue
        hot = [s["name"] for s in specs
               if isinstance(s.get("delta_pct"), (int, float))
               and abs(s["delta_pct"]) > threshold]
        if hot:
            out[b] = hot
    return out


def compare(base: Dict[str, str], d_proj: Path, t_proj: Path, step: str,
            d_drive: dict, t_drive: dict, control: Optional[dict],
            flow_yaml: Path) -> dict:
    blocks = list(d_drive.get("blocks") or [])
    md, mt = manifest(d_proj), manifest(t_proj)
    # (b1) pre-existing files, both arms
    changed = sorted(rel for rel, h in base.items()
                     if md.get(rel) != mt.get(rel))
    a8 = {}
    for b in blocks:
        for ext in (".gds", ".lef", ".lib", ".v"):
            rel = f"phase3/analog/hardmacro/{b}/{b}{ext}"
            a8[rel] = {"direct": md.get(rel), "tool": mt.get(rel),
                       "identical": md.get(rel) == mt.get(rel)
                       and md.get(rel) is not None}
    pop_d = {r for r in md if not _own(step, r)}
    pop_t = {r for r in mt if not _own(step, r)}
    b1 = {"pre_existing_changed_across_arms": changed,
          "a8_products": a8,
          "population_only_direct": sorted(pop_d - pop_t),
          "population_only_tool": sorted(pop_t - pop_d),
          "step_own_records": list(STEP_OWN_RECORDS.get(step, ()))}
    b1["pass"] = (not changed and not b1["population_only_direct"]
                  and not b1["population_only_tool"]
                  and all(v["identical"] for v in a8.values()))
    # (b2) verdicts
    def runner_map(dr):
        return {f"{r['block']}/{r['step']}": r["status"]
                for r in dr.get("runner") or [] if r.get("step")}
    rd, rt = runner_map(d_drive), runner_map(t_drive)
    diffs = []
    for key in sorted(set(rd) | set(rt)):
        a, b = rd.get(key), rt.get(key)
        if a == b:
            continue
        allowed = None
        if key.endswith(f"/{step}") and a == "WAIVED" and b == "PASS":
            allowed = "WAIVED->PASS at the step under test"
        elif b == "FAIL" and a != "FAIL" and control and \
                key in (control.get("confirmed") or {}):
            allowed = f"independent control: {control['confirmed'][key]}"
        diffs.append({"where": f"runner {key}", "direct": a, "tool": b,
                      "allowed": allowed})
    cd, ct = d_drive.get("compliance") or {}, t_drive.get("compliance") or {}
    for sid in COMPLIANCE_STEPS:
        a = (cd.get(sid) or {}).get("status")
        b = (ct.get(sid) or {}).get("status")
        if a == b:
            continue
        allowed = ("WAIVED->PASS at the step under test"
                   if sid == step and a == "WAIVED" and b == "PASS" else None)
        diffs.append({"where": f"compliance {sid}", "direct": a, "tool": b,
                      "allowed": allowed})
    b2 = {"differences": diffs,
          "pass": bool(cd) and bool(ct)
          and all(d["allowed"] for d in diffs)}
    # (b3) closed-loop re-entry
    thr = closed_loop_threshold(step, flow_yaml)
    ed, et = reentries(d_proj, blocks, thr), reentries(t_proj, blocks, thr)
    extra = {b: rows for b, rows in et.items() if b not in ed}
    b3 = {"threshold_pct": thr, "direct": ed, "tool": et,
          "tool_only": extra, "pass": not extra}
    ok = b1["pass"] and b2["pass"] and b3["pass"]
    return {"program": PROGRAM, "step": step, "blocks": blocks,
            "b1": b1, "b2": b2, "b3": b3,
            "result": "PASS" if ok else "FAIL"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drive")
    d.add_argument("project", type=Path)
    d.add_argument("--blocks", required=True)
    d.add_argument("--steps", default="A6,A7,A8,A9")
    d.add_argument("--container", default="")
    d.add_argument("--json", type=Path, required=True)
    m = sub.add_parser("manifest")
    m.add_argument("project", type=Path)
    m.add_argument("--json", type=Path, required=True)
    c = sub.add_parser("compare")
    c.add_argument("--base", type=Path, required=True)
    c.add_argument("--direct", type=Path, required=True)
    c.add_argument("--tool", type=Path, required=True)
    c.add_argument("--direct-drive", type=Path, required=True)
    c.add_argument("--tool-drive", type=Path, required=True)
    c.add_argument("--step", required=True, choices=sorted(STEP_OWN_RECORDS))
    c.add_argument("--control", type=Path, default=None)
    c.add_argument("--flow", type=Path,
                   default=PROGRAMS_DIR.parent / "flow"
                   / "phase1_phase2_phase3.yaml")
    c.add_argument("--json", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "drive":
        if not a.container:
            import _eda_pin as _pin
            a.container = _pin.default_container_name()
        rec = drive(a.project.resolve(),
                    [b for b in a.blocks.split(",") if b],
                    [s for s in a.steps.split(",") if s], a.container)
        write_json(a.json, rec)
        return 0
    if a.cmd == "manifest":
        write_json(a.json, manifest(a.project.resolve()))
        return 0
    try:
        base = json.loads(a.base.read_text())
        dd = json.loads(a.direct_drive.read_text())
        td = json.loads(a.tool_drive.read_text())
        ctl = json.loads(a.control.read_text()) if a.control else None
    except (OSError, ValueError) as exc:
        print(f"NOT_MEASURED: {PROGRAM}: {exc}", file=sys.stderr)
        return 2
    rec = compare(base, a.direct.resolve(), a.tool.resolve(), a.step, dd, td,
                  ctl, a.flow)
    write_json(a.json, rec)
    print(f"[{PROGRAM}] {a.step}: b1={rec['b1']['pass']} "
          f"b2={rec['b2']['pass']} b3={rec['b3']['pass']} -> {rec['result']}")
    return 0 if rec["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
