"""R-0915-87(4): the post-route STA summary counts the multi-corner sign-off report.

MEASURED on subservient r27: `reports/phase3/sta_mcorner_ocv.rpt` holds a SETUP
section (SS, max SPEF) and a HOLD section (FF, min SPEF), each stamped
`STA_BASIS: POST_ROUTE_SPEF`, while `post_route_summary.json` said
STA_SINGLE_CORNER_ONLY with corner_reports_distinct_by_basis POST_ROUTE 0 —
only `per_corner/` directories were counted, though the same audit already
read that report through `_signoff_basis_corners_elsewhere`. Fixture: r27's own reports, vendored
(tests/fixtures/sta_mcorner_r27, project paths replaced by <project>).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sta_mcorner_r27"
MC = "reports/phase3/sta_mcorner_ocv.rpt"


def _project(tmp_path: Path) -> Path:
    assert FIXTURE.is_dir(), f"vendored fixture missing: {FIXTURE}"
    p = tmp_path / "p"
    shutil.copytree(FIXTURE, p)
    return p


def _summary(p: Path, under: str = "phase3/stage3/sta/post_route_timing.rpt"):
    out = p / "out.json"
    subprocess.run([sys.executable, str(PROGRAMS / "sta_report_check.py"), str(p),
                    "--mode", "sta", "--under", under, "--json", str(out)],
                   capture_output=True, text=True)
    d = json.loads(out.read_text())
    return d["summary"], {f["rule"] for f in d["findings"]}


def test_r27s_two_post_route_corners_are_multi_corner_evidence(tmp_path):
    s, rules = _summary(_project(tmp_path))
    assert s["signoff_corners_from_sta_records"] == 2, s
    assert s["multi_corner_executed"] is True
    assert "STA_SINGLE_CORNER_ONLY" not in rules


def test_without_the_report_the_warning_still_fires(tmp_path):
    """Negative control: r27's tree minus the multi-corner report is r27's old answer."""
    p = _project(tmp_path)
    (p / MC).unlink()
    s, rules = _summary(p)
    assert s.get("signoff_corners_from_sta_records", 0) == 0
    assert s["multi_corner_executed"] is False
    # per_corner/ holds PRE_LAYOUT reports, so the stricter basis-mismatch ERROR
    # fires here rather than the warning; either is a refusal of the claim.
    assert rules & {"STA_SINGLE_CORNER_ONLY", "STA_CORNER_BASIS_MISMATCH"}, rules


def test_one_section_is_not_multi_corner(tmp_path):
    p = _project(tmp_path)
    text = (p / MC).read_text()
    (p / MC).write_text(text.split("=== HOLD corner:")[0])
    s, rules = _summary(p)
    assert s["signoff_corners_from_sta_records"] == 1
    assert s["multi_corner_executed"] is False
    assert rules & {"STA_SINGLE_CORNER_ONLY", "STA_CORNER_BASIS_MISMATCH"}, rules


def test_the_same_report_in_two_places_is_counted_once(tmp_path):
    p = _project(tmp_path)
    dup = p / "phase3/stage3/sta/sta_mcorner_ocv.rpt"
    dup.write_text((p / MC).read_text())
    s, _ = _summary(p)
    assert s["signoff_corners_from_sta_records"] == 2


def test_the_count_is_the_shared_readers_answer(tmp_path):
    """No second parser: the summary publishes exactly what
    `_signoff_basis_corners_elsewhere` (the corner-record reader) returns."""
    sys.path.insert(0, str(PROGRAMS))
    import eda_report_audit as E
    p = _project(tmp_path)
    s, _ = _summary(p)
    assert s["signoff_corners_from_sta_records"] == \
        E._signoff_basis_corners_elsewhere(p, "POST_ROUTE") == 2


def test_a_pre_layout_scoped_audit_does_not_read_post_route_sections(tmp_path):
    """Step 10's PRE_LAYOUT audit must not count post-route sections as its own
    evidence or as contradictions of its scope."""
    s, _ = _summary(_project(tmp_path), under="phase3/stage3/sta/pre_pnr_timing.rpt")
    assert s.get("signoff_corners_from_sta_records", 0) == 0
