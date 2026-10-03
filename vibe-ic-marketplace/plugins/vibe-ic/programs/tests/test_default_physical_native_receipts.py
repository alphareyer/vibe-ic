"""Real producer artefact controls, selected explicitly inside the pinned image."""
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _physical_current as current
import clock_plan_check as clock
import digital_hardmacro_check as kit
import release_docs_check as docs
import gds_xor_check as xor
import erc_density_check as erc


@pytest.fixture
def native_project(tmp_path):
    declared = os.environ.get("VIBEIC_DEFAULT_PHYSICAL_NATIVE_PROJECT")
    if not declared:
        pytest.skip("native producer artefacts not supplied: NOT_VERIFIED")
    source = Path(declared)
    assert source.is_dir()
    project = tmp_path / "project"
    shutil.copytree(source, project)
    return project


def test_native_clock_current_is_consumed(native_project):
    assert clock.main([str(native_project)]) == 0


def test_native_direct_reader_is_explicit_current(native_project):
    receipt = current.read_direct_half(native_project, "drc")
    assert receipt["verdict"] == "CURRENT"
    assert receipt["tool"] == "klayout"
    assert receipt["subject"]["sha256"]
    assert set(receipt["outputs"]) == {"report", "native_report", "log"}


@pytest.mark.parametrize("mutation", ["gds", "def", "netlist", "pdk_deck", "report", "log",
                                     "stage", "design", "tool", "pdk", "execution", "native_output", "removed_producer"])
def test_native_direct_current_reverse(native_project, mutation):
    path = native_project / "reports/phase3/direct_current_drc.json"
    doc = json.loads(path.read_text())
    if mutation in ("gds", "def", "netlist", "pdk_deck"):
        p = native_project / doc["inputs"][mutation]["path"]
        p.write_bytes(p.read_bytes() + b"changed")
    elif mutation in ("report", "log"):
        p = native_project / doc["outputs"][mutation]["path"]
        p.write_bytes(p.read_bytes() + b"changed")
    elif mutation in ("stage", "design", "tool", "pdk"):
        doc[mutation] = "wrong"
    elif mutation == "execution":
        doc["execution"]["rc"] = 1
    elif mutation == "native_output":
        doc["execution"]["native_invocation"]["outputs"] = {}
    elif mutation == "removed_producer":
        doc = {}
    path.write_text(json.dumps(doc))
    assert current.read_direct_half(native_project, "drc")["verdict"] == "NOT_MEASURED"


def test_native_kit_and_document_consumers(native_project):
    assert current.check_kit(native_project) == ""
    assert kit.run_audit(native_project).passed
    assert docs.run_audit(native_project, "ip").passed


@pytest.mark.parametrize("mutation", ["gds", "def", "route", "timing_sdc", "timing_netlist", "lef_recipe",
                                     "lef", "liberty", "verilog", "timing_log", "log",
                                     "stage", "tool", "design", "pdk", "execution", "timing_execution", "removed_producer"])
def test_native_kit_and_docs_current_reverse(native_project, mutation):
    path = native_project / "phase3/stage4/hardmacro/current_kit.json"
    doc = json.loads(path.read_text())
    if mutation in ("gds", "def", "route", "timing_sdc", "timing_netlist", "lef_recipe"):
        p = native_project / doc["inputs"][mutation]["path"]
        p.write_bytes(p.read_bytes() + b"changed")
    elif mutation in ("lef", "liberty", "verilog", "timing_log", "log"):
        p = native_project / doc["outputs"][mutation]["path"]
        p.write_bytes(p.read_bytes() + b"changed")
    elif mutation in ("stage", "tool", "design", "pdk"):
        doc[mutation] = "wrong"
    elif mutation == "execution":
        doc["execution"]["rc"] = 1
    elif mutation == "timing_execution":
        doc["execution"]["timing"]["rc"] = 1
    else:
        doc = {}
    path.write_text(json.dumps(doc))
    assert current.check_kit(native_project)
    assert not kit.run_audit(native_project).passed
    assert not docs.run_audit(native_project, "ip").passed


def test_native_erc_current_is_consumed_without_inventing_density(native_project):
    findings, stats = erc.audit(native_project)
    assert stats["current_erc"] == "CURRENT"
    assert stats["density_checked"] is False


def test_native_xor_absent_connectivity_is_not_pass(native_project):
    rc, _, doc = xor.judge_receipt(native_project, "reports/phase3/gds_xor.json")
    assert rc == 2
    assert doc["design__xor_difference__count"] == 0
    assert doc["current"]["execution"]["rc"] == 0
