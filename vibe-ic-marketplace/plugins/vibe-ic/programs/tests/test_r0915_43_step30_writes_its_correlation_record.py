"""R-0915-43 — step 30's declared correlation record exists, ran or refused.

THE GAP: step 30 declares two outputs —

    phase3/stage3/spice/*.sp OR *.spice OR sim_spice/*.sp
    phase3/stage3/spice/correlation.json OR reports/phase3/spice_correlation.json

and a run whose driver RAN produced the first and not the second: the deck and
the log were on disk, the correlation had been computed, and the record the
flow asks for was absent. The numbers existed and nothing published them.

The cause is a path that belongs to no list. `run_installed_pdk_path_correlation`
wrote `reports/phase3/spice_path_correlation.json` — not one of the two declared
outputs, and not one of the three this module's OWN loader
(`_check_spice_correlation_json`) reads. Four names, and the one written was in
neither list.

The other direction matters just as much. MEASURED on sha256 run11: the driver
reached a real conclusion — "Liberty grid tolerance could not be derived", an
implemented-capability failure rather than a capability gap — and the declared
record did not exist. A refusal that is only written under a name nobody
declares is silence to every reader of the declared one.
"""
import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import spice_correlation_check as SCC  # noqa: E402
import _path_layout as _pl  # noqa: E402


# ------------------------------------------------- the record, when it RAN

def test_the_driver_writes_the_path_the_step_declares(tmp_path):
    """One correlation, written to the declared name as well as its own."""
    src = (PROG / "spice_correlation_check.py").read_text()
    # IN THE DRIVER THE RUNNER ACTUALLY CALLS. This module has four
    # correlation drivers and the runner calls exactly one of them for an
    # installed PDK; a write placed in `run_commercial_pdk_path_correlation`
    # would be a fix that never executes on the common path, which is what the
    # first version of this change was until this assertion named the function.
    fn = src.index("def run_installed_pdk_path_correlation")
    nxt = src.index("\ndef ", fn + 10)
    window = src[fn:nxt]
    assert 'declared = _pl.spice_dir(project) / "correlation.json"' in window
    assert "declared.write_text(json.dumps(report" in window
    assert "_scc.run_installed_pdk_path_correlation(" in \
        (PROG / "phase3_one_shot_runner.py").read_text()


def test_it_is_the_same_dict_and_never_a_second_summary(tmp_path):
    """Two documents describing one correlation are two things that can
    disagree, and the one a reader reaches first would decide the step."""
    src = (PROG / "spice_correlation_check.py").read_text()
    fn = src.index("def run_installed_pdk_path_correlation")
    nxt = src.index("\ndef ", fn + 10)
    window = src[fn:nxt]
    # both writes serialise the SAME object
    assert window.count("json.dumps(report, indent=2, ensure_ascii=False)") == 2


def test_the_declared_path_is_the_first_one_this_modules_loader_reads(tmp_path):
    """The record must land where the consumer already looks, not beside it."""
    proj = tmp_path / "p"
    (proj / "phase3/stage3/spice").mkdir(parents=True)
    declared = _pl.spice_dir(proj) / "correlation.json"
    declared.write_text(json.dumps({"correlation": {"verdict": "PASS"}}))
    got = SCC._check_spice_correlation_json(proj)
    assert got is not None and got["correlation"]["verdict"] == "PASS"


def test_the_record_carries_what_was_correlated_the_numbers_and_a_verdict():
    """The ruling's three requirements, pinned against the shape the driver
    actually builds rather than against prose about it."""
    src = (PROG / "spice_correlation_check.py").read_text()
    # Anchor INSIDE run_installed_pdk_path_correlation. `"correlation": {`
    # occurs three times in this module (single-cell, full-path, and the
    # per-arc report); indexing the first one reads a different block and the
    # assertion would then be about code this ruling never touched.
    fn = src.index("def run_installed_pdk_path_correlation")
    i = src.index('"correlation": {', fn)
    # bounded by the block's OWN end, not a guessed byte count: the verdict is
    # the last key in it and a short window silently drops the field this test
    # exists to require.
    window = src[i:src.index('"artifacts": {"deck"', i)]
    for field in ("spice_path_delay_ns", "liberty_spef_cone_delay_ns",
                  "pct_error", '"verdict": verdict'):
        assert field in window, field


# ------------------------------------------- the record, when it REFUSED

def test_the_runner_writes_a_named_refusal_at_the_declared_path():
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    i = src.index('_spice_declared = spice_out / "correlation.json"')
    window = src[i:i + 400]
    assert "_spice_declared.write_text(json.dumps(_payload" in window
    assert "written.append(str(_spice_declared))" in window


def test_the_refusal_and_the_disclosure_are_the_same_payload():
    """One refusal, two names. If they were built separately they could give
    two different reasons for one absence."""
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    i = src.index("spice_skip.write_text(json.dumps(_payload")
    # to the end of the declared-path write, not a fixed byte count: the
    # comment between the two writes is longer than any window worth guessing.
    j = src.index("written.append(str(_spice_declared))", i)
    window = src[i:j]
    assert window.count("json.dumps(_payload, indent=2)") == 2


def test_the_refusal_carries_a_verdict_and_a_reason():
    """A refusal with no reason is silence with a filename."""
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    i = src.index("_spice_cap_flag, _spice_reason = \\")
    window = src[i:i + 700]
    assert '"verdict": "SKIPPED-CONDITION" if _tool_absent else "ERROR"' in window
    assert '"reason": _spice_reason' in window


def test_a_record_the_run_already_produced_is_left_alone():
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    i = src.index('_spice_declared = spice_out / "correlation.json"')
    assert "if not _spice_declared.is_file():" in src[i:i + 200]


def test_the_declared_refusal_is_only_written_when_the_correlation_did_not_run():
    """The refusal branch sits under the `not _spice_have` guard, so a run that
    DID correlate never has a refusal written over its numbers."""
    src = (PROG / "phase3_one_shot_runner.py").read_text()
    guard = src.index("if not _spice_have and not spice_skip.is_file():")
    decl = src.index('_spice_declared = spice_out / "correlation.json"')
    nxt = src.index("# --- Step 32b", guard)
    assert guard < decl < nxt
