"""vibe-ic#2081 — a Phase-3 sign-off STA violation that no physical remedy can
close must REFUSE, name its residual, and route to the step that can act.

Every fixture below is a REDUCTION of the real reports of the run in #2081
(sha256 x sky130A, lane rbsha2) or of its own pre-layout per-corner control.
The numbers are that run's; the geometry of each path is preserved.
"""
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

mod = importlib.import_module("sta_architectural_residual_check")

PLUGIN = Path(__file__).resolve().parents[2]

# ── fixtures ────────────────────────────────────────────────────────────────

# The architectural path: 39 arcs, ONE buffer (0.55 ns), 1.03 ns of adverse
# clock skew, slack -2.46. PRUB 1.58 < 2.46 -> residual +0.88 ns.
_ARCH = """=== SETUP corner: process=SS liberty=lib_ss ===
STA_BASIS: POST_ROUTE_SPEF
Startpoint: _17630_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _17661_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

    Cap    Slew   Delay    Time   Description
                   6.33    6.33   clock network delay (propagated)
   0.05    0.38    1.06    7.39 ^ _17630_/Q (cell__dfxtp_2)
{ARCS}
   0.01    0.15    0.55   99.00 v rebuffer1/X (cell__buf_1)
                  25.90   25.90   clock clk (rise edge)
                   5.30   31.20   clock network delay (propagated)
                          -2.46   slack (VIOLATED)
"""
_ARCH = _ARCH.replace(
    "{ARCS}",
    "\n".join(f"   0.04    0.20    0.68   {10 + i:5.2f} v _1{i:04d}_/X "
              f"(cell__maj3_4)" for i in range(37)))

# The DRIVE-LIMITED control, from the run's own pre-layout per-corner report:
# 4 arcs, no buffers, slack -125.54, and ONE arc carries 94.70 of 151.17 ns.
# The bare bound calls this architectural; it is not — placement and buffering
# DID fix it (-125.54 -> -2.53 on the routed netlist).
_DRIVE = """=== SETUP corner: process=SS liberty=lib_ss ===
STA_BASIS: POST_ROUTE_SPEF
Startpoint: _18239_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _18269_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

  Delay    Time   Description
   0.00    0.00   clock network delay (propagated)
   2.36    2.36 v _18239_/Q (cell__dfxtp_1)
  47.39   49.75 ^ _08229_/Y (cell__o21ai_0)
  94.70  144.45 v _08263_/Y (cell__nor2_1)
   6.72  151.17 ^ _08266_/Y (cell__o21ai_0)
   0.00    0.00   clock network delay (propagated)
        -125.54   slack (VIOLATED)
"""

# A HOLD path. Different physics, different remedy; this bound says nothing
# about it and must not judge it.
_HOLD = _ARCH.replace("Path Type: max", "Path Type: min")

# A path whose violation IS inside the physical bound: same shape, but 3.00 ns
# of buffers against a 2.46 ns violation.
_PHYS = _ARCH.replace("   0.01    0.15    0.55   99.00 v rebuffer1/X "
                      "(cell__buf_1)",
                      "   0.01    0.15    3.00   99.00 v rebuffer1/X "
                      "(cell__buf_1)")

_MET = _ARCH.replace("-2.46   slack (VIOLATED)", "1.20   slack (MET)")


def _project(tmp_path: Path, body: str, name="sta_mcorner_ocv.rpt") -> Path:
    d = tmp_path / "phase3" / "stage3" / "sta"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def _run(project: Path, tmp_path: Path):
    out = tmp_path / "r.json"
    rc = mod.main([str(project), "--json", str(out)])
    return rc, json.loads(out.read_text())


# ── the subject ─────────────────────────────────────────────────────────────

def test_architectural_violation_refuses(tmp_path):
    rc, rep = _run(_project(tmp_path, _ARCH), tmp_path)
    assert rc == 1, rep["reason"]
    assert rep["verdict"] == "FAIL"
    assert len(rep["architectural_paths"]) == 1
    p = rep["architectural_paths"][0]
    assert p["category"] == "architectural"
    assert p["corner"] == "SS"
    assert p["residual_ns"] == pytest.approx(0.88, abs=0.01)
    assert p["physical_recovery_upper_bound_ns"] == pytest.approx(1.58,
                                                                 abs=0.01)


def test_refusal_names_the_step_that_can_act(tmp_path):
    """The point of the gate: not a number, a ROUTE."""
    rc, rep = _run(_project(tmp_path, _ARCH), tmp_path)
    assert rc == 1
    assert rep["route_step"] == mod.ROUTE_STEP
    assert rep["route_skill"] == mod.ROUTE_SKILL
    assert mod.ROUTE_STEP_NAME in rep["reason"]
    assert mod.ROUTE_SKILL in rep["reason"]
    # the residual is named, never waived
    assert "never waived" in rep["reason"]


def test_reason_is_readable_under_both_spellings(tmp_path):
    """#2081's own first defect: a reader that knows only `reasons` saw the
    verdict word and nothing else."""
    _, rep = _run(_project(tmp_path, _ARCH), tmp_path)
    assert rep["reason"] and isinstance(rep["reasons"], list)
    assert rep["reasons"][0] == rep["reason"]


# ── the guards, each with the measurement that forced it ────────────────────

def test_drive_limited_path_is_not_architectural(tmp_path):
    """One arc carrying 63% of the data delay is a drive/fanout problem."""
    rc, rep = _run(_project(tmp_path, _DRIVE), tmp_path)
    assert rc == 0, rep["reason"]
    assert [p["category"] for p in rep["paths"]] == ["drive_limited"]


def test_pre_layout_basis_is_not_judged(tmp_path):
    """Before place, buffer and repair the tool has not yet had its chance."""
    body = _DRIVE.replace("POST_ROUTE_SPEF", "PRE_LAYOUT_ESTIMATE")
    rc, rep = _run(_project(tmp_path, body), tmp_path)
    assert rc == 2 and rep["verdict"] == "NOT_CHECKED"
    assert rep["reports_skipped"][0]["declared_basis"] == "PRE_LAYOUT"


def test_hold_path_is_not_judged(tmp_path):
    rc, rep = _run(_project(tmp_path, _HOLD), tmp_path)
    assert rc == 0 and rep["paths"] == []


def test_physically_reachable_violation_passes(tmp_path):
    rc, rep = _run(_project(tmp_path, _PHYS), tmp_path)
    assert rc == 0
    assert [p["category"] for p in rep["paths"]] == ["physical_reachable"]
    assert "one-sided" in rep["reason"]


def test_met_design_passes_with_no_paths(tmp_path):
    rc, rep = _run(_project(tmp_path, _MET), tmp_path)
    assert rc == 0 and rep["paths"] == []


def test_empty_project_is_not_checked(tmp_path):
    rc, rep = _run(tmp_path, tmp_path)
    assert rc == 2 and rep["verdict"] == "NOT_CHECKED"


def test_the_same_report_in_two_places_is_counted_once(tmp_path):
    """MEASURED on #2081's own run: the emitters write each report to two
    locations, and counting both reported one architectural path as two."""
    proj = _project(tmp_path, _ARCH)
    dup = proj / "reports" / "phase3"
    dup.mkdir(parents=True)
    (dup / "sta_mcorner_ocv.rpt").write_text(_ARCH, encoding="utf-8")
    rc, rep = _run(proj, tmp_path)
    assert rc == 1
    assert len(rep["architectural_paths"]) == 1, rep["architectural_paths"]
    assert len(rep["reports_read"]) == 1


def test_it_offers_no_way_to_turn_the_refusal_into_a_pass(tmp_path):
    """The trap named in #2081: the cheap wrong answer is to relax something so
    the number goes green. This gate must expose no such lever — no baseline to
    write, no flag to waive with, no threshold on its command line."""
    proj = _project(tmp_path, _ARCH)
    for lever in ("--write-baseline", "--fix", "--waive", "--baseline",
                  "--allow", "--threshold", "--max-arc-fraction"):
        r = subprocess.run(
            [sys.executable,
             str(PLUGIN / "programs" / "sta_architectural_residual_check.py"),
             str(proj), lever],
            capture_output=True, text=True)
        assert r.returncode == 2 and "unrecognized arguments" in r.stderr, (
            f"{lever} is accepted: {r.returncode} {r.stderr[-200:]}")
    # and the argparse surface really is just the project and the report
    assert mod.main([str(proj)]) == 1


# ── the wiring: a gate nobody runs is the defect this issue is about ────────

def test_the_gate_is_wired_into_the_signoff_step():
    flow = yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s["id"]) == "23")
    blob = json.dumps(step["gate"])
    assert "sta_architectural_residual_check" in blob, (
        "step 23 (post-route STA sign-off) does not run the gate")


def test_the_step_declares_the_report_it_produces():
    flow = yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())
    step = next(s for s in flow["steps"] if str(s["id"]) == "23")
    assert any("architectural_residual" in o
               for o in step["required_outputs"])


def test_capture_routing_sends_the_finding_to_authoring():
    """#2081's structural half: `phase3.sta` routed to the STA runner and the
    sta-review skill — both physical. The architectural case must route to the
    step that can remove a combinational depth."""
    routing = json.loads(
        (PLUGIN / "benchmark" / "CAPTURE_ROUTING.json").read_text())
    entry = routing["steps"].get("phase3.sta.architectural_residual")
    assert entry, "no routing entry for the architectural-residual finding"
    assert entry["bucket_A_program"] == \
        "programs/sta_architectural_residual_check.py"
    assert entry["bucket_B_skill_file"] == \
        f"skills/{mod.ROUTE_SKILL}/SKILL.md"


def test_runs_as_a_subprocess_like_the_gate_does(tmp_path):
    proj = _project(tmp_path, _ARCH)
    r = subprocess.run(
        [sys.executable,
         str(PLUGIN / "programs" / "sta_architectural_residual_check.py"),
         str(proj), "--json", str(tmp_path / "s.json")],
        capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Spec-to-RTL" in r.stdout


# ── the corner label is prose, and its polarity is asked (vibe-ic#712) ───────
#
# `prose_polarity_consulted_check` REFUSED this program's landing on
# `analyse_report`, and the refusal was right. MEASURED before the fix, on the
# fixture below: `corner` came back as the string
# "Corner SS: this corner is NOT a sign-off corner" — a denying sentence
# published as a declared field and quoted back in the refusal as the corner
# this gate had judged. That is #706's shape one field over: `_PROCESS_RE`
# matches only when the emitter writes a `process=` token, and the fallback
# publishes the banner's own WORDS.
#
# The pair below is bidirectional. `test_a_denying_corner_banner...` FAILS
# against the pre-fix function (it asserted the sentence into `corner`), and
# `test_an_ordinary_corner_banner...` passes against BOTH — so the fix is not
# free to answer by blanking every label it reads.

_DENIED_BANNER = _ARCH.replace(
    "=== SETUP corner: process=SS liberty=lib_ss ===",
    "=== SETUP corner SS: this corner is NOT a sign-off corner, retained "
    "for reference only ===")


def test_a_denying_corner_banner_is_not_published_as_the_corner(tmp_path):
    rc, rep = _run(_project(tmp_path, _DENIED_BANNER), tmp_path)
    assert rc == 1, rep["reason"]                      # the finding is UNTOUCHED
    p = rep["architectural_paths"][0]
    assert p["corner"] is None
    assert p["corner_label_denied"] == "NOT"
    # the evidence survives: a reader can see what was withheld and why
    assert "NOT a sign-off corner" in p["corner_banner"]
    assert "denies its own label" in rep["reason"]


def test_an_ordinary_corner_banner_still_names_its_corner(tmp_path):
    """The other direction: asking polarity must not cost the label."""
    rc, rep = _run(_project(tmp_path, _ARCH), tmp_path)
    assert rc == 1
    p = rep["architectural_paths"][0]
    assert p["corner"] == "SS"
    # `.get` deliberately: this control must be runnable against the PRE-FIX
    # function, which had no such key, so that its passing there means the fix
    # did not buy its answer by blanking every label it reads.
    assert p.get("corner_label_denied") is None
    assert "'SS'" in rep["reason"]


def test_analyse_report_consults_the_one_polarity_vocabulary(tmp_path):
    """Not a private copy of the word list — the shared module, which is the
    whole point of #712 (`_FOUNDRY_NEGATION_RE` and `_DIE_NEGATION_RE` were
    two copies of it, and they drifted)."""
    import _prose_polarity
    assert mod.is_denied is _prose_polarity.is_denied
