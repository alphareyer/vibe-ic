"""Tapeout tier credits remain explicit without machine-issued waivers."""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(TESTS))
import signoff_audit as audit  # noqa: E402


def _credits(tmp_path, summary):
    tmp_path.mkdir(parents=True, exist_ok=True)
    result = audit.AuditResult(program="signoff_audit:tapeout", passed=True)
    result.summary = dict(summary, verdict_tier="PASS_WITH_WAIVERS")
    rows = audit._tapeout_tier_credits(tmp_path, result.summary)
    audit._emit_tapeout_waiver_entry(tmp_path, result)
    assert not (tmp_path / "waivers.json").exists()
    return rows


def test_an_attribution_credit_is_recorded_as_an_attribution_end_to_end(tmp_path):
    from test_die_level_attribution_reaches_the_signoff_checkers import _tapeout_project
    project = _tapeout_project(tmp_path)
    rc = audit.main([str(project), "--mode", "tapeout"])
    assert rc == audit.WAIVER_EXIT_CODE
    assert not (project / "waivers.json").exists()
    rows = audit._tapeout_tier_credits(project, {
        "drc_die_level_attributed": True,
        "drc_die_level_attribution": {
            "rules": ["m2.9", "m3.9"],
            "drc_report": "reports/phase3/drc_signoff.rpt",
            "attribution_report": "reports/phase3/die_level_rule_attribution.json",
            "handoff": "phase3/stage4/hardmacro/integrator_requirements.json",
        },
    })
    assert [r["kind"] for r in rows] == ["drc_die_level_attribution"]
    row = rows[0]
    assert row["rules"] == ["m2.9", "m3.9"]
    assert "DIE-LEVEL ATTRIBUTION, not a waiver" in row["clause"]
    assert row["handoff"] in row["evidence"]
    assert "reports/phase3/die_level_rule_attribution.json" in row["evidence"]


def test_a_library_internal_waiver_is_still_called_a_waiver(tmp_path):
    rows = _credits(tmp_path, {"drc_library_internal_waived": True,
                               "env_unavailable_steps": []})
    assert [r["kind"] for r in rows] == ["drc_library_internal_waiver"]
    assert "library-internal waiver" in rows[0]["clause"]
    assert "ATTRIBUTION" not in rows[0]["clause"]


def test_no_recorded_credit_creates_no_approval(tmp_path):
    assert _credits(tmp_path, {}) == []


def test_an_attribution_beside_a_real_waiver_names_both(tmp_path):
    rows = _credits(tmp_path, {
        "drc_die_level_attributed": True,
        "drc_die_level_attribution": {
            "rules": ["M9.4"],
            "drc_report": "reports/phase3/drc_signoff.rpt",
            "attribution_report": "reports/phase3/die_level_rule_attribution.json",
            "handoff": "phase3/stage4/hardmacro/integrator_requirements.json"},
        "lvs_power_pin_only_waived": True})
    assert [r["kind"] for r in rows] == ["drc_die_level_attribution",
                                        "lvs_power_pin_only_waiver"]
    assert "DIE-LEVEL ATTRIBUTION" in rows[0]["clause"]
    assert "POWER_PIN_ONLY waiver" in rows[1]["clause"]


def test_no_credit_kind_can_make_a_waiver_file(tmp_path):
    for i, summary in enumerate(({}, {"drc_library_internal_waived": True},
                                 {"drc_die_level_attributed": True,
                                  "drc_die_level_attribution": {"rules": ["M9.4"]}})):
        _credits(tmp_path / f"k{i}", summary)
