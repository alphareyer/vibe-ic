"""`_spare_insertion_provenance` reads VERILOG, not prose — measured.

It is a `_NOT_PROSE` entry in `prose_polarity_consulted_check`, and the claim is
measured here rather than asserted. The function asks ONE question of the chip
wrapper the runner actually read: does this planned spare-pad name occur as an
IDENTIFIER TOKEN in the module? Before it tokenises, it deletes every string
literal, line comment and block comment, and it requires a complete
`module ... endmodule`. So the text it judges is Verilog module-item grammar,
which has no negation form — an instance that is not there is ABSENT, and
absence is already the answer (`planned_not_inserted`).

The two things a denial could try are measured separately, because they fail
for different reasons:

  * MINT one. A comment naming a pad verbatim — including one that DENIES it —
    is stripped before tokenising, so it can never move a name into
    `required_insertions`. This is the direction that would publish a false
    insertion, and it is the one the strip exists for.
  * CANCEL one. A real instantiation stays in the token set whatever any
    comment says, which is the function's own documented contract: the cell
    insertion producer owns every `instances` obligation and a wrapper cannot
    cancel it.

The negative controls at the end move the answer deliberately.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as R  # noqa: E402
import _prose_polarity as pol  # noqa: E402

_TOKENS = ("not", "no", "none", "without", "excluding", "never", "non",
           "removed", "obsolete", "superseded", "n/a", "inapplicable",
           "deprecated", "no longer", "does not apply",
           "非", "无", "無", "不", "否")

_PLAN = {"instances": [{"name": "spare_cell_0"}],
         "spare_pads": [{"name": "spare_pad_in_0"}]}

_WRAPPER_WITHOUT_PAD = """module chip_top (input wire clk);
  sky130_fd_sc_hd__conb_1 spare_cell_0 (.HI(), .LO());
endmodule
"""

_WRAPPER_WITH_PAD = """module chip_top (input wire clk);
  sky130_fd_sc_hd__conb_1 spare_cell_0 (.HI(), .LO());
  pad_in spare_pad_in_0 (.PAD(), .C());
endmodule
"""


def _answer(tmp_path, text, name="w.v"):
    p = tmp_path / name
    p.write_text(text)
    got = R._spare_insertion_provenance(_PLAN, p)
    return (got["status"], tuple(got["required_insertions"]),
            tuple(got["planned_not_inserted"]),
            got["wrapper"]["read_status"])


def test_every_token_is_a_denial_in_the_shared_vocabulary():
    for token in _TOKENS:
        assert pol.is_denied(f"spare_pad_in_0 is {token} instantiated"), token


def test_no_denial_can_MINT_an_insertion(tmp_path):
    """A comment naming the pad — even one DENYING it — is stripped first."""
    base = _answer(tmp_path, _WRAPPER_WITHOUT_PAD)
    assert base == ("OBSERVED", ("spare_cell_0",), ("spare_pad_in_0",), "READ")
    for token in _TOKENS:
        claim = f"spare_pad_in_0 is {token} instantiated"
        for text in (f"// {claim}\n" + _WRAPPER_WITHOUT_PAD,
                     _WRAPPER_WITHOUT_PAD + f"// {claim}\n",
                     _WRAPPER_WITHOUT_PAD.replace(
                         "endmodule", f"  /* {claim} */\nendmodule"),
                     _WRAPPER_WITHOUT_PAD.replace(
                         "input wire clk", f'input wire clk /* {claim} */')):
            assert _answer(tmp_path, text) == base, (token, text)


def test_no_denial_can_CANCEL_an_insertion(tmp_path):
    base = _answer(tmp_path, _WRAPPER_WITH_PAD)
    assert base == ("OBSERVED",
                    ("spare_cell_0", "spare_pad_in_0"), (), "READ")
    for token in _TOKENS:
        claim = f"spare_pad_in_0 is {token} instantiated"
        for text in (f"// {claim}\n" + _WRAPPER_WITH_PAD,
                     _WRAPPER_WITH_PAD + f"/* {claim} */\n"):
            assert _answer(tmp_path, text) == base, (token, text)


def test_a_denial_inside_a_string_literal_is_stripped_too(tmp_path):
    base = _answer(tmp_path, _WRAPPER_WITHOUT_PAD)
    for token in _TOKENS:
        text = _WRAPPER_WITHOUT_PAD.replace(
            "endmodule",
            f'  initial $display("spare_pad_in_0 {token} placed");\n'
            f"endmodule")
        assert _answer(tmp_path, text) == base, token


# ── NEGATIVE CONTROLS: the fixture CAN move the answer ──────────────────────

def test_instantiating_the_pad_moves_it_out_of_planned_not_inserted(tmp_path):
    assert _answer(tmp_path, _WRAPPER_WITHOUT_PAD)[2] == ("spare_pad_in_0",)
    assert _answer(tmp_path, _WRAPPER_WITH_PAD)[2] == ()


def test_a_wrapper_with_no_complete_module_is_refused_not_defaulted(tmp_path):
    p = tmp_path / "partial.v"
    p.write_text("module chip_top (input wire clk);\n")
    got = R._spare_insertion_provenance(_PLAN, p)
    assert got["status"] == "UNVERIFIED"
    assert got["wrapper"]["read_status"] == "UNREADABLE"
    assert got["reason"] == "wrapper has no complete module"
    # The cell obligation survives an unreadable wrapper; only the OPTIONAL
    # claim is withheld.
    assert got["required_insertions"] == ["spare_cell_0"]
    assert got["planned_not_inserted"] == []
