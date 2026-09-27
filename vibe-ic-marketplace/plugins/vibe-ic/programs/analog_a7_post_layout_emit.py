#!/usr/bin/env python3
"""analog_a7_post_layout_emit.py — the deterministic A7 producer.

A7 (post-layout resimulation) had no producer: the runner WAIVED it to the
`analog-extraction-resim` skill, which hand-built a Magic RC recipe
(`magic_extract_spice_emit.build_extraction_tcl`). The tool does that job, so
the tool does it here:

  1. EXTRACT with LibreLane `Magic.RCX` (`python3 -m librelane.steps run`,
     through `librelane_contract.run_chain`) on the A5 GDS — one run per
     `ngspice` extraction style the A5 layout's OWN Magic technology declares
     (`extract / style ngspice variants (...)`), the device-only `lvs` variant
     excluded because it extracts no parasitics.
  2. AUDIT what Magic wrote with `magic_extract_spice_emit
     .audit_extracted_netlist`: 0 R and 0 C is refused (a re-simulation of it
     is the pre-layout circuit again, a false 0 % degradation); the depth
     achieved (RC / C_ONLY) is recorded.
  3. RESIMULATE with A4's own machinery (`analog_real_corner_sweep
     ._run_ngspice`): the block's A3 testbench, once as delivered (pre) and
     once with the extracted netlist behind a wrapper subcircuit that keeps
     the A3 port order (post), in the same container. The transient stops at
     the end of the last window a `meas` card reads, plus one sample clock
     (`measurement_span`), and every simulation runs under the SAME declared
     budget (`simulation_budget`); a run that spends it is NOT_MEASURED with
     the time it reached, never a hang and never a FAIL (T130).
  4. WRITE `phase3/analog/<block>/pre_vs_post.json` — one spec row per
     measurement per extraction style — naming the extracted netlist as its
     post-layout evidence. The A7 gate owns the verdict.

WHAT THE PRODUCER GUARANTEES BEYOND "IT RAN" (q7, T109):
  * NON-INTERFERENCE. Its working decks live in its own area,
    `phase3/librelane/analog/<block>/a7_resim/`, never under
    `phase3/analog/<block>/`: A3's gates rglob `phase3/analog` for `*.sp`,
    and 11 resimulation decks there grew one gate's population 13 -> 24 and
    flipped another to FAIL. Only `pre_vs_post.json` and
    `a7_post_layout.json` are written beside the block.
  * NO SILENT DROP. The extracted body sits one level down (`xrcx`) in the
    wrapper, so a testbench probe `v(<dut>.<n>)` of an internal net is
    rewritten to `v(<dut>.xrcx.<n>)` when `<n>` is a net of that style's
    extracted netlist (to the wrapper's node when `<n>` is one of the
    extracted subcircuit's ports). A probe whose net is ABSENT after
    extraction is taken out of the post deck and its measurement listed in
    `not_compared` with the reason. Any other measurement the pre run had and
    the post run lacks refuses `A7_POST_MEASUREMENT_MISSING`.
  * DEVICE INVENTORY. Each style's extracted PDK devices must equal the A3
    netlist's: for a model the layout's Magic technology declares a
    `mosfet`/`msubcircuit` device, the sum of w*m per (model, l) (Magic may
    split fingers); for every other model the (model, w, l) multiset with m
    expanded. A difference refuses `A7_RCX_DEVICE_INVENTORY_MISMATCH` — a
    resimulation of a circuit that lost or gained a device is not a
    post-layout measurement of this one.

Nothing here grades, and nothing here invents a value: the PDK and the Magic
technology come from A5's `layout_provenance.json` (the technology the layout
was drawn with), the ports and model binding from the A3 netlist, the stimulus
from the A3 testbench.

Opt-in: the analog runner calls this producer only when
`phase3/librelane_switch.json` selects `librelane` (or `dual`) for step `A7`.

Exit codes: 0 written; 1 the tool ran and its product is refused (named in
`a7_post_layout.json`); 2 honest gap (an upstream artefact this step reads is
absent); 69 environment refusal (the tool could not be reached); 75 a
simulation spent its budget (NOT_MEASURED, reason `budget_exhausted`).
chip-AGNOSTIC.
"""
from __future__ import annotations

import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import _analog_producer_common as _pc
from _atomic_artefact import write_json, write_text

PRODUCER = "analog_a7_post_layout_emit"
STEP = "A7"
RCX_STEP = "Magic.RCX"
#: The extraction variant that is device-only by Magic's own convention: it
#: carries no parasitic corner, so it is not an RCX corner.
_DEVICE_ONLY_VARIANT = "lvs"

_SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)\s*(.*)$", re.I)
_STYLE_RE = re.compile(r"^\s*style\s+(\S+)(?:\s+variants\s+(.*))?$")
_INCLUDE_RE = re.compile(r"^\s*include\s+(\S+)\s*$")
_LIB_RE = re.compile(r"^(\s*\.(?:lib|include|inc)\s+)(\S+)(.*)$", re.I)


# ── pure helpers (tested directly) ─────────────────────────────────────────
def _joined_lines(text: str) -> List[str]:
    """SPICE continuation lines (`+`) joined onto their predecessor."""
    out: List[str] = []
    for raw in text.splitlines():
        if raw.startswith("+") and out:
            out[-1] += " " + raw[1:].strip()
        else:
            out.append(raw)
    return out


def _tech_text(tech: Path) -> str:
    """The technology file with its `include`s spliced in, relative to it."""
    seen: set = set()

    def read(path: Path) -> str:
        if path in seen or not path.is_file():
            return ""
        seen.add(path)
        parts = []
        for line in path.read_text(errors="replace").splitlines():
            m = _INCLUDE_RE.match(line)
            if m:
                name = m.group(1)
                cand = path.parent / name
                if not cand.suffix:
                    cand = cand.with_suffix(".tech")
                parts.append(read(cand))
            else:
                parts.append(line)
        return "\n".join(parts)
    return read(tech)


def extraction_styles(tech: Path) -> List[str]:
    """The `ngspice` extraction styles the technology declares, in its order.

    Read from the `extract` section only; `style ngspice variants (),(x)`
    yields `ngspice()`, `ngspice(x)`. The device-only variant is excluded.
    An absent or unreadable technology yields [] (the caller refuses).
    """
    styles: List[str] = []
    section = None
    for line in _tech_text(tech).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if section is None and re.fullmatch(r"[A-Za-z_]\w*", s):
            section = s
            continue
        if s == "end":
            section = None
            continue
        if section != "extract":
            continue
        m = _STYLE_RE.match(s)
        if not m or not m.group(1).startswith("ngspice"):
            continue
        base = m.group(1)
        if m.group(2):
            for v in re.findall(r"\(([^)]*)\)", m.group(2)):
                if v.strip() != _DEVICE_ONLY_VARIANT:
                    styles.append(f"{base}({v.strip()})")
        elif base not in styles:
            styles.append(base)
    return styles


def subckt_ports(text: str, name: str) -> Optional[List[str]]:
    for line in _joined_lines(text):
        m = _SUBCKT_RE.match(line)
        if m and m.group(1) == name:
            return [t for t in m.group(2).split() if "=" not in t]
    return None


def model_binding_lines(netlist_text: str, netlist_dir: Path,
                        new_dir: Path) -> List[str]:
    """The netlist's own `.lib`/`.include` cards before its first `.subckt`,
    with every RELATIVE target re-expressed relative to `new_dir` (ngspice
    resolves a relative target against the including file's directory)."""
    out: List[str] = []
    for line in _joined_lines(netlist_text):
        if _SUBCKT_RE.match(line):
            break
        m = _LIB_RE.match(line)
        if not m:
            continue
        target = m.group(2).strip("'\"")
        if not os.path.isabs(target):
            target = os.path.relpath((netlist_dir / target).resolve(), new_dir)
        out.append(f"{m.group(1)}{target}{m.group(3)}")
    return out


def post_layout_netlist(block: str, ports: List[str], rcx_text: str,
                        binding: List[str]) -> Tuple[str, Dict[str, str]]:
    """The extracted netlist behind a wrapper with the A3 port order.

    The extracted subcircuit is renamed `<block>__rcx`. Each of its ports that
    IS a declared port name connects to that port; every other port (a split
    node Magic exported) becomes a node private to the wrapper, so no
    extracted resistance is shorted out. A declared port the extraction does
    not expose is refused: the circuit could not be driven as A3 drives it.
    """
    lines = _joined_lines(rcx_text)
    rcx_ports = None
    body: List[str] = []
    for line in lines:
        m = _SUBCKT_RE.match(line)
        if m and m.group(1) == block and rcx_ports is None:
            rcx_ports = m.group(2).split()
            body.append(f".subckt {block}__rcx {' '.join(rcx_ports)}")
            continue
        body.append(line)
    if rcx_ports is None:
        raise ValueError(f"A7_RCX_NO_SUBCKT: the extracted netlist defines no "
                         f".subckt {block}")
    missing = [p for p in ports if p not in rcx_ports]
    if missing:
        raise ValueError(f"A7_RCX_PORT_MISSING: declared port(s) {missing} are "
                         f"not ports of the extracted .subckt {block}")
    mapping: Dict[str, str] = {}
    conn = []
    for i, p in enumerate(rcx_ports):
        node = p if p in ports else f"rcx_int_{i}"
        mapping[p] = node
        conn.append(node)
    text = "\n".join(
        [f"* post-layout netlist for {block}: LibreLane {RCX_STEP} output "
         f"behind a wrapper with the A3 port order ({PRODUCER})"]
        + binding + [""] + body + [""]
        + [f".subckt {block} {' '.join(ports)}",
           f"xrcx {' '.join(conn)} {block}__rcx", f".ends {block}", ""])
    return text, mapping


def post_layout_testbench(tb_text: str, block: str, post_name: Optional[str],
                          tb_dir: Path, new_dir: Path) -> str:
    """The A3 testbench relocated to `new_dir`, with its one include of
    `<block>.sp` pointed at `post_name` (the post-layout netlist) — or, when
    `post_name` is None, at the delivered netlist itself (the pre-layout run,
    kept out of the block directory other readers glob). Every other relative
    card is re-expressed for `new_dir`. Refused unless the block netlist is
    included exactly once."""
    hits = 0
    out = []
    for line in tb_text.splitlines():
        m = _LIB_RE.match(line)
        if m:
            target = m.group(2).strip("'\"")
            if Path(target).name == f"{block}.sp":
                hits += 1
                if post_name is None:
                    post_name = os.path.relpath(
                        (tb_dir / target).resolve(), new_dir)
                out.append(f"{m.group(1)}{post_name}{m.group(3)}")
                continue
            if not os.path.isabs(target):
                target = os.path.relpath((tb_dir / target).resolve(), new_dir)
            out.append(f"{m.group(1)}{target}{m.group(3)}")
            continue
        out.append(line)
    if hits != 1:
        raise ValueError(f"A7_TB_INCLUDE_AMBIGUOUS: the A3 testbench includes "
                         f"{block}.sp {hits} time(s); exactly one is required")
    return "\n".join(out) + "\n"


def compare(pre: Dict[str, Optional[float]], post: Dict[str, Optional[float]],
            style: str, not_compared: Optional[Dict[str, str]] = None,
            missing: Optional[List[str]] = None) -> List[dict]:
    """One row per measurement both runs produced. NOTHING IS DROPPED
    SILENTLY: a measurement with no pre-layout value, or taken out of the post
    deck because its net is absent after extraction (`not_compared` on the
    way in), is written to `not_compared` with its reason; one the post run
    lacks for any other reason is appended to `missing` — the caller refuses
    on it."""
    rows = []
    skipped = not_compared if not_compared is not None else {}
    lost = missing if missing is not None else []
    for name in sorted(pre):
        a = pre[name]
        b = post.get(name)
        if a is None:
            skipped[name] = "no pre-layout value (the A3 testbench's own " \
                            "run did not produce it)"
            continue
        if name in skipped:
            continue
        if b is None:
            lost.append(name)
            continue
        row = {"name": f"{name}@{style}", "metric": name, "extraction_style": style,
               "pre_value": a, "post_value": b}
        if a != 0:
            row["delta_pct"] = 100.0 * (b - a) / abs(a)
        rows.append(row)
    return rows


#: SPICE magnitude suffixes, longest first (`meg` before `m`).
_SPICE_SCALE = (("meg", 1e6), ("mil", 25.4e-6), ("t", 1e12), ("g", 1e9),
                ("k", 1e3), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9),
                ("p", 1e-12), ("f", 1e-15), ("a", 1e-18))
_NUM_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)([a-z]*)$",
                     re.I)


def spice_number(text: str) -> Optional[float]:
    """A SPICE value (`115.384u`, `0.11538m`, `2e-6`) as a float, or None."""
    m = _NUM_RE.match((text or "").strip().strip("'\""))
    if not m:
        return None
    val, suf = float(m.group(1)), m.group(2).lower()
    for s, k in _SPICE_SCALE:
        if suf.startswith(s):
            return val * k
    return val


def _subckt_body(text: str, name: str) -> Optional[List[str]]:
    body: Optional[List[str]] = None
    for line in _joined_lines(text):
        m = _SUBCKT_RE.match(line)
        if m and body is None and m.group(1) == name:
            body = []
            continue
        if body is not None:
            if re.match(r"^\s*\.ends\b", line, re.I):
                return body
            body.append(line)
    return body


def device_instances(text: str, name: str) -> List[dict]:
    """Every PDK-device instance (an `X` card whose model is not a subcircuit
    of the same file) in `.subckt name`: `{model, w, l, m}` in SI units."""
    defined = {m.group(1).lower() for m in
               (_SUBCKT_RE.match(ln) for ln in _joined_lines(text)) if m}
    out: List[dict] = []
    for line in _subckt_body(text, name) or []:
        toks = line.split()
        if not toks or toks[0][0] not in "xX":
            continue
        pos = [t for t in toks[1:] if "=" not in t]
        if not pos or pos[-1].lower() in defined:
            continue
        params = {k.lower(): v for k, v in
                  (t.split("=", 1) for t in toks[1:] if "=" in t)}
        out.append({"model": pos[-1].lower(),
                    "w": spice_number(params.get("w", "")),
                    "l": spice_number(params.get("l", "")),
                    "m": spice_number(params.get("m", "1")) or 1.0})
    return out


def summed_device_models(tech_text: str) -> set:
    """Models the Magic technology extracts as MOS devices (`device mosfet`
    or `device msubcircuit`): their fingers may be split, so their width is
    compared as a sum. Read from the technology's own `extract` section."""
    out = set()
    for line in (tech_text or "").splitlines():
        t = line.split()
        if len(t) >= 3 and t[0] == "device" and t[1] in ("mosfet",
                                                          "msubcircuit"):
            out.add(t[2].lower())
    return out


def _close(a: Optional[float], b: Optional[float], rel: float = 1e-3) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-30)


def device_inventory(a3: List[dict], rcx: List[dict], summed: set) -> dict:
    """Compare two device inventories (see the module docstring). Values are
    matched with a 1e-3 relative tolerance: Magic writes 5 significant
    digits (`115.384u` comes back `0.11538m`)."""
    diffs: List[str] = []
    models = sorted({d["model"] for d in a3} | {d["model"] for d in rcx})
    for model in models:
        mine = [d for d in a3 if d["model"] == model]
        theirs = [d for d in rcx if d["model"] == model]
        if model in summed:
            def by_l(devs):
                acc: List[List[float]] = []
                for d in devs:
                    wm = (d["w"] or 0.0) * d["m"]
                    for row in acc:
                        if _close(row[0], d["l"]):
                            row[1] += wm
                            break
                    else:
                        acc.append([d["l"], wm])
                return sorted(acc, key=lambda r: (r[0] is None, r[0] or 0))
            x, y = by_l(mine), by_l(theirs)
            if len(x) != len(y) or not all(
                    _close(p[0], q[0]) and _close(p[1], q[1])
                    for p, q in zip(x, y)):
                diffs.append(f"{model}: sum(w*m) per l A3={x} extracted={y}")
        else:
            def expand(devs):
                return sorted(((d["w"] or 0.0), (d["l"] or 0.0))
                              for d in devs for _ in range(int(round(d["m"]))))
            x, y = expand(mine), expand(theirs)
            if len(x) != len(y) or not all(
                    _close(p[0], q[0]) and _close(p[1], q[1])
                    for p, q in zip(x, y)):
                diffs.append(f"{model}: (w, l) multiset A3={len(x)} "
                             f"extracted={len(y)} device(s) differ")
    return {"result": "MISMATCH" if diffs else "MATCH",
            "a3_devices": len(a3), "extracted_devices": len(rcx),
            "summed_models": sorted(summed & set(models)),
            "differences": diffs}


def rcx_nets(rcx_text: str, block: str) -> Tuple[set, set]:
    """(ports, internal nets) of the extracted `.subckt block`, lower-cased."""
    ports: set = set()
    nets: set = set()
    for line in _joined_lines(rcx_text):
        m = _SUBCKT_RE.match(line)
        if m and m.group(1) == block:
            ports = {t.lower() for t in m.group(2).split() if "=" not in t}
            break
    for line in _subckt_body(rcx_text, block) or []:
        toks = line.split()
        if not toks or toks[0][0] in "*.+":
            continue
        kind = toks[0][0].lower()
        pos = [t for t in toks[1:] if "=" not in t]
        if kind == "x":
            nets.update(t.lower() for t in pos[:-1])
        elif kind in "rcl":
            nets.update(t.lower() for t in pos[:2])
        elif kind in "mdq":
            nets.update(t.lower() for t in pos[:-1])
    return ports, nets - ports


def dut_instances(tb_text: str, block: str) -> List[str]:
    """Instance names of `block` in a testbench, lower-cased."""
    out = []
    for line in _joined_lines(tb_text):
        toks = line.split()
        if toks and toks[0][0] in "xX":
            pos = [t for t in toks[1:] if "=" not in t]
            if pos and pos[-1] == block:
                out.append(toks[0].lower())
    return out


_PROBE_RE = re.compile(r"\bv\(\s*([A-Za-z_][\w]*)\.([^\s,()]+)\s*\)", re.I)
_MEAS_NAME_RE = re.compile(r"^\s*\.?meas(?:ure)?\s+\w+\s+(\w+)", re.I)
_LET_RE = re.compile(r"^\s*let\s+(\w+)\s*=(.*)$", re.I)
_ECHO_VAR_RE = re.compile(r"\$&(\w+)")


def remap_probes(tb_text: str, block: str, ports: set, internal: set,
                 mapping: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
    """The post-layout testbench's internal-node probes, pointed into the
    extracted body. Returns (text, {measurement: reason}) where the second
    maps every measurement taken out because its net is absent."""
    duts = set(dut_instances(tb_text, block))
    low_map = {k.lower(): v for k, v in mapping.items()}
    absent: set = set()

    def sub(m: "re.Match") -> str:
        inst, node = m.group(1), m.group(2)
        if inst.lower() not in duts:
            return m.group(0)
        n = node.lower()
        if n in low_map and n in ports:
            return f"v({inst}.{low_map[n]})"
        if n in internal:
            return f"v({inst}.xrcx.{node})"
        absent.add(f"{inst.lower()}.{n}")
        return m.group(0)

    dropped: Dict[str, str] = {}
    out: List[str] = []
    for line in tb_text.splitlines():
        new = _PROBE_RE.sub(sub, line)
        gone = [f"{mm.group(1).lower()}.{mm.group(2).lower()}"
                for mm in _PROBE_RE.finditer(line)
                if f"{mm.group(1).lower()}.{mm.group(2).lower()}" in absent]
        low = line.strip().lower()
        if gone and low.startswith(".save"):
            keep = [t for t in new.split()[1:]
                    if not any(g in t.lower() for g in gone)]
            new = ".save " + " ".join(keep) if keep else "* " + line
        elif gone:
            mm = _MEAS_NAME_RE.match(line)
            lm = _LET_RE.match(line)
            name = (mm.group(1) if mm else lm.group(1) if lm else None)
            if name:
                dropped[name.lower()] = (
                    f"probes {', '.join(sorted(set(gone)))}: the net is absent "
                    f"from the extracted netlist")
            new = "* A7: net absent after extraction: " + line
        else:
            lm = _LET_RE.match(line)
            if lm and any(re.search(rf"\b{re.escape(d)}\b", lm.group(2), re.I)
                          for d in dropped):
                dropped[lm.group(1).lower()] = "depends on a measurement " \
                    "whose net is absent after extraction"
                new = "* A7: depends on an absent net: " + line
            elif low.startswith("echo") and any(
                    v.lower() in dropped for v in _ECHO_VAR_RE.findall(line)):
                out_keys: dict = {}
                new = _echo_without(line, dropped, out_keys)
                for k in out_keys:
                    dropped.setdefault(k, "echoes a measurement whose net is "
                                          "absent after extraction")
        out.append(new)
    return "\n".join(out) + ("\n" if tb_text.endswith("\n") else ""), dropped


def _echo_without(line: str, dropped: Dict[str, str], keys: dict) -> str:
    """An `echo "MEAS k1=" $&v1 " k2=" $&v2` card without the pairs whose
    variable was taken out; the removed keys are recorded in `keys`."""
    parts = re.split(r"(\$&\w+)", line)
    out = [parts[0]]
    i = 1
    while i < len(parts):
        var, text = parts[i][2:], parts[i + 1] if i + 1 < len(parts) else ""
        if var.lower() in dropped:
            km = re.search(r"(\w+)=\s*\"?\s*$", out[-1])
            if km:
                keys[km.group(1).lower()] = True
                out[-1] = out[-1][:km.start()] + out[-1][km.end():]
            out.append(text)
        else:
            out.extend([parts[i], text])
        i += 2
    return "".join(out)


def layout_tech(bdir: Path) -> Path:
    """The Magic technology A5 drew the block with, from its own record
    (`layout_provenance.json` -> `pdk_sources.magic_tech`). The PDK and its
    root follow from the path: `<pdk_root>/<pdk>/libs.tech/magic/<tech>`."""
    lay = bdir / "layout_provenance.json"
    try:
        tech = Path(json.loads(lay.read_text())["pdk_sources"]["magic_tech"])
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ValueError(f"{lay.name} names no pdk_sources.magic_tech "
                         f"({exc})") from exc
    if len(tech.parents) < 4 or tech.parents[1].name != "libs.tech":
        raise ValueError(f"{tech} is not <pdk_root>/<pdk>/libs.tech/magic/…")
    return tech


def _style_slug(style: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", style).strip("_") or "default"


# ── how long each simulation runs, and for how long it may (T130) ─────────
#: WHY THE RUN IS CUT TO WHAT IT MEASURES. MEASURED (lane mig109, 8hd-3,
#: delta_sigma on ihp-sg13g2): the A3 testbench asks for `tran 5n 28673000n`
#: -- 28,673 clocks of a 1 MHz modulator, a record `record_constraints` sized
#: for the A4 GRADED measurement -- while every windowed `meas` card it
#: carries reads `from=523240n to=1025000n`, about 500 clocks. The pre-layout
#: run alone went 32.5 h without finishing, with five post-layout styles
#: queued behind it, because it ran ~28x past the last point any card reads.
#: A7 compares what the deck MEASURES, so the transient stops at the end of
#: the last measurement window plus a stated settle margin, unless a card
#: genuinely needs a longer record (named, with its source, below).
#:
#: The margin is ONE period of the deck's own sample clock (the one top-level
#: pulse source), so the window's last sample is an interior point of the
#: record and not the run's final breakpoint; a deck with no such clock gets
#: `_SETTLE_FRACTION` of the window end instead.
_SETTLE_CLOCKS = 1
_SETTLE_FRACTION = 0.01

#: The budget each A7 simulation is given when the block declares none: a
#: floor for start-up (model load, operating point) plus a per-clock cost.
#: MEASURED on 8hd-3 (32 cores, load < 3, image vibeic-eda 0.3.79): the
#: delta_sigma pre-layout deck advances ~9.2 s of wall per simulated 1 us
#: clock. The default allows 30 s per clock -- about three times that, because
#: an extracted netlist carries hundreds of R and C the pre-layout run does
#: not -- and applies the SAME budget to the pre deck and every post deck, so
#: no style is given more time than another. A block that knows better states
#: `simulation_budget_s` (seconds per simulation) in its `spec.json`.
BUDGET_FLOOR_S = 600
BUDGET_S_PER_CLOCK = 30.0
SPEC_BUDGET_KEY = "simulation_budget_s"

#: The producer's fourth outcome: a simulation spent its budget. NOT a FAIL
#: (nothing about the circuit was learned) and not an environment refusal (the
#: tool ran): the step is NOT_MEASURED, reason `budget_exhausted`.
EX_BUDGET_EXHAUSTED = 75
NOT_MEASURED_TOKEN = "NOT_MEASURED:"

_TRAN_CARD_RE = re.compile(r"^(\s*\.?tran\s+)(\S+)(\s+)(\S+)(.*)$", re.I)
_TRAN_MEAS_RE = re.compile(r"^\s*\.?meas(?:ure)?\s+tran\s+(\w+)\s+(.*)$", re.I)
_KV_RE = re.compile(r"\b(from|to|at|td)\s*=\s*(\S+)", re.I)
_EVENT_RE = re.compile(r"\b(trig|targ|when)\b", re.I)
#: Cards anchored at the END of the run: moving the end changes what they read.
_END_ANCHORED_RE = re.compile(r"^\s*\.?(fourier|four)\b", re.I)
#: Cards that dump the record over whatever span the run has.
_RECORD_RE = re.compile(r"^\s*\.?(wrdata|write|print|plot)\b", re.I)
#: ngspice's own progress line in batch mode: `Reference value :  4.05e-06`.
_REFERENCE_RE = re.compile(r"Reference value\s*:\s*([-+0-9.eE]+)")


def _seconds(tok: str) -> Optional[float]:
    import analog_adc_enob_corner_check as _enob
    return _enob._si(tok)


def _ns_token(seconds: float) -> str:
    ns = seconds * 1e9
    return (f"{int(round(ns))}n" if abs(ns - round(ns)) < 1e-6
            else f"{ns:.6f}".rstrip("0").rstrip(".") + "n")


def measurement_span(tb_text: str) -> dict:
    """What the deck's own cards need of the transient, in seconds.

    Every `meas tran` card is one of three kinds:
      * WINDOWED -- it names where it stops reading (`to=`, or `at=`); the
        latest such end sets the stop, plus the settle margin;
      * SPAN-FOLLOWING -- it reads from `from=` (or 0) to wherever the run
        ends (the `railx_*` rail extremes are these): it takes the run's span
        and never sets it, so pre and post read the SAME span;
      * EVENT-LOCATED -- `trig`/`targ`/`when` with no stated end: where it
        reads depends on the circuit, so the deck's declared stop stands.
    An end-anchored card (`fourier`) also keeps the declared stop: it reads
    the LAST periods of the run, so a shorter run would change its subject.
    `wrdata`/`print` dump whatever span the run has; A7 compares none of them,
    so they follow the span and are listed as doing so."""
    lines = _joined_lines(tb_text or "")
    declared = None
    for line in lines:
        m = _TRAN_CARD_RE.match(line)
        if m:
            declared = _seconds(m.group(4))
            break
    windowed: List[dict] = []
    following: List[str] = []
    holds: List[dict] = []
    records: List[str] = []
    for line in lines:
        if line.lstrip().startswith("*"):
            continue
        m = _TRAN_MEAS_RE.match(line)
        if m:
            name, rest = m.group(1).lower(), m.group(2)
            kv = {k.lower(): v for k, v in _KV_RE.findall(rest)}
            end = next((_seconds(kv[k]) for k in ("to", "at")
                        if k in kv and _seconds(kv[k]) is not None), None)
            if end is not None:
                windowed.append({"meas": name, "end_s": end})
            elif _EVENT_RE.search(rest):
                holds.append({"card": name, "reason": (
                    "event-located (trig/targ/when) with no stated end: "
                    "where it reads depends on the circuit")})
            else:
                following.append(name)
            continue
        if _END_ANCHORED_RE.match(line):
            holds.append({"card": line.split()[0].lower(), "reason": (
                "anchored at the end of the run: a shorter run changes the "
                "periods it analyses")})
        elif _RECORD_RE.match(line):
            records.append(line.split()[0].lower())
    span = {"declared_stop_s": declared, "windowed": windowed,
            "span_following": following, "record_dumps": records,
            "holds_declared_record": holds}
    if declared is None:
        span.update(stop_s=None, rule="no_transient_card")
        return span
    if holds:
        span.update(stop_s=declared, rule="a_card_needs_the_declared_record")
        return span
    if not windowed:
        span.update(stop_s=declared, rule="no_windowed_measurement")
        return span
    import analog_adc_enob_corner_check as _enob
    last = max(w["end_s"] for w in windowed)
    card = _enob.sample_clock_card(tb_text or "")
    if card is not None:
        margin = _SETTLE_CLOCKS * card[1]
        source = f"{_SETTLE_CLOCKS} period of the deck's sample clock card"
    else:
        margin = _SETTLE_FRACTION * last
        source = (f"{_SETTLE_FRACTION:g} of the last window end (the deck "
                  f"names no single top-level pulse clock)")
    stop = last + margin
    span.update(last_window_end_s=last, settle_margin_s=margin,
                settle_margin_source=source,
                last_window_meas=[w["meas"] for w in windowed
                                  if w["end_s"] == last])
    if stop >= declared:
        span.update(stop_s=declared,
                    rule="declared_record_already_within_the_last_window")
    else:
        span.update(stop_s=stop, rule="last_measurement_window_plus_settle")
    return span


def bound_transient(tb_text: str) -> Tuple[str, dict]:
    """The testbench with its transient stopped where `measurement_span`
    says, and the span record. Only the stop token is rewritten; the step and
    anything after the stop survive."""
    span = measurement_span(tb_text)
    if span.get("stop_s") is None or span["stop_s"] == span["declared_stop_s"]:
        return tb_text, span
    out, done = [], False
    for line in (tb_text or "").splitlines():
        m = None if done else _TRAN_CARD_RE.match(line)
        if m:
            line = (m.group(1) + m.group(2) + m.group(3)
                    + _ns_token(span["stop_s"]) + m.group(5))
            done = True
        out.append(line)
    return "\n".join(out) + "\n", span


def simulation_budget(spec: Optional[dict], tb_text: str,
                      stop_s: Optional[float]) -> Tuple[float, dict]:
    """(seconds, source) for ONE A7 simulation. Never 0: 0 is "no deadline".

    The block's `spec.json` `simulation_budget_s` wins when it is a positive
    number; otherwise the default is derived from the clocks the (bounded)
    deck simulates; a deck with no clock card falls back to A4's deck-scaled
    deadline, named as such."""
    declared = (spec or {}).get(SPEC_BUDGET_KEY)
    if isinstance(declared, (int, float)) and not isinstance(declared, bool) \
            and declared > 0:
        return float(declared), {"source": f"spec.json:{SPEC_BUDGET_KEY}"}
    import analog_adc_enob_corner_check as _enob
    card = _enob.sample_clock_card(tb_text or "")
    if card is not None and stop_s:
        clocks = stop_s / card[1]
        return (BUDGET_FLOOR_S + clocks * BUDGET_S_PER_CLOCK,
                {"source": "default_per_clock", "clocks": clocks,
                 "floor_s": BUDGET_FLOOR_S,
                 "s_per_clock": BUDGET_S_PER_CLOCK})
    import analog_real_corner_sweep as ars
    return float(ars.sim_deadline_s(tb_text)), {
        "source": "analog_real_corner_sweep.sim_deadline_s (no clock card)"}


def simulated_time_reached_s(raw: str) -> Optional[float]:
    """The last `Reference value` ngspice printed: how far the transient got
    before it was stopped. None when it printed none."""
    last = None
    for m in _REFERENCE_RE.finditer(raw or ""):
        last = m.group(1)
    try:
        return float(last) if last is not None else None
    except ValueError:
        return None


# ── the producer ───────────────────────────────────────────────────────────
def _not_measured(record: dict, out: Path, deck: Path, sim: dict,
                  project: Path) -> int:
    """A simulation spent its budget: NOT_MEASURED with the numbers a reader
    needs to act on it -- never a hang and never a FAIL."""
    span, budget = record.get("transient_span", {}), record.get("budget", {})
    reached, requested = sim["reached_s"], span.get("stop_s")
    need = (sim["wall_s"] * requested / reached
            if reached and requested else None)
    record.update({
        "result": "NOT_MEASURED", "reason_class": "budget_exhausted",
        "rule": "A7_SIM_BUDGET_EXHAUSTED",
        "budget_exhausted": {
            "deck": str(deck.relative_to(project)), "log": sim["log"],
            "simulated_time_reached_s": reached,
            "simulated_time_requested_s": requested,
            "wall_s": round(sim["wall_s"], 1),
            "budget_s": budget.get("seconds"),
            "budget_source": budget.get("source"),
            "remedy": (
                f"state `{SPEC_BUDGET_KEY}` in phase3/analog/"
                f"{record['block']}/spec.json at or above the wall this run "
                f"extrapolates to"
                + (f" (~{need:.0f} s at the rate it reached)" if need else "")
                + ", or re-run on a host with less load; the deck's span is "
                  "already cut to what it measures")}})
    detail = (f"{deck.name}: simulated "
              f"{reached if reached is not None else 'an unread'} s of "
              f"{requested} s requested in {sim['wall_s']:.0f} s wall "
              f"(budget {budget.get('seconds')} s from "
              f"{budget.get('source')})")
    record["detail"] = detail
    write_json(out, record)
    print(f"{NOT_MEASURED_TOKEN} {PRODUCER} A7_SIM_BUDGET_EXHAUSTED: {detail}",
          file=sys.stderr)
    return EX_BUDGET_EXHAUSTED


def _refuse(record: dict, out: Path, rule: str, detail: str, rc: int) -> int:
    record.update({"result": "REFUSED" if rc == 1 else "NOT_PRODUCED",
                   "rule": rule, "detail": detail})
    write_json(out, record)
    token = _pc.HONEST_GAP_TOKEN if rc == 2 else (
        _pc.ENV_REFUSED_TOKEN if rc == _pc.EX_ENV_REFUSED else "FAIL:")
    print(f"{token} {PRODUCER} {rule}: {detail}", file=sys.stderr)
    return rc


def run(project: Path, block: str, container: str, image: str,
        styles: Optional[List[str]] = None) -> int:
    import librelane_contract as lc
    import magic_extract_spice_emit as mx
    import analog_real_corner_sweep as ars
    import _designs_root as dr

    bdir = project / "phase3" / "analog" / block
    # NON-INTERFERENCE: the working decks never land under phase3/analog,
    # which A3's gates rglob. Only the two records below are the block's.
    work = project / "phase3" / "librelane" / "analog" / block / "a7_resim"
    record_path = bdir / "a7_post_layout.json"
    record: dict = {"producer": PRODUCER, "schema": 1, "block": block,
                    "step": STEP, "image": image, "extraction_tool": RCX_STEP}
    gds = bdir / f"{block}.gds"
    netlist = bdir / f"{block}.sp"
    tb = bdir / f"tb_{block}.sp"
    lay = bdir / "layout_provenance.json"
    for need, owner in ((gds, "A5"), (lay, "A5"), (netlist, "A3"), (tb, "A3")):
        if not need.is_file():
            return _refuse(record, record_path, "A7_INPUT_ABSENT",
                           f"{need.relative_to(project)} (owed by {owner})", 2)
    try:
        tech = layout_tech(bdir)
    except ValueError as exc:
        return _refuse(record, record_path, "A7_LAYOUT_TECH_UNDECLARED",
                       f"{lay.relative_to(project)}: {exc}", 2)
    # <pdk_root>/<pdk>/libs.tech/magic/<tech>: the layout's own PDK.
    pdk, pdk_root = tech.parents[2].name, str(tech.parents[3])
    record.update({"pdk": pdk, "pdk_root": pdk_root, "magic_tech": str(tech)})
    ports = subckt_ports(netlist.read_text(errors="replace"), block)
    if not ports:
        return _refuse(record, record_path, "A7_NETLIST_NO_SUBCKT",
                       f"{netlist.name} defines no .subckt {block}", 2)
    work.mkdir(parents=True, exist_ok=True)
    if styles is None:
        # The technology is read inside the image: it is the image's PDK.
        cp = ars._docker(container, f"cat {tech} "
                         f"$(sed -n 's/^ *include  *//p' {tech} | "
                         f"sed 's#^#{tech.parent}/#; s#$#.tech#') 2>/dev/null")
        if cp.returncode != 0 or not cp.stdout.strip():
            return _refuse(record, record_path, "A7_TECH_UNREADABLE",
                           f"{tech} could not be read in {container}",
                           _pc.EX_ENV_REFUSED)
        flat = work / "magic_tech.flat"
        write_text(flat, cp.stdout)
        styles = extraction_styles(flat)
    if not styles:
        return _refuse(record, record_path, "A7_NO_EXTRACTION_STYLE",
                       f"{tech} declares no ngspice extraction style", 1)
    record["extraction_styles"] = styles
    flat_tech = work / "magic_tech.flat"
    summed = summed_device_models(
        flat_tech.read_text(errors="replace") if flat_tech.is_file()
        else _tech_text(tech))
    a3_devices = device_instances(netlist.read_text(errors="replace"), block)

    host_root = dr.resolve_host_root(project, container)
    # ONE span and ONE budget for the pre deck and every post deck: the
    # comparison reads the same window in each, and no style gets more time.
    tb_text, span = bound_transient(tb.read_text(errors="replace"))
    net_text = netlist.read_text(errors="replace")
    spec_path = bdir / "spec.json"
    try:
        spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
    except (OSError, ValueError):
        spec = {}
    budget_s, budget_src = simulation_budget(
        spec if isinstance(spec, dict) else {}, tb_text, span.get("stop_s"))
    record["transient_span"] = span
    record["budget"] = {"seconds": round(budget_s, 1), **budget_src,
                        "applies_to": "each simulation (pre and every post)"}

    def simulate(deck: Path) -> dict:
        t0 = time.monotonic()
        ok, meas, raw, status = ars._run_ngspice(
            container, ars._container_path(container, host_root, deck),
            deck_text=deck.read_text(errors="replace"), deadline_s=budget_s)
        wall = time.monotonic() - t0
        write_text(deck.with_suffix(".ngspice.log"), raw or "")
        return {"ok": ok, "meas": meas, "status": status, "wall_s": wall,
                "stopped": bool((status or {}).get("stopped")),
                "reached_s": simulated_time_reached_s(raw),
                "log": str(deck.with_suffix(".ngspice.log").relative_to(project))}

    try:
        pre_tb = work / f"tb_{block}_pre.sp"
        write_text(pre_tb, post_layout_testbench(tb_text, block, None, bdir,
                                                 work))
    except ValueError as exc:
        return _refuse(record, record_path, str(exc).split(":", 1)[0],
                       str(exc), 1)
    pre = simulate(pre_tb)
    record["pre_wall_s"] = round(pre["wall_s"], 1)
    if pre["stopped"]:
        return _not_measured(record, record_path, pre_tb, pre, project)
    if not pre["ok"]:
        return _refuse(record, record_path, "A7_PRE_SIM_FAILED",
                       f"the A3 testbench did not simulate ({pre['log']})", 1)
    record["pre"] = {"testbench": str(pre_tb.relative_to(project)),
                     "relocated_from": str(tb.relative_to(project)),
                     "measurements": pre["meas"], "log": pre["log"]}

    specs: List[dict] = []
    corners: List[dict] = []
    not_compared: Dict[str, str] = {}
    state_in = work / "state_in.json"
    write_json(state_in, {"gds": str(gds.resolve()), "metrics": {}})
    for style in styles:
        slug = _style_slug(style)
        src = work / f"rcx_{slug}.src.json"
        write_json(src, {"meta": {"version": 2, "step": RCX_STEP},
                         "DESIGN_NAME": block, "PDK": pdk,
                         "MAGIC_RCX_EXTRACT_STYLE": style})
        try:
            cfg = lc.resolve_step_config(project, image, src,
                                         work / f"rcx_{slug}.json",
                                         pdk_root=pdk_root)
            resolved = json.loads(cfg.read_text())
            if resolved.get("MAGIC_TECH") not in (None, str(tech)):
                return _refuse(record, record_path, "A7_TECH_MISMATCH",
                               f"LibreLane resolved MAGIC_TECH="
                               f"{resolved.get('MAGIC_TECH')} but A5 drew with "
                               f"{tech}", 1)
            resolved.setdefault("meta", {})["step"] = RCX_STEP
            write_json(cfg, resolved)
            folders = lc.run_chain(project, image, [(RCX_STEP, cfg, state_in)],
                                   namespace=f"analog/{block}/a7_{slug}",
                                   pdk_root=pdk_root)
        except lc.Refusal as exc:
            rc = _pc.EX_ENV_REFUSED if exc.code in (
                "LL_IMAGE_INCAPABLE", "LL_CONFIG_RESOLVE_FAILED",
                *lc.TIME_REFUSALS) else 1
            return _refuse(record, record_path, exc.code, str(exc), rc)
        folder = folders[-1]
        state = json.loads((folder / "state_out.json").read_text())
        rcx = Path(state.get("spice_rcx") or "")
        if not rcx.is_file():
            return _refuse(record, record_path, "A7_RCX_NO_NETLIST",
                           f"{RCX_STEP} state names no SPICE_RCX view "
                           f"({folder})", 1)
        rcx_text = rcx.read_text(errors="replace")
        audit = mx.audit_extracted_netlist(rcx_text, spice_file=str(rcx))
        corner = {"extraction_style": style,
                  "step_dir": str(folder.relative_to(project)),
                  "extracted_netlist": str(rcx.relative_to(project)),
                  "depth": audit.depth, "audit_passed": audit.passed,
                  "resistors": audit.summary.get("resistors"),
                  "capacitors": audit.summary.get("capacitors")}
        corners.append(corner)
        if not audit.passed:
            record["corners"] = corners
            return _refuse(record, record_path, "A7_RCX_PARASITIC_FREE",
                           f"{rcx.name}: depth {audit.depth} — a re-simulation "
                           f"of it is the pre-layout circuit again", 1)
        inventory = device_inventory(a3_devices,
                                     device_instances(rcx_text, block), summed)
        corner["device_inventory"] = inventory
        if inventory["result"] != "MATCH":
            record["corners"] = corners
            return _refuse(record, record_path,
                           "A7_RCX_DEVICE_INVENTORY_MISMATCH",
                           f"{rcx.name}: {'; '.join(inventory['differences'])}",
                           1)
        try:
            post_text, mapping = post_layout_netlist(
                block, ports, rcx_text,
                model_binding_lines(net_text, bdir, work))
            post_net = work / f"{block}_post_{slug}.sp"
            write_text(post_net, post_text)
            post_tb = work / f"tb_{block}_post_{slug}.sp"
            rports, rinternal = rcx_nets(rcx_text, block)
            post_tb_text, dropped = remap_probes(
                post_layout_testbench(tb_text, block, post_net.name, bdir,
                                      work),
                block, rports, rinternal, mapping)
            write_text(post_tb, post_tb_text)
        except ValueError as exc:
            record["corners"] = corners
            rule = str(exc).split(":", 1)[0]
            return _refuse(record, record_path, rule, str(exc), 1)
        corner["post_layout_netlist"] = str(post_net.relative_to(project))
        corner["private_nodes"] = sum(1 for p, n in mapping.items() if p != n)
        post = simulate(post_tb)
        corner["post_log"] = post["log"]
        corner["post_wall_s"] = round(post["wall_s"], 1)
        corner["post_measurements"] = post["meas"]
        if post["stopped"]:
            record["corners"] = corners
            return _not_measured(record, record_path, post_tb, post, project)
        if not post["ok"]:
            record["corners"] = corners
            return _refuse(record, record_path, "A7_POST_SIM_FAILED",
                           f"{post_tb.name} did not simulate ({post['log']})", 1)
        skipped: Dict[str, str] = dict(dropped)
        lost: List[str] = []
        specs.extend(compare(pre["meas"], post["meas"], style, skipped, lost))
        if skipped:
            corner["not_comparable_post_layout"] = skipped
            not_compared.update({f"{k}@{style}": v for k, v in skipped.items()})
        if lost:
            record["corners"] = corners
            return _refuse(record, record_path, "A7_POST_MEASUREMENT_MISSING",
                           f"{post_tb.name}: the pre-layout run measured "
                           f"{lost} and the post-layout run did not, on nets "
                           f"the extraction kept ({post['log']})", 1)
    record["corners"] = corners
    if not specs:
        return _refuse(record, record_path, "A7_NOTHING_COMPARED",
                       "pre and post runs share no numeric measurement", 1)
    typical = corners[0]
    write_json(bdir / "pre_vs_post.json", {
        "block": block,
        "_provenance": {
            "producer": PRODUCER, "schema": 1,
            "extracted_netlist": typical["extracted_netlist"],
            "post_layout_netlist": typical["post_layout_netlist"],
            "extraction_tool": f"LibreLane {RCX_STEP}", "image": image,
            "record": str(record_path.relative_to(project)),
            **({"not_compared": not_compared} if not_compared else {}),
        },
        "extraction_depth": sorted({c["depth"] for c in corners}),
        "specs": specs,
    })
    if not_compared:
        record["not_comparable_post_layout"] = not_compared
    record["result"] = "PRODUCED"
    write_json(record_path, record)
    print(f"[{PRODUCER}] block={block} {len(corners)} extraction style(s), "
          f"{len(specs)} spec row(s) -> phase3/analog/{block}/pre_vs_post.json")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = _pc.ProducerArgumentParser(prog=PRODUCER, description=__doc__.split("\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--block", required=True)
    ap.add_argument("--container", required=True)
    ap.add_argument("--image", required=True,
                    help="the EDA image by digest (LibreLane runs in a fresh "
                         "container of it)")
    ap.add_argument("--style", action="append", default=None,
                    help="extraction style(s); default: every ngspice style "
                         "the layout's Magic technology declares")
    a = ap.parse_args(argv)
    return run(a.project.resolve(), a.block, a.container, a.image, a.style)


if __name__ == "__main__":
    sys.exit(main())
