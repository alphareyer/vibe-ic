#!/usr/bin/env python3
"""llv1 W20 / decision 25: a flagged run never overwrites, and is never
published as, the vibe-ic product result.

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
FLAGGED_CELL = "v9.9.9_openpdkx_librelane"
IMPL_FILE = "IMPL.json"
IMPORT_MANIFEST_REL = "reports/phase3/impl/import_manifest.json"


def _pub_id():
    """The identity module, imported where a test asks it directly; the CLI
    tests go through the publisher only, so they run (and fail by behaviour)
    on a publisher that has never heard of it."""
    import _publication_identity
    return _publication_identity


def _publish(run, dest_root, *extra, json_out=None):
    args = base._base_args(run, dest_root) + list(extra)
    if json_out is not None:
        args += ["--json", str(json_out)]
    return base._run(args)


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _flagged_run(tmp_path, name="flagged"):
    run = base._make_run(tmp_path / name)
    _impl_flow.write_record(run, "librelane", resolved_by="test")
    return run


def _cells(dest_root):
    ic = dest_root / "ic" / "widgetmul"
    return sorted(p.name for p in ic.iterdir() if p.is_dir() and p.name.startswith("v"))


# ── the default path is unchanged ─────────────────────────────────────────

def test_a_default_run_keeps_its_slot_and_carries_no_impl_record(tmp_path):
    run = base._make_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    summary = tmp_path / "summary.json"
    r = _publish(run, dest_root, json_out=summary)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _cells(dest_root) == [DEFAULT_CELL]
    assert not (dest_root / "ic" / "widgetmul" / DEFAULT_CELL / IMPL_FILE).exists()
    got = json.loads(summary.read_text())
    assert (got["impl"], got["impl_source"], got["impl_evidence"]) == ("vibe-ic", "default", [])
    assert "impl        :" not in r.stdout


# ── a flagged run ─────────────────────────────────────────────────────────

def test_a_flagged_run_takes_its_own_slot_and_says_so_in_the_cell(tmp_path):
    run = _flagged_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    summary = tmp_path / "summary.json"
    r = _publish(run, dest_root, json_out=summary)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _cells(dest_root) == [FLAGGED_CELL]
    cell = dest_root / "ic" / "widgetmul" / FLAGGED_CELL
    record = json.loads((cell / IMPL_FILE).read_text())
    assert record["impl"] == "librelane" and record["flag"] == "--librelane"
    assert record["product_result"] is False and record["source"] == "record"
    assert "self-check  : PASS" in r.stdout
    assert "NOT the vibe-ic product result (decision 25)" in r.stdout
    got = json.loads(summary.read_text())
    assert (got["impl"], got["impl_source"]) == ("librelane", "record")
    assert got["dest"].endswith(FLAGGED_CELL)


def test_a_flagged_run_never_overwrites_the_default_cell(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    default_run = base._make_run(tmp_path / "default")
    assert _publish(default_run, dest_root).returncode == 0
    default_cell = dest_root / "ic" / "widgetmul" / DEFAULT_CELL
    before = _tree(default_cell)
    for extra in ([], ["--force"]):
        r = _publish(_flagged_run(tmp_path, f"flagged{len(extra)}"), dest_root, *extra)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _tree(default_cell) == before
    assert _cells(dest_root) == [DEFAULT_CELL, FLAGGED_CELL]


def test_declaring_a_flagged_run_as_the_default_refuses_by_name(tmp_path):
    run = _flagged_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    r = _publish(run, dest_root, "--impl", "vibe-ic")
    assert r.returncode == 1
    assert "REFUSED: PUBLISH_FLAGGED_INTO_DEFAULT_SLOT" in r.stderr
    assert not (dest_root / "ic" / "widgetmul").exists() or _cells(dest_root) == []


def test_a_declared_mode_must_match_the_run(tmp_path):
    dest_root = tmp_path / "benchmark-data"
    r = _publish(base._make_run(tmp_path / "d"), dest_root, "--impl", "librelane")
    assert r.returncode == 1 and "PUBLISH_IMPL_CONFLICT" in r.stderr
    r = _publish(base._make_run(tmp_path / "e"), dest_root, "--impl", "openroad")
    assert r.returncode == 1 and "PUBLISH_IMPL_CONFLICT" in r.stderr and "direct" in r.stderr
    assert _publish(_flagged_run(tmp_path), dest_root, "--impl", "librelane").returncode == 0
    assert _cells(dest_root) == [FLAGGED_CELL]


def test_a_flagged_dry_run_names_its_slot_and_writes_nothing(tmp_path):
    run = _flagged_run(tmp_path)
    dest_root = tmp_path / "benchmark-data"
    summary = tmp_path / "summary.json"
    r = _publish(run, dest_root, "--dry-run", json_out=summary)
    assert r.returncode == 0, r.stderr
    assert json.loads(summary.read_text())["dest"].endswith(FLAGGED_CELL)
    assert not (dest_root / "ic" / "widgetmul" / FLAGGED_CELL).exists()


# ── without the record: the run's own artefacts ──────────────────────────

def _append_provenance(run, row):
    with (run / "provenance.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def test_without_the_record_an_external_provenance_row_decides(tmp_path):
    run = base._make_run(tmp_path)
    _append_provenance(run, {"tool": "openroad", "attributed_to": "librelane",
                             "outputs": {"phase3/stage3/pnr/routed.def": "0" * 64}})
    identity = _pub_id().run_identity(run)
    assert (identity["impl"], identity["source"]) == ("librelane", "evidence")
    dest_root = tmp_path / "benchmark-data"
    assert _publish(run, dest_root).returncode == 0
    assert _cells(dest_root) == [FLAGGED_CELL]


def test_without_the_record_the_import_manifest_decides_even_unnamed(tmp_path):
    run = base._make_run(tmp_path)
    manifest = run / IMPORT_MANIFEST_REL
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"flow": "librelane", "rows": [{"flow": "librelane"}]}))
    assert _pub_id().run_identity(run)["impl"] == "librelane"
    manifest.write_text(json.dumps({"rows": []}))
    with pytest.raises(_pub_id().IdentityRefusal, match="PUBLISH_IMPL_CONFLICT.*not a flow mode"):
        _pub_id().run_identity(run)


def test_without_the_record_an_orchestrator_report_decides(tmp_path):
    run = base._make_run(tmp_path)
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"impl": "librelane", "verdict": "PASS"}))
    assert _pub_id().run_identity(run)["impl"] == "librelane"
    report.write_text(json.dumps({"impl": "vibe-ic", "verdict": "PASS"}))
    assert _pub_id().run_identity(run)["impl"] == "vibe-ic"


def test_artefacts_that_disagree_refuse(tmp_path):
    run = base._make_run(tmp_path)
    _append_provenance(run, {"tool": "openroad", "attributed_to": "librelane"})
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"impl": "orfs"}))
    with pytest.raises(_pub_id().IdentityRefusal, match="PUBLISH_IMPL_CONFLICT"):
        _pub_id().run_identity(run)
    r = _publish(run, tmp_path / "benchmark-data")
    assert r.returncode == 1 and "REFUSED: PUBLISH_IMPL_CONFLICT" in r.stderr


def test_an_artefact_contradicting_the_record_refuses(tmp_path):
    run = _flagged_run(tmp_path)
    report = run / "reports" / "orchestrator" / "phase3_one_shot.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"impl": "orfs"}))
    with pytest.raises(_pub_id().IdentityRefusal, match=r"PUBLISH_IMPL_CONFLICT: the mode record .* says 'librelane'"):
        _pub_id().run_identity(run)


def test_a_default_ledger_row_is_not_a_claim_against_the_record(tmp_path):
    """W1 before W2: a flagged project's admission row may lack `impl`, which
    reads as the default. That row must not contradict the record."""
    run = _flagged_run(tmp_path)
    ledger = run / _impl_flow.STATE_DIR / _impl_flow.ADMISSION_LEDGER
    ledger.write_text(json.dumps({"identity": {"dispatch_config": {"phase": 3}}}) + "\n")
    assert _pub_id().run_identity(run)["impl"] == "librelane"
    ledger.write_text(json.dumps({"identity": {"dispatch_config": {"impl": "librelane"}}}) + "\n")
    identity = _pub_id().run_identity(run)
    assert identity["impl"] == "librelane"
    assert any("dispatch_config.impl" in e["where"] for e in identity["evidence"])


def test_a_damaged_record_is_never_read_as_the_default(tmp_path):
    run = base._make_run(tmp_path)
    path = _impl_flow.record_path(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    dest_root = tmp_path / "benchmark-data"
    r = _publish(run, dest_root)
    assert r.returncode == 1 and "REFUSED: PUBLISH_IMPL_UNREADABLE" in r.stderr
    assert not (dest_root / "ic" / "widgetmul" / DEFAULT_CELL).exists()


def test_slot_names():
    assert _pub_id().slot_name("1.2.3", "pdkA", "vibe-ic") == "v1.2.3_pdkA"
    assert _pub_id().slot_name("1.2.3", "pdkA", "librelane") == "v1.2.3_pdkA_librelane"
    assert _pub_id().cell_record({"impl": "vibe-ic", "source": "default", "evidence": []}) is None
