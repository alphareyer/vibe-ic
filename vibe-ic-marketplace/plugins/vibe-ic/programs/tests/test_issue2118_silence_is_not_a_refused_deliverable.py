"""vibe-ic#2118 — `_effective_deliverable`'s whole truth table.

RE-PINNED TO R-0915-95 (icrev1, 2026-09-21), NOT WEAKENED. This file was
written to restore the derivation for one case -- a declaration that EXISTS and
that NOBODY HAS ANSWERED YET -- on the ground that such a template is neither
unreadable nor unattested, so #2376 had "reached one case too far". The owner
ruling it collided with is the one that took `deliverable` off the derivable
list altogether, and the collision was not seen when this landed: this file's
own commit message dates the counterpart test file to f5a237d21 (2026-09-07),
"thirteen days before the landing that reddened it", while
`test_hardmacro_delivery_ships_no_die_seal_ring.py` had in fact been re-pinned
to the ruling seventeen hours earlier, in 58ca26548 (2026-09-20 23:11). Since
then the tree has failed its own rule: two landed tests asserted opposite
values for one call, and the census carries it as cause C11
(`assert 'HARDMACRO' is None`).

WHAT THE RE-MEASUREMENT SHOWED, on cbca058b3, 8HD-d, image 0.3.67:

  * THE GROUND DOES NOT HOLD. `derived` at the publisher's call site is
    `_declared_deliverable`, whose only surviving authority is
    `_ppa/delivery_path` -- a route read from WHICH ROUTER ARTEFACT IS ON DISK.
    Under a silent declaration the value handed back is therefore the old
    router marker itself, which is the one thing #2376 exists to refuse. Driven
    directly: silent declaration with no router artefact -> (None, "the
    delivery route is NOT_DETERMINED"); the same tree plus
    `input/submission_template/slots/*.yaml` -> DIE; plus
    `input/submission_template/NO_TEMPLATE.txt` -> HARDMACRO. The "fresh
    template" case and the "stale marker" case are the same case.

  * THE STATED CONSEQUENCE DOES NOT ARRIVE. This file's premise was that the
    publisher would then name the size rectangle "on every DIE and every
    HARDMACRO whose declaration had not been hand-answered -- which is the
    normal state of a fresh run". Driven WITH the change in place against a
    real fresh project and the REAL `_declared_deliverable` (the publisher
    case below monkeypatches that function), `published` came back
    `['top_cell']` with `die_area_um` and `macro_area_um` both in
    `not_determined` -- the very symptom the change was for.

WHAT IS KEPT, and it is most of the file: #2376's refusal of an unattested
answer, #2425's refusal of an answer with no provenance map at all, the
unreadable case, and the no-file case. Only the two SILENT-declaration
assertions move, and they move to the ruling.
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


def test_a_template_nobody_answered_is_not_answered_by_the_derivation(tmp_path):
    """R-0915-95: silence is not an answer, and no derivation fills it.

    The fixture checks below are UNCHANGED -- this really is the silent shape,
    distinguishable from the two refusals -- and only the verdict moves. Under
    a silent declaration the `derived` value can only have come from a router
    artefact on disk, so returning it is returning the old marker.
    """
    _silent(tmp_path)
    assert td.answer(td.load(tmp_path / td.DECLARATION_REL)[0],
                     "deliverable") == td.NOT_DETERMINED
    assert (td.attestation_of(td.load(tmp_path / td.DECLARATION_REL)[0],
                              "deliverable")["answered_by"]
            == td.ANSWERED_BY_MISSING), "the fixture is not silent"
    assert r._effective_deliverable(tmp_path, _DERIVED) is None
    # BOTH derivations, so this cannot pass for a function that merely ignores
    # one particular value.
    assert r._effective_deliverable(tmp_path, _OTHER) is None
    assert r._effective_deliverable(tmp_path, None) is None


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
def test_a_silent_declaration_gets_NEITHER_name_even_with_a_route_stated(
        tmp_path, monkeypatch):
    """The consequence at the publisher, and it is the sibling file's own rule.

    That file's header already says an undeclared deliverable "publishes
    NEITHER -- choosing one name would be a guess wearing a derivation's
    clothes", and the publisher says the same thing in a comment at the call
    site. This pins it against the strongest available contrary input: the
    route is STATED through the `_declared_deliverable` mock and the
    declaration is still silent, so nothing but the declaration can settle it.

    The declared arms of that rule -- a HARDMACRO publishing `macro_*` and a
    DIE publishing `die_*` -- are in the sibling file and are unchanged; they
    now declare the deliverable in the fixture instead of relying on the mock.
    """
    from test_issue2118_a_hardmacro_has_no_die_to_declare import (
        _publisher_project, _publish)
    project = _publisher_project(tmp_path)          # silent, deliverable=None
    rec = _publish(project, monkeypatch, td.DELIVERABLE_HARDMACRO)
    for key in ("macro_area_um", "macro_origin_um",
                "die_area_um", "die_origin_um"):
        assert key not in rec["published"], rec["published"]
        assert "not known" in rec["not_determined"][key], rec["not_determined"]
