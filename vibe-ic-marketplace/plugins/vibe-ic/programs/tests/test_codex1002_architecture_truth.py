"""Architecture truth contracts for the IC/IP and analog/mixed-signal paths."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))


def _flow() -> list[dict]:
    return yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text(
            encoding="utf-8"))["steps"]


def _step(step_id: str) -> dict:
    return next(s for s in _flow() if str(s["id"]) == step_id)


EXPECTED_STEP_ORDER = (
    "D1", "0.5ic", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "10", "11", "FS1", "DT1", "12", "13", "A1", "A2", "A3", "A4",
    "A5", "A6", "A7", "A8", "A9", "14", "15", "15.5ic", "16", "17",
    "18", "19", "20", "21", "22", "DT2", "DT3", "32", "23", "24",
    "25", "26", "26.5ic", "27", "28", "29", "30", "33", "34", "35",
    "37", "37.3", "31", "36", "37.4", "37.5ip", "37.5ic", "38", "39",
    "M1", "M2", "M3", "M4", "40", "41", "42", "43", "44", "P0",
)
EXPECTED_HALF_STEPS = frozenset({
    "0.5ic", "15.5ic", "26.5ic", "37.5ip", "37.5ic",
})


def test_canonical_flow_order_and_half_steps_are_exact() -> None:
    ids = tuple(str(step["id"]) for step in _flow())
    assert ids == EXPECTED_STEP_ORDER
    assert frozenset(step_id for step_id in ids if ".5" in step_id) == \
        EXPECTED_HALF_STEPS
    assert len(ids) == 70


def test_route_owner_names_and_ip_applicability_are_truthful() -> None:
    owner = _step("0.5ic")
    ip = _step("37.5ip")
    assert "route authority" in owner["name"].lower()
    assert "chip/ic path only" not in owner["name"].lower()
    assert "chip/ic path only" not in owner["notes"].lower()
    assert "ic/ip" in owner["notes"].lower()

    assert "hardmacro/ip" in ip["name"].lower()
    assert "die" in ip["name"].lower()
    assert "owner-attested" in ip["notes"].lower()
    assert "die" in ip["notes"].lower()
    assert ip["condition"] == {
        "any_of": True,
        "files_exist": [
            "input/submission_template/NO_TEMPLATE.txt",
            "input/submission_template/SELF_TAPEOUT.txt",
            "input/submission_template/slots/*.yaml",
        ],
        "delivery_declares": {
            "declaration": "input/submission_template/tapeout_declaration.json",
            "field": "answers.deliverable",
            "absent_when": ["DIE"],
            "corroborated_by": "owner_attestation",
        },
    }


def test_mcp_eda_is_a_tool_surface_behind_the_canonical_front_door() -> None:
    text = (PLUGIN / "mcp-eda" / "CLAUDE.md").read_text(encoding="utf-8").lower()
    for required in (
        "vibe_ic_one_shot_runner.py",
        "phase 1",
        "program-first",
        "evidence",
        "0.5ic",
        "37.5ip",
        "m2",
        "cannot be certified",
    ):
        assert required in text
    for stale in (
        "phase 7",
        "pause at every human checkpoint",
        "default pdk",
        "pdk selection guide",
    ):
        assert stale not in text


def _write_boundary_tree(root: Path, pct: float) -> None:
    block = root / "phase3" / "analog" / "ldo"
    block.mkdir(parents=True)
    (block / "corner_results.json").write_text(json.dumps({
        "corners": [{"process": "TT", "simulator_run": True}],
        "design_content": "structure_and_geometry",
    }))
    post = 100.0 - pct
    (block / "pre_vs_post.json").write_text(json.dumps({
        "specs": [{"name": "vout", "pre_value": 100.0,
                   "post_value": post, "delta_pct": -pct}],
        "overall_status": "NEEDS_RELAYOUT" if pct > 10.0 else "OK",
    }))
    (root / "phase3" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": ["ldo"]}))


@pytest.mark.parametrize("pct, expected", [(9.99, 0), (10.00, 0), (10.01, 1)])
def test_a7_boundary_is_one_10_percent_rule_for_both_gates(
        tmp_path: Path, pct: float, expected: int) -> None:
    """The flow gate and the runner gate must agree at the owner boundary."""
    import analog_a7_post_layout_resim_check as runner_gate
    import analog_pre_vs_post_layout_check as flow_gate

    _write_boundary_tree(tmp_path, pct)
    assert runner_gate.DEFAULT_MAX_DELTA_PCT == pytest.approx(10.0)
    assert flow_gate.MAX_DEGRADATION_PCT == pytest.approx(10.0)

    import subprocess
    for program in (runner_gate.__file__, flow_gate.__file__):
        report = tmp_path / (Path(program).stem + ".json")
        cp = subprocess.run(
            [sys.executable, program, str(tmp_path), "--json", str(report)],
            capture_output=True, text=True)
        assert cp.returncode == expected, (program, pct, cp.stdout, cp.stderr)


def test_a7_flow_config_and_registry_choose_a3_once() -> None:
    a7 = _step("A7")
    assert a7["closed_loop"] == {
        "fallback_to": "A3",
        "trigger": "Performance degradation > 10% post-extraction",
    }
    registry = (PLUGIN / "config" / "ppa_actuator_registry.yaml").read_text(
        encoding="utf-8")
    assert '"A7"' in registry and 'fallback_to: "A3"' in registry
    assert "degradation > 10%" in registry

    librelane = (PLUGIN / "docs" / "librelane_contract.md").read_text(
        encoding="utf-8")
    assert "A7: degradation > 10 % → A3" in librelane
    assert ">20%" not in librelane and ">30%" not in librelane


def test_a7_artifact_status_compatibility_is_separate_from_gate_fail(
        tmp_path: Path) -> None:
    """The skill-owned artifact keeps NEEDS_RELAYOUT while the gate is FAIL."""
    skill = (PLUGIN / "skills" / "analog-extraction-resim" / "SKILL.md").read_text(
        encoding="utf-8")
    marker = '### `analog/<block>/pre_vs_post.json`'
    example = json.loads(skill.split(marker, 1)[1].split("```json", 1)[1]
                         .split("```", 1)[0])
    assert example["overall_status"] == "NEEDS_RELAYOUT"
    assert '"overall_status": "ERROR"' not in skill
    assert "NEEDS_RELAYOUT" in skill and "A7→A3" in skill
    gate_source = (PROGRAMS / "analog_pre_vs_post_layout_check.py").read_text(
        encoding="utf-8")
    runner_source = (PROGRAMS / "analog_a7_post_layout_resim_check.py").read_text(
        encoding="utf-8")
    cutover_source = (PROGRAMS / "analog_b_analog_cutover.py").read_text(
        encoding="utf-8")
    common_source = (PROGRAMS / "_analog_a_check_common.py").read_text(
        encoding="utf-8")
    assert "parse_pre_vs_post" in gate_source
    assert "parse_pre_vs_post" in runner_source
    assert "parse_pre_vs_post" in cutover_source
    assert "overall_status" in common_source

    _write_boundary_tree(tmp_path, 10.01)
    pvp = tmp_path / "phase3" / "analog" / "ldo" / "pre_vs_post.json"
    doc = json.loads(pvp.read_text(encoding="utf-8"))
    doc["overall_status"] = "NEEDS_RELAYOUT"
    pvp.write_text(json.dumps(doc), encoding="utf-8")
    import analog_pre_vs_post_layout_check as flow_gate
    report = tmp_path / "gate.json"
    result = flow_gate.run_audit(tmp_path)
    assert result.passed is False
    assert result.summary["verdict_tier"] == "FAIL"
    assert json.loads(pvp.read_text(encoding="utf-8"))["overall_status"] == \
        "NEEDS_RELAYOUT"


@pytest.mark.parametrize("pct,status", [(9.0, "NEEDS_RELAYOUT"),
                                         (11.0, "OK")])
def test_a7_reverse_status_mapping_is_rejected_by_both_gates(
        tmp_path: Path, pct: float, status: str) -> None:
    import subprocess
    import analog_a7_post_layout_resim_check as runner_gate
    import analog_pre_vs_post_layout_check as flow_gate

    _write_boundary_tree(tmp_path, pct)
    pvp = tmp_path / "phase3/analog/ldo/pre_vs_post.json"
    doc = json.loads(pvp.read_text(encoding="utf-8"))
    doc["overall_status"] = status
    pvp.write_text(json.dumps(doc), encoding="utf-8")
    for program in (runner_gate.__file__, flow_gate.__file__):
        report = tmp_path / (Path(program).stem + ".json")
        cp = subprocess.run([sys.executable, program, str(tmp_path),
                             "--json", str(report)],
                            capture_output=True, text=True)
        assert cp.returncode == 1, (program, pct, status, cp.stdout, cp.stderr)


def test_a7_forged_stated_delta_is_rejected_by_both_gates(
        tmp_path: Path) -> None:
    import subprocess
    import analog_a7_post_layout_resim_check as runner_gate
    import analog_pre_vs_post_layout_check as flow_gate

    _write_boundary_tree(tmp_path, 20.0)
    pvp = tmp_path / "phase3/analog/ldo/pre_vs_post.json"
    doc = json.loads(pvp.read_text(encoding="utf-8"))
    doc["specs"][0]["delta_pct"] = -5.0
    doc["overall_status"] = "NEEDS_RELAYOUT"
    pvp.write_text(json.dumps(doc), encoding="utf-8")
    for program in (runner_gate.__file__, flow_gate.__file__):
        report = tmp_path / (Path(program).stem + ".json")
        cp = subprocess.run([sys.executable, program, str(tmp_path),
                             "--json", str(report)],
                            capture_output=True, text=True)
        assert cp.returncode == 1, (program, cp.stdout, cp.stderr)


def test_a7_threshold_mutation_is_rejected_by_the_consistency_contract() -> None:
    """Reverse control: changing the flow declaration to 30% cannot look
    consistent with the executable 10% gates."""
    mutated = (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text(
        encoding="utf-8").replace(
            "Performance degradation > 10% post-extraction",
            "Performance degradation > 30% post-extraction", 1)
    assert "Performance degradation > 30% post-extraction" in mutated
    mutated_flow = yaml.safe_load(mutated)
    mutated_a7 = next(s for s in mutated_flow["steps"] if str(s["id"]) == "A7")
    threshold = float(re.search(
        r">\s*([0-9.]+)\s*%", mutated_a7["closed_loop"]["trigger"]
    ).group(1))
    with pytest.raises(AssertionError):
        assert threshold == pytest.approx(10.0)


def test_mixed_signal_metadata_matches_reachable_producers_and_m2_gap(
        tmp_path: Path) -> None:
    truth = json.loads((PLUGIN / "docs" / "architecture"
                        / "analog_mixed_signal_truth.json").read_text(
                            encoding="utf-8"))
    m1 = _step("M1")
    m2 = _step("M2")
    m3 = _step("M3")
    m4 = _step("M4")
    runner = (PROGRAMS / "vibe_ic_one_shot_runner.py").read_text(
        encoding="utf-8")
    phase3 = (PROGRAMS / "phase3_one_shot_runner.py").read_text(
        encoding="utf-8")
    phase23 = (PROGRAMS / "phase23_one_shot_runner.py").read_text(
        encoding="utf-8")

    assert truth["analog"]["dedicated_runner"] == \
        "programs/analog_one_shot_runner.py"
    assert truth["analog"]["steps"] == [f"A{i}" for i in range(1, 10)]
    assert truth["mixed_signal"]["m1"]["producer"] == \
        "programs/mixed_signal_top_lvs_run.py"
    assert truth["mixed_signal"]["m1"]["required_outputs"] == \
        m1["required_outputs"]
    assert "mixed_signal_top_lvs_run.py" in runner
    assert "mixed_signal_top_lvs_run.py" not in phase3
    assert "mixed_signal_top_lvs_run.py" not in phase23
    assert truth["mixed_signal"]["m1"]["reachable_via"] == ["/vibe-ic-all"]

    m2_truth = truth["mixed_signal"]["m2"]
    assert m2_truth["producer"] is None
    assert m2_truth["required_outputs"] == m2["required_outputs"]
    assert m2_truth["certification"] == "CANNOT_CERTIFY"
    assert m2_truth["known_gap"] == m2["known_gap"]

    (tmp_path / "phase1" / "analog").mkdir(parents=True)
    (tmp_path / "phase1" / "analog" / "analog_block_list.json").write_text(
        json.dumps({"blocks": ["ldo"]}))
    import flow_compliance_check as compliance
    result = compliance.check_step(tmp_path, m2, {})
    assert result.status != "PASS"
    assert result.reason_class == "missing_artefact"
    assert all(not (tmp_path / output).exists()
               for output in m2["required_outputs"])

    m3_truth = truth["mixed_signal"]["m3"]
    m4_truth = truth["mixed_signal"]["m4"]
    assert m3_truth["certification"] == "PARTIAL/CANNOT_CERTIFY"
    assert m3_truth["existing_producers"][
        "phase3/mixed_signal/cosim/mixed_signal_results.json"] == \
        "programs/analog_a9_cosim_emit.py"
    assert m3_truth["missing_producers"][
        "reports/analog/mixed_signal/interface_si.json"]["producer"] is None
    assert m3["certification"] == "PARTIAL/CANNOT_CERTIFY"
    assert "interface_si.json" in m3["known_gap"]
    assert m4_truth["certification"] == "CANNOT_CERTIFY"
    assert m4_truth["missing_producers"][
        "reports/analog/mixed_signal/signoff.json"]["producer"] is None
    assert m4["certification"] == "CANNOT_CERTIFY"
    assert "signoff.json" in m4["known_gap"]

    # Ownership test: consumers may mention the path, but no production
    # source contains a write call that targets either missing artifact.
    for filename in ("interface_si.json", "signoff.json"):
        for source in PROGRAMS.glob("*.py"):
            text = source.read_text(encoding="utf-8", errors="replace")
            if filename not in text:
                continue
            assert not re.search(
                rf"(?:write_json|write_text|atomic_write_text|open)\([^\n]*"
                rf"{re.escape(filename)}",
                text), (filename, source)

    for step in (m3, m4):
        result = compliance.check_step(tmp_path, step, {})
        assert result.status != "PASS", step["id"]
        assert result.reason_class == "missing_artefact", result
        assert all(not (tmp_path / output).exists()
                   for output in step["required_outputs"])


def test_absent_fpga_evidence_keeps_steps_6_and_39_unmeasured(
        tmp_path: Path) -> None:
    """A disclosed runner receipt synthesizes both excluded rows."""
    import flow_compliance_check as compliance
    receipt = tmp_path / "reports/phase2/fpga/quartus_map_audit.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"verdict": "SKIP", "sof_present": False,
                                   "skip_reason": "not_attempted"}))
    waivers = compliance._load_waivers(tmp_path)
    assert waivers[6]["_fpga_skip"] and waivers[39]["_fpga_skip"]
    results = []
    for sid in ("6", "39"):
        result = compliance.check_step(tmp_path, _step(sid), waivers)
        results.append(result)
        assert result.status == "NOT_MEASURED", (sid, result)
        assert result.excluded_from_verdict, (sid, result)
        assert result.status != "PASS_WITH_WAIVERS"

    # Run the real synthesised-row/tally path and prove both rows are in the
    # explicit excluded set, rather than merely carrying a local flag.
    report = tmp_path / "flow.json"
    compliance.main([str(tmp_path), "--json", str(report)])
    audit = json.loads(report.read_text(encoding="utf-8"))
    excluded = {int(row["step_id"]) for row in audit["not_measured_excluded"]}
    assert {6, 39} <= excluded


def test_post_tapeout_steps_keep_their_documentation_only_scope() -> None:
    """Manufacturing records remain gated by the silicon intake declaration."""
    claude = (PLUGIN / "mcp-eda" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Steps 40-44 are post-tapeout documentation-only records" in claude
    for sid in ("40", "41", "42", "43", "44"):
        assert _step(sid)["condition"] == {
            "files_exist": ["phase3/stage5_manufacturing/silicon_received.json"]
        }
