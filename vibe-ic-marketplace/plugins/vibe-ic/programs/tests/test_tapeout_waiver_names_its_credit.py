#!/usr/bin/env python3
"""The Step-36 waiver entry names the credit that produced its tier.

MEASURED on the subservient hardmacro run r32 (gf180mcuD, tree eecc69bec, lane
icsubaudit2). The tape-out checklist reached PASS_WITH_WAIVERS for ONE reason:
its DRC slot was credited by a die-level ATTRIBUTION (M2.4, M3.4 handed to the
integrator; `drc_library_internal_waived: false`, no LVS / SI waiver, no
ENV_UNAVAILABLE step), and its own finding says "This is not a waiver". The
entry `signoff_audit._emit_tapeout_waiver_entry` appended to `waivers.json`
nevertheless read "a DRC/LVS slot was credited via a waiver ... must close the
waived slot", listed only `reports/audit/tapeout_checklist.json` as evidence,
and named neither rule nor the integrator handoff. The final summary's
"Waivers" section quotes that entry verbatim, so the one human-readable place a
reviewer is sent told them to close a waiver that does not exist and did not
show them the obligation that does.

Cases, both directions:
  attribution only      -> the entry says ATTRIBUTION, not waiver; names the
                           rules; carries the report, the attribution record
                           and the handoff as evidence; still review_required,
                           still covered by the growth declaration.
  library-internal only -> unchanged shape of claim: a waiver, named as one.
  no recorded credit    -> the legacy sentence, byte for byte.
  two credits           -> both named; the "nothing was waived" sentence is
                           NOT used when something was.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(TESTS))

import signoff_audit as audit  # noqa: E402
import waivers_materialize as WM  # noqa: E402


def _entry(project: Path) -> dict:
    data = json.loads((project / "waivers.json").read_text())
    got = [w for w in data["waived_steps"]
           if str(w.get("id")) == str(audit._TAPEOUT_STEP_ID)]
    assert len(got) == 1, data
    return got[0]


def _emit(tmp_path: Path, summary: dict) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = audit.AuditResult(program="signoff_audit:tapeout", passed=True)
    r.summary = dict(summary, verdict_tier="PASS_WITH_WAIVERS")
    audit._emit_tapeout_waiver_entry(tmp_path, r)
    return _entry(tmp_path)


def test_an_attribution_credit_is_recorded_as_an_attribution_end_to_end(
        tmp_path):
    """Through `main()`, on the same fixture the attribution branch is tested
    with, so the summary field the entry reads is the one `_check_tapeout`
    actually writes."""
    from test_die_level_attribution_reaches_the_signoff_checkers import (
        _tapeout_project)
    p = _tapeout_project(tmp_path)
    rc = audit.main([str(p), "--mode", "tapeout"])
    assert rc == audit.WAIVER_EXIT_CODE
    e = _entry(p)
    assert e["credited_by"] == ["drc_die_level_attribution"]
    assert e["attributed_rules"] == ["m2.9", "m3.9"]
    assert "DIE-LEVEL ATTRIBUTION, not a waiver" in e["reason"]
    assert "m2.9, m3.9" in e["reason"]
    assert "credited via a waiver" not in e["reason"]
    assert e["integrator_handoff"] == "phase3/stage4/hardmacro/" \
        "integrator_requirements.json"
    assert e["integrator_handoff"] in e["evidence"]
    assert "reports/phase3/die_level_rule_attribution.json" in e["evidence"]
    assert e["drc_die_level_attributed"] is True
    assert e["drc_library_internal_waived"] is False
    # the review obligation and the tier are NOT relaxed by the rename
    assert e["review_required"] is True
    assert e["verdict_tier"] == "PASS_WITH_WAIVERS"
    assert e["ticket"] == audit._TAPEOUT_WAIVER_TICKET
    data = json.loads((p / "waivers.json").read_text())
    assert audit._TAPEOUT_STEP_ID in data["growth_rationale_covers"]
    assert "die-level attribution" in data["growth_rationale"]


def test_a_library_internal_waiver_is_still_called_a_waiver(tmp_path):
    e = _emit(tmp_path, {"drc_library_internal_waived": True,
                         "env_unavailable_steps": []})
    assert e["credited_by"] == ["drc_library_internal_waiver"]
    assert "library-internal waiver" in e["reason"]
    assert "ATTRIBUTION" not in e["reason"]
    assert "Nothing here was waived" not in e["reason"]
    assert "attributed_rules" not in e


def test_an_entry_with_no_recorded_credit_keeps_the_legacy_sentence(tmp_path):
    e = _emit(tmp_path, {})
    assert e["credited_by"] == []
    assert e["reason"] == audit._TAPEOUT_WAIVER_REASON
    assert e["evidence"] == ["reports/audit/tapeout_checklist.json"]


def test_an_attribution_beside_a_real_waiver_names_both(tmp_path):
    e = _emit(tmp_path, {
        "drc_die_level_attributed": True,
        "drc_die_level_attribution": {
            "rules": ["M9.4"], "drc_report": "reports/phase3/drc_signoff.rpt",
            "attribution_report":
                "reports/phase3/die_level_rule_attribution.json",
            "handoff": "phase3/stage4/hardmacro/integrator_requirements.json"},
        "lvs_power_pin_only_waived": True})
    assert e["credited_by"] == ["drc_die_level_attribution",
                                "lvs_power_pin_only_waiver"]
    assert "DIE-LEVEL ATTRIBUTION" in e["reason"]
    assert "POWER_PIN_ONLY waiver" in e["reason"]
    assert "Nothing here was waived" not in e["reason"], (
        "an LVS waiver WAS taken; the attribution-only sentence would be false")


def test_the_growth_declaration_still_covers_every_credit_kind(tmp_path):
    """The entry stays a sign-off tier entry by its OWN fields whatever
    credited it, so the declaration never leaves it uncovered."""
    for i, summary in enumerate((
            {}, {"drc_library_internal_waived": True},
            {"drc_die_level_attributed": True,
             "drc_die_level_attribution": {"rules": ["M9.4"]}})):
        d = tmp_path / f"k{i}"
        e = _emit(d, summary)
        assert WM._is_signoff_tier_entry(e), e
        data = json.loads((d / "waivers.json").read_text())
        assert data["growth_rationale_covers"] == [audit._TAPEOUT_STEP_ID]
