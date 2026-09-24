"""A failed sparse-core GPL attempt gets one disclosed, bounded retry."""
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402

from test_phase3_routability_driven_placement import _build  # noqa: E402


def _fake_openroad(deck: str, log_path: Path, *, sparse=True, fail=True):
    """Only the EDA transcript is faked; the real deck builder supplies Tcl."""
    if not fail:
        log_path.write_text("[INFO GPL-0100] placement complete\n")
        return
    area = 10000 if sparse else 100000
    log_path.write_text(
        "[INFO GPL-0015] Region area: 5000000.000 um^2\n"
        f"[INFO GPL-0018] Movable instances area: {area}.000 um^2\n"
        "[INFO GPL-0100] Timing-driven iteration 1/2, virtual: false.\n"
        "[INFO GPL-0107] Timing-driven: repair_design delta area: "
        "700.000 um^2 (+7.00%)\n"
        "[ERROR GPL-0305] RePlAce diverged during gradient descent "
        "calculation, resulting in an invalid step length (Inf or NaN).\n")


def _decision(deck, log):
    # On main this returns a value-level None, so the control is not a missing
    # import/symbol test. The production decision is called on the branch.
    fn = getattr(R, "_sparse_gpl_retry_deck", lambda *_: None)
    return fn(deck, log)


def test_sparse_gpl_divergence_retries_once_with_disclosure(tmp_path):
    deck = _build(util=0.30)
    log = tmp_path / "openroad.log"
    _fake_openroad(deck, log)
    result = _decision(deck, log.read_text())
    actual_deck = result[0] if result is not None else deck
    assert "\nglobal_placement -routability_driven -density 0.3" in actual_deck
    retry, receipt = result
    assert receipt["trigger"] == "GPL-0305"
    assert receipt["natural_density"] == 0.002
    assert receipt["max_retries"] == 1
    assert "-timing_driven" in receipt["first_command"]
    assert "-timing_driven" not in receipt["retry_command"]
    assert "-routability_driven -density 0.3" in receipt["retry_command"]
    assert "GPL_SPARSE_RETRY" in retry
    assert retry.count("\nglobal_placement") == 1
    assert _decision(retry, log.read_text()) is None  # never a third attempt


def test_non_sparse_or_other_failure_does_not_change_placement(tmp_path):
    deck = _build(util=0.30)
    log = tmp_path / "openroad.log"
    _fake_openroad(deck, log, sparse=False)
    assert _decision(deck, log.read_text()) is None
    _fake_openroad(deck, log, fail=False)
    assert _decision(deck, log.read_text()) is None
