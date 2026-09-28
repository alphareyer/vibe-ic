"""Wave 11: design-input authorization must finish before an oracle is opened."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _reference_flow_boundary as boundary


def test_a_docs_symlink_outside_input_is_denied_before_a_reader_opens_it(
        tmp_path, monkeypatch):
    outside = tmp_path / "oracle" / "answer.md"
    outside.parent.mkdir()
    outside.write_text("ORACLE_SENTINEL\n")
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    link = docs / "overview.md"
    link.symlink_to(outside)

    import phase1_doc_one_shot_runner as reader
    opened = []
    original = Path.read_text

    def checked_read(path, *args, **kwargs):
        if path == link:
            opened.append(path.name)
            return "ORACLE_SENTINEL\n"  # no oracle bytes are actually opened
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked_read)
    read_result = reader._v1_6_collect_input_docs_text(tmp_path)
    assert opened == []
    assert "ORACLE_SENTINEL" not in read_result
    assert boundary.design_input_denial(tmp_path, link) == (
        "§4.05: input file resolves outside input/")


@pytest.mark.parametrize("root_name", ["reference_flow", "Reference_Flow", "ref_flow"])
def test_neutral_reference_flow_json_is_refused_without_reading_it(
        tmp_path, monkeypatch, root_name):
    staged = tmp_path / "input" / root_name
    staged.mkdir(parents=True)
    oracle = staged / "limits.json"
    oracle.write_text('{"timing": {"compare": "<=", "value": 1}}\n')
    recipe = staged / "flow.mk"
    recipe.write_text("export CORE_UTILIZATION = 40\n")
    opened = []
    original = Path.read_text

    def checked_read(path, *args, **kwargs):
        if path in (oracle, recipe):
            opened.append(path.name)
            return ""  # record the attempted read without opening the oracle
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", checked_read)
    oracle_denial = boundary.design_input_denial(tmp_path, oracle)
    recipe_denial = boundary.design_input_denial(tmp_path, recipe)
    assert opened == []
    assert oracle_denial == (
        f"§4.05: ambiguous file limits.json inside input/{root_name.lower()}/")
    assert recipe_denial is None
