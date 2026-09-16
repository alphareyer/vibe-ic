"""ORGANIC #590 — step_lvs proceeded when step_pnr FAILed mid-tcl and
labelled the inevitable checkpoint mismatch a "design/extraction defect"
(hundreds of `(no pin, node is ...)` against an otherwise clean
checkpoint — the final DEF/pin-label stages were simply never written).

Fix: step_lvs takes `upstream_pnr`; a non-PASS pnr outcome SKIPs with
finding LVS_UPSTREAM_PNR_INCOMPLETE naming the pnr failure. The
orchestrator hands it the last pnr StepResult from the plan.
"""
import inspect
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import phase3_one_shot_runner as R  # noqa: E402


def _pdk() -> "R.PdkConfig":
    return R.PdkConfig(
        name="fixture_pdk",
        liberty="/pdk/lib.lib", tech_lef="/pdk/tech.lef",
        cell_lef="/pdk/cells.lef", cell_gds=None,
        site="unithd", drc_deck=None, metal_prefix="met",
    )


def test_lvs_skips_on_upstream_pnr_fail(tmp_path):
    """The issue's exact 現象: pnr FAIL (mid-tcl death) → lvs must SKIP
    naming the upstream failure, never the design-defect wording."""
    pnr_fail = R.StepResult(
        "pnr", "FAIL", 12.3,
        "ROUTE_NOT_CONVERGED: detailed route completed with 297 "
        "violations remaining (final DRT-0199).")
    r = R.step_lvs(tmp_path, "chip_top", _pdk(), "",
                   upstream_pnr=pnr_fail)
    assert r.status == "NOT_MEASURED"
    assert r.reason_class == "upstream_failed"
    assert r.extras["finding"] == "LVS_UPSTREAM_PNR_INCOMPLETE"
    assert "ROUTE_NOT_CONVERGED" in r.detail
    assert "design/extraction defect" not in r.detail.split("not a")[0]


def test_lvs_skips_on_upstream_pnr_timeout(tmp_path):
    """A KILLED pnr is a FAIL, and the ruling on timeouts is why.

    `TIMEOUT` was a status of its own. R-0915-85 does not give it one, and the
    owner's standing ruling on timeouts says why that is right: a run that was
    stopped is SUPERVISED, never relabelled into a softer tier. The step
    failed; that it failed by being killed at 3600 s is in its DETAIL, which is
    what this test reads.
    """
    pnr_to = R.StepResult("pnr", "FAIL", 3600.0, "rc=124 killed after 3600s")
    r = R.step_lvs(tmp_path, "chip_top", _pdk(), "",
                   upstream_pnr=pnr_to)
    assert r.status == "NOT_MEASURED"
    assert r.reason_class == "upstream_failed"
    assert r.extras["upstream_pnr_status"] == "FAIL"
    assert "rc=124 killed" in r.detail


def test_lvs_proceeds_on_upstream_pnr_pass(tmp_path):
    """NEGATIVE: pnr PASS → the gate must NOT trip; lvs proceeds into
    its normal flow (here: fails later on missing inputs, which is the
    pre-existing behaviour — anything but the upstream-SKIP)."""
    pnr_ok = R.StepResult("pnr", "PASS", 100.0, "def=chip_top.def")
    r = R.step_lvs(tmp_path, "chip_top", _pdk(), "",
                   upstream_pnr=pnr_ok)
    assert r.extras.get("finding") != "LVS_UPSTREAM_PNR_INCOMPLETE"


def test_lvs_proceeds_without_upstream_info(tmp_path):
    """Standalone invocation (no plan context) keeps the old behaviour."""
    r = R.step_lvs(tmp_path, "chip_top", _pdk(), "")
    assert r.extras.get("finding") != "LVS_UPSTREAM_PNR_INCOMPLETE"


def test_orchestrator_hands_pnr_result_to_lvs():
    src = inspect.getsource(R.main)
    assert "upstream_pnr=_pnr_result" in src
