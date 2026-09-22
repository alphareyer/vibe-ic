#!/usr/bin/env python3
"""Tests for internal_vs_external_timing_check.py"""
from __future__ import annotations
import subprocess, sys, json
from pathlib import Path
import pytest

PROG = Path(__file__).resolve().parent.parent / "internal_vs_external_timing_check.py"

def _run(args: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PROG)] + args, capture_output=True, text=True, **kw)

def test_help():
    r = _run(["--help"])
    assert r.returncode == 0

def test_with_waveform(tmp_path):
    # v0.2.55: an L8 with NO protocol/symbol timing content (empty waveforms,
    # no rx_*/tx_* group keys) is N/A for the RX/TX-split rule — a non-protocol
    # IC (e.g. a pure-digital arithmetic primitive) has nothing to split. The
    # gate VACUOUS_PASSes (rc=0) instead of FAILing. A genuinely half-duplex L8
    # that carries only the host-side half is still caught (see the rx_*/tx_*
    # fixtures in test_internal_vs_external_timing_check.py).
    wf = tmp_path / "L8_TIMING_WAVEFORM.json"
    wf.write_text(json.dumps({"waveforms": []}))
    r = _run([str(wf)])
    assert r.returncode == 0
    # THE TIER, and it is NOT re-pinned: this branch was a VACUOUS_PASS before
    # R-0915-124/125 and still is. `flow_compliance_check._stdout_signals_vacuous`
    # believes the disclosure only where the token BEGINS a line, and a clause
    # invoking this gate without `--json` has stdout as its only channel, so the
    # position is load-bearing and not cosmetic.
    assert r.stdout.lstrip().startswith("VACUOUS_PASS"), r.stdout
    # THE CLASS, which is what changed. This fixture stages NO L2 and an L8 empty
    # of protocol content, so it is the ENUMERATING escape -- the gate infers the
    # absence rather than reading a declaration -- and R-0915-124/125 require it be
    # named by structure WITH the enumeration behind it.
    assert "NOT_APPLICABLE_BY_STRUCTURE" in r.stdout
    # AND THE EVIDENCE, not merely the token: a class with nothing behind it is
    # the thing that guard refuses, so assert the enumeration the line carries.
    assert "enumerated 3" in r.stdout and "found 0" in r.stdout, r.stdout
