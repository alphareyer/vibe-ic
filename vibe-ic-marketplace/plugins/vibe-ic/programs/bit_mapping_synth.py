#!/usr/bin/env python3
"""bit_mapping_synth.py — the deterministic consumer of a bit-mapping contract.

WHAT THIS IS FOR (vibe-ic#2215)
===============================
`spec_numeric_pack_extract` retains the conditional numeric bit mapping a
public input states: per-bit and per-slice targets and sources, the
transformation, the condition, the extension source, and the preservation
prohibitions. Retaining it is half the job. An extracted contract that no
deterministic consumer reads leaves the authoring still to be done by
judgment, and leaves the contract unfalsifiable -- nothing ever demonstrates
that the recorded mapping is the mapping a candidate must implement.

This module is the other half, and it is TWO consumers of ONE contract:

    emit_rtl(contract)   synthesizable Verilog implementing exactly the
                         stated mapping and nothing else
    emit_tb(contract)    an executable test that DISCRIMINATES the branches:
                         it sweeps every condition value and every source bit
                         within a bounded exhaustive space, and it fails any
                         implementation that differs on any of them

THE TEST IS NOT A RESTATEMENT OF THE RTL, AND THE DIFFERENCE IS MEASURABLE
=========================================================================
Both are derived from the same contract, so agreement between them proves
nothing by itself -- that objection is correct and this module does not
pretend otherwise. What makes the test worth having is SENSITIVITY, and
sensitivity is measured by mutation, not asserted: `programs/tests/
test_issue2215_bit_mapping_synth.py` takes the emitted RTL, removes the
conditional inversion, and shows the SAME immutable test that passed now
fails. A test that merely mentions the signal names survives that mutation;
this one does not. The issue names exactly that check, and it is the reason
the emitted vectors are exhaustive over the stated space rather than sampled.

REFUSAL IS THE DEFAULT, AND IT IS NOT AN ERROR
==============================================
`plan()` returns a REFUSED plan, with named reasons, for every contract that
is not complete, consistent and supported. Generating from a partial contract
would invent the part that is missing, which is the §4.05 leak the extractor
exists to avoid; a caller that gets REFUSED hands the design to the authoring
path it would have used anyway. Refused, by name:

  * no mapping at all, or no declared output width;
  * more than one target signal, or a target bit mapped twice with the same
    condition, or mapped inconsistently;
  * a target bit no mapping covers -- a gap is an unstated bit, not a zero;
  * a conditioned bit whose complementary branch is unstated -- half a
    condition does not say what the other half does;
  * a preservation prohibition the mapping would violate;
  * a source slice whose width does not match its target's.

chip-AGNOSTIC: nothing here knows a chip, vendor, SKU or benchmark. The module
and signal names come from the contract, which came from the public input.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "vibeic.bit_mapping_plan.v1"

# The transforms this module can EMIT. A contract carrying anything else is
# refused rather than approximated: an unsupported transform silently dropped
# is a wrong implementation, not a partial one.
_EMITTABLE = ("identity", "invert", "xor", "and", "or", "replicate", "zero")
_BINARY_OP = {"xor": "^", "and": "&", "or": "|"}


def _bits(ref: Dict[str, Any]) -> List[int]:
    """The target bit indices a reference covers, high to low."""
    return list(range(int(ref["hi"]), int(ref["lo"]) - 1, -1))


def _is_slice_ref(ref: Any) -> bool:
    return (isinstance(ref, dict) and ref.get("signal") is not None
            and ref.get("hi") is not None and ref.get("lo") is not None)


def _condition_key(condition: Optional[Dict[str, Any]]) -> Optional[Tuple[str, int]]:
    if condition is None:
        return None
    return (str(condition["signal"]), int(condition["value"]))


def plan(items: List[dict], *, module: str = "bit_mapping_unit") -> Dict[str, Any]:
    """A complete, consistent, emittable plan -- or a REFUSED one, by name.

    `items` is `spec_numeric_pack_extract.extract()`'s output, unfiltered: the
    plan reads the kinds it consumes and ignores the rest, so a caller never
    has to know which kinds exist.
    """
    reasons: List[str] = []
    mappings = [i for i in items if i.get("kind") == "bit_mapping"]
    preserves = [i for i in items if i.get("kind") == "bit_preserve"]
    widths = [i for i in items if i.get("kind") == "width_convert"]
    if not mappings:
        return _refused(["the contract states no bit mapping"], module)
    if not widths:
        return _refused(["the contract states no output width, so the target "
                         "vector has no declared size"], module)
    out_width = int(widths[0]["out_width"])
    in_width = int(widths[0]["in_width"])
    if len({(int(w["in_width"]), int(w["out_width"])) for w in widths}) != 1:
        reasons.append("the contract states more than one width conversion")

    targets = {m["target"]["signal"] for m in mappings
               if _is_slice_ref(m.get("target"))}
    if len(targets) > 1:
        reasons.append("the contract maps more than one target signal: "
                       + ", ".join(sorted(targets)))
    if not targets:
        reasons.append("no mapping names a target slice")
    target_signal = sorted(targets)[0] if targets else "result"

    # Every bit, under every condition it is stated for.
    by_bit: Dict[int, Dict[Optional[Tuple[str, int]], dict]] = {}
    controls: Dict[str, None] = {}
    sources: Dict[str, None] = {}
    for item in mappings:
        transform = str(item.get("transform"))
        if transform not in _EMITTABLE:
            reasons.append(f"unsupported transform {transform!r}")
            continue
        target = item.get("target")
        if not _is_slice_ref(target):
            # "the upper N bits" with no explicit slice: anchor it to the top
            # of the declared output rather than guess where it starts.
            width = target.get("width") if isinstance(target, dict) else None
            if not width:
                reasons.append("a mapping names neither an explicit target "
                               "slice nor a width for the upper bits")
                continue
            target = {"signal": target_signal, "hi": out_width - 1,
                      "lo": out_width - int(width), "width": int(width)}
        source = item.get("source")
        condition = item.get("condition")
        if condition is not None:
            controls[str(condition["signal"])] = None
        if item.get("operand"):
            controls[str(item["operand"])] = None
        if _is_slice_ref(source):
            sources[str(source["signal"])] = None
            if transform != "replicate" and (
                    int(source["hi"]) - int(source["lo"])
                    != int(target["hi"]) - int(target["lo"])):
                reasons.append(
                    f"source {source['signal']}[{source['hi']}:{source['lo']}]"
                    f" and target {target['signal']}[{target['hi']}:"
                    f"{target['lo']}] have different widths")
                continue
        key = _condition_key(condition)
        for offset, bit in enumerate(_bits(target)):
            if bit >= out_width or bit < 0:
                reasons.append(f"{target['signal']}[{bit}] lies outside the "
                               f"declared {out_width}-bit output")
                continue
            slot = by_bit.setdefault(bit, {})
            if key in slot and slot[key] != _bit_rule(item, source, offset,
                                                      transform):
                reasons.append(
                    f"{target['signal']}[{bit}] is mapped twice with the same "
                    f"condition and the two mappings disagree")
            slot[key] = _bit_rule(item, source, offset, transform)

    for bit in range(out_width):
        if bit not in by_bit:
            reasons.append(f"{target_signal}[{bit}] is covered by no mapping; "
                           "an unstated bit is not a zero")
            continue
        keys = set(by_bit[bit])
        conditioned = {k for k in keys if k is not None}
        if conditioned:
            for signal in {k[0] for k in conditioned}:
                polarities = {k[1] for k in conditioned if k[0] == signal}
                if polarities != {0, 1} and None not in keys:
                    reasons.append(
                        f"{target_signal}[{bit}] states the {signal} == "
                        f"{sorted(polarities)[0]} branch and not its "
                        "complement; half a condition does not say what the "
                        "other half does")

    prohibited = {t for row in preserves
                  for t in (row.get("prohibited_transforms") or [])}
    for bit, slot in by_bit.items():
        for rule in slot.values():
            if rule["transform"] in prohibited:
                reasons.append(
                    f"{target_signal}[{bit}] applies {rule['transform']}, "
                    "which the contract explicitly prohibits")

    # A MAPPING MAY SOURCE THE TARGET ITSELF. "bits result[19:12] repeat
    # result[11]" states internal feedback, not a second input: the sign bit
    # being replicated is one this module computes. Emitting it as an input
    # port would declare `result` twice, once in each direction. So the target
    # is removed from the PORT list while staying usable in expressions, and
    # a bit that sources ITSELF is refused -- that is a combinational loop,
    # and no stated mapping means it.
    sources.pop(target_signal, None)
    for bit, slot in by_bit.items():
        for rule in slot.values():
            ref = rule.get("source")
            if (ref and ref["signal"] == target_signal
                    and int(ref["bit"]) == bit):
                reasons.append(f"{target_signal}[{bit}] is defined in terms of "
                               "itself, which is a combinational loop")
    if reasons:
        return _refused(sorted(set(reasons)), module)
    return {
        "schema": SCHEMA,
        "status": "READY",
        "module": module,
        "target": target_signal,
        "out_width": out_width,
        "in_width": in_width,
        "sources": sorted(sources),
        "controls": sorted(controls),
        "bits": {str(b): by_bit[b] for b in sorted(by_bit)},
        "prohibited_transforms": sorted(prohibited),
        "reasons": [],
    }


def _bit_rule(item: dict, source: Any, offset: int, transform: str) -> dict:
    """One target bit's rule: where its value comes from, and how."""
    if transform == "zero" or not _is_slice_ref(source):
        return {"transform": "zero", "source": None, "operand": None}
    if transform == "replicate":
        return {"transform": "identity",
                "source": {"signal": source["signal"], "bit": int(source["hi"])},
                "operand": None}
    bit = int(source["hi"]) - offset
    return {"transform": transform,
            "source": {"signal": source["signal"], "bit": bit},
            "operand": item.get("operand")}


def _refused(reasons: List[str], module: str) -> Dict[str, Any]:
    return {"schema": SCHEMA, "status": "REFUSED", "module": module,
            "reasons": reasons}


def _expr(rule: dict) -> str:
    """One bit's right-hand side, as Verilog."""
    if rule["transform"] == "zero" or rule["source"] is None:
        return "1'b0"
    ref = f"{rule['source']['signal']}[{rule['source']['bit']}]"
    if rule["transform"] == "identity":
        return ref
    if rule["transform"] == "invert":
        return f"~{ref}"
    op = _BINARY_OP.get(rule["transform"])
    if op and rule.get("operand"):
        return f"({ref} {op} {rule['operand']})"
    return ref


def _bit_assignment(plan_: dict, bit: int) -> str:
    """The full right-hand side for one bit, conditions folded in."""
    slot = plan_["bits"][str(bit)]
    unconditional = slot.get(None)
    branches = [(k, v) for k, v in slot.items() if k is not None]
    if not branches:
        return _expr(unconditional)
    # Nested ternaries in stated order; the unconditional rule, when there is
    # one, is the final else so a value always exists.
    signal = branches[0][0][0]
    true_rule = next((v for k, v in branches if k[1] == 1), None)
    false_rule = next((v for k, v in branches if k[1] == 0), None)
    if true_rule is None:
        true_rule = unconditional
    if false_rule is None:
        false_rule = unconditional
    return f"{signal} ? {_expr(true_rule)} : {_expr(false_rule)}"


def emit_rtl(plan_: dict) -> str:
    """Synthesizable Verilog for a READY plan. Raises on a REFUSED one."""
    if plan_.get("status") != "READY":
        raise ValueError("cannot emit RTL from a refused plan: "
                         + "; ".join(plan_.get("reasons") or []))
    ports = [f"    input  wire [{plan_['in_width'] - 1}:0] {s}"
             for s in plan_["sources"]]
    ports += [f"    input  wire {c}" for c in plan_["controls"]]
    ports.append(f"    output wire [{plan_['out_width'] - 1}:0] "
                 f"{plan_['target']}")
    lines = [f"// Generated from a public-input bit-mapping contract "
             f"({SCHEMA}).",
             "// Every assignment below is one stated mapping; nothing is "
             "inferred.",
             f"module {plan_['module']} (",
             ",\n".join(ports),
             ");"]
    for bit in range(plan_["out_width"] - 1, -1, -1):
        lines.append(f"    assign {plan_['target']}[{bit}] = "
                     f"{_bit_assignment(plan_, bit)};")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _evaluation_order(plan_: dict) -> List[int]:
    """Target bits, with every self-referenced bit computed before its reader.

    A mapping may source the target's own bits (sign replication), so bit
    order is a dependency order, not a numeric one. Bits that source only
    inputs come first; the rest follow once what they read exists. A cycle
    cannot reach here -- `plan` refuses a bit defined in terms of itself --
    and any bit this cannot order is appended rather than dropped, so the
    model never silently omits one.
    """
    target = plan_["target"]
    pending = list(range(plan_["out_width"]))
    order: List[int] = []
    resolved: set = set()
    while pending:
        progressed = False
        for bit in list(pending):
            needs = {int(r["source"]["bit"])
                     for r in plan_["bits"][str(bit)].values()
                     if r.get("source") and r["source"]["signal"] == target}
            if needs <= resolved:
                order.append(bit)
                resolved.add(bit)
                pending.remove(bit)
                progressed = True
        if not progressed:
            order.extend(pending)
            break
    return order


def _model(plan_: dict, source_values: Dict[str, int],
           control_values: Dict[str, int]) -> int:
    """The contract's own answer for one input vector."""
    out = 0
    target = plan_["target"]
    for bit in _evaluation_order(plan_):
        slot = plan_["bits"][str(bit)]
        rule = slot.get(None)
        for key, candidate in slot.items():
            if key is not None and control_values.get(key[0]) == key[1]:
                rule = candidate
                break
        if rule is None:
            continue
        if rule["transform"] == "zero" or rule["source"] is None:
            continue
        ref = rule["source"]
        holder = out if ref["signal"] == target else source_values.get(
            ref["signal"], 0)
        value = (holder >> ref["bit"]) & 1
        if rule["transform"] == "invert":
            value ^= 1
        elif rule["transform"] in _BINARY_OP and rule.get("operand"):
            operand = control_values.get(rule["operand"], 0) & 1
            if rule["transform"] == "xor":
                value ^= operand
            elif rule["transform"] == "and":
                value &= operand
            else:
                value |= operand
        out |= (value & 1) << bit
    return out


def vectors(plan_: dict, *, max_source_bits: int = 12) -> List[dict]:
    """The bounded exhaustive space this contract can be discriminated over.

    Every combination of the control bits, crossed with a source sweep that
    exercises EVERY source bit in both polarities: all-zero, all-one, and one
    hot bit at a time. That is what makes a dropped inversion on any single
    bit observable -- a sampled or all-zero-only stimulus would miss it, and a
    test that misses it is the "merely mentions the signal names" case the
    issue rules insufficient.
    """
    controls = plan_["controls"]
    if len(controls) > max_source_bits:
        return []
    patterns = [0, (1 << plan_["in_width"]) - 1]
    patterns += [1 << b for b in range(min(plan_["in_width"], max_source_bits))]
    rows = []
    for mask in range(1 << len(controls)):
        control_values = {c: (mask >> i) & 1 for i, c in enumerate(controls)}
        for pattern in patterns:
            source_values = {s: pattern for s in plan_["sources"]}
            rows.append({"controls": control_values, "sources": source_values,
                         "expected": _model(plan_, source_values,
                                            control_values)})
    return rows


def emit_tb(plan_: dict, *, top: str = "vibeic_ai_challenge_tb") -> str:
    """A branch-discriminating executable test for a READY plan."""
    if plan_.get("status") != "READY":
        raise ValueError("cannot emit a test from a refused plan")
    rows = vectors(plan_)
    if not rows:
        raise ValueError("the contract's control space is too large to "
                         "enumerate exhaustively")
    out_width, in_width = plan_["out_width"], plan_["in_width"]
    decls = [f"    reg [{in_width - 1}:0] {s};" for s in plan_["sources"]]
    decls += [f"    reg {c};" for c in plan_["controls"]]
    decls.append(f"    wire [{out_width - 1}:0] {plan_['target']};")
    conn = ", ".join(f".{n}({n})" for n in
                     plan_["sources"] + plan_["controls"] + [plan_["target"]])
    body = []
    for i, row in enumerate(rows):
        for name, value in row["sources"].items():
            body.append(f"        {name} = {in_width}'d{value};")
        for name, value in row["controls"].items():
            body.append(f"        {name} = 1'b{value};")
        body.append("        #1;")
        body.append(
            f"        if ({plan_['target']} !== {out_width}'d{row['expected']})"
            f" begin")
        body.append('            $display("VIBEIC_AI_CHALLENGE=FAIL");')
        body.append(f'            $display("vector {i}: expected %0d got %0d",'
                    f" {out_width}'d{row['expected']}, {plan_['target']});")
        body.append("            $fatal(1);")
        body.append("        end")
    return "\n".join([
        "`timescale 1ns/1ps",
        f"// {len(rows)} vectors, exhaustive over every control combination "
        "crossed with",
        "// all-zero, all-one and one-hot source patterns: every stated bit "
        "relation is",
        "// observed in both polarities, so dropping any one of them fails "
        "this test.",
        f"module {top};",
        *decls,
        f"    {plan_['module']} dut ({conn});",
        "    initial begin",
        *body,
        '        $display("VIBEIC_AI_CHALLENGE=PASS");',
        "        $finish;",
        "    end",
        "    initial begin",
        "        #100000;",
        '        $display("VIBEIC_AI_CHALLENGE=FAIL");',
        '        $fatal(1, "watchdog");',
        "    end",
        "endmodule",
    ]) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prompt", help="public input text file, or - for stdin")
    ap.add_argument("--module", default="bit_mapping_unit")
    ap.add_argument("--rtl", help="write the emitted RTL here")
    ap.add_argument("--tb", help="write the emitted test here")
    ap.add_argument("--plan", help="write the plan JSON here")
    a = ap.parse_args(argv)
    import spec_numeric_pack_extract as extractor        # noqa: PLC0415
    text = (_sys.stdin.read() if a.prompt == "-"
            else open(a.prompt, encoding="utf-8", errors="replace").read())
    made = plan(extractor.extract(text), module=a.module)
    if a.plan:
        with open(a.plan, "w", encoding="utf-8") as fh:
            json.dump(made, fh, indent=2)
            fh.write("\n")
    if made["status"] != "READY":
        print("REFUSED: " + "; ".join(made["reasons"]), file=_sys.stderr)
        return 1
    if a.rtl:
        open(a.rtl, "w", encoding="utf-8").write(emit_rtl(made))
    if a.tb:
        open(a.tb, "w", encoding="utf-8").write(emit_tb(made))
    print(json.dumps({"status": made["status"], "module": made["module"],
                      "out_width": made["out_width"],
                      "vectors": len(vectors(made))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
