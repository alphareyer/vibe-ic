"""vibe-ic#2118 — `_effective_deliverable`'s whole truth table.

THE DEFECT. c34f56d2a (v1.22.13, #2376, 2026-09-20) replaced this resolver's
trailing `return derived` with `return None` and added an early
`if not path.exists(): return derived`, so the derivation survived only for a
MISSING declaration file. Its purpose was sound -- "an unreadable or unattested
declaration must not regain its refused value through an old router marker" --
but it reached one case too far: a declaration template that EXISTS and that
NOBODY HAS ANSWERED YET is neither unreadable nor unattested, and there is no
refused value to regain.

WHAT THAT COST, measured on this tree before the fix: for a fresh project
`publish_tapeout_declarations` derived `deliverable` and PUBLISHED it, then said
of the size rectangle in the very same call

    die_area_um   `deliverable` is NOT_DETERMINED, so whether this run's
                  rectangle is a die or a macro is not known and neither name
                  is published
    macro_area_um (the same)

so `published` came back as ['deliverable', 'top_cell'] and `KLayout.CheckSize`
was starved of both fields it needs, on every DIE and every HARDMACRO whose
declaration had not been hand-answered.

WHY `attestation_of` AND NOT `is_answered`. `answer()` returns NOT_DETERMINED
for BOTH silence and an owner answer with no citation, so `is_answered` alone
cannot tell the fresh template from the value #2376 refuses. `attestation_of` is
this tree's ONE reader of "who answered and does that count" and it separates
them: `ANSWERED_BY_MISSING` is silence; anything else with `declares` False is
an answer that does not declare, and that one still refuses. `raw_answer` shows
the difference too and says of itself that it is for disclosure only and never
for a decision, so it is not used.
"""
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _tapeout_declaration as td            # noqa: E402
import phase3_one_shot_runner as r           # noqa: E402

_DERIVED = td.DELIVERABLE_HARDMACRO
_OTHER = td.DELIVERABLE_DIE


def _write(project: Path, doc) -> None:
    p = project / td.DECLARATION_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=1))


def r_effective(project: Path):
    """The resolver under test, with a DERIVED value always available -- so a
    `None` result can only mean the declaration refused, never that there was
    nothing to fall back to."""
    return r._effective_deliverable(project, _DERIVED)


def _silent(project: Path) -> None:
    _write(project, td.blank_declaration())


def _answered(project: Path, value: str, *, attested: bool) -> None:
    doc = td.blank_declaration()
    doc.setdefault("answers", {})["deliverable"] = value
    prov = doc.setdefault(td.PROVENANCE_KEY, {})
    prov["deliverable"] = ({"answered_by": td.ANSWERED_BY_OWNER_VALUE,
                            "citation": "the owner's mail of 2026-09-21"}
                           if attested else
                           {"answered_by": td.ANSWERED_BY_OWNER_VALUE})
    _write(project, doc)


# ── the five cases, and each one moves the answer ───────────────────────────
def test_no_declaration_file_uses_the_derivation(tmp_path):
    """#2376's own early exit, kept."""
    assert r._effective_deliverable(tmp_path, _DERIVED) == _DERIVED


def test_a_template_nobody_answered_uses_the_derivation(tmp_path):
    """THE RED. Silence is not a refused value."""
    _silent(tmp_path)
    assert td.answer(td.load(tmp_path / td.DECLARATION_REL)[0],
                     "deliverable") == td.NOT_DETERMINED
    assert (td.attestation_of(td.load(tmp_path / td.DECLARATION_REL)[0],
                              "deliverable")["answered_by"]
            == td.ANSWERED_BY_MISSING), "the fixture is not silent"
    assert r._effective_deliverable(tmp_path, _DERIVED) == _DERIVED


def test_an_attested_answer_outranks_the_derivation(tmp_path):
    """The declaration's own answer wins; the derivation is only a fall-back."""
    _answered(tmp_path, _OTHER, attested=True)
    assert r._effective_deliverable(tmp_path, _DERIVED) == _OTHER


def test_an_unattested_answer_does_not_regain_its_value(tmp_path):
    """#2376's REFUSAL, which this change must not weaken: an owner answer with
    no citation is refused, and the router-derived value must NOT stand in for
    it -- that substitution is the exact thing #2376 closed."""
    _answered(tmp_path, _OTHER, attested=False)
    got = r._effective_deliverable(tmp_path, _DERIVED)
    assert got is None, (
        f"an unattested {_OTHER} declaration regained a value as {got!r}; "
        f"#2376 closed exactly this path")


def test_an_answer_with_no_provenance_at_all_is_refused_not_silent(tmp_path):
    """THE SHAPE THIS FILE MISSED, and #2425 is the red that found it.

    A declaration carrying an ANSWER and no provenance map whatsoever. My first
    fix asked `attestation_of(...)["answered_by"] == ANSWERED_BY_MISSING`, which
    is a question about the PROVENANCE MAP and not about whether an answer
    exists -- so this shape reported `missing`, was read as silence, and the
    value regained its authority through the router marker. That is exactly the
    hole #2376 had closed, reopened from the other side.

    `owner_attestation_refusals` is the reader that draws the line where the
    module's own words do, and it names this one: a value that LOOKS like a
    declaration and was not declared by anybody entitled to declare it.
    """
    _write(tmp_path, {"answers": {"deliverable": _OTHER}})
    doc, err = td.load(tmp_path / td.DECLARATION_REL)
    assert err is None
    # the fixture really is the shape that fooled the first predicate
    assert (td.attestation_of(doc, "deliverable")["answered_by"]
            == td.ANSWERED_BY_MISSING), "fixture no longer reproduces #2425"
    assert td.answer(doc, "deliverable") == td.NOT_DETERMINED
    # and the module DOES call it a refusal, by name, with its remedy
    refusals = td.owner_attestation_refusals(doc)
    assert [r["key"] for r in refusals] == ["deliverable"], refusals
    assert "NOT_DECLARED: deliverable" in refusals[0]["message"]
    # so the resolver must refuse it and NOT hand back the derivation
    assert r_effective(tmp_path) is None, (
        "an unattested answer regained its value through the router marker")


def test_an_unreadable_declaration_refuses(tmp_path):
    """Fail-closed, unchanged."""
    _write(tmp_path, "{ this is not JSON")
    assert r._effective_deliverable(tmp_path, _DERIVED) is None


# ── and the consequence the publisher cares about ───────────────────────────
def test_a_silent_declaration_still_gets_its_size_rectangle(tmp_path,
                                                            monkeypatch):
    """The end the resolver is a means to: with the deliverable derived, the
    publisher names the rectangle instead of declining both names."""
    from test_issue2118_a_hardmacro_has_no_die_to_declare import (
        _publisher_project, _publish)
    project = _publisher_project(tmp_path)
    rec = _publish(project, monkeypatch, td.DELIVERABLE_HARDMACRO)
    assert "macro_area_um" in rec["published"], rec["not_determined"]
    assert "die_area_um" in rec["not_determined"]
    why = rec["not_determined"]["die_area_um"]
    assert "2118" in why and td.DELIVERABLE_HARDMACRO in why, why
