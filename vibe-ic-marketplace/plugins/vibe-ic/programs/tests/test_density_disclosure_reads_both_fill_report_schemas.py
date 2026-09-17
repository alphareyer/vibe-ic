"""The integrator's density disclosure carries NUMBERS, or names what it lacks.

MEASURED, lane czsubdrc 2026-09-07 on 8HD-4, base e2b3c08170b5 (v1.19.43),
against the image this tree pins (`0.3.49`,
sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49) and
the `subservient` front-door run's own artefacts:

    die_level_rule_attribution.density_disclosure  (before)
      M2.4  layer metal2  achieved 0.1877  floor None  legal_ceiling None
      M3.4  layer metal3  achieved 0.2042  floor None  legal_ceiling None
                                                (after)
      M2.4  layer metal2  achieved 0.1877  floor 0.3   legal_ceiling 0.318139
      M3.4  layer metal3  achieved 0.2042  floor 0.3   legal_ceiling 0.331869

WHY THE NUMBERS WERE ABSENT. `metal_fill_emit` stages its engine through
`_klayout_launch.find_engine`, whose order puts an engine baked into the
container image AHEAD of the copy vendored beside the plugin. The pinned image
carries `metal-fill/metal_fill.py` at that path — the hyphen spelling
`_subdir_spellings` tries — so the override resolves and the image's engine
runs. It is 390 lines to the vendored copy's 681 and predates vibe-ic#2135:
`ceiling_any_fill`, `free_frac` and the per-layer `floor` appear in it zero
times. Falling through to an OLDER engine is silent by construction —
`find_engine` names a miss only when the override carries no such engine at
all. `metal_fill_emit` compensates by attaching a `capacity` block, which it
does precisely WHEN a layer is below the floor — which is every run this
disclosure exists for — but the disclosure read only the engine-native
spelling and published `None` for both figures.

WHY IT MATTERS. This record is `integrator_requirements.json`, and it travels
with the macro. The legal ceiling is its load-bearing number: below the floor
it is the sentence "this cannot be closed inside the macro either". An
integrator handed a shortfall without it is handed a number they cannot act
on, and a bare `None` there reads as "there is no such limit" rather than as
"nobody looked".

The schemas here are the two REAL shapes, with PDK-agnostic layer names: no
foundry, PDK, vendor, design or rule-name literal appears in this file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _owner_declared as _OD                              # noqa: E402
import die_level_deck_rule_attribution as D  # noqa: E402

DIE = [0.0, 0.0, 100.0, 100.0]
DIE_POLY = "(0,0;0,100;100,100;100,0)"

#: The deck excerpt: one die-scope coverage rule per layer, gated on the
#: deck's own whole-die area scalar, in the shape `die_area_identifiers`
#: reads. Nothing here is a real rule name.
DECK = f"""{D.FILE_SEP}/deck/density.rb
CHIP = extent.sized(0.0)
chip_area = CHIP.area

# Rule COV1.a: layer_one coverage over the entire die shall be > 30%
l1_ratio = layer_one.area / chip_area
l1_ratio.output("COV1.a", "COV1.a")

# Rule COV2.a: layer_two coverage over the entire die shall be > 30%
l2_ratio = layer_two.area / chip_area
l2_ratio.output("COV2.a", "COV2.a")
"""


def _rdb(rules):
    body = "".join(
        f"<item><category>'{r}'</category><values>"
        f"<value>polygon: {DIE_POLY}</value></values></item>" for r in rules)
    return f"<report-database>{body}</report-database>"


def _capacity_schema():
    """What the PINNED image's engine produces: no per-layer `floor` and no
    `ceiling_any_fill`, the pair carried only by the `capacity` block."""
    return {
        "verdict": "PARTIAL",
        "keepout": {"measurement_bbox_um": list(DIE)},
        "layers": [
            {"name": "layer_one", "density_after": 0.1877,
             "worst_window_after": 0.1877},
            {"name": "layer_two", "density_after": 0.2042,
             "worst_window_after": 0.2042},
        ],
        "capacity": {
            "program": "_metal_fill_capacity",
            "floor": 0.30,
            "layers": [
                {"name": "layer_one", "drawn_frac": 0.09477,
                 "free_frac": 0.223368, "absolute_ceiling": 0.318139,
                 "lattice_ceiling": 0.228832, "floor": 0.30,
                 "reachable_by_lattice": False},
                {"name": "layer_two", "drawn_frac": 0.105671,
                 "free_frac": 0.226198, "absolute_ceiling": 0.331869,
                 "lattice_ceiling": 0.24143, "floor": 0.30,
                 "reachable_by_lattice": False},
            ],
        },
    }


def _engine_native_schema():
    """What the VENDORED post-#2135 engine produces: the pair on the layer."""
    return {
        "verdict": "PARTIAL",
        "floor": 0.30,
        "keepout": {"measurement_bbox_um": list(DIE)},
        "layers": [
            {"name": "layer_one", "density_after": 0.1877,
             "worst_window_after": 0.1877, "floor": 0.30,
             "free_frac": 0.223368, "ceiling_any_fill": 0.318139},
            {"name": "layer_two", "density_after": 0.2042,
             "worst_window_after": 0.2042, "floor": 0.30,
             "free_frac": 0.226198, "ceiling_any_fill": 0.331869},
        ],
    }


def _neither_schema():
    """A report that states the achieved density and nothing else — the case
    that must be NAMED rather than defaulted."""
    return {
        "verdict": "PARTIAL",
        "keepout": {"measurement_bbox_um": list(DIE)},
        "layers": [
            {"name": "layer_one", "density_after": 0.1877,
             "worst_window_after": 0.1877},
        ],
    }


def _project(tmp_path, name, deliverable=None):
    deliverable = deliverable or D.DELIVERABLE_HARDMACRO
    proj = tmp_path / name
    (proj / "input" / "submission_template").mkdir(parents=True)
    (proj / "input" / "submission_template" /
     "tapeout_declaration.json").write_text(json.dumps(_OD.attest(
         {"schema": "vibe-ic/tapeout_declaration/1",
          "answers": {"deliverable": deliverable}})))
    (proj / "reports" / "phase3").mkdir(parents=True)
    return proj


def _disclosure(proj, report, rules=("COV1.a", "COV2.a")):
    rec = D.run(Path(proj), {r: 1 for r in rules}, DECK, None,
                _rdb(rules), report)
    return rec, {d["rule"]: d for d in rec["density_disclosure"]}


# ---------------------------------------------------------------------------
# 1. The capacity block IS a source of the pair.
# ---------------------------------------------------------------------------

def test_the_capacity_block_carries_the_floor_and_the_legal_ceiling(tmp_path):
    """The schema the pinned image's engine actually produces. Before the fix
    both figures came back `None` on this exact input."""
    proj = _project(tmp_path, "cap")
    rec, by_rule = _disclosure(proj, _capacity_schema())

    assert rec["verdict"] == D.DENSITY_ATTRIBUTED
    one, two = by_rule["COV1.a"], by_rule["COV2.a"]

    assert one["layer"] == "layer_one" and two["layer"] == "layer_two"
    assert one["achieved"] == 0.1877 and two["achieved"] == 0.2042
    assert one["floor"] == 0.30, (
        f"the capacity block states this layer's floor; the disclosure "
        f"published {one['floor']!r}")
    assert two["floor"] == 0.30
    assert one["legal_ceiling"] == 0.318139, (
        f"`absolute_ceiling` is the same quantity the engine-native schema "
        f"spells `ceiling_any_fill` (both are drawn + the whole free region); "
        f"the disclosure published {one['legal_ceiling']!r}")
    assert two["legal_ceiling"] == 0.331869
    # A figure that IS stated must not also be called unknown.
    assert "not_measured" not in one and "not_measured" not in two


def test_the_shortfall_is_disclosed_as_not_closable_inside_the_macro(tmp_path):
    """The one inference the integrator needs, and it needs BOTH numbers to
    make it: achieved is under the floor, and the legal ceiling is barely
    over it, so the macro's own free area cannot carry the difference."""
    proj = _project(tmp_path, "shortfall")
    _rec, by_rule = _disclosure(proj, _capacity_schema())
    for d in by_rule.values():
        assert isinstance(d["floor"], float)
        assert isinstance(d["legal_ceiling"], float)
        assert d["achieved"] < d["floor"] <= d["legal_ceiling"]


def test_the_handoff_record_carries_the_numbers(tmp_path):
    """`integrator_requirements` is what travels with the macro. A record whose
    requirements carry no floor is a note, not a requirement."""
    proj = _project(tmp_path, "handoff")
    rec, _ = _disclosure(proj, _capacity_schema())
    ho = D.handoff_record(rec)
    assert ho["record"] == "integrator_requirements"
    assert ho["requirements"], "the handoff lists no requirement at all"
    for req in ho["requirements"]:
        assert req["floor"] != D.NOT_MEASURED and req["floor"] is not None
        assert (req["legal_ceiling"] != D.NOT_MEASURED
                and req["legal_ceiling"] is not None)


# ---------------------------------------------------------------------------
# 2. The engine-native schema is not disturbed.
# ---------------------------------------------------------------------------

def test_the_engine_native_schema_still_wins(tmp_path):
    """The vendored post-#2135 engine's own spelling keeps working, and keeps
    working when a capacity block DISAGREES — the layer's own figure is the
    engine's measurement of what it actually did."""
    proj = _project(tmp_path, "native")
    _rec, by_rule = _disclosure(proj, _engine_native_schema())
    assert by_rule["COV1.a"]["floor"] == 0.30
    assert by_rule["COV1.a"]["legal_ceiling"] == 0.318139

    both = _engine_native_schema()
    both["capacity"] = {"floor": 0.99, "layers": [
        {"name": "layer_one", "absolute_ceiling": 0.99, "floor": 0.99},
        {"name": "layer_two", "absolute_ceiling": 0.99, "floor": 0.99}]}
    proj2 = _project(tmp_path, "both")
    _rec2, by_rule2 = _disclosure(proj2, both)
    assert by_rule2["COV1.a"]["floor"] == 0.30
    assert by_rule2["COV1.a"]["legal_ceiling"] == 0.318139


# ---------------------------------------------------------------------------
# 3. What no schema states is NAMED, never defaulted.
# ---------------------------------------------------------------------------

def test_a_figure_no_schema_states_is_named_not_measured(tmp_path):
    """Before the fix this published `floor: None, legal_ceiling: None` with
    no `not_measured` entry — "nobody looked" wearing "there is no limit"."""
    proj = _project(tmp_path, "neither")
    _rec, by_rule = _disclosure(proj, _neither_schema(), rules=("COV1.a",))
    one = by_rule["COV1.a"]
    assert one["achieved"] == 0.1877, "the figure that IS stated is still read"
    assert one["floor"] == D.NOT_MEASURED
    assert one["legal_ceiling"] == D.NOT_MEASURED
    why = one.get("not_measured")
    assert why, "an absent figure was published without naming that it is absent"
    # NAME WHAT WAS LOOKED FOR, not merely that it was not found.
    assert "layer_one" in why
    assert "floor" in why and "legal ceiling" in why
    assert "ceiling_any_fill" in why and D._CAPACITY_CEILING_KEY in why


def test_none_is_never_published_for_either_figure(tmp_path):
    """The membership property, across all three schemas: whatever the report
    says, neither figure ever arrives as a bare `None`."""
    for i, rep in enumerate((_capacity_schema(), _engine_native_schema(),
                             _neither_schema())):
        rules = ("COV1.a",) if rep is not None and len(
            rep["layers"]) == 1 else ("COV1.a", "COV2.a")
        proj = _project(tmp_path, f"none_{i}")
        _rec, by_rule = _disclosure(proj, rep, rules=rules)
        for d in by_rule.values():
            assert d["floor"] is not None, d
            assert d["legal_ceiling"] is not None, d


# ---------------------------------------------------------------------------
# 4. The reader itself, in isolation.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False, "0.3", None, [], {}])
def test_a_non_number_is_not_a_measurement(bad):
    """`True` is an `int` in Python. A floor of 1.0 minted from a flag would
    be a supplied value wearing a measurement's clothes."""
    assert D._number(bad) is None


def test_the_first_stated_number_wins_and_absent_is_skipped():
    assert D._first_number(None, None, 0.30, 0.99) == 0.30
    # 0.0 is a number, not an absence
    assert D._first_number(None, 0.0, 0.5) == 0.0
    assert D._first_number(None, "x", True, []) is None


def test_capacity_rows_index_by_name_and_never_invent_one():
    rows, floor = D.capacity_rows(_capacity_schema())
    assert sorted(rows) == ["layer_one", "layer_two"]
    assert floor == 0.30
    assert D.capacity_rows({}) == ({}, None)
    assert D.capacity_rows({"capacity": "not a mapping"}) == ({}, None)
    assert D.capacity_rows({"capacity": {"layers": [{"no": "name"}]}}) == (
        {}, None)
