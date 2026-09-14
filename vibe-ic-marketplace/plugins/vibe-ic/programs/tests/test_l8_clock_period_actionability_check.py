#!/usr/bin/env python3
"""Negative-control smoke tests for l8_clock_period_actionability_check.py.

EVERY fixture here is SYNTHESIZED neutral data. No real design's files
are copied, and no design name / PDK name / vendor part number / pin
literal from any real project appears. Ports are ``clk_a`` / ``clk_b``,
the "design" is ``fixture_core``.

The point of a negative control: a test that cannot fail proves nothing.
Each rule is asserted in BOTH directions —
  * gutted / mis-formed layer  => the gate FAILS (exit 1, named rule)
  * well-formed layer          => the gate PASSES (exit 0, no findings)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent / \
    "l8_clock_period_actionability_check.py"
sys.path.insert(0, str(PROG.parent))
import sdc_gen  # noqa: E402


def _run(project: Path, *extra: str):
    rep = project / "rep.json"
    cmd = [sys.executable, str(PROG), str(project), "--json", str(rep), *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    report = json.loads(rep.read_text()) if rep.is_file() else {}
    return proc, report


def _rules(report: dict) -> set[str]:
    return {f["rule"] for f in report.get("findings", [])}


def _write_l8(project: Path, doc: dict, stem: str = "L8_TIMING_WAVEFORM"):
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / f"{stem}.json").write_text(json.dumps(doc, indent=1))


def _clock(name: str, mhz: float, primary: bool = True, **extra) -> dict:
    rec = {
        "name": name,
        "source_pin": name,
        "freq_mhz": mhz,
        "freq_hz": int(mhz * 1e6),
        "period_ns": 1000.0 / mhz,
        "domain_kind": "primary" if primary else "derived",
        "role": "master" if primary else "derived",
    }
    rec.update(extra)
    return rec


# ─────────────────────────── POSITIVE CONTROLS ───────────────────────────

def test_wellformed_single_clock_passes(tmp_path):
    """A single unambiguous clock => PASS. If this ever fails, the gate is
    over-firing and must be narrowed."""
    _write_l8(tmp_path, {
        "doc_class": "timing_waveform",
        "ic_name": "fixture_core",
        "clock_domains": [_clock("clk_a", 100.0)],
        "timing_constants": [
            {"name": "T_SETUP", "value": 1.2, "unit": "ns"},
            {"name": "T_HOLD", "value": 0.4, "unit": "ns"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["passed"] is True
    assert report["findings"] == []


def test_wellformed_two_distinct_ports_passes(tmp_path):
    """Two genuinely different clock PORTS at different rates is a normal
    multi-domain design, NOT an ambiguity => PASS."""
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0),
                          _clock("clk_b", 25.0, primary=False)],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["findings"] == []


def test_same_port_same_period_passes(tmp_path):
    """Duplicate records that AGREE are redundant, not ambiguous => PASS."""
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 100.0)],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["findings"] == []


def test_demoted_duplicate_passes(tmp_path):
    """The documented fix path: keep exactly one record in the consumer's
    preferred tier and demote the rest => PASS. Proves the gate tells the
    author something they can actually act on."""
    _write_l8(tmp_path, {
        "clock_domains": [
            _clock("clk_a", 100.0, primary=True),
            _clock("clk_a", 50.0, primary=False),
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["findings"] == []


def test_ticks_with_declared_clock_passes(tmp_path):
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0)],
        "timing_constants": [
            {"name": "FRAME_END_GAP_TICKS", "value": 2500, "unit": "ticks"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["findings"] == []


# ══ THE NOT_MEASURED TIER ═════════════════════════════════════════════════
#
# 9320b0269 ("Fix scoped L8 clock consumer contracts") closed a real hole: an
# absent L8 used to announce `VACUOUS_PASS:` and read as a skip that costs
# nothing. It closed it with severity `ERROR` -> rc 1, and that contradicted
# the finding's OWN sentence, "this is NOT_MEASURED, not a vacuous pass".
#
# rc 1 is a live finding. This gate is wired `advisory_program_exit_zero` at
# step D1 but declares no `ENFORCEMENT:` in its docstring, so
# `flow_compliance_check._gate_is_two_source_advisory` reads it BLOCKING and
# the refusal took D1 down — MEASURED as three reds on main 2f230524b
# (`test_issue1446_incomplete_in_scope_is_not_green`, both arms).
#
# NOT_MEASURED has its own tier and it is neither of the two: `skip_kind`
# `zero-denominator` -> `_flow_reason_taxonomy.ZERO_DENOMINATOR` -> the
# `INCOMPLETE` set -> `DISCLOSED_INCOMPLETE`. Reported, printed, typed,
# carried — and NEVER promoted, because ZERO_DENOMINATOR is deliberately
# absent from `SKIP_ELIGIBLE`. These cases pin BOTH halves: the tier is not a
# FAIL, and it is not a pass.

def test_no_l8_is_not_measured_and_is_neither_pass_nor_fail(tmp_path):
    (tmp_path / "phase1" / "generated_docs").mkdir(parents=True)
    proc, report = _run(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert report["passed"] is False, report
    assert report["verdict"] == "INCOMPLETE", report
    assert report["reason_class"] == "ZERO_DENOMINATOR", report
    assert report["summary"]["skip_kind"] == "zero-denominator", report
    # The sentinel that promotes an rc 2 toward a skip reading as a pass must
    # NOT be printed. This is the half 9320b0269 was right about.
    assert "VACUOUS_PASS" not in (proc.stdout + proc.stderr), proc.stdout


def test_l8_without_clock_or_tick_is_not_measured_either(tmp_path):
    """The second empty-denominator door: the document exists and declares
    nothing this gate dereferences. Same tier, for the same reason."""
    _write_l8(tmp_path, {"note": "no clock_domains, no timing_constants"})
    proc, report = _run(tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert report["passed"] is False, report
    assert report["reason_class"] == "ZERO_DENOMINATOR", report
    assert "VACUOUS_PASS" not in (proc.stdout + proc.stderr), proc.stdout


def test_the_flow_books_the_empty_denominator_as_incomplete_not_a_pass(
        tmp_path):
    """THE CONTROL THAT DECIDES IT, asked of the consumer rather than of the
    gate's own rc: `flow_compliance_check` must type this gate's rc 2 as
    INCOMPLETE. If the `zero-denominator` token stops reaching
    `_flow_reason_taxonomy`, the reason falls through to a SKIP_ELIGIBLE class
    and the rc 2 is promoted to VACUOUS_PASS — the exact vacuous pass this
    gate exists to refuse — and this case goes red naming it."""
    import importlib.util
    fcc_path = PROG.parent / "flow_compliance_check.py"
    spec = importlib.util.spec_from_file_location("fcc_l8_tier", fcc_path)
    fcc = importlib.util.module_from_spec(spec)
    sys.modules["fcc_l8_tier"] = fcc
    spec.loader.exec_module(fcc)

    (tmp_path / "phase1" / "generated_docs").mkdir(parents=True)
    res = fcc._check_program_exit_zero(
        tmp_path, "l8_clock_period_actionability_check .")

    assert res.exit_code == 2, res
    assert res.verdict == "INCOMPLETE", res
    assert res.reason_class == "ZERO_DENOMINATOR", res
    assert res.verdict not in {"PASS", "VACUOUS_PASS", "NOT_APPLICABLE"}, res


def test_a_populated_l8_that_contradicts_itself_still_fails(tmp_path):
    """THE OTHER DIRECTION — the negative control for the tier above. A
    denominator that is NOT empty and whose contents disagree is a live
    finding and must keep rc 1. A fix that reached this case would have
    turned every real L8 contradiction into a disclosure."""
    _write_l8(tmp_path, {"clock_domains": [
        _clock("clk_a", 100.0, primary=True),
        _clock("clk_a", 50.0, primary=True),
    ]})
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert report["passed"] is False, report
    assert report["verdict"] == "FAIL", report
    assert report["reason_class"] is None, report
    assert report["summary"].get("skip_kind") != "zero-denominator", report


def _write_target(project: Path, value: str) -> None:
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({
        "fields": {"pdk_target": value},
    }))


def test_scoped_clock_selects_only_matching_l19_target(tmp_path):
    _write_target(tmp_path, "process_family_a")
    _write_l8(tmp_path, {"clock_domains": [
        _clock("clk_a", 100.0, pdk_scoped_target="process_family_a"),
        _clock("clk_a", 50.0, pdk_scoped_target="process_family_b"),
    ]})
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["summary"]["target_scope"] == "process_family_a"
    assert report["findings"] == []


def test_scoped_clock_rejects_nonmatching_l19_target(tmp_path):
    _write_target(tmp_path, "process_family_b")
    _write_l8(tmp_path, {"clock_domains": [
        _clock("clk_a", 100.0, pdk_scoped_target="process_family_a"),
    ]})
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_CLOCK_SCOPE_MISMATCH" in _rules(report)


def test_backend_consumer_selects_scope_before_unscoped_scalar():
    l8 = {"clock_mhz": 50.0, "clock_domains": [
        _clock("clk_a", 100.0, pdk_scoped_target="process_family_a"),
        _clock("clk_a", 25.0, pdk_scoped_target="process_family_b"),
    ]}
    assert sdc_gen._clock_mhz_from_l8_domains(l8, "process_family_a") == 100.0
    assert sdc_gen._clock_mhz_from_l8_domains(l8, "process_family_b") == 25.0
    assert sdc_gen._clock_mhz_from_l8_domains(l8, "process_family_c") is None


# ───────────────────────── NEGATIVE CONTROLS ─────────────────────────────

def test_NEGATIVE_same_port_two_periods_fails(tmp_path):
    """GUTTED LAYER: the per-target qualifier is dropped, leaving two
    records that bind the SAME port to different periods and are
    indistinguishable to the consumer. This is the measured real-world
    defect; the gate MUST fail."""
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 50.0)],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert report["passed"] is False
    assert "L8_CLOCK_PERIOD_AMBIGUOUS" in _rules(report)
    msg = next(f["message"] for f in report["findings"]
               if f["rule"] == "L8_CLOCK_PERIOD_AMBIGUOUS")
    # The message must name BOTH periods and say order decides.
    assert "10.0ns" in msg and "20.0ns" in msg
    assert "LIST ORDER" in msg
    # No discriminator present => the fix is layer-side; say so.
    assert "NO discriminator key" in msg


def test_NEGATIVE_scalar_present_downgrades_to_contradiction_warn(tmp_path):
    """SWEEP-DRIVEN NARROWING: when a top-level scalar short-circuits the
    record walk the backend IS deterministic, so claiming 'order decides'
    would be FALSE. The contradiction is still reported — as a WARN that
    does not block — and the message must state the deterministic value."""
    _write_l8(tmp_path, {
        "clock_mhz": 100.0,
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 50.0)],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert report["passed"] is True                    # no ERROR
    assert _rules(report) == {"L8_CLOCK_PERIOD_CONTRADICTED"}
    msg = report["findings"][0]["message"]
    assert "deterministic" in msg
    assert "10.0ns" in msg and "20.0ns" in msg
    assert report["findings"][0]["severity"] == "WARN"


def test_NEGATIVE_discriminator_present_still_fails_but_says_so(tmp_path):
    """Even WITH a discriminator the consumer has no selector, so it still
    fails — but the message must route the fix to the consumer side."""
    _write_l8(tmp_path, {
        "clock_domains": [
            _clock("clk_a", 100.0, pdk="fixture_pdk_alpha"),
            _clock("clk_a", 50.0, pdk="fixture_pdk_beta"),
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_CLOCK_PERIOD_AMBIGUOUS" in _rules(report)
    msg = next(f["message"] for f in report["findings"]
               if f["rule"] == "L8_CLOCK_PERIOD_AMBIGUOUS")
    assert "DO carry discriminator" in msg


def test_records_without_frequency_pass_when_scalar_clock_mhz_resolves(tmp_path):
    """SWEEP-DRIVEN NARROWING: the consumer reads a top-level scalar
    ``clock_mhz`` BEFORE it walks the records, so nothing is fabricated
    when that scalar resolves. Firing here would be a false positive."""
    _write_l8(tmp_path, {
        "clock_mhz": 100.0,
        "clock_domains": [
            {"name": "clk_a", "source_pin": "clk_a", "domain_kind": "primary"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "L8_CLOCK_PERIOD_UNRESOLVABLE" not in _rules(report)


def test_NEGATIVE_no_resolvable_frequency_fails(tmp_path):
    """GUTTED LAYER: clock records with the frequency stripped out. The
    consumer falls through to a hard-coded default and FABRICATES a
    period."""
    _write_l8(tmp_path, {
        "clock_domains": [
            {"name": "clk_a", "source_pin": "clk_a", "domain_kind": "primary"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_CLOCK_PERIOD_UNRESOLVABLE" in _rules(report)


def test_NEGATIVE_ticks_without_any_clock_fails(tmp_path):
    """GUTTED LAYER: tick-denominated constants with the clock removed —
    no consumer can convert them to microseconds."""
    _write_l8(tmp_path, {
        "timing_constants": [
            {"name": "FRAME_END_GAP_TICKS", "value": 2500, "unit": "ticks"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_TICK_CONSTANT_UNRESOLVABLE" in _rules(report)


def test_NEGATIVE_ticks_naming_undeclared_clock_fails(tmp_path):
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0)],
        "timing_constants": [
            {"name": "TURNAROUND_TICKS", "value": 60, "unit": "cycles",
             "clock": "clk_missing"},
        ],
    })
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_TICK_CONSTANT_UNRESOLVABLE" in _rules(report)


def test_NEGATIVE_ambiguity_spans_both_l8_faces(tmp_path):
    """The two contradicting records may live in DIFFERENT L8 files —
    consumers read whichever is present, so the gate must see both."""
    _write_l8(tmp_path, {"clock_domains": [_clock("clk_a", 100.0)]},
              stem="L8_TIMING_WAVEFORM")
    _write_l8(tmp_path, {"clock_domains": [_clock("clk_a", 40.0)]},
              stem="L8_RTL_CONSTANTS")
    proc, report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "L8_CLOCK_PERIOD_AMBIGUOUS" in _rules(report)


# ───────────────────── BLOCK / ADVISE + ESCAPE HATCHES ───────────────────

def test_advise_flag_downgrades_exit_code_but_keeps_findings(tmp_path):
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 50.0)],
    })
    proc, report = _run(tmp_path, "--advise")
    assert proc.returncode == 0, proc.stdout
    assert report["blocks"] is False
    assert report["passed"] is False
    assert "L8_CLOCK_PERIOD_AMBIGUOUS" in _rules(report)


def test_default_run_declares_that_it_blocks(tmp_path):
    _write_l8(tmp_path, {"clock_domains": [_clock("clk_a", 100.0)]})
    _proc, report = _run(tmp_path)
    assert report["blocks"] is True


def test_tolerance_flag_suppresses_negligible_spread(tmp_path):
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 99.5)],
    })
    proc, _report = _run(tmp_path, "--tol-pct", "5.0")
    assert proc.returncode == 0, proc.stdout


def test_waiver_suppresses_ambiguity(tmp_path):
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 50.0)],
    })
    (tmp_path / "waivers.json").write_text(json.dumps({"waivers": [{
        "id": "l8_clock_period_ambiguity_override",
        "rationale": "synthesized fixture: both records are intentionally "
                     "retained for a documented dual-target bring-up flow",
    }]}))
    proc, report = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "L8_CLOCK_PERIOD_AMBIGUOUS" not in _rules(report)


def test_short_waiver_rationale_does_not_suppress(tmp_path):
    """A waiver must cost something to write, or it is a rubber stamp."""
    _write_l8(tmp_path, {
        "clock_domains": [_clock("clk_a", 100.0), _clock("clk_a", 50.0)],
    })
    (tmp_path / "waivers.json").write_text(json.dumps({"waivers": [{
        "id": "l8_clock_period_ambiguity_override", "rationale": "ok",
    }]}))
    proc, _report = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
