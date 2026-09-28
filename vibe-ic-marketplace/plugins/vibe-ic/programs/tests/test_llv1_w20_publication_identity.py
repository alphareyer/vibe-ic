#!/usr/bin/env python3
"""llv1 W20 / decision 25 (binding): flagged runs are NOT published in v1 and
never overwrite the default publication identity.

Driven through the real publisher CLI on the same synthetic runs its own tests
use; the mode record is written by W0's real `_impl_flow.write_record`.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import test_benchmark_evidence_publish as base  # noqa: E402
import _impl_flow  # noqa: E402

DEFAULT_CELL = "v9.9.9_openpdkx"
IMPORT_MANIFEST_REL = "reports/phase3/impl/import_manifest.json"


def _pub_id():
    """The identity module, imported where a test asks it directly; the CLI
    tests go through the publisher only."""
    import _publication_identity
    return _publication_identity


def _publish(run, dest_root, *extra, json_out=None):
    args = base._base_args(run, dest_root) + list(extra)
    if json_out is not None:
        args += ["--json", str(json_out)]
    return base._run(args)


def _tree(root: Path) -> dict:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _flagged_run(tmp_path, name="flagged"):
    run = base._make_run(tmp_path / name)
    _impl_flow.write_record(run, "librelane", resolved_by="test")
    return run


def _refused_writing_nothing(r, dest_root, reason):
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"REFUSED: {reason}" in r.stderr, r.stderr
    assert not (dest_root / "ic").exists(), sorted(p for p in dest_root.rglob("*"))


# ── the default path is unchanged ─────────────────────────────────────────

def test_a_default_run_keeps_its_slot_and_carries_no_impl_record(tmp_path):
    run = base._make_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    summary = tmp_path / "summary.json"
    r = _publish(run, dest_root, json_out=summary)
    assert r.returncode == 0, r.stdout + r.stderr
    ic = dest_root / "ic" / "widgetmul"
    assert sorted(p.name for p in ic.iterdir()) == ["input", DEFAULT_CELL]
    assert not (ic / DEFAULT_CELL / "IMPL.json").exists()
    got = json.loads(summary.read_text())
    assert (got["impl"], got["impl_source"], got["impl_evidence"], got["impl_unread"]) == \
        ("vibe-ic", "default", [], [])


@pytest.mark.parametrize("damage", ["truncated_report", "torn_ledger", "provenance_byte",
                                    "torn_provenance",
                                    "report_impl_not_a_mode"])
def test_a_damaged_side_file_does_not_change_a_default_run(tmp_path, damage):
    """Review W20: none of these runs ever saw a flag. The side file is
    recorded as unread; the run is published exactly as before."""
    run = base._make_run(tmp_path)
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    if damage == "truncated_report":
        report.write_text('{"verdict": "PA')
    elif damage == "torn_ledger":
        ledger = run / _impl_flow.STATE_DIR / _impl_flow.ADMISSION_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"identity": {"dispatch_config": {"phase": 2}}}) + '\n{"identity": {"dis')
    elif damage == "provenance_byte":
        with (run / "provenance.jsonl").open("ab") as fh:
            fh.write(b'{"tool": "x\xff"}\n')
    elif damage == "torn_provenance":
        with (run / "provenance.jsonl").open("a") as fh:
            fh.write('{"tool": "x"\n')
    else:
        report.write_text(json.dumps({"impl": {"pnr": "openroad"}}))
    dest_root = tmp_path / "benchmark-data"
    summary = tmp_path / "summary.json"
    r = _publish(run, dest_root, json_out=summary)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest_root / "ic" / "widgetmul" / DEFAULT_CELL / "RESULT.md").is_file()
    got = json.loads(summary.read_text())
    assert got["impl"] == "vibe-ic"
    if damage != "provenance_byte":      # a replaced byte still parses the other rows
        assert got["impl_unread"], got
    if damage == "torn_provenance":
        assert any("provenance.jsonl line 2: not a JSON object" in item
                   for item in got["impl_unread"]), got["impl_unread"]


# ── a flagged run is not published in v1 ─────────────────────────────────

def test_a_flagged_run_refuses_by_name_and_writes_nothing(tmp_path):
    run = _flagged_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    r = _publish(run, dest_root)
    _refused_writing_nothing(r, dest_root, "PUBLISH_FLAGGED_NOT_IN_V1")
    assert "--librelane" in r.stderr and "from record" in r.stderr
    assert ".vibeic-state/impl-mode-v1.json says 'librelane'" in r.stderr
    assert "Remedy: publish only default-flow runs in v1" in r.stderr


def test_a_flagged_dry_run_refuses_too(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(_flagged_run(tmp_path), dest_root, "--dry-run"),
                             dest_root, "PUBLISH_FLAGGED_NOT_IN_V1")


def test_a_flagged_run_touches_neither_the_default_cell_nor_the_shared_input(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    assert _publish(base._make_run(tmp_path / "default"), dest_root).returncode == 0
    ic = dest_root / "ic" / "widgetmul"
    before = _tree(ic)
    assert any(k.startswith("input/") for k in before) and any(k.startswith(DEFAULT_CELL) for k in before)
    for extra in ([], ["--force"], ["--force", "--impl", "librelane"]):
        r = _publish(_flagged_run(tmp_path, f"flagged{len(extra)}"), dest_root, *extra)
        assert r.returncode == 1 and "PUBLISH_FLAGGED_NOT_IN_V1" in r.stderr
        assert _tree(ic) == before


def test_declaring_a_flagged_run_as_the_default_refuses_by_name(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(_flagged_run(tmp_path), dest_root, "--impl", "vibe-ic"),
                             dest_root, "PUBLISH_FLAGGED_INTO_DEFAULT_SLOT")


def test_a_declared_mode_must_match_the_run(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    r = _publish(base._make_run(tmp_path / "d"), dest_root, "--impl", "librelane")
    assert r.returncode == 1 and "PUBLISH_IMPL_CONFLICT" in r.stderr
    r = _publish(base._make_run(tmp_path / "e"), dest_root, "--impl", "openroad")
    assert r.returncode == 1 and "PUBLISH_IMPL_CONFLICT" in r.stderr and "direct" in r.stderr
    assert not (dest_root / "ic").exists()


# ── without the record: the run's own artefacts ──────────────────────────

def _append_provenance(run, row):
    with (run / "provenance.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")


@pytest.mark.parametrize("claim", ["provenance", "manifest", "report", "ledger"])
def test_without_the_record_the_runs_own_claim_is_enough_to_refuse(tmp_path, claim):
    run = base._make_run(tmp_path)
    if claim == "provenance":
        _append_provenance(run, {"tool": "openroad", "attributed_to": "librelane",
                                 "outputs": {"phase3/stage3/pnr/routed.def": "0" * 64}})
    elif claim == "manifest":
        manifest = run / IMPORT_MANIFEST_REL
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"flow": "librelane", "rows": [{"flow": "librelane"}]}))
    elif claim == "report":
        report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"impl": "librelane"}))
    else:
        ledger = run / _impl_flow.STATE_DIR / _impl_flow.ADMISSION_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(json.dumps({"identity": {"dispatch_config": {"impl": "librelane"}}}) + "\n")
    identity = _pub_id().run_identity(run)
    assert (identity["impl"], identity["source"]) == ("librelane", "evidence")
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(run, dest_root), dest_root, "PUBLISH_FLAGGED_NOT_IN_V1")


def test_a_manifest_naming_no_flow_still_refuses(tmp_path):
    run = base._make_run(tmp_path)
    manifest = run / IMPORT_MANIFEST_REL
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"rows": []}))
    with pytest.raises(_pub_id().IdentityRefusal, match="PUBLISH_IMPL_CONFLICT.*not a flow mode"):
        _pub_id().run_identity(run)


def test_artefacts_that_disagree_refuse(tmp_path):
    run = base._make_run(tmp_path)
    _append_provenance(run, {"tool": "openroad", "attributed_to": "librelane"})
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"impl": "orfs"}))
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(run, dest_root), dest_root, "PUBLISH_IMPL_CONFLICT")


def test_an_artefact_contradicting_the_record_refuses(tmp_path):
    run = _flagged_run(tmp_path)
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"impl": "orfs"}))
    with pytest.raises(_pub_id().IdentityRefusal,
                       match=r"PUBLISH_IMPL_CONFLICT: the mode record .* says 'librelane'"):
        _pub_id().run_identity(run)


def test_a_default_ledger_row_is_not_a_claim_against_the_record(tmp_path):
    """W1 before W2: a flagged project's admission row may lack `impl`."""
    run = _flagged_run(tmp_path)
    ledger = run / _impl_flow.STATE_DIR / _impl_flow.ADMISSION_LEDGER
    ledger.write_text(json.dumps({"identity": {"dispatch_config": {"phase": 3}}}) + "\n")
    assert _pub_id().run_identity(run)["impl"] == "librelane"


def test_a_damaged_record_is_never_read_as_the_default(tmp_path):
    run = base._make_run(tmp_path)
    path = _impl_flow.record_path(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(run, dest_root), dest_root, "PUBLISH_IMPL_UNREADABLE")


def test_a_missing_record_and_truncated_import_manifest_refuse(tmp_path):
    run = base._make_run(tmp_path)
    manifest = run / IMPORT_MANIFEST_REL
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"flow": "librelane", "rows": [')
    dest_root = tmp_path / "benchmark-data"
    _refused_writing_nothing(_publish(run, dest_root), dest_root,
                             "PUBLISH_IMPL_UNREADABLE")


@pytest.mark.parametrize("value", [["librelane"], 3])
def test_malformed_manifest_flow_type_refuses_by_name(tmp_path, value):
    run = base._make_run(tmp_path)
    manifest = run / IMPORT_MANIFEST_REL
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"flow": value, "rows": []}))
    with pytest.raises(_pub_id().IdentityRefusal, match="PUBLISH_IMPL_UNREADABLE"):
        _pub_id().run_identity(run)


# ── kept for the day the owner opens flagged publication ──────────────────

def test_slot_names_and_cell_record_are_kept_but_unreachable_in_v1(tmp_path):
    pub = _pub_id()
    assert pub.slot_name("1.2.3", "pdkA", "vibe-ic") == "v1.2.3_pdkA"
    assert pub.slot_name("1.2.3", "pdkA", "librelane") == "v1.2.3_pdkA_librelane"
    assert pub.cell_record({"impl": "vibe-ic", "source": "default", "evidence": []}) is None
    flagged = {"impl": "librelane", "source": "record", "evidence": []}
    assert pub.cell_record(flagged)["product_result"] is False
    with pytest.raises(pub.IdentityRefusal, match="PUBLISH_FLAGGED_NOT_IN_V1"):
        pub.check_publishable(flagged)
