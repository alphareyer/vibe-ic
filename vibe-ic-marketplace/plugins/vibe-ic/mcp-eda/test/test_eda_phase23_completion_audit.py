#!/usr/bin/env python3
"""Wave 75 — tests for eda_phase23_completion_audit (v0.109 tool).

This is the SOLE acceptance gate for Phase 2+3 completion claims; the
contract is documented in CLAUDE.md rule #11. Tests verify:

Positive: tool wraps phase23_completion_self_audit_check.py (the
          gate referenced in CLAUDE.md), with --json output piped back.
Negative: missing project_dir is not silently defaulted (zod required).
Edge   : exit code 0 → phase23_complete:true; non-zero → false.
SKIP   : description forbids skipping — claims without this audit
         are explicitly called out as a process violation.
"""
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX_JS = ROOT / "src" / "index.js"
GATE_NAME = "phase23_completion_self_audit_check.py"


def _slice():
    src = INDEX_JS.read_text()
    idx = src.find('"eda_phase23_completion_audit"')
    assert idx > 0
    return src[idx: idx + 4000]


def test_tool_registered():
    assert '"eda_phase23_completion_audit"' in INDEX_JS.read_text()


def test_project_dir_is_required():
    """Negative: project_dir must be required (no default). A default
    would let an agent forget to pass it and silently audit the wrong
    tree — exactly the failure mode CLAUDE.md rule #11 forbids."""
    w = _slice()
    i = w.find("project_dir")
    line = w[i: i + 250]
    assert ".default(" not in line, "project_dir must be required"


def test_dispatches_to_canonical_gate_script():
    """Positive: must run the canonical phase23_completion_self_audit_check.py
    via a subprocess — NOT a re-implementation. The hardening switched the runner
    from execSync(string) to _spawnSync(argv) so project_dir is never
    shell-parsed; either subprocess primitive satisfies the delegation
    contract."""
    w = _slice()
    assert "phase23_completion_self_audit_check.py" in w, (
        "tool must delegate to the canonical gate, not re-implement"
    )
    assert "_spawnSync" in w or "execSync" in w


def test_uses_json_output_mode():
    """Edge: must invoke gate with --json - so structured Overall/PASS/
    FAIL data round-trips back to the agent."""
    w = _slice()
    assert "--json" in w


def test_phase23_complete_field_derived_from_exit_code():
    """Positive: exit_code===0 → phase23_complete:true. This is the
    single field downstream gates / human reviewers should branch on.
    A regression that always returns true would defeat the gate."""
    w = _slice()
    assert "phase23_complete" in w
    assert "exitCode === 0" in w or "exitCode == 0" in w


def test_description_forbids_skipping():
    """SKIP_NOT_APPLICABLE: the description must explicitly call out
    that individual gate PASSes are insufficient (CLAUDE.md rule #11).
    A regression that softens the language risks agents skipping."""
    w = _slice()
    desc_terms = [
        "SOLE",
        "necessary but insufficient",
        "Phase 2+3",
    ]
    desc_lower = w.lower()
    matched = sum(1 for t in desc_terms if t.lower() in desc_lower)
    assert matched >= 2, (
        f"description must reinforce the SOLE-gate contract; "
        f"matched only {matched}/3 strict terms"
    )


@pytest.mark.skipif(
    shutil.which("node") is None
    or not (ROOT / "node_modules" / "@modelcontextprotocol" / "sdk").is_dir(),
    reason="NOT_MEASURED: node or mcp-eda/node_modules absent (run `npm ci`)",
)
def test_canonical_gate_exists_on_disk(tmp_path):
    """Ask the TOOL where its gate is, then check that path.

    This test used to probe its OWN candidate list, which included the
    current layout, so it stayed green while the tool resolved a `vibe-ic-d`
    path that exists nowhere (#2348). Now the server is booted over MCP stdio
    with $VIBE_IC_PROGRAMS_DIR unset (the default resolver) and the tool's
    own answer is checked: it must have RUN, and the gate it names must be
    the canonical program on disk."""
    env = {k: v for k, v in os.environ.items() if k != "VIBE_IC_PROGRAMS_DIR"}
    env["TMPDIR"] = str(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "eda_phase23_completion_audit",
                    "arguments": {"project_dir": str(proj)}}},
    ]
    p = subprocess.Popen(["node", str(INDEX_JS)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, env=env, cwd=str(tmp_path))
    try:
        for m in msgs:
            p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
        reply, end = None, time.time() + 600
        while reply is None and time.time() < end:
            if not select.select([p.stdout], [], [], 1)[0]:
                continue
            line = p.stdout.readline()
            if not line:
                break
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == 2:
                reply = m
    finally:
        p.kill()
        p.wait()
    assert reply is not None, "the tool never answered"
    res = reply["result"]
    out = json.loads("".join(c.get("text", "") for c in res["content"]))
    gate = out.get("gate")
    assert gate, f"the tool did not report its resolved gate: {out}"
    assert out.get("status") == "MEASURED" and out.get("audit_ran") is True, (
        f"the tool could not run its gate at {gate}: {out}")
    gate = Path(gate)
    assert gate.name == GATE_NAME and gate.is_file(), gate
    assert gate.resolve() == (ROOT.parent / "programs" / GATE_NAME).resolve(), (
        f"the tool resolved {gate}, not the plugin's own programs/ gate")
