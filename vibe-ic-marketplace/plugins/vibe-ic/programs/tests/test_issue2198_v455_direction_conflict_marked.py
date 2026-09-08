"""ORGANIC #2198 — a direction INFERRED from a role noun, against the only
direction word the sentence contains, is now MARKED instead of silent.

The defect, in one sentence: `_v455_dir_from_line`'s ordered word table mixes
two different kinds of evidence.  ``inputs?`` / ``outputs?`` are a READING of
the design input — the sentence states a direction.  ``clocks?`` and
``suppl|reference|…`` are a GUESS — a direction inferred from a port-role
noun.  `_v455_dir_for_span` resolves that table per clause (#455), so a
trailing clause that names a role but states no direction had its direction
decided by the noun, and the whole line's ``outputs`` — the sentence's ONLY
direction word — never entered the decision.  Two halves of one sentence came
out with opposite `mode` values, carrying that same sentence as their shared
`description`, and nothing on either said which was read and which was
guessed.

This module pins the SECOND half of #2198: not which direction is right (the
prose is genuinely ambiguous, and §4.05 forbids settling it from any
reference), but that a resolver which disagrees with the sentence it read
SAYS SO.  Every assertion below fails on the pre-#2198 tree, where the key
does not exist at all.

The negative cases are load-bearing in the other direction: a marker that
fired on every bullet would carry no information, so a clause that states its
own direction word, a clause whose role AGREES with the line, a line with no
direction word, and a pipe-table row that reads its direction from a cell are
all asserted UNMARKED.

Synthetic bullets throughout; chip-AGNOSTIC, no chip/vendor/node literal.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phase1_doc_one_shot_runner as P  # noqa: E402

_HEAD = "## Externally-observable interface (per the chip's top pins)\n\n"


def _pins(*bullets: str) -> dict:
    doc = _HEAD + "".join(b if b.endswith("\n") else b + "\n" for b in bullets)
    return {p["name"]: p for p in P._v455_interface_pins({"L1_DATASHEET.md":
                                                         doc})}


# ── the reproduction ───────────────────────────────────────────────────────

#: The #2198 shape: ONE bullet, one direction word ("outputs"), and a trailing
#: clause whose only direction evidence is the role noun "clocks".
_BULLET = ("- Digital serial outputs `OUT1..OUT6` (+ `dout` serial), "
           "modulator clocks `CK4/CK5/CK6`.")


def test_role_inferred_direction_against_the_sentence_is_marked():
    by = _pins(_BULLET)
    for n in ("CK4", "CK5", "CK6"):
        assert by[n]["mode"] == "input", by[n]
        assert by[n].get("low_confidence") is True, (
            f"{n} took its direction from the noun 'clocks' while the "
            f"sentence's only direction word says 'outputs', and said "
            f"nothing about it")


def test_both_readings_are_recorded_not_just_the_winner():
    c = _pins(_BULLET)["CK4"]["direction_conflict"]
    assert c["emitted"] == "input"
    assert c["emitted_from"] == "clause_role_noun"
    # the sentence's own word is PRESERVED, not discarded
    assert c["alternative"] == "output"
    assert c["alternative_from"] == "line_direction_word"
    assert "clocks" in c["clause"].lower()
    assert "outputs" in c["line"].lower()


def test_the_directions_themselves_are_unchanged():
    """CONTROL — green on both arms by construction.

    #2198 is about the silence, not the answer.  If a later change makes the
    marker fire by RE-DECIDING the direction, this goes red and says so.
    """
    by = _pins(_BULLET)
    assert [by[n]["mode"] for n in ("OUT1", "OUT6", "dout")] == \
        ["output", "output", "output"]
    assert [by[n]["mode"] for n in ("CK4", "CK5", "CK6")] == \
        ["input", "input", "input"]


def test_the_unconflicted_pins_of_the_same_sentence_are_not_marked():
    by = _pins(_BULLET)
    for n in ("OUT1", "OUT6", "dout"):
        assert "low_confidence" not in by[n], by[n]
        assert "direction_conflict" not in by[n], by[n]


# ── the same shape, the other role row ─────────────────────────────────────

def test_supply_reference_role_against_the_sentence_is_marked_too():
    """`suppl|reference|…` is the table's other ROLE row and takes the same
    rule — the population is the role TIER, not the word 'clock'."""
    by = _pins("- Analog inputs `A1`/`A2` (PAD), differential referenced "
               "to `VHI`/`VLO`.")
    for n in ("VHI", "VLO"):
        assert by[n]["mode"] == "inout", by[n]
        assert by[n].get("low_confidence") is True, by[n]
        assert by[n]["direction_conflict"]["alternative"] == "input"


# ── the negatives: a marker that fires everywhere says nothing ─────────────

def test_clause_stating_its_own_direction_word_is_not_marked():
    by = _pins("- Analog inputs `A1`, digital outputs `O1`.")
    assert by["A1"]["mode"] == "input" and "low_confidence" not in by["A1"]
    assert by["O1"]["mode"] == "output" and "low_confidence" not in by["O1"]


def test_role_that_agrees_with_the_sentence_is_not_marked():
    by = _pins("- Digital inputs `D1`, sampling clocks `CKA`.")
    assert by["CKA"]["mode"] == "input"
    assert "low_confidence" not in by["CKA"], (
        "the noun and the sentence agree — there is nothing to flag")


def test_line_with_no_direction_word_is_not_marked():
    by = _pins("- Modulator clocks `CKA`/`CKB`.")
    assert by["CKA"]["mode"] == "input"
    assert "low_confidence" not in by["CKA"], (
        "no direction word was overruled — the noun was the only evidence "
        "there was")


def test_two_role_clauses_that_disagree_with_EACH_OTHER_are_not_marked():
    """The guard that says the LINE must state an explicit direction.

    Found by mutation: removing that guard left all 14 tests green, because the
    only negative case exercising it used a SINGLE-clause line — where clause ==
    line, so the remaining disjunct (`line_stated == clause_dir`) suppressed the
    marker anyway and the branch was never reached.

    This is the input that separates them: TWO clauses, BOTH resolving from the
    ROLE tier, whose role answers DIFFER (`clocks` -> input at line level,
    `supply` -> inout in the token's own clause), and NO explicit direction word
    anywhere on the line.  Two nouns disagreeing with each other is not "the
    sentence and the name disagree" — the sentence says nothing at all, so there
    is nothing for a noun to contradict and there is no conflict to report.
    Mark it and the marker fires on ordinary two-role bullets and means nothing.
    """
    line = "- Modulator clocks `CKA`, supply rails `VDDA`."
    # the branch really is reached: the two tiers disagree and neither is explicit
    assert P._v455_dir_from_line_tiered(line) == ("input", "role")
    j = line.index("`VDDA`")
    assert P._v455_dir_from_line_tiered(
        P._v455_clause_for_span(line, j)) == ("inout", "role")

    by = _pins(line)
    assert by["VDDA"]["mode"] == "inout"
    assert "low_confidence" not in by["VDDA"], (
        "no direction word was stated anywhere on this line — a role noun "
        "cannot contradict a sentence that says nothing")
    assert by["CKA"]["mode"] == "input"
    assert "low_confidence" not in by["CKA"]


def test_single_clause_line_is_not_marked():
    by = _pins("- Digital serial outputs `Q1` and modulator clocks `CKA`.")
    # one clause: the whole-line precedence applies exactly as before #455
    assert by["Q1"]["mode"] == "output" and by["CKA"]["mode"] == "output"
    assert "low_confidence" not in by["CKA"]


def test_pipe_table_row_direction_is_read_from_its_cell_and_never_marked():
    by = _pins("| Port | Width | Dir |",
               "|---|---|---|",
               "| `ckx` | 1 | output |")
    assert by["ckx"]["mode"] == "output"
    assert "low_confidence" not in by["ckx"], (
        "a table row STATES its direction in a cell — it is never an "
        "inference from a noun")


# ── the tier split itself ──────────────────────────────────────────────────

def test_tiered_resolver_reports_which_kind_of_evidence_it_used():
    assert P._v455_dir_from_line_tiered("digital outputs") == \
        ("output", "explicit")
    assert P._v455_dir_from_line_tiered("modulator clocks") == \
        ("input", "role")
    assert P._v455_dir_from_line_tiered("core supply rail") == \
        ("inout", "role")
    assert P._v455_dir_from_line_tiered("no direction here") == \
        ("unspecified", "none")


def test_tiered_resolver_is_a_pure_refactor_of_the_flat_one():
    """CONTROL — the direction half of the table did not move."""
    for s in ("analog inputs `a`", "digital outputs `q`", "modulator clocks",
              "supply `vdd`", "reference `vref`", "ground return",
              "nothing at all", "", "power rails"):
        assert P._v455_dir_from_line(s) == P._v455_dir_from_line_tiered(s)[0]


# ── the marker has to survive to the layer consumers read ──────────────────

_MARKED_L1_PIN = {
    "name": "ckx",
    "mode": "input",
    "extraction_strategy": "backticked_interface_v455",
    "low_confidence": True,
    "direction_conflict": {"emitted": "input",
                           "emitted_from": "clause_role_noun",
                           "alternative": "output",
                           "alternative_from": "line_direction_word",
                           "clause": "modulator clocks `ckx`.",
                           "line": "- outputs `q`, modulator clocks `ckx`.",
                           "reason": "…"},
}


def test_marker_survives_the_l1_to_l9_promotion():
    """L9.top_ports is the layer downstream consumers read, and its build is a
    WHITELIST projection — an un-forwarded marker is a marker that does not
    exist."""
    out = P._promote_l1_pins_to_l9_ports([dict(_MARKED_L1_PIN)], "dut")
    assert out, "the guard chain dropped the pin — test fixture is wrong"
    e = out[0]
    assert e["mode"] == "input"
    assert e.get("low_confidence") is True
    assert e["direction_conflict"]["alternative"] == "output"


def test_marker_survives_the_other_l9_producer_too(tmp_path):
    """There are TWO producers of L9.top_ports (this one inline in
    `gen_l9_integration_spec`, the other `_promote_l1_pins_to_l9_ports`).
    Which one ran must not decide whether the marker exists."""
    doc = _HEAD + _BULLET + "\n"
    extracted = {"input/docs/L1_DATASHEET.md": doc}
    # end-to-end: the REAL L1 extractor writes the pin_table gen_l9 reads.
    P.gen_l1_datasheet(tmp_path, extracted)
    P.gen_l9_integration_spec(tmp_path, extracted, {})
    import json
    l9 = json.loads((tmp_path / "phase1" / "generated_docs"
                     / "L9_INTEGRATION_SPEC.json").read_text())
    ports = {p["name"]: p for p in (l9.get("top_ports")
                                    or l9.get("top_module_pins") or [])}
    assert ports, f"no ports emitted: {sorted(l9)}"
    marked = {n for n, p in ports.items() if p.get("low_confidence")}
    assert marked == {"ck4", "ck5", "ck6"}, (
        f"L9 lost the direction-conflict marker: {ports}")
    assert ports["ck4"]["direction_conflict"]["alternative"] == "output"


def test_marker_survives_the_WHOLE_post_emit_chain(tmp_path):
    """The two producers are patched — but passes run AFTER them and rewrite
    both layers.  Any of those that projects a port dict through a whitelist
    would silently drop the marker, which is this issue's own defect class:
    an un-forwarded marker is a marker that does not exist.

    The two tests above pin the two producers individually, with hand-built
    inputs.  Neither reaches the post-emit chain.  This one does: it runs the
    real `_post_emit_*` passes and then asserts BOTH layers still carry the
    marker AND both readings.  Enumerated at the time of writing —
    `crosswalk_l9_ports_to_l1_pin_table_v1_6_555`,
    `crosswalk_l9_ports_to_l7_debug_v1_6_551`,
    `resolve_symbolic_port_widths_v1_11_91` — but the test calls whatever the
    module exposes, so a pass added later is covered without editing this list.
    """
    import json
    doc = _HEAD + _BULLET + "\n"
    extracted = {"input/docs/L1_DATASHEET.md": doc}
    P.gen_l1_datasheet(tmp_path, extracted)
    P.gen_l9_integration_spec(tmp_path, extracted, {})

    ran = []
    for nm in sorted(n for n in dir(P) if n.startswith("_post_emit_")):
        fn = getattr(P, nm)
        if not callable(fn):
            continue
        try:
            fn(tmp_path); ran.append(nm)
        except TypeError:
            pass          # a post-emit pass with a different arity: not ours
    assert ran, "no _post_emit_ pass ran — this test would be vacuous"

    gd = tmp_path / "phase1" / "generated_docs"
    l1 = json.loads((gd / "L1_DATASHEET.json").read_text())
    l9 = json.loads((gd / "L9_INTEGRATION_SPEC.json").read_text())
    ports = l9.get("top_ports") or l9.get("top_module_pins") or []

    m1 = {p["name"].lower() for p in l1.get("pin_table", [])
          if p.get("low_confidence")}
    m9 = {p["name"].lower() for p in ports if p.get("low_confidence")}
    assert m1 == {"ck4", "ck5", "ck6"}, f"L1 lost the marker after {ran}: {m1}"
    assert m9 == {"ck4", "ck5", "ck6"}, f"L9 lost the marker after {ran}: {m9}"

    # the READINGS have to survive too — a bare boolean is not the finding
    for layer, rows in (("L1", l1.get("pin_table", [])), ("L9", ports)):
        for row in rows:
            if row.get("low_confidence"):
                c = row.get("direction_conflict")
                assert c and c["alternative"] == "output", (layer, row)


def test_a_silent_clause_inherits_the_LINE_direction_and_survives_to_L9():
    """The clause-silent fallback, which #2198's refactor moved.

    `_v455_dir_for_span_ex` opens with `if clause_dir == "unspecified": return
    line_dir` — a token whose own clause states nothing takes the LINE's answer.
    Found by mutation (M8): replacing that guard with `if False` left BOTH this
    module (16 passed) and the wider v455 suite (38 passed) green, while
    silently changing three ports of the tree's own fixture from `inout` to
    `unspecified` — and a mode outside {input, output, inout} is DROPPED by both
    L9 producers.  So the unguarded branch deletes ports from the interface and
    nothing said so.

    It survived because the existing coverage asserts pin MEMBERSHIP at the L1
    walker (`{"VLDO", "VREF"} <= names`), where an unspecified-mode pin still
    exists.  Nothing asserted the MODE, and nothing carried it to L9 — the layer
    where it disappears.  This asserts both.

    It is the largest unmarked class in the tree: 56 of the 260 pins whose
    direction comes from a role noun reach this branch.
    """
    line = ("- Supplies `IOVDD` (1.8 V), `CORE` (1.2 V); "
            "`VLDO`/`VREF` for the LDO channel.")
    by = _pins(line)

    # the branch is REACHED: these tokens' own clauses state no direction
    for n in ("VLDO", "VREF"):
        j = line.index("`%s`" % n)
        assert P._v455_dir_from_line_tiered(
            P._v455_clause_for_span(line, j))[0] == "unspecified", n

    # ...so they inherit the LINE's answer, and are NOT left unspecified
    for n in ("IOVDD", "CORE", "VLDO", "VREF"):
        assert by[n]["mode"] == "inout", (n, by[n]["mode"])
        assert "low_confidence" not in by[n], (
            "the sentence states no direction — nothing to contradict")

    # and an inherited direction is a VALID mode, so the port reaches L9.
    # This is the half that makes the failure visible: mode=unspecified is
    # dropped by the promotion, so the port vanishes from the interface.
    promoted = {e["name"] for e in P._promote_l1_pins_to_l9_ports(
        [dict(by[n], extraction_strategy="backticked_interface_v455")
         for n in ("IOVDD", "CORE", "VLDO", "VREF")], "dut")}
    assert promoted == {"iovdd", "core", "vldo", "vref"}, (
        f"a port was dropped on the way to L9: {promoted}")
