#!/usr/bin/env python3
"""Stream a single transient's declared measurements from ngspice's raw pipe.

The eligibility check is deliberately narrow.  A deck with an analysis or
control command we cannot reproduce keeps the ordinary ngspice route.  The
original deck remains the measurement definition and is never rewritten on
disk; only a simulator-only sibling is made for ``ngspice -b -r``.
"""
from __future__ import annotations

import ast
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

import numpy as np

_CARD = re.compile(r"(?im)^\s*\.control\s*$([\s\S]*?)^\s*\.endc\s*$")
_TRAN = re.compile(r"(?i)^tran\s+(\S+)\s+(\S+)(?:\s+.*)?$")
_MEAS = re.compile(r"(?i)^meas\s+tran\s+(\w+)\s+(avg|max|min)\s+([vi]\([^)]+\))(.*)$")
_VECTOR = re.compile(r"(?i)[vi]\([^)]+\)")
_WRDATA = re.compile(r"(?i)^wrdata\s+(\S+)\s+([vi]\([^)]+\))\s*$")
_LET = re.compile(r"(?i)^let\s+(\w+)\s*=\s*(.+)$")
_BOUND = re.compile(r"(?i)\b(from|to)\s*=\s*(\S+)")
_SAVE = re.compile(r"(?im)^\s*\.save\s+(.+)$")
_SOURCE = re.compile(r"(?im)^\s*v[\w.$]*\s+(\S+)\s+0\s+(.+?)\s*$")
_SI = re.compile(r"(?i)^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(meg|[munpfkgt])?s?$")
_SCALE = {None: 1., "": 1., "t": 1e12, "g": 1e9, "meg": 1e6,
          "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9,
          "p": 1e-12, "f": 1e-15}


def _si(token):
    m = _SI.fullmatch(token.strip())
    if not m:
        raise ValueError(f"unreadable SPICE scalar: {token!r}")
    return float(m.group(1)) * _SCALE[(m.group(2) or "").lower()]


def plan(deck: str):
    """Return the card-derived stream contract, or a named refusal."""
    blocks = list(_CARD.finditer(deck))
    if len(blocks) != 1:
        return None, "requires_one_control_block"
    if "analog_incremental_decimator: mode=incremental" not in deck:
        return None, "requires_incremental_decode_declaration"
    save_cards = list(_SAVE.finditer(deck))
    if not save_cards:
        return None, "no_declared_save_vectors"
    saved = {v.lower() for card in save_cards
             for v in _VECTOR.findall(card.group(1))}
    lines = []
    tran = None
    measures = []
    exports = []
    lets = []
    echoes = []
    try:
        for raw in blocks[0].group(1).splitlines():
            line = raw.strip()
            if not line or line.startswith(("*", ";")):
                continue
            lines.append(line)
            if m := _TRAN.fullmatch(line):
                if tran is not None:
                    return None, "multiple_transients"
                tran = (m.group(1), m.group(2))
            elif m := _MEAS.fullmatch(line):
                bounds = {k.lower(): _si(v) for k, v in _BOUND.findall(m.group(4))}
                measures.append({"name": m.group(1).lower(),
                                 "kind": m.group(2).lower(),
                                 "vector": m.group(3).lower(),
                                 "from": bounds.get("from", 0.),
                                 "to": bounds.get("to", math.inf)})
            elif m := _WRDATA.fullmatch(line):
                exports.append({"path": m.group(1), "vector": m.group(2).lower()})
            elif m := _LET.fullmatch(line):
                lets.append((m.group(1).lower(), m.group(2)))
            elif line.lower().startswith("echo "):
                echoes.append(line)
            else:
                return None, f"unsupported_control_card:{line.split()[0]}"
        if tran is None or len(exports) != 1 or not measures:
            return None, "incomplete_transient_measurement"
        used = {m["vector"] for m in measures} | {e["vector"] for e in exports}
        if not used <= saved:
            return None, "measured_vector_not_saved"
        # The compact waveform retains threshold states and clock samples. It
        # is valid for the incremental grader named by the deck's own stamp.
        pulses = re.findall(r"(?im)^\s*v\w+\s+\S+\s+\S+\s+pulse\(([^)]+)\)", deck)
        if len(pulses) != 1:
            return None, "sample_clock_not_unique"
        parts = pulses[0].split()
        if len(parts) < 7:
            return None, "sample_clock_unreadable"
        clock = {"first": _si(parts[2]), "period": _si(parts[6]),
                 "high": _si(parts[1])}
        if clock["period"] <= 0:
            return None, "sample_clock_unreadable"
        # The two consumers compare the bitstream against the clock high/2
        # and the declared supply/2. Read the same supply node the density
        # consumer reads; unrelated PWL *time* numbers must never become
        # thresholds (and create millions of false waveform transitions).
        supply = None
        for node, rest in _SOURCE.findall(deck):
            if node.lower() != "vdd":
                continue
            if rest.lower().startswith("pwl"):
                tokens = re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?:meg|[munpfkgt])?", rest, re.I)
                supply = _si(tokens[-1]) if tokens else None
            else:
                supply = _si(rest.split()[0])
        if supply is None or supply <= 0:
            return None, "density_supply_not_declared"
        thresholds = sorted({clock["high"] / 2, supply / 2})
        return {"tran": tran, "stop_s": _si(tran[1]),
                "measures": measures, "exports": exports,
                "lets": lets, "echoes": echoes, "clock": clock,
                "thresholds": thresholds,
                "saved_vectors": sorted(used)}, None
    except (ValueError, IndexError) as exc:
        return None, f"unreadable_measurement_definition:{exc}"


def simulator_deck(deck: str, contract: dict, num_threads: int | None = None):
    """Move the transient outside control and save only measured vectors."""
    text = _CARD.sub("", deck, count=1)
    # A delivered deck may save diagnostic nodes that no declared A4
    # measurement reads. Keep those cards in the original definition, but
    # pass ONLY the vectors actually consumed to ngspice's raw pipe.
    text = _SAVE.sub("", text)
    end = re.search(r"(?im)^\s*\.end\s*$", text)
    if end is None:
        raise ValueError("no .end card")
    controls = (f".control\nset num_threads={num_threads}\n.endc\n"
                if num_threads is not None else "")
    return (text[:end.start()] + controls
            + ".save " + " ".join(contract["saved_vectors"]) + "\n"
            + f".tran {contract['tran'][0]} {contract['tran'][1]}\n"
            + text[end.start():])


def _expr(expr, values):
    def go(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values[node.id.lower()]
        if isinstance(node, ast.BinOp):
            a, b = go(node.left), go(node.right)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            if isinstance(node.op, ast.Div): return a / b
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -go(node.operand)
        raise ValueError("unsupported derived measurement expression")
    return go(ast.parse(expr, mode="eval").body)


def _read_raw(pipe, contract, progress):
    header = []
    with open(pipe, "rb", buffering=1024 * 1024) as f:
        while True:
            line = f.readline()
            if not line:
                raise ValueError("rawfile ended before Binary card")
            header.append(line.decode("ascii", "replace"))
            if line.strip() == b"Binary:":
                break
        h = "".join(header)
        n = re.search(r"(?im)^No\. Variables:\s*(\d+)", h)
        if n is None or not re.search(r"(?im)^Flags:\s*real\b", h):
            raise ValueError("rawfile variable count or real format absent")
        width = int(n.group(1))
        vars_ = re.findall(r"(?m)^\s*(\d+)\s+(\S+)\s+\S+\s*$", h)
        names = {name.lower(): int(i) for i, name in vars_}
        if names.get("time") != 0 or len(vars_) != width:
            raise ValueError("rawfile variable table incomplete")
        for row in contract["measures"]:
            if row["vector"] not in names:
                raise ValueError(f"declared measure vector absent: {row['vector']}")
        export = contract["exports"][0]
        if export["vector"] not in names:
            raise ValueError("declared export vector absent")
        outpath = Path(export["path"])
        outpath.parent.mkdir(parents=True, exist_ok=True)
        stats = {m["name"]: {"value": None, "area": 0., "span": 0.}
                 for m in contract["measures"]}
        previous = None
        rows = 0
        bytes_per_row = 8 * width
        carry = b""
        tick = time.monotonic()
        next_clock = contract["clock"]["first"] + .9 * contract["clock"]["period"]
        period = contract["clock"]["period"]
        levels = np.array(contract["thresholds"])
        last_state = None
        last_written = -math.inf
        last_pair = None
        with outpath.open("w") as dump:
            while True:
                part = f.read(1024 * 1024)
                if not part:
                    break
                blob = carry + part
                end = len(blob) // bytes_per_row * bytes_per_row
                carry = blob[end:]
                if not end:
                    continue
                data = np.frombuffer(blob[:end], dtype="<f8").reshape(-1, width)
                if not np.isfinite(data).all():
                    raise ValueError("rawfile contains nonfinite data")
                times = data[:, 0]
                if previous is not None and times[0] < previous[0]:
                    raise ValueError("rawfile time moved backwards")
                if len(times) > 1 and np.any(np.diff(times) < 0):
                    raise ValueError("rawfile time moved backwards")
                rows += len(times)
                chain = np.vstack((previous, data)) if previous is not None else data
                t = chain[:, 0]
                for m in contract["measures"]:
                    v = chain[:, names[m["vector"]]]
                    lo, hi = m["from"], m["to"]
                    if m["kind"] == "avg":
                        left = np.maximum(t[:-1], lo)
                        right = np.minimum(t[1:], hi)
                        valid = right > left
                        if np.any(valid):
                            a, b = t[:-1][valid], t[1:][valid]
                            va, vb = v[:-1][valid], v[1:][valid]
                            vl = va + (vb - va) * (left[valid] - a) / (b - a)
                            vr = va + (vb - va) * (right[valid] - a) / (b - a)
                            stats[m["name"]]["area"] += float(np.sum((vl + vr) * .5 * (right[valid] - left[valid])))
                            stats[m["name"]]["span"] += float(np.sum(right[valid] - left[valid]))
                    else:
                        mask = (t >= lo) & (t <= hi)
                        if np.any(mask):
                            val = float(np.max(v[mask]) if m["kind"] == "max" else np.min(v[mask]))
                            prior = stats[m["name"]]["value"]
                            stats[m["name"]]["value"] = val if prior is None else (
                                max(prior, val) if m["kind"] == "max" else min(prior, val))
                ev = data[:, names[export["vector"]]]
                state = (ev[:, None] > levels[None, :])
                changed = np.any(state[1:] != state[:-1], axis=1)
                idxs = np.flatnonzero(changed) + 1
                if last_state is None or np.any(state[0] != last_state):
                    idxs = np.r_[0, idxs]
                pairs = [(float(times[i]), float(ev[i])) for i in idxs]
                while next_clock <= float(times[-1]):
                    pos = int(np.searchsorted(times, next_clock, side="right")) - 1
                    value = float(ev[pos]) if pos >= 0 else (float(previous[names[export["vector"]]]) if previous is not None else float(ev[0]))
                    pairs.append((next_clock, value))
                    next_clock += period
                for when, value in sorted(pairs):
                    if when >= last_written:
                        dump.write(f"{when:.17g} {value:.17g}\n")
                        last_written = when
                        last_pair = (when, value)
                previous = data[-1].copy()
                last_state = state[-1].copy()
                if time.monotonic() - tick >= 30:
                    progress.write_text(json.dumps({"rows": rows, "simulated_s": float(times[-1]),
                                                    "updated_unix": time.time()}))
                    dump.flush()
                    tick = time.monotonic()
            # A seekable rawfile patches ``No. Points`` in its header. A pipe
            # cannot seek, so this ngspice build appends the decimal count at
            # EOF. Validate that trailer against points actually consumed.
            if carry and (not carry.isdigit() or int(carry) != rows):
                raise ValueError("rawfile ended inside a point or has a bad count trailer")
            if previous is None or rows < 2:
                raise ValueError("rawfile has no transient points")
            final_t = float(previous[0])
            if final_t > last_written:
                dump.write(f"{final_t:.17g} {float(previous[names[export['vector']]]):.17g}\n")
        progress.write_text(json.dumps({"rows": rows, "simulated_s": final_t,
                                        "updated_unix": time.time(), "complete": True}))
        if final_t + max(1e-12, contract["stop_s"] * 1e-8) < contract["stop_s"]:
            raise ValueError(f"transient stopped at {final_t} before {contract['stop_s']}")
        values = {}
        for m in contract["measures"]:
            s = stats[m["name"]]
            val = s["area"] / s["span"] if m["kind"] == "avg" and s["span"] > 0 else s["value"]
            if val is None or not math.isfinite(val):
                raise ValueError(f"measure {m['name']} has no finite result")
            values[m["name"]] = val
        for name, expr in contract["lets"]:
            values[name] = _expr(expr, values)
        return values, rows, final_t


def run(deck_path: Path, ngspice_bin: str):
    deck = deck_path.read_text()
    contract, refusal = plan(deck)
    if refusal:
        raise ValueError(refusal)
    raw_threads = os.environ.get("VIBEIC_ANALOG_NUM_THREADS", "").strip()
    threads = int(raw_threads) if raw_threads else None
    if threads is not None and not 1 <= threads <= 64:
        raise ValueError("VIBEIC_ANALOG_NUM_THREADS must be 1..64")
    sim_path = deck_path.with_name(deck_path.stem + ".stream.sp")
    sim_path.write_text(simulator_deck(deck, contract, threads))
    fifo = deck_path.with_name(deck_path.stem + ".stream.rawpipe")
    progress = deck_path.with_name(deck_path.stem + ".stream.progress.json")
    log = deck_path.with_name(deck_path.stem + ".stream.ngspice.stdout")
    try:
        fifo.unlink(missing_ok=True)
        os.mkfifo(fifo)
        result = {}
        def reader():
            try:
                result["data"] = _read_raw(fifo, contract, progress)
            except BaseException as exc:
                result["error"] = exc
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        with log.open("w") as stdout:
            proc = subprocess.Popen([ngspice_bin, "-b", "-r", str(fifo), str(sim_path)],
                                    stdout=stdout, stderr=subprocess.STDOUT)
            while proc.poll() is None and thread.is_alive():
                thread.join(.5)
            if thread.is_alive() and proc.poll() is not None:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    os.close(fd)
                except OSError:
                    pass
            if not thread.is_alive() and result.get("error") and proc.poll() is None:
                proc.terminate()
            rc = proc.wait()
            thread.join(timeout=30)
        print(log.read_text(errors="replace"))
        if rc or thread.is_alive() or result.get("error"):
            raise ValueError(f"ngspice_rc={rc}; stream_error={result.get('error')!r}")
        values, rows, final_t = result["data"]
        for name, val in values.items():
            print(f"{name} = {val:.12e}")
        for line in contract["echoes"]:
            mapping = re.findall(r"(?i)([a-z_]\w*)=\"?\s*\$&([a-z_]\w*)", line)
            if mapping:
                print("MEAS " + " ".join(f"{alias}={values[var.lower()]:.12e}" for alias, var in mapping))
        print(f"STREAM_COMPLETE rows={rows} simulated_s={final_t:.12g} rawfile=disk_pipe")
        return 0
    finally:
        fifo.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", type=Path)
    ap.add_argument("--ngspice", default="ngspice")
    args = ap.parse_args()
    try:
        sys.exit(run(args.deck, args.ngspice))
    except Exception as exc:
        print(f"STREAM_FAILED: {exc}", file=sys.stderr)
        sys.exit(2)
