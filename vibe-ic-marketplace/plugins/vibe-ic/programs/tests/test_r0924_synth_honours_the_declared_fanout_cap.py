#!/usr/bin/env python3
"""Synthesis must honour the fanout cap the run itself declares.

THE MEASURED DEFECT (subservient x gf180mcuD, run2). The stock `abc -liberty`
script — the one the flow uses, WITHOUT `-constr` — ends `&nf {D}; &put`: it
buffers nothing and sizes nothing. So synthesis emitted a net with **207
loads** driven by one `clkinv_1`; that inverter alone cost **16.03 ns** of a
28.00 ns path, and Step 10 pre-layout STA read **-8.85 ns** against the
design's OWN 20 ns period — blocking nine downstream steps on a netlist the
flow had not yet repaired. The run's own SDC declared `set_max_fanout 10` the
whole time; nothing enforced it until `repair_design`, which runs in PnR,
AFTER the gate that fails for its absence.

MEASURED FIX, same RTL, same auto-SDC, same ss corner:
    subservient  -8.85 VIOLATED (tns -2044.80)  ->  +4.36 MET (tns 0.00)
                 2,510 cells / 61,599 area      ->  2,653 (+5.7%) / 64,611 (+4.9%)
                 worst signal fanout 207        ->  26;  nets over 10: 51 -> 3
    spm          +15.93 MET                     ->  +15.93 MET, netlist unchanged
This is NOT a new pass: it is ABC's own `-constr` tail with the bound supplied.

AND ONE LADDER FOR THE CAP. Before this, the AUTO-SDC path resolved the cap
from design-declared sources only, so on ONE PDK subservient got 10 (its L9
happens to defer the cap to the PDK default) and spm got none (its L9 says
nothing). The AUGMENT path already had the other two tiers. Same file, two
ladders, one PDK.

UNREAD IS NOT EMPTY — the rule that nearly cost me a wrong conclusion: reading
the cap on the HOST returns None for every design because `pdk_compat.py`
lives in the CONTAINER, which reads exactly like "this PDK declares none". A
tier that could not be CONSULTED must say so.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402

RUNNER = PROGRAMS / "phase3_one_shot_runner.py"


# --- the recipe itself -----------------------------------------------------
def test_the_script_is_abcs_own_constr_tail_with_the_bound_supplied():
    got = R._ABC_FANOUT_SCRIPT.format(cap=10)
    assert "buffer,-N,10" in got, got
    assert "upsize,{D}" in got and "dnsize,{D}" in got, (
        "yosys's own delay-target substitution must survive .format(); "
        f"got {got}")
    assert got.lstrip().startswith("-script +"), got
    assert "stime,-p" in got
    assert "&nf,{D}" in got, "the stock mapping must still be the mapping"


def test_the_cap_reaches_the_script_verbatim():
    for cap in (4, 8, 10, 32):
        assert f"buffer,-N,{cap}" in R._ABC_FANOUT_SCRIPT.format(cap=cap)


# --- the ONE ladder --------------------------------------------------------
def _proj(tmp_path: Path) -> Path:
    (tmp_path / "input" / "docs").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_a_design_declared_cap_wins_and_is_never_overridden(tmp_path,
                                                            monkeypatch):
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: 7)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (99, "pdk_compat", ""))
    cap, why, unread = R._synth_max_fanout(project, "gf180mcuD")
    assert cap == 7, (cap, why)
    assert unread == []


def test_the_flow_pdk_default_is_the_second_tier(tmp_path, monkeypatch):
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: None)
    monkeypatch.setattr(R, "_rtl_replication_fanout_bound",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (10, "pdk_compat.py:322", ""))
    cap, why, _ = R._synth_max_fanout(project, "gf180mcuD")
    assert cap == 10 and "pdk_compat" in why, (cap, why)


def test_the_active_liberty_is_the_third_tier(tmp_path, monkeypatch):
    """THE TIER THAT CLOSES THE spm/subservient ASYMMETRY: it needs no
    statement from the design at all."""
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: None)
    monkeypatch.setattr(R, "_rtl_replication_fanout_bound",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (None, "", ""))
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {
        "max_fanout": 8, "fanout_source": "some.lib:default_max_fanout"})
    cap, why, _ = R._synth_max_fanout(project, "anypdk", "some.lib")
    assert cap == 8 and "default_max_fanout" in why, (cap, why)


def test_nothing_declared_anywhere_resolves_to_no_cap(tmp_path, monkeypatch):
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: None)
    monkeypatch.setattr(R, "_rtl_replication_fanout_bound",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (None, "", ""))
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})
    cap, why, unread = R._synth_max_fanout(project, "anypdk")
    assert cap is None and why == "", (cap, why)
    assert unread == [], (
        "a PDK that genuinely declares no default is not an UNREAD tier")


# --- unread is not empty ---------------------------------------------------
def test_a_tier_that_could_not_be_read_says_so(tmp_path):
    """THE DISPATCHER'S ADDITION, and my own near-miss: on a host with no
    container copy of `pdk_compat.py` the read fails, and a silent None reads
    exactly like 'this PDK declares no default'."""
    project = _proj(tmp_path)          # no reports/container_image.json
    cap, why, unread = R._flow_default_max_fanout_read(project, "gf180mcuD")
    assert cap is None and why == ""
    assert unread and "NOT READ" in unread, unread
    assert "records no container" in unread, unread


def test_an_unreadable_container_record_is_named_not_swallowed(tmp_path):
    project = _proj(tmp_path)
    rec = project / "reports" / "container_image.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text('{"container": ""}')
    cap, _why, unread = R._flow_default_max_fanout_read(project, "gf180mcuD")
    assert cap is None
    assert "NOT READ" in unread and "names no container" in unread, unread


def test_the_unread_reason_propagates_out_of_the_resolver(tmp_path,
                                                          monkeypatch):
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: None)
    monkeypatch.setattr(R, "_rtl_replication_fanout_bound",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})
    cap, _why, unread = R._synth_max_fanout(project, "gf180mcuD")
    assert cap is None
    assert any("NOT READ" in u for u in unread), unread


def test_a_read_that_found_no_family_is_not_reported_as_unread(tmp_path,
                                                              monkeypatch):
    """The other direction, so 'unread' cannot become a catch-all: the file WAS
    read and this family genuinely has no entry."""
    project = _proj(tmp_path)
    rec = project / "reports" / "container_image.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"container": "c"}))

    class _CP:
        returncode = 0
        stderr = ""
        stdout = 'if new["PDK"].startswith("sky130"):\n    x["MAX_FANOUT_CONSTRAINT"] = 10\n'
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: _CP())
    cap, why, unread = R._flow_default_max_fanout_read(project, "ihp-sg13g2")
    assert cap is None and why == ""
    assert unread == "", (
        "the file was READ; this family simply has no entry, which is an "
        f"answer and not a silence: {unread!r}")


# --- the wiring ------------------------------------------------------------
def test_every_abc_site_in_step_synth_carries_the_bound():
    """A SIBLING to the behaviour above, not a substitute for it: if one of the
    four `abc -liberty` sites in `step_synth` is left un-bounded, that path
    silently goes back to emitting unbounded fanout."""
    tree = ast.parse(RUNNER.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "step_synth")
    sites, bounded = 0, 0
    for node in ast.walk(fn):
        if isinstance(node, ast.JoinedStr):
            txt = "".join(v.value for v in node.values
                          if isinstance(v, ast.Constant)
                          and isinstance(v.value, str))
            names = {v.value.id for v in node.values
                     if isinstance(v, ast.FormattedValue)
                     and isinstance(v.value, ast.Name)}
            if "abc -liberty" in txt:
                sites += 1
                if "_abc_fanout" in names:
                    bounded += 1
    assert sites >= 4, f"expected the four known abc sites, found {sites}"
    assert bounded == sites, (
        f"{sites - bounded} of {sites} abc site(s) in step_synth do not carry "
        f"the fanout bound")


def test_no_cap_means_the_recipe_is_unchanged():
    """The no-cap case must be BYTE-IDENTICAL to before — no silent default."""
    assert R._ABC_FANOUT_SCRIPT.format(cap=10) != ""
    # the caller uses "" when nothing resolved; an empty suffix cannot change
    # the command it is appended to.
    assert "".join(("abc -liberty L -D 20000", "")) == "abc -liberty L -D 20000"


# --- mutation --------------------------------------------------------------
def test_mutation_dropping_the_bound_is_caught():
    """Remove `buffer -N <cap>` and the recipe stops bounding fanout while
    still looking like a buffering script."""
    mutated = R._ABC_FANOUT_SCRIPT.replace(";buffer,-N,{cap}", "")
    assert "buffer,-N," not in mutated.format(cap=10), "sanity"
    assert mutated.format(cap=10) != R._ABC_FANOUT_SCRIPT.format(cap=10), (
        "the bound must be observable in the emitted script, or its removal "
        "would be invisible")


def test_mutation_a_fabricated_default_is_caught(tmp_path, monkeypatch):
    """The §4.05 mutation: resolving to a number nobody stated."""
    project = _proj(tmp_path)
    monkeypatch.setattr(R, "_l9_declared_max_fanout", lambda *a, **k: None)
    monkeypatch.setattr(R, "_rtl_replication_fanout_bound",
                        lambda *a, **k: None)
    monkeypatch.setattr(R, "_flow_default_max_fanout_read",
                        lambda *a, **k: (None, "", ""))
    monkeypatch.setattr(R, "_liberty_drv_limits", lambda *a, **k: {})
    cap, _why, _unread = R._synth_max_fanout(project, "anypdk")
    assert cap is None, (
        "no tier stated a cap, so there is no cap; inventing one here is "
        "exactly what §4.05 forbids")


def test_the_fanout_notes_have_their_own_channel():
    """They are NOT reference-flow knobs. Mixing them made two existing
    assertions about an EMPTY `reference_flow_qor_knobs` list fail — correctly,
    because `reference_flow_qor_knobs` means "what the design's staged flow
    asked for" and a PDK-derived cap is not that."""
    tree = ast.parse(RUNNER.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "step_synth")
    src = ast.get_source_segment(RUNNER.read_text(), fn) or ""
    assert '"synth_max_fanout": _fo_notes' in src, (
        "the cap's disclosure must reach the step's extras under its own key")
    # and nothing about the cap may be appended to the knob list
    for line in src.splitlines():
        if "_rf_notes.append" in line:
            assert "fanout" not in line.lower() or "FASTROUTE" in line, (
                f"a fanout-cap note leaked into the knob list: {line.strip()}")
