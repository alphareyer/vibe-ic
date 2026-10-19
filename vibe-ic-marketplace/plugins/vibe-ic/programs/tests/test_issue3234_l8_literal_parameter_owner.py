"""Issue #3234: literal L8 parameters retain their declared module owner.

The public fixture is the issue's verbatim input.  The multi-module strings
below are generic guards constructed for this regression; they are not a
replay or reconstruction of any unpublished module artifact.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from programs import phase1_doc_one_shot_runner as producer
from programs.tests._hostpaths import require_repo


PROGRAMS = Path(__file__).resolve().parents[1]
EXPECTED_PUBLIC = [("TAG_BITS", "80"), ("DIGEST_BITS", "224")]


def _public_fixture() -> Path:
    return require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs", "tests",
        "fixtures", "phase1_local", "l8_literal_parameter_owner",
    ) / "literal_parameters.md"


def _stage(project: Path, name: str, text: str) -> dict[str, str]:
    source = project / "input" / "docs" / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return {name: text}


def _pairs(doc: dict) -> list[tuple[str | None, str | None]]:
    return [
        (row.get("name"),
         None if row.get("default") is None else str(row.get("default")))
        for row in doc.get("parameters", [])
        if isinstance(row, dict)
    ]


def _emit_l8(project: Path, name: str, text: str) -> dict:
    extracted = _stage(project, name, text)
    result = producer.gen_l8_timing_waveform(project, extracted)
    return json.loads(result.path.read_text(encoding="utf-8"))


def test_public_literal_parameter_fixture_reaches_actual_l8_producer(tmp_path):
    text = _public_fixture().read_text(encoding="utf-8")
    assert _pairs(_emit_l8(tmp_path, "literal_parameters.md", text)) == EXPECTED_PUBLIC


def test_public_literal_parameter_fixture_reaches_cli_and_l9_consumer(tmp_path):
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    shutil.copyfile(_public_fixture(), docs / "literal_parameters.md")
    command = [
        sys.executable,
        str(PROGRAMS / "phase1_doc_one_shot_runner.py"),
        str(tmp_path),
        "--ic-name",
        "neutral_literal_parameter_owner",
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    generated = tmp_path / "phase1" / "generated_docs"
    l8 = json.loads((generated / "L8_RTL_CONSTANTS.json").read_text())
    l9 = json.loads((generated / "L9_INTEGRATION_SPEC.json").read_text())
    actual = {"L8": _pairs(l8), "L9": _pairs(l9)}
    assert actual == {"L8": EXPECTED_PUBLIC, "L9": EXPECTED_PUBLIC}, actual


def _generic_owned_modules(order: str) -> str:
    selected = """module frame_top ();
  parameter integer DATA_BITS = 20;
endmodule"""
    sibling = """module trace_aux ();
  parameter integer DATA_BITS = 44;
  parameter integer TRACE_BITS = 9;
endmodule"""
    bodies = [selected, sibling] if order == "selected-first" else [sibling, selected]
    return (
        "# Current integration\n\n"
        "The top module is `frame_top`.\n\n"
        "```systemverilog\n" + "\n\n".join(bodies) + "\n```\n"
    )


@pytest.mark.parametrize("order", ["selected-first", "selected-last"])
def test_selected_module_owns_literal_defaults_in_both_source_orders(
        tmp_path, order):
    doc = _emit_l8(tmp_path, "module_parameters.md", _generic_owned_modules(order))
    assert _pairs(doc) == [("DATA_BITS", "20")]


def test_multiple_modules_without_selected_owner_fail_closed(tmp_path):
    text = _generic_owned_modules("selected-first").replace(
        "The top module is `frame_top`.\n\n", "")
    _stage(tmp_path, "unowned_modules.md", text)
    command = [
        sys.executable,
        str(PROGRAMS / "phase1_doc_one_shot_runner.py"),
        str(tmp_path),
        "--ic-name",
        "neutral_literal_parameter_owner",
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    generated = tmp_path / "phase1" / "generated_docs"
    outcome = json.loads((
        tmp_path / "reports" / "audit" / "phase1"
        / "L8_RTL_CONSTANTS_step_outcome.json"
    ).read_text())
    l8 = json.loads((generated / "L8_RTL_CONSTANTS.json").read_text())
    l9 = json.loads((generated / "L9_INTEGRATION_SPEC.json").read_text())
    for record in (outcome, l8):
        assert record["phase1_step_error"] is True
        assert record["error_class"] == "ValueError"
        assert "L8_PARAMETER_OWNER_UNRESOLVED" in record["error_message"]
    assert "parameters" not in l8
    assert not l9.get("parameters")


def test_example_fence_is_not_current_parameter_authority(tmp_path):
    text = """# Configuration guide

## Example

```systemverilog
parameter integer EXAMPLE_BITS = 91;
```
"""
    doc = _emit_l8(tmp_path, "example.md", text)
    assert _pairs(doc) == []
