"""A refused power net makes the IR number SMALLER — it must not pass the budget.

MEASURED on u_hawaii_adc (PDK ihp-sg13g2, `_uhadc_r16/proj_d`, tree read
2026-09-02 13:22, nothing re-run for this test):

    reports/phase3/ir_drop.json
        "verdict": "FAIL"
        "power_nets":           ["IOVDD", "VDD", "vhi", "vldo"]
        "nets_analysed":        ["VDD", "vhi", "vldo"]
        "nets_analysis_failed": ["IOVDD"]
        "worst_ir_uv": 208.0   "budget_uv": 120000.0
        "verdict_basis": "analyze_power_grid failed on IOVDD — the worst-case IR
                          reported is the worst of the nets that SUCCEEDED and
                          is not a statement about the design"

    reports/phase3/ir_drop_signoff.json
        "passed": true   "ir_within_budget": true

`_check_ir_drop` opened and parsed that very file and read only `worst_ir_uv`
and `budget_uv` from it. `analyze_power_grid` runs once per power net inside a
Tcl `catch`, so a REFUSED net emits no `IR drop` line at all: the worst case
becomes the worst of the nets that SUCCEEDED. The failure makes the number
smaller and the budget likelier to pass — an INVERTED signal, which is why
reading the number without the coverage turns a partial analysis into a clean
pass.

The repair records NOT_MEASURED. It deliberately does NOT flip the verdict to
"over budget": the population that claim would be made over is the incomplete
one. `ir_within_budget` becomes None, `ir_coverage_complete` False, and the
refused nets are named in the summary so the next reader does not have to
re-derive them.

Chip-AGNOSTIC: keyed only on the producer's own generic fields
(`nets_analysis_failed`, `verdict`), no PDK, vendor or design literal.
"""
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / 'eda_report_audit.py'
sys.path.insert(0, str(SCRIPT.parent))
import eda_report_audit as era  # noqa: E402

_PAD = "# " + ("=" * 78 + "\n") * 40


def _psm_report(project):
    (project / "reports" / "phase3").mkdir(parents=True, exist_ok=True)
    rpt = project / "ir_drop.rpt"
    rpt.write_text(
        "OpenROAD PSM IR-drop analysis\n"
        "power grid mesh nodes: 12458\n"
        "max IR drop: 15 mV drop on VDD rail\n"
        "worst voltage drop 0.5% Vdd\nstatic IR: 12 mV\ndynamic IR: 15 mV\n"
        + _PAD)
    return project / "reports" / "phase3" / "ir_drop.json"


def _write(jp, **over):
    base = {"tool": "openroad-psm", "mode": "static_ir_drop",
            "power_nets": ["IOVDD", "VDD"], "nets_analysed": ["VDD"],
            "worst_ir_uv": 208.0, "budget_uv": 120000.0}
    base.update(over)
    jp.write_text(json.dumps(base))


def test_a_refused_net_is_not_a_budget_pass(tmp_path):
    jp = _psm_report(tmp_path)
    _write(jp, nets_analysis_failed=["IOVDD"], verdict="FAIL")
    r = era._check_ir_drop(tmp_path)
    assert r.passed is False, r.summary
    assert r.summary["ir_coverage_complete"] is False
    assert r.summary["ir_within_budget"] is None, (
        "NOT_MEASURED, not True and not False — the design is not shown to be "
        "over budget either")
    assert r.summary["ir_nets_analysis_failed"] == ["IOVDD"]
    assert any(f.rule == "IR_COVERAGE_INCOMPLETE" for f in r.findings)


def test_a_producer_fail_verdict_alone_is_enough(tmp_path):
    """Even with no named net, the producer's own FAIL is not a pass."""
    jp = _psm_report(tmp_path)
    _write(jp, verdict="FAIL")
    r = era._check_ir_drop(tmp_path)
    assert r.passed is False, r.summary
    assert r.summary["ir_coverage_complete"] is False
    assert r.summary["ir_within_budget"] is None


def test_complete_coverage_still_passes_and_still_reports_the_number(tmp_path):
    """The repair must not refuse a run that DID analyse every net."""
    jp = _psm_report(tmp_path)
    _write(jp, nets_analysis_failed=[], verdict="PASS",
           nets_analysed=["IOVDD", "VDD"])
    r = era._check_ir_drop(tmp_path)
    assert r.passed is True, r.summary
    assert r.summary["ir_coverage_complete"] is True
    assert r.summary["ir_within_budget"] is True
    assert r.summary["worst_ir_uv"] == 208.0


def test_over_budget_is_still_over_budget_when_coverage_is_complete(tmp_path):
    """#444's comparison is untouched on a complete run."""
    jp = _psm_report(tmp_path)
    _write(jp, verdict="PASS", worst_ir_uv=200000.0, budget_uv=120000.0)
    r = era._check_ir_drop(tmp_path)
    assert r.passed is False
    assert r.summary["ir_within_budget"] is False
    assert any(f.rule == "IR_OVER_BUDGET" for f in r.findings)
