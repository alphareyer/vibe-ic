"""tests/test_m1_top_lvs_freshness.py — `mixed_signal_top_lvs_run` must not
certify a comparison that never happened.

MEASURED DEFECT (2026-08-01). Every tool step's success test was "does the
output file exist on the HOST". Those files survive a `cp -a` from another
run in another directory, so an invocation in which magic and netgen never
executed emitted, verbatim:

    "verdict": "FAIL", "compared": true,
    "reason": "netgen top-level LVS did not match — real compare ran on the
               merged GDS; design/extraction defect"

while `ext2spice_merged.log` kept its mtime from 2h52m earlier and
`top_lvs.rpt` its mtime from 4h16m earlier. `mixed_signal_merge_check` —
M1's BLOCKING gate — reads that file.

Every test here FAILS against the pre-freshness program: it had no notion
of "did THIS invocation run the tool", so each of these inputs produced a
confident verdict about a comparison nobody performed.

The fakes write into paths derived from the project root rather than parsed
out of the command line, because this container's account name contains a
newline and `\\S+` path extraction truncates on it — an environment quirk,
not a subject of these tests.

chip-AGNOSTIC: monkeypatched container + synthetic fixtures.
"""
import json
import hashlib
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mixed_signal_top_lvs_run as TL  # noqa: E402

TOP = "chip_top"


def _project(tmp_path):
    pnr = tmp_path / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / "routed.def").write_text(
        f"DESIGN {TOP} ;\nUNITS DISTANCE MICRONS 1000 ;\n"
        "COMPONENTS 1 ;\n- u_ldo ldo + FIXED ( 0 0 ) N ;\n"
        "END COMPONENTS\nEND DESIGN\n")
    g = tmp_path / "phase3" / "stage4" / "gds"
    g.mkdir(parents=True)
    (g / f"{TOP}.gds").write_bytes(b"\x00\x06digital")
    hm = tmp_path / "phase3" / "analog" / "hardmacro" / "ldo"
    hm.mkdir(parents=True)
    (hm / "ldo.gds").write_bytes(b"\x00\x06macro")
    (hm / "ldo.v").write_text("module ldo(input en, output vout);\nendmodule\n")
    sy = tmp_path / "phase2" / "stage2" / "synth"
    sy.mkdir(parents=True)
    (sy / f"{TOP}_synth.v").write_text(
        f"module {TOP}(input en, output vout);\n"
        "ldo u_ldo (.en(en), .vout(vout));\nendmodule\n")
    return tmp_path


def _ms(p):
    d = p / "phase3" / "mixed_signal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rpt(p):
    d = p / "reports" / "analog" / "mixed_signal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _carried_forward(p, lvs_text="Netlists do not match.\n"):
    """Exactly what `cp -a` from a finished run in another directory leaves
    behind: every output present, every one of them older than this run."""
    ms, rp = _ms(p), _rpt(p)
    (ms / "top_merged.gds").write_bytes(b"\x00\x06merged")
    (ms / f"{TOP}_merged_extracted.sp").write_text(
        f".subckt {TOP} a b\n.ends\n")
    (ms / "merge.log").write_text("KLAYOUT_MERGE_DONE\n")
    (ms / "ext2spice_merged.log").write_text("MAGIC_EXT2SPICE_DONE\n")
    (rp / "top_lvs.rpt").write_text("Netgen 1.5\n" + lvs_text)
    old = time.time() - 7200
    for f in (ms / "top_merged.gds", ms / f"{TOP}_merged_extracted.sp",
              ms / "merge.log", ms / "ext2spice_merged.log",
              rp / "top_lvs.rpt"):
        import os
        os.utime(f, (old, old))
    return p


def _fake_nothing_runs(reachable=True):
    """Every tool invocation fails to find its inputs — the shape produced by
    a project dir that is not mounted inside the container."""
    def fake(container, cmd, timeout=600, **_):
        if cmd.startswith("command -v") or cmd.startswith("test -f"):
            return 0, "", ""
        if cmd.startswith("test -d"):
            return (0 if reachable else 1), "", ""
        return 1, "", "No such file or directory"
    return fake


# ── the pre-flight ────────────────────────────────────────────────────────

def test_skips_when_the_project_is_not_reachable_in_the_container(tmp_path):
    p = _carried_forward(_project(tmp_path))
    TL._docker_exec, orig = _fake_nothing_runs(reachable=False), TL._docker_exec
    try:
        rep = TL.run(p, TOP, "some-container", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["rc"] == 2 and rep["verdict"] == "SKIP"
    assert "not reachable inside container" in rep["reason"]
    assert "some-container" in rep["reason"]
    # and it must NOT have claimed a comparison
    assert rep.get("compared") is not True


# ── freshness per tool ────────────────────────────────────────────────────

def test_no_verdict_when_magic_did_not_run_this_invocation(tmp_path):
    """The exact carried-forward shape: the extracted .sp is on disk, so the
    old presence test passed it straight through to netgen."""
    p = _carried_forward(_project(tmp_path))
    TL._docker_exec, orig = _fake_nothing_runs(), TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "FAIL" and rep["rc"] == 1
    assert "did not complete in THIS run" in rep["reason"]
    assert "real compare ran" not in rep["reason"]
    assert rep.get("compared") is not True


def test_no_compare_claimed_when_netgen_did_not_rewrite_the_report(tmp_path):
    """magic runs; netgen does not. The stale report says "Netlists do not
    match", and the old code classified it as a real LVS mismatch."""
    p = _carried_forward(_project(tmp_path))
    ms = _ms(p)

    def fake(container, cmd, timeout=600, **_):
        if cmd.startswith("command -v") or cmd.startswith("test -f") \
                or cmd.startswith("test -d"):
            return 0, "", ""
        if "magic" in cmd:
            (ms / f"{TOP}_merged_extracted.sp").write_text(
                f".subckt {TOP} a b\n.ends\n")
            (ms / "ext2spice_merged.log").write_text("MAGIC_EXT2SPICE_DONE\n")
            return 0, "MAGIC_EXT2SPICE_DONE", ""
        if "netgen" in cmd:
            return 1, "", "netgen: no such file"
        return 0, "", ""

    TL._docker_exec, orig = fake, TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "FAIL" and rep["rc"] == 1
    assert rep["compared"] is False
    assert "Nothing was compared" in rep["reason"]
    assert "NOT an LVS mismatch" in rep["reason"]


# ── provenance of a reused merge ──────────────────────────────────────────

def test_a_reused_merged_gds_is_disclosed_by_name(tmp_path):
    """An unbound carried merge cannot stand in for this invocation.

    Current declared inputs reach KLayout, which refuses here. Magic/netgen
    could report a match, but that cannot certify the unregenerated layout.
    The native current-input positive is retained in the bounded M1 fixture.
    """
    p = _carried_forward(_project(tmp_path))
    ms, rp = _ms(p), _rpt(p)
    os.utime(p / f"phase3/stage4/gds/{TOP}.gds", None)
    calls = []

    def fake(container, cmd, timeout=600, **_):
        calls.append(cmd)
        if cmd.startswith("command -v") or cmd.startswith("test -f") \
                or cmd.startswith("test -d"):
            return 0, "", ""
        if "M1_TOOL_IDENTITY" in cmd:
            return 0, json.dumps({"executable": "/tools/klayout",
                                  "sha256": "d" * 64,
                                  "version": "KLayout fixture"}), ""
        if "klayout -b" in cmd:
            return 1, "", "KLayout did not produce a current merged layout"
        if "magic" in cmd:
            (ms / f"{TOP}_merged_extracted.sp").write_text(
                f".subckt {TOP} a b\n.ends\n")
            (ms / "ext2spice_merged.log").write_text("MAGIC_EXT2SPICE_DONE\n")
            return 0, "MAGIC_EXT2SPICE_DONE", ""
        if "netgen" in cmd:
            (rp / "top_lvs.rpt").write_text(
                "Netgen 1.5\nFinal result: Circuits match uniquely.\n")
            return 0, "Final result: Circuits match uniquely.", ""
        return 0, "", ""

    TL._docker_exec, orig = fake, TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "FAIL" and rep["rc"] == 1, rep
    assert "KLayout merge did not complete in THIS run" in rep["reason"], rep
    assert any("klayout -b" in cmd for cmd in calls), calls
    assert rep.get("compared") is False, rep
    assert (ms / "top_merged.gds").read_bytes() == b"\x00\x06merged"


def _write_a8_manifest(p, *, source_sha=None, view_shas=None):
    hm = p / "phase3/analog/hardmacro/ldo"
    source = p / "phase3/analog/ldo/ldo.gds"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00\x06source")
    (hm / "ldo.lef").write_text("VERSION 5.8 ;\n")
    (hm / "ldo.lib").write_text("library(ldo) {}\n")
    files = {suffix: hm / f"ldo{suffix}"
             for suffix in (".lef", ".lib", ".gds", ".v")}
    actual = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema": "vibe-ic/analog_a8_views/1",
        "producer": "analog_a8_hardmacro_emit",
        "block": "ldo",
        "source_gds": str(source.relative_to(p)),
        "source_gds_sha256": source_sha or actual(source),
        "views": {suffix: {"path": str(path.relative_to(p)),
                            "sha256": (view_shas or {}).get(suffix,
                                        actual(path))}
                  for suffix, path in files.items()},
    }
    (hm / "a8_views_provenance.json").write_text(json.dumps(manifest))


def test_m1_accepts_a_valid_a8_manifest_bound_to_consumed_views(tmp_path):
    p = _project(tmp_path)
    _write_a8_manifest(p)
    _carried_forward(p)
    ms, rp = _ms(p), _rpt(p)

    def fake(container, cmd, timeout=600, **_):
        if cmd.startswith("command -v") or cmd.startswith("test -f") \
                or cmd.startswith("test -d"):
            return 0, "", ""
        if "M1_TOOL_IDENTITY" in cmd:
            return 0, json.dumps({"executable": "/tools/klayout",
                                  "sha256": "d" * 64,
                                  "version": "KLayout fixture"}), ""
        if "klayout -b" in cmd:
            tokens = shlex.split(cmd)
            env = dict(t.split("=", 1) for t in tokens if "=" in t)
            out = Path(env["MERGED_OUT"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fresh merged gds")
            (ms / "merge.log").write_text("KLAYOUT_MERGE_DONE\n")
            placement = json.loads(
                Path(env["PLACEMENTS_JSON"]).read_text())["placements"]
            placed = [{"macro": cell, **item}
                      for cell, items in placement.items() for item in items]
            Path(env["MERGE_JSON"]).write_text(json.dumps({
                "design_top": TOP, "single_top": True,
                "top_cells_after": [TOP], "placed": placed,
                "macros": [{"macro": "ldo", "action": "added"}],
            }))
            return 0, "KLAYOUT_MERGE_DONE", ""
        if "magic" in cmd:
            (ms / f"{TOP}_merged_extracted.sp").write_text(
                f".subckt {TOP} a b\n.ends\n")
            (ms / "ext2spice_merged.log").write_text(
                "MAGIC_EXT2SPICE_DONE\n")
            return 0, "MAGIC_EXT2SPICE_DONE", ""
        if "netgen" in cmd:
            (rp / "top_lvs.rpt").write_text(
                "Netgen 1.5\nFinal result: Circuits match uniquely.\n")
            return 0, "Final result: Circuits match uniquely.", ""
        return 0, "", ""

    TL._docker_exec, orig = fake, TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "PASS" and rep["rc"] == 0
    assert rep["macro_views_consumed"][0]["a8_view_hashes"][".gds"]


def test_m1_refuses_a_mismatched_a8_manifest_before_lvs(tmp_path):
    p = _project(tmp_path)
    _write_a8_manifest(p, source_sha="0" * 64,
                       view_shas={suffix: "1" * 64
                                  for suffix in (".lef", ".lib", ".gds", ".v")})
    def fake(container, cmd, timeout=600, **_):
        if cmd.startswith("command -v") or cmd.startswith("test -f") \
                or cmd.startswith("test -d"):
            return 0, "", ""
        return 1, "", "not reached"
    TL._docker_exec, orig = fake, TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "SKIP" and rep["rc"] == 2
    assert "A8_VIEW_PROVENANCE_INVALID" in rep["reason"]


def test_m1_refuses_the_reviewer_manifest_shape_without_required_views(tmp_path):
    p = _project(tmp_path)
    hm = p / "phase3/analog/hardmacro/ldo"
    (hm / "a8_views_provenance.json").write_text(json.dumps({
        "source_gds_sha256": "0" * 64,
        "views": {"gds": {"sha256": "1" * 64},
                  "verilog": {"sha256": "2" * 64}},
    }))
    def fake(container, cmd, timeout=600, **_):
        if cmd.startswith("command -v") or cmd.startswith("test -f") \
                or cmd.startswith("test -d"):
            return 0, "", ""
        return 1, "", "not reached"
    TL._docker_exec, orig = fake, TL._docker_exec
    try:
        rep = TL.run(p, TOP, "x", "sky130A")
    finally:
        TL._docker_exec = orig
    assert rep["verdict"] == "SKIP" and rep["rc"] == 2
    assert "A8_VIEW_PROVENANCE_INVALID" in rep["reason"]


# ── unit: _ran_fresh ──────────────────────────────────────────────────────

def test_ran_fresh_rejects_a_log_that_did_not_advance(tmp_path):
    log = tmp_path / "t.log"
    log.write_text("MARKER\n")
    before = log.stat().st_mtime
    assert TL._ran_fresh(log, "MARKER", before) is False


def test_ran_fresh_rejects_a_fresh_log_without_the_marker(tmp_path):
    log = tmp_path / "t.log"
    before = None
    log.write_text("segfault\n")
    assert TL._ran_fresh(log, "MARKER", before) is False


def test_ran_fresh_accepts_a_fresh_log_carrying_the_marker(tmp_path):
    log = tmp_path / "t.log"
    before = None
    log.write_text("... MARKER /x/y\n")
    assert TL._ran_fresh(log, "MARKER", before) is True


def test_ran_fresh_rejects_an_absent_or_empty_log(tmp_path):
    assert TL._ran_fresh(tmp_path / "nope.log", "MARKER", None) is False
    empty = tmp_path / "e.log"
    empty.write_text("")
    assert TL._ran_fresh(empty, "MARKER", None) is False


def test_ran_fresh_with_no_marker_still_requires_freshness(tmp_path):
    """netgen prints no completion token; the report it writes IS the marker,
    so an empty marker must still mean "this invocation wrote it"."""
    rpt = tmp_path / "r.rpt"
    rpt.write_text("Netgen 1.5\n")
    stale = rpt.stat().st_mtime
    assert TL._ran_fresh(rpt, "", stale) is False
    import os
    os.utime(rpt, (stale + 10, stale + 10))
    assert TL._ran_fresh(rpt, "", stale) is True
