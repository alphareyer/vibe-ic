"""Generated cocotb source references survive a different project mount."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import professional_tb_gen as T  # noqa: E402


@pytest.mark.parametrize("custom_output", [False, True])
@pytest.mark.parametrize("relative_project", [False, True])
def test_generated_sources_resolve_after_project_relocation(
        tmp_path, monkeypatch, custom_output, relative_project):
    """Exercise the real producer, then consume its paths with the host gone."""
    original = tmp_path / "producer-project"
    docs = original / "phase1/generated_docs"
    docs.mkdir(parents=True)
    (docs / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({"fields": {
        "top_module": "widget",
        "top_ports": [{"name": "clk", "dir": "input", "width": 1},
                      {"name": "ctrl", "dir": "input", "width": 4},
                      {"name": "status", "dir": "output", "width": 4}],
        "clocks": [{"name": "clk", "edge": "posedge", "period_ns": 10}],
    }}))
    rtl = original / "phase2/stage1/rtl"
    (rtl / "nested").mkdir(parents=True)
    sources = {
        "widget.v": "module widget(input clk, input [3:0] ctrl, output [3:0] status); endmodule\n",
        "nested/leaf.sv": "module leaf; endmodule\n",
    }
    for name, content in sources.items():
        (rtl / name).write_text(content)
    monkeypatch.chdir(tmp_path)
    project = Path(original.name) if relative_project else original
    out_dir = project / "verification/custom_bundle" if custom_output else None
    result = T.generate(project, out_dir=out_dir)
    assert result["status"] == "PASS", result
    assert result["rtl_files"] == len(sources)
    generated = Path(result["out_dir"]).absolute()
    makefile = (generated / "Makefile").read_text()
    # GNU make joins these continuations before expanding VERILOG_SOURCES.
    logical_lines = makefile.replace("\\\n", " ").splitlines()
    assignment = next(line for line in logical_lines
                      if line.startswith("VERILOG_SOURCES = "))
    references = assignment.split("=", 1)[1].split()
    assert len(references) == len(sources), assignment
    assert all(not Path(ref).is_absolute() for ref in references), assignment

    consumer = tmp_path / "consumer-project"
    original.rename(consumer)
    assert not original.exists()
    consumer_cwd = consumer / generated.relative_to(original)
    resolved = [(consumer_cwd / ref).resolve() for ref in references]
    expected = {consumer / "phase2/stage1/rtl" / name: content
                for name, content in sources.items()}
    assert set(resolved) == set(expected), (references, resolved)
    assert {path: path.read_text() for path in resolved} == expected
