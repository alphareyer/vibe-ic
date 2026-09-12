"""The DRC receipt producer must bind real bytes and use a fresh docker run."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import klayout_drc_measure as M  # noqa: E402

_PROGRAMS = Path(__file__).resolve().parents[1]


def test_loads_by_path_without_programs_on_sys_path():
    """Hygiene loads each program by path in a fresh child interpreter."""
    program = _PROGRAMS / "klayout_drc_measure.py"
    code = """
import importlib.util
import sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
sys.path[:] = [entry for entry in sys.path
               if Path(entry or '.').resolve() != p.parent]
spec = importlib.util.spec_from_file_location('isolated_klayout_drc_measure', p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(program)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _args(project: Path):
    return SimpleNamespace(
        project=str(project), gds="phase3/stage4/gds/top.gds",
        report="reports/phase3/drc_signoff.rpt", deck="/pdk/drc.lydrc",
        top="top", image="example@sha256:" + "0" * 64, cpus=2, memory="4g",
    )


def test_measure_runs_a_fresh_container_and_binds_input_output_and_log(tmp_path, monkeypatch):
    gds = tmp_path / "phase3/stage4/gds/top.gds"
    gds.parent.mkdir(parents=True)
    gds.write_bytes(b"original gds")
    report = tmp_path / "reports/phase3/drc_signoff.rpt"

    def fake_run(command, **kwargs):
        assert command[:3] == ["docker", "run", "--rm"]
        image_at = command.index(_args(tmp_path).image)
        assert command[image_at + 1] == "--skip"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("<report-database><items/></report-database>")
        return SimpleNamespace(returncode=0, stdout="KLayout output\n", stderr="")

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    assert M.measure(_args(tmp_path)) == 0
    row = json.loads((tmp_path / "provenance.jsonl").read_text())
    assert row["record"] == "invocation" and row["measured"] is True
    assert row["exit_code"] == 0 and row["tool"] == "klayout"
    assert set(row["inputs"]) == {"phase3/stage4/gds/top.gds"}
    assert set(row["outputs"]) == {
        "reports/phase3/drc_signoff.rpt", "reports/phase3/drc_signoff.log"}
    assert (tmp_path / "reports/phase3/drc_signoff.log").read_text() == "KLayout output\n"


def test_measure_refuses_paths_outside_project(tmp_path):
    args = _args(tmp_path)
    args.gds = "/tmp/not-our-layout.gds"
    with pytest.raises(ValueError, match="under project"):
        M.measure(args)
