"""W0 (`--librelane` v1): the implementation-flow mode record.

Contracts:
  1. The default writes nothing and reads nothing but its own absent record:
     resolving and "recording" vibe-ic leaves the project tree byte-identical,
     even over a corrupt admission ledger it must not consult.
  2. `dispatch_config_entry` is empty for the default, so a default run's
     admission identity cannot move.
  3. `--librelane` is recorded once; `--orfs` and `openroad` refuse BY NAME.
  4. An in-place switch refuses with IMPL_MODE_CONFLICT, whichever way it
     goes, including against spans the REAL admission ledger admitted.
  5. A damaged record is a refusal, never the default.
  6. Once a record exists it is the answer: later history (a flagged
     admission row without `impl`) cannot lock the project out of its mode.
     History -- the ledger AND default-flow output -- is asked when a record
     is CREATED, by resolve() or by write_record().
  7. The record's image is never silently frozen: a null image carries its
     NOT CAPTURED reason and is filled once, recorded; a second dispatch on
     ANOTHER image refuses IMPL_IMAGE_CHANGED.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _impl_flow as IF  # noqa: E402
import canonical_run_admission as CRA  # noqa: E402


def _tree(project: Path) -> dict:
    return {str(p.relative_to(project)): (hashlib.sha256(p.read_bytes()).hexdigest()
                                          if p.is_file() else "dir")
            for p in sorted(project.rglob("*"))}


def _admit(project: Path, config: dict) -> None:
    adm = CRA.admit(project, "phase3", container_image="image-under-test",
                    config=config, program_paths=[])
    assert adm.admitted, adm


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "input").mkdir(parents=True)
    (p / "input" / "spec.md").write_text("a design input\n")
    return p


def test_ledger_constants_agree_with_the_admission_module():
    assert IF.STATE_DIR == CRA.STATE_DIR
    assert IF.ADMISSION_LEDGER == CRA.LEDGER_NAME


def test_default_writes_nothing_and_leaves_the_tree_identical(project):
    before = _tree(project)
    assert IF.resolve(project, None) == IF.IMPL_DEFAULT
    assert IF.resolve(project, "vibe-ic") == IF.IMPL_DEFAULT
    assert IF.write_record(project, IF.IMPL_DEFAULT, resolved_by="t") is None
    assert IF.recorded_impl(project) == IF.IMPL_DEFAULT
    assert _tree(project) == before
    assert not IF.record_path(project).exists()


def test_default_never_reads_the_admission_ledger(project):
    ledger = project / IF.STATE_DIR / IF.ADMISSION_LEDGER
    ledger.parent.mkdir(parents=True)
    ledger.write_text("this is not json\n")
    before = _tree(project)
    assert IF.resolve(project, None) == IF.IMPL_DEFAULT
    assert _tree(project) == before
    # The same ledger IS read for a non-default request, and refuses there.
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.resolve(project, "librelane")
    assert ei.value.reason_class == IF.IMPL_RECORD_UNREADABLE


def test_dispatch_config_entry_is_empty_for_the_default():
    assert IF.dispatch_config_entry(None) == {}
    assert IF.dispatch_config_entry("vibe-ic") == {}
    assert IF.dispatch_config_entry("librelane") == {"impl": "librelane"}


def test_librelane_is_recorded_once(project):
    assert IF.resolve(project, "librelane") == IF.IMPL_LIBRELANE
    # resolve() alone writes nothing: the front door writes, once.
    assert not IF.record_path(project).exists()
    path = IF.write_record(project, "librelane", resolved_by="vibe_ic_one_shot_runner")
    rec = json.loads(path.read_text())
    assert rec == {**rec, "schema": IF.SCHEMA, "impl": "librelane",
                   "flag": "--librelane", "tool_defaults": {}, "image": None,
                   "resolved_by": "vibe_ic_one_shot_runner"}
    assert IF.validate_record(rec) == []
    first = path.read_bytes()
    assert IF.write_record(project, "librelane", resolved_by="child") == path
    assert path.read_bytes() == first  # resolved once, never rewritten
    assert IF.resolve(project, "librelane") == IF.IMPL_LIBRELANE
    assert IF.recorded_impl(project) == IF.IMPL_LIBRELANE


@pytest.mark.parametrize("value", ["orfs", "ORFS"])
def test_orfs_is_reserved_and_refused_by_name(project, value):
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.resolve(project, value)
    assert ei.value.reason_class == IF.IMPL_NOT_YET_SUPPORTED
    assert "--orfs" in str(ei.value)
    with pytest.raises(IF.ImplRefusal):
        IF.write_record(project, value, resolved_by="t")
    assert not IF.record_path(project).exists()


def test_openroad_is_not_a_mode_and_names_the_reserved_flag(project):
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.resolve(project, "openroad")
    assert ei.value.reason_class == IF.IMPL_UNKNOWN
    assert "direct" in str(ei.value) and "--orfs" in str(ei.value)
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.resolve(project, "yosys")
    assert ei.value.reason_class == IF.IMPL_UNKNOWN


def test_a_missing_flag_on_a_librelane_project_conflicts(project):
    IF.write_record(project, "librelane", resolved_by="t")
    for req in (None, "vibe-ic"):
        with pytest.raises(IF.ImplRefusal) as ei:
            IF.resolve(project, req)
        assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT
        assert "fresh project clone" in str(ei.value)
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.write_record(project, "vibe-ic", resolved_by="t")
    assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT


def test_a_record_for_another_mode_is_never_overwritten(project):
    path = IF.record_path(project)
    path.parent.mkdir(parents=True)
    other = {"schema": IF.SCHEMA, "impl": "orfs", "flag": "--orfs",
             "resolved_at": "t", "resolved_by": "t", "tool_defaults": {},
             "image": None, "image_capture": "NOT CAPTURED: test"}
    path.write_text(json.dumps(other))
    before = path.read_bytes()
    for call in (lambda: IF.write_record(project, "librelane", resolved_by="t"),
                 lambda: IF.resolve(project, "librelane")):
        with pytest.raises(IF.ImplRefusal) as ei:
            call()
        assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT
    assert path.read_bytes() == before


def test_a_project_already_run_by_default_refuses_librelane(project):
    _admit(project, {"entry_step": None})          # a real default admission
    assert IF.admitted_impls(project) == {IF.IMPL_DEFAULT}
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.resolve(project, "librelane")
    assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT
    assert "vibe-ic" in str(ei.value)
    # ...while the default run itself is still admitted as before.
    assert IF.resolve(project, None) == IF.IMPL_DEFAULT


def test_librelane_admissions_do_not_conflict_with_librelane(project):
    IF.write_record(project, "librelane", resolved_by="t")
    _admit(project, {"entry_step": None, **IF.dispatch_config_entry("librelane")})
    assert IF.admitted_impls(project) == {IF.IMPL_LIBRELANE}
    assert IF.resolve(project, "librelane") == IF.IMPL_LIBRELANE


@pytest.mark.parametrize("body", [
    "{not json",
    json.dumps({"schema": IF.SCHEMA, "impl": "vibe-ic", "flag": None,
                "resolved_at": "t", "resolved_by": "t", "tool_defaults": {},
                "image": None}),
    json.dumps({"schema": IF.SCHEMA, "impl": "librelane", "flag": "--orfs",
                "resolved_at": "t", "resolved_by": "t", "tool_defaults": {},
                "image": None}),
    json.dumps({"schema": IF.SCHEMA, "impl": "librelane", "flag": "--librelane",
                "resolved_at": "t", "resolved_by": "t",
                "tool_defaults": {"FP_CORE_UTIL": {"value": 50}}, "image": None}),
])
def test_a_damaged_record_refuses_and_is_never_the_default(project, body):
    path = IF.record_path(project)
    path.parent.mkdir(parents=True)
    path.write_text(body)
    for req in (None, "librelane"):
        with pytest.raises(IF.ImplRefusal) as ei:
            IF.resolve(project, req)
        assert ei.value.reason_class == IF.IMPL_RECORD_UNREADABLE


def test_a_tool_default_with_its_source_is_a_valid_record():
    rec = {"schema": IF.SCHEMA, "impl": "librelane", "flag": "--librelane",
           "resolved_at": "t", "resolved_by": "t", "image": "sha256:" + "0" * 64,
           "tool_defaults": {"FP_CORE_UTIL": {"value": 50,
                                              "source": "librelane default"}}}
    assert IF.validate_record(rec) == []


def test_every_reason_class_is_distinct_and_named():
    assert len(set(IF.REASON_CLASSES)) == len(IF.REASON_CLASSES) == 5
    assert all(r.startswith("IMPL_") for r in IF.REASON_CLASSES)


def test_a_record_is_not_overruled_by_later_rows_without_impl(project):
    """Correctness review MAJOR: a flagged admission row lacking `impl` (every
    row between W1 and W2, or a site W2 misses) must not refuse the mode."""
    IF.write_record(project, "librelane", resolved_by="front door")
    _admit(project, {"entry_step": None})          # no `impl` in the config
    assert IF.admitted_impls(project) == {IF.IMPL_DEFAULT}
    assert IF.resolve(project, "librelane") == IF.IMPL_LIBRELANE
    assert IF.write_record(project, "librelane", resolved_by="child")
    with pytest.raises(IF.ImplRefusal) as ei:      # the default still refused
        IF.resolve(project, None)
    assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT


def test_creating_a_record_checks_the_ledger(project):
    """Correctness review MINOR: write_record without resolve() first."""
    _admit(project, {"entry_step": None})
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.write_record(project, "librelane", resolved_by="t")
    assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT
    assert not IF.record_path(project).exists()
    assert IF.resolve(project, None) == IF.IMPL_DEFAULT


def _prov(project, **row):
    with (project / "provenance.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")


@pytest.mark.parametrize("evidence", ["provenance", "producer", "step"])
def test_default_flow_output_is_proof_of_a_mode(project, evidence):
    """The phase-3 window admits into a deleted temp copy: its output stays."""
    if evidence == "provenance":
        _prov(project, tool="openroad", exit_code=0,
              outputs={"phase3/stage3/pnr/top.def": "sha256:" + "0" * 64})
    else:
        side = project / "phase3" / "stage3" / "pnr" / (
            "producer_identity.json" if evidence == "producer"
            else "step_identity.json")
        side.parent.mkdir(parents=True)
        side.write_text("{}")
    assert IF.admitted_impls(project) == set()
    for call in (lambda: IF.resolve(project, "librelane"),
                 lambda: IF.write_record(project, "librelane", resolved_by="t")):
        with pytest.raises(IF.ImplRefusal) as ei:
            call()
        assert ei.value.reason_class == IF.IMPL_MODE_CONFLICT
        assert "default-flow output" in str(ei.value)
    assert not IF.record_path(project).exists()
    assert IF.resolve(project, None) == IF.IMPL_DEFAULT


def test_phase1_output_and_imported_rows_are_not_default_flow_output(project):
    _prov(project, tool="yosys", exit_code=0,
          outputs={"phase1/generated_docs/L1.json": "sha256:" + "0" * 64})
    _prov(project, tool="openroad", attributed_to="librelane", exit_code=0,
          outputs={"phase3/stage3/pnr/top.def": "sha256:" + "0" * 64})
    assert IF.default_output_evidence(project) == []
    assert IF.resolve(project, "librelane") == IF.IMPL_LIBRELANE


def test_a_new_image_is_never_silently_frozen(project):
    IF.write_record(project, "librelane", resolved_by="t", image="img-A")
    assert IF.write_record(project, "librelane", resolved_by="t", image="img-A")
    assert IF.write_record(project, "librelane", resolved_by="t")  # not given
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.write_record(project, "librelane", resolved_by="t", image="img-B")
    assert ei.value.reason_class == IF.IMPL_IMAGE_CHANGED
    assert "img-A" in str(ei.value) and "img-B" in str(ei.value)
    assert IF.read_record(project)["image"] == "img-A"


def test_a_null_image_carries_its_reason_and_is_filled_once(project):
    """Wave-4b review: a null image is NOT CAPTURED with a reason (#312/#365),
    and the first dispatch that resolves an image fills it, saying so; only a
    change from one image to ANOTHER is refused."""
    IF.write_record(project, "librelane", resolved_by="front door")
    rec = IF.read_record(project)
    assert rec["image"] is None
    assert rec["image_capture"].startswith("NOT CAPTURED:")
    IF.write_record(project, "librelane", resolved_by="phase3", image="img-A")
    rec = IF.read_record(project)
    assert rec["image"] == "img-A"
    assert rec["image_capture"].startswith("FILLED by phase3 at ")
    assert "was NOT CAPTURED:" in rec["image_capture"]
    assert rec["resolved_by"] == "front door"      # the first resolution kept
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.write_record(project, "librelane", resolved_by="t", image="img-B")
    assert ei.value.reason_class == IF.IMPL_IMAGE_CHANGED


def test_a_bare_null_image_is_a_damaged_record(project):
    path = IF.record_path(project)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": IF.SCHEMA, "impl": "librelane", "flag": "--librelane",
        "resolved_at": "t", "resolved_by": "t", "tool_defaults": {},
        "image": None}))
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.read_record(project)
    assert ei.value.reason_class == IF.IMPL_RECORD_UNREADABLE
    assert "image_capture" in str(ei.value)
