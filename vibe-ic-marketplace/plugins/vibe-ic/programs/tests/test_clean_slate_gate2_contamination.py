#!/usr/bin/env python3
"""vibe-ic#2221 — the contamination gate must be able to FAIL.

DELIBERATELY IMPORTS NOTHING THIS BRANCH ADDS. `plugin_clean_slate_test.sh`
ships on main today, so these two run against main's own sources and say what
main's gate does: for two stream-outs of ONE layout it answers "[OK] GDS md5
distinct — fresh build", because a GDSII stream stamps its own write time and
two stream-outs never share an md5. The gate could not fail in the direction it
exists to catch.

Its own byte builder, for the same reason: a shared helper would be taken from
main in the sources-only arm and this file would stop being a falsification.
"""
from __future__ import annotations

import hashlib
import struct
import subprocess
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
CLEAN_SLATE = PROGRAMS / "plugin_clean_slate_test.sh"

#: Two genuinely different wall-clock reads, in the shape a writer stamps.
_RUN_A = (2026, 8, 9, 13, 10, 28) * 2
_RUN_B = (2026, 9, 10, 2, 41, 7) * 2


def _rec(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload


def _gds(lib_date, str_date, *, width: int = 1000, structures: int = 40) -> bytes:
    """A real GDSII stream, > 1 KiB, with chosen dates and a chosen box."""
    out = _rec(0x00, 0x02, struct.pack(">h", 600))
    out += _rec(0x01, 0x02, struct.pack(">12h", *lib_date))
    out += _rec(0x02, 0x06, b"LIB\x00")
    out += _rec(0x03, 0x05, b"\x00" * 16)
    for _ in range(structures):
        out += _rec(0x05, 0x02, struct.pack(">12h", *str_date))
        out += _rec(0x06, 0x06, b"TOP\x00")
        out += _rec(0x08, 0x00)
        out += _rec(0x0D, 0x02, struct.pack(">h", 1))
        out += _rec(0x0E, 0x02, struct.pack(">h", 0))
        out += _rec(0x10, 0x03, struct.pack(
            ">10i", 0, 0, width, 0, width, 500, 0, 500, 0, 0))
        out += _rec(0x11, 0x00)
        out += _rec(0x07, 0x00)
    out += _rec(0x04, 0x00)
    return out


def _project(tmp_path: Path, raw: bytes) -> Path:
    proj = tmp_path / "proj"
    for d in ("gds", "rtl", "fpga/output_files", "reports"):
        (proj / d).mkdir(parents=True, exist_ok=True)
    (proj / "gds" / "chip_top.gds").write_bytes(raw)
    return proj


def _gate2_gds_line(proj: Path, baseline: Path) -> str:
    got = subprocess.run(
        ["bash", str(CLEAN_SLATE), "--project-dir", str(proj),
         "--baseline-gds", str(baseline)],
        capture_output=True, text=True)
    lines = [ln for ln in got.stdout.splitlines()
             if "GDS" in ln and ln.startswith(("[OK]", "[FAIL]"))]
    assert lines, ("Gate 2 printed no GDS verdict:\n"
                   + got.stdout + got.stderr)
    return lines[0]


@pytest.mark.skipif(not CLEAN_SLATE.is_file(), reason="script absent")
def test_gate2_catches_a_rebuild_of_the_very_same_layout(tmp_path):
    """The direction an md5 comparison can never fail in.

    Same geometry, later clock — what a contaminated re-stream really looks
    like. Its raw digest differs from the baseline's, which is exactly why
    comparing raw digests calls it fresh.
    """
    baseline = tmp_path / "baseline.gds"
    baseline.write_bytes(_gds(_RUN_A, _RUN_A))
    proj = _project(tmp_path, _gds(_RUN_B, _RUN_B))

    assert (hashlib.sha256(baseline.read_bytes()).hexdigest()
            != hashlib.sha256(
                (proj / "gds" / "chip_top.gds").read_bytes()).hexdigest()), (
        "the fixture must reproduce the defect it pins: two stream-outs of one "
        "layout have to differ in raw bytes, or this proves nothing")

    line = _gate2_gds_line(proj, baseline)
    assert line.startswith("[FAIL]"), (
        "Gate 2 accepted a rebuild of the very same layout as a fresh build: "
        + line)
    assert "contamination" in line


@pytest.mark.skipif(not CLEAN_SLATE.is_file(), reason="script absent")
def test_gate2_still_passes_a_genuinely_different_layout(tmp_path):
    """The negative control: a real fresh build must still be accepted."""
    baseline = tmp_path / "baseline.gds"
    baseline.write_bytes(_gds(_RUN_A, _RUN_A, width=1000))
    proj = _project(tmp_path, _gds(_RUN_B, _RUN_B, width=1400))
    line = _gate2_gds_line(proj, baseline)
    assert line.startswith("[OK]"), line
    assert "distinct" in line
