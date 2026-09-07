"""vibe-ic#2090 — L1.pin_table carried REGISTER names that are not ports.

MEASURED (lane cz2090, 8hd-3, on the opentitan_aes input, base cd83d08a933c):
the run halted at `final_audit` on `l_doc_cross_consistency_check` rc=1 because
L1.pin_table carried `CTRL_SHADOWED`, `KEY_SHARE0_0` and `KEY_SHARE1_7` — three
names the same input declares in a REGISTER-MAP table, none of which is an L9
port. The gate was right; the extractor was wrong. All three arrived with
`_extraction=backticked_interface_v455` and `mode=unspecified`: the backtick
walker promotes any backticked identifier inside a port-context heading range,
and a programmer's guide names its registers exactly that way ("write to
[`CTRL_SHADOWED`](registers.md#ctrl_shadowed)").

ONE IDENTITY, TWO TABLES. A name is a port or it is a register, never both.
`_v455_sanitize_and_merge_pins` now refuses a pin whose name the same input's
register-map extraction owns, and L1 NAMES the exclusion.

THE FIXTURE REPRODUCES THE PRODUCTION MECHANISM, not just the outcome: the
register names are never handed to the pass as pins at all. They are harvested
by the pass's own `_v455_interface_pins` re-add walker out of a programmer's
guide that mentions them under an interface heading — which is exactly how the
three opentitan_aes names got in.

The rule is deliberately narrow, and each test pins one leg of it:

  D1  the defect itself      a register name with no direction is refused
  D2  the exclusion is NAMED L1 provenance carries the name + the owning doc
  D3  the stem control       `ctrl_shadowed_i` beside a `CTRL_SHADOWED`
                             register is KEPT — the issue asks for this by name
  D4  the direction control  a register-table name a walker DID give a
                             direction is KEPT: the guard can never swallow an
                             established port — UNLESS the only document behind
                             the entry is the register document itself, which
                             the stem control caught and which D4b pins
  D5  the empty-input control  no register table => the pass is inert and the
                             very same names come through
  D6  the `_NOT_PROSE` falsifier for the register-table reader

D1, D2 and D5 discriminate: on the pre-fix tree D1 finds the registers still in
the table, D2 finds no provenance, and D5 passes on BOTH trees, which is what
makes it a control rather than a second copy of D1. MEASURED on the base blob
9050cea989e6 that every name asserted here survives the pre-existing #455
ALL-CAPS and self-name rejectors, so no assertion below is satisfied by a rule
that was already there.

Nothing here raises TypeError on the pre-fix signature: `_merge` passes the
new keyword only when the tree under test has the parameter, so the old tree
RUNS these tests and answers wrongly instead of erroring.
"""
import inspect
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase1_doc_one_shot_runner as P1  # noqa: E402
import regmap_table_extractor as RMX  # noqa: E402

from _hostpaths import require_repo  # noqa: E402


#: A GFM register table — the shape `extract_regmap_table` reads.
REG_DOC = """\
# Register Map

The block is named `demo_engine`.

| Name             | Offset   | Length | Description                 |
|:-----------------|:---------|-------:|:----------------------------|
| CTRL_SHADOWED    | 0x00     |      4 | Control Register.           |
| KEY_SHARE0_0     | 0x04     |      4 | Initial Key Register.       |
| STATUS_FLAGS     | 0x08     |      4 | Status Register.            |
"""

#: The prose that names those registers under an INTERFACE heading — the shape
#: that made the backtick walker promote them as pins in the first place.
GUIDE_DOC = """\
# Programmer's Guide

## Interface signals

Software configures the block by writing the [`CTRL_SHADOWED`](registers.md#ctrl_shadowed)
register, then polls [`STATUS_FLAGS`](registers.md#status_flags) until it clears.
"""

BOTH = {"registers.md": REG_DOC, "guide.md": GUIDE_DOC}
GUIDE_ONLY = {"guide.md": GUIDE_DOC}
SELF = "demo_engine"


def _merge(pins, extracted, self_name=SELF, collect=False):
    """Call the merge pass, supplying the #2090 provenance sink only when the
    tree under test has the parameter.

    On the PRE-FIX tree the parameter does not exist; the call still runs and
    `out` stays empty, so D2 reports the real absence rather than dying on an
    unexpected keyword."""
    out = []
    fn = P1._v455_sanitize_and_merge_pins
    if collect and "register_exclusions_out" in inspect.signature(fn).parameters:
        kept = fn(pins, extracted, self_name, register_exclusions_out=out)
    else:
        kept = fn(pins, extracted, self_name)
    return kept, out


def _names(rows):
    return [str(r.get("name") or "") for r in rows]


# ---------------------------------------------------------------------------
# D1 — the defect. RED on the pre-fix tree.
# ---------------------------------------------------------------------------
def test_a_register_the_input_declares_never_reaches_the_pin_table():
    """The production path: no register is handed in as a pin; the pass's own
    interface walker harvests them out of the guide's prose."""
    kept, _ = _merge([{"name": "clk_i", "mode": "input"}], BOTH)
    assert _names(kept) == ["clk_i"], (
        "vibe-ic#2090: `CTRL_SHADOWED` / `STATUS_FLAGS` are rows of this "
        "input's own register-map table and no walker gave either a "
        "direction, so they are registers of the design, not pins of the "
        f"chip — they must not reach L1.pin_table; got {_names(kept)}")


def test_a_register_name_handed_in_as_a_pin_is_refused_too():
    """Same rule on the main merge loop, not only on the re-add path — a drop
    made in one and not the other is undone one loop later."""
    kept, _ = _merge([{"name": "CTRL_SHADOWED", "mode": "unspecified"},
                      {"name": "KEY_SHARE0_0", "mode": "unspecified"}], BOTH)
    assert _names(kept) == [], _names(kept)


# ---------------------------------------------------------------------------
# D2 — the exclusion is NAMED. RED on the pre-fix tree.
# ---------------------------------------------------------------------------
def test_the_exclusion_is_named_with_the_document_that_owns_the_name():
    _, excl = _merge([{"name": "KEY_SHARE0_0", "mode": "unspecified"}],
                     BOTH, collect=True)
    by_name = {e["name"]: e for e in excl}
    assert "KEY_SHARE0_0" in by_name, (
        "vibe-ic#2090: a refused pin must be NAMED in L1's provenance — a pin "
        "that merely vanishes leaves a reader unable to tell an extractor "
        "that refused a register from one that never read the register "
        f"document at all; got {excl}")
    rec = by_name["KEY_SHARE0_0"]
    assert rec["owned_by"] == "input/docs/registers.md", rec
    assert rec["rule"] == "l1_pin_is_not_a_register_v2090", rec
    assert rec["basis"] == "regmap_table_extractor.extract_regmap_table", rec
    assert rec["reason"], rec


def test_each_refused_name_is_recorded_exactly_once():
    """The same name is refused on both paths in this fixture; provenance is a
    set of names, not a tally of drop sites."""
    _, excl = _merge([{"name": "CTRL_SHADOWED", "mode": "unspecified"}],
                     BOTH, collect=True)
    names = [e["name"] for e in excl]
    assert sorted(names) == sorted(set(names)), names
    assert "CTRL_SHADOWED" in names, names


def test_a_run_that_refuses_nothing_records_nothing():
    """The provenance list is evidence of an event, not a field that is always
    filled in."""
    _, excl = _merge([{"name": "clk_i", "mode": "input"}],
                     GUIDE_ONLY, collect=True)
    assert excl == [], excl


# ---------------------------------------------------------------------------
# D3 — the stem control. The issue asks for this one by name.
# ---------------------------------------------------------------------------
def test_a_port_that_shares_a_stem_with_a_register_is_kept():
    """`ctrl_shadowed_i` is a DIFFERENT NAME from the register
    `CTRL_SHADOWED`; the identity test is exact (case-folded), so a
    stem-sharing port is never touched."""
    stems = ["ctrl_shadowed_i", "status_flags_o", "key_share0_0_valid"]
    kept, excl = _merge([{"name": n, "mode": "unspecified"} for n in stems],
                        BOTH, collect=True)
    # MEMBERSHIP, and a control that passes on BOTH trees: the pre-fix tree
    # keeps these too, which is the point — this test exists to catch a fix
    # that over-reaches into prefix/suffix matching, not to re-prove D1.
    missing = [n for n in stems if n not in _names(kept)]
    assert missing == [], (
        "a port sharing a STEM with a register is not that register; the "
        f"identity test is exact (case-folded) — {missing} were dropped, "
        f"kept={_names(kept)}, excluded={[e['name'] for e in excl]}")
    assert [e for e in excl if e["name"] in stems] == [], excl


def test_the_identity_test_is_case_folded_not_case_sensitive():
    """A document that writes its register name in lower case owns that name
    just as much as one that shouts it."""
    kept, _ = _merge([{"name": "key_share0_0", "mode": "unspecified"}], BOTH)
    assert "key_share0_0" not in _names(kept), _names(kept)


# ---------------------------------------------------------------------------
# D4 — the direction control. The guard must never reach an established port.
# ---------------------------------------------------------------------------
def test_a_name_in_the_register_table_that_carries_a_direction_is_kept():
    """The narrowing condition, stated as a test. Every genuine port reaches
    this pass with a direction — a port-table row carries its own direction
    cell, a prose bullet resolves one per clause. If a design really does
    declare a PORT named like one of its registers, the direction is what says
    so, and the pin survives."""
    for mode in ("input", "output", "inout"):
        kept, excl = _merge([{"name": "STATUS_FLAGS", "mode": mode}],
                            BOTH, collect=True)
        assert "STATUS_FLAGS" in _names(kept), (
            f"mode={mode}: a walker established a direction for this name, so "
            f"it is a declared port — got {_names(kept)}")
        assert "STATUS_FLAGS" not in [e["name"] for e in excl], excl


def test_a_direction_inferred_from_the_registers_own_row_does_not_protect_it():
    """FOUND BY THE STEM CONTROL, not reasoned to.

    A register row `| DATA_IN_0 | 0x08 | 4 | Input Data Register. |` makes a
    walker infer `mode=input` from the word "Input" in the register's OWN
    description and stamp `evidence` = that register document. Before this leg
    existed the pin survived, promoted into L9.top_ports, and the
    cross-consistency gate could not see it because BOTH sides then agreed —
    a register had become a top-level port of the design.

    A direction read out of the very row that declares the register is not a
    port declaration."""
    entry = {"name": "KEY_SHARE0_0", "mode": "input",
             "evidence": "input/docs/registers.md"}
    kept, excl = _merge([entry], BOTH, collect=True)
    assert "KEY_SHARE0_0" not in _names(kept), (
        "the only document behind this entry IS the document whose register "
        f"table owns the name; got {_names(kept)}")
    assert "KEY_SHARE0_0" in [e["name"] for e in excl], excl


def test_a_direction_from_any_other_document_still_protects_the_pin():
    """The safety that keeps the exception above from becoming a licence to
    delete ports: a design that really does declare a PORT named like one of
    its registers says so in a port table, which is a different document, and
    that pin survives. Dropping it would be L9_PORT_LIST_INCOMPLETE — the
    ERROR half of the same gate's attribution."""
    entry = {"name": "KEY_SHARE0_0", "mode": "input",
             "evidence": "input/docs/guide.md"}
    kept, excl = _merge([entry], BOTH, collect=True)
    assert "KEY_SHARE0_0" in _names(kept), _names(kept)
    assert "KEY_SHARE0_0" not in [e["name"] for e in excl], excl


# ---------------------------------------------------------------------------
# D5 — inert on every input that declares no register table.
# PASSES ON BOTH TREES BY DESIGN: it is the control for D1, not a copy of it.
# ---------------------------------------------------------------------------
def test_an_input_with_no_register_table_leaves_the_very_same_names_alone():
    kept, _ = _merge([{"name": "clk_i", "mode": "input"}], GUIDE_ONLY)
    assert sorted(_names(kept)) == ["CTRL_SHADOWED", "STATUS_FLAGS", "clk_i"], (
        "with no register-map table in the input, nothing owns these names and "
        f"the pass must not touch them; got {_names(kept)}")


def test_the_census_is_empty_when_no_document_declares_a_register_table():
    assert P1._v2090_input_declared_register_names({}) == {}
    assert P1._v2090_input_declared_register_names(GUIDE_ONLY) == {}
    owned = P1._v2090_input_declared_register_names({"registers.md": REG_DOC})
    assert owned == {"CTRL_SHADOWED": "registers.md",
                     "KEY_SHARE0_0": "registers.md",
                     "STATUS_FLAGS": "registers.md"}, owned


# ---------------------------------------------------------------------------
# D6 — THE FALSIFIER FOR THE `_NOT_PROSE` CLAIM.
# ---------------------------------------------------------------------------
def test_the_not_prose_claim_for_the_register_table_reader_is_falsifiable():
    """`_v2090_input_declared_register_names` reads a register-map ADDRESS
    TABLE, not prose, which is why it consults no polarity vocabulary. The
    comment above it says so; this re-measures the claim instead of asserting
    it.

    Every text below is a SENTENCE that names a register and, in most cases, a
    hex address as well — including sentences that DENY the register. If any of
    them ever yields a register row, the `_NOT_PROSE` argument has stopped
    being true and the reader must start consulting `_prose_polarity` (or the
    guard must stop keying on this extractor). The instruction in that case is
    to fix the code, not to relax this test.
    """
    prose = [
        "Software configures the block by writing the `CTRL_SHADOWED` register.",
        "The CTRL_SHADOWED register lives at 0x00 and STATUS_FLAGS at 0x08.",
        "There is NO CTRL_SHADOWED register in this variant; 0x00 is reserved.",
        "The CTRL_SHADOWED register at offset 0x00 has been REMOVED, not moved.",
        "Register STATUS_FLAGS is not implemented and must never be written.",
        "See section 4.2 (registers) for the CTRL_SHADOWED description.",
    ]
    for text in prose:
        rows = RMX.extract_regmap_table(text, "input/docs/prose.md")
        assert rows == [], (
            "a PROSE sentence yielded register rows — the `_NOT_PROSE` claim "
            "made by `_v2090_input_declared_register_names` no longer holds "
            f"for: {text!r} -> {rows}")
        census = getattr(P1, "_v2090_input_declared_register_names", None)
        if census is not None:
            assert census({"prose.md": text}) == {}

    # The positive control for the same instrument: without it, the six
    # assertions above would also pass on a reader that reads nothing at all.
    assert RMX.extract_regmap_table(REG_DOC, "input/docs/registers.md"), (
        "the reader must still read a real table")


# ---------------------------------------------------------------------------
# D7 — THE REAL ARTEFACT. Every test above is synthetic, and a change whose
# tests are all fixtures authored alongside it cannot distinguish itself from
# its own absence (flow-change-acceptance §4, vibe-ic#400). This one is driven
# by the published input documents of the cell the issue was measured on.
#
# §4.05: it reads `input/docs/` only — never the golden, the vendor RTL or any
# harness output. It SKIPS, loudly and by name, when no corpus pointer names a
# clone: "could not look" is not "looked and found nothing".
# ---------------------------------------------------------------------------
def _real_input_docs():
    docs = require_repo("benchmark-data", "ic", "opentitan_aes", "input",
                        "docs")
    return {f.name: f.read_text(errors="replace")
            for f in sorted(docs.iterdir()) if f.is_file()}


def test_on_the_real_input_the_three_reported_names_are_owned_by_one_document():
    """The census, measured on the documents the defect was reported against.

    It also re-measures the `_NOT_PROSE` argument on REAL text rather than on
    text written to make the argument: of the ten published documents, only the
    one that prints an offset table yields any register name at all. The other
    nine — README, theory of operation, programmer's guide, checklist,
    interfaces and four diagram dumps — are prose, and return nothing."""
    extracted = _real_input_docs()
    owned = P1._v2090_input_declared_register_names(extracted)
    for name in ("CTRL_SHADOWED", "KEY_SHARE0_0", "KEY_SHARE1_7"):
        assert name in owned, (
            f"{name} is a row of this input's register table; the census must "
            f"own it — got {sorted(owned)[:10]}")
    sources = set(owned.values())
    assert len(sources) == 1, (
        "every register name must come from the one document that prints an "
        f"address table; got {sources}")


def test_on_the_real_input_the_three_reported_names_are_refused_as_pins():
    """The end of vibe-ic#2090, on the real documents: the backtick walker
    still promotes the three register names out of the programmer's guide, and
    the merge pass still refuses every one of them."""
    extracted = _real_input_docs()
    kept, excl = _merge([], extracted, "aes", collect=True)
    names = _names(kept)
    for name in ("CTRL_SHADOWED", "KEY_SHARE0_0", "KEY_SHARE1_7"):
        assert name not in names, (
            f"vibe-ic#2090: {name} is a register of this design, not a pin of "
            f"the chip; got {names}")
    refused = {e["name"] for e in excl}
    assert {"CTRL_SHADOWED", "KEY_SHARE0_0", "KEY_SHARE1_7"} <= refused, (
        f"each refusal must be named in provenance; got {sorted(refused)}")
