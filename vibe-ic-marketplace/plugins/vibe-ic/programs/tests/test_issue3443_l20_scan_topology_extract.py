"""Issue #3443: source-owned L20 scan topology has a canonical producer."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path


PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import l20_dft_scan_topology_extract as X  # noqa: E402
import phase1_post_process as P  # noqa: E402

GATE = PROGRAMS / "l20_dft_scan_topology_actionable_check.py"

GOOD = """# Implementation contract

The design shall implement the following DFT scan topology.

## DFT scan topology

| name | length | scan_in | scan_out | clock |
| --- | --- | --- | --- | --- |
| core_scan_0 | 128 | scan_in_0 | scan_out_0 | scan_clk |
| core_scan_1 | 96 | scan_in_1 | scan_out_1 | scan_clk |
"""


def _project(tmp_path: Path, text: str, name: str = "dft.md") -> Path:
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run_gate(project: Path):
    return subprocess.run([sys.executable, str(GATE), str(project)],
                          capture_output=True, text=True)


def _write_emitted_l20(project: Path) -> dict:
    doc = P.emit_l_doc_skeleton("L20", "unknown", project_dir=project)
    generated = project / "phase1" / "generated_docs"
    generated.mkdir(parents=True, exist_ok=True)
    (generated / "L20_DFT_SCAN_TOPOLOGY.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def test_explicit_table_extracts_typed_rows_and_exact_source_citations(tmp_path):
    project = _project(tmp_path, GOOD)
    out = X.extract_dft_scan_topology(project)
    assert out is not None
    assert [(row["name"], row["length"], row["scan_in"],
             row["scan_out"], row["clock"])
            for row in out["scan_chains"]] == [
        ("core_scan_0", 128, "scan_in_0", "scan_out_0", "scan_clk"),
        ("core_scan_1", 96, "scan_in_1", "scan_out_1", "scan_clk"),
    ]
    first = out["scan_chains"][0]["source"]
    assert first["document"] == "input/docs/dft.md"
    assert first["section"] == "Implementation contract / DFT scan topology"
    assert first["header_line"] == 7
    assert first["row_line"] == 9
    assert len(first["row_sha256"]) == 64
    assert len(first["document_sha256"]) == 64


def test_emitter_publishes_extracted_l20_and_consumer_passes(tmp_path):
    project = _project(tmp_path, GOOD)
    doc = _write_emitted_l20(project)
    assert doc["extraction_status"] == "EXTRACTED"
    assert doc["source_documents"] == ["input/docs/dft.md"]
    assert len(doc["fields"]["scan_chains"]) == 2
    assert doc["evidence"]["input/docs/dft.md"][0]["literal"] == "core_scan_0"
    result = _run_gate(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_real_phase1_writer_and_consumer_replay(tmp_path, monkeypatch):
    """Use the canonical Phase-1 L19-L23 writer, then its actual L20 gate."""
    project = _project(tmp_path, GOOD)
    runner = importlib.import_module("phase1_doc_one_shot_runner")
    # L21's post-emit producers answer a different layer.  Keep this replay
    # bounded to the L19-L23 writer whose L20 call site is under test.
    monkeypatch.setitem(runner.__dict__, "_CLI_PDK", None)
    results = runner._emit_l19_to_l23_skeletons(project)
    assert any(result.path.name == "L20_DFT_SCAN_TOPOLOGY.json"
               for result in results)
    l20 = json.loads((project / "phase1" / "generated_docs" /
                      "L20_DFT_SCAN_TOPOLOGY.json").read_text())
    assert l20["extraction_status"] == "EXTRACTED"
    assert l20["source_documents"] == ["input/docs/dft.md"]
    assert l20["extraction_evidence"]["input/docs/dft.md"]
    result = _run_gate(project)
    assert result.returncode == 0, result.stdout + result.stderr


def test_partial_row_stays_unextracted_and_consumer_fails_closed(tmp_path):
    bad = GOOD.replace(
        "| core_scan_0 | 128 | scan_in_0 | scan_out_0 | scan_clk |",
        "| core_scan_0 | 128 | scan_in_0 | scan_out_0 | TBD |")
    project = _project(tmp_path, bad)
    doc = _write_emitted_l20(project)
    assert doc["extraction_status"] == "NOT_YET_EXTRACTED"
    assert doc["fields"]["scan_chains"] == []
    result = _run_gate(project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REQUIREMENT_OUTSIDE_CONSUMING_LAYER" in result.stdout


def test_unknown_planned_prose_is_not_a_topology(tmp_path):
    project = _project(tmp_path, (
        "# DFT plan\n\nDFT scan topology is unknown and planned for a later producer.\n"))
    assert X.extract_dft_scan_topology(project) is None
    assert P.emit_l_doc_skeleton(
        "L20", "unknown", project_dir=project)["extraction_status"] == (
            "NOT_YET_EXTRACTED")


def test_two_declarations_are_ambiguous_even_when_identical(tmp_path):
    project = _project(tmp_path, GOOD)
    (project / "input" / "docs" / "second.md").write_text(GOOD)
    assert X.extract_dft_scan_topology(project) is None


def test_duplicate_chain_names_are_rejected(tmp_path):
    duplicate = GOOD.replace("core_scan_1", "core_scan_0")
    assert X.extract_dft_scan_topology(
        _project(tmp_path, duplicate)) is None


def test_table_outside_explicit_scan_section_is_ignored(tmp_path):
    outside = GOOD.replace("## DFT scan topology", "## Port summary")
    assert X.extract_dft_scan_topology(_project(tmp_path, outside)) is None


def test_fenced_example_is_not_a_design_declaration(tmp_path):
    fenced = GOOD.replace("## DFT scan topology\n\n|", "## DFT scan topology\n\n```\n|") + "```\n"
    assert X.extract_dft_scan_topology(_project(tmp_path, fenced)) is None
