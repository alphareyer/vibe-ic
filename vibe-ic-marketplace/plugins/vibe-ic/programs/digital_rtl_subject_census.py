#!/usr/bin/env python3
"""digital_rtl_subject_census.py — is there a digital datapath to AUTHOR?

THE QUESTION AN AUTHORING HANDOFF NEVER ASKED.

`design_one_shot_runner.step_rtl_gen` hands a class with `rtl_gen=null` to an
LLM author (`fallback_skill`, normally `spec-to-rtl`). It decides to hand off
from the REGISTRY alone — the class says "no deterministic generator" — and
never asks the one question the author is about to be stuck on: does THIS
design's input contain a digital datapath at all?

Measured, lane czadcrtl, 2026-09-08, on the live tip (main e2b3c08170b5, tree
75d478b34c17, host 8HD-4, load 6.7): a `data_converter` front-door run WAIVEs
`rtl_gen` to `spec-to-rtl` four times (once + three RTL-repair retries), every
one of them byte-identical, and `phase2/stage1/rtl/` never comes into
existence. `rtl_validate`, `sim`, `reference_tb` and `yosys_synth` are all
REFUSED-TO-RUN on the absent tree and the phase-2 verdict is FAIL. The runner
declared a handoff and then charged the design with the fact that nobody took
it.

`analog_interface_classify` already answers half of this — it routes an
ALL-ANALOG top interface to the analog track instead of to `spec-to-rtl` — and
its own source names the half it cannot answer:

    "A clocked-but-logic-free SC modulator (clock in, bitstream out, no data,
     no reset) is exactly the case that predicate cannot express."

That is exactly the design above: three `ck*` INPUT clocks, six analog inputs,
seven bitstream outputs, and nothing else. `digital_datapath_absent` is
`not (has_clk or has_rst or has_data)`, so ONE clock pin re-asserts a digital
datapath the rest of the pinout denies.

WHAT THIS MODULE ADDS, AND WHY IT NEEDS TWO SIGNALS
---------------------------------------------------
A clock is a TIMING reference, not information. Synthesizable logic computes
outputs from inputs; with no digital data input there is nothing to compute
from, and with no reset there is not even a defined initial state to compute
from. But an interface read alone is not enough to say so — the fail-safe bias
in `analog_interface_classify` exists because dropping a real digital datapath
is worse than an honest FAIL. So this module requires TWO independent signals,
both read from the flow's OWN artifacts, and reports ABSENT only when both
agree:

  (1) INTERFACE — the L9 top interface exposes no digital DATA input and no
      digital RESET input. Port classification is delegated wholly to
      `analog_interface_classify.classify_top_ports`, so there is exactly ONE
      implementation of what a port is and the two can never disagree.

  (2) CONTENT — the flow's own Phase-1 extraction found NO digital behaviour
      anywhere: no opcodes (L3), no registers (L4), no FSM states / machines /
      pipeline stages (L6), no internal wires / memories / memory map (L9).

Measured on the two designs the lane compared, the two signals separate them
cleanly and for the right reason:

    u_hawaii_adc  (data_converter)     L3.opcodes 0  L4.registers 0
                                       L6.fsm_states 0  fsm_machines 0
                                       L9.internal_wires 0  memories 0
                                       interface: 3 clock INPUTs, no data, no
                                       reset                     -> ABSENT
    sha256        (crypto_accelerator) L4.registers 7            -> PRESENT

`L9.submodules` is DELIBERATELY NOT in the census, and the reason is measured
rather than assumed: on `u_hawaii_adc` that list holds one entry whose own
`extraction_strategy` is `analog_block_multiplicity_v1_6_403` — an ANALOG
block. A non-empty submodule list is therefore not evidence of DIGITAL content,
and counting it would have made this census answer PRESENT for a design with
no digital content at all.

FAIL-SAFE, IN THE SAME DIRECTION AS #141
----------------------------------------
Every uncertainty answers PRESENT (keep the RTL track): a missing or unreadable
L doc, an empty L9 `top_ports`, an unparsable field, any exception. ABSENT is
returned only when every one of the eight census fields was READ and was empty
AND the interface was read and carries no data/reset input. "Could not read it"
is never "read it and it was empty".

Usage:
    python3 digital_rtl_subject_census.py <project> [--json out.json]
    exit 0 -> no digital RTL subject (both signals agree it is absent)
    exit 1 -> a digital RTL subject is present (keep the RTL track)
    exit 2 -> indeterminate (nothing could be read; treated as PRESENT)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _atomic_artefact import write_text

#: (L-doc file stem, field) pairs that carry DIGITAL behaviour. Each is a list
#: in the emitted schema; a non-empty list is content, an empty one is not.
#: `L9_INTEGRATION_SPEC.submodules` is excluded on measured grounds — see the
#: module docstring.
CENSUS_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("L3_CMD_PROTOCOL", "opcodes"),
    ("L4_REGMAP", "registers"),
    ("L6_CONTROL_LOGIC", "fsm_states"),
    ("L6_CONTROL_LOGIC", "fsm_machines"),
    ("L6_CONTROL_LOGIC", "pipeline_stages"),
    ("L9_INTEGRATION_SPEC", "internal_wires"),
    ("L9_INTEGRATION_SPEC", "memories"),
    ("L9_INTEGRATION_SPEC", "memory_map"),
)


def _generated_docs_dirs(project: Path) -> List[Path]:
    """Every directory under phase1/ that holds L-doc JSON, most-specific first."""
    base = project / "phase1"
    if not base.is_dir():
        return []
    seen: List[Path] = []
    for p in sorted(base.rglob("L9_INTEGRATION_SPEC.json")):
        if p.parent not in seen:
            seen.append(p.parent)
    return seen


def _read_doc(project: Path, stem: str) -> Optional[Dict[str, Any]]:
    """The named L doc as a dict, or None when it cannot be READ.

    None is "could not read it", never "read it and it was empty" — every
    caller here turns None into PRESENT (keep RTL), not into an empty field.
    """
    for d in _generated_docs_dirs(project):
        f = d / f"{stem}.json"
        if not f.is_file():
            continue
        try:
            doc = json.loads(f.read_text(errors="replace"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None
    return None


def digital_content_census(project: Path) -> Dict[str, Any]:
    """Per-field census of DIGITAL behaviour in the flow's own Phase-1 L docs.

    Returns ``{fields: {"<stem>.<field>": <count|None>}, present: [...],
    unreadable: [...], content_absent: bool}``. ``None`` for a field means the
    doc could not be read; any unreadable field forces ``content_absent`` False.
    """
    fields: Dict[str, Optional[int]] = {}
    present: List[str] = []
    unreadable: List[str] = []
    docs: Dict[str, Optional[Dict[str, Any]]] = {}
    for stem, field in CENSUS_FIELDS:
        if stem not in docs:
            docs[stem] = _read_doc(project, stem)
        doc = docs[stem]
        key = f"{stem}.{field}"
        if doc is None:
            fields[key] = None
            unreadable.append(key)
            continue
        val = doc.get(field)
        if val is None:
            # The field is absent from a doc that WAS read. The schema declares
            # it a list; a doc that does not carry it made no statement about
            # it, so it cannot be counted as an emptiness either.
            fields[key] = None
            unreadable.append(key)
            continue
        if isinstance(val, (list, tuple, dict)):
            n = len(val)
        else:
            n = 1 if val else 0
        fields[key] = n
        if n:
            present.append(key)
    return {
        "fields": fields,
        "present": present,
        "unreadable": unreadable,
        "content_absent": (not present) and (not unreadable),
    }


def interface_has_no_information_input(project: Path) -> Dict[str, Any]:
    """The interface half: no digital DATA input and no digital RESET input.

    Port classification is delegated ENTIRELY to `analog_interface_classify`,
    which owns the definition of what a clock / reset / analog / data port is.
    A clock INPUT is permitted here and nowhere else in the flow: a clock
    carries timing, not information.
    """
    try:
        import analog_interface_classify as _aic          # noqa: PLC0415
    except ImportError as exc:                            # pragma: no cover
        return {"readable": False, "reason": f"classifier unavailable: {exc}",
                "no_information_input": False}
    ports = _aic.read_l9_top_ports(project)
    if not ports:
        return {"readable": False,
                "reason": "no L9 top_ports to classify",
                "no_information_input": False}
    ev = _aic.classify_top_ports(ports)
    no_info = not (ev["has_digital_data_input"]
                   or ev["has_digital_reset_input"])
    ev.update({"readable": True, "no_information_input": bool(no_info)})
    return ev


def digital_rtl_subject_absent(project: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """(absent, reason, evidence). True ONLY when BOTH signals say absent."""
    project = Path(project)
    iface = interface_has_no_information_input(project)
    census = digital_content_census(project)
    ev = {"interface": iface, "content_census": census}
    if not iface.get("readable"):
        return (False,
                f"cannot assert: {iface.get('reason', 'interface unreadable')} "
                f"— keep the RTL track", ev)
    if not iface.get("no_information_input"):
        carried = [k for k in ("has_digital_data_input",
                               "has_digital_reset_input") if iface.get(k)]
        return (False,
                f"the top interface carries an information input "
                f"({', '.join(carried)}) — keep the RTL track", ev)
    if census["unreadable"]:
        return (False,
                f"NOT_MEASURED: {len(census['unreadable'])} census field(s) "
                f"could not be read ({', '.join(census['unreadable'][:4])}) — "
                f"keep the RTL track", ev)
    if census["present"]:
        return (False,
                f"Phase-1 extraction declares digital content "
                f"({', '.join(census['present'])}) — keep the RTL track", ev)
    return (True,
            "no digital RTL subject: the top interface carries no digital data "
            "or reset INPUT (clock only — a clock is timing, not information) "
            "AND the flow's own Phase-1 extraction found no digital behaviour "
            f"in any of the {len(CENSUS_FIELDS)} census fields "
            "(opcodes, registers, FSM states/machines, pipeline stages, "
            "internal wires, memories, memory map)", ev)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    if not args.project.is_dir():
        print(f"error: not a directory: {args.project}", file=sys.stderr)
        return 2
    absent, reason, ev = digital_rtl_subject_absent(args.project.resolve())
    out = {
        "program": "digital_rtl_subject_census",
        "digital_rtl_subject_absent": absent,
        "reason": reason,
        "evidence": ev,
    }
    blob = json.dumps(out, indent=2, ensure_ascii=False)
    if args.json:
        write_text(args.json, blob + "\n")
    print(blob)
    if absent:
        return 0
    if not ev["interface"].get("readable"):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
