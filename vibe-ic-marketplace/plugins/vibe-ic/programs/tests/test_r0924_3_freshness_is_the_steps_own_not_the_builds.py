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
              image_digest="sha256:img", recording=_fake_recording())
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


def test_a_program_the_step_runs_is_its_code_a_declared_one_is_not(
        tmp_path, monkeypatch):
    """SUPERSEDED BY ROUND-3 REVIEW FINDING 3, and the replacement is the
    stronger rule. r3 added the span's declared `programs:` WHOLESALE, which
    pulled `flow_compliance_check.py` in entire — so every edit to it re-ran
    PnR and GDS, the exact coarseness R-0924-3 exists to remove. A declared
    program belongs in a kind's identity only if that kind's code REACHES it,
    and if the step runs it the closure finds it (including by file-path
    dispatch). Enforced by construction instead of by a list."""
    prog, runner, flow = _tree(tmp_path)
    (prog / "declared_prog.py").write_text("X = 5\n")
    (prog / "dispatched.py").write_text("def main():\n    return 1\n")
    runner.write_text(
        "def step_synth():\n"
        "    return run(['python3', 'dispatched.py'])\n",
        encoding="utf-8")
    _seeded(monkeypatch)
    before, why = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    assert before is not None, why
    (prog / "dispatched.py").write_text("def main():\n    return 2\n")
    mid, _ = si.code_digest(runner, prog, si.load_steps(flow), "synth")
    assert mid != before, (
        "a program this step dispatches by path is code it runs")
    (prog / "declared_prog.py").write_text("X = 500\n")
    assert si.code_digest(runner, prog, si.load_steps(flow),
                          "synth")[0] == mid, (
        "a declared program the step never runs is not the step's code")



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
    """A PDK OUTSIDE the project, which is where a PDK lives.

    r3 added a structural guard: a PDK path inside the run directory is this
    run's own derivation, not a PDK input, and refuses. Writing the fixture's
    PDK inside the project tripped it — correctly."""
    d = Path(root).parent / "_pdk_outside"
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



def _fake_recording():
    """A minimal RECORDING, so `identity_now` has a `code` to compute.

    r5 makes `code` come from what the step RAN, and a step with no recording
    gets no cache — which is the point. Tests that are about the OTHER three
    components supply a stable stand-in here; the recorder's own behaviour is
    covered by the r5 tests that drive it for real."""
    import _step_recorder as _sr
    d, err = _sr.source_digests(PROGRAMS / "_step_identity.py", [])
    assert not err, err
    return {"_step_identity.py": d}


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
        extra_inputs=extra, recording=_fake_recording())[0]


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
    # HOST-INDEPENDENT, which the r2 form was not: on a box with no docker
    # client every named container resolves to LOCAL_EXEC (the runner is
    # inside the image), and on a box with one an absent container cannot be
    # identified at all. Both are correct answers from the same ladder, so the
    # assertion is on the ladder, not on which box happens to run it.
    import _container_exec as _cx
    got = _R._step_image_digest("no-such-container-xyz-0924")
    if _cx.no_container_route():
        assert got == "LOCAL_EXEC", got
    else:
        assert got is None, (
            "a container that WAS named and cannot be identified still "
            f"refuses; got {got!r}")


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
    """The same finding, DRIVEN — and driven entirely inside tmp, because
    nothing that reads this tree may write to it (`suite_write_guard`), a test
    least of all.

    The runner and every in-tree module pnr's closure resolves are COPIED, the
    digest is taken over the copies, the emitter's copy is edited, and the
    digest is taken again. Resolution is identical in both passes, so the only
    thing that moved is the emitter."""
    import shutil
    prog = tmp_path / "programs"
    prog.mkdir()
    runner_copy = prog / "phase3_one_shot_runner.py"
    shutil.copy2(RUNNER, runner_copy)
    for src in PROGRAMS.glob("*.py"):
        if src.name != runner_copy.name:
            shutil.copy2(src, prog / src.name)
    for pkg in PROGRAMS.glob("*/"):
        if (pkg / "__init__.py").is_file():
            shutil.copytree(pkg, prog / pkg.name, dirs_exist_ok=True)

    before, why = si.code_digest(runner_copy, prog, _REAL_STEPS, "pnr")
    assert before is not None, why
    emitter = prog / "_route_wire_transaction.py"
    assert emitter.is_file(), "the emitter was copied"
    # EDIT THE MEMBER, not the file. Under r4's function-level closure a
    # trailing comment outside every reached member correctly changes nothing
    # — that is the whole point of the change — so the edit has to land in the
    # function pnr actually calls.
    mems, _b, _e = si._index_module(emitter)
    assert "wire_transaction_tcl" in mems, (
        "pnr reaches `wire_transaction_tcl`; if that moved, this test must "
        "follow it rather than pass vacuously")
    body = mems["wire_transaction_tcl"]
    # A STATEMENT, not a comment. A trailing comment is not part of the AST
    # node, so it falls OUTSIDE the member's line span and is correctly
    # invisible — which is exactly what
    # `test_a_comment_outside_every_reached_member_changes_nothing` asserts.
    # A landed FIX is a statement, and that is what this must simulate.
    emitter.write_text(emitter.read_text().replace(
        body, body.rstrip() + "\n    _r4_proof_edit = 1\n", 1))
    after, _ = si.code_digest(runner_copy, prog, _REAL_STEPS, "pnr")
    assert before != after, (
        "the Tcl emitters behind every PnR script are not reflected in pnr's "
        "code identity, so a landed fix to them would be silently skipped")


def test_a_comment_outside_every_reached_member_changes_nothing(tmp_path):
    """THE OTHER HALF, and the acceptance case the round-3 review named: r3
    hashed whole imported files, so ANY edit to one re-ran PnR and GDS."""
    import shutil
    prog = tmp_path / "programs"
    prog.mkdir()
    runner_copy = prog / "phase3_one_shot_runner.py"
    shutil.copy2(RUNNER, runner_copy)
    for src in PROGRAMS.glob("*.py"):
        if src.name != runner_copy.name:
            shutil.copy2(src, prog / src.name)
    for pkg in PROGRAMS.glob("*/"):
        if (pkg / "__init__.py").is_file():
            shutil.copytree(pkg, prog / pkg.name, dirs_exist_ok=True)
    before, why = si.code_digest(runner_copy, prog, _REAL_STEPS, "pnr")
    assert before is not None, why
    emitter = prog / "_route_wire_transaction.py"
    emitter.write_text(emitter.read_text()
                       + "\n# a trailing comment no step runs\n")
    after, _ = si.code_digest(runner_copy, prog, _REAL_STEPS, "pnr")
    assert before == after, (
        "a comment outside every reached member still re-ran PnR — the "
        "whole-file coarseness r4 exists to remove")


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


# ===========================================================================
# r3 — ROUND-2 REVIEW (wt0nrjhv6, 5 CONFIRMED)
#
# The theme: r2 still hashed what the FLOW DECLARES rather than what the STEP
# OPENS. `step_pnr` does not read `phase2/stage2/constraints/<top>.sdc` — that
# is a copy the runner writes once — and two files a step genuinely reads are
# rewritten by the flow AFTER the stamp that vouches for them.
#
# These drive the REAL resolution path: a staged SDC under `input/constraints`,
# a real declaration with real `answer_provenance`, the real knobs.
# ===========================================================================
def _staged_project(tmp_path: Path, *, stage_sdc: bool = True) -> Path:
    project = _span_project(tmp_path)
    if stage_sdc:
        ic = project / "input" / "constraints"
        ic.mkdir(parents=True, exist_ok=True)
        (ic / "silicon.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    (project / "input" / "submission_template"
     / "tapeout_declaration.json").write_text(json.dumps({
         "schema": 1,
         "answers": {"deliverable": "DIE", "core_area_um": [0, 0, 10, 10],
                     "top_cell": "top"},
         "answer_provenance": {"deliverable": {"answered_by": "owner"}},
     }, indent=2))
    (project / "phase2" / "stage2" / "synth" / "top_synth.v").write_text(
        "module top(); endmodule\n")
    (project / "phase3" / "stage3" / "pnr" / "top.def").write_text(
        "VERSION 5.8 ;\nEND DESIGN\n")
    return project


class _Args:
    spare_density = 0.02
    container = ""


def _r3_ident(project: Path, kind: str, args=None):
    si.set_declaration_flow_keys(_R._DECLARATION_PUBLISH_KEYS)
    inputs, knobs, _bad = _R._step_inputs(project, kind, "top", args or _Args())
    return si.identity_now(
        project=project, kind=kind, runner_path=RUNNER, programs_dir=PROGRAMS,
        flow_yaml=FLOW, pdk=_pdk_with_real_files(project),
        image_digest=_R._step_image_digest(""),
        inputs=inputs, knobs=knobs, recording=_fake_recording())[0]


# --- A: the SDC the step ACTUALLY resolves --------------------------------
def test_the_sdc_hashed_is_the_one_the_step_resolves(tmp_path):
    """FINDING A. r2 hashed `phase2/stage2/constraints/<top>.sdc`, which
    `step_pnr` never opens: it is a copy the runner writes once. The step
    resolves through `sdc_constraints.collect_sdc_files` —
    `input/constraints/*.sdc` first."""
    project = _staged_project(tmp_path)
    inputs, _k, _b = _R._step_inputs(project, "pnr", "top", _Args())
    sdcs = [Path(p) for label, p, _r in inputs if label == "sdc"]
    assert sdcs, "PnR must hash an SDC"
    assert sdcs[0] == project / "input" / "constraints" / "silicon.sdc", (
        f"PnR resolved its SDC to {sdcs[0]}, which is not what the step reads")


def test_editing_the_staged_sdc_invalidates_pnr(tmp_path):
    project = _staged_project(tmp_path)
    base = _r3_ident(project, "pnr")
    sdc = project / "input" / "constraints" / "silicon.sdc"
    sdc.write_text("create_clock -period 5 [get_ports clk]\n")
    fresh, why = si.compare(base, _r3_ident(project, "pnr"))
    assert fresh is False, f"the SDC the step reads changed and PnR stayed: {why}"


def test_pnr_hashes_whatever_its_own_resolver_returns(tmp_path):
    """SUPERSEDES the r3 form of this test, by round-3 review finding 2.

    r3 kept only `input/` paths, on the reasoning that everything else under
    the project is something the flow wrote. But `_resolve_staged_silicon_sdc`
    — the function `step_pnr` itself calls — falls back to LEGACY locations
    (`<project>/constraints`, `phase2/stage2/constraints`) when the ground
    truth is empty, and hands PnR whatever it finds there. So the r3 rule made
    the identity and the step DISAGREE about which file the run is constrained
    by, which is the one thing a cache key may never do.

    The identity now follows the resolver wherever it points, and DISCLOSES
    where that was (`sdc_origin`), instead of overruling it."""
    project = _staged_project(tmp_path, stage_sdc=False)
    legacy = project / "phase2" / "stage2" / "constraints"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "top.sdc").write_text("create_clock -period 10\n")
    inputs, knobs, _bad = _R._step_inputs(project, "pnr", "top", _Args())
    sdcs = [Path(q) for lbl, q, _r in inputs if lbl == "sdc"]
    assert sdcs, "PnR must hash the SDC its own resolver returns"
    assert sdcs[0] == _R._resolve_staged_silicon_sdc(project), (
        "identity and resolver must name the SAME file")
    assert knobs.get("sdc_origin") == "flow_emitted_or_legacy", knobs


def test_a_staged_sdc_under_input_is_disclosed_as_the_designs(tmp_path):
    project = _staged_project(tmp_path)          # stages input/constraints
    inputs, knobs, _bad = _R._step_inputs(project, "pnr", "top", _Args())
    sdcs = [Path(q) for lbl, q, _r in inputs if lbl == "sdc"]
    assert sdcs and sdcs[0].parent.name == "constraints"
    assert "input" in str(sdcs[0])
    assert knobs.get("sdc_origin") == "design", knobs


# --- A: the knobs ---------------------------------------------------------
def test_the_cli_and_env_knobs_are_part_of_the_identity(tmp_path,
                                                        monkeypatch):
    """FINDING A. None of these is a file, and each changes what the step
    produces."""
    project = _staged_project(tmp_path)
    base = {k: _r3_ident(project, k) for k in ("pnr", "gds")}

    class _Dense(_Args):
        spare_density = 0.05
    assert si.compare(base["pnr"], _r3_ident(project, "pnr", _Dense()))[0] \
        is False, "--spare-density changed and PnR stayed fresh"

    monkeypatch.setenv("VIBEIC_TAP_PITCH_UM", "20")
    assert si.compare(base["pnr"], _r3_ident(project, "pnr"))[0] is False, (
        "VIBEIC_TAP_PITCH_UM changed and PnR stayed fresh")
    monkeypatch.delenv("VIBEIC_TAP_PITCH_UM")

    monkeypatch.setenv("VIBEIC_FORCE_KLAYOUT_STREAMOUT", "1")
    assert si.compare(base["gds"], _r3_ident(project, "gds"))[0] is False, (
        "VIBEIC_FORCE_KLAYOUT_STREAMOUT changed and the GDS stayed fresh")


def test_a_slot_record_appearing_invalidates_every_kind(tmp_path):
    """FINDING A. The die rectangle the seal ring and die fill are built on
    comes from the operator's slot template.

    THE OPERATOR'S FILE, NOT THE FLOW'S COPY OF IT, and that correction came
    from driving the real runner: `reports/phase1/submission_template.json` is
    written BY THE RUN (step 0.5ic ingests the template into it), so it goes
    absent -> present DURING the run and a step stamped before it could never
    be fresh again. Measured, and the third member of finding D's family I
    have had to close. `input/submission_template/` is what the operator
    staged; the ingest is a restatement of it."""
    project = _staged_project(tmp_path)
    base = {k: _r3_ident(project, k) for k in ("synth", "pnr", "gds")}
    slots = project / "input" / "submission_template" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "1x1.yaml").write_text("die: [0, 0, 1000, 1000]\n")
    for kind in ("synth", "pnr", "gds"):
        fresh, why = si.compare(base[kind], _r3_ident(project, kind))
        assert fresh is False, f"{kind} survived a slot being staged: {why}"


def test_the_runs_own_copy_of_the_slot_template_is_not_an_input(tmp_path):
    """The other half, and it is what the measurement forced: the run writes
    `reports/phase1/submission_template.json` itself, so it must not be an
    input to a step stamped before it."""
    project = _staged_project(tmp_path)
    before, _k1, _b1 = _R._step_inputs(project, "synth", "top", _Args())
    rec = project / "reports" / "phase1" / "submission_template.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text('{"slot": "1x1"}\n')
    after, _k2, _b2 = _R._step_inputs(project, "synth", "top", _Args())
    assert [q for _l, q, _r in after] == [q for _l, q, _r in before], (
        "the run's own ingest of the slot template became an input to a step "
        "that ran before it")


# --- D: the declaration the flow writes back ------------------------------
def test_a_key_the_flow_derived_does_not_invalidate_the_step(tmp_path):
    """FINDING D. `step_gds` rewrites `tapeout_declaration.json` with the keys
    it DERIVED, after the synth and PnR stamps — so hashing it raw meant those
    two were never fresh on the next run, for a change they did not make."""
    project = _staged_project(tmp_path)
    base = {k: _r3_ident(project, k) for k in ("synth", "pnr")}
    decl = project / "input" / "submission_template" / \
        "tapeout_declaration.json"
    doc = json.loads(decl.read_text())
    doc["answers"]["core_area_um"] = [0, 0, 99, 99]   # flow-derived, unclaimed
    doc["answers"]["top_cell"] = "chip_top"
    decl.write_text(json.dumps(doc, indent=2))
    for kind in ("synth", "pnr"):
        fresh, why = si.compare(base[kind], _r3_ident(project, kind))
        assert fresh is True, (
            f"{kind} re-ran because the FLOW rewrote the declaration: {why}")


def test_an_answer_the_operator_owns_still_invalidates(tmp_path):
    """THE LIMIT OF THAT RULE, and the first cut of it was wrong here:
    `deliverable` is in `_DECLARATION_PUBLISH_KEYS`, so dropping every
    published key hid an OPERATOR change from DIE to HARDMACRO — the single
    answer that changes the most about a run. The flow writes a key only when
    NOBODY HAS ANSWERED IT, and `answer_provenance` records who has."""
    project = _staged_project(tmp_path)
    base = {k: _r3_ident(project, k) for k in ("synth", "pnr", "gds")}
    decl = project / "input" / "submission_template" / \
        "tapeout_declaration.json"
    doc = json.loads(decl.read_text())
    assert "deliverable" in doc["answer_provenance"], "owner-answered"
    assert "deliverable" in _R._DECLARATION_PUBLISH_KEYS, (
        "and also a key the flow may publish — which is the whole trap")
    doc["answers"]["deliverable"] = "HARDMACRO"
    decl.write_text(json.dumps(doc, indent=2))
    for kind in ("synth", "pnr", "gds"):
        fresh, why = si.compare(base[kind], _r3_ident(project, kind))
        assert fresh is False, (
            f"{kind} survived the operator changing the deliverable: {why}")


def test_the_declaration_rule_keeps_provenance_itself(tmp_path):
    project = _staged_project(tmp_path)
    decl = project / "input" / "submission_template" / \
        "tapeout_declaration.json"
    before = si.canonical_bytes(decl, "declaration_as_asked")
    doc = json.loads(decl.read_text())
    doc["answer_provenance"]["deliverable"]["answered_by"] = "someone else"
    decl.write_text(json.dumps(doc, indent=2))
    after = si.canonical_bytes(decl, "declaration_as_asked")
    assert before != after, "who answered is itself operator input"


# --- C/E: the volatile SPEF ------------------------------------------------
def test_the_spef_date_line_is_normalised_away(tmp_path):
    """FINDINGS C and E. A SPEF carries `*DATE "<wall clock>"`, so two runs
    never agree. The GDS identity does not list the SPEF at all — `step_gds`
    does not read it and it is written after the stamp — but the rule exists
    and is stated, so it is asserted rather than assumed."""
    a = tmp_path / "a.spef"
    b = tmp_path / "b.spef"
    a.write_text('*SPEF "ieee 1481-1999"\n*DATE "one o\'clock"\n*DESIGN "x"\n')
    b.write_text('*SPEF "ieee 1481-1999"\n*DATE "half past two"\n*DESIGN "x"\n')
    assert si.canonical_bytes(a, "raw") != si.canonical_bytes(b, "raw")
    assert si.canonical_bytes(a, "spef_no_date") == \
        si.canonical_bytes(b, "spef_no_date")
    b.write_text('*SPEF "ieee 1481-1999"\n*DATE "x"\n*DESIGN "DIFFERENT"\n')
    assert si.canonical_bytes(a, "spef_no_date") != \
        si.canonical_bytes(b, "spef_no_date"), (
        "normalising the clock must not normalise the content")


def test_the_gds_identity_does_not_list_the_spef(tmp_path):
    project = _staged_project(tmp_path)
    inputs, _k, _b = _R._step_inputs(project, "gds", "top", _Args())
    assert not any(str(p).endswith(".spef") for _l, p, _r in inputs), (
        "step_gds does not read the SPEF, and the SPEF is written after this "
        "stamp — hashing it is how r2 made the GDS permanently stale")


# --- B: in-place writers and file-path programs ---------------------------
def test_the_in_place_writers_of_the_def_are_in_the_code_identity():
    """FINDING B. `step_signoff_spef_repair` and
    `step_signoff_drv_wire_length_repair` run AFTER the PnR stamp and
    `shutil.copy2` a repaired DEF over `routed.def` and `{top}.def`. A fix
    landed in either changed the cached artefact and nothing in the key that
    vouched for it."""
    members, err = si._module_members(RUNNER)
    assert not err, err
    for writer in ("step_signoff_spef_repair",
                   "step_signoff_drv_wire_length_repair"):
        assert writer in members, f"{writer} moved"
        assert writer in si.KIND_RECIPE_SEEDS["pnr"], (
            f"{writer} writes the cached DEF and is not in pnr's code")
        assert writer in si.KIND_RECIPE_SEEDS["gds"], (
            f"{writer} writes the streamed DEF and is not in gds's code")


def test_editing_an_in_place_writer_invalidates_pnr(tmp_path):
    """The same finding, driven ON A COPY — `suite_write_guard` is right that
    nothing reading this tree may write to it, and that includes this test.

    The edit goes into the writer's own body, so a closure that did not reach
    `step_signoff_spef_repair` would not notice it."""
    import shutil
    runner_copy = tmp_path / "phase3_one_shot_runner.py"
    shutil.copy2(RUNNER, runner_copy)
    before, why = si.code_digest(runner_copy, PROGRAMS, _REAL_STEPS, "pnr")
    assert before is not None, why
    members, _ = si._module_members(runner_copy)
    body = members["step_signoff_spef_repair"]
    runner_copy.write_text(runner_copy.read_text().replace(
        body, body.replace('"""', '"""r3 proof edit. ', 1), 1))
    after, _ = si.code_digest(runner_copy, PROGRAMS, _REAL_STEPS, "pnr")
    assert before != after, (
        "an edit to an in-place writer of the cached DEF did not invalidate "
        "PnR")


def test_a_program_run_by_file_path_is_in_the_code_identity(tmp_path,
                                                            monkeypatch):
    """FINDING B, second half: a program dispatched as
    `python3 <programs>/x.py` is never imported, so no import binding names
    it."""
    prog, runner, flow = _tree(tmp_path)
    (prog / "dispatched_by_path.py").write_text("VALUE = 1\n")
    runner.write_text(
        "def step_synth():\n"
        "    return run(['python3', 'dispatched_by_path.py'])\n",
        encoding="utf-8")
    _seeded(monkeypatch)
    before, why = si.runner_code_closure(runner, "synth")
    assert before is not None, why
    (prog / "dispatched_by_path.py").write_text("VALUE = 2\n")
    after, _ = si.runner_code_closure(runner, "synth")
    assert before != after, (
        "a program this step runs by file path is outside its code identity")


# --- fail-closed, harder than r2 ------------------------------------------
def test_an_empty_input_set_refuses(tmp_path):
    dig, why = si.resolved_inputs_digest(tmp_path, (), {})
    assert dig is None
    assert any("enumerated no input" in w for w in why), why


def test_an_input_the_step_could_not_resolve_refuses(tmp_path):
    dig, why = si.resolved_inputs_digest(
        tmp_path, (("sdc", None, "raw"),), {})
    assert dig is None
    assert any("produced no path" in w for w in why), why


def test_a_pdk_path_in_the_runs_output_tree_refuses(tmp_path):
    """The structural form of finding 4, narrowed by round-3 review finding 5:
    only the run's OUTPUT tree is this run's own derivation."""
    derived = tmp_path / "phase3" / "stage3" / "pnr" / "active.tlef"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text("VERSION 5.8 ;\n")

    class _Pdk:
        liberty = None
        tech_lef = str(derived)
        cell_lef = cell_gds = drc_deck = None

    dig, why = si.pdk_digest(_Pdk(), None, tmp_path)
    assert dig is None, "a PDK path in the run's OUTPUT tree is not an input"
    assert any("OUTPUT tree" in w for w in why), why


def test_a_pdk_the_design_stages_under_input_is_hashed(tmp_path):
    """ROUND-3 REVIEW FINDING 5. `input/` is the design's — that is this
    module's own rule — so a PDK staged there is a DESIGN INPUT. Refusing it
    made every staged-PDK run (asap7 measured) permanently un-fresh."""
    staged = tmp_path / "input" / "pdk" / "tech.lef"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("VERSION 5.8 ;\n")

    class _Pdk:
        liberty = None
        tech_lef = str(staged)
        cell_lef = cell_gds = drc_deck = None

    dig, why = si.pdk_digest(_Pdk(), None, tmp_path)
    assert dig is not None, (
        f"a PDK the design staged under input/ must be hashed, not refused: "
        f"{why}")
    staged.write_text("VERSION 5.8 ;\n# a different PDK\n")
    assert si.pdk_digest(_Pdk(), None, tmp_path)[0] != dig, (
        "and a change to it must invalidate")


# ===========================================================================
# r4 — ROUND-3 REVIEW (w18fc56v9, 5 CONFIRMED)
#
# The theme, and it is the last restatement: r3 still NAMED each input instead
# of ASKING the step. Every miss the review found is that one mistake.
# ===========================================================================
def test_the_identity_calls_the_steps_own_netlist_resolver(tmp_path):
    """FINDING 1, the headline. `step_pnr` routes `pnr_input_netlist()`, which
    returns `post_dft_netlist.v` when the L20 scan contract authorises it —
    while r3's identity hashed `{top}_synth.v` unconditionally. A DFT re-run
    then left the routed DEF 'fresh' against a netlist PnR never read."""
    project = _staged_project(tmp_path)
    resolved = _R.pnr_input_netlist(project, "top")
    want = Path(resolved[0] if isinstance(resolved, tuple) else resolved)
    inputs, knobs, bad = _R._step_inputs(project, "pnr", "top", _Args())
    assert bad == [], bad
    got = [Path(q) for lbl, q, _r in inputs if lbl == "netlist"]
    assert got and got[0] == want, (
        f"identity hashes {got} but the step reads {want}")


def test_a_new_post_dft_netlist_invalidates_pnr(tmp_path):
    project = _staged_project(tmp_path)
    base = _r3_ident(project, "pnr")
    resolved = _R.pnr_input_netlist(project, "top")
    nl = Path(resolved[0] if isinstance(resolved, tuple) else resolved)
    nl.write_text("module top(); wire rescanned; endmodule\n")
    fresh, why = si.compare(base, _r3_ident(project, "pnr"))
    assert fresh is False, f"the netlist PnR reads changed and it stayed: {why}"


def test_a_reference_flow_knob_invalidates_synth(tmp_path):
    """FINDING 1. `_reference_flow_qor_knobs` reshapes synthesis
    (`SWAP_ARITH_OPERATORS`, `ADDER_MAP_FILE`) and was not in the identity at
    all."""
    project = _staged_project(tmp_path)
    base = _r3_ident(project, "synth")
    rf = project / "input" / "reference_flow"
    rf.mkdir(parents=True, exist_ok=True)
    (rf / "config.mk").write_text("export SWAP_ARITH_OPERATORS = 1\n")
    fresh, why = si.compare(base, _r3_ident(project, "synth"))
    assert fresh is False, f"a reference-flow QoR knob did not reach synth: {why}"


def test_a_staged_macro_invalidates_every_kind(tmp_path):
    """FINDING 1. `input/pdk_local` macros become synth blackboxes AND the
    PnR/GDS macro LEF/GDS — and they are not in `_PDK_FIELDS`, so nothing
    hashed them."""
    project = _staged_project(tmp_path)
    base = {k: _r3_ident(project, k) for k in ("synth", "pnr", "gds")}
    lef = project / "input" / "pdk_local" / "vendor" / "u_otp.lef"
    lef.parent.mkdir(parents=True, exist_ok=True)
    lef.write_text("MACRO u_otp\nEND u_otp\n")
    for kind in ("synth", "pnr", "gds"):
        fresh, why = si.compare(base[kind], _r3_ident(project, kind))
        assert fresh is False, f"{kind} survived a staged macro: {why}"


def test_an_otp_image_invalidates_synth(tmp_path):
    """FINDING 1. `input/otp/*.hex` is copied into the synth working directory
    so `$readmemh` resolves — it is part of what synthesis reads."""
    project = _staged_project(tmp_path)
    base = _r3_ident(project, "synth")
    otp = project / "input" / "otp" / "image.hex"
    otp.parent.mkdir(parents=True, exist_ok=True)
    otp.write_text("00\n")
    fresh, why = si.compare(base, _r3_ident(project, "synth"))
    assert fresh is False, why


def test_a_legacy_location_sdc_invalidates_pnr(tmp_path):
    """FINDING 2: identity and resolver must name the SAME file."""
    project = _staged_project(tmp_path, stage_sdc=False)
    legacy = project / "phase2" / "stage2" / "constraints"
    legacy.mkdir(parents=True, exist_ok=True)
    sdc = legacy / "top.sdc"
    sdc.write_text("create_clock -period 10\n")
    base = _r3_ident(project, "pnr")
    sdc.write_text("create_clock -period 5\n")
    fresh, why = si.compare(base, _r3_ident(project, "pnr"))
    assert fresh is False, (
        f"PnR is constrained by this file and did not notice it change: {why}")


def test_a_resolver_that_cannot_be_called_means_no_cache(tmp_path,
                                                         monkeypatch):
    """The fail-closed rule the review asked for: a read-set we cannot
    establish is not a freshness answer."""
    project = _staged_project(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("resolver unavailable")

    monkeypatch.setattr(_R, "_reference_flow_qor_knobs", _boom)
    _inputs, _knobs, bad = _R._step_inputs(project, "synth", "top", _Args())
    assert bad and "_reference_flow_qor_knobs" in bad[0], bad
    ok, why = _R._producer_cache_valid_for(
        _R._pl.synth_dir(project), "synth", project=project,
        pdk=_pdk_with_real_files(project), container="", top="top",
        args=_Args())
    assert ok is False
    assert "read-set cannot be established" in why, why


def test_a_comment_in_an_unrelated_runner_function_changes_nothing(tmp_path):
    """THE ACCEPTANCE CASE. r3 hashed the WHOLE runner for pnr and gds, so any
    edit anywhere re-ran both — the build key again under another name."""
    import shutil
    prog = tmp_path / "programs"
    prog.mkdir()
    runner_copy = prog / "phase3_one_shot_runner.py"
    shutil.copy2(RUNNER, runner_copy)
    for src in PROGRAMS.glob("*.py"):
        if src.name != runner_copy.name:
            shutil.copy2(src, prog / src.name)
    for pkg in PROGRAMS.glob("*/"):
        if (pkg / "__init__.py").is_file():
            shutil.copytree(pkg, prog / pkg.name, dirs_exist_ok=True)
    base = {k: si.code_digest(runner_copy, prog, _REAL_STEPS, k)[0]
            for k in ("synth", "pnr", "gds")}
    mems, _b, _e = si._index_module(runner_copy)
    reached = set()
    for k in ("synth", "pnr", "gds"):
        seen, stack = set(), [(str(runner_copy), x)
                              for x in si.KIND_RECIPE_SEEDS[k]]
        while stack:
            m, n = stack.pop()
            ms, bs, er = si._index_module(Path(m))
            if er or n is None or (m, n) in seen or n not in ms:
                continue
            seen.add((m, n))
            for t in si._referenced_targets(ms[n], Path(m), bs, ms, prog):
                if t[1] is not None and t not in seen:
                    stack.append(t)
        reached |= {n for m, n in seen if m == str(runner_copy)}
    unrelated = [n for n in mems
                 if n not in reached and len(mems[n]) > 200]
    assert unrelated, "there must be a runner function no kind reaches"
    tgt = unrelated[0]
    runner_copy.write_text(runner_copy.read_text().replace(
        mems[tgt], mems[tgt].rstrip() + "\n    # r4 unrelated edit\n", 1))
    for k in ("synth", "pnr", "gds"):
        now, _ = si.code_digest(runner_copy, prog, _REAL_STEPS, k)
        assert now == base[k], (
            f"{k} re-ran for an edit to {tgt}, which it never calls")


def test_the_three_kinds_do_not_share_one_code_value():
    digs = {k: si.code_digest(RUNNER, PROGRAMS, _REAL_STEPS, k)[0]
            for k in ("synth", "pnr", "gds")}
    assert len(set(digs.values())) == 3, digs
    assert all(v is not None for v in digs.values()), digs


def test_the_declaration_rule_covers_the_top_level_keys_too(tmp_path):
    """FINDING 4: `step_gds`'s merge also rewrites the TOP-LEVEL
    `from_the_technology` and `forbidden_layers`, so a phase-3-only re-run
    moved synth's and pnr's input hash for something the FLOW wrote."""
    si.set_declaration_flow_keys(_R._DECLARATION_PUBLISH_KEYS)
    decl = tmp_path / "d.json"
    decl.write_text(json.dumps({"answers": {"deliverable": "DIE"},
                                "answer_provenance": {
                                    "deliverable": {"answered_by": "owner"}}}))
    before = si.canonical_bytes(decl, "declaration_as_asked")
    doc = json.loads(decl.read_text())
    doc["from_the_technology"] = {"derived": "by the flow"}
    doc["forbidden_layers"] = ["M9"]
    decl.write_text(json.dumps(doc))
    after = si.canonical_bytes(decl, "declaration_as_asked")
    assert before == after, (
        "the flow's own top-level writes must not move a step's input hash")


# ===========================================================================
# r5 — ROUND-4 REVIEW (wlfte9cfq, 6 confirmed)
#
# "Stop patching the static closure; this is round 4 of the same hole class,
# and Python's dynamism guarantees a round 5." So the closure stops being the
# authority: the step RUNS and what ran is RECORDED.
# ===========================================================================
import _step_recorder as sr  # noqa: E402


def _mini_tree(tmp_path: Path) -> Path:
    root = tmp_path / "progs"
    root.mkdir()
    (root / "m_alias.py").write_text("TUNING = 7\ndef helper():\n    return TUNING\n")
    (root / "m_dyn.py").write_text("def dynamic():\n    return 2\n")
    (root / "m_top.py").write_text(
        "from m_alias import helper as _h\n"
        "import importlib\n"
        "def run():\n"
        "    mod = importlib.import_module('m_dyn')\n"
        "    return _h() + mod.dynamic()\n")
    return root


def _record_run(root: Path):
    import importlib
    sys.path.insert(0, str(root))
    try:
        for name in ("m_top", "m_alias", "m_dyn"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("m_top")
        with sr.Recorder(root) as r:
            mod.run()
        return r.recorded()
    finally:
        sys.path.remove(str(root))


def test_an_aliased_import_is_recorded_because_it_ran(tmp_path):
    """ROUND-4 FINDING 1. `from X import f as g` was recorded by the static
    closure as a member named `g`, which does not exist — so it was DROPPED
    WITH NO NOTE. Real escapes: `routing_layer_upper_bound as _rub` (shapes
    the routed DEF), `pdn_ring_dimensions` (the PDN ring), `drop_include_hubs`
    (synth's read set)."""
    root = _mini_tree(tmp_path)
    record, why = _record_run(root)
    assert record is not None, why
    assert "m_alias.py" in record, record
    assert any(k.startswith("helper@") for k in record["m_alias.py"]), record
    base = sr.digest_of(record)
    (root / "m_alias.py").write_text(
        "TUNING = 7\ndef helper():\n    return TUNING + 1\n")
    now, err = sr.rederive(record, root)
    assert not err, err
    assert sr.digest_of(now) != base, (
        "an edit to a function reached only through an ALIAS went unnoticed")


def test_an_importlib_loaded_module_is_recorded(tmp_path):
    """ROUND-4 FINDING 1. `importlib.import_module("<literal>")` is invisible
    to any import-graph walk — measured on the GDS label restore
    (`gds_port_label_check`)."""
    root = _mini_tree(tmp_path)
    record, why = _record_run(root)
    assert record is not None, why
    assert "m_dyn.py" in record, record
    base = sr.digest_of(record)
    (root / "m_dyn.py").write_text("def dynamic():\n    return 3\n")
    now, _ = sr.rederive(record, root)
    assert sr.digest_of(now) != base


def test_a_module_level_constant_is_recorded_though_it_never_runs(tmp_path):
    """Constants, compiled regex tables and dict dispatch tables are not
    functions and never appear as a call, yet changing one changes what every
    function in the file does. Earlier rounds had to special-case exactly
    this; the module-body digest covers it by construction."""
    root = _mini_tree(tmp_path)
    record, why = _record_run(root)
    assert record is not None, why
    base = sr.digest_of(record)
    (root / "m_alias.py").write_text(
        "TUNING = 8\ndef helper():\n    return TUNING\n")
    now, _ = sr.rederive(record, root)
    assert sr.digest_of(now) != base, (
        "a module-level constant the step's code READS was not covered")


def test_an_unrelated_edit_in_a_recorded_file_changes_nothing(tmp_path):
    """The other half: function-level, so a function the step never ran does
    not cost a re-run even in a file it did."""
    root = _mini_tree(tmp_path)
    record, why = _record_run(root)
    assert record is not None, why
    base = sr.digest_of(record)
    (root / "m_alias.py").write_text(
        "TUNING = 7\ndef helper():\n    return TUNING\n"
        "def never_called():\n    return 999\n")
    now, _ = sr.rederive(record, root)
    assert sr.digest_of(now) == base, (
        "adding a function the step never runs re-ran it anyway")


def test_a_recorded_function_that_vanished_is_the_strongest_stale(tmp_path):
    root = _mini_tree(tmp_path)
    record, why = _record_run(root)
    assert record is not None, why
    (root / "m_alias.py").write_text("TUNING = 7\n")
    now, _ = sr.rederive(record, root)
    assert any(v == "ABSENT" for d in now.values() for v in d.values()), now
    assert sr.digest_of(now) != sr.digest_of(record)


def test_no_recording_means_no_cache(tmp_path):
    """A step whose code could not be recorded is not a step anyone can prove
    current."""
    dig, why = si.code_from_stored(None, PROGRAMS)
    assert dig is None
    assert any("carries no recording" in w for w in why), why
    dig, why = si.code_from_recording(None)
    assert dig is None and why


def test_the_pdk_identity_is_every_field_the_step_received(tmp_path):
    """ROUND-4 FINDING 2. The recipe-shaping fields are built in `main()`
    (`_detect_pdk` -> `_pdk_config_from_registry` -> `_derive_tapcell_master`),
    outside every seed closure — so a `pdk_registry.json` edit
    (`clk_buf_cell`, `pdn_straps`, `pdn_ring`) or a tap-master rule change
    altered the DEF with all four components unchanged."""
    lib = tmp_path / "x.lib"
    lib.write_text("library(a){}\n")

    class _Pdk:
        name = "gf180mcuD"
        liberty = str(lib)
        tech_lef = cell_lef = cell_gds = drc_deck = None
        clk_buf_cell = "gf180mcu_fd_sc_mcu7t5v0__clkbuf_1"
        tapcell_master = "gf180mcu_fd_sc_mcu7t5v0__filltie"
        macro_lefs: list = []

    pdk = _Pdk()
    base, why = si.pdk_digest(pdk, None, tmp_path / "proj")
    assert base is not None, why
    pdk.clk_buf_cell = "gf180mcu_fd_sc_mcu7t5v0__clkbuf_4"
    assert si.pdk_digest(pdk, None, tmp_path / "proj")[0] != base, (
        "a registry-derived master changed and the PDK identity did not")
    pdk.clk_buf_cell = "gf180mcu_fd_sc_mcu7t5v0__clkbuf_1"
    pdk.macro_lefs = ["/some/macro.lef"]
    assert si.pdk_digest(pdk, None, tmp_path / "proj")[0] != base, (
        "the macro set is part of the PDK the step received")


def test_the_tools_component_is_carried_not_re_read(tmp_path, monkeypatch):
    """ROUND-4 FINDING 4. `step_canonicalize_artefacts` keeps APPENDING to
    `provenance.jsonl` after the stamps, with version-less reconstructed rows
    (measured: yosys -> "", pnr tools -> None), so re-reading the ledger made
    this component move on every no-op re-run. The ledger is a record of the
    past and it grows; a freshness key may not be read from it."""
    project = _staged_project(tmp_path)
    stored = json.dumps({"yosys": "0.38"}, sort_keys=True)
    ident, why = si.identity_now(
        project=project, kind="synth", runner_path=RUNNER,
        programs_dir=PROGRAMS, flow_yaml=FLOW,
        pdk=_pdk_with_real_files(project), image_digest="sha256:img",
        inputs=[("declaration",
                 project / "input/submission_template/tapeout_declaration.json",
                 "raw")],
        knobs={}, recording=_fake_recording(), stored_tools=stored)
    assert ident["tools"] is not None, why["tools"]
    # the ledger grows underneath — and it must not matter
    (project / "provenance.jsonl").write_text(
        (project / "provenance.jsonl").read_text()
        + json.dumps({"tool": "klayout", "version": "",
                      "outputs": {"phase2/stage2/synth/x.v": "sha256:0"}})
        + "\n")
    again, _ = si.identity_now(
        project=project, kind="synth", runner_path=RUNNER,
        programs_dir=PROGRAMS, flow_yaml=FLOW,
        pdk=_pdk_with_real_files(project), image_digest="sha256:img",
        inputs=[("declaration",
                 project / "input/submission_template/tapeout_declaration.json",
                 "raw")],
        knobs={}, recording=_fake_recording(), stored_tools=stored)
    assert again["tools"] == ident["tools"], (
        "the tools component moved because the ledger grew after the stamp")


def test_the_flows_own_stamped_auto_sdc_is_not_design_staged(tmp_path):
    """ROUND-4 FINDING 5, pre-existing on main. `_stamp_sdc_provenance`
    PREPENDS `# VIBEIC_SDC_PDK_PROVENANCE`, and
    `step_canonicalize_artefacts` runs it over the deck the flow itself
    emitted — so the auto banner stopped being the first line and a
    `startswith` no longer matched. The flow's own deck then read back as
    DESIGN-STAGED: `sdc_staged` flipped no -> yes after the stamps and the
    re-run took the design-staged branch on the flow's own file, which is the
    laundering this guard exists to prevent."""
    d = tmp_path / "phase2" / "stage2" / "constraints"
    d.mkdir(parents=True)
    (d / "top.sdc").write_text(
        "# VIBEIC_SDC_PDK_PROVENANCE: gf180mcuD\n"
        "# Auto-generated minimal SDC for silicon top (no constraints/*.sdc "
        "supplied; clk_period_ns=20.0)\n"
        "create_clock -name clk -period 20.0 [get_ports i_clk]\n")
    assert _R._resolve_staged_silicon_sdc(tmp_path) is None, (
        "the flow's own CANONICALIZED auto-SDC was read back as the design's")


def test_an_unstamped_auto_sdc_is_still_recognised(tmp_path):
    """The pre-existing case must keep working: the banner on line 1."""
    d = tmp_path / "phase2" / "stage2" / "constraints"
    d.mkdir(parents=True)
    (d / "top.sdc").write_text(
        "# Auto-generated minimal SDC for silicon top (no constraints/*.sdc "
        "supplied; clk_period_ns=20.0)\n"
        "create_clock -name clk -period 20.0 [get_ports i_clk]\n")
    assert _R._resolve_staged_silicon_sdc(tmp_path) is None


def test_a_hand_authored_sdc_is_still_the_designs(tmp_path):
    """And the other direction, so the guard cannot become a blanket refusal:
    a deck with no flow banner is the design's and must be returned."""
    d = tmp_path / "input" / "constraints"
    d.mkdir(parents=True)
    (d / "silicon.sdc").write_text(
        "# hand written by the designer\ncreate_clock -period 10 [get_ports clk]\n")
    got = _R._resolve_staged_silicon_sdc(tmp_path)
    assert got is not None and got.name == "silicon.sdc", got


def test_mutation_recording_only_calls_would_miss_the_module_body(tmp_path):
    """The mutation for the recorder: drop the module-body digest and a
    constant edit becomes invisible again."""
    root = _mini_tree(tmp_path)
    record, _why = _record_run(root)
    stripped = {f: {k: v for k, v in d.items() if k != sr.MODULE_BODY}
                for f, d in record.items()}
    base = sr.digest_of(stripped)
    (root / "m_alias.py").write_text(
        "TUNING = 8\ndef helper():\n    return TUNING\n")
    now, _ = sr.rederive(record, root)
    now_stripped = {f: {k: v for k, v in d.items() if k != sr.MODULE_BODY}
                    for f, d in now.items()}
    assert sr.digest_of(now_stripped) == base, (
        "sanity: without the module body, the constant edit is invisible")
    assert sr.digest_of(now) != sr.digest_of(record), (
        "and WITH it, the same edit is caught — which is why it is there")
