"""The PDN's supply current must describe the layout being sized, and the one
EM resize is bounded per DESIGN STATE — not per tree, and not per run.

THE TWO DEFECTS THIS FILE DEFENDS AGAINST
=========================================
`step_pnr` derives the PDN strap-width floor from an EM measurement, but the EM
reports are written by `step_canonicalize_artefacts`, which runs AFTER PnR —
verified statically (enclosing defs) and from two real run logs. So the first
PnR of any run can only ever read a PREVIOUS run's measurement.

`_pdn_em_first_pass_resize` closes that gap inside a run by re-measuring and
re-dispatching PnR once. It was bounded by a sentinel written into the project
tree that NOTHING ever removes, so the bound retired the corrector for the life
of the tree. MEASURED on spm run23: a sentinel dated 2026-09-23 18:50 left two
later runs with no corrector, and the current the PDN was sized from drifted
2.900e-03 -> 2.600e-03 -> 2.860e-03 with the design unchanged. The control that
proves it is not the code: the SAME commit re-run on the drifted tree
reproduced the drifted number.

WHY THE KEY IS THE DESIGN STATE. Three candidate bounds, and only one holds
both ends:
  * per TREE (original)  — corrector retires for ever.               Defect.
  * per RUN (first fix)  — a run that crashes after the second PnR and resumes
                           in the same tree sees a foreign sentinel and can buy
                           a THIRD PnR, breaking the stated contract.
  * per DESIGN STATE     — the synthesis netlist is stable across the passes of
                           one run (so a resume buys nothing) and changes when
                           the design does (so the corrector is never retired).

WHY NOT mtime, AND WHY NOT `measured_subject.def_sha256`. "Written during this
run" is not identity: a same-build re-run that cache-hits the PnR DEF holds a
measurement of exactly this layout, and refusing it by clock discards an
accurate number and republishes the sizing record as NOT_DERIVED for a DEF that
WAS drawn with the floor. And `_pdn_em_measured_subject` digests the DEF at
READ time, so comparing it to the DEF on disk compares the file with itself and
is true always — a check that cannot fail is not a check. The subject digest is
therefore RECORDED BY THE PRODUCER, and a measurement carrying none is declined.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
from _ppa import power as PW  # noqa: E402


def _design(project: Path, body: bytes = b"module spm; endmodule\n") -> Path:
    d = project / "phase2" / "stage2" / "synth"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spm_synth.v").write_bytes(body)
    return project


def _layout(project: Path, body: bytes = b"DESIGN chip_top ;\n") -> str:
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / PW._PDN_EM_SUBJECT_DEF).write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _em_json(project: Path, subject: object, current: float = 0.00286) -> Path:
    rpt3 = R._pl.reports_phase3_dir(project)
    rpt3.mkdir(parents=True, exist_ok=True)
    doc = {"max_segment_current_A": current, "segments_analysed": 34778}
    if subject is not None:
        doc["subject_def_sha256"] = subject
    (rpt3 / "em.json").write_text(json.dumps(doc))
    return rpt3


# ---------------------------------------------------------------------------
# the bound is per DESIGN STATE
# ---------------------------------------------------------------------------
def _sentinel(project: Path, payload) -> Path:
    pnr = R._pl.pnr_dir(project)
    pnr.mkdir(parents=True, exist_ok=True)
    s = pnr / R._PDN_EM_RESIZE_SENTINEL
    s.write_text(payload if isinstance(payload, str)
                 else json.dumps(payload) + "\n")
    return s


def test_this_designs_own_sentinel_binds(tmp_path):
    """THE CONTRACT. One resize per design state — a resume in the same tree,
    or any later pass over the same design, buys nothing."""
    proj = _design(tmp_path)
    s = _sentinel(proj, {"reason": "pdn_em_first_pass_resize",
                         "design": PW._pdn_em_design_state(proj), "short": []})
    binds, why = PW._pdn_em_sentinel_binds(s, proj)
    assert binds is True, why


def test_a_sentinel_spent_on_another_design_does_not_bind(tmp_path):
    """THE DEFECT. A sentinel nothing removes retired the corrector for the
    life of the tree, whatever the design became."""
    proj = _design(tmp_path)
    s = _sentinel(proj, {"reason": "pdn_em_first_pass_resize",
                         "design": "0" * 64, "short": []})
    binds, why = PW._pdn_em_sentinel_binds(s, proj)
    assert binds is False
    assert "another design state" in why


def test_the_design_state_actually_tracks_the_design(tmp_path):
    """A key that never changes is a tree key wearing a different name."""
    proj = _design(tmp_path, b"module spm; endmodule\n")
    before = PW._pdn_em_design_state(proj)
    _design(proj, b"module spm; wire w; endmodule\n")
    after = PW._pdn_em_design_state(proj)
    assert before and after and before != after


def test_the_design_state_is_stable_across_passes_of_one_run(tmp_path):
    """The half that keeps a crash-resume from buying a third PnR: PnR does not
    rewrite the synthesis netlist, so the key does not move between passes."""
    proj = _design(tmp_path)
    first = PW._pdn_em_design_state(proj)
    _layout(proj, b"DESIGN chip_top ; # pass 2 wrote a new DEF\n")
    assert PW._pdn_em_design_state(proj) == first


def test_a_legacy_sentinel_naming_no_design_does_not_bind(tmp_path):
    """Migration, and it is the case run23 was found in: every sentinel already
    on disk predates this rule. Reading it as binding would leave those trees
    permanently un-corrected."""
    proj = _design(tmp_path)
    s = _sentinel(proj, {"reason": "pdn_em_first_pass_resize", "short": []})
    binds, why = PW._pdn_em_sentinel_binds(s, proj)
    assert binds is False
    assert "names no design state" in why


def test_an_unreadable_sentinel_does_not_bind(tmp_path):
    proj = _design(tmp_path)
    binds, why = PW._pdn_em_sentinel_binds(_sentinel(proj, "{ not json"), proj)
    assert binds is False and "could not be read" in why


def test_no_sentinel_does_not_bind(tmp_path):
    proj = _design(tmp_path)
    binds, _ = PW._pdn_em_sentinel_binds(R._pl.pnr_dir(proj) / "absent", proj)
    assert binds is False


def test_the_writer_records_the_design_it_spent_the_resize_on():
    """A sentinel that does not name its design cannot bound one design rather
    than the tree, so the write site is part of the contract."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.find('"reason": "pdn_em_first_pass_resize"')
    assert i > 0, "the sentinel write site moved; this test must follow it"
    assert '"design": _ppa_power._pdn_em_design_state(project)' in src[i:i + 500], (
        "the sentinel is written without a design tag, so it will bound every "
        "future run in this tree")


# ---------------------------------------------------------------------------
# the measurement must describe the layout in front of us
# ---------------------------------------------------------------------------
def test_a_measurement_of_this_layout_is_accepted(tmp_path):
    """THE PAIRED HALF, and the one that keeps this from being a ban: a
    same-build re-run that cache-hits the DEF holds an ACCURATE measurement of
    exactly this layout and must keep using it."""
    proj = tmp_path / "p"
    sha = _layout(proj)
    rpt3 = _em_json(proj, sha)
    ok, why = PW._pdn_em_measures_this_layout(rpt3, proj)
    assert ok is True, why


def test_a_measurement_of_a_different_layout_is_declined(tmp_path):
    proj = tmp_path / "p"
    _layout(proj)
    rpt3 = _em_json(proj, "f" * 64)
    ok, why = PW._pdn_em_measures_this_layout(rpt3, proj)
    assert ok is False
    assert "different design state" in why


def test_a_measurement_recording_no_subject_is_declined(tmp_path):
    """The legacy artefact. It cannot be SHOWN to measure this layout, and the
    stale-basis case is exactly what that looks like."""
    proj = tmp_path / "p"
    _layout(proj)
    rpt3 = _em_json(proj, None)
    ok, why = PW._pdn_em_measures_this_layout(rpt3, proj)
    assert ok is False
    assert "records no subject_def_sha256" in why


def test_the_subject_check_is_not_vacuous(tmp_path):
    """THE TRAP THIS FILE EXISTS FOR. `_pdn_em_measured_subject` digests the
    DEF at READ time; a check built on it compares the file with itself and
    passes always. Prove ours moves when only the LAYOUT changes."""
    proj = tmp_path / "p"
    sha = _layout(proj)
    rpt3 = _em_json(proj, sha)
    assert PW._pdn_em_measures_this_layout(rpt3, proj)[0] is True
    _layout(proj, b"DESIGN chip_top ; # re-routed\n")     # measurement unchanged
    assert PW._pdn_em_measures_this_layout(rpt3, proj)[0] is False, (
        "the subject check did not notice the layout changing under a fixed "
        "measurement — it is comparing the DEF with itself")


def test_the_producer_records_the_subject_at_measurement_time():
    """The consumer can only check a digest the producer actually wrote."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert ('"subject_def_sha256": _ppa_power._pdn_em_subject_digest(project)'
            in src), (
        "em.json is emitted without a recorded subject, so every subject "
        "check downstream is unfalsifiable")


# ---------------------------------------------------------------------------
# the design-declared fallback, and the disclosure
# ---------------------------------------------------------------------------
class _Pdk:
    def __init__(self, liberty=None):
        self.liberty = liberty
        self.tech_lef = None


def _l19(project: Path, fields: dict) -> Path:
    d = project / "phase1" / "generated_docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({"fields": fields}))
    return project


def test_a_budget_uses_the_pdk_nominal_voltage(tmp_path):
    """THE RUNG MUST BE REACHABLE. The flow-produced L19 carries
    `fields.power_budget_uw` and NO voltage field at all (measured on run23),
    so a rung needing a declared voltage could never be climbed."""
    proj = _l19(tmp_path / "p", {"power_budget_uw": 5000.0})
    i, src, gap = PW._pdn_em_declared_current(proj, 5.0)
    assert i == pytest.approx(5000.0e-6 / 5.0)
    assert "nom_voltage" in src and gap == ""


def test_a_declared_voltage_overrides_the_pdk(tmp_path):
    proj = _l19(tmp_path / "p", {"power_budget_uw": 5000.0,
                                 "supply_voltage_v": 1.8})
    i, _src, _gap = PW._pdn_em_declared_current(proj, 5.0)
    assert i == pytest.approx(5000.0e-6 / 1.8)


def test_a_null_budget_says_so_precisely(tmp_path):
    """spm declares the FIELD and leaves it null: the design has not answered,
    and that must not become a fabricated number."""
    proj = _l19(tmp_path / "p", {"power_budget_uw": None})
    i, _s, gap = PW._pdn_em_declared_current(proj, 5.0)
    assert i is None
    assert "declares no power_budget_uw" in gap


def test_a_budget_with_no_voltage_anywhere_says_THAT_instead(tmp_path):
    """The two gaps are different facts. Reporting 'no budget declared' when a
    budget IS declared sends the reader to the wrong file."""
    proj = _l19(tmp_path / "p", {"power_budget_uw": 5000.0})
    i, _s, gap = PW._pdn_em_declared_current(proj, None)
    assert i is None
    assert "no supply voltage" in gap and "5000.0 uW" in gap


def test_a_stale_basis_derives_no_floor_and_discloses_the_precise_gap(
        tmp_path, capsys, monkeypatch):
    """END TO END. A project holding only a previous layout's measurement must
    derive no floor from it and must SAY so — silence made 'needs no floor' and
    'could not derive one' the same observable."""
    proj = _l19(tmp_path / "p", {"power_budget_uw": None})
    _design(proj)
    _layout(proj)
    _em_json(proj, "f" * 64)                       # measures another layout
    monkeypatch.setattr(R, "_pdk_nominal_voltage", lambda pdk, c=None: 5.0)

    out = R._pdn_em_width_floor(proj, _Pdk(), None)

    assert out is None, "a previous layout's measurement sized this grid"
    err = capsys.readouterr().err
    assert "PDN_EM_FLOOR_NOT_DERIVED" in err, err
    assert "different design state" in err, err
    assert "declares no power_budget_uw" in err, err

    rec = json.loads((R._pl.reports_phase3_dir(proj)
                      / "pdn_em_sizing.json").read_text())
    assert rec["derived"] is False
    assert "power_budget_uw" in rec["declared_budget_gap"]
    assert any("em.json" in s for s in rec["declined_stale_sources"])
