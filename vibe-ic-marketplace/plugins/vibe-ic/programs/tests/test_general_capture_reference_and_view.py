"""General reference hierarchy and score-view facts; no dataset identifiers."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "capture_reference_scorer", ROOT / "benchmark/score_iverilog_tb.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

LAYOUT = {"prompt_filename": "description.txt", "tb_filename": "testbench.v",
          "ref_glob": "reference*.v"}
ARGS = {"pass_regex": "CHECK_OK", "fail_regex": "CHECK_BAD", "cwd_design_dir": True}
GOLDEN = '''module storage_leaf(input [3:0] a, output [3:0] z);
assign z = a + 1'b1;
endmodule
module payload_unit(input [3:0] i, output [3:0] q);
storage_leaf cell_u(.a(i), .z(q));
endmodule
'''
TB = '''module testbench;
reg [3:0] i = 4'd4; wire [3:0] q;
channel dut(.i(i), .q(q));
initial begin #1;
if (q === 4'd5) $display("CHECK_OK"); else $display("CHECK_BAD");
$finish; end
endmodule
'''


def reference(tmp_path, source=GOLDEN):
    dataset = tmp_path / "dataset"
    design = dataset / "channel"
    design.mkdir(parents=True)
    (design / "description.txt").write_text("Module name:\nchannel\n")
    (design / "testbench.v").write_text(TB)
    ref = design / "reference_bundle.v"
    ref.write_text(source)
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    return dataset, ref, aliases


def test_reference_helper_first_selects_the_actual_hierarchy_root(tmp_path):
    dataset, ref, aliases = reference(tmp_path)
    sources, ports = S._aliased_golden_srcs("channel", dataset, LAYOUT, [ref], aliases)
    assert ports == {"i", "q"}
    text = Path(sources[0]).read_text()
    assert "module storage_leaf" in text
    assert "module channel" in text
    assert "storage_leaf cell_u" in text


def test_reference_helper_first_runs_the_existing_native_audit(tmp_path):
    dataset, _, _ = reference(tmp_path)
    assert S._golden_ref_fails_own_tb_runtime("channel", dataset, LAYOUT, ARGS) is False


def test_wrong_reference_still_fails_the_existing_native_audit(tmp_path):
    dataset, _, _ = reference(tmp_path, GOLDEN.replace("a + 1'b1", "a - 1'b1"))
    assert S._golden_ref_fails_own_tb_runtime("channel", dataset, LAYOUT, ARGS) is True


def test_two_unidentified_roots_are_not_guessed(tmp_path):
    dataset, ref, aliases = reference(tmp_path, GOLDEN +
        "module unrelated(input x, output y); assign y=x; endmodule\n")
    assert S._aliased_golden_srcs("channel", dataset, LAYOUT, [ref], aliases)[0] is None


def test_declared_file_root_preserves_comments_and_string_literals(tmp_path):
    source = GOLDEN.replace(
        "storage_leaf cell_u", '// payload_unit stays a comment\ninitial $display("payload_unit");\nstorage_leaf cell_u')
    dataset, ref, aliases = reference(tmp_path, source)
    named = ref.with_name("payload_unit.v")
    ref.rename(named)
    sources, ports = S._aliased_golden_srcs("channel", dataset, LAYOUT, [named], aliases)
    text = Path(sources[0]).read_text()
    assert ports == {"i", "q"}
    assert '// payload_unit stays a comment' in text
    assert '$display("payload_unit")' in text


def test_multi_file_reference_root_can_be_after_its_helper(tmp_path):
    dataset, ref, aliases = reference(tmp_path)
    helper, root = GOLDEN.split("module payload_unit", 1)
    ref.write_text(helper)
    second = ref.with_name("reference_tail.v")
    second.write_text("module payload_unit" + root)
    sources, ports = S._aliased_golden_srcs("channel", dataset, LAYOUT, [ref, second], aliases)
    assert ports == {"i", "q"}
    assert "module storage_leaf" in Path(sources[0]).read_text()
    assert "module channel" in Path(sources[1]).read_text()


def view(tmp_path, keys):
    outcomes = [{"id": str(i), "status": "ACCEPTED",
                 "review_path": f"/reviews/{key}.json"} for i, key in enumerate(keys)]
    (tmp_path / "program_first_ai_review_acceptance.json").write_text(
        json.dumps({"review_outcomes": outcomes}))
    return S.scored_view(tmp_path)


def test_known_repair_proves_composed_view_despite_an_unknown_round(tmp_path):
    result = view(tmp_path, ["ai_repair-r2-" + "a" * 64, "reentry-unknown"])
    assert result["view"] == "converged-composed"
    assert result["recovered_ids"] == ["0"]
    assert result["unparsed_round_ids"] == ["1"]
    assert "1" not in result["candidate_rounds"]


@pytest.mark.parametrize("keys", [["reentry-unknown"],
    ["program-r0-" + "a" * 64, "reentry-unknown"]])
def test_partial_first_candidate_evidence_never_claims_single_shot(tmp_path, keys):
    assert view(tmp_path, keys)["view"] == "NOT_MEASURED"


@pytest.mark.parametrize("golden_passes,stub_passes,candidate_passes,expected", [
    (True, True, True, None),
    (False, False, True, None),
    (True, False, False, "FAIL"),
    (True, False, True, "PASS"),
])
def test_escalation_keeps_reference_and_wrong_stub_qualification(
        tmp_path, monkeypatch, golden_passes, stub_passes, candidate_passes, expected):
    dataset, _, _ = reference(tmp_path)
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "channel.v").write_text(
        "module channel(input [3:0] i, output [3:0] q); assign q=i+1; endmodule\n")
    judged = []

    def run_text(text, design, tb, cwd, pass_re, tag):
        judged.append(tag)
        return True, golden_passes if tag == "qualify_golden" else stub_passes

    candidate_calls = []

    def run_candidate(*args):
        candidate_calls.append(args)
        return {"verdict": "PASS" if candidate_passes else "FAIL"}

    monkeypatch.setattr(S, "_verilator_run_text", run_text)
    monkeypatch.setattr(S, "_verilator_compile_run", run_candidate)
    result = S._primary_tool_disqualified_escalation(
        "channel", samples, dataset, LAYOUT, ARGS)
    assert (result["verdict"] if result else None) == expected
    assert len(candidate_calls) == (0 if expected is None else 1)
