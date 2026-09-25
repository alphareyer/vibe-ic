"""A resumed timing repair must consume the current routed implementation."""
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as runner


def test_existing_repair_is_reused_only_for_the_same_deck_def_sdc_and_spef(
        tmp_path, monkeypatch):
    project = tmp_path / "subject"
    repair = project / "phase3/stage3/postroute_timing_repair"
    repair.mkdir(parents=True)
    deck = repair / "postroute_timing_repair.tcl"
    deck.write_text("read_def /current.def\n")
    inputs = []
    for name in ("route.def", "constraint.sdc", "nom.spef"):
        path = project / name
        path.write_text(name)
        inputs.append(path)
    calls = []

    def fake_tool(*args, **kwargs):
        calls.append(1)
        (repair / "chip_timing_repaired.v").write_text(f"netlist-{len(calls)}")
        (repair / "timing_repaired.def").write_text(f"def-{len(calls)}")
        (repair / "postroute_timing_repair.log").write_text(
            f"repair-{len(calls)}\n")

    monkeypatch.setattr(runner, "_docker_exec", fake_tool)
    monkeypatch.setattr(runner, "_to_container_path", lambda p, c: p)
    notes = []
    run = lambda: runner._run_postroute_timing_repair(
        project, "chip", "container", deck, notes, source_paths=inputs)
    assert run()
    assert len(calls) == 1
    assert run()
    assert len(calls) == 1
    inputs[0].write_text("new route, same file name")
    assert run()
    assert len(calls) == 2
    assert (repair / "chip_timing_repaired.v").read_text() == "netlist-2"
    assert run()
    assert len(calls) == 2
    deck.write_text("read_def /current.def\n# new repair code\n")
    assert run()
    assert len(calls) == 3
