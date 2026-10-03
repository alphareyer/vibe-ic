"""Owner-local routing controls; a proof entry is not an equivalence result.

The optional current packet supplies unmodified RTL/netlist and actual library
logs. Otherwise neutral fixtures exercise the same public entry points. No
Docker process is executed, and no LEC PASS record is synthesized.
"""
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(os.environ.get("VIBEIC_LOCAL_OWNER_PROGRAMS",
                               str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(PROGRAMS))
import _container_exec as CE
import design_one_shot_runner as D
import lec_run as L
import pdk_revision_resolve as P
import vibe_ic_one_shot_runner as V


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    packet = os.environ.get("VIBEIC_LOCAL_OWNER_CURRENT_PACKET")
    if packet:
        source = Path(packet)
        declaration = json.loads((source / "plugin_output/declaration.json").read_text())
        top = declaration["top_module"]
        for relative in ("phase2/stage1/rtl", "phase2/stage2/synth"):
            shutil.copytree(source / relative, root / relative)
        synthesis = source / "phase3/librelane/02-yosys-synthesis"
        for name in ("yosys-synthesis.log", "invocation.log", "COMMANDS"):
            destination = root / "phase3/librelane/02-yosys-synthesis" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(synthesis / name, destination)
        tree = json.loads((source / "reports/pdk_revision.json").read_text())["trees"][0]["tree"]
    else:
        top = "unit_core"
        rtl = root / "phase2/stage1/rtl"
        rtl.mkdir(parents=True)
        (rtl / f"{top}.v").write_text(
            f"module {top}(input a, output y); assign y=a; endmodule\n")
        synth = root / "phase2/stage2/synth"
        synth.mkdir(parents=True)
        shutil.copyfile(rtl / f"{top}.v", synth / f"{top}_synth.v")
        tree = str(tmp_path / "pdkstore/versions" / ("ab" * 20) / "process")
        library = Path(tree) / "libs.ref/cells/lib/typical.lib"
        library.parent.mkdir(parents=True)
        library.write_text("/* neutral library */\n")
        log = root / "phase3/librelane/synthesis/tool.log"
        log.parent.mkdir(parents=True)
        log.write_text(f"Executing Liberty frontend: {library}\n")
    return root, top, tree


def test_ordinary_lec_argv_reaches_library_stage_locally(project, monkeypatch):
    """Use the runner's normal argv, then stop before any proof is attempted."""
    root, top, _ = project
    class ReachedLibraryStage(Exception):
        pass
    def library_stage(*args, **kwargs):
        raise ReachedLibraryStage
    monkeypatch.setattr(CE, "no_container_route", lambda: True)
    monkeypatch.setattr(L, "resolve_liberty", library_stage)
    argv = D._lec_run_argv(root, PROGRAMS / "lec_run.py",
                          f"phase2/stage2/synth/{top}_synth.v", top,
                          "named-eda-lane")
    with pytest.raises(ReachedLibraryStage):
        L.main(argv[2:])
    assert not (root / "reports/lec.json").exists()


def test_revision_cli_reads_current_tool_namespace(project):
    root, _, tree = project
    output = root / "reports/pdk_revision.json"
    rc = P.main(["--from-run", str(root), "--container", "named-eda-lane",
                 "--json", str(output)])
    assert rc == 0, output.read_text()
    record, error = P.load_record(root)
    assert error is None and record["resolved"], (record, error)
    assert record["read_in"] == "host"
    assert record["trees"][0]["resolved_tree"] == str(Path(tree).resolve())


def test_ordinary_finalize_revision_receipt_is_consumable(project):
    root, _, _ = project
    record = V._capture_pdk_revision(root, "named-eda-lane")
    assert record["resolved"] and record["read_in"] == "host", record
    loaded, error = P.load_record(root)
    assert error is None and loaded == record, error
    assert P.record_refusal(loaded) is None


def test_absent_local_tree_still_refuses(tmp_path):
    record = P.build_record([
        P.resolve_tree(P.Fs("named-eda-lane"), str(tmp_path / "absent"))],
        "host", "control")
    assert record["resolved"] is False and record["revision"] is None
    assert P.record_refusal(record) == P.REFUSAL_NOT_RECORDED


def test_named_container_route_is_retained(project, monkeypatch, capsys):
    """A route-selection fixture, without executing a Docker command."""
    root, top, _ = project
    monkeypatch.setattr(CE, "no_container_route", lambda: False)
    observed = []
    def unavailable(container):
        observed.append(container)
        return False
    monkeypatch.setattr(L, "_container_available", unavailable)
    argv = D._lec_run_argv(root, PROGRAMS / "lec_run.py",
                          f"phase2/stage2/synth/{top}_synth.v", top,
                          "named-eda-lane")
    assert L.main(argv[2:]) == 1
    assert observed == ["named-eda-lane"]
    assert "container 'named-eda-lane' not available" in capsys.readouterr().err
    assert P.Fs("named-eda-lane").container == "named-eda-lane"


def test_native_marked_probe_keeps_watchdog_and_native_argv(tmp_path):
    """Real bounded capability probe, not a Yosys equivalence invocation."""
    if shutil.which("yosys") is None:
        pytest.skip("Yosys capability not installed")
    telemetry = tmp_path / "probe.telemetry.json"
    result = L._docker("host", "yosys -V", marker="yosys -V",
                       telemetry_path=telemetry)
    assert result.returncode == 0 and "Yosys" in result.stdout
    assert result.args[:2] == ["bash", "-lc"]
    record = json.loads(telemetry.read_text())
    assert len(record["attempts"]) == 1 and record["status"] == "complete"
