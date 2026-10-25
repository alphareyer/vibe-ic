#!/usr/bin/env python3
"""Typed clock/reset facts must reach L8's release-document consumer field."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_PROGRAMS = _HERE.parent
sys.path.insert(0, str(_PROGRAMS))

import l8_clock_reset_waveform_emit as EMIT  # noqa: E402
import _atomic_artefact as A  # noqa: E402
import phase1_doc_one_shot_runner as RUNNER  # noqa: E402

HELPER = "_post_emit_l8_clock_reset_waveform"
RUNNER_PATH = _PROGRAMS / "phase1_doc_one_shot_runner.py"


@pytest.mark.parametrize('metadata', [
    'written_at: 2026-05-22',
    'released: 2025/10/12',
    'build_number: 12345',
])
def test_document_metadata_is_not_a_timing_constant(tmp_path, metadata):
    fixture = (_HERE / 'fixtures/phase1_local/metadata_date_repro2996/timing_notes.md').read_text()
    source = fixture.replace('written_at: 2026-05-22', metadata)
    source += '\nWAIT_LIMIT = 5000 cycles\nT_DELAY_TICKS = 6000\nBUFFER_WORDS = 9000\npublished_on: 2026-05-22\n'
    (tmp_path / 'phase1/generated_docs').mkdir(parents=True)
    result = RUNNER.gen_l8_timing_waveform(tmp_path, {'timing_notes.md': source})
    doc = json.loads(Path(result.path).read_text())
    rows = doc.get('timing_parameters', []) + doc.get('timing_constants', [])
    names = {r['name'] for r in rows}
    assert not names.intersection({'written_at', 'released', 'build_number', 'published_on', 'BUFFER_WORDS'})
    for name, value, unit in [('T_GUARD', 7000, 'ns'), ('WAIT_LIMIT', 5000, 'cycles'), ('T_DELAY_TICKS', 6000, 'ticks')]:
        assert any(r['name'] == name and r['value'] == value and r['unit'] == unit for r in rows)


def test_front_matter_value_can_be_reused_by_explicit_technical_statement(tmp_path):
    source = '---\nT_GUARD: 2026-05-22\n---\n# Timing\nT_GUARD = 7000 ns\n'
    (tmp_path / 'phase1/generated_docs').mkdir(parents=True)
    result = RUNNER.gen_l8_timing_waveform(tmp_path, {'timing_notes.md': source})
    doc = json.loads(Path(result.path).read_text())
    assert {r['value'] for r in doc['timing_constants'] if r['name'] == 'T_GUARD'} == {7000}


def _write(project: Path, *, clock: bool = True, reset: bool = True,
           existing=None) -> None:
    docs = project / "phase1" / "generated_docs"
    docs.mkdir(parents=True, exist_ok=True)
    l8 = {"doc_class": "timing_waveform", "clocks": []}
    if clock:
        l8["clocks"] = [{
            "name": "clk", "period_ns": 24, "freq_mhz": 41.6666666667,
            "pdk_scoped_target": "process_family_a",
            "evidence": {"file": "input/docs/timing.md", "line": 7},
        }]
    if existing is not None:
        l8["clock_and_reset_waveform"] = existing
    l9 = {"doc_class": "integration_spec", "clocks": [
        {"name": "clk", "edge": "posedge"}], "reset_domains": [],
        "ports": []}
    if reset:
        l9["reset_domains"] = [{
            "name": "rst", "polarity": "active_high", "sync": "unknown",
            "extraction_strategy": "typed_reset_fixture",
        }]
        l9["ports"] = [{
            "name": "rst", "description":
            "Synchronous active-high reset; state clears within one cycle."}]
    (docs / "L8_TIMING_WAVEFORM.json").write_text(
        json.dumps(l8), encoding="utf-8")
    (docs / "L9_INTEGRATION_SPEC.json").write_text(
        json.dumps(l9), encoding="utf-8")


def _l8(project: Path) -> dict:
    return json.loads((project / EMIT.L8_REL).read_text(encoding="utf-8"))


def test_typed_clock_and_reset_reach_release_doc_field(tmp_path):
    _write(tmp_path)
    assert getattr(RUNNER, HELPER)(tmp_path) == 1
    value = _l8(tmp_path)["clock_and_reset_waveform"]
    assert value["clocks"][0]["period_ns"] == 24
    assert value["clocks"][0]["edge"] == "posedge"
    assert value["resets"][0]["polarity"] == "active_high"
    assert "Synchronous active-high" in value["resets"][0]["port_description"]
    assert value["derived_from"] == [EMIT.L8_REL, EMIT.L9_REL]


def _derive_source_clock_waveform(project, source, *, names=("clk",), domains=()):
    _write(project)
    inputs = project / "input/docs"
    inputs.mkdir(parents=True)
    (inputs / "timing.md").write_text(source)
    path = project / EMIT.L9_REL
    l9 = json.loads(path.read_text())
    l9.pop("clocks", None)
    l9["clock_domains"] = list(domains)
    l9["top_ports"] = [{"name": name, "direction": "input",
                         "description": "Clock input."} for name in names]
    l9["ports"] += l9["top_ports"]
    path.write_text(json.dumps(l9))
    l8 = _l8(project)
    l8["clocks"] = [{"name": name, "period_ns": 24} for name in names]
    (project / EMIT.L8_REL).write_text(json.dumps(l8))
    RUNNER._post_emit_derive_l9_clocks_v1_6_323(project)
    assert getattr(RUNNER, HELPER)(project) == 1
    return json.loads(path.read_text()), _l8(project)["clock_and_reset_waveform"]


@pytest.mark.parametrize("source,expected", [
    ("All DFFs should be triggered by the negative edge of clk.", "negedge"),
    ("Data is sampled on the positive edge of clk.", "posedge"),
    ("The input clock is clk; clear is synchronous.", None),
    ("Clear on the negative edge of rst, sample on the positive edge of clk.", "posedge"),
    ("Clear on the negative edge of rst; clk is the sampling clock.", None),
    ("Data must not be sampled on the negative edge of clk.", None),
    ("Historical behavior: sample on the negative edge of clk.", None),
    ("For example, sample on the negative edge of clk.", None),
    ("Data is sampled on both rising and falling edges of clk.", None),
])
def test_source_bound_clock_edge_reaches_l9_and_actual_l8_projection(tmp_path, source, expected):
    l9, waveform = _derive_source_clock_waveform(tmp_path, source)
    assert {row["name"]: row.get("edge") for row in l9["clocks"]} == {"clk": expected}
    assert {row["name"]: row.get("edge") for row in waveform["clocks"]} == {"clk": expected}
    assert waveform["clocks"][0]["period_ns"] == 24
    if expected is None:
        assert "No unique source-backed" in l9["clocks"][0]["notes"]


def test_owning_typed_clock_edge_precedes_a_prose_fallback(tmp_path):
    l9, waveform = _derive_source_clock_waveform(
        tmp_path, "Data is sampled on the positive edge of clk.",
        domains=[{"name": "clk", "edge": "negedge", "period_ns": 24}])
    assert l9["clocks"][0]["edge"] == "negedge"
    assert waveform["clocks"][0]["edge"] == "negedge"


def test_clock_edge_clauses_remain_scoped_to_their_own_clock(tmp_path):
    l9, waveform = _derive_source_clock_waveform(
        tmp_path, "Sample on the negative edge of clk_read and the positive edge of clk_write.",
        names=("clk_read", "clk_write"))
    expected = {"clk_read": "negedge", "clk_write": "posedge"}
    assert {row["name"]: row["edge"] for row in l9["clocks"]} == expected
    assert {row["name"]: row["edge"] for row in waveform["clocks"]} == expected


def test_conflicting_stated_clock_edges_are_not_defaulted_to_positive(tmp_path):
    l9, waveform = _derive_source_clock_waveform(
        tmp_path, "Sample on the negative edge of clk. Sample on the positive edge of clk.")
    assert l9["clocks"][0].get("edge") is None
    assert waveform["clocks"][0].get("edge") is None


@pytest.mark.parametrize("clock,reset", [(False, True), (True, False)])
def test_one_sided_fact_set_is_named_skip_and_writes_nothing(
        tmp_path, capsys, clock, reset):
    _write(tmp_path, clock=clock, reset=reset)
    path = tmp_path / EMIT.L8_REL
    before = path.read_bytes()
    assert getattr(RUNNER, HELPER)(tmp_path) == 0
    assert "L8 clock/reset waveform: SKIPPED" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_protocol_specific_existing_waveform_wins(tmp_path):
    existing = {"ACLK": "rising edge", "ARESETn": "active low"}
    _write(tmp_path, existing=existing)
    assert getattr(RUNNER, HELPER)(tmp_path) == 0
    assert _l8(tmp_path)["clock_and_reset_waveform"] == existing


@pytest.mark.parametrize(("description", "edge"), [
    ("all data is sampled on the rising edge", "posedge"),
    ("all data is sampled on the falling edge", "negedge"),
])
def test_retained_waveform_refreshes_missing_edge_from_named_l9_description(
        tmp_path, description, edge):
    _write(tmp_path, existing={"clocks": [{"name": "clk", "period_ns": 24}],
                               "resets": [{"name": "rst", "sync": "synchronous"}]})
    l9_path = tmp_path / EMIT.L9_REL
    l9 = json.loads(l9_path.read_text())
    l9["clocks"] = []
    l9["ports"] = [{"name": "clk", "description": description}]
    l9_path.write_text(json.dumps(l9))
    assert getattr(RUNNER, HELPER)(tmp_path) == 1
    row = _l8(tmp_path)["clock_and_reset_waveform"]["clocks"][0]
    assert row["edge"] == edge and row["edge_derived_from"] == EMIT.L9_REL


@pytest.mark.parametrize("description", ["clock input", "not a rising edge",
                                          "rising edge and falling edge"])
def test_missing_or_negated_description_does_not_guess_edge(tmp_path, description):
    existing = {"clocks": [{"name": "clk", "period_ns": 24}],
                "resets": [{"name": "rst", "sync": "synchronous"}]}
    _write(tmp_path, existing=existing)
    l9_path = tmp_path / EMIT.L9_REL
    l9 = json.loads(l9_path.read_text()); l9["clocks"] = []
    l9["ports"] = [{"name": "clk", "description": description}]
    l9_path.write_text(json.dumps(l9))
    assert getattr(RUNNER, HELPER)(tmp_path) == 0
    assert _l8(tmp_path)["clock_and_reset_waveform"] == existing


def test_retained_waveform_refresh_does_not_require_duplicate_top_level_facts(tmp_path):
    existing = {"clocks": [{"name": "clk", "period_ns": 20}],
                "resets": [{"name": "rst", "sync": "synchronous",
                            "polarity": "active_high"}]}
    _write(tmp_path, clock=False, reset=False, existing=existing)
    l9_path = tmp_path / EMIT.L9_REL
    l9 = {"ports": [{"name": "clk", "description": "rising edge sample clock"}]}
    l9_path.write_text(json.dumps(l9))
    assert getattr(RUNNER, HELPER)(tmp_path) == 1
    value = _l8(tmp_path)["clock_and_reset_waveform"]
    assert value["clocks"][0]["edge"] == "posedge"
    assert value["resets"] == existing["resets"]


def test_description_with_non_immediate_negation_does_not_guess_edge(tmp_path):
    _write(tmp_path, existing={"clocks": [{"name": "clk"}], "resets": [{"name": "rst"}]})
    p = tmp_path / EMIT.L9_REL
    p.write_text(json.dumps({"ports": [{"name": "clk", "description": "This clock does not use the rising edge."}]}))
    assert getattr(RUNNER, HELPER)(tmp_path) == 0
    assert "edge" not in _l8(tmp_path)["clock_and_reset_waveform"]["clocks"][0]


def test_shared_prose_negation_vocabulary_blocks_never_samples_edge(tmp_path):
    _write(tmp_path, existing={"clocks": [{"name": "clk"}], "resets": [{"name": "rst"}]})
    p = tmp_path / EMIT.L9_REL
    p.write_text(json.dumps({"ports": [{"name": "clk", "description": "This clock never samples on the rising edge."}]}))
    assert getattr(RUNNER, HELPER)(tmp_path) == 0
    assert "edge" not in _l8(tmp_path)["clock_and_reset_waveform"]["clocks"][0]


def test_empty_primary_lists_fall_through_to_typed_aliases(tmp_path):
    _write(tmp_path)
    l8 = _l8(tmp_path)
    l8["clock_domains"] = l8["clocks"]
    l8["clocks"] = []
    (tmp_path / EMIT.L8_REL).write_text(json.dumps(l8), encoding="utf-8")
    l9_path = tmp_path / EMIT.L9_REL
    l9 = json.loads(l9_path.read_text(encoding="utf-8"))
    l9["resets"] = l9["reset_domains"]
    l9["reset_domains"] = []
    l9_path.write_text(json.dumps(l9), encoding="utf-8")

    assert getattr(RUNNER, HELPER)(tmp_path) == 1
    waveform = _l8(tmp_path)["clock_and_reset_waveform"]
    assert waveform["clocks"][0]["period_ns"] == 24
    assert waveform["resets"][0]["polarity"] == "active_high"


def test_unreadable_layer_is_error_not_successful_zero(tmp_path):
    _write(tmp_path)
    (tmp_path / EMIT.L9_REL).write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        getattr(RUNNER, HELPER)(tmp_path)
    assert EMIT.main([str(tmp_path)]) == 1


def test_cli_report_appears_only_after_an_atomic_write(tmp_path, monkeypatch):
    """The report writer still runs, and an interrupted first write is absent."""
    existing = {"clock": "rising edge", "reset": "active high"}
    _write(tmp_path, existing=existing)
    complete = tmp_path / "reports" / "complete.json"
    assert EMIT.main([str(tmp_path), "--json", str(complete)]) == 0
    assert json.loads(complete.read_text(encoding="utf-8"))["tool"] == EMIT.TOOL

    def die(*_args, **_kwargs):
        raise OSError("simulated interruption before atomic rename")

    doomed = tmp_path / "reports" / "doomed.json"
    monkeypatch.setattr(A.os, "fsync", die)
    try:
        rc = EMIT.main([str(tmp_path), "--json", str(doomed)])
    except OSError as exc:
        outcome = ("raised", str(exc))
    else:
        outcome = ("returned", rc)
    assert outcome[0] == "raised", outcome
    assert not doomed.exists()
    assert not A.temp_name_for(doomed).exists()


def test_runner_calls_projection_after_protocol_overlay():
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    ours = [n.lineno for n in ast.walk(main)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == HELPER]
    overlays = [n.lineno for n in ast.walk(main)
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", getattr(n.func, "id", None))
                in ("apply_spi_synth", "_apply_spi")]
    coverage = [n.lineno for n in ast.walk(main)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "emit_coverage_report"]
    assert ours, f"{HELPER} is defined but not called by main()"
    assert overlays, "protocol overlay call disappeared; ordering test vacuous"
    assert coverage, "coverage report call disappeared; ordering test vacuous"
    assert min(ours) > max(overlays), (
        "generic projection ran before a protocol-specific waveform producer")
    assert max(ours) < min(coverage), (
        "coverage report ran before the final L8 projection and graded stale "
        "L-document bytes")
