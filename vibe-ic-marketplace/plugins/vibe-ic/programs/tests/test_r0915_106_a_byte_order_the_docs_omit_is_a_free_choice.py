"""R-0915-106 — a register byte order the documents do not state is a FREE CHOICE.

MEASURED, sha256 x sky130A, lane icsha2 run20, front door. All three of the
design's L10 `known_answer_vector` cases refused with

    no sentence in the design's documents states a byte order OF THE REGISTERS

and the refusal is CORRECT: mapping a hex message onto `BLOCK0..BLOCK15` needs
to know whether BLOCK0 carries the first four bytes or the last, and
`grep -niE 'endian|byte order|位元組|大端|小端|MSB|LSB'` over the whole of
`input/docs/*.md` matches NOTHING. The docs genuinely do not say.

That is the definition of a free choice — a decision no downstream tool can
recover by inference — and the flow already has the place to record one: the
author's `plugin_output/declaration.json`.
"""
from __future__ import annotations

import inspect

import pytest

import register_bus_driver_gen as R


_DOC_BIG = {"L5.md": "All registers are big-endian."}
_DOC_LITTLE = {"L5.md": "Note that all registers are little-endian."}


def test_a_document_sentence_still_wins_over_the_declaration():
    """The design's own prose outranks the author's declaration."""
    order, why = R.register_endianness(_DOC_LITTLE,
                                       {"register_byte_order": "big"})
    assert order == "little"
    assert "L5.md" in why


def test_a_contradicting_document_refuses_before_the_declaration_is_read():
    """A declaration can never paper over a contradiction."""
    corpus = {"a.md": "All registers are big-endian.",
              "b.md": "all registers are little-endian."}
    order, why = R.register_endianness(corpus, {"register_byte_order": "big"})
    assert order is None
    assert "BOTH byte orders" in why


def test_silent_docs_plus_a_declaration_resolve():
    order, why = R.register_endianness({}, {"register_byte_order": "big"})
    assert order == "big"
    assert "plugin_output/declaration.json" in why and "register_byte_order" in why


@pytest.mark.parametrize("key", ["register_byte_order", "byte_order",
                                 "endianness"])
def test_every_declared_spelling_is_read(key):
    order, _ = R.declared_register_endianness({key: "little"})
    assert order == "little"


@pytest.mark.parametrize("value", ["big-endian", "LITTLE", " big_endian "])
def test_the_common_spellings_of_the_value_are_read(value):
    order, _ = R.declared_register_endianness({"register_byte_order": value})
    assert order in ("big", "little")


def test_silent_docs_and_no_declaration_refuse_exactly_as_before():
    """NEGATIVE CONTROL, and the load-bearing one: with neither source the
    original sentence is still the refusal, word for word."""
    order, why = R.register_endianness({}, None)
    assert order is None
    assert why.startswith("no sentence in the design's documents states a byte "
                          "order OF THE REGISTERS")


def test_a_value_this_consumer_cannot_read_is_refused_not_coerced():
    """A declaration of 'network' is a refusal, never a reading of a default."""
    order, why = R.declared_register_endianness(
        {"register_byte_order": "network"})
    assert order is None
    assert "neither 'big' nor 'little'" in why


def test_a_declaration_without_the_field_is_named_not_guessed():
    order, why = R.declared_register_endianness({"top_module": "sha256"})
    assert order is None
    assert "declares none of" in why


def test_a_missing_declaration_is_its_own_reason():
    order, why = R.declared_register_endianness(None)
    assert order is None and why == "no declaration was supplied"


def test_the_declaration_reader_is_pure():
    src = inspect.getsource(R.declared_register_endianness)
    for forbidden in ("open(", "Path(", "subprocess", "json.load"):
        assert forbidden not in src, forbidden


def test_the_plan_resolver_takes_the_declaration():
    sig = inspect.signature(R.resolve_register_plan)
    assert "declaration" in sig.parameters
    assert sig.parameters["declaration"].default is None


def test_the_kav_emitter_reads_the_projects_declaration():
    import known_answer_vector_tb_gen as K
    src = inspect.getsource(K)
    assert "plugin_output" in src and "declaration.json" in src
