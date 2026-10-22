#!/usr/bin/env python3
"""Issue #3384: a discovered IO library list is not a selected timing scene."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as runner  # noqa: E402
import io_pad_chip_top_gen as producer  # noqa: E402


def _liberty(path: Path, *, library: str, process: float = 1.0,
             voltage: float = 1.0, temperature: float = 25.0,
             cells: tuple[str, ...] = ("pad",), cap_unit: str = "pf",
             rails: tuple[tuple[str, float], ...] = (("VDD", 1.0),
                                                     ("VSS", 0.0))) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'''library ({library}) {{
  time_unit : "1ns";
  capacitive_load_unit (1, {cap_unit});
  default_operating_conditions : active;
  operating_conditions (active) {{
    process : {process}; voltage : {voltage}; temperature : {temperature};
    {" ".join(f'power_rail ({name}, {value});' for name, value in rails)}
  }}
  {" ".join(f'voltage_map ({name}, {value});' for name, value in rails)}
'''
        + "".join(f"  cell ({cell}) {{ }}\n" for cell in cells)
        + "}\n")
    return path


def test_discovery_inventory_cannot_select_a_filename_matching_wrong_model(
        tmp_path: Path) -> None:
    """The historical suffix selector accepts bytes whose own PVT disagrees."""
    core = _liberty(tmp_path / "core__ss_125C_4v50.lib",
                    library="core_slow", voltage=4.5, temperature=125)
    wrong = _liberty(tmp_path / "io__ss_125C_4v50.lib",
                     library="io_typical", voltage=5.0, temperature=25)
    record = tmp_path / "reports/phase3/io_pad_chip_top.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"verdict": "WROTE",
                                  "io_library_liberty": [str(wrong)]}))

    with pytest.raises(Exception):
        runner._sta_extra_liberties(
            tmp_path, SimpleNamespace(macro_libs=[]), str(core))


def _scene_fixture(tmp_path: Path) -> tuple[dict, Path, list[Path], Path]:
    root = tmp_path / "pdk-root"
    tree = root / "testpdk"
    lib_dir = tree / "libs.ref/test_io/lib"
    core = _liberty(tmp_path / "active_core_slow.lib", library="core",
                    voltage=0.9, temperature=125,
                    cells=("stdcell",), rails=(("CORE", 0.9), ("VSS", 0)))
    pad = _liberty(lib_dir / "model_alpha.lib", library="pad_model",
                   voltage=0.9, temperature=125,
                   cells=("pad", "supply"),
                   rails=(("CORE", 0.9), ("IO", 1.8), ("VSS", 0)))
    connector = _liberty(lib_dir / "model_beta.lib",
                         library="connector_model", voltage=0.9,
                         temperature=125, cells=("connector",),
                         rails=(("CORE", 0.9), ("IO", 1.8), ("VSS", 0)))
    config = tree / "libs.tech/librelane/test_io/config.tcl"
    config.parent.mkdir(parents=True)
    config.write_text(
        "set ::env(PAD_LIBS) [dict create]\n"
        'dict set ::env(PAD_LIBS) "*_core_slow" "\\\n'
        f"  {pad} \\\n  {connector} \\\n"
        '"\n')
    receipt = producer.bind_io_timing_scenes(
        str(root), "testpdk", {"SS": str(core)},
        ["pad", "supply", "connector"], [pad, connector])
    return receipt, core, [pad, connector], config


def _write_receipt(project: Path, receipt: dict) -> None:
    record = project / "reports/phase3/io_pad_chip_top.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({
        "program": "io_pad_chip_top_gen", "verdict": "WROTE",
        "io_timing_scene_receipt": receipt,
    }))


def test_producer_receipt_drives_the_actual_sta_consumer_without_filename_pvt(
        tmp_path: Path) -> None:
    receipt, core, models, config = _scene_fixture(tmp_path)
    _write_receipt(tmp_path, receipt)

    selected = runner._sta_extra_liberties(
        tmp_path, SimpleNamespace(macro_libs=["/macro.lib"]), str(core))

    assert selected == ["/macro.lib", *map(str, models)]
    scene = receipt["scenes"]["SS"]
    assert scene["selection_source"]["path"] == str(config)
    assert scene["selection_source"]["pdk_relative_path"] == (
        "libs.tech/librelane/test_io/config.tcl")
    assert scene["selection_pattern"] == "*_core_slow"
    assert scene["core"]["selected_condition"]["voltage"] == 0.9
    assert scene["models"][0]["named_rails"]["IO"] == 1.8
    assert scene["models"][0]["pdk_relative_path"] == (
        "libs.ref/test_io/lib/model_alpha.lib")
    assert scene["reachable_master_owners"] == {
        "connector": [str(models[1])],
        "pad": [str(models[0])],
        "supply": [str(models[0])],
    }


@pytest.mark.parametrize("mutate", ["model", "config", "core"])
def test_consumer_refuses_every_stale_scene_authority(
        tmp_path: Path, mutate: str) -> None:
    receipt, core, models, config = _scene_fixture(tmp_path)
    _write_receipt(tmp_path, receipt)
    target = {"model": models[0], "config": config, "core": core}[mutate]
    target.write_text(target.read_text() + "\n/* changed after producer */\n")
    with pytest.raises(ValueError, match="(BYTES_CHANGED|SOURCE_CHANGED)"):
        runner._sta_extra_liberties(
            tmp_path, SimpleNamespace(macro_libs=[]), str(core))


def test_consumer_refuses_a_receipt_with_a_wrong_named_rail(
        tmp_path: Path) -> None:
    receipt, core, _models, _config = _scene_fixture(tmp_path)
    receipt["scenes"]["SS"]["models"][0]["named_rails"]["IO"] = 2.5
    _write_receipt(tmp_path, receipt)
    with pytest.raises(ValueError, match="MODEL_METADATA_CHANGED"):
        runner._sta_extra_liberties(
            tmp_path, SimpleNamespace(macro_libs=[]), str(core))


def test_consumer_refuses_a_changed_pdk_relative_model_identity(
        tmp_path: Path) -> None:
    receipt, core, _models, _config = _scene_fixture(tmp_path)
    receipt["scenes"]["SS"]["models"][0]["pdk_relative_path"] = (
        "libs.ref/other_io/lib/model_alpha.lib")
    _write_receipt(tmp_path, receipt)
    with pytest.raises(ValueError, match="MODEL_IDENTITY_CHANGED"):
        runner._sta_extra_liberties(
            tmp_path, SimpleNamespace(macro_libs=[]), str(core))


def test_normal_runner_invocation_hands_active_scenes_to_the_producer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core = _liberty(tmp_path / "pdk/testpdk/core.lib", library="core",
                    cells=("stdcell",))
    captured: list[str] = []

    def fake_run(argv, **_kwargs):
        captured.extend(map(str, argv))
        return subprocess.CompletedProcess(argv, 0, "producer ok\n", "")

    monkeypatch.setattr(runner, "_padring_pdk_root_and_tree",
                        lambda *_args: (str(tmp_path / "pdk"), "testpdk"))
    monkeypatch.setattr(runner, "_design_supply_nets",
                        lambda *_args: ({"VDD"}, {"VSS"}))
    monkeypatch.setattr(runner, "_v1_6_596_discover_tie_cells",
                        lambda *_args: {})
    monkeypatch.setattr(runner, "_resolve_signoff_corner_libs",
                        lambda *_args: {"TT": str(core)})
    monkeypatch.setattr(runner._pr, "run", fake_run)
    pdk = SimpleNamespace(liberty=str(core), pdn_ring={}, name="testpdk")

    result = runner.step_io_pad_chip_top_gen(tmp_path, None, pdk)

    assert result.status == "PASS"
    index = captured.index("--timing-scenes")
    assert json.loads(captured[index + 1]) == {"TT": str(core)}


def test_container_runner_rebases_host_staged_core_liberty_with_same_bytes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _liberty(
        tmp_path / "cache/testpdk/libs.ref/std/lib/core.lib",
        library="core", cells=("stdcell",))
    runtime = "/foss/pdks/testpdk/libs.ref/std/lib/core.lib"
    digest = __import__('hashlib').sha256(host.read_bytes()).hexdigest()
    captured: list[str] = []

    monkeypatch.setattr(runner, "_padring_pdk_root_and_tree",
                        lambda *_args: ("/foss/pdks", "testpdk"))
    monkeypatch.setattr(runner, "_design_supply_nets",
                        lambda *_args: ({"VDD"}, {"VSS"}))
    monkeypatch.setattr(runner, "_v1_6_596_discover_tie_cells",
                        lambda *_args: {})
    monkeypatch.setattr(runner, "_resolve_signoff_corner_libs",
                        lambda _project, pdk, _container:
                        {"TT": str(pdk.liberty)})
    monkeypatch.setattr(
        runner, "_current_synth_liberty_sha256",
        lambda path, _container: digest if str(path) == runtime else None)
    monkeypatch.setattr(runner, "_to_container_path", lambda path, _c: path)

    def fake_exec(_container, command, **_kwargs):
        captured.append(command)
        return 0, "producer ok\n", ""

    monkeypatch.setattr(runner, "_docker_exec", fake_exec)
    pdk = SimpleNamespace(liberty=str(host), pdn_ring={}, name="testpdk")

    result = runner.step_io_pad_chip_top_gen(tmp_path, "container", pdk)

    assert result.status == "PASS"
    assert captured
    assert runtime in captured[0]
    assert str(host) not in captured[0]


def test_container_runner_refuses_runtime_pdk_file_with_different_bytes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _liberty(
        tmp_path / "cache/testpdk/libs.ref/std/lib/core.lib",
        library="core", cells=("stdcell",))
    runtime = "/foss/pdks/testpdk/libs.ref/std/lib/core.lib"
    monkeypatch.setattr(
        runner, "_current_synth_liberty_sha256",
        lambda path, _container: "0" * 64 if str(path) == runtime else None)

    with pytest.raises(ValueError, match="PADRING_PDK_FILE_BYTES_CHANGED"):
        runner._padring_runtime_pdk_file(
            str(host), "/foss/pdks", "testpdk", "container")


def test_container_hash_readers_ignore_image_entrypoint_banner(
        monkeypatch: pytest.MonkeyPatch) -> None:
    digest = "a" * 64
    stdout = ("[INFO] Final PATH variable: /tools\n"
              "[INFO] Final PYTHONPATH variable: /python\n"
              f"{digest}  /foss/pdks/testpdk/core.lib\n")
    monkeypatch.setattr(runner._cex, "no_container_route", lambda: False)
    monkeypatch.setattr(
        runner._cex, "run_in_container_supervised",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args, 0, stdout, ""))

    assert runner._current_synth_liberty_sha256(
        "/foss/pdks/testpdk/core.lib", "container") == digest
    assert runner._io_timing_path_sha256(
        "/foss/pdks/testpdk/core.lib", "container") == digest


def test_normal_runner_refuses_when_active_scenes_cannot_be_resolved(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_padring_pdk_root_and_tree",
                        lambda *_args: (str(tmp_path / "pdk"), "testpdk"))
    monkeypatch.setattr(runner, "_design_supply_nets",
                        lambda *_args: ({"VDD"}, {"VSS"}))
    monkeypatch.setattr(runner, "_v1_6_596_discover_tie_cells",
                        lambda *_args: {})
    monkeypatch.setattr(
        runner, "_resolve_signoff_corner_libs",
        lambda *_args: (_ for _ in ()).throw(ValueError("scene discovery failed")))
    pdk = SimpleNamespace(liberty="/pdk/core.lib", pdn_ring={}, name="testpdk")

    result = runner.step_io_pad_chip_top_gen(tmp_path, "container", pdk)

    assert result.status == "FAIL"
    assert "IO_TIMING_CORE_SCENES_UNRESOLVED" in result.detail


def test_producer_refuses_missing_reachable_master(tmp_path: Path) -> None:
    receipt, core, models, _config = _scene_fixture(tmp_path)
    assert receipt["required_masters"]
    with pytest.raises(producer.Refusal, match="population"):
        producer.bind_io_timing_scenes(
            str(tmp_path / "pdk-root"), "testpdk", {"SS": str(core)},
            ["pad", "master_absent_from_every_model"], models)


def test_producer_refuses_wrong_content_pvt_even_when_pattern_matches(
        tmp_path: Path) -> None:
    _receipt, core, models, _config = _scene_fixture(tmp_path)
    models[0].write_text(models[0].read_text().replace(
        "temperature : 125", "temperature : 25"))
    with pytest.raises(producer.Refusal, match="incompatible PVT"):
        producer.bind_io_timing_scenes(
            str(tmp_path / "pdk-root"), "testpdk", {"SS": str(core)},
            ["pad", "connector"], models)


def test_producer_refuses_unusable_model_units(tmp_path: Path) -> None:
    _receipt, core, models, _config = _scene_fixture(tmp_path)
    models[0].write_text(models[0].read_text().replace(
        "capacitive_load_unit (1, pf);", ""))
    with pytest.raises(producer.Refusal, match="capacitive_load_unit"):
        producer.bind_io_timing_scenes(
            str(tmp_path / "pdk-root"), "testpdk", {"SS": str(core)},
            ["pad", "connector"], models)


def test_producer_refuses_two_source_declared_compatible_rosters(
        tmp_path: Path) -> None:
    _receipt, core, models, _config = _scene_fixture(tmp_path)
    duplicate = _liberty(
        tmp_path / "pdk-root/testpdk/libs.ref/other_io/lib/model_gamma.lib",
        library="other_pad_model", voltage=0.9, temperature=125,
        cells=("pad", "supply", "connector"),
        rails=(("CORE", 0.9), ("IO", 1.8), ("VSS", 0)))
    config = (tmp_path /
              "pdk-root/testpdk/libs.tech/librelane/other_io/config.tcl")
    config.parent.mkdir(parents=True)
    config.write_text(
        "set ::env(PAD_LIBS) [dict create]\n"
        f'dict set ::env(PAD_LIBS) "*_core_slow" "{duplicate}"\n')
    with pytest.raises(producer.Refusal, match="2 compatible"):
        producer.bind_io_timing_scenes(
            str(tmp_path / "pdk-root"), "testpdk", {"SS": str(core)},
            ["pad", "supply", "connector"], [*models, duplicate])
