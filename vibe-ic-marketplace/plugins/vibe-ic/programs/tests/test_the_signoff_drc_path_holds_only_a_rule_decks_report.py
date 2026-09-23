"""The sign-off DRC path holds ONLY a report a rule deck produced from the layout.

MEASURED (icsub5, read-only): subservient run2's `reports/phase3/drc_signoff.rpt`
opens

    # Sign-off DRC report (ORGANIC-20260531 Step 31 alias).
    # Source: phase3/stage3/pnr/routed.drc.rpt
    # Tool: openroad

-- the ROUTER's routability DRC published under the sign-off name. On spm run23
the same path is written by the KLayout rule deck (a report database naming its
deck) and step 31 passes. Two writers filled the path on EXISTENCE, never on
PRODUCER: `step_canonicalize_artefacts` fell back to the router projection when
no deck had run, and `step_drc` mirrored whatever report it had.

Both now ask the question the step-31 detector already asks, from the report's
own bytes (`_signoff_drc_format.classify_file(...).is_signoff_deck`). With no
deck report the sign-off path is ABSENT and the router report stays at its own
path under its own name. The detector and step 31's gate are untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
sys.path.insert(0, str(PROGRAMS / "tests"))

import _signoff_drc_format as SDF                           # noqa: E402
import test_postlayout_lec_nameerror as H                   # noqa: E402

R = H.R
CANON = "reports/phase3/drc_signoff.rpt"
DECK_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <description>DRC Run Report at widget</description>
 <generator>drc: script='/pdk/libs.tech/klayout/drc/signoff.drc'</generator>
 <top-cell>widget</top-cell>
 <categories/>
 <cells/>
 <items/>
</report-database>
"""
NO_DECK_RDB = DECK_REPORT.replace(
    " <generator>drc: script='/pdk/libs.tech/klayout/drc/signoff.drc'</generator>\n",
    "")
ROUTER_LOG = ("[INFO DRT-0180] Post processing.\n"
              "[INFO DRT-0199]   Number of violations = 0.\n"
              "[INFO DRT-0198] Complete detail routing.\n")


def _canonicalize(tmp_path, monkeypatch, *, deck_report=None, canon=None):
    H._quiet_canonicalize(monkeypatch)
    monkeypatch.setattr(R, "_emit_lec_post_layout", lambda *a, **k: "SKIP")
    project = H._canonicalize_project(tmp_path)
    pnr = project / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / "openroad.log").write_text(ROUTER_LOG)
    if deck_report is not None:
        d = project / "phase3" / "reports" / "drc.rpt"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(deck_report)
    if canon is not None:
        c = project / CANON
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_bytes(canon)
    R.step_canonicalize_artefacts(
        project, H.TOP,
        H._pdk(str(tmp_path / "x.lib"), str(tmp_path / "x.lef")),
        "nocontainer")
    return project


# ── run2's shape: RED on main ───────────────────────────────────────────────

def test_with_no_deck_the_signoff_path_is_absent(tmp_path, monkeypatch):
    project = _canonicalize(tmp_path, monkeypatch)
    assert not (project / CANON).exists(), (project / CANON).read_text()[:300]


def test_the_router_report_stays_at_its_own_path(tmp_path, monkeypatch):
    project = _canonicalize(tmp_path, monkeypatch)
    own = project / "phase3" / "stage3" / "pnr" / "routed.drc.rpt"
    assert own.is_file()
    assert SDF.classify_file(own).kind == SDF.OPENROAD


def test_a_router_report_an_earlier_run_published_is_cleared(
        tmp_path, monkeypatch):
    stale = ("# Sign-off DRC report (ORGANIC-20260531 Step 31 alias).\n"
             "# Source: phase3/stage3/pnr/routed.drc.rpt\n# Tool: openroad\n#\n"
             "openroad / drt-pass: detailed_route invoked\n"
             "[INFO DRT-0199]   Number of violations = 0.\n")
    project = _canonicalize(tmp_path, monkeypatch, canon=stale.encode())
    assert not (project / CANON).exists()


def test_a_report_database_naming_no_deck_is_not_published(
        tmp_path, monkeypatch):
    project = _canonicalize(tmp_path, monkeypatch, deck_report=NO_DECK_RDB)
    assert not (project / CANON).exists()


def test_step_drc_mirrors_only_a_decks_report(tmp_path):
    """Writer two: `step_drc`'s mirror, read at the source it runs."""
    import inspect
    src = inspect.getsource(R.step_drc)
    i = src.index('_canon = project / "reports" / "phase3" / "drc_signoff.rpt"')
    window = src[i:i + 900]
    assert "_sdf.classify_file(rpt)" in window
    assert "is_signoff_deck" in window
    assert window.index("is_signoff_deck") < window.index(
        "_canon.write_bytes(rpt.read_bytes())")


# ── spm is unaffected ──────────────────────────────────────────────────────

def test_spm_the_decks_report_already_at_the_path_is_left_byte_identical(
        tmp_path, monkeypatch):
    """run23: `step_drc` put the deck's report at the sign-off path."""
    project = _canonicalize(tmp_path, monkeypatch, deck_report=DECK_REPORT,
                            canon=DECK_REPORT.encode())
    assert (project / CANON).read_bytes() == DECK_REPORT.encode()
    assert SDF.classify_file(project / CANON).is_signoff_deck


def test_a_decks_report_is_still_published_when_the_path_is_empty(
        tmp_path, monkeypatch):
    project = _canonicalize(tmp_path, monkeypatch, deck_report=DECK_REPORT)
    c = project / CANON
    assert c.is_file()
    assert SDF.classify_file(c).is_signoff_deck
    assert c.read_text().endswith(DECK_REPORT)


def test_a_decks_report_replaces_a_router_report_left_at_the_path(
        tmp_path, monkeypatch):
    stale = b"# Tool: openroad\nopenroad / drt-pass: detailed_route invoked\n"
    project = _canonicalize(tmp_path, monkeypatch, deck_report=DECK_REPORT,
                            canon=stale)
    assert SDF.classify_file(project / CANON).is_signoff_deck
