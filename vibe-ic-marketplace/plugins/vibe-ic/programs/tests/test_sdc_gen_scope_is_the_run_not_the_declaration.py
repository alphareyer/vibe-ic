"""A PDK-scoped L8 clock must be selected by the PDK THIS RUN targets, not by
the design's primary DECLARATION.

MEASURED 2026-09-15, lane icspm, on live main ``9320b02697c6`` running the
canonical front door on a design whose L1 declares TWO target processes::

    $ vibe_ic_one_shot_runner.py <project> --pdk gf180mcuD --ic-name spm
    FAIL  sdc_gen  rc=1 err=FAIL: L8_CLOCK_SCOPE_MISMATCH:
                                  no scoped L8 clock matches L19 target 'sky130'
    overall verdict : FAIL     halted at : phase2

Nothing about that design is wrong, and Phase 1 got it right twice:

  * ``L8_RTL_CONSTANTS.clock_domains[0]`` = ``{"pdk_scoped_target": "gf180mcuD",
    "period_ns": 24.0}``, stamped by ``phase1_doc_one_shot_runner`` from the
    CLI ``--pdk`` (``d["pdk_scoped_target"] = _CLI_PDK``) off the design's own
    GF180MCU clock row;
  * ``reports/phase1/submission_template_fetch.json`` = ``{"pdk": "gf180mcuD",
    "pdk_source": "--pdk"}`` — flow step 0.5ic's record of what this run targets.

``sdc_gen`` then asked a THIRD place: ``L19.fields.pdk_target``. That field is the
DESIGN'S DECLARATION — its primary declared family — and the emitter says so in
its own words (``phase1_doc_one_shot_runner`` at the ``pdk_target_alternates``
write): "a design may declare MORE THAN ONE target process, and ``pdk_target``
is one scalar … phase3's declared-vs-resolved guard then REFUSED an entire run
on <B> — a process the design names, in the same breath, on the same row."
``tapeout_precheck`` already honours ``pdk_target_alternates`` for exactly this.
``sdc_gen`` read the scalar, got the design's PRIMARY family, and refused a run
that targets the secondary one.

TWO INDEPENDENT DEFECTS, and each of these tests isolates one:

  D1  WRONG SOURCE OF TRUTH — the run's PDK, not the design's declaration,
      decides which scoped clock applies.
  D2  EXACT STRING EQUALITY ACROSS TWO NAMING REGISTERS — L8 scopes to the PDK
      name (``gf180mcuD``) while L19's ``pdk_target``/``pdk_target_alternates``
      hold FAMILY names (``gf180mcu``). ``'gf180mcu' != 'gf180mcud'``, so the
      match failed even when both sides meant the same process. The repo already
      owns the matcher for this: ``pdk_family_identity.same_family``.

D2 is not a corollary of D1: handing over the family name alone still fails.

WHAT MUST NOT CHANGE — ``test_a_foreign_scope_is_still_refused`` and
``test_an_unscoped_clock_is_untouched`` are the anti-weakening controls. The
whole point of the scoped branch is that a backend must never close timing
against a period this design never asked for on this process, and that refusal
is preserved verbatim.

chip-AGNOSTIC: the program change names no PDK, design, vendor or IC. The open
PDK spellings below appear in the TEST only, as two spellings of one family and
one spelling of another — the same practice as ``test_issue2136_area_signoff_baseline``
and ``test_r7_surface_residue_def_units_and_second_declared_target``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import sdc_gen as G  # noqa: E402

#: L8 scopes to the PDK name; L19 and its alternates hold family names.
_RUN_PDK = "gf180mcuD"
_RUN_FAMILY = "gf180mcu"
_OTHER_FAMILY = "sky130"
_OTHER_PDK = "sky130A"

#: The design's own scoped row: 24 ns on the process this run targets.
_SCOPED_PERIOD_NS = 24.0
_SCOPED_MHZ = 1000.0 / _SCOPED_PERIOD_NS


def _l8_scoped(scope: str) -> dict:
    """L8 exactly as phase 1 emits it for a PDK-scoped clock row."""
    return {
        "clock_mhz": _SCOPED_MHZ,
        "clock_domains": [{
            "name": "clk", "source_pin": "clk",
            "domain_kind": "primary", "role": "primary",
            "freq_mhz": _SCOPED_MHZ, "period_ns": _SCOPED_PERIOD_NS,
            "extraction_strategy": "clock_domain_pdk_scoped_row",
            "pdk_scoped_target": scope,
        }],
    }


def _project(tmp_path: Path, *, l8: dict,
             l19_target: str | None = None,
             l19_alternates: list[str] | None = None,
             run_pdk: str | None = None) -> Path:
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L8_RTL_CONSTANTS.json").write_text(json.dumps(l8))
    (gd / "L9_INTEGRATION_SPEC.json").write_text(json.dumps({
        "top_module": "chip_top",
        "top_module_pins": [
            {"name": "clk", "mode": "input"},
            {"name": "rst_n", "mode": "input"},
            {"name": "dout", "mode": "output"},
        ],
    }))
    if l19_target is not None:
        fields: dict = {"pdk_target": l19_target}
        if l19_alternates is not None:
            fields["pdk_target_alternates"] = l19_alternates
        (gd / "L19_CONSTRAINTS_PDK.json").write_text(
            json.dumps({"fields": fields}))
    if run_pdk is not None:
        rep = tmp_path / "reports" / "phase1"
        rep.mkdir(parents=True, exist_ok=True)
        (rep / "submission_template_fetch.json").write_text(json.dumps(
            {"pdk": run_pdk, "pdk_source": "--pdk"}))
    rtl = tmp_path / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    (rtl / "chip_top.v").write_text(
        "module chip_top(input wire clk, input wire rst_n,\n"
        "                output reg dout);\n"
        "  always @(posedge clk or negedge rst_n)\n"
        "    if (!rst_n) dout <= 1'b0; else dout <= 1'b1;\n"
        "endmodule\n"
    )
    return tmp_path


def _emitted_sdc(project: Path) -> str:
    files = list((project / "phase2" / "stage1" / "fpga").glob("*.sdc"))
    assert len(files) == 1, files
    return files[0].read_text()


# ── D1: the RUN decides, not the DECLARATION ────────────────────────────────

def test_run_pdk_beats_the_l19_declaration(tmp_path):
    """THE MEASURED FAILURE, reduced. L19 declares the OTHER family primary;
    step 0.5ic recorded that this run targets the scoped one. Pre-fix this is
    rc=1 L8_CLOCK_SCOPE_MISMATCH; the design's own 24 ns row is right there."""
    proj = _project(
        tmp_path, l8=_l8_scoped(_RUN_PDK),
        l19_target=_OTHER_FAMILY,
        l19_alternates=[_OTHER_FAMILY, _RUN_FAMILY],
        run_pdk=_RUN_PDK,
    )
    assert G.main([str(proj), "--force"]) == 0
    assert f"-period {_SCOPED_PERIOD_NS:g} " in _emitted_sdc(proj)


# ── D2: family spelling is not a mismatch ───────────────────────────────────

def test_family_spelling_is_not_a_mismatch(tmp_path):
    """The run records the FAMILY spelling while L8 scopes to the PDK
    spelling. Same process, two registers. Pre-fix: rc=1, because the match
    was `target.lower() in {scope.lower()}` and 'gf180mcu' != 'gf180mcud'.
    This fails pre-fix EVEN IF D1 is fixed, which is why it is its own test."""
    proj = _project(
        tmp_path, l8=_l8_scoped(_RUN_PDK),
        l19_target=_OTHER_FAMILY,
        run_pdk=_RUN_FAMILY,
    )
    assert G.main([str(proj), "--force"]) == 0
    assert f"-period {_SCOPED_PERIOD_NS:g} " in _emitted_sdc(proj)


def test_family_spelling_is_not_a_mismatch_on_the_declaration_path_too(tmp_path):
    """No run record at all: the L19 DECLARATION supplies the family spelling
    and must still match the PDK-spelled scope. Isolates D2 from D1 entirely —
    this project has no `submission_template_fetch.json` to read."""
    proj = _project(
        tmp_path, l8=_l8_scoped(_RUN_PDK),
        l19_target=_RUN_FAMILY,
    )
    assert G.main([str(proj), "--force"]) == 0
    assert f"-period {_SCOPED_PERIOD_NS:g} " in _emitted_sdc(proj)


# ── anti-weakening controls: the refusal the branch exists for ──────────────

def test_a_foreign_scope_is_still_refused(tmp_path):
    """MUST FAIL, before and after. The run targets a process the design
    states NO clock row for; the only scoped row belongs to another family.
    Closing timing against it is exactly what the scoped branch forbids.
    (Green on both arms by construction — it is the control that proves the
    fix did not turn the gate off, not evidence that the fix works.)"""
    proj = _project(
        tmp_path, l8=_l8_scoped(_RUN_PDK),
        l19_target=_RUN_FAMILY,
        run_pdk=_OTHER_PDK,
    )
    assert G.main([str(proj), "--force"]) == 1


def test_a_scoped_clock_with_no_target_at_all_is_not_measured(tmp_path):
    """No run record and no L19: the branch must say NOT_MEASURED rather than
    guess a scope. Unchanged behaviour; control against a fix that invents a
    target when it cannot find one."""
    proj = _project(tmp_path, l8=_l8_scoped(_RUN_PDK))
    assert G.main([str(proj), "--force"]) == 1


def test_an_unscoped_clock_is_untouched(tmp_path):
    """A design that scopes nothing never enters the branch, with or without a
    run record. Control against a fix that widens the branch's reach."""
    l8 = {"clock_mhz": None, "clock_domains": [
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "master", "freq_mhz": 125.0, "period_ns": 8.0}]}
    proj = _project(tmp_path, l8=l8, run_pdk=_RUN_PDK)
    assert G.main([str(proj), "--force"]) == 0
    assert "-period 8 " in _emitted_sdc(proj)


# ── the constant this program borrows must not drift ────────────────────────

def test_run_record_path_is_the_producers_own_constant():
    """`sdc_gen` reads the report `submission_template_fetch` writes. The path
    is stated ONCE, by the producer; this asserts the consumer did not retype
    a copy that can drift."""
    import submission_template_fetch as F  # noqa: PLC0415
    assert G._RUN_PDK_REPORT_REL == F.REPORT_REL


# ── outcome 2: an unscoped design-owned record applies to every process ─────

def test_unscoped_design_owned_record_is_used_when_no_scope_matches(tmp_path):
    """R-0915-6 outcome (2). The design states a clock for ANOTHER process AND
    a clock that names no process at all. A record that names no process
    applies to every process, so it is used rather than refused — and the
    disclosure names it, so nothing passes silently."""
    l8 = {"clock_mhz": None, "clock_domains": [
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": _SCOPED_MHZ,
         "period_ns": _SCOPED_PERIOD_NS, "pdk_scoped_target": _RUN_PDK},
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": 125.0, "period_ns": 8.0},
    ]}
    proj = _project(tmp_path, l8=l8, run_pdk=_OTHER_PDK)
    assert G.main([str(proj), "--force"]) == 0
    assert "-period 8 " in _emitted_sdc(proj)


def test_unscoped_selection_is_disclosed_not_silent(tmp_path, capsys):
    """Outcome 2 must SAY it fell back. A reader who cannot see which record
    was used cannot tell outcome 1 from outcome 2."""
    l8 = {"clock_mhz": None, "clock_domains": [
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": _SCOPED_MHZ,
         "period_ns": _SCOPED_PERIOD_NS, "pdk_scoped_target": _RUN_PDK},
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": 125.0, "period_ns": 8.0},
    ]}
    proj = _project(tmp_path, l8=l8, run_pdk=_OTHER_PDK)
    assert G.main([str(proj), "--force"]) == 0
    assert "unscoped_design_owned" in capsys.readouterr().out


def test_scoped_match_wins_over_an_unscoped_record(tmp_path):
    """Outcome 1 beats outcome 2: when the run's own process IS named, the
    unscoped record must not displace it."""
    l8 = {"clock_mhz": None, "clock_domains": [
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": _SCOPED_MHZ,
         "period_ns": _SCOPED_PERIOD_NS, "pdk_scoped_target": _RUN_PDK},
        {"name": "clk", "source_pin": "clk", "domain_kind": "primary",
         "role": "primary", "freq_mhz": 125.0, "period_ns": 8.0},
    ]}
    proj = _project(tmp_path, l8=l8, run_pdk=_RUN_PDK)
    assert G.main([str(proj), "--force"]) == 0
    assert f"-period {_SCOPED_PERIOD_NS:g} " in _emitted_sdc(proj)


# ── the matcher must not select on a shared WORD ────────────────────────────

def test_a_shared_word_is_not_the_same_process(tmp_path):
    """REGRESSION, measured while writing the fix. `pdk_family_identity.
    same_family` is True at MATCH_TOKEN — a shared word — so a first cut of
    this fix matched two unrelated processes that both contain "process" and
    "family", and handed one the other's period. The contract requires at
    least MATCH_PREFIX."""
    import _l8_clock_scope as S  # noqa: PLC0415
    assert S.matches_target("process_family_c",
                            {"process_family_a", "process_family_b"}) is False
    # …while the register difference the real case turns on still matches.
    assert S.matches_target(_RUN_FAMILY, {_RUN_PDK.lower()}) is True
    assert S.matches_target(_OTHER_FAMILY, {_OTHER_PDK.lower()}) is True
    assert S.matches_target(_OTHER_FAMILY, {_RUN_PDK.lower()}) is False


def test_not_measured_and_mismatch_are_different_answers(tmp_path):
    """"I could not tell" must never reach a reader as "they disagree"."""
    import _l8_clock_scope as S  # noqa: PLC0415
    recs = [{"pdk_scoped_target": _RUN_PDK, "period_ns": _SCOPED_PERIOD_NS}]
    assert S.select(recs, None).status == S.NOT_MEASURED
    assert S.select(recs, _OTHER_PDK, "run_record").status == S.MISMATCH
    assert S.NOT_MEASURED != S.MISMATCH
