"""W9 (`--librelane` v1): 0.5ic under a flag -- the declaration stays the owner's.

Decision 6: the tape-out declaration is vibe-ic's and owner-answered,
"NOT_DETERMINED, never a default". Contracts:
  1. Under a non-default mode NOTHING is written into the declaration: the
     real `publish_tapeout_declarations` records its derived answers as
     APPLIED in the mode record (per question, with its basis), and the
     declaration's bytes do not move. The default flow publishes as before.
  2. `record_applied` never records over a declared answer, keeps a recorded
     value, refuses a different one (IMPL_APPLIED_CONFLICT), and writes
     nothing without a non-default record.
  3. `record_applied_config` records a resolved config's die/core/sizing with
     the config provenance's own source, and skips what the declaration gave.
  4. `applied_answer`: the declaration first; under the flag only, the
     applied value; else NOT_DETERMINED with the reason.
  5. Step 37.3's finishing core: the declared core; under the flag the
     applied one; neither is LL_FINISHING_CORE_UNDECLARED.
  6. (needs D1 on main: `librelane_contract._apply_runner_floorplan`) the Chip
     DIE_AREA/CORE_AREA the auto-die gives a PadRing chain are recorded as
     applied with the floorplan record as their source; the declaration's
     die/core stay NOT_DETERMINED.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import _impl_flow as IF  # noqa: E402
import _owner_declared as _OD  # noqa: E402
import _tapeout_declaration as td  # noqa: E402

_DEF = ("VERSION 5.8 ;\nDESIGN spm ;\nUNITS DISTANCE MICRONS 2000 ;\n"
        "DIEAREA ( 762000 762000 ) ( 4800000 4800000 ) ;\nEND DESIGN\n")


class _Pdk:
    tech_lef = "/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_sc/tech.lef"
    tech_lef_source = None
    cell_lef = ""
    liberty = ""
    cell_gds = ""


def _r():
    import phase3_one_shot_runner as r
    return r


def _project(tmp_path: Path, name: str = "proj") -> Path:
    r = _r()
    p = tmp_path / name
    (p / "input" / "submission_template").mkdir(parents=True)
    doc = td.blank_declaration()
    doc["answers"]["deliverable"] = td.DELIVERABLE_DIE
    _OD.attest(doc)
    (p / td.DECLARATION_REL).write_text(json.dumps(doc, indent=2))
    (p / "phase3" / "stage3" / "pnr").mkdir(parents=True)
    (p / "phase3" / "stage3" / "pnr" / "routed.def").write_text(_DEF)
    r._floorplan_rectangles_record(
        p, die_rect=[0, 0, 3162, 3162], fp_rect=[381, 381, 2400, 2400],
        die_source="the run's auto-die", core_pad=381, ring_inset_um=380.4)
    return p


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def no_seal(monkeypatch):
    monkeypatch.setattr(_r(), "_docker_exec", lambda *a, **k: (1, "", "no such"))


def test_under_the_flag_the_publisher_records_and_never_writes(tmp_path, no_seal):
    r = _r()
    default = _project(tmp_path, "default")
    flagged = _project(tmp_path, "flagged")
    IF.write_record(flagged, "librelane", resolved_by="test")
    decl = flagged / td.DECLARATION_REL
    before = _sha(decl)

    rec_d = r.publish_tapeout_declarations(
        default, _Pdk(), "c", default / "phase3/stage3/pnr/routed.def", "spm")
    rec_f = r.publish_tapeout_declarations(
        flagged, _Pdk(), "c", flagged / "phase3/stage3/pnr/routed.def", "spm")

    # The default flow publishes into the declaration, exactly as before.
    assert {"top_cell", "die_area_um", "core_area_um"} <= set(rec_d["published"])
    assert not IF.record_path(default).exists()
    # Under the flag: nothing published, the declaration's bytes unchanged...
    assert rec_f["published"] == []
    assert _sha(decl) == before
    doc, _ = td.load(decl)
    for key in ("top_cell", "die_area_um", "core_area_um"):
        assert td.answer(doc, key) == td.NOT_DETERMINED
    assert "decision 6" in rec_f["not_published_reason"]
    # ...and the same derivations are recorded as APPLIED, with their basis.
    assert set(rec_f["recorded_as_applied"]) == set(rec_d["published"])
    applied = IF.read_record(flagged)["tool_defaults"]
    assert applied["die_area_um"]["value"] == [0, 0, 3162, 3162]
    assert applied["core_area_um"]["value"] == [381, 381, 2400, 2400]
    assert "the run's own" in applied["core_area_um"]["source"]
    assert applied["top_cell"]["recorded_by"].endswith(
        "publish_tapeout_declarations")


def _flagged(tmp_path):
    p = _project(tmp_path)
    IF.write_record(p, "librelane", resolved_by="test")
    return p


def test_record_applied_rules(tmp_path):
    p = _flagged(tmp_path)
    ans = {"core_area_um": {"value": [1, 1, 9, 9], "source": "auto-die"},
           "deliverable": {"value": "HARDMACRO", "source": "tool default"}}
    # deliverable is DECLARED (DIE): never recorded over the owner's answer.
    assert IF.record_applied(p, ans, recorded_by="t") == ["core_area_um"]
    assert "deliverable" not in IF.read_record(p)["tool_defaults"]
    assert IF.record_applied(p, ans, recorded_by="t2") == []      # kept
    with pytest.raises(IF.ImplRefusal) as ei:
        IF.record_applied(p, {"core_area_um": {"value": [2, 2, 8, 8],
                                               "source": "other"}},
                          recorded_by="t")
    assert ei.value.reason_class == IF.IMPL_APPLIED_CONFLICT
    before = IF.record_path(p).read_bytes()
    with pytest.raises(IF.ImplRefusal, match="applied 'die_area_um' needs a "
                                             "value and a source"):
        IF.record_applied(p, {"die_area_um": {"value": [0, 0, 9, 9],
                                              "source": ""}}, recorded_by="t")
    assert IF.record_path(p).read_bytes() == before


def test_the_default_records_nothing(tmp_path):
    p = _project(tmp_path)
    assert IF.record_applied(
        p, {"core_area_um": {"value": [1, 1, 9, 9], "source": "x"}},
        recorded_by="t") == []
    assert not IF.record_path(p).exists()


def test_record_applied_config_skips_what_the_declaration_gave(tmp_path):
    p = _flagged(tmp_path)
    config = {"DESIGN_NAME": "spm", "DIE_AREA": [0, 0, 3162, 3162],
              "CORE_AREA": [381, 381, 2400, 2400], "FP_SIZING": "absolute",
              "FP_CORE_UTIL": 50, "CLOCK_PERIOD": 10}
    sources = {"DESIGN_NAME": td.DECLARATION_REL.removesuffix(".json")
               + ".answers.top_cell",
               "DIE_AREA": "reports/phase3/floorplan_rectangles.json.die_rect_um",
               "CORE_AREA": "reports/phase3/floorplan_rectangles.json.floorplan_rect_um",
               "FP_SIZING": "reports/phase3/floorplan_rectangles.json.die_rect_um",
               "FP_CORE_UTIL": "librelane:OpenROAD.Floorplan default"}
    got = IF.record_applied_config(p, config, sources, recorded_by="seg2")
    assert got == ["config:FP_CORE_UTIL", "core_area_um", "die_area_um",
                   "fp_sizing"]
    table = IF.read_record(p)["tool_defaults"]
    assert "top_cell" not in table
    # FP_CORE_UTIL is not a 0.5ic question: recorded, and labelled as a config key.
    assert table["config:FP_CORE_UTIL"] == {
        "value": 50, "source": "librelane:OpenROAD.Floorplan default",
        "recorded_by": "seg2"}


def test_applied_answer_reads_the_declaration_first(tmp_path):
    p = _flagged(tmp_path)
    assert IF.applied_answer(p, "deliverable")[0] == td.DELIVERABLE_DIE
    value, why = IF.applied_answer(p, "core_area_um")
    assert value == td.NOT_DETERMINED and "recorded no applied" in why
    IF.record_applied(p, {"core_area_um": {"value": [1, 1, 9, 9],
                                           "source": "auto-die"}},
                      recorded_by="t")
    value, why = IF.applied_answer(p, "core_area_um")
    assert value == [1, 1, 9, 9] and "auto-die" in why


def test_the_default_never_reads_an_applied_value(tmp_path):
    p = _project(tmp_path)
    value, why = IF.applied_answer(p, "core_area_um")
    assert (value, why) == (td.NOT_DETERMINED, "not declared")


def test_the_finishing_core(tmp_path):
    import librelane_contract as LC
    import librelane_step37 as S37
    p = _project(tmp_path)
    with pytest.raises(LC.Refusal) as ei:
        S37._finishing_core(p)
    assert ei.value.args[0] == "LL_FINISHING_CORE_UNDECLARED" or \
        "LL_FINISHING_CORE_UNDECLARED" in str(ei.value)
    IF.write_record(p, "librelane", resolved_by="t")
    with pytest.raises(LC.Refusal):
        S37._finishing_core(p)
    IF.record_applied(p, {"core_area_um": {"value": [381, 381, 2400, 2400],
                                           "source": "auto-die"}},
                      recorded_by="t")
    core, source = S37._finishing_core(p)
    assert core == [381.0, 381.0, 2400.0, 2400.0]
    assert "tool_defaults.core_area_um" in source and "auto-die" in source


def test_a_declared_core_wins_over_an_applied_one(tmp_path):
    import librelane_step37 as S37
    p = _flagged(tmp_path)
    IF.record_applied(p, {"core_area_um": {"value": [1, 1, 9, 9],
                                           "source": "auto-die"}},
                      recorded_by="t")
    doc, _ = td.load(p / td.DECLARATION_REL)
    doc, _ = td.merge_answers(doc, {"die_area_um": [0, 0, 3162, 3162],
                                    "die_origin_um": [0, 0],
                                    "core_area_um": [381, 381, 2400, 2400]})
    (p / td.DECLARATION_REL).write_text(json.dumps(doc))
    core, source = S37._finishing_core(p)
    assert list(core) == [381, 381, 2400, 2400]
    assert source.startswith(td.DECLARATION_REL.removesuffix(".json"))


def test_the_auto_die_a_padring_chain_applies_is_recorded_not_declared(tmp_path):
    """Needs D1 (landed on main as v1.25.65): the Chip die/core the run's own
    floorplan record gives a PadRing chain."""
    import librelane_contract as LC
    p = _flagged(tmp_path)
    config, sources = LC.declaration_config(p)
    LC._apply_runner_floorplan(p, config, sources, ["OpenROAD.PadRing"])
    assert config["DIE_AREA"] == [0, 0, 3162, 3162]
    got = IF.record_applied_config(p, config, sources, recorded_by="seg2")
    assert {"die_area_um", "core_area_um", "fp_sizing"} <= set(got)
    table = IF.read_record(p)["tool_defaults"]
    assert "floorplan_rectangles.json" in table["die_area_um"]["source"]
    doc, _ = td.load(p / td.DECLARATION_REL)
    assert td.answer(doc, "die_area_um") == td.NOT_DETERMINED
    assert td.answer(doc, "core_area_um") == td.NOT_DETERMINED


# ── wave-5 review fixes ─────────────────────────────────────────────────────

def _resolved(tmp_path):
    """A LibreLane resolved step config, in its real shape: every variable,
    unset ones as null, the tool's own defaults with no vibe-ic provenance."""
    cfg = {"DESIGN_NAME": "spm", "DIE_AREA": None, "CORE_AREA": None,
           "FP_SIZING": "relative", "FP_CORE_UTIL": 50, "CLOCK_PERIOD": 10.0,
           "PL_TARGET_DENSITY_PCT": None}
    path = tmp_path / "runs" / "seg2" / "resolved.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cfg))
    return cfg, path


def test_librelanes_own_defaults_are_recorded_with_the_resolved_config(tmp_path):
    p = _flagged(tmp_path)
    cfg, path = _resolved(tmp_path)
    sources = {"DESIGN_NAME": td.DECLARATION_REL.removesuffix(".json")
               + ".answers.top_cell"}
    got = IF.record_applied_config(p, cfg, sources, recorded_by="seg2",
                                   resolved_config=path)
    assert got == ["config:FP_CORE_UTIL", "fp_sizing"]      # None: not applied
    table = IF.read_record(p)["tool_defaults"]
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    for q in got:
        assert table[q]["source"] == (f"librelane resolved default ({path} "
                                      f"sha256:{sha})")
    assert table["fp_sizing"]["value"] == "relative"


def test_without_the_resolved_config_a_tool_default_is_not_recorded(tmp_path):
    p = _flagged(tmp_path)
    cfg, _ = _resolved(tmp_path)
    assert IF.record_applied_config(p, cfg, {}, recorded_by="seg2") == []


def test_a_hardmacro_records_its_rectangle_under_its_own_name(tmp_path):
    p = _flagged(tmp_path)
    doc, _ = td.load(p / td.DECLARATION_REL)
    doc["answers"]["deliverable"] = td.DELIVERABLE_HARDMACRO
    _OD.attest(doc)
    (p / td.DECLARATION_REL).write_text(json.dumps(doc))
    got = IF.record_applied_config(
        p, {"DIE_AREA": [0, 0, 400, 400], "CORE_AREA": [10, 10, 390, 390],
            "FP_SIZING": "absolute"},
        {"DIE_AREA": "run", "CORE_AREA": "run", "FP_SIZING": "run"},
        recorded_by="seg2")
    assert got == ["macro_area_um"]           # core/sizing are DIE questions


def test_an_unreadable_declaration_is_refused_never_empty(tmp_path):
    p = _flagged(tmp_path)
    (p / td.DECLARATION_REL).write_text("{not json")
    for call in (lambda: IF.applied_answer(p, "core_area_um"),
                 lambda: IF.record_applied(
                     p, {"core_area_um": {"value": [1, 1, 9, 9],
                                          "source": "x"}}, recorded_by="t")):
        with pytest.raises(IF.ImplRefusal) as ei:
            call()
        assert ei.value.reason_class == IF.IMPL_DECLARATION_UNREADABLE


def test_a_superseded_declared_answer_is_recorded_with_its_reason(tmp_path):
    p = _flagged(tmp_path)
    doc, _ = td.load(p / td.DECLARATION_REL)
    doc, _ = td.merge_answers(doc, {"top_cell": "spm"})
    (p / td.DECLARATION_REL).write_text(json.dumps(doc))
    ans = {"top_cell": {"value": "chip_top", "source": "the DEF"}}
    assert IF.record_applied(p, ans, recorded_by="t") == []
    assert IF.record_applied(p, ans, recorded_by="t",
                             outrank={"top_cell": "the wrapper this run WROTE"}
                             ) == ["top_cell"]
    src = IF.read_record(p)["tool_defaults"]["top_cell"]["source"]
    assert "outranks the declared answer: the wrapper this run WROTE" in src


def _relative_core(p):
    doc, _ = td.load(p / td.DECLARATION_REL)
    doc, _ = td.merge_answers(doc, {"fp_sizing": "relative",
                                    "die_area_um": [0, 0, 3162, 3162],
                                    "die_origin_um": [0, 0],
                                    "core_area_um": [5, 5, 50, 50]})
    (p / td.DECLARATION_REL).write_text(json.dumps(doc))


def test_a_withheld_declared_core_never_reaches_the_finishing_xor(tmp_path):
    """Wave-5 review: `declaration_config` withholds a relative-sized core.
    The DEFAULT flow refuses it by name, and the flag must not read it back
    through the applied-answer door."""
    import librelane_contract as LC
    import librelane_step37 as S37
    default = _project(tmp_path, "default")
    _relative_core(default)
    with pytest.raises(LC.Refusal):
        S37._finishing_core(default)
    flagged = _project(tmp_path, "flagged")
    _relative_core(flagged)
    IF.write_record(flagged, "librelane", resolved_by="t")
    with pytest.raises(LC.Refusal):
        S37._finishing_core(flagged)
    # ...a withheld declared core is not an answer, so the applied one records.
    assert IF.record_applied(
        flagged, {"core_area_um": {"value": [381, 381, 2400, 2400],
                                   "source": "auto-die"}},
        recorded_by="t") == ["core_area_um"]
    core, source = S37._finishing_core(flagged)
    assert core == [381.0, 381.0, 2400.0, 2400.0] and "auto-die" in source


def test_an_applied_core_from_an_earlier_run_is_refused(tmp_path):
    import librelane_contract as LC
    import librelane_step37 as S37
    p = _flagged(tmp_path)
    IF.record_applied(p, {"core_area_um": {"value": [100, 100, 900, 900],
                                           "source": "an earlier run"}},
                      recorded_by="t")
    with pytest.raises(LC.Refusal, match="LL_APPLIED_CORE_STALE"):
        S37._finishing_core(p)


def test_a_conflict_while_publishing_is_a_named_gds_fail(tmp_path, no_seal):
    r = _r()
    p = _flagged(tmp_path)
    IF.record_applied(p, {"core_area_um": {"value": [1, 1, 9, 9],
                                           "source": "an earlier run"}},
                      recorded_by="t")
    rec = r.publish_tapeout_declarations(
        p, _Pdk(), "c", p / "phase3/stage3/pnr/routed.def", "spm")
    assert rec["refused"]["reason_class"] == IF.IMPL_APPLIED_CONFLICT
    import ast
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    tree = ast.parse(src)
    for name in ("_step_gds_direct", "step_gds"):
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        body = ast.get_source_segment(src, fn)
        call = body.index("publish_tapeout_declarations(")
        guard = body.index('if _decl_rec.get("refused"):', call)
        assert 'StepResult("gds", "FAIL"' in body[guard:guard + 300], name
