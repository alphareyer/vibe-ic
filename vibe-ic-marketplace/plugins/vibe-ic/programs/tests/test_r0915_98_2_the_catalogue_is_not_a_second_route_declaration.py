"""R-0915-98(2): a retained CATALOGUE is not a second route declaration.

MEASURED on subservient x gf180mcuD r48 (`_lane_icsub2/c24_proj`), an
owner-attested DIE. Step 0.5ic PASSES and writes BOTH router files: its own
`SELF_TAPEOUT.txt` and the operator's `slots/*.yaml` catalogue — four files, the
same mtime, the same step. Listed as three FLAT alternatives under
`exactly_one: true`, the condition-owner predicate matched TWO and booked::

    delivery_route declaration is CONFLICTING: matched 2 mutually exclusive
    alternatives ['input/submission_template/slots/*.yaml',
                  'input/submission_template/SELF_TAPEOUT.txt']

which put 15.5ic, 26.5ic, 37.5ip and 37.5ic on
`blocked-by-upstream(0.5ic)` / FAIL / missing_artefact — the DIE's entire phase-3
entry — on a route the design had declared unambiguously.

WHICH SIDE WAS WRONG: the READER. The co-existence is BY DESIGN and three places
in the tree already said so before this change:

  * R-0915-98(2) — the slot files on disk are the PDK's LIVE CATALOGUE:
    information, not a purchase. `slot_pad_budget_check` is built on that.
  * `tapeout_declaration_gen`'s own docstring — "37.5ic's condition is
    `slots/*.yaml` OR `SELF_TAPEOUT.txt`, `any_of`" — and it retires only
    `NO_TEMPLATE.txt` on a self tape-out, because IP and CHIP are what cannot
    both be true.
  * `_tapeout_declaration.route_of` — which ORDERS the two chip markers instead
    of calling them a conflict: slot files retain the operator obligation, and
    an owner-consistent DIE with an explicit operator absence distinguishes a
    retained catalogue from a purchase.

Only the `condition_declarations.delivery_route` predicate re-derived the route
from FILE PRESENCE alone, and it was the one that disagreed. The exclusivity it
should express is IP-TERMINAL vs CHIP-PATH, never between the two chip markers.

THE FIX IS TWO HALVES THAT NOW AGREE ON ONE RULE:
  * `files_exist` learns the `" OR "` dialect the rest of this flow definition
    already speaks, through `_declared_output_branches` — the ONE splitter
    `required_outputs` uses. Step 0.5ic's own `required_outputs` line already
    wrote `slots/*.yaml OR NO_TEMPLATE.txt OR SELF_TAPEOUT.txt` and this reader
    could not read it: MEASURED, `_glob_first` returns `[]` for the joined form
    on a project where both named files exist, because it globbed the whole
    string as one literal.
  * the yaml states the chip path as ONE alternative with two accepted
    spellings, so `exactly_one` counts routes rather than files.

THE GUARD IS NOT LAUNDERED. A project carrying the IP terminal AND either chip
marker still matches two alternatives and is still CONFLICTING; that is the
third case below, and it is the contradiction the predicate exists for.

chip-AGNOSTIC: the declaration, its alternatives and every path are read from
the shipped flow definition; no chip, PDK or vendor literal appears here.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import flow_compliance_check as F  # noqa: E402

FLOW_YAML = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"

SLOTS = "input/submission_template/slots/1x1.yaml"
SELF_TAPEOUT = "input/submission_template/SELF_TAPEOUT.txt"
NO_TEMPLATE = "input/submission_template/NO_TEMPLATE.txt"

#: The four steps whose phase-3 entry the conflict was blocking.
BLOCKED_STEPS = ("15.5ic", "26.5ic", "37.5ip", "37.5ic")


def _flow():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(FLOW_YAML.read_text())


def _delivery_route_declaration():
    for st in _flow()["steps"]:
        if str(st.get("id")) == "0.5ic":
            return st["condition_declarations"]["delivery_route"]
    raise AssertionError("step 0.5ic declares no delivery_route")


def _project(tmp_path: Path, *rels: str) -> Path:
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# router file\n")
    return tmp_path


def _matched(project: Path):
    """The alternatives the shipped declaration matches, as the reader counts."""
    decl = _delivery_route_declaration()
    return [p for p in decl["files_exist"]
            if F._condition_pattern_satisfied(project, p)]


# --------------------------------------------------------------------------- #
# the OR dialect: one splitter, and it is the one required_outputs uses
# --------------------------------------------------------------------------- #
def test_a_joined_pattern_is_read_as_any_of_its_branches(tmp_path):
    """RED before this change: `_glob_first` globbed the whole joined string as
    one literal and matched nothing, so the grouped alternative could never be
    satisfied and CONFLICTING would have become MISSING."""
    joined = f"{SLOTS} OR {SELF_TAPEOUT}"
    assert F._condition_pattern_satisfied(_project(tmp_path, SLOTS), joined)
    assert F._condition_pattern_satisfied(
        _project(tmp_path / "b", SELF_TAPEOUT), joined)
    assert not F._condition_pattern_satisfied(tmp_path / "empty", joined)


def test_the_branches_come_from_the_flows_own_splitter():
    """One `OR` dialect, not two: this reader must split exactly the way
    `required_outputs` does, or a flow author has to know which key they are
    writing under."""
    joined = f"{SLOTS} OR {SELF_TAPEOUT}"
    assert F._declared_output_branches(joined) == [SLOTS, SELF_TAPEOUT]


def test_an_ordinary_pattern_is_unchanged(tmp_path):
    """A pattern with no ` OR ` keeps plain existence semantics."""
    assert F._condition_pattern_satisfied(_project(tmp_path, SLOTS), SLOTS)
    assert not F._condition_pattern_satisfied(tmp_path, NO_TEMPLATE)


# --------------------------------------------------------------------------- #
# (1) a DIE with SELF_TAPEOUT and the catalogue retained: ONE route
# --------------------------------------------------------------------------- #
def test_a_DIE_that_retained_the_catalogue_declares_ONE_route(tmp_path):
    """The measured r48 shape. RED before this change: 2 matched -> CONFLICTING."""
    project = _project(tmp_path, SELF_TAPEOUT, SLOTS)
    matched = _matched(project)
    assert len(matched) == 1, matched
    assert _delivery_route_declaration()["exactly_one"] is True


def test_the_chip_path_is_one_alternative_with_two_spellings():
    """Stated in the shipped yaml, not inferred here: the chip markers share an
    alternative and the IP terminal is its own."""
    alts = _delivery_route_declaration()["files_exist"]
    assert len(alts) == 2, alts
    chip = [a for a in alts if "SELF_TAPEOUT" in a]
    assert len(chip) == 1
    assert "slots/" in chip[0] and " OR " in chip[0]
    ip = [a for a in alts if "NO_TEMPLATE" in a]
    assert len(ip) == 1 and " OR " not in ip[0]


@pytest.mark.parametrize("only", [SELF_TAPEOUT, SLOTS, NO_TEMPLATE])
def test_each_marker_alone_still_declares_exactly_one_route(tmp_path, only):
    """Every single-marker project keeps the reading it already had."""
    assert len(_matched(_project(tmp_path / only.replace('/', '_'), only))) == 1


# --------------------------------------------------------------------------- #
# (2) the HARDMACRO / purchased-slot readings are unchanged
# --------------------------------------------------------------------------- #
def test_a_purchased_slot_alone_is_still_exactly_one_route(tmp_path):
    """A shuttle design carrying only the operator's slot files is UNCHANGED —
    and that is a claim about the route COUNT, which is what `exactly_one`
    reads. It holds in both arms by construction, which is the point: this fix
    must not move the purchased-slot reading at all.

    (The grouped SPELLING is a separate claim and is asserted by
    `test_the_chip_path_is_one_alternative_with_two_spellings`; mixing the two
    here would have made an unchanged-behaviour test pass only on one arm and
    told a reader the purchased-slot path had moved.)"""
    assert len(_matched(_project(tmp_path, SLOTS))) == 1


def test_the_IP_terminal_alone_is_still_its_own_alternative(tmp_path):
    """ITS OWN, which is the load-bearing word. Measured while proving this
    file: grouping all three markers into ONE alternative left this test green,
    because it only asked that whatever matched mentioned the IP terminal. That
    is not the claim — the claim is that the IP terminal shares its alternative
    with NEITHER chip marker, which is what keeps IP-vs-CHIP a contradiction."""
    matched = _matched(_project(tmp_path, NO_TEMPLATE))
    assert len(matched) == 1, matched
    assert "NO_TEMPLATE" in matched[0]
    assert "SELF_TAPEOUT" not in matched[0]
    assert "slots/" not in matched[0]


# --------------------------------------------------------------------------- #
# (3) the guard is NOT laundered away: a real contradiction still CONFLICTS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("second", [SELF_TAPEOUT, SLOTS])
def test_the_IP_terminal_beside_a_chip_marker_is_STILL_conflicting(
        tmp_path, second):
    """IP and CHIP are what cannot both be true, and that is the contradiction
    this predicate exists for. It must survive the fix."""
    project = _project(tmp_path / second.replace('/', '_'),
                       NO_TEMPLATE, second)
    assert len(_matched(project)) == 2


def test_a_project_declaring_no_route_at_all_is_still_MISSING(tmp_path):
    """"Nobody declared" is not "one route": an empty project matches nothing,
    which the reader reports as MISSING rather than as a route."""
    assert _matched(tmp_path) == []


# --------------------------------------------------------------------------- #
# the four steps: they name this declaration, and that wiring is the premise
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sid", BLOCKED_STEPS)
def test_each_blocked_step_names_this_declaration_as_its_owner(sid):
    """If a step stopped naming it, this fix would silently stop reaching that
    step — so the wiring is asserted, not assumed."""
    for st in _flow()["steps"]:
        if str(st.get("id")) == sid:
            owner = st.get("condition_owner") or {}
            assert str(owner.get("step")) == "0.5ic", (sid, owner)
            assert owner.get("declaration") == "delivery_route", (sid, owner)
            return
    raise AssertionError(f"step {sid} is not in the flow definition")
