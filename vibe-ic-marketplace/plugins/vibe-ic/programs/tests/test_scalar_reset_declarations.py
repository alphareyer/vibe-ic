"""`_scalar_reset_declarations` -- the first test this shipped module has ever had.

RC16: the second of the two modules under `programs/` that no test named. Its
docstring states the doctrine this file pins:

    Explicit whole-register reset declarations, never proximity inference.
    A None candidate denotes an explicit but unsupported/ambiguous declaration;
    it blocks choosing another value. Empty candidates mean no declaration.

That three-way distinction is the whole point and it is easy to lose: a set with
a None in it is NOT the same as an empty set, and collapsing them would let a
register whose stated reset value nobody could parse silently inherit a value
from somewhere else. `unique_value` is where the distinction is enforced.

Every expectation was MEASURED against the module as it ships.
"""
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _scalar_reset_declarations as S        # noqa: E402


# -- only the whole register is a subject --------------------------------------
def test_the_whole_register_declares_its_scalar():
    assert S.candidates("CTRL reset value is 0x1.", "CTRL") == {"0x1"}


def test_a_field_suffix_cannot_supply_the_registers_scalar():
    """`CTRL.INIT` is not `CTRL`. This is the `never proximity inference` half:
    a value sitting next to the name is not a declaration ABOUT the name."""
    assert S.candidates("CTRL.INIT reset value is 0x1.", "CTRL") == set()


def test_a_bit_clause_cannot_supply_the_registers_scalar():
    assert S.candidates("CTRL bit0 reset value is 1.", "CTRL") == set()


def test_cleared_by_reset_is_zero():
    assert S.candidates("CTRL is cleared by reset.", "CTRL") == {"0x0"}


# -- every radix normalises to one spelling -----------------------------------
def test_binary_decimal_and_hex_all_normalise_to_hex():
    assert S.candidates("CTRL reset value is 0b10.", "CTRL") == {"0x2"}
    assert S.candidates("CTRL reset value is 16.", "CTRL") == {"0x10"}
    assert S.candidates("CTRL reset value is 0x10.", "CTRL") == {"0x10"}


# -- THE THREE-WAY DISTINCTION, which is what this module exists for ----------
def test_an_unparsable_value_is_None_not_absent():
    """An explicit declaration whose value cannot be read is a None MEMBER --
    present, unsupported -- and not an empty set."""
    got = S.candidates("CTRL reset value is TBD.", "CTRL")
    assert got == {None}
    assert got != set(), "an unsupported declaration is not the absence of one"


def test_a_None_member_blocks_choosing_another_value():
    assert S.unique_value({"0x1"}) == "0x1"
    assert S.unique_value({"0x1", None}) is None, (
        "an unreadable declaration must not be outvoted by a readable one")
    assert S.unique_value({None}) is None
    assert S.unique_value(set()) is None


def test_two_readable_values_do_not_elect_a_winner():
    assert S.unique_value({"0x1", "0x2"}) is None


# -- the table reader, and its address filter ---------------------------------
_TABLE = ("| Name | Reset | Address |\n"
          "|---|---|---|\n"
          "| CTRL | 0x5 | 0x10 |\n"
          "| STAT | 0x0 | 0x14 |\n")


def test_a_table_row_is_read_by_name():
    assert S.candidates(_TABLE, "CTRL") == {"0x5"}
    assert S.candidates(_TABLE, "STAT") == {"0x0"}


def test_a_table_row_must_also_match_the_address_when_one_is_given():
    assert S.candidates(_TABLE, "CTRL", 0x10) == {"0x5"}
    assert S.candidates(_TABLE, "CTRL", 0x99) == set(), (
        "a row at another address is not this register's declaration")


def test_a_name_absent_from_the_table_declares_nothing():
    assert S.candidates(_TABLE, "MISSING") == set()


# -- the guard rails ----------------------------------------------------------
def test_a_non_string_or_empty_name_declares_nothing():
    assert S.candidates(None, "CTRL") == set()
    assert S.candidates("CTRL reset value is 0x1.", "") == set()
    assert S.candidates("CTRL reset value is 0x1.", None) == set()
