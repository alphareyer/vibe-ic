"""A PDK native antenna rule must be measured on the delivered GDS."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import gds_antenna_deck_check as gate  # noqa: E402
import eda_report_audit as audit  # noqa: E402


def test_real_native_rdb_calibration_pair():
    samples = PROGRAMS / "calibration"
    dirty = (samples / "native_antenna_violation.lyrdb").read_text()
    clean = (samples / "native_antenna_clean.lyrdb").read_text()
    assert audit._antenna_klayout_count(dirty) == 1
    assert audit._antenna_klayout_count(clean) == 0
    assert "Executing rule ANT.1" in (samples / "native_antenna_violation.log").read_text()
    assert "Executing rule ANT.1" in (samples / "native_antenna_clean.log").read_text()


def _rdb(violations: int) -> str:
    items = "".join("<item><category>ANT.1</category></item>" for _ in range(violations))
    return ("<?xml version='1.0'?><report-database>"
            "<description>Report for antenna_native</description>"
            "<generator>drc: script='/pdk/tech/drc/main.drc'</generator>"
            "<categories><category><name>ANT.1</name><description>ANT.1: gate ratio"
            "</description></category></categories><items>" + items +
            "</items></report-database>")


class NativeRunner:
    kind = "host"

    def __init__(self, violations=0):
        self.violations = violations
        self.argv = None

    def covers(self, path):
        return True

    def cpath(self, path):
        return str(path)

    def klayout_bin(self):
        return "klayout"

    def run_argv(self, argv, env, *, timeout):
        self.argv = argv
        assert "decks=antenna" in argv
        target = Path(next(x.split("=", 1)[1] for x in argv if x.startswith("report=")))
        target.write_text(_rdb(self.violations))
        return 0, "2026-09-28: Executing rule ANT.1\n", ""


def _project(tmp_path, *, deck=True):
    gds_dir = tmp_path / "phase3/stage4/gds"
    gds_dir.mkdir(parents=True)
    (gds_dir / "layout.gds").write_bytes(b"delivered stream geometry")
    root = tmp_path / "pdk_root"
    drc = root / "fixture_pdk/libs.tech/klayout/tech/drc"
    rules = drc / "rule_decks"
    rules.mkdir(parents=True)
    (drc / "main.drc").write_text("decks: $decks\nload rule_decks\n")
    if deck:
        (rules / "antenna.rb").write_text("# original PDK antenna rule\n")
    receipt = tmp_path / "phase3/librelane_pdk_root.provenance.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"path": str(root),
                                   "derivation": {"pdk": "fixture_pdk"}}))
    return gds_dir / "layout.gds"


@pytest.mark.parametrize("violations, verdict", [(0, "PASS"), (1, "FAIL")])
def test_native_pdk_deck_runs_and_preserves_violations(tmp_path, monkeypatch,
                                                       violations, verdict):
    gds = _project(tmp_path)
    runner = NativeRunner(violations)
    monkeypatch.setattr(gate._kl, "find_runner", lambda **kw: runner)
    result = gate.run(tmp_path, None, None, None, None)
    assert result["verdict"] == verdict, result
    assert result["violations"] == violations
    assert result["gds_sha256"] == hashlib.sha256(gds.read_bytes()).hexdigest()
    assert result["pdk_rule_sha256"] == hashlib.sha256(
        (tmp_path / "pdk_root/fixture_pdk/libs.tech/klayout/tech/drc/"
         "rule_decks/antenna.rb").read_bytes()).hexdigest()
    assert result["rdb_sha256"] == gate._sha(Path(result["rdb"]))
    assert result["transcript_sha256"] == gate._sha(Path(result["transcript"]))
    assert runner.argv is not None


def test_stale_native_transcript_is_not_measured(tmp_path):
    gds = _project(tmp_path)
    old_sha = hashlib.sha256(b"a different GDS").hexdigest()
    rdb = tmp_path / "report.lyrdb"
    rdb.write_text(_rdb(0))
    log = tmp_path / "report.log"
    log.write_text(gate._BIND_PREFIX + old_sha + "\nExecuting rule ANT.1\n")
    now_sha = gate._sha(gds)
    count, why, _ = gate._bound_native_count(gds, rdb, log, now_sha)
    assert count is None
    assert "another GDS SHA-256" in why


def test_no_pdk_antenna_deck_remains_not_measured(tmp_path):
    _project(tmp_path, deck=False)
    result = gate.run(tmp_path, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP"
    assert "no native antenna rule deck" in result["reason"]


@pytest.mark.parametrize("bridge_text, reason", [
    ('{"antenna_deck":"missing.json"}', "points at a missing deck"),
    ("{invalid JSON", "is unreadable"),
])
def test_broken_explicit_bridge_cannot_fall_through_to_another_deck(
        tmp_path, monkeypatch, bridge_text, reason):
    _project(tmp_path)
    bridge = tmp_path / "input/pdk/bridge/signoff_config.json"
    bridge.parent.mkdir(parents=True)
    bridge.write_text(bridge_text)
    runner = NativeRunner()
    monkeypatch.setattr(gate._kl, "find_runner", lambda **kw: runner)
    result = gate.run(tmp_path, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP"
    assert reason in result["reason"]
    assert runner.argv is None


def test_multiple_streams_cannot_select_a_delivered_gds_by_filename(tmp_path,
                                                                     monkeypatch):
    gds = _project(tmp_path)
    gds.with_name("another.gds").write_bytes(b"a different stream")
    runner = NativeRunner()
    monkeypatch.setattr(gate._kl, "find_runner", lambda **kw: runner)
    result = gate.run(tmp_path, None, None, None, None)
    assert result["verdict"] == "DISCLOSED_SKIP"
    assert "multiple streamed GDS" in result["reason"]
    assert runner.argv is None


def test_declared_native_deck_execution_failure_is_not_clean(tmp_path, monkeypatch):
    _project(tmp_path)
    runner = NativeRunner()
    runner.run_argv = lambda *a, **kw: (1, "", "KLayout failed")
    monkeypatch.setattr(gate._kl, "find_runner", lambda **kw: runner)
    result = gate.run(tmp_path, None, None, None, None)
    assert result["verdict"] == "FAIL"
    assert "failed rc=1" in result["reason"]
