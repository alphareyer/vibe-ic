"""R-0915-29 — two more checklist items learn the third state the program
already has for a third.

`spec_review_lint --strict` refused `subservient`'s NINE INPUT documents -- a
benchmark input nobody may edit -- on two WARNings, and neither asked the design
for missing information:

    corner-case-uncovered: 'back-to-back transactions'
    corner-case-uncovered: 'full / empty / overflow / underflow'

MEASURED on that corpus (lane icsub2):

  * the whole fill-level vocabulary returns THREE hits in nine documents and
    not one is a container -- `L2:74` an "IO buffer" (a GPIO drive-strength
    cell), `L9:100` "❌ Buffer insertion 策略" (the PnR tool's timing buffers, in
    an explicit NOT-SPECIFIED list), `L7:69` a hold-fix "buffer-based 修復";
  * `L4_command_protocol.md` is headed `status: not-applicable` and says
    "它「執行 firmware」而非「接受外部 command」", with an explicit 沒有 list: no
    chip-level SPI/I2C/UART command interface, no opcode-encoded command
    parsing, no software-visible chip register.

So both items were reporting "uncovered" over an EMPTY population, which is
what the program's own FIX 3 comment calls a fabricated requirement: it "asks
the design to INVENT" the subject, and "a spec author who complies has made the
spec worse".  `illegal-inputs` already self-skips for exactly this reason.

The two tests below are the A/B pair the analysis rested on, and they are the
whole argument: A keeps both warnings LIVE on a spec that HAS the subjects, so
the rules still measure; B skips at INFO by NAME on a spec that declares it has
none.  Fixtures are written by the test, not staged in any project.
"""
import subprocess
import sys
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import spec_review_lint as L  # noqa: E402

BTB = "back-to-back transactions"
FEO = "full / empty / overflow / underflow"
ILL = "illegal inputs"

#: A — every subject present: a SERVED interface, a container with a depth, an
#: accumulation with a width, and an encoding layer with code points.
SPEC_WITH_SUBJECTS = """# L4 — Command / Protocol Layer
The block exposes an AXI-lite slave. A requester may issue back-to-back
transactions with no idle cycle between them; the slave accepts consecutive
requests every cycle. An 8-deep FIFO buffers write data. When the FIFO is full
the slave deasserts ready; when it is empty the read side stalls. The byte
counter wraps modulo 2^8 on overflow. Illegal opcodes (0x0F, 0x1F reserved)
return an error response. On reset during operation the FIFO is cleared.
"""

#: B — the same shape, written from `subservient`'s own words: no served
#: interface, no container, no command layer. It DRIVES a bus to its memory.
SPEC_WITHOUT_SUBJECTS = """# L4 — Command / Protocol Layer
This is an MCU-class SoC: it executes firmware rather than accepting external
commands. There is no chip-level SPI / I2C / UART command interface, no
opcode-encoded command parsing and no software-visible chip register. It drives
an external SRAM: address, write data, read data, write enable and a
bus-cycle-valid output. 同步 reset 為 active-high;reset 後內部狀態歸零。
"""


def _lint(tmp_path, body, name="L4.md", strict=False):
    f = tmp_path / name
    f.write_text(body)
    argv = ([sys.executable, str(PROG / "spec_review_lint.py")]
            + (["--strict"] if strict else []) + [str(f)])
    cp = subprocess.run(argv, capture_output=True, text=True)
    return cp.returncode, cp.stdout + cp.stderr


def _items(out, tag):
    """{checklist item -> the line that reported it} for one finding tag."""
    return {item: ln for ln in out.splitlines() if tag in ln
            for item in (BTB, FEO, ILL) if f"'{item}" in ln}


# ------------------------------------------------------------------ A

def test_a_spec_that_HAS_the_subjects_keeps_both_rules_live(tmp_path):
    """The rules must still measure. On a spec with a slave, a FIFO with a
    depth and an accumulation with a width, neither item warns AND neither is
    skipped -- they ran, and the spec covered them."""
    rc, out = _lint(tmp_path, SPEC_WITH_SUBJECTS)
    warned = _items(out, "corner-case-uncovered")
    skipped = _items(out, "corner-case-not-applicable")
    assert BTB not in warned and BTB not in skipped, out
    assert FEO not in warned and FEO not in skipped, out
    assert ILL not in warned and ILL not in skipped, out
    assert rc == 0


def test_a_spec_that_HAS_the_subjects_still_warns_about_what_it_omits(tmp_path):
    """The NEGATIVE CONTROL for A: removing the coverage sentences brings both
    warnings straight back, so the quiet above is coverage and not a new
    blanket."""
    stripped = (SPEC_WITH_SUBJECTS
                .replace("back-to-back\ntransactions with no idle cycle between them; the slave accepts "
                         "consecutive\nrequests every cycle.", "requests.")
                .replace("When the FIFO is full\nthe slave deasserts ready; "
                         "when it is empty the read side stalls.", "")
                .replace(" The byte\ncounter wraps modulo 2^8 on overflow.", ""))
    _, out = _lint(tmp_path, stripped)
    warned = _items(out, "corner-case-uncovered")
    assert BTB in warned, out
    assert FEO in warned, out
    # ... and they are WARNs, not skips: the subject is still declared
    assert BTB not in _items(out, "corner-case-not-applicable"), out
    assert FEO not in _items(out, "corner-case-not-applicable"), out


# ------------------------------------------------------------------ B

def test_a_spec_with_NO_subject_skips_by_name_at_info(tmp_path):
    """Never a WARN over an empty population. The skip names the item and the
    reason, so one third of the checklist not running is visible rather than
    indistinguishable from a pass."""
    rc, out = _lint(tmp_path, SPEC_WITHOUT_SUBJECTS, strict=True)
    skipped = _items(out, "corner-case-not-applicable")
    assert BTB in skipped, out
    assert FEO in skipped, out
    assert ILL in skipped, out
    assert "[INFO]" in skipped[BTB] and "[INFO]" in skipped[FEO]
    assert BTB not in _items(out, "corner-case-uncovered"), out
    assert FEO not in _items(out, "corner-case-uncovered"), out
    assert rc == 0, out


def test_the_skip_says_what_was_not_examined(tmp_path):
    _, out = _lint(tmp_path, SPEC_WITHOUT_SUBJECTS)
    skipped = _items(out, "corner-case-not-applicable")
    assert "no interface the design SERVES" in skipped[BTB]
    assert "no storage element with a fill level" in skipped[FEO]


# --------------------------------------------------- the predicates themselves

def test_naming_the_subject_is_not_enough(tmp_path):
    """The illegal-inputs rule for writing a predicate: a chapter that NAMES
    the noun while stating the design has none must not be read as having one.
    A container needs a depth; a served interface needs a served role."""
    assert not L._has_fill_level_container(
        "There is no FIFO and no queue in this design.")
    assert not L._has_fill_level_container(
        "GPIO output drive strength / IO buffer; ❌ Buffer insertion 策略")
    assert L._has_fill_level_container("An 8-deep FIFO buffers write data.")
    assert L._has_fill_level_container("佇列深度 16,每項 8 位元")
    assert not L._has_served_transaction_interface(
        "It drives an external SRAM and a bus-cycle-valid output.")
    assert L._has_served_transaction_interface("The block exposes an AXI-lite slave.")


def test_the_bare_word_buffer_is_not_a_container():
    """MEASURED: in this corpus `buffer` is a CELL far more often than a
    container -- a GPIO drive buffer, PnR buffer insertion, hold-fix buffers.
    A depth beside it is what makes it one."""
    for cell in ("output drive strength / IO buffer",
                 "❌ Buffer insertion 策略",
                 "Hold-fix 允許工具自動 buffer-based 修復"):
        assert not L._has_fill_level_container(cell + " 8 bits elsewhere")


def test_a_hyphenated_word_is_not_a_handshake():
    """MEASURED FALSE POSITIVE, pinned. `-` is a word boundary, so a bare
    `\\bready\\b` matches inside "production-ready GDS" -- which is the ONLY
    `ready` in `subservient`'s nine documents. Paired with the `有效` of
    "匯流排 cycle 有效" it made a TAPE-OUT STATUS LINE look like a served
    handshake and brought the warning back."""
    tapeout_status = ("| Tapeout status | 已在 OpenMPW shuttle 跑過 sign-off;"
                      "baseline 預期可重現 production-ready GDS |\n"
                      "| `o_sram_cyc` | 1-bit | output | 匯流排 cycle 有效 |")
    assert not L._has_served_transaction_interface(tapeout_status)
    # ... while the real thing, spelt as signal names, still is one
    assert L._has_served_transaction_interface(
        "input valid, output ready; a transfer happens when both are high")


#: The env var a lane points at its own staged corpus with. NAMED, never a
#: personal path: shipped source must be portable (rule R1,
#: `shipped_path_portability_check`), and a `/home/<user>/` literal in a test is
#: exactly as unshippable as one in a program. The case below SKIPS with this
#: name when it is unset, so a reader is told what to set rather than told
#: nothing.
CORPUS_ENV = "VIBEIC_SPEC_CORPUS_DIR"


def test_the_real_corpus_is_the_case_this_was_written_for(tmp_path):
    """END TO END on the nine INPUT documents this lane may not edit: strict
    goes from a refusal on two fabricated requirements to a pass with three
    named disclosures, and not one assertion was weakened to get there.

    Point `VIBEIC_SPEC_CORPUS_DIR` at a staged `input/docs` to run it; the
    lane that authored this ran it against `subservient`'s nine documents."""
    import os
    import pytest
    root = os.environ.get(CORPUS_ENV)
    if not root:
        pytest.skip(f"set {CORPUS_ENV} to a staged input/docs to run this case")
    docs = Path(root)
    if not docs.is_dir():
        pytest.skip(f"{CORPUS_ENV}={root!r} is not a directory")
    import subprocess
    cp = subprocess.run(
        [sys.executable, str(PROG / "spec_review_lint.py"), "--strict"]
        + sorted(str(p) for p in docs.glob("*.md")),
        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout
    assert cp.stdout.count("corner-case-uncovered") == 0
    assert cp.stdout.count("corner-case-not-applicable") == 3


def test_a_valid_the_design_DRIVES_is_not_a_served_interface():
    """BOTH halves of a handshake are required, and this is why: a lone `valid`
    is just as likely to be an output the design DRIVES. `subservient`'s
    `o_sram_cyc` is exactly that -- a bus-cycle-valid it asserts at its own
    memory, with nothing on the other side pacing it. Accepting a lone `valid`
    would put every bus MASTER back under an item about what arrives."""
    driver_only = ("output o_sram_cyc: bus cycle valid, asserted by this block\n"
                   "output o_sram_we, output o_sram_addr (10 bits)")
    assert not L._has_served_transaction_interface(driver_only)
    # the pair, and only the pair, is a served interface
    assert L._has_served_transaction_interface(driver_only + "\ninput ready")
