"""An input that declares TWO DRC engines must get two DRC engines.

MEASURED on spm run18L and run19 (lane icspm5, 8HD-4, read-only, 2026-09-22):

    input/docs/L1_product_metadata.md:46 declares  DRC engines ['klayout','magic']
    reports/phase3/drc_signoff.log                 179 klayout mentions, 0 magic
    reports/phase3/precheck_magic_drc.json         producers [klayout, null]
    reports/phase3/lvs_extraction_preflight.json   performed=false,
                                                   INCONCLUSIVE,
                                                   tech_load_path_is_unexpanded_tcl

and `magic_illegal_overlap.json` is step 31's EXTRACTION-feedback screen, not a
rule deck applied to a layout. So `l24_signoff_evidence_backed_check` reported
the DRC half UNMET_ENGINE_EVIDENCE and it was RIGHT to: nothing in either run
was a Magic sign-off DRC. §4.05 forbids touching the input to make the
requirement go away, so the FLOW has to run what the input asked for.

Two halves, both measured in the pinned container before a line was written:

(1) THE TECHNOLOGY WAS NEVER MISSING. The gf180mcuD rc loads it as
    `tech load $PDK_ROOT/gf180mcuD/libs.tech/magic/gf180mcuD.tech`, and the
    reader took literals only. magic 8.3.684, handed that same rc headless:

        MAGIC_TECH_NAME:     gf180mcuD
        MAGIC_TECH_FILENAME: /foss/pdks/gf180mcuD/libs.tech/magic/gf180mcuD.tech
        MAGIC_DRC_STYLE:     drc(fast) drc(full) drc(routing) empty

(2) THE MAGIC DIALECT IS NOT GUESSABLE, so it was measured on a cell built to
    violate on purpose:

        drc list count total -> 4                       (error TILES)
        drc listall why      -> {Metal1 minimum area < 0.1444um^2 (M1.3)}
                                {{0 0 20 20} {1000 0 1020 20}}
                                {Metal1 width < 0.23um (M1.1)}
                                {{20 0 46 20} {0 20 20 46} ...}

    a FLAT list alternating rule text and that rule's rectangles — 6 shapes
    over 4 tiles, which is why both numbers are reported and either one being
    non-zero is the FAIL.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402
import extraction_input_capability_check as E  # noqa: E402
import _signoff_drc_format as SDF  # noqa: E402
import l24_signoff_evidence_backed_check as L24  # noqa: E402


# ── (1) the technology the rc names through a variable ─────────────────────

_RUN19_RC = "/foss/pdks/gf180mcuD/libs.tech/magic/gf180mcuD.magicrc"
_RUN19_RAW = "$PDK_ROOT/gf180mcuD/libs.tech/magic/gf180mcuD.tech"
_RUN19_TECH = "/foss/pdks/gf180mcuD/libs.tech/magic/gf180mcuD.tech"


def test_the_unexpanded_tech_load_resolves_against_the_rcs_own_tree():
    """run19's exact shape: the remainder is the rc's own text and the base is
    PROVEN, so the pre-flight stops reporting a technology it could have found."""
    seen = []

    def exists(path):
        seen.append(path)
        return path == _RUN19_TECH

    assert E.resolve_tech_load(_RUN19_RAW, _RUN19_RC, exists) == _RUN19_TECH
    assert any(p == _RUN19_TECH for p in seen)


def test_a_tech_that_is_nowhere_is_still_refused():
    """THE OTHER DIRECTION, and the reason this is a resolution and not a
    guess: every candidate is proven by `exists`, so a rc whose technology is
    absent still yields None and the run still reports the pre-flight as not
    performed."""
    assert E.resolve_tech_load(_RUN19_RAW, _RUN19_RC, lambda p: False) is None


def test_the_environment_the_tool_will_run_in_outranks_the_tree():
    """`$PDK_ROOT` is expanded by MAGIC from the environment, so when the
    caller knows that environment it is the first base tried."""
    env_tech = "/elsewhere/gf180mcuD/libs.tech/magic/gf180mcuD.tech"
    got = E.resolve_tech_load(_RUN19_RAW, _RUN19_RC,
                              lambda p: p in (env_tech, _RUN19_TECH),
                              {"PDK_ROOT": "/elsewhere"})
    assert got == env_tech


def test_a_variable_that_is_not_the_root_prefix_is_not_reconstructed():
    """Only the leading-`$VAR` case is reconstructible. Anything else would
    require inventing the part of the path the rc did not write."""
    assert E.split_leading_tcl_var("/a/$X/b.tech") is None
    assert E.resolve_tech_load("/a/$X/b.tech", _RUN19_RC, lambda p: True) is None


def test_the_text_only_contract_is_unchanged():
    """`tech_load_directive` still answers about the TEXT alone — the callers
    that ask it what the rc says must keep getting that answer."""
    path, why, raw = E.tech_load_directive(f"tech load {_RUN19_RAW}\n")
    assert path is None
    assert why == E.TECH_LOAD_UNEXPANDED
    assert raw == _RUN19_RAW


def test_the_runner_resolves_before_it_declares_the_preflight_unperformed():
    """The producer-side wiring: the resolver is consulted on the UNEXPANDED
    branch, and only a failure to resolve still reports 'not performed'."""
    src = Path(R.__file__).read_text()
    i_branch = src.index("_why == _eicap.TECH_LOAD_UNEXPANDED")
    i_resolve = src.index("_eicap.resolve_tech_load(")
    i_report = src.index("extraction pre-flight DID NOT RUN")
    assert i_branch < i_resolve < i_report


# ── (2) the Magic sign-off DRC deck ────────────────────────────────────────

def _tcl():
    return R._magic_signoff_drc_tcl("/c/design.gds", "chip_top", "/c/out.rpt")


def test_the_deck_asserts_the_full_style_and_checks_the_whole_cell():
    """A run that silently checked `drc(routing)` would report a clean chip it
    never fully looked at, and `drc check` only rechecks the area UNDER THE
    BOX — so the top cell must be selected before it."""
    t = _tcl()
    assert "drc style drc(full)" in t
    assert t.index("select top cell") < t.index("drc check")
    assert "drc catchup" in t
    assert "drc listall why" in t
    assert "drc list count total" in t


def test_the_deck_reads_the_layout_and_never_writes_it():
    t = _tcl()
    assert "gds readonly true" in t
    assert "gds read /c/design.gds" in t
    assert "gds write" not in t
    assert "load chip_top" in t


def test_a_deck_that_could_not_read_the_layout_says_so_and_stops():
    """A `gds read` that fails must not fall through into a check of an empty
    database and report it clean."""
    t = _tcl()
    assert "MAGIC_GDS_READ_FAILED" in t
    i_fail = t.index("MAGIC_GDS_READ_FAILED")
    assert "quit -noprompt" in t[i_fail:i_fail + 120]


_MAGIC_STUBS = r"""
# ONLY the commands the deck is entitled to use. Anything else raises
# "invalid command name" exactly as magic did, which is the point: the FIRST
# version of this deck printed `[magic::magicversion]`, magic 8.3.684 has no
# such command, and the raise ended the script after two header lines -- a
# 69-byte report the runner was about to call `violations: 0, status: PASS`.
# A string assertion over the generated Tcl could not see that; running it can.
proc drc {args} {
  switch -- [lindex $args 0] {
    listall { return {{Rule A spacing} {{0 0 1 1} {2 2 3 3}} {Rule B width} {{4 4 5 5}}} }
    list    { return 4 }
    default { return {} }
  }
}
proc gds {args} { return }
proc load {args} { return }
proc select {args} { return }
proc box {args} { return "0 0 10 10" }
proc tech {args} {
  switch -- [lindex $args 0] {
    name     { return TECHNAME }
    filename { return /pdk/t.tech }
    version  { return 1.2.3 }
  }
  return {}
}
proc quit {args} { exit 0 }
"""


def _drive_deck(tmp_path):
    """Run the deck the runner generates, under tclsh, with magic stubbed."""
    tclsh = shutil.which("tclsh")
    if tclsh is None:                                    # pragma: no cover
        pytest.skip("tclsh not installed")
    rpt = tmp_path / "out.rpt"
    script = tmp_path / "deck.tcl"
    script.write_text(_MAGIC_STUBS + R._magic_signoff_drc_tcl(
        "/c/design.gds", "chip_top", str(rpt)))
    cp = subprocess.run([tclsh, str(script)], capture_output=True, text=True,
                        cwd=str(tmp_path))
    return cp, rpt


def test_driven_the_deck_runs_and_writes_a_complete_report(tmp_path):
    cp, rpt = _drive_deck(tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert "invalid command name" not in (cp.stderr + cp.stdout)
    assert rpt.is_file()
    text = rpt.read_text()
    for line in ("# Tool: magic", "tech name: TECHNAME",
                 "tech file: /pdk/t.tech", "tech version: 1.2.3",
                 "layout: /c/design.gds", "top cell: chip_top",
                 "drc style requested: drc(full)",
                 "RULE: 2 | Rule A spacing", "RULE: 1 | Rule B width",
                 "rules with violations: 2",
                 "error tiles (drc list count total): 4",
                 "total violations: 3", "COUNT: 3"):
        assert line in text, (line, text)


def test_driven_the_report_the_deck_writes_classifies_as_magic(tmp_path):
    """The deck's OWN output — not a fixture — must satisfy the producer
    classification `_report_engine` reads."""
    _cp, rpt = _drive_deck(tmp_path)
    prod = SDF.classify_file(rpt)
    assert prod.kind == SDF.MAGIC, prod.evidence
    assert prod.is_signoff_deck is True


def test_a_report_that_stops_early_is_not_a_clean_chip(tmp_path):
    """THE FAILURE THAT HAPPENED. 69 bytes, two header lines, no trailer —
    and the first version of this runner read it as zero violations."""
    proj = tmp_path / "p"
    (proj / "reports" / "phase3").mkdir(parents=True)
    (proj / R._MAGIC_SIGNOFF_DRC_REL).write_text(
        "# Tool: magic\n# sign-off DRC, rule deck applied to a streamed "
        "layout\n")
    src = Path(R.__file__).read_text()
    i = src.index("carries no COUNT trailer")
    assert 'row["status"] = "NOT_MEASURED"' in src[i - 400:i]
    assert re.search(r'\^COUNT:\\\\s\*\\\\d\+\\\\s\*\$', src) or \
        'r"^COUNT:\\s*\\d+\\s*$"' in src


#: The transcript the deck above writes, in the dialect measured in the image.
#: Every prefix used here is asserted to exist in the emitted Tcl by
#: `test_the_fixture_is_the_report_the_deck_actually_writes`, so the fixture
#: cannot drift away from the producer.
_CLEAN_RPT = """# Tool: magic
# sign-off DRC, rule deck applied to a streamed layout
magic version: 8.3.684
tech name: gf180mcuD
tech file: /pdk/libs.tech/magic/gf180mcuD.tech
tech version: 1.0.599
layout: /c/design.gds
top cell: chip_top
drc style requested: drc(full)
commands: drc check ; drc catchup ; drc listall why ; drc list count total

rules with violations: 0
error tiles (drc list count total): 0
total violations: 0
COUNT: 0
"""

#: What the runner APPENDS after the deck ran: the rule descriptions the
#: technology's own `drc` section declares. MEASURED on gf180mcuD: 155
#: distinct descriptions, 7 KB, 61 naming a spacing rule and 26 a width rule.
#: Without it a clean Magic transcript is 618 bytes and `eda_report_audit`
#: refuses it as `DRC_CATEGORIES_EXIST` + `DRC_REPORT_TOO_SMALL`.
_DECK_RULES = ("\ndeck rules declared by /pdk/t.tech (drc section): 3\n"
               "DECK_RULE: Metal1 spacing < 0.23um (M1.2)\n"
               "DECK_RULE: Metal1 width < 0.23um (M1.1)\n"
               "DECK_RULE: Diffusion spacing to tap < %d (DV.6)\n"
               + "DECK_RULE: filler rule text to carry the report past the "
                 "hand-typed-stub floor\n" * 40)

_DIRTY_RPT = _CLEAN_RPT.replace(
    "rules with violations: 0",
    "RULE: 2 | Metal1 minimum area < 0.1444um^2 (M1.3)\n"
    "RULE: 4 | Metal1 width < 0.23um (M1.1)\n\n"
    "rules with violations: 2").replace(
    "error tiles (drc list count total): 0",
    "error tiles (drc list count total): 4").replace(
    "total violations: 0", "total violations: 6").replace(
    "COUNT: 0", "COUNT: 6")


def test_the_fixture_is_the_report_the_deck_actually_writes():
    """A fixture the producer stopped writing proves nothing. Every line
    prefix below is looked up in the emitted Tcl."""
    t = _tcl()
    for prefix in ("# Tool: magic", "tech name:", "tech file:", "layout:",
                   "top cell:", "commands:",
                   "rules with violations:",
                   "error tiles (drc list count total):",
                   "total violations:", "COUNT:", "RULE:",
                   "drc style requested:"):
        assert prefix in t, prefix


def test_the_transcript_classifies_as_a_magic_signoff_producer(tmp_path):
    """THE LOAD-BEARING ONE. `l24_signoff_evidence_backed_check._report_engine`
    takes the engine from `_signoff_drc_format`'s classification of the
    report's OWN bytes, and that function is not being weakened — the report
    is made real enough to satisfy it."""
    for text in (_CLEAN_RPT, _DIRTY_RPT):
        p = tmp_path / "r.rpt"
        p.write_text(text)
        prod = SDF.classify_file(p)
        assert prod.kind == SDF.MAGIC, prod.evidence
        assert prod.is_signoff_deck is True
        assert SDF.attribution_disagrees(prod) is False


def test_the_router_is_still_not_a_magic_signoff_producer(tmp_path):
    """OVER-BREADTH CONTROL. Nothing added here may let the router's own
    detailed-route DRC classify as a sign-off deck."""
    p = tmp_path / "r.rpt"
    p.write_text("# Tool: klayout\n[INFO DRT-0702] Post-route verification: "
                 "0 violation(s).\ndrc check\n")
    prod = SDF.classify_file(p)
    assert prod.kind != SDF.MAGIC


def test_the_per_rule_table_is_parsed_in_the_dialect_that_was_measured():
    rows = R._magic_rule_rows(_DIRTY_RPT)
    assert rows == [(2, "Metal1 minimum area < 0.1444um^2 (M1.3)"),
                    (4, "Metal1 width < 0.23um (M1.1)")]
    assert R._magic_rule_rows(_CLEAN_RPT) == []


def test_no_rule_falls_out_of_the_category_table():
    """A category table whose parts do not sum to the total can hide a rule
    class nobody named, so an unmatched rule is counted `unclassified`."""
    rows = [(3, "Some rule nobody's vocabulary knows")]
    cats = R._magic_rule_categories(rows)
    assert cats == {"unclassified": 3}
    cats2 = R._magic_rule_categories(R._magic_rule_rows(_DIRTY_RPT))
    assert cats2["area"] == 2 and cats2["width"] == 4


def test_the_engine_list_comes_from_the_input_and_gates_the_leg(tmp_path):
    """OVER-BREADTH CONTROL: a design whose input names one engine must start
    no Magic process at all."""
    docs = tmp_path / "input" / "docs"
    docs.mkdir(parents=True)
    (docs / "L1.md").write_text("- sign-off: DRC clean with klayout\n")
    assert "magic" not in R._declared_drc_engines(tmp_path)
    (docs / "L1.md").write_text(
        "- sign-off: DRC clean, checked with klayout and magic\n")
    assert "magic" in R._declared_drc_engines(tmp_path)


def test_the_leg_runs_only_when_the_input_declared_magic():
    src = Path(R.__file__).read_text()
    i_gate = src.index('if "magic" in _engines:')
    i_run = src.index("_run_magic_signoff_drc(\n")
    assert i_gate < i_run


def test_a_second_engine_finding_violations_fails_a_first_engine_pass():
    """'FAIL on any count > 0, like the klayout deck' — and a design the
    second engine finds violations in is not DRC clean because the first
    engine did not find them."""
    src = Path(R.__file__).read_text()
    i = src.index('if _mrow.get("status") == "FAIL" and status == "PASS":')
    assert 'status = "FAIL"' in src[i:i + 600]


@pytest.mark.parametrize("violations,tiles,want", [
    (0, 0, "PASS"),
    (6, 4, "FAIL"),
    (0, 4, "FAIL"),   # tiles alone — the two numbers disagree by construction
    (6, 0, "FAIL"),
])
def test_either_number_being_non_zero_is_the_fail(violations, tiles, want):
    """Reconstructed from the same expression the runner uses, so the table
    and the code cannot disagree about which states fail."""
    got = ("FAIL" if (violations > 0 or (tiles is not None and tiles > 0))
           else "PASS")
    assert got == want
    src = Path(R.__file__).read_text()
    assert ('row["status"] = ("FAIL" if (row["violations"] > 0\n'
            '                                or (tiles is not None and tiles > 0))'
            ) in src


def test_a_watchdog_kill_is_blocked_and_never_a_clean_verdict():
    src = Path(R.__file__).read_text()
    i = src.index("def _run_magic_signoff_drc")
    body = src[i:i + 6000]
    assert "_RC_STALLED" in body
    assert 'row["status"] = "BLOCKED"' in body
    assert "timeout=" not in body.split("_docker_exec(container")[1][:400]


def test_the_receipt_the_engine_check_reads_is_produced_end_to_end(tmp_path):
    """END TO END on the real programs: transcript -> `drc_report_check
    --signoff` -> `_report_engine` == 'magic'. This is the whole point of the
    change, and nothing in `_report_engine` is relaxed to get there."""
    proj = tmp_path / "p"
    (proj / "reports" / "phase3").mkdir(parents=True)
    (proj / "phase3" / "stage3" / "pnr").mkdir(parents=True)
    (proj / "phase3" / "stage3" / "pnr" / "chip_top.gds").write_bytes(b"GDS\0")
    rel = R._MAGIC_SIGNOFF_DRC_REL
    (proj / rel).write_text(_CLEAN_RPT + _DECK_RULES)
    row = R._audit_magic_signoff_drc(proj)
    assert row["status"] == "PRODUCED", row
    payload = json.loads((proj / R._MAGIC_SIGNOFF_DRC_JSON_REL).read_text())
    assert payload.get("program") == "eda_report_audit:drc"
    engine = L24._report_engine(R._MAGIC_SIGNOFF_DRC_JSON_REL, payload, proj)
    assert engine == "magic", json.dumps(payload.get("summary"), indent=1)[:2000]
    # AND THE AUDIT MUST PASS IT. A receipt whose verdict is `fail` is read by
    # `l24_signoff_evidence_backed_check` as a FAILING engine, which is worse
    # than a missing one. MEASURED: the first version of this report was 618
    # bytes and the audit refused it on DRC_CATEGORIES_EXIST +
    # DRC_REPORT_TOO_SMALL.
    assert payload.get("passed") is True, [
        f for f in payload.get("findings", [])
        if f.get("severity") == "ERROR"]
    assert row["rc"] == 0, row


def test_the_audit_is_skipped_when_the_deck_produced_nothing(tmp_path):
    proj = tmp_path / "p"
    (proj / "reports" / "phase3").mkdir(parents=True)
    row = R._audit_magic_signoff_drc(proj)
    assert row["status"] == "NOT_APPLICABLE"
    assert not (proj / R._MAGIC_SIGNOFF_DRC_JSON_REL).exists()


_TECH_SNIPPET = """
tech
 format 32
end

drc
 width  m1 23 "Metal1 width < 0.23um (M1.1)"
 spacing m1 m1 23 touching_ok \\
    "Metal1 spacing < 0.23um (M1.2)"
 edge4way m2 x 1 "s"
end

extract
 style "ngspice extraction style for this technology"
end

cifoutput
 style "gds write style, not a design rule at all"
end
"""


def test_the_deck_rule_list_comes_from_the_technology_that_ran():
    rules = R._magic_deck_rule_texts(_TECH_SNIPPET)
    assert rules == ["Metal1 spacing < 0.23um (M1.2)",
                     "Metal1 width < 0.23um (M1.1)"]


def test_nothing_outside_the_drc_section_is_taken_for_a_rule():
    """OVER-BREADTH CONTROL: the `extract` and `tech` sections have quoted
    strings too, and a reader that swept the whole file would describe the
    deck with things that are not rules."""
    got = " ".join(R._magic_deck_rule_texts(_TECH_SNIPPET))
    assert "ngspice" not in got, got
    assert "gds write style" not in got, got
    assert R._magic_deck_rule_texts(
        'extract\n style "ngspice extraction style here"\nend\n') == []


def test_a_deck_rule_line_is_never_counted_as_a_violation():
    """`DECK_RULE:` says the deck HAS this rule; `RULE: n |` says it FIRED n
    times. A parser that confused them would report a clean chip as 155
    violations."""
    rows = R._magic_rule_rows(_CLEAN_RPT + _DECK_RULES)
    assert rows == []


def test_an_unreadable_technology_is_disclosed_not_hidden():
    src = Path(R.__file__).read_text()
    assert 'row["deck_rules_unreadable"]' in src
    i = src.index('row["deck_rules_unreadable"]')
    assert "never silent" in src[i - 300:i]


def test_a_denial_spliced_into_the_magic_transcript_moves_nothing():
    """THE FALSIFIER the `_NOT_PROSE` entry is measured by, not asserted from.

    The transcript is a fixed field grammar this module writes itself, so the
    polarity question does not arise — but "does not arise" is a claim, and a
    claim gets a measurement. Every token of `_prose_polarity`'s vocabulary,
    the CJK spellings included, is spliced INTO each read field and appended
    to it, and the reader's answers must not move. The negative control is the
    last two asserts: deleting the trailer DOES move the verdict, so the zeros
    below are about the grammar and not a fixture that cannot move."""
    import _prose_polarity as pp

    tokens = ["not", "no", "none", "without", "excluding", "never", "non",
              "removed", "obsolete", "superseded", "n/a", "inapplicable",
              "deprecated", "no longer", "does not apply",
              "非", "无", "無", "不", "否"]
    for tok in tokens:
        assert pp.NEGATION_RE.search(tok), tok   # the vocabulary, not my list

    base = _CLEAN_RPT + _DECK_RULES
    tech = re.search(r"^tech file:\s*(/\S*)\s*$", base, re.M).group(1)
    tiles = re.search(
        r"^error tiles \(drc list count total\):\s*(-?\d+)\s*$",
        base, re.M).group(1)
    count = re.search(r"^COUNT:\s*(\d+)\s*$", base, re.M).group(1)
    assert (tiles, count) == ("0", "0")
    assert tech.endswith(".tech"), tech

    moved = []
    for tok in tokens:
        for spelling in (f"{tok} ", f" {tok}", f"{tok}"):
            for line, repl in (
                    (f"tech file: {tech}",
                     f"tech file: {spelling}{tech}"),
                    ("error tiles (drc list count total): 0",
                     f"error tiles (drc list count total): {spelling}0"),
                    ("COUNT: 0", f"COUNT: {spelling}0"),
                    (f"tech file: {tech}",
                     f"tech file: {tech} {tok}"),
                    ("error tiles (drc list count total): 0",
                     f"error tiles (drc list count total): 0 {tok}"),
                    ("COUNT: 0", f"COUNT: 0 {tok}")):
                spliced = base.replace(line, repl, 1)
                t = re.search(r"^tech file:\s*(/\S*)\s*$", spliced, re.M)
                k = re.search(
                    r"^error tiles \(drc list count total\):\s*(-?\d+)\s*$",
                    spliced, re.M)
                c = re.search(r"^COUNT:\s*\d+\s*$", spliced, re.M)
                # A denial cannot make a field say something ELSE. Either the
                # anchored field still reads exactly what the deck wrote, or
                # the line stops parsing and the value is simply absent —
                # which this runner reports as unmeasured, never as clean.
                if t is not None and t.group(1) != tech:
                    moved.append(("tech", tok, spelling, t.group(1)))
                if k is not None and k.group(1) != "0":
                    moved.append(("tiles", tok, spelling, k.group(1)))
                # An UNPARSED field is a refusal, not a rival answer: the
                # runner reports NOT_MEASURED and never a clean chip. What
                # must never happen is a field reading something ELSE, which
                # is what the two checks above look for.
                if c is not None and c.group(0).strip() != "COUNT: 0":
                    moved.append(("count", tok, spelling, c.group(0)))
    assert not moved, moved[:8]

    # NEGATIVE CONTROL: the trailer is load-bearing, so its absence MUST move
    # the answer. If this passed too, the loop above would prove nothing.
    assert re.search(r"^COUNT:\s*\d+\s*$", base, re.M)
    assert not re.search(r"^COUNT:\s*\d+\s*$",
                         base.replace("COUNT: 0", "COUNT:"), re.M)


def test_the_register_entry_names_this_reader_and_carries_its_argument():
    import prose_polarity_consulted_check as gate
    key = "phase3_one_shot_runner::_run_magic_signoff_drc"
    assert key in gate._NOT_PROSE, sorted(gate._NOT_PROSE)[:5]
    reason = gate._NOT_PROSE[key]
    assert len(reason) >= gate._EXEMPT_REASON_MIN
    assert "test_a_denial_spliced_into_the_magic_transcript_moves_nothing" \
        in reason, "the entry must name the measurement it rests on"


def test_the_receipt_is_found_without_being_declared_in_the_flow():
    """AND THE FLOW DOES NOT DECLARE IT — measured, and here is why.

    The first version of this branch added
    `reports/phase3/drc_signoff_magic.json OR reports/phase3/drc_signoff.json`
    to step 31's `required_outputs`, spelled ` OR ` so a design whose input
    never asked for a second engine would not start failing. Dimension 8
    refused it, correctly:

        step 31: entry 'reports/phase3/drc_signoff.json' shares EVERY one of
        its alternatives with the dropped 'reports/phase3/drc_signoff_magic
        .json OR reports/phase3/drc_signoff.json', so no tree satisfies one
        and not the other and this cell's negative half is not measurable as
        declared.

    That is a finding about the DECLARATION. An entry whose every alternative
    is already another entry cannot be measured in either direction, and the
    honest answer is not to regenerate the census around it — it is to not
    make the declaration.

    IT BUYS NOTHING ANYWAY, and that is measurable: `signoff_record_paths_for`
    derives declared records only from steps whose NAME says sign-off, and
    step 31 is named "Physical Verification (DRC + LVS + ERC + Density)". So
    DRC has no declared record at all today, `l24_signoff_evidence_backed_check`
    falls back to its report-name scan, and the scan finds this receipt by its
    filename. The engine evidence arrives either way.
    """
    from l24_signoff_requirements_extract import signoff_record_paths_for
    assert signoff_record_paths_for("DRC") == (), (
        "DRC now HAS a declared record — the scan is no longer the path this "
        "receipt reaches the engine check by, and it must be declared after "
        "all (with an entry dimension 8 can measure in both directions)")
    yaml_text = (PROGRAMS.parent / "flow"
                 / "phase1_phase2_phase3.yaml").read_text()
    assert "drc_signoff_magic" not in yaml_text, (
        "the flow declares an entry whose every alternative is another "
        "entry's; dimension 8 cannot measure its negative half")


def test_the_receipt_filename_is_what_the_scan_matches_on():
    """The scan keys on the report TOKEN in the path, so the name is load
    bearing: rename it and the engine evidence silently stops arriving."""
    from l24_signoff_requirements_extract import _path_names_token, \
        report_tokens_for
    tokens = report_tokens_for("DRC")
    assert tokens, tokens
    assert _path_names_token(R._MAGIC_SIGNOFF_DRC_JSON_REL, tokens)


def test_the_two_paths_are_spelled_once():
    assert R._MAGIC_SIGNOFF_DRC_REL == "reports/phase3/drc_signoff_magic.rpt"
    assert R._MAGIC_SIGNOFF_DRC_JSON_REL == "reports/phase3/drc_signoff_magic.json"
    src = Path(R.__file__).read_text()
    assert src.count('"reports/phase3/drc_signoff_magic.rpt"') == 1
    assert src.count('"reports/phase3/drc_signoff_magic.json"') == 1
