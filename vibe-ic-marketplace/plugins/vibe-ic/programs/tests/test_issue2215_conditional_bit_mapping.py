#!/usr/bin/env python3
"""Conditional numeric bit mappings must survive into the deterministic handoff.

MEASURED on the shared extractor at v1.20.7, on two public inputs that state
DIFFERENT contracts:

    "Convert a 12-bit width to a larger width of 20-bit. When active is one,
     result[11] is sample[11] XOR flip. When active is zero, result[11] equals
     sample[11]. Bits result[10:0] always equal sample[10:0]. If extend is one,
     bits result[19:12] repeat result[11]; otherwise those upper bits are zero."

    "Convert a 12-bit width to a larger width of 20-bit. Preserve all twelve
     source bits. Do not invert any source bit. The upper eight result bits
     are zero."

both returned ONE identical item -- `width_convert 12->20` -- and
`spec_coverage_check` emitted only that kind. No condition, no bit relation,
no extension source and no preservation prohibition reached the deterministic
consumer, so two contracts that differ in every bit they describe were
indistinguishable downstream.

WHAT MUST NOT CHANGE, and is pinned below: §4.05 no-leak. Every field is read
off an EXPLICIT clause and carries that clause as evidence; an unspecified
mapping still emits nothing, a negated instruction is recorded as the
prohibition it is rather than dropped, and an `otherwise` arm with no
resolvable guard emits NOTHING rather than an unconditional mapping -- which
would be a different contract from the one stated.

SCOPE. This lands the extraction half of #2215: the typed, source-grounded
mapping contract the issue asks the shared extractor to retain. The
deterministic generator consumer and the branch-discriminating assertion
emitter that consume this contract are separate components and are NOT in
this change.

chip-AGNOSTIC: no chip, vendor or SKU literal; signal names are synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import spec_numeric_pack_extract as M                    # noqa: E402

BASE = "Convert a 12-bit width to a larger width of 20-bit. "

CONDITIONAL = BASE + (
    "When active is one, result[11] is sample[11] XOR flip. "
    "When active is zero, result[11] equals sample[11]. "
    "Bits result[10:0] always equal sample[10:0]. "
    "If extend is one, bits result[19:12] repeat result[11]; "
    "otherwise those upper bits are zero.")

PRESERVE_ONLY = BASE + (
    "Preserve all twelve source bits. Do not invert any source bit. "
    "The upper eight result bits are zero.")

UNSPECIFIED = ("Implement a configurable data format block; detailed mapping "
               "is not yet supplied.")


def _kind(prompt: str, kind: str) -> list[dict]:
    return [item for item in M.extract(prompt) if item["kind"] == kind]


# --------------------------------------------------------------------------- #
# the two contracts are no longer the same item
# --------------------------------------------------------------------------- #
def test_the_two_contracts_are_distinguishable():
    conditional = [(i["kind"], i["requirement"]) for i in M.extract(CONDITIONAL)]
    preserve = [(i["kind"], i["requirement"]) for i in M.extract(PRESERVE_ONLY)]
    assert conditional != preserve
    assert conditional and preserve


def test_an_unspecified_mapping_invents_nothing():
    """The whole §4.05 point: silence must stay silent."""
    assert M.extract(UNSPECIFIED) == []


def test_the_width_item_is_unchanged():
    """The existing kind keeps its exact shape; this extends, never replaces."""
    for prompt in (CONDITIONAL, PRESERVE_ONLY):
        widths = _kind(prompt, "width_convert")
        assert len(widths) == 1
        assert widths[0]["in_width"] == 12 and widths[0]["out_width"] == 20


# --------------------------------------------------------------------------- #
# the conditional contract, field by field
# --------------------------------------------------------------------------- #
def test_a_control_conditioned_transform_keeps_its_condition_and_operand():
    hit = [i for i in _kind(CONDITIONAL, "bit_mapping")
           if i["transform"] == "xor"]
    assert len(hit) == 1
    item = hit[0]
    assert item["target"] == {"signal": "result", "hi": 11, "lo": 11, "width": 1}
    assert item["source"] == {"signal": "sample", "hi": 11, "lo": 11, "width": 1}
    assert item["operand"] == "flip"
    assert item["condition"]["signal"] == "active"
    assert item["condition"]["value"] == 1
    assert "XOR flip" in item["evidence"]


def test_the_complementary_branch_is_its_own_mapping():
    hit = [i for i in _kind(CONDITIONAL, "bit_mapping")
           if i["transform"] == "identity"
           and i["target"]["hi"] == 11 and i["target"]["lo"] == 11]
    assert len(hit) == 1
    assert hit[0]["condition"] == {"signal": "active", "value": 0,
                                   "evidence": "When active is zero"}


def test_an_always_clause_is_recorded_as_unconditional():
    hit = [i for i in _kind(CONDITIONAL, "bit_mapping")
           if i["target"]["hi"] == 10 and i["target"]["lo"] == 0]
    assert len(hit) == 1
    assert hit[0]["condition"] is None
    assert hit[0]["source"] == {"signal": "sample", "hi": 10, "lo": 0,
                                "width": 11}


def test_conditional_extension_keeps_its_source_bit():
    hit = [i for i in _kind(CONDITIONAL, "bit_mapping")
           if i["transform"] == "replicate"]
    assert len(hit) == 1
    assert hit[0]["target"] == {"signal": "result", "hi": 19, "lo": 12,
                                "width": 8}
    assert hit[0]["source"]["signal"] == "result" and hit[0]["source"]["hi"] == 11
    assert hit[0]["condition"] == {"signal": "extend", "value": 1,
                                   "evidence": "If extend is one"}


def test_the_otherwise_arm_carries_the_complement_never_no_condition():
    """An else-arm recorded unconditionally is a DIFFERENT contract."""
    hit = [i for i in _kind(CONDITIONAL, "bit_mapping")
           if i["transform"] == "zero"]
    assert len(hit) == 1
    assert hit[0]["condition"]["signal"] == "extend"
    assert hit[0]["condition"]["value"] == 0


def test_an_otherwise_with_no_resolvable_guard_emits_nothing():
    """Silence beats a guessed guard."""
    assert _kind("Otherwise those upper bits are zero.", "bit_mapping") == []


# --------------------------------------------------------------------------- #
# the preserve-only contract
# --------------------------------------------------------------------------- #
def test_preservation_and_its_prohibition_are_both_recorded():
    rules = _kind(PRESERVE_ONLY, "bit_preserve")
    assert len(rules) == 2
    preserve = [r for r in rules if r["preserved_width"] == 12]
    prohibit = [r for r in rules if r["prohibited_transforms"] == ["invert"]]
    assert len(preserve) == 1 and len(prohibit) == 1
    assert "invert" in prohibit[0]["evidence"].lower()


def test_the_preserve_contract_declares_no_conditional_transform():
    """The difference that was being lost: no XOR, no invert, no condition."""
    mappings = _kind(PRESERVE_ONLY, "bit_mapping")
    assert all(i["transform"] == "zero" for i in mappings)
    assert all(i["condition"] is None for i in mappings)


def test_an_upper_zero_fill_keeps_its_stated_width():
    zeros = [i for i in _kind(PRESERVE_ONLY, "bit_mapping")
             if i["transform"] == "zero"]
    assert len(zeros) == 1
    assert zeros[0]["target"]["width"] == 8
    assert zeros[0]["target"]["position"] == "upper"


def test_a_prohibition_is_not_read_as_an_instruction_to_invert():
    assert not [i for i in _kind(PRESERVE_ONLY, "bit_mapping")
                if i["transform"] == "invert"]


# --------------------------------------------------------------------------- #
# generality: the same path, different names and widths
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("target, source, ctrl, operand", [
    ("out_word", "in_word", "enable", "mask"),
    ("q", "d", "sel", "toggle"),
    ("payload", "raw", "mode_bit", "key"),
])
def test_renaming_every_signal_uses_the_same_path(target, source, ctrl, operand):
    prompt = (f"When {ctrl} is one, {target}[7] is {source}[7] XOR {operand}. "
              f"When {ctrl} is zero, {target}[7] equals {source}[7].")
    items = _kind(prompt, "bit_mapping")
    assert len(items) == 2
    assert {i["condition"]["value"] for i in items} == {0, 1}
    assert all(i["target"]["signal"] == target for i in items)
    assert all(i["source"]["signal"] == source for i in items)


@pytest.mark.parametrize("hi, lo", [(31, 16), (3, 0), (63, 32)])
def test_multiple_legal_widths_use_the_same_path(hi, lo):
    prompt = f"Bits result[{hi}:{lo}] always equal sample[{hi}:{lo}]."
    items = _kind(prompt, "bit_mapping")
    assert len(items) == 1
    assert items[0]["target"]["width"] == hi - lo + 1


def test_a_reversed_slice_is_normalized_not_refused():
    items = _kind("Bits result[0:7] always equal sample[0:7].", "bit_mapping")
    assert items[0]["target"] == {"signal": "result", "hi": 7, "lo": 0,
                                  "width": 8}


# --------------------------------------------------------------------------- #
# refusals: what must NOT produce a contract
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("prompt", [
    "The result tracks the sample.",                     # no bit references
    "When active is one, the result changes.",           # condition, no mapping
    "result[3] is sample[3] XOR",                        # no operand
    "When active is maybe, result[3] is sample[3].",     # unparseable polarity
])
def test_an_incomplete_statement_produces_no_mapping(prompt):
    assert _kind(prompt, "bit_mapping") == []


def test_a_signal_name_alone_establishes_nothing():
    """A mapping must say WHICH bits; a bare name is not a reference."""
    assert _kind("result is sample XOR flip.", "bit_mapping") == []


def test_every_item_carries_the_clause_it_was_read_from():
    for prompt in (CONDITIONAL, PRESERVE_ONLY):
        for item in M.extract(prompt):
            assert item["evidence"], item
            normalized = " ".join(prompt.split())
            assert " ".join(item["evidence"].split()) in normalized, item
