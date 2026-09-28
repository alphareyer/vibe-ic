"""A bounded backend synth must publish the mapped arm consumed by PnR."""

from types import SimpleNamespace

import phase3_one_shot_runner as runner


def _run(tmp_path, status, produce_mapped=True):
    project = tmp_path / "project"
    project.mkdir()
    existing = project / "phase2/stage2/synth/chip_top_synth.v"
    existing.parent.mkdir(parents=True)
    existing.write_text("module chip_top; stale_cell u_old(); endmodule\n")

    def gate(isolated, *args):
        synth = isolated / "phase2/stage2/synth"
        synth.mkdir(parents=True, exist_ok=True)
        (synth / "netlist.v").write_text("module chip_top; endmodule\n")
        (synth / "stats.json").write_text("{}\n")
        if produce_mapped:
            (synth / "chip_top_synth.v").write_text(
                "module chip_top; mapped_cell u_cell(); endmodule\n")
        return runner.StepResult("synth", status, 0.0, "fake tool outputs")

    row = runner._direct_flow_window(
        project, "chip_top", None, SimpleNamespace(container=None),
        "synth", gate)
    return project, row


def test_pass_publishes_mapped_arm_for_pnr(tmp_path):
    project, row = _run(tmp_path, "PASS")
    mapped = project / "phase2/stage2/synth/chip_top_synth.v"
    assert mapped.read_text() == (
        "module chip_top; mapped_cell u_cell(); endmodule\n")
    assert str(mapped) in row.output_files


def test_failed_synthesis_does_not_publish_candidate_mapped_arm(tmp_path):
    project, row = _run(tmp_path, "FAIL")
    assert (project / "phase2/stage2/synth/chip_top_synth.v").read_text() == (
        "module chip_top; stale_cell u_old(); endmodule\n")
    assert row.status == "FAIL"


def test_pass_without_new_mapped_arm_cannot_reuse_stale_arm(tmp_path):
    project, row = _run(tmp_path, "PASS", produce_mapped=False)
    assert row.status == "NOT_MEASURED"
    assert "without a new mapped netlist" in row.detail
    assert (project / "phase2/stage2/synth/chip_top_synth.v").read_text() == (
        "module chip_top; stale_cell u_old(); endmodule\n")
