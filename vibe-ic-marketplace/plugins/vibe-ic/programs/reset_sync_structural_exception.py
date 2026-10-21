#!/usr/bin/env python3
"""Exact mapped-netlist proof for asynchronous reset release synchronizers.

The program never grants a reset-wide exception.  It accepts only a source
contract that explicitly requests structural synchronous release, proves the
complete direct fanout of that reset in the current mapped netlist, and emits
one endpoint-scoped exception for each proven sequential async-control pin.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import _path_layout as PL
from fault_cut_async_observe import async_pin_and_polarity, parse_instances

SCHEMA = "vibe-ic/reset-sync-structural-exception/1"


class StructuralResetRefusal(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _net(value: str) -> str | None:
    value = value.strip()
    if value.startswith("\\"):
        value = value[1:].strip()
    return value if re.fullmatch(r"[A-Za-z_][\w$]*", value) else None


def _constant(value: str) -> int | None:
    value = re.sub(r"\s+", "", value)
    if value in ("1'b0", "1'h0", "1'd0", "0"):
        return 0
    if value in ("1'b1", "1'h1", "1'd1", "1"):
        return 1
    return None


def _balanced_block(text: str, brace: int) -> str:
    depth = 0
    quoted = False
    escaped = False
    for index in range(brace, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1:index]
    raise StructuralResetRefusal("RESET_SYNC_LIBERTY_BLOCK_UNTERMINATED")


_IDENT = r'"?([A-Za-z_][\w$]*)"?'


def _liberty_ffs(liberty: str) -> dict[str, dict[str, str]]:
    """Read FF roles from ordinary Liberty, including quoted identifiers."""
    result: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\bcell\s*\(\s*" + _IDENT + r"\s*\)\s*\{",
                             liberty):
        body = _balanced_block(liberty, match.end() - 1)
        ff = re.search(r"\bff\s*\([^)]*\)\s*\{", body)
        if not ff:
            continue
        ff_body = _balanced_block(body, ff.end() - 1)
        record = {}
        for key in ("clear", "preset", "clocked_on", "next_state"):
            value = re.search(key + r'\s*:\s*"([^"]*)"\s*;', ff_body)
            if value:
                record[key] = value.group(1).strip()
        if record:
            result[match.group(1)] = record
    return result


def _output_pins(liberty: str) -> dict[str, set[str]]:
    """Return pins that carry the primary FF state, never its complement."""
    result: dict[str, set[str]] = {}
    for match in re.finditer(r"\bcell\s*\(\s*" + _IDENT + r"\s*\)\s*\{",
                             liberty):
        body = _balanced_block(liberty, match.end() - 1)
        ff = re.search(r"\bff\s*\(\s*" + _IDENT, body)
        primary_state = ff.group(1) if ff else None
        outputs = set()
        for pin in re.finditer(r"\bpin\s*\(\s*" + _IDENT + r"\s*\)\s*\{",
                               body):
            pin_body = _balanced_block(body, pin.end() - 1)
            function = re.search(r'\bfunction\s*:\s*"([^"\s]+)"\s*;',
                                 pin_body)
            if (primary_state and
                    re.search(r'\bdirection\s*:\s*"?output"?\s*;', pin_body)
                    and function and function.group(1) == primary_state):
                outputs.add(pin.group(1))
        result[match.group(1)] = outputs
    return result


_ALL_INST_RE = re.compile(
    r"\b([A-Za-z_][\w$]*)\s+(\\?[^\s(\\]+)\s*\(\s*((?:\.[^;]*?))\)\s*;", re.S)
_CONN_RE = re.compile(r"\.\s*([A-Za-z_][\w$]*)\s*\(\s*([^)]*?)\s*\)")


def _all_connections(netlist: str) -> list[tuple[str, str, dict[str, str]]]:
    return [(m.group(1), m.group(2).lstrip("\\"),
             {pin: value.strip() for pin, value in _CONN_RE.findall(m.group(3))})
            for m in _ALL_INST_RE.finditer(netlist)]


def build_structural_exception(netlist: str, liberty: str,
                               policies: dict[str, list[dict[str, Any]]]) -> tuple[str, dict]:
    """Prove exact release chains and return SDC text plus a typed receipt."""
    ffs = _liberty_ffs(liberty)
    outputs = _output_pins(liberty)
    instances = parse_instances(netlist, set(ffs))
    all_connections = _all_connections(netlist)
    commands: list[str] = []
    proven: list[dict[str, Any]] = []

    for reset, bindings in sorted(policies.items()):
        if not bindings or any(
                b.get("deassertion_policy") != "structural_sync_release"
                and (type(b.get("deassertion_guard_ns")) not in (int, float)
                     or b["deassertion_guard_ns"] <= 0)
                for b in bindings):
            raise StructuralResetRefusal(
                f"RESET_SYNC_POLICY_UNBOUND: {reset}")
        reset_key = _net(reset)
        candidates = []
        candidate_keys = set()
        for cell, inst, conns in instances:
            ff = ffs[cell]
            async_controls = []
            for role in ("clear", "preset"):
                parsed = async_pin_and_polarity(ff.get(role, ""))
                if parsed:
                    async_controls.append((role, parsed[0], parsed[1]))
            for role, pin, active_low in async_controls:
                if _net(conns.get(pin, "")) != reset_key:
                    continue
                clock_pin = async_pin_and_polarity(ff.get("clocked_on", ""))
                data_pin = async_pin_and_polarity(ff.get("next_state", ""))
                if clock_pin is None or data_pin is None:
                    raise StructuralResetRefusal(
                        f"RESET_SYNC_LIBERTY_ROLE_UNSUPPORTED: {cell}")
                q_pins = outputs.get(cell, set())
                q_nets = {_net(conns.get(q, "")) for q in q_pins}
                q_nets.discard(None)
                if not q_nets:
                    raise StructuralResetRefusal(
                        f"RESET_SYNC_OUTPUT_UNPROVEN: {inst}")
                record = {"cell": cell, "instance": inst, "async_pin": pin,
                          "async_role": role, "async_active_low": active_low,
                          "clock_pin": clock_pin[0],
                          "clock_net": _net(conns.get(clock_pin[0], "")),
                          "data_pin": data_pin[0],
                          "data_value": conns.get(data_pin[0], "").strip(),
                          "output_nets": sorted(q_nets)}
                candidates.append(record)
                candidate_keys.add((inst, pin))

        # The top port may drive only the proven async-control pins.  A buffer,
        # combinational gate, or another sequential role is an extra fanout.
        loads = {(inst, pin) for _cell, inst, conns in all_connections
                 for pin, value in conns.items() if _net(value) == reset_key}
        assigned_aliases = re.findall(
            r"\bassign\s+([^;=]+?)\s*=\s*\\?" + re.escape(reset) +
            r"\s*;", netlist)
        if loads != candidate_keys or assigned_aliases:
            raise StructuralResetRefusal(
                f"RESET_SYNC_EXTRA_FANOUT: {reset}: "
                f"{sorted(loads - candidate_keys)} aliases={assigned_aliases}")

        expected_total = sum(b.get("release_stages", 0) for b in bindings)
        if len(candidates) != expected_total:
            raise StructuralResetRefusal(
                f"RESET_SYNC_DEPTH_MISMATCH: {reset}: expected "
                f"{expected_total}, found {len(candidates)}")

        for binding in sorted(bindings, key=lambda row: row["clock"]):
            clock = binding.get("release_clock")
            depth = binding.get("release_stages")
            domain = [c for c in candidates if c["clock_net"] == _net(clock)]
            if type(depth) is not int or depth <= 0 or len(domain) != depth:
                raise StructuralResetRefusal(
                    f"RESET_SYNC_DOMAIN_DEPTH_MISMATCH: {reset}/{clock}: "
                    f"expected {depth}, found {len(domain)}")
            polarity = binding.get("polarity")
            if polarity not in ("active_low", "active_high"):
                raise StructuralResetRefusal(
                    f"RESET_SYNC_POLARITY_UNBOUND: {reset}/{clock}")
            if any(c["async_active_low"] != (polarity == "active_low")
                   for c in domain):
                raise StructuralResetRefusal(
                    f"RESET_SYNC_POLARITY_MISMATCH: {reset}/{clock}")
            if len({c["async_role"] for c in domain}) != 1:
                raise StructuralResetRefusal(
                    f"RESET_SYNC_ASYNC_ROLE_MISMATCH: {reset}/{clock}")

            # A clear-based release chain shifts ones; a preset-based chain
            # shifts zeroes.  Exactly one stage must be the constant-fed head,
            # and every following D must consume the prior stage Q.
            head_value = 1 if domain[0]["async_role"] == "clear" else 0
            heads = [c for c in domain if _constant(c["data_value"]) == head_value]
            if len(heads) != 1:
                raise StructuralResetRefusal(
                    f"RESET_SYNC_CHAIN_HEAD_MISMATCH: {reset}/{clock}")
            ordered = [heads[0]]
            remaining = [c for c in domain if c is not heads[0]]
            while remaining:
                previous = set(ordered[-1]["output_nets"])
                followers = [c for c in remaining if _net(c["data_value"]) in previous]
                if len(followers) != 1:
                    raise StructuralResetRefusal(
                        f"RESET_SYNC_CHAIN_LINK_MISMATCH: {reset}/{clock}")
                ordered.append(followers[0])
                remaining.remove(followers[0])
            endpoints = [f"{c['instance']}/{c['async_pin']}" for c in ordered]
            exception_authorized = (binding.get("deassertion_policy") ==
                                    "structural_sync_release")
            if exception_authorized:
                for endpoint in endpoints:
                    commands.append(
                        f"set_false_path -from [get_ports {{{reset}}}] "
                        f"-to [get_pins {{{endpoint}}}]")
            proven.append({"reset": reset, "clock": clock, "stages": depth,
                           "endpoints": endpoints,
                           "exception_authorized": exception_authorized,
                           "source_basis": binding.get("deassertion_policy_basis"),
                           "phase_basis": binding.get("basis")})

    text = "\n".join(commands) + ("\n" if commands else "")
    if "set_case_analysis" in text or re.search(
            r"set_false_path\s+-from\s+\[get_ports\s+\{?[^}\]]+\}?\]\s*$",
            text, re.M):
        raise StructuralResetRefusal("RESET_SYNC_BROAD_EXCEPTION_REFUSED")
    return text, {"schema": SCHEMA, "verdict": "PASS",
                  "netlist_sha256": _sha(netlist.encode()),
                  "liberty_sha256": _sha(liberty.encode()),
                  "commands_sha256": _sha(text.encode()),
                  "proven_domains": proven}


def apply_current_project(project: Path, liberty_path: Path,
                          sdc_text: str) -> tuple[str, dict | None]:
    """Apply current source policy to the current mapped netlist, if present."""
    project = Path(project)
    docs_dir = PL.generated_docs_dir(project)
    try:
        l9 = json.loads((docs_dir / "L9_INTEGRATION_SPEC.json").read_text())
        l8 = json.loads((docs_dir / "L8_TIMING_WAVEFORM.json").read_text())
    except (OSError, ValueError) as exc:
        return sdc_text, None
    ports = {row.get("name") for row in l9.get("ports", [])
             if isinstance(row, dict) and isinstance(row.get("name"), str)}
    from _reset_input_semantics import admit_reset_phases
    phases = admit_reset_phases(project, [l9, l8], ports)
    policies = {name: rows for name, rows in phases.items()
                if any(row.get("deassertion_policy") ==
                       "structural_sync_release" for row in rows)}
    if not policies:
        return sdc_text, None
    netlist_path = PL.mapped_synth_netlist(project)
    if netlist_path is None:
        raise StructuralResetRefusal("RESET_SYNC_MAPPED_NETLIST_ABSENT")
    try:
        netlist = netlist_path.read_text(errors="replace")
        liberty = Path(liberty_path).read_text(errors="replace")
    except OSError as exc:
        raise StructuralResetRefusal(
            "RESET_SYNC_CURRENT_INPUT_UNREADABLE") from exc
    exception, report = build_structural_exception(netlist, liberty, policies)
    report.update({"mapped_netlist": str(netlist_path.relative_to(project)),
                   "netlist_sha256": _sha(netlist_path.read_bytes()),
                   "liberty": str(liberty_path),
                   "liberty_sha256": _sha(Path(liberty_path).read_bytes())})
    banner = ("\n# Exact mapped reset synchronizer exception; current source and "
              "netlist bound\n")
    return sdc_text.rstrip() + banner + exception, report
