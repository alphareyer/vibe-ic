"""A cut-off LEC must still NAME the cells it left unproven (vibe-ic#2182).

`parse_equiv_output` read the unproven-cell list from ONE place: the closing
`equiv_status` summary line `Unproven $equiv cells: ...`. A run that never
reaches `equiv_status` -- killed inside the ladder, or still running -- does not
print that line, so the published `unproven_cells` was `[]` while the same raw
log named every unproven cell in the `equiv_induct` workset block.

Measured 2026-09-07 on the two blocked benchmark ICs:

  * opentitan_aes -- `unproven_points: 3`, `unproven_cells: []`; the log named
    three `u_reg_status_key_init.clean_d` replicas.
  * sha256 -- 34 unproven, `unproven_cells: []`; the log named
    `_LECWRAP.__uuf__.{ready,valid}_reg` and `read_data [0..31]`.

A count with no names is not a finding anyone can act on. These tests pin that
the workset block is read as a SECONDARY source, that the primary source still
wins when it is present, and that neither source present still yields `[]` --
never a fabricated default.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lec_run  # noqa: E402


_AES_TAIL = """
24. Executing EQUIV_INDUCT pass.
Found 3 unproven $equiv cells in module equiv:
  Proving existence of base case for step 1. (2975289 clauses over 1137455 variables)
  Proving induction step 1. (6025179 clauses over 2297864 variables)
  Proof for induction step failed. Trying to prove individual $equiv from workset.
  Trying to prove $equiv for \\u_dut.u_aes_core.gen_fsm[0].u_reg_status_key_init.clean_d: failed.
  Trying to prove $equiv for \\u_dut.u_aes_core.gen_fsm[1].u_reg_status_key_init.clean_d: failed.
  Trying to prove $equiv for \\u_dut.u_aes_core.gen_fsm[2].u_reg_status_key_init.clean_d: failed.
Proved 0 previously unproven $equiv cells.
"""

# A name with a SPACE in it -- `read_data [0]` is how yosys spells a bit
# select. A whitespace-split extractor shreds it into two tokens, which is why
# the pattern anchors on the `: failed.` suffix instead.
_SHA_TAIL = """
25. Executing EQUIV_INDUCT pass.
Found 3 unproven $equiv cells in module equiv:
  Proof for induction step failed. Trying to prove individual $equiv from workset.
  Trying to prove $equiv for \\_LECWRAP.__uuf__.ready_reg: failed.
  Trying to prove $equiv for \\read_data [0]: failed.
  Trying to prove $equiv for \\read_data [31]: failed.
Proved 0 previously unproven $equiv cells.
"""


def test_workset_block_names_the_unproven_cells_when_equiv_status_never_ran():
    parsed = lec_run.parse_equiv_output(_AES_TAIL)
    assert parsed["unproven"] == 3
    assert parsed["unproven_cells"] == [
        "\\u_dut.u_aes_core.gen_fsm[0].u_reg_status_key_init.clean_d",
        "\\u_dut.u_aes_core.gen_fsm[1].u_reg_status_key_init.clean_d",
        "\\u_dut.u_aes_core.gen_fsm[2].u_reg_status_key_init.clean_d",
    ]
    # MEMBERSHIP, not just a count: the names must account for the residual.
    assert len(parsed["unproven_cells"]) == parsed["unproven"]


def test_a_name_containing_a_space_survives_intact():
    parsed = lec_run.parse_equiv_output(_SHA_TAIL)
    assert parsed["unproven_cells"] == [
        "\\_LECWRAP.__uuf__.ready_reg",
        "\\read_data [0]",
        "\\read_data [31]",
    ]


def test_only_the_last_workset_block_is_read():
    """Each rung re-reports what is STILL unproven, so the furthest state the
    run reached is the LAST block -- the same rule `_INDUCT_FOUND_RE` follows
    for the residual count. Reading an earlier block would name cells a later
    rung has since proven."""
    earlier = _AES_TAIL.replace("clean_d", "clean_d_EARLIER")
    parsed = lec_run.parse_equiv_output(earlier + _SHA_TAIL)
    assert all("EARLIER" not in c for c in parsed["unproven_cells"])
    assert parsed["unproven_cells"][0] == "\\_LECWRAP.__uuf__.ready_reg"


def test_the_equiv_status_summary_still_wins_when_it_is_present():
    """NO-LEAK: on a run that DOES reach `equiv_status` the published list is
    byte-identical to what it was before the secondary source existed."""
    text = _AES_TAIL + "\nUnproven $equiv cells: alpha beta\n"
    assert lec_run.parse_equiv_output(text)["unproven_cells"] == ["alpha", "beta"]


def test_neither_source_present_stays_empty_and_invents_nothing():
    """'Could not read it' is not 'read it and it was empty' -- but it is also
    not a licence to supply a default. With no workset block and no summary
    line the list stays empty."""
    text = "20. Executing EQUIV_SIMPLE pass.\nFound 7 unproven $equiv cells in module equiv:\n"
    parsed = lec_run.parse_equiv_output(text)
    assert parsed["unproven"] == 7
    assert parsed["unproven_cells"] == []


def test_a_success_line_is_not_turned_into_a_name_list():
    """`equiv_induct` prints `success!` for the cells it PROVES in the same
    block. Only the `failed.` lines are unproven."""
    text = _AES_TAIL.replace(
        "Proved 0 previously unproven",
        "  Trying to prove $equiv for \\proven_one: success!\nProved 1 previously unproven")
    assert "\\proven_one" not in lec_run.parse_equiv_output(text)["unproven_cells"]
