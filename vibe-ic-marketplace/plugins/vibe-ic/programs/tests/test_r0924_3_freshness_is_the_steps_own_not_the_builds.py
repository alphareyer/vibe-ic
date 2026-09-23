#!/usr/bin/env python3
"""R-0924-3 — a cached step is fresh when THE STEP has not changed.

THE MEASURED DEFECT (spm run23, lane icspm5, 2026-09-23):

    [synth] the cached synth artefact was produced by a DIFFERENT build
    (plugin 1.23.70 -> 1.23.93; recipe e8fb29723876 -> cf8b2cd4ba76)
    — re-running

`--force-step gds` on a FINISHED tree re-ran synthesis and started PnR, because
freshness was decided from `_plugin_version()` + `_recipe_sha256()` — the
released version and the sha256 of the WHOLE ~66k-line runner. Both are facts
about the BUILD. Every landed fix bumps the version, so every landed fix
invalidated every cached step, whatever it actually touched.

The same key was simultaneously TOO NARROW, which the old code disclosed in a
comment instead of fixing: an in-tree edit to a helper module with no version
bump was not detected at all.

This deck holds BOTH halves, because a fix for either one alone is a different
and worse bug:

  SYMPTOM 1 (too coarse)  a no-op version bump must REUSE.
  SYMPTOM 2 (too narrow)  a helper edit with no version bump must INVALIDATE.

and it holds the fail-closed rules, which are the reason this is safe at all:
an unknown component is never freshness.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

# A HARD import, deliberately. `importorskip` would turn "main does not have
# this fix" into a SKIP, and a skip is not a red arm: the falsifiable-reference
# check needs this deck to FAIL against main's sources, not to pass by
# declining to run.
import _step_identity as si  # noqa: E402

RUNNER = PROGRAMS / "phase3_one_shot_runner.py"
FLOW = PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml"


# ---------------------------------------------------------------------------
# a synthetic tree, so a "code change" is a real edit to a file this test owns
# ---------------------------------------------------------------------------
def _tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(programs_dir, runner.py, flow.yaml) — a miniature of the real shape."""
    prog = tmp_path / "programs"
    prog.mkdir()
    (prog / "helper_a.py").write_text(
        "import helper_b\nVALUE = 1\n", encoding="utf-8")
    (prog / "helper_b.py").write_text(
        "import helper_c\nOTHER = 2\n", encoding="utf-8")
    (prog / "helper_c.py").write_text("DEEP = 3\n", encoding="utf-8")
    (prog / "unrelated.py").write_text("NOPE = 4\n", encoding="utf-8")
    (prog / "declared_prog.py").write_text("X = 5\n", encoding="utf-8")
    runner = prog / "runner.py"
    runner.write_text(
        "import helper_a\n"
        "import unrelated\n"
        "TUNING = 7\n"
        "\n"
        "def _leaf():\n"
        "    return TUNING\n"
        "\n"
        "def step_synth():\n"
        "    return helper_a.VALUE + _leaf()\n"
        "\n"
        "def step_pnr():\n"
        "    return unrelated.NOPE\n",
        encoding="utf-8")
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "steps:\n"
        "  - id: 9\n"
        "    name: Synthesis\n"
        "    stage: stage2\n"
        "    programs: [declared_prog]\n"
        "    required_inputs:\n"
        "      - {from: 1, path: 'rtl/*.v'}\n"
        "    required_outputs: ['phase2/stage2/synth/netlist.v']\n"
        "  - id: 14\n"
        "    name: Synthesis handoff gate\n"
        "    stage: stage2\n"
        "    programs: [declared_prog]\n"
        "    required_inputs:\n"
        "      - {from: 9, path: 'phase2/stage2/synth/netlist.v'}\n"
        "    required_outputs: ['phase2/stage2/synth/netlist.v']\n",
        encoding="utf-8")
    return prog, runner, flow


def _seeded(monkeypatch, seeds=("step_synth",)):
    monkeypatch.setitem(si.KIND_RECIPE_SEEDS, "synth", tuple(seeds))


# ---------------------------------------------------------------------------
# THE PRODUCER OF AN ARTEFACT IS NOT EVERY STEP THAT NAMES IT
# ---------------------------------------------------------------------------
def test_a_step_that_reads_the_artefact_is_not_its_producer(tmp_path):
    """Measured on the real flow: step 14 ("Synthesis handoff gate")
    re-declares the netlist as a required_output while also declaring it as a
    required_input from step 9. Counting it as a producer pulls a gate's whole
    program fan-out into synth's identity, and then a die-finishing edit
    invalidates the cached netlist — the exact harm this ruling forbids."""
    _prog, _runner, flow = _tree(tmp_path)
    steps = si.load_steps(flow)
    ids = [str(s["id"]) for s in si.steps_for_kind(steps, "synth")]
    assert ids == ["9"], (
        f"the producer of the netlist is step 9; got {ids}")


def test_the_real_flow_attributes_each_kind_to_one_producing_step():
    steps = si.load_steps(FLOW)
    assert steps, "the real flow must load"
    got = {k: [str(s["id"]) for s in si.steps_for_kind(steps, k)]
           for k in ("synth", "pnr", "gds")}
    assert got == {"synth": ["9"], "pnr": ["21"], "gds": ["37"]}, got


# ---------------------------------------------------------------------------
# THE CODE CLOSURE — derived, kind-specific, and DEPTH 1 ON PURPOSE
# ---------------------------------------------------------------------------
def test_the_closure_is_depth_one_because_transitive_degenerates(tmp_path):
    """`helper_a` imports `helper_b` imports `helper_c`. Only what the step
    NAMES, plus what that directly imports, is the step's own code.

    Depth is a measured choice: in the real tree
    `_watchdog -> step_input_scope -> step_required_inputs_check ->
    flow_compliance_check -> phase1_doc_one_shot_runner` makes every kind's
    transitive closure the same 459 files."""
    prog, runner, _flow = _tree(tmp_path)
    files, _notes = si.direct_modules([runner], prog)
    names = {f.name for f in files}
    assert "helper_a.py" in names, "a directly imported module is the step's"
    assert "helper_c.py" not in names, (
        "depth 1: a second-order import is NOT pulled in — transitive closure "
        "degenerates to the whole tree and destroys per-step separation")


def test_a_kinds_closure_excludes_what_only_another_kind_reaches(
        tmp_path, monkeypatch):
    prog, runner, _flow = _tree(tmp_path)
    _seeded(monkeypatch)
    d_synth, why = si.runner_code_closure(runner, "synth")
    assert d_synth is not None, why
    monkeypatch.setitem(si.KIND_RECIPE_SEEDS, "synth", ("step_pnr",))
    d_pnr, _ = si.runner_code_closure(runner, "synth")
    assert d_synth != d_pnr, (
        "two steps that share a file must not share one code identity")


def test_a_constant_the_recipe_reads_is_part_of_the_recipe(
        tmp_path, monkeypatch):
    """`TUNING` is a module-level constant `_leaf()` returns. Changing what a
    step asks for is a code change even when no call moves."""
    prog, runner, _flow = _tree(tmp_path)
    _seeded(monkeypatch)
    before, _ = si.runner_code_closure(runner, "synth")
    runner.write_text(runner.read_text().replace("TUNING = 7", "TUNING = 8"),
                      encoding="utf-8")
    after, _ = si.runner_code_closure(runner, "synth")
    assert before != after, "a changed tuning constant must invalidate"


def test_a_missing_seed_refuses_rather_than_returning_an_empty_closure(
        tmp_path, monkeypatch):
    prog, runner, _flow = _tree(tmp_path)
    monkeypatch.setitem(si.KIND_RECIPE_SEEDS, "synth", ("step_renamed",))
    dig, why = si.runner_code_closure(runner, "synth")
    assert dig is None, "an anchor that moved must not produce a digest"
    assert any("seed" in w for w in why), why


def test_the_real_runner_resolves_every_declared_seed():
    """The one hand-written anchor in the module. If a seed is renamed and this
    is not updated, every kind fails closed — this says so out loud."""
    members, err = si._module_members(RUNNER)
    assert not err, err
    for kind, seeds in si.KIND_RECIPE_SEEDS.items():
        for seed in seeds:
            assert seed in members, (
                f"{kind}'s seed {seed!r} is not defined in the runner")


def test_each_real_kind_has_its_own_code_identity():
    digs = {}
    for kind in ("synth", "pnr", "gds"):
        d, why = si.runner_code_closure(RUNNER, kind)
        assert d is not None, why
        digs[kind] = d
    assert len(set(digs.values())) == 3, (
        f"the three kinds must not share one identity: {digs}")


def test_die_finishing_reaches_pnr_and_gds_but_not_synth():
    """R-0924-3's own worked example, asserted against the real tree.

    The p0seal change edits `die_finishing_gen`. Synthesis cannot see it, so a
    cached netlist must survive it; PnR and GDS name it, so they must not."""
    members, err = si._module_members(RUNNER)
    assert not err, err
    bindings = si._import_bindings(RUNNER)

    def directs(kind: str) -> set[str]:
        seen: set[str] = set()
        stack = list(si.KIND_RECIPE_SEEDS[kind])
        while stack:
            name = stack.pop()
            if name in seen or name not in members:
                continue
            seen.add(name)
            for ref in si._referenced_names(members[name]):
                if ref in members and ref not in seen:
                    stack.append(ref)
        out: set[str] = set()
        for name in seen:
            for ref in si._referenced_names(members[name]):
                if ref in bindings:
                    m = si._resolve_module(PROGRAMS, bindings[ref])
                    if m is not None:
                        out.add(m.name)
        return out

    assert "die_finishing_gen.py" not in directs("synth"), (
        "a die-finishing edit must NOT invalidate the cached netlist")
    assert "die_finishing_gen.py" in directs("pnr")
    assert "die_finishing_gen.py" in directs("gds")


# ---------------------------------------------------------------------------
# FAIL CLOSED — an unknown is never freshness
# ---------------------------------------------------------------------------
def test_two_unknowns_do_not_agree():
    """"I could not tell then" and "I cannot tell now" is not evidence that
    nothing moved."""
    fresh, why = si.compare({"inputs": None, "code": None,
                             "tools": None, "pdk": None},
                            {"inputs": None, "code": None,
                             "tools": None, "pdk": None})
    assert fresh is False, why


def test_no_sidecar_is_stale():
    fresh, why = si.compare(None, {c: "a" * 64 for c in si._COMPONENTS})
    assert fresh is False
    assert any("no recorded step identity" in w for w in why), why


def test_every_component_must_match_for_reuse():
    good = {c: "a" * 64 for c in si._COMPONENTS}
    assert si.compare(dict(good), dict(good))[0] is True
    for comp in si._COMPONENTS:
        rec = dict(good)
        rec[comp] = "b" * 64
        fresh, why = si.compare(rec, dict(good))
        assert fresh is False, f"{comp} differing must invalidate"
        assert any(w.startswith(f"{comp}:") for w in why), why


def test_a_declared_input_that_is_absent_refuses(tmp_path):
    _prog, _runner, flow = _tree(tmp_path)
    steps = si.load_steps(flow)
    project = tmp_path / "proj"
    (project / "rtl").mkdir(parents=True)
    dig, why = si.inputs_digest(project, steps, "synth")
    assert dig is None, "no RTL on disk means the question is unanswerable"
    assert any("resolves to no file" in w for w in why), why


def test_inputs_change_when_a_declared_input_changes(tmp_path):
    _prog, _runner, flow = _tree(tmp_path)
    steps = si.load_steps(flow)
    project = tmp_path / "proj"
    (project / "rtl").mkdir(parents=True)
    rtl = project / "rtl" / "top.v"
    rtl.write_text("module top; endmodule\n", encoding="utf-8")
    first, _ = si.inputs_digest(project, steps, "synth")
    assert first is not None
    rtl.write_text("module top; wire w; endmodule\n", encoding="utf-8")
    second, _ = si.inputs_digest(project, steps, "synth")
    assert first != second, "an edited input must invalidate"


def test_tools_refuse_without_an_image_digest(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "yosys", "version": "0.38",
        "outputs": {"phase2/stage2/synth/netlist.v": "sha256:" + "0" * 64},
    }) + "\n", encoding="utf-8")
    dig, why = si.tools_digest(project, "synth", None)
    assert dig is None, "an unnameable image is not a proven image"
    assert any("image digest" in w for w in why), why


def test_tools_refuse_when_the_ledger_knows_nothing_of_this_step(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "klayout", "version": "0.28",
        "outputs": {"phase3/stage4/gds/top.gds": "sha256:" + "0" * 64},
    }) + "\n", encoding="utf-8")
    dig, why = si.tools_digest(project, "synth", "sha256:img")
    assert dig is None, why
    assert any("no invocation writing into" in w for w in why), why


def test_pdk_refuses_a_path_it_cannot_read(tmp_path):
    class _Pdk:
        liberty = "/nonexistent/inside/container/only.lib"
        tech_lef = cell_lef = cell_gds = drc_deck = None
    dig, why = si.pdk_digest(_Pdk())
    assert dig is None, "a PDK file that cannot be hashed is not a match"
    assert any("could not be read" in w for w in why), why


def test_pdk_is_hashed_where_it_actually_lives(tmp_path):
    """The container-only case, which is the ORDINARY one: measured on run2,
    every PDK path is under `/foss/pdks` and the host has no such directory.
    Without a reader that can reach them this component is never computable
    and the whole change degenerates into 'always re-run'."""
    class _Pdk:
        liberty = "/foss/pdks/only/inside/the/container.lib"
        tech_lef = cell_lef = cell_gds = drc_deck = None

    assert si.pdk_digest(_Pdk())[0] is None, "no reader: still refuses"

    seen = {}

    def hasher(paths):
        seen["paths"] = list(paths)
        return {p: "c" * 64 for p in paths}

    dig, why = si.pdk_digest(_Pdk(), hasher)
    assert dig is not None, why
    assert seen["paths"] == ["/foss/pdks/only/inside/the/container.lib"]


def test_a_hasher_that_answers_for_only_some_files_still_refuses():
    """Half an answer is not an answer: a PDK proven unchanged in four files
    out of five is not a PDK proven unchanged."""
    class _Pdk:
        liberty = "/foss/a.lib"
        tech_lef = "/foss/b.tlef"
        cell_lef = cell_gds = drc_deck = None
    dig, why = si.pdk_digest(_Pdk(), lambda paths: {"/foss/a.lib": "d" * 64})
    assert dig is None, "a partially readable PDK is not a matching PDK"
    assert any("/foss/b.tlef" in w for w in why), why


def test_a_failing_hasher_refuses_rather_than_raising():
    class _Pdk:
        liberty = "/foss/a.lib"
        tech_lef = cell_lef = cell_gds = drc_deck = None

    def boom(paths):
        raise RuntimeError("container gone")

    dig, why = si.pdk_digest(_Pdk(), boom)
    assert dig is None
    assert any("hasher failed" in w for w in why), why


def test_pdk_changes_when_a_pdk_file_changes(tmp_path):
    lib = tmp_path / "x.lib"
    lib.write_text("library(a){}\n", encoding="utf-8")

    class _Pdk:
        liberty = str(lib)
        tech_lef = cell_lef = cell_gds = drc_deck = None
    first, _ = si.pdk_digest(_Pdk())
    lib.write_text("library(b){}\n", encoding="utf-8")
    second, _ = si.pdk_digest(_Pdk())
    assert first is not None and first != second


# ---------------------------------------------------------------------------
# SYMPTOM 1 — TOO COARSE: a no-op version bump must REUSE
# ---------------------------------------------------------------------------
def test_the_identity_does_not_contain_the_plugin_version(
        tmp_path, monkeypatch):
    """The whole measured harm in one assertion. Nothing about the released
    version may enter a step's identity, or every landed fix invalidates every
    cached step again."""
    prog, runner, flow = _tree(tmp_path)
    _seeded(monkeypatch)
    project = tmp_path / "proj"
    (project / "rtl").mkdir(parents=True)
    (project / "rtl" / "top.v").write_text("module top; endmodule\n",
                                           encoding="utf-8")
    (project / "provenance.jsonl").write_text(json.dumps({
        "tool": "yosys", "version": "0.38",
        "outputs": {"phase2/stage2/synth/netlist.v": "sha256:" + "0" * 64},
    }) + "\n", encoding="utf-8")
    lib = tmp_path / "x.lib"
    lib.write_text("library(a){}\n", encoding="utf-8")

    class _Pdk:
        liberty = str(lib)
        tech_lef = cell_lef = cell_gds = drc_deck = None

    kw = dict(project=project, kind="synth", runner_path=runner,
              programs_dir=prog, flow_yaml=flow, pdk=_Pdk(),
              image_digest="sha256:img")
    first, _ = si.identity_now(**kw)
    assert all(v is not None for v in first.values()), first
    # A RELEASE, and nothing else: no file this step reads has moved.
    second, _ = si.identity_now(**kw)
    assert first == second
    fresh, why = si.compare(first, second)
    assert fresh is True, why
    blob = json.dumps(first)
    assert "1.23" not in blob and "version" not in blob, (
        f"a version leaked into the step identity: {first}")


# ---------------------------------------------------------------------------
# SYMPTOM 2 — TOO NARROW: a helper edit with no version bump must INVALIDATE
# ---------------------------------------------------------------------------
def test_editing_a_helper_the_step_names_invalidates_it(
        tmp_path, monkeypatch):
    """The half the old key disclosed and did not fix: `helper_a` is not the
    runner file and a working-copy edit bumps no version, so the old predicate
    could not see it at all."""
    prog, runner, flow = _tree(tmp_path)
    _seeded(monkeypatch)
    before, why = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    assert before is not None, why
    helper = prog / "helper_a.py"
    helper.write_text(helper.read_text().replace("VALUE = 1", "VALUE = 99"),
                      encoding="utf-8")
    after, _ = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    assert before != after, (
        "an edit to a helper this step names must invalidate the step")


def test_editing_a_helper_no_step_names_does_not_invalidate_it(
        tmp_path, monkeypatch):
    """The other direction, and it is the one that makes the fix worth having:
    an edit the step cannot see must NOT cost a re-run."""
    prog, runner, flow = _tree(tmp_path)
    _seeded(monkeypatch)
    before, _ = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    deep = prog / "helper_c.py"
    deep.write_text("DEEP = 999\n", encoding="utf-8")
    after, _ = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    assert before == after, (
        "a second-order module the step never names is outside its identity "
        "— this is the disclosed depth-1 residual, asserted so it cannot "
        "change silently")


def test_a_declared_program_of_the_step_is_in_its_code(
        tmp_path, monkeypatch):
    prog, runner, flow = _tree(tmp_path)
    _seeded(monkeypatch)
    steps = si.load_steps(flow)
    before, _ = si.code_digest(runner, prog, steps, "synth")
    dp = prog / "declared_prog.py"
    dp.write_text("X = 500\n", encoding="utf-8")
    after, _ = si.code_digest(runner, prog, steps, "synth")
    assert before != after, "the step's own declared program is its code"


# ---------------------------------------------------------------------------
# THE PREDICATE DELEGATES — the wiring itself, not a grep for a spelling
# ---------------------------------------------------------------------------
def _runner_module():
    import importlib
    return importlib.import_module("phase3_one_shot_runner")


def test_the_predicate_refuses_without_a_project():
    r = _runner_module()
    ok, why = r._producer_cache_valid_for(Path("/nonexistent"), "synth")
    assert ok is False
    assert "without the project" in why, why


def test_the_predicate_reuses_only_on_a_matching_sidecar(
        tmp_path, monkeypatch):
    """Drive the real predicate with a prepared directory: a sidecar that
    agrees with what this build computes reuses; one component moved does
    not."""
    r = _runner_module()
    out = tmp_path / "out"
    out.mkdir()
    ident = {c: "a" * 64 for c in si._COMPONENTS}
    monkeypatch.setattr(r, "_step_image_digest", lambda c: "sha256:img")
    monkeypatch.setattr(si, "identity_now",
                        lambda **kw: (dict(ident), {}))
    si.write_sidecar(out, "synth", dict(ident))
    ok, why = r._producer_cache_valid_for(
        out, "synth", project=tmp_path, pdk=None, container="")
    assert ok is True, why
    assert "step unchanged" in why, why

    moved = dict(ident)
    moved["code"] = "b" * 64
    si.write_sidecar(out, "synth", moved)
    ok, why = r._producer_cache_valid_for(
        out, "synth", project=tmp_path, pdk=None, container="")
    assert ok is False
    assert "code:" in why, why


def test_an_unnameable_image_makes_the_step_re_run(tmp_path, monkeypatch):
    r = _runner_module()
    monkeypatch.setattr(r, "_step_image_digest", lambda c: None)
    out = tmp_path / "out"
    out.mkdir()
    ok, why = r._producer_cache_valid_for(
        out, "synth", project=tmp_path, pdk=None, container="")
    assert ok is False, why


# ---------------------------------------------------------------------------
# MUTATIONS — one per symptom. Each breaks the fix; each must be caught.
# ---------------------------------------------------------------------------
def test_mutation_putting_the_build_back_in_is_caught(tmp_path, monkeypatch):
    """SYMPTOM 1's mutation: fold the released version back into the identity.

    This is the one-line regression that would restore the measured harm, so
    it is asserted to be OBSERVABLE: an identity with a version in it is not
    the identity without one, which is exactly why no version may be there."""
    prog, runner, flow = _tree(tmp_path)
    _seeded(monkeypatch)
    steps = si.load_steps(flow)
    clean, why = si.code_digest(runner, prog, steps, "synth")
    assert clean is not None, why
    mutated = si._digest_pairs((("code", clean), ("version", "1.23.93")))
    assert mutated != clean, (
        "folding a plugin version into a step's code identity must change it "
        "— if it did not, the version could hide there unnoticed")
    fresh, _ = si.compare({**{c: "a" * 64 for c in si._COMPONENTS},
                           "code": mutated},
                          {**{c: "a" * 64 for c in si._COMPONENTS},
                           "code": clean})
    assert fresh is False, (
        "and the comparison must reject it, so a build-keyed identity can "
        "never be mistaken for a step-keyed one")


def test_mutation_comparing_unknowns_as_equal_is_caught():
    """SYMPTOM 2's mutation, and the fail-closed rule's: if two `None`s
    compared equal, a step whose identity cannot be computed would REUSE."""
    both_unknown = {c: None for c in si._COMPONENTS}
    fresh, _ = si.compare(dict(both_unknown), dict(both_unknown))
    assert fresh is False, (
        "compare() must never treat an uncomputable component as a match")
    partial = {c: "a" * 64 for c in si._COMPONENTS}
    partial["tools"] = None
    fresh, why = si.compare({c: "a" * 64 for c in si._COMPONENTS}, partial)
    assert fresh is False, why
    assert any("cannot be established" in w for w in why), why


# ===========================================================================
# r2 — THE REVIEW FINDINGS (wcxu446tu, 6 CONFIRMED)
#
# ROOT CAUSE of most of them: r1 tied each kind to the ONE flow step that
# declares its artefact, but the runner FUNCTION implements a RANGE.
# `step_pnr` is floorplan THROUGH routing; `step_gds` and the finishing around
# it span die-finishing through stream-out. Keying pnr on step 21 alone made
# its inputs `sha(post_hold.def)` — a file `step_pnr` WRITES ITSELF — so a new
# netlist, SDC or slot left the routed DEF "fresh" and the disclosure said so.
#
# NOTHING BELOW PATCHES AN IDENTITY HELPER. The r1 suite pinned
# `_step_image_digest`, and that is exactly what hid finding 5.
# ===========================================================================
import phase3_one_shot_runner as _R  # noqa: E402

_REAL_STEPS = si.load_steps(FLOW)


def _pdk_with_real_files(root: Path):
    d = root / "_pdk"
    d.mkdir(parents=True, exist_ok=True)
    lib, tlef = d / "tt.lib", d / "tech.lef"
    for f, t in ((lib, "library(t){}\n"), (tlef, "VERSION 5.8 ;\n")):
        if not f.is_file():
            f.write_text(t)

    class _Pdk:
        liberty = str(lib)
        tech_lef = str(tlef)
        cell_lef = cell_gds = drc_deck = None
    return _Pdk()


def _span_project(tmp_path: Path) -> Path:
    """A finished tree carrying what every span declares and reads."""
    for rel, text in (
        ("input/submission_template/tapeout_declaration.json",
         '{"answers": {"deliverable": "DIE"}}\n'),
        ("phase2/stage1/rtl/top.v", "module top(); endmodule\n"),
        ("phase2/stage2/constraints/top.sdc", "create_clock -period 10\n"),
        ("phase2/stage2/synth/netlist.v", "module top(); endmodule\n"),
        ("phase3/stage3/pnr/routed.def", "VERSION 5.8 ;\nEND DESIGN\n"),
        ("phase3/stage3/pnr/top.def", "VERSION 5.8 ;\nEND DESIGN\n"),
        ("phase3/stage3/pnr/spare_cells.json", "{}\n"),
        ("phase3/stage3/extracted/parasitic.spef", "*SPEF\n"),
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (tmp_path / "provenance.jsonl").write_text("".join(
        json.dumps({"tool": t, "version": v,
                    "outputs": {o: "sha256:" + "0" * 64}}) + "\n"
        for t, v, o in (
            ("yosys", "0.38", "phase2/stage2/synth/netlist.v"),
            ("openroad", "2.0", "phase3/stage3/pnr/routed.def"),
            ("klayout", "0.28", "phase3/stage4/gds/top.gds"))))
    return tmp_path


def _ident(project: Path, kind: str):
    extra = ()
    if kind == "gds":
        extra = (project / "phase3/stage3/pnr/top.def",)
    elif kind == "pnr":
        extra = (project / "phase2/stage2/synth/netlist.v",)
    return si.identity_now(
        project=project, kind=kind, runner_path=RUNNER, programs_dir=PROGRAMS,
        flow_yaml=FLOW, pdk=_pdk_with_real_files(project),
        image_digest=_R._step_image_digest(""),      # REAL, not patched
        extra_inputs=extra)[0]


# --- the span itself -------------------------------------------------------
def test_each_kind_spans_the_steps_its_function_implements():
    got = {k: [str(s["id"]) for s in si.steps_in_span(_REAL_STEPS, k)]
           for k in ("synth", "pnr", "gds")}
    assert got["synth"] == ["9"]
    assert got["pnr"] == ["15", "15.5ic", "16", "17", "18", "19", "20", "21"]
    assert "26.5ic" in got["gds"] and "37" in got["gds"]
    assert "34" in got["gds"], "metal fill is inside the stream-out span"


def test_a_span_does_not_hash_what_it_produces_itself():
    """FINDING 1. `post_hold.def` is step 20's output and step 21's declared
    input, and `step_pnr` implements both — so hashing it asked the routed DEF
    whether the routed DEF had changed. A key that cannot fire."""
    specs, produced = si.span_input_specs(_REAL_STEPS, "pnr")
    paths = [p for p, _cond in specs]
    assert "phase3/stage3/pnr/post_hold.def" in produced
    assert not any("post_hold.def" in p for p in paths), (
        f"pnr still hashes a file it writes itself: {paths}")
    assert any("constraints" in p for p in paths), "the SDC must be an input"
    assert any("tapeout_declaration" in p for p in paths), (
        "the slot declaration must be an input")


def test_the_gds_span_subtracts_the_fill_it_makes_itself():
    """FINDING 2/6. Step 37 declares `filled.def OR metal_fill.done`, which
    `step_canonicalize_artefacts` writes AFTER the stamp — so the stamp
    recorded the PREVIOUS run's fill and the stream-out was re-run for
    nothing. Step 34 is inside the span, so the rule subtracts it."""
    specs, produced = si.span_input_specs(_REAL_STEPS, "gds")
    paths = [p for p, _c in specs]
    assert any("metal_fill.done" in p for p in produced)
    assert not any("filled.def" in p or "metal_fill.done" in p
                   for p in paths), paths


def test_an_input_whose_producer_is_conditional_is_absent_not_unanswerable(
        tmp_path):
    """MEASURED: a finished gf180 tree has no `post_dft_netlist.v`, because
    step 12 is `condition_kind: design_dependent`. Refusing on its absence
    would mean PnR is never reused on any design without DFT."""
    specs, _ = si.span_input_specs(_REAL_STEPS, "pnr")
    conds = {p: c for p, c in specs}
    dft = [p for p in conds if "post_dft_netlist" in p]
    assert dft, "the flow still declares the post-DFT netlist to PnR"
    assert conds[dft[0]] is True, "step 12 is conditional, so this input is"
    project = _span_project(tmp_path)
    dig, why = si.inputs_digest(project, _REAL_STEPS, "pnr")
    assert dig is not None, why
    assert any("absent(conditional producer)" in w for w in why), why


def test_a_conditional_input_that_appears_still_invalidates(tmp_path):
    """`absent` is a VALUE, not a pass: if the file shows up, the step moves."""
    project = _span_project(tmp_path)
    before, _ = si.inputs_digest(project, _REAL_STEPS, "pnr")
    p = project / "phase2/stage2/synth/post_dft_netlist.v"
    p.write_text("module top(); endmodule\n")
    after, _ = si.inputs_digest(project, _REAL_STEPS, "pnr")
    assert before != after, "a conditional input appearing must invalidate"


def test_an_unconditional_missing_input_still_refuses(tmp_path):
    project = _span_project(tmp_path)
    (project / "phase2/stage2/constraints/top.sdc").unlink()
    dig, why = si.inputs_digest(project, _REAL_STEPS, "pnr")
    assert dig is None, "a broken tree is not a design choice"
    assert any("resolves to no file" in w for w in why), why


# --- (a) a new netlist / SDC / slot must invalidate PnR --------------------
def test_a_new_netlist_invalidates_the_routed_def(tmp_path):
    """FINDING 1, the headline. PnR reads the NETLIST, not the RTL — hashing
    the RTL would re-run PnR for an edit synthesis proved changed nothing. So
    the chain is RTL -> synth re-runs -> new netlist -> PnR re-runs, and this
    holds the link that was broken."""
    project = _span_project(tmp_path)
    base = _ident(project, "pnr")
    nl = project / "phase2/stage2/synth/netlist.v"
    nl.write_text("module top(); wire w; endmodule\n")
    fresh, why = si.compare(base, _ident(project, "pnr"))
    assert fresh is False, f"a new netlist left the routed DEF fresh: {why}"


def test_a_new_sdc_invalidates_pnr_and_synth(tmp_path):
    project = _span_project(tmp_path)
    base = {k: _ident(project, k) for k in ("synth", "pnr")}
    sdc = project / "phase2/stage2/constraints/top.sdc"
    sdc.write_text("create_clock -period 5\n")
    for kind in ("synth", "pnr"):
        fresh, why = si.compare(base[kind], _ident(project, kind))
        assert fresh is False, f"{kind} survived an SDC change: {why}"


def test_a_new_slot_declaration_invalidates_every_kind(tmp_path):
    project = _span_project(tmp_path)
    base = {k: _ident(project, k) for k in ("synth", "pnr", "gds")}
    td = project / "input/submission_template/tapeout_declaration.json"
    td.write_text('{"answers": {"deliverable": "HARDMACRO"}}\n')
    for kind in ("synth", "pnr", "gds"):
        fresh, why = si.compare(base[kind], _ident(project, kind))
        assert fresh is False, f"{kind} survived a slot change: {why}"


def test_the_def_the_gds_streams_is_part_of_its_identity(tmp_path):
    """FINDING 2. No `required_inputs` entry names it, and
    `step_signoff_spef_repair` promotes it IN PLACE — so without this a GDS
    stayed fresh across a DEF that had been replaced underneath it."""
    project = _span_project(tmp_path)
    base = _ident(project, "gds")
    d = project / "phase3/stage3/pnr/top.def"
    d.write_text("VERSION 5.8 ;\n# promoted in place\nEND DESIGN\n")
    fresh, why = si.compare(base, _ident(project, "gds"))
    assert fresh is False, f"an in-place DEF promotion was missed: {why}"


def test_a_rerouted_def_invalidates_the_gds(tmp_path):
    project = _span_project(tmp_path)
    base = _ident(project, "gds")
    (project / "phase3/stage3/pnr/routed.def").write_text(
        "VERSION 5.8 ;\n# rerouted\nEND DESIGN\n")
    fresh, why = si.compare(base, _ident(project, "gds"))
    assert fresh is False, why


# --- (b) a no-op version bump still reuses, with NOTHING patched -----------
def test_a_no_op_version_bump_reuses_every_kind_unpatched(tmp_path,
                                                          monkeypatch):
    project = _span_project(tmp_path)
    base = {k: _ident(project, k) for k in ("synth", "pnr", "gds")}
    for kind, ident in base.items():
        assert all(v is not None for v in ident.values()), (
            f"{kind} has an uncomputable component with NOTHING patched — "
            f"that is the 'never fresh' shape finding 5 was about: {ident}")
    monkeypatch.setattr(_R, "_plugin_version", lambda: "99.99.99-brand-new")
    for kind in ("synth", "pnr", "gds"):
        fresh, why = si.compare(base[kind], _ident(project, kind))
        assert fresh is True, f"{kind} re-ran for a release alone: {why}"


def test_the_image_identity_ladder_answers_without_a_registry_digest():
    """FINDING 5. `image_identity` returns a REGISTRY digest or
    IMAGE_UNAVAILABLE; r1 turned anything else into None, so a locally built
    image — or running inside the image, where there is no docker client —
    meant every step re-ran for ever. The r1 tests patched this away."""
    assert _R._step_image_digest("") == "NO_CONTAINER", (
        "a container nobody named has no identity to fail closed on")
    assert _R._step_image_digest("no-such-container-xyz-0924") is None, (
        "a container that WAS named and cannot be identified still refuses")


# --- (c) the Tcl emitters must be inside pnr's code ------------------------
def test_from_imports_resolve_to_their_module():
    """FINDING 3. `from X import f` was bound only to `"X.f"`, which resolves
    to no file, so `_route_wire_transaction`, `pad_signal_route_repair` and
    `_pdk_via_analyzer` — the Tcl emitters behind every PnR script — were
    outside pnr's code identity and a landed fix to them was silently
    skipped."""
    members, err = si._module_members(RUNNER)
    assert not err, err
    bindings = si._import_bindings(RUNNER)

    def directs(kind: str) -> set[str]:
        seen: set[str] = set()
        stack = list(si.KIND_RECIPE_SEEDS[kind])
        while stack:
            name = stack.pop()
            if name in seen or name not in members:
                continue
            seen.add(name)
            for ref in si._referenced_names(members[name]):
                if ref in members and ref not in seen:
                    stack.append(ref)
        out: set[str] = set()
        for name in seen:
            for ref in si._referenced_names(members[name]):
                for dotted in (bindings.get(ref),
                               bindings.get(si._FROM_FALLBACK + ref)):
                    if not dotted:
                        continue
                    m = si._resolve_module(PROGRAMS, dotted)
                    if m is not None:
                        out.add(m.name)
                        break
        return out

    pnr = directs("pnr")
    for emitter in ("_route_wire_transaction.py", "pad_signal_route_repair.py",
                    "_pdk_via_analyzer.py"):
        assert emitter in pnr, (
            f"{emitter} is outside pnr's code identity, so a landed fix to it "
            f"would be silently skipped. pnr names {len(pnr)} module(s).")


def test_editing_a_tcl_emitter_invalidates_pnr(tmp_path):
    """The same finding, driven rather than inspected."""
    target = PROGRAMS / "_route_wire_transaction.py"
    before, why = si.code_digest(RUNNER, PROGRAMS, _REAL_STEPS, "pnr")
    assert before is not None, why
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# R-0924-3 r2 proof edit\n")
        after, _ = si.code_digest(RUNNER, PROGRAMS, _REAL_STEPS, "pnr")
    finally:
        target.write_bytes(original)
    assert before != after, (
        "an edit to a Tcl emitter behind every PnR script did not invalidate "
        "PnR")


# --- the PDK that the step itself derives ----------------------------------
def test_the_pdk_is_hashed_as_the_flow_received_it(tmp_path):
    """FINDING 4. `step_pnr` stages a VIA-legalised tech LEF into the RUN
    directory and MUTATES the shared PdkConfig to point at it. The stamp is
    written after the step, so the recorded PDK was the derived file, which
    differs every run — PnR was NEVER reused. `<field>_source` is the
    plugin's own record of the original."""
    derived = tmp_path / "active_via_legalized.tlef"
    derived.write_text("VERSION 5.8 ;\n# derived by this run\n")
    source = tmp_path / "nom.tlef"
    source.write_text("VERSION 5.8 ;\n")

    class _Pdk:
        liberty = None
        tech_lef = str(derived)
        tech_lef_source = str(source)
        cell_lef = cell_gds = drc_deck = None

    first, why = si.pdk_digest(_Pdk())
    assert first is not None, why
    # The run derives it again, differently — as it does on every run.
    derived.write_text("VERSION 5.8 ;\n# derived again, different bytes\n")
    second, _ = si.pdk_digest(_Pdk())
    assert first == second, (
        "the PDK identity moved because the STEP derived a file — that is an "
        "output, and it made the component never match")
    # ...but a real PDK change still moves it.
    source.write_text("VERSION 5.8 ;\n# a different PDK\n")
    third, _ = si.pdk_digest(_Pdk())
    assert third != first, "a real PDK change must still invalidate"


def test_mutation_hashing_the_derived_pdk_again_is_caught(tmp_path):
    """The mutation for finding 4: read `tech_lef` in preference to
    `tech_lef_source` and the component goes back to moving every run."""
    derived = tmp_path / "active_via_legalized.tlef"
    derived.write_text("a\n")
    source = tmp_path / "nom.tlef"
    source.write_text("b\n")

    class _Pdk:
        liberty = None
        tech_lef = str(derived)
        tech_lef_source = str(source)
        cell_lef = cell_gds = drc_deck = None

    assert si.pdk_files(_Pdk()) == [("tech_lef", str(source))], (
        "pdk_files must name the SOURCE; naming the derived file is the "
        "regression that made PnR never reusable")
