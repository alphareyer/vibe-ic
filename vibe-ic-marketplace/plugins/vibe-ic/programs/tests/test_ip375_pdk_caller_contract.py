"""IP37.5's narrow PDK caller contract.

The release adapter owns declaration collection.  These tests exercise only
the producer-side binding: a named current PDK selects its exact directory and
magicrc under a multi-technology mount, while absent or ambiguous metadata is
typed and fail-closed.  No EDA binary or hardmacro view is created here.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import digital_hardmacro_gen as mod  # noqa: E402
import _owner_declared as owner  # noqa: E402


def _pdk(parent: Path, name: str, *magicrc_names: str) -> Path:
    magic = parent / name / "libs.tech" / "magic"
    magic.mkdir(parents=True, exist_ok=True)
    for filename in magicrc_names or (f"{name}.magicrc",):
        (magic / filename).write_text("# test technology\n")
    return magic.parent.parent


def test_named_default_caller_selects_gf180_root_and_magicrc(tmp_path):
    parent = tmp_path / "pdk"
    sky = _pdk(parent, "sky130A")
    gf = _pdk(parent, "gf180mcuD")

    binding = mod.resolve_pdk_binding("gf180mcuD", parent)

    assert binding.pdk_name == "gf180mcuD"
    assert binding.pdk_root == gf.resolve()
    assert binding.magicrc == (
        gf / "libs.tech" / "magic" / "gf180mcuD.magicrc").resolve()
    assert binding.pdk_root != sky.resolve()
    assert "sky130A" not in str(binding.magicrc)


def test_named_binding_does_not_fall_back_to_alphabetical_sibling(tmp_path):
    parent = tmp_path / "pdk"
    _pdk(parent, "aaa_first")
    selected = _pdk(parent, "zzz_requested")

    binding = mod.resolve_pdk_binding("zzz_requested", parent)

    assert binding.pdk_root == selected.resolve()
    assert binding.magicrc.name == "zzz_requested.magicrc"


def test_missing_name_under_multi_pdk_mount_is_typed_ambiguous(tmp_path):
    parent = tmp_path / "pdk"
    _pdk(parent, "sky130A")
    _pdk(parent, "gf180mcuD")

    with pytest.raises(mod.PdkBindingRefusal) as caught:
        mod.resolve_pdk_binding(None, parent)

    assert caught.value.code == "PDK_ROOT_AMBIGUOUS"
    assert len(caught.value.candidates) == 2
    assert "sky130A" in str(caught.value)
    assert "gf180mcuD" in str(caught.value)


def test_absent_root_and_absent_name_are_typed_refusals(tmp_path):
    with pytest.raises(mod.PdkBindingRefusal) as missing_root:
        mod.resolve_pdk_binding("gf180mcuD", tmp_path / "missing")
    assert missing_root.value.code == "PDK_ROOT_ABSENT"

    parent = tmp_path / "pdk"
    _pdk(parent, "gf180mcuD")
    with pytest.raises(mod.PdkBindingRefusal) as missing_name:
        mod.resolve_pdk_binding("", parent)
    assert missing_name.value.code == "PDK_NAME_ABSENT"


def test_multiple_magicrcs_in_named_root_are_typed_ambiguous(tmp_path):
    parent = tmp_path / "pdk"
    _pdk(parent, "gf180mcuD", "a.magicrc", "b.magicrc")

    with pytest.raises(mod.PdkBindingRefusal) as caught:
        mod.resolve_pdk_binding("gf180mcuD", parent)

    assert caught.value.code == "PDK_MAGICRC_AMBIGUOUS"


def test_specific_root_for_a_different_named_pdk_is_typed_mismatched(tmp_path):
    parent = tmp_path / "pdk"
    sky = _pdk(parent, "sky130A")
    _pdk(parent, "gf180mcuD")

    with pytest.raises(mod.PdkBindingRefusal) as caught:
        mod.resolve_pdk_binding("gf180mcuD", sky)

    assert caught.value.code == "PDK_ROOT_MISMATCH"


def test_contract_exposes_only_canonical_name_and_mounted_root(tmp_path):
    signature = inspect.signature(mod.run)
    assert signature.parameters["pdk_name"].default is None
    assert mod.PDK_CALLER_CONTRACT["pdk_name"] == (
        "reports/phase1/pdk_staging_read.json:adopted_pdk_target",
        "phase1/generated_docs/L19_CONSTRAINTS_PDK.json:fields.pdk_target",
    )
    assert mod.PDK_CALLER_CONTRACT["pdk_root"] == (
        "mounted PDK namespace root supplied by the caller")
    assert mod.PDK_CALLER_CONTRACT["refusal"].startswith("PdkBindingRefusal")


def test_run_rejects_caller_name_that_disagrees_with_current_identity(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    definition = project / "input.def"
    definition.write_text("DESIGN macro_a ;\n")
    gds = project / "input.gds"
    gds.write_bytes(b"gds fixture")
    (project / "input/submission_template").mkdir(parents=True)
    (project / "input/submission_template/tapeout_declaration.json").write_text(
        json.dumps(owner.attest({"answers": {"deliverable": "HARDMACRO"}})))
    tech = project / "reports/phase3/technology_units.json"
    tech.parent.mkdir(parents=True)
    tech.write_text(json.dumps({"pdk": "gf180mcuD"}))

    original_def = mod.find_def
    original_gds = mod.find_signoff_gds
    mod.find_def = lambda _project: definition
    mod.find_signoff_gds = lambda _project: gds
    try:
        rc, record = mod.run(project, str(tmp_path), False, False,
                             pdk_name="sky130A")
    finally:
        mod.find_def = original_def
        mod.find_signoff_gds = original_gds

    assert rc == mod.RC_REFUSED
    assert record.status == "REFUSED"
    assert record.reason.startswith("PDK_NAME_MISMATCH:")
