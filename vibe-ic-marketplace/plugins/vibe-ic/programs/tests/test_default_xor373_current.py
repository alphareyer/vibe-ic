"""Current native Step37.3 artefacts and substantive refusal controls."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _native373_current as native
import _physical_current as pc
import gds_xor_check as xor
import librelane_contract as lc


@pytest.fixture
def project():
    path = Path(os.environ["VIBEIC_XOR373_NATIVE_PROJECT"])
    assert path.is_dir()
    return path


def test_native_ordinary_caller_and_declared_consumer(project):
    result = json.loads((project / "NATIVE_RESULT.json").read_text())
    assert result["ordinary_rows"][0]["status"] == "PASS"
    assert result["gate_rc"] == 0
    assert native.read_current(project)["count"] == 0
    rc, line, doc = xor.judge_receipt(project, xor.REPORT_REL)
    assert rc == 0, line
    assert doc["reference"]["kind"] == "native_stream"
    assert doc["connectivity"]["measurements"][0]["evidence"] == native.REL


@pytest.mark.parametrize("mutation", ["shipped", "reference", "def", "netlist", "sdc", "technology",
                                     "stage", "design", "pdk", "tool", "execution", "log",
                                     "count", "connectivity", "missing_dependency", "material_hash",
                                     "material_unbound", "material_path", "mount", "image", "native_log",
                                     "native_execution", "stream_swapped", "identical_streams"])
def test_current_native_refusal(project, mutation):
    saved = {}
    def edit(path, content):
        path = Path(path)
        saved.setdefault(path, path.read_bytes())
        path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
    try:
        dep_path = project / native.REL
        dep = json.loads(dep_path.read_text())
        report_path = project / xor.REPORT_REL
        report = json.loads(report_path.read_text())
        current = dep["current"]
        if mutation in ("shipped", "reference"):
            path = project / report["current"]["inputs"][mutation]["path"]
            edit(path, path.read_bytes() + b"changed")
        elif mutation in ("def", "netlist", "sdc", "technology", "log"):
            field = "outputs" if mutation == "log" else "inputs"
            path = project / current[field][mutation]["path"]
            edit(path, path.read_bytes() + b"changed")
        elif mutation in ("stage", "design", "pdk", "tool"):
            current[mutation] = "wrong"
            edit(dep_path, dep)
        elif mutation == "execution":
            current["execution"]["rc"] = 1
            edit(dep_path, dep)
        elif mutation == "count":
            dep["count"] = 99
            edit(dep_path, dep)
        elif mutation == "connectivity":
            report["connectivity"]["measurements"][0]["evidence_sha256"] = "0" * 64
            edit(report_path, report)
        elif mutation == "missing_dependency":
            edit(dep_path, {})
        elif mutation in ("material_hash", "material_unbound", "material_path"):
            folder = project / dep["folders"][0]
            receipt_path = folder / "vibeic_receipt.json"
            receipt = json.loads(receipt_path.read_text())
            fp = receipt["input"]
            key = next(key for key in fp["config_files"] if "libs.ref" in key)
            if mutation == "material_hash":
                fp["config_files"][key] = "0" * 64
            elif mutation == "material_unbound":
                del fp["config_files"][key]
            else:
                fp["config_files"][str(project / "absent.pdk")] = fp["config_files"].pop(key)
            edit(receipt_path, receipt)
        elif mutation in ("mount", "image", "native_log", "native_execution"):
            folder = project / dep["folders"][-1]
            if mutation == "mount":
                path = folder / "pdk_root.json"
                record = json.loads(path.read_text())
                record["mounts_under_it"][0][0] = str(project / "wrong_pdk")
                edit(path, record)
            elif mutation == "image":
                path = project / "phase3/librelane_switch.json"
                record = json.loads(path.read_text())
                record["image"] = "wrong"
                edit(path, record)
            elif mutation == "native_log":
                edit(folder / "invocation.log", b"no execution\n")
            else:
                path = project / current["outputs"]["launches"]["path"]
                edit(path, b'{}\n')
        else:
            current["outputs"]["magic_gds"] = current["outputs"]["klayout_gds"]
            if mutation == "stream_swapped":
                report["current"]["inputs"]["reference"] = report["current"]["inputs"]["shipped"]
                edit(report_path, report)
            edit(dep_path, dep)
        rc, _, _ = xor.judge_receipt(project, xor.REPORT_REL)
        assert rc != 0
    finally:
        for path, content in saved.items():
            path.write_bytes(content)


@pytest.mark.parametrize("scenario", ["current", "changed", "missing"])
def test_material_bytes_and_mount_are_read(tmp_path, scenario):
    root = tmp_path / "real_mount"
    root.mkdir()
    deck = root / "declared.deck"
    deck.write_text("declared fixture material\n")
    config = {"deck": "/pdk/declared.deck"}
    mounts = [(root, "/pdk")]
    first = lc._current_config_material(config, mounts)
    assert first == {str(deck): pc.digest(deck)}
    if scenario == "changed":
        deck.write_text("changed declared fixture material\n")
        assert lc._current_config_material(config, mounts) != first
    elif scenario == "missing":
        deck.unlink()
        with pytest.raises(lc.Refusal, match="LL_CONFIG_MATERIAL_MISSING"):
            lc._current_config_material(config, mounts)
