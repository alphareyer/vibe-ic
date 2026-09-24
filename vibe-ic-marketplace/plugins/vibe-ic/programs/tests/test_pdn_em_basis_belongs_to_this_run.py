"""The PDN's supply current must belong to the run that is sizing the grid.

WHAT THIS FILE DEFENDS, AND WHY EACH ARM IS HERE
================================================
`step_pnr` derives the PDN strap-width floor from an EM measurement. The EM
reports are written by `step_canonicalize_artefacts`, which runs AFTER PnR —
verified statically (enclosing defs) and from two real run logs, where `pnr`
precedes `canonicalize_artefacts` in both. So the first PnR of any run can only
ever read a PREVIOUS run's measurement.

`_pdn_em_first_pass_resize` exists to close that gap inside one run, bounded by
a sentinel. The sentinel was written into the project tree and NOTHING removed
it, so the bound retired the corrector for the life of the tree.

MEASURED on spm run23 (2026-09-24): a sentinel dated 2026-09-23 18:50 meant
neither the 00:46 nor the 09:27 run had the corrector, and the I_total the PDN
was sized from drifted 2.900e-03 -> 2.600e-03 -> 2.860e-03 across three runs
with the design unchanged. The control that proves it is code-independent: the
SAME plugin commit re-run on the drifted tree reproduced the drifted number.

Each arm below is paired: the refusal must bite, AND the legitimate case must
still work, because a fix that only refuses would silently disable a feature
that does real work (the run23 sentinel recorded a Metal4 shortfall of 2.74x).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402


# ---------------------------------------------------------------------------
# the sentinel bounds ONE RUN, not the tree
# ---------------------------------------------------------------------------
def _sentinel(tmp_path: Path, payload) -> Path:
    s = tmp_path / R._PDN_EM_RESIZE_SENTINEL
    s.write_text(payload if isinstance(payload, str)
                 else json.dumps(payload) + "\n")
    return s


def test_a_previous_runs_sentinel_does_not_bind_this_run(tmp_path):
    """THE DEFECT. A sentinel another run wrote used to end the corrector at
    its first statement, for every future run in that tree."""
    s = _sentinel(tmp_path, {"reason": "pdn_em_first_pass_resize",
                             "run": "phase3-999999-1000000000000",
                             "short": []})
    binds, why = R._pdn_em_sentinel_binds(s)
    assert binds is False, why
    assert "another run" in why


def test_this_runs_own_sentinel_still_binds(tmp_path):
    """THE PAIRED HALF. The bound must survive: one resize per run, still."""
    s = _sentinel(tmp_path, {"reason": "pdn_em_first_pass_resize",
                             "run": R._pdn_em_this_run_tag(), "short": []})
    binds, why = R._pdn_em_sentinel_binds(s)
    assert binds is True, why


def test_a_sentinel_with_no_run_tag_is_read_as_a_previous_runs(tmp_path):
    """Migration: every sentinel already on disk predates the tag. Treating it
    as this run's would leave existing trees permanently un-corrected — the
    exact state run23 was found in."""
    s = _sentinel(tmp_path, {"reason": "pdn_em_first_pass_resize", "short": []})
    binds, why = R._pdn_em_sentinel_binds(s)
    assert binds is False
    assert "names no run" in why


def test_an_unreadable_sentinel_does_not_bind(tmp_path):
    """Absent evidence is not evidence that this run spent its resize."""
    s = _sentinel(tmp_path, "{ not json")
    binds, why = R._pdn_em_sentinel_binds(s)
    assert binds is False
    assert "could not be read" in why


def test_no_sentinel_at_all_does_not_bind(tmp_path):
    binds, _why = R._pdn_em_sentinel_binds(tmp_path / "absent")
    assert binds is False


def test_the_writer_records_which_run_spent_the_resize():
    """A sentinel that does not name its run cannot bound one run rather than
    the tree, so the write site is part of the contract, not an ornament."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.find('"reason": "pdn_em_first_pass_resize"')
    assert i > 0, "the sentinel write site moved; this test must follow it"
    window = src[i:i + 400]
    assert '"run": _pdn_em_this_run_tag()' in window, (
        "the sentinel is written without a run tag, so every future run in "
        "this tree will read it as its own bound")


# ---------------------------------------------------------------------------
# a measurement is evidence only for the run that took it
# ---------------------------------------------------------------------------
def test_a_file_from_before_this_run_is_not_this_runs(tmp_path):
    old = tmp_path / "em.json"
    old.write_text("{}")
    import os
    os.utime(old, (R._RUN_STARTED_AT - 600, R._RUN_STARTED_AT - 600))
    assert R._pdn_em_is_this_runs(old) is False


def test_a_file_written_now_is_this_runs(tmp_path):
    fresh = tmp_path / "em.json"
    fresh.write_text("{}")
    assert R._pdn_em_is_this_runs(fresh) is True


def test_an_unreadable_path_is_not_this_runs(tmp_path):
    assert R._pdn_em_is_this_runs(tmp_path / "nope") is False


# ---------------------------------------------------------------------------
# the design's declared budget, and the disclosure when nothing is available
# ---------------------------------------------------------------------------
def _project_with_l19(tmp_path: Path, fields: dict) -> Path:
    d = tmp_path / "proj" / "phase1" / "generated_docs"
    d.mkdir(parents=True)
    (d / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps(fields))
    return tmp_path / "proj"


def test_a_declared_power_budget_becomes_a_current(tmp_path):
    proj = _project_with_l19(tmp_path, {"power_budget_uw": 5000.0,
                                        "supply_voltage_v": 5.0})
    i, src = R._pdn_em_declared_current(proj)
    assert i == pytest.approx(5000.0e-6 / 5.0)
    assert "power_budget_uw" in src


def test_a_null_budget_is_not_a_current(tmp_path):
    """spm declares the FIELD and leaves it null. That is the design not having
    answered, and it must not become a fabricated number."""
    proj = _project_with_l19(tmp_path, {"power_budget_uw": None,
                                        "supply_voltage_v": 5.0})
    assert R._pdn_em_declared_current(proj) == (None, None)


def test_a_budget_without_a_voltage_is_not_a_current(tmp_path):
    proj = _project_with_l19(tmp_path, {"power_budget_uw": 5000.0})
    assert R._pdn_em_declared_current(proj) == (None, None)


def test_a_stale_measurement_is_declined_and_the_refusal_is_disclosed(
        tmp_path, capsys):
    """END TO END on the decision that matters: a project carrying ONLY a
    previous run's EM reports must derive no floor from them, and must SAY so.

    Silence was the defect: `return None` made "this design needs no floor" and
    "this run could not derive one" the same observable.
    """
    import os
    proj = _project_with_l19(tmp_path, {"power_budget_uw": None})
    rpt3 = proj / "reports" / "phase3"
    rpt3.mkdir(parents=True)
    for name, body in (("em.json", {"max_segment_current_A": 0.00286,
                                    "segments_analysed": 34778}),
                       ("em_current_authority.json",
                        {"supply_authority": [{"supply_current_A": 0.0029}]})):
        p = rpt3 / name
        p.write_text(json.dumps(body))
        os.utime(p, (R._RUN_STARTED_AT - 600, R._RUN_STARTED_AT - 600))

    out = R._pdn_em_width_floor(proj, pdk=None, container=None)

    assert out is None, "a previous run's measurement sized this run's grid"
    err = capsys.readouterr().err
    assert "PDN_EM_FLOOR_NOT_DERIVED" in err, err
    assert "em.json" in err and "em_current_authority.json" in err, err

    rec = json.loads((rpt3 / "pdn_em_sizing.json").read_text())
    assert rec["derived"] is False
    assert rec["code"] == "PDN_EM_FLOOR_NOT_DERIVED"
    assert any("em.json" in s for s in rec["declined_stale_sources"])


def test_a_measurement_from_this_run_is_NOT_declined(tmp_path, capsys):
    """THE PAIRED HALF, and the one that keeps the fix from being a ban.

    With a fresh `em.json` the basis IS accepted, so the function must get PAST
    basis selection. It still returns None here — there is no tech LEF in this
    fixture — but it must not do so for lack of a current, and it must not
    emit the refusal.
    """
    proj = _project_with_l19(tmp_path, {"power_budget_uw": None})
    rpt3 = proj / "reports" / "phase3"
    rpt3.mkdir(parents=True)
    (rpt3 / "em.json").write_text(json.dumps(
        {"max_segment_current_A": 0.00286, "segments_analysed": 34778}))

    R._pdn_em_width_floor(proj, pdk=None, container=None)

    err = capsys.readouterr().err
    assert "PDN_EM_FLOOR_NOT_DERIVED" not in err, (
        "this run's OWN measurement was refused — the fix has become a ban "
        f"on the feature it was meant to keep honest\n{err}")
