#!/usr/bin/env python3
"""analog_a2_topology_select_check.py — A2 deterministic gate (v1.6.35).

Verifies that the upstream `analog-topology-select` skill has emitted
the canonical per-block A2 artefact:

    analog/<block>/topology.md

with substance:

  * file size ≥ 200 bytes (a placeholder line is < 50 bytes)
  * names at least one CIRCUIT-SPECIFIC primitive — a term that cannot
    plausibly appear in ordinary non-technical prose ('pmos', 'nmos',
    'mosfet', 'transistor', 'cascode', 'current mirror', 'differential',
    'amplifier', 'opamp', 'common-source', 'bandgap', 'oscillator',
    'comparator', 'capacitor', 'resistor', 'inductor', 'ldo',
    'regulator', 'charge pump', 'widlar', 'bjt', 'topology selected',
    …). Lower-cased substring over the whole file text.

  A2 measures VOCABULARY, not circuit structure. It is a substance
  floor ("did the skill describe a circuit at all?"), NOT a topology
  parser — a prose paragraph that names a cascode is accepted without
  any check that the cascode is wired to anything. That limit is
  deliberate and disclosed here so no reader mistakes a PASS for
  structural verification.

  The GENERIC panel below ('stage', 'load', 'switch', 'bias',
  'driver', 'feedback', 'reference', 'pole', 'zero', 'mirror', …) is
  CORROBORATION ONLY and can never satisfy the gate on its own. It
  used to sit in the same flat panel as the specific terms, which made
  the substance floor vacuous: a 486-byte office memo about a company
  picnic ("first stage", "feedback session", "load of paperwork",
  "driver of the shuttle bus", "bias toward the beach", "switch to the
  park") scored six hits and PASSed A2.

Failure rules:
  A2_TOPOLOGY_MISSING       — topology.md absent
  A2_TOPOLOGY_IR_*          — the block's topology.json IR is structurally
                              wrong: a device with the wrong terminal count,
                              a net no port/rail/internal-net declaration
                              names, a declared net no device touches, or no
                              device at all (checked whenever the IR exists)
  A2_TOPOLOGY_EMPTY         — present but < 200 bytes (placeholder)
  A2_TOPOLOGY_NO_PRIMITIVE  — present but names no circuit vocabulary
                              at all (neither panel hit)
  A2_TOPOLOGY_GENERIC_ONLY  — present, but every hit is an ordinary
                              English word from the generic panel; no
                              circuit-specific term was named

VACUOUS_PASS when no `analog_block_list.json` exists under
`phase3/analog/` (the analog runner's root) or `phase1/analog/` (the
root every A-step's flow `condition:` names), or it declares no blocks.

INCOMPLETE (rc=1) in project mode when SOME declared blocks have a
topology.md and others have none: A2 cannot be certified done while a
declared block has produced nothing. All blocks missing stays
VACUOUS_PASS (defer to the skill).

Artefact resolution: `phase3/analog/<block>/topology.md` (what the analog
runner writes) OR `phase2/analog/<block>/topology.md` (what the flow
declares as A2's required_output).

Exit codes / CLI: see `analog_a1_spec_extract_check.py`.
chip-AGNOSTIC — keyword panel is generic analog vocabulary.
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


import sys
from pathlib import Path
from typing import List, Optional

from _analog_a_check_common import (
    BLOCK_LIST_ABSENT_REASON,
    load_block_list, select_blocks, make_argparser, vacuous_pass,
    artefact_missing_for_block, emit_pass, emit_fail, emit_incomplete,
    resolve_block_artefact,
)

GATE = "analog_a2_topology_select_check"
SKILL = "analog-topology-select"
MIN_BYTES = 200
DECLARED_PHASE = 2

# ── substance panels ──────────────────────────────────────────────────────
# SPECIFIC: terms that do not occur in ordinary non-technical English. A hit
# here is on its own evidence that the document describes a circuit.
_SPECIFIC_PRIMITIVES_LC: tuple[str, ...] = (
    "pmos", "nmos", "mosfet", "transistor", "cascode", "differential",
    "amplifier", "topology selected", "opamp", "op-amp",
    "common-source", "common-gate", "source-follower", "comparator",
    "bandgap", "oscillator", "current mirror", "current source",
    "capacitor", "resistor", "inductor",
    "brokaw", "widlar", "wilson", "ldo", "regulator", "charge pump",
    "ring oscillator", "rc oscillator", "bjt",
    "phase margin", "slew rate", "transconductance",
)

# GENERIC: words that name a circuit concept AND are ordinary English. A hit
# here CORROBORATES a specific hit; it can never satisfy the gate alone.
# 'mirror' is generic; 'current mirror' (above) is the specific spelling.
_GENERIC_TERMS_LC: tuple[str, ...] = (
    "mirror", "reference", "driver", "switch", "load", "bias",
    "pole", "zero", "feedback", "open-loop", "closed-loop", "stage",
)

# Kept as the union so any external reader of the panel (docs, tests) still
# sees the full vocabulary. NOT used for the verdict — see `_check_block`.
_PRIMITIVE_KEYWORDS_LC: tuple[str, ...] = (
    _SPECIFIC_PRIMITIVES_LC + _GENERIC_TERMS_LC
)


def _ir_findings(project: Path, block: str, ir_path: Path) -> List[dict]:
    """Structural findings over the A2 topology IR; [] when there is none.

    Each device's net count equals its role's terminal count; every net a
    device touches is declared (a port, a rail or an internal net); every
    port and every declared internal net is touched by some device.
    """
    import json
    if not ir_path.is_file():
        return []
    rel = str(ir_path.relative_to(project))
    try:
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [{"block": block, "rule": "A2_TOPOLOGY_IR_INVALID",
                 "rel_path": rel, "detail": f"unparsable: {exc}"}]
    devices = [d for d in (ir.get("devices") or []) if isinstance(d, dict)]
    if not devices:
        return [{"block": block, "rule": "A2_TOPOLOGY_IR_NO_DEVICES",
                 "rel_path": rel, "detail": "the IR declares no device"}]
    ports = [str(p) for p in (ir.get("ports") or [])]
    rails = {str(v) for v in (ir.get("rails") or {}).values()}
    internal = [str(n) for n in (ir.get("internal_nets") or [])]
    declared = set(ports) | rails | set(internal)
    terms = ir.get("role_terminals") or {}
    out: List[dict] = []
    bad = [f"{d.get('name')}({d.get('role')}: {len(d.get('nets') or [])} "
           f"nets, role takes {terms.get(d.get('role'))})"
           for d in devices if terms.get(d.get("role")) is not None
           and len(d.get("nets") or []) != terms.get(d.get("role"))]
    if bad:
        out.append({"block": block, "rule": "A2_TOPOLOGY_IR_TERMINALS",
                    "rel_path": rel, "detail": "; ".join(bad[:8])})
    used = {str(n) for d in devices for n in (d.get("nets") or [])}
    undeclared = sorted(used - declared)
    if undeclared:
        out.append({"block": block, "rule": "A2_TOPOLOGY_IR_NET_UNDECLARED",
                    "rel_path": rel,
                    "detail": (f"{len(undeclared)} net(s) a device touches are "
                               f"not declared as a port, rail or internal net: "
                               f"{undeclared[:12]}")})
    dangling = [n for n in ports + internal if n not in used]
    if dangling:
        out.append({"block": block, "rule": "A2_TOPOLOGY_IR_NET_UNUSED",
                    "rel_path": rel,
                    "detail": f"declared net(s) no device touches: {dangling}"})
    return out


def _check_block(project: Path, block: str
                 ) -> tuple[Optional[str], List[dict]]:
    path, found = resolve_block_artefact(
        project, block, "topology.md", DECLARED_PHASE)
    if not found:
        return "MISSING", [{
            "block": block, "rule": "A2_TOPOLOGY_MISSING",
            "rel_path": str(path.relative_to(project)),
            "detail": "topology.md not found",
        }]
    try:
        size = path.stat().st_size
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError as exc:
        return "FAIL", [{
            "block": block, "rule": "A2_TOPOLOGY_EMPTY",
            "rel_path": str(path.relative_to(project)),
            "detail": f"OSError: {exc}",
        }]
    if size < MIN_BYTES:
        return "FAIL", [{
            "block": block, "rule": "A2_TOPOLOGY_EMPTY",
            "rel_path": str(path.relative_to(project)),
            "detail": f"{size}B < min {MIN_BYTES}B (placeholder?)",
        }]
    # THE IR IS THE TOPOLOGY (T94, A2 harvest #3). When the block carries the
    # `topology.json` IR that A3 renders from, its STRUCTURE is checked, not
    # its vocabulary: a memo headed "# Topology - ldo" passed the keyword
    # floor. MEASURED on vibeic-eda 0.3.77 / u_hawaii_adc/delta_sigma: 351
    # devices touch 8 nets the IR never declares, so A3's per-internal-net
    # rail measurement (which reads `internal_nets`) never measured them.
    ir_findings = _ir_findings(project, block, path.parent / "topology.json")
    if ir_findings:
        return "FAIL", ir_findings
    # The verdict asserts the GOOD thing is PRESENT: at least one term that
    # ordinary prose cannot supply. Generic hits are reported for diagnosis
    # but never promote the verdict on their own.
    specific = [kw for kw in _SPECIFIC_PRIMITIVES_LC if kw in text]
    generic = [kw for kw in _GENERIC_TERMS_LC if kw in text]
    if specific:
        return "PASS", []
    if generic:
        return "FAIL", [{
            "block": block, "rule": "A2_TOPOLOGY_GENERIC_ONLY",
            "rel_path": str(path.relative_to(project)),
            "detail": (
                "only ordinary-English terms matched "
                f"({'|'.join(sorted(generic))}) — none of them is evidence "
                "that a circuit topology was described; name a "
                "circuit-specific primitive (pmos|nmos|cascode|"
                "current mirror|opamp|bandgap|...)"),
        }]
    return "FAIL", [{
        "block": block, "rule": "A2_TOPOLOGY_NO_PRIMITIVE",
        "rel_path": str(path.relative_to(project)),
        "detail": "no transistor/primitive keyword found "
                  "(panel = pmos|nmos|cascode|current mirror|opamp|...)",
    }]


def main(argv: Optional[List[str]] = None) -> int:
    ap = make_argparser(GATE, __doc__)
    args = ap.parse_args(argv)
    project = args.project_dir.resolve()
    if not project.is_dir():
        print(f"error: not a directory: {project}", file=sys.stderr)
        return 2

    blocks_all = load_block_list(project)
    if blocks_all is None or (not blocks_all and not args.block):
        return vacuous_pass(GATE, args,
                            BLOCK_LIST_ABSENT_REASON)

    blocks = select_blocks(blocks_all or [], args.block)
    if not blocks:
        return vacuous_pass(GATE, args, "no blocks selected.")

    findings: List[dict] = []
    blocks_pass = 0
    missing_seen: List[dict] = []
    for block in blocks:
        status, fs = _check_block(project, block)
        if status == "PASS":
            blocks_pass += 1
        elif status == "MISSING":
            missing_seen.extend(fs)
        else:
            findings.extend(fs)

    summary = {
        "blocks_checked": len(blocks),
        "blocks_pass": blocks_pass,
        "blocks_missing": len(missing_seen),
        "blocks_fail": len(findings),
    }

    if args.block:
        if findings:
            return emit_fail(GATE, args, findings, summary)
        if missing_seen:
            return artefact_missing_for_block(
                GATE, args, args.block,
                missing_seen[0]["rel_path"], SKILL)
        return emit_pass(GATE, args, summary)

    if findings:
        return emit_fail(GATE, args, findings, summary)
    if missing_seen and blocks_pass == 0:
        return vacuous_pass(GATE, args,
                            f"all {len(missing_seen)} block(s) missing "
                            f"topology.md; defer to skill `{SKILL}`.")
    if missing_seen:
        # Mixed PASS + missing. Until v1.7.36 this fell through to
        # emit_pass, certifying A2 done on partial block coverage.
        return emit_incomplete(GATE, args, missing_seen, summary, SKILL)
    return emit_pass(GATE, args, summary)


if __name__ == "__main__":
    sys.exit(main())
